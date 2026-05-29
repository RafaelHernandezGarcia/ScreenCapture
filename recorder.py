"""
Screen Recorder - Captures a screen region as MP4 video with audio
"""
import sys
import os
import time
import wave
import threading
import subprocess
import tempfile
from datetime import datetime
from fractions import Fraction

import av
import mss
import numpy as np

from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QIcon, QImage, QPainterPath, QRegion
)
from PyQt6.QtWidgets import QWidget, QApplication

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
SYSTEM_FONT = ".AppleSystemUIFont" if IS_MACOS else "Segoe UI"

# --- Mouse cursor overlay (macOS) ---
_HAS_CURSOR = False
if IS_MACOS:
    try:
        from AppKit import NSEvent, NSScreen
        _HAS_CURSOR = True
    except ImportError:
        pass


def _configure_nswindow(widget, click_through=False, level=25):
    """Configure NSWindow for overlay widgets on macOS.

    Uses direct winId() -> NSView -> NSWindow access instead of the
    unreliable NSApp.windows() iteration pattern.
    """
    if not IS_MACOS:
        return
    try:
        import ctypes as _ct
        import objc
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorStationary,
            NSWindowCollectionBehaviorIgnoresCycle,
        )
        nsview_ptr = int(widget.winId())
        if nsview_ptr == 0:
            return
        nsview = objc.objc_object(c_void_p=_ct.c_void_p(nsview_ptr))
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setIgnoresMouseEvents_(click_through)
        nswindow.setLevel_(level)
        nswindow.setHidesOnDeactivate_(False)
        nswindow.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
    except Exception as e:
        print(f"_configure_nswindow: {e}")

# Arrow cursor bitmap: 0=transparent, 1=black outline, 2=white fill
_CURSOR_BITMAP = np.array([
    [1,0,0,0,0,0,0,0,0,0,0],
    [1,1,0,0,0,0,0,0,0,0,0],
    [1,2,1,0,0,0,0,0,0,0,0],
    [1,2,2,1,0,0,0,0,0,0,0],
    [1,2,2,2,1,0,0,0,0,0,0],
    [1,2,2,2,2,1,0,0,0,0,0],
    [1,2,2,2,2,2,1,0,0,0,0],
    [1,2,2,2,2,2,2,1,0,0,0],
    [1,2,2,2,2,2,2,2,1,0,0],
    [1,2,2,2,2,2,1,1,1,1,0],
    [1,2,2,1,2,2,1,0,0,0,0],
    [1,2,1,0,1,2,2,1,0,0,0],
    [1,1,0,0,1,2,2,1,0,0,0],
    [1,0,0,0,0,1,2,2,1,0,0],
    [0,0,0,0,0,1,2,2,1,0,0],
    [0,0,0,0,0,0,1,1,0,0,0],
], dtype=np.uint8)


def _build_cursor_masks(dpr):
    """Pre-build DPR-scaled cursor boolean masks."""
    scale = max(1, round(dpr))
    black = _CURSOR_BITMAP == 1
    white = _CURSOR_BITMAP == 2
    if scale > 1:
        black = np.repeat(np.repeat(black, scale, axis=0), scale, axis=1)
        white = np.repeat(np.repeat(white, scale, axis=0), scale, axis=1)
    return black, white


def _overlay_cursor(frame, cx, cy, black_mask, white_mask):
    """Overlay arrow cursor onto frame at pixel (cx, cy). Fast boolean indexing."""
    h, w = frame.shape[:2]
    ch, cw = black_mask.shape

    sy1, sx1 = max(0, -cy), max(0, -cx)
    sy2, sx2 = min(ch, h - cy), min(cw, w - cx)
    if sy2 <= sy1 or sx2 <= sx1:
        return

    dy1, dy2 = cy + sy1, cy + sy2
    dx1, dx2 = cx + sx1, cx + sx2

    bm = black_mask[sy1:sy2, sx1:sx2]
    wm = white_mask[sy1:sy2, sx1:sx2]

    region = frame[dy1:dy2, dx1:dx2]
    region[bm] = [0, 0, 0]
    region[wm] = [255, 255, 255]


def _composite_webcam_circle(frame, webcam_bgr, cx, cy, radius):
    """Composite a circular webcam feed onto the frame at (cx, cy)."""
    import cv2
    h, w = frame.shape[:2]
    diameter = radius * 2

    # Center-crop to square (preserve aspect ratio, avoid stretch distortion)
    cam_h, cam_w = webcam_bgr.shape[:2]
    size = min(cam_w, cam_h)
    x0 = (cam_w - size) // 2
    y0 = (cam_h - size) // 2
    cropped = webcam_bgr[y0:y0 + size, x0:x0 + size]
    cam = cv2.resize(cropped, (diameter, diameter), interpolation=cv2.INTER_LANCZOS4)
    cam_rgb = cv2.cvtColor(cam, cv2.COLOR_BGR2RGB)

    # Create circular mask
    yy, xx = np.ogrid[:diameter, :diameter]
    circle_mask = ((xx - radius) ** 2 + (yy - radius) ** 2) <= radius ** 2

    # Compute bounding box in frame
    x1 = cx - radius
    y1 = cy - radius
    x2 = x1 + diameter
    y2 = y1 + diameter

    # Clip to frame bounds
    sx1 = max(0, -x1)
    sy1 = max(0, -y1)
    sx2 = diameter - max(0, x2 - w)
    sy2 = diameter - max(0, y2 - h)
    dx1 = max(0, x1)
    dy1 = max(0, y1)
    dx2 = min(w, x2)
    dy2 = min(h, y2)

    if dx2 <= dx1 or dy2 <= dy1:
        return

    mask_region = circle_mask[sy1:sy2, sx1:sx2]
    frame[dy1:dy2, dx1:dx2][mask_region] = cam_rgb[sy1:sy2, sx1:sx2][mask_region]

    # Draw border ring
    border_mask = ((xx - radius) ** 2 + (yy - radius) ** 2 <= radius ** 2) & \
                  ((xx - radius) ** 2 + (yy - radius) ** 2 > (radius - 3) ** 2)
    border_region = border_mask[sy1:sy2, sx1:sx2]
    frame[dy1:dy2, dx1:dx2][border_region] = [255, 255, 255]


def _composite_annotation(frame, ann, dpr):
    """Composite annotation layer onto the frame. ann is numpy (h,w,4) BGRA, no Qt."""
    if ann is None or ann.size == 0:
        return

    fh, fw = frame.shape[:2]

    # Scale annotation to match physical frame if needed
    if ann.shape[0] != fh or ann.shape[1] != fw:
        import cv2
        ann = cv2.resize(ann, (fw, fh), interpolation=cv2.INTER_NEAREST)

    # Alpha composite (ann is BGRA from QImage Format_ARGB32)
    alpha = ann[:, :, 3:4].astype(np.float32) / 255.0
    ann_rgb = ann[:, :, 2::-1]  # BGR -> RGB

    mask = alpha[:, :, 0] > 0.01
    if not np.any(mask):
        return

    frame[mask] = (frame[mask].astype(np.float32) * (1 - alpha[mask]) +
                   ann_rgb[mask].astype(np.float32) * alpha[mask]).astype(np.uint8)


def get_recordings_dir() -> str:
    """Get the recordings output directory, creating it if needed."""
    if IS_MACOS:
        base = os.path.expanduser("~/Movies/ScreenCapture")
    else:
        base = os.path.expanduser("~/Videos/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def generate_filename() -> str:
    """Generate a timestamped filename for the recording."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"recording_{ts}.mp4"


def _get_ffmpeg_path():
    """Get the ffmpeg binary path from imageio-ffmpeg."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


class ScreenRecorder(QThread):
    """Records a screen region to MP4 with system audio + microphone."""

    recording_stopped = pyqtSignal(str)   # emits saved file path
    recording_error = pyqtSignal(str)     # emits error message

    def __init__(self, region: dict, output_path: str, fps: int = 30,
                 dpr: float = 1.0, logical_origin: tuple = (0, 0),
                 audio_offset_ms: int = 0):
        super().__init__()
        self.region = dict(region)
        self.output_path = output_path
        self.fps = fps
        self.dpr = dpr
        self.logical_origin = logical_origin
        # +ms delays audio (use if voice is ahead of lips); -ms advances audio
        # (use if voice lags behind lips). The user-tunable "sync offset".
        self.audio_offset_ms = int(audio_offset_ms)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        self._pause_intervals = []  # List of (start, end) wall-clock offsets
        self._pause_start = None
        self._recording_start = None
        self._total_pause_duration = 0.0  # cumulative pause seconds (for PTS)

        # Webcam and annotation sources (set from main.py)
        self._webcam = None       # WebcamCapture instance
        self._webcam_pos = None   # (x, y, radius) in physical pixels
        self._webcam_lock = threading.Lock()
        self._annotation_overlay = None  # RecordingAnnotationOverlay instance

        # Audio capture refs (set after creation in run())
        self._sys_capture = None
        self._mic_capture = None

        # H.264 requires even dimensions
        self.region["width"] = self.region["width"] - (self.region["width"] % 2)
        self.region["height"] = self.region["height"] - (self.region["height"] % 2)

    def stop(self):
        """Signal the recording loop to stop."""
        # Resume if paused so the loop can exit
        self._pause_event.set()
        self._stop_event.set()

    def pause(self):
        """Pause recording."""
        if self._pause_event.is_set():
            self._pause_start = time.perf_counter()
            self._pause_event.clear()
            # Pause audio
            if self._sys_capture:
                self._sys_capture.set_paused(True)
            if self._mic_capture:
                self._mic_capture.set_paused(True)

    def resume(self):
        """Resume recording."""
        if not self._pause_event.is_set():
            if self._pause_start and self._recording_start:
                pause_offset_start = self._pause_start - self._recording_start
                pause_offset_end = time.perf_counter() - self._recording_start
                self._pause_intervals.append((pause_offset_start, pause_offset_end))
                # Track cumulative pause for video PTS calculation
                self._total_pause_duration += (time.perf_counter() - self._pause_start)
            self._pause_start = None
            # Resume audio
            if self._sys_capture:
                self._sys_capture.set_paused(False)
            if self._mic_capture:
                self._mic_capture.set_paused(False)
            self._pause_event.set()

    def set_mic_muted(self, muted: bool):
        """Mute/unmute microphone."""
        if self._mic_capture:
            self._mic_capture.set_muted(muted)

    def set_webcam(self, webcam):
        """Set or clear the webcam capture source."""
        with self._webcam_lock:
            self._webcam = webcam

    def set_webcam_position(self, x: int, y: int, radius: int):
        """Set webcam PiP position in physical pixels."""
        with self._webcam_lock:
            self._webcam_pos = (x, y, radius)

    def set_annotation_overlay(self, overlay):
        """Set the annotation overlay for drawing compositing."""
        self._annotation_overlay = overlay

    def run(self):
        temp_video = None
        temp_audio = None
        temp_container = None
        try:
            # Use recordings dir instead of /var/folders/ - macOS can fail with
            # "Invalid argument" (errno 22) when FFmpeg/PyAV writes to system temp
            temp_dir = get_recordings_dir()
            temp_video = os.path.join(temp_dir, f"_temp_video_{os.getpid()}_{int(time.time())}.mp4")
            temp_audio = os.path.join(temp_dir, f"_temp_audio_{os.getpid()}_{int(time.time())}.wav")

            # --- Audio setup (48 kHz, OBS-standard) ---
            sys_capture = None
            mic_capture = None
            audio_sample_rate = 48000
            t_audio_start = time.perf_counter()

            # System audio via ScreenCaptureKit (macOS 13+)
            if IS_MACOS:
                try:
                    from audio_helper import SystemAudioCapture
                    sys_capture = SystemAudioCapture(sample_rate=audio_sample_rate)
                    sys_capture.start()
                except Exception as e:
                    print(f"System audio unavailable: {e}")
                    sys_capture = None

            # Microphone via sounddevice (blocking reads — no cffi callback)
            try:
                from audio_helper import MicCapture
                mic_capture = MicCapture(sample_rate=audio_sample_rate)
                mic_capture.start()
            except Exception as e:
                print(f"Mic capture unavailable: {e}")
                mic_capture = None

            # Store refs for pause/mute control from main thread
            self._sys_capture = sys_capture
            self._mic_capture = mic_capture

            # --- Cursor setup ---
            cursor_black = cursor_white = None
            main_screen_h = 0
            if _HAS_CURSOR:
                cursor_black, cursor_white = _build_cursor_masks(self.dpr)
                main_screen_h = NSScreen.mainScreen().frame().size.height

            # --- Video recording with PyAV (ultrafast preset) ---
            # Use MKV container — MP4 muxer triggers errno 22 on macOS with PyAV
            frame_interval = 1.0 / self.fps
            temp_container = temp_video.replace('.mp4', '.mkv')  # Write to MKV, remux to MP4 later
            container = av.open(temp_container, mode='w', format='matroska')
            stream = container.add_stream('libx264', rate=self.fps)
            stream.time_base = Fraction(1, self.fps)
            stream.width = self.region['width']
            stream.height = self.region['height']
            stream.pix_fmt = 'yuv420p'
            stream.options = {'preset': 'ultrafast', 'crf': '23'}

            # --- Encoder on its own thread so a slow encode never stalls frame
            #     capture (smoother video). The capture loop only grabs +
            #     composites + queues; this worker encodes + muxes. PyAV's
            #     stream/container are touched ONLY here. ---
            import queue as _queue
            frame_q = _queue.Queue(maxsize=max(8, self.fps * 3))
            _enc_err = {}

            def _encode_worker():
                try:
                    while True:
                        vf = frame_q.get()
                        if vf is None:
                            break
                        for packet in stream.encode(vf):
                            container.mux(packet)
                    for packet in stream.encode():   # flush
                        container.mux(packet)
                except Exception as e:
                    _enc_err["e"] = e

            enc_thread = threading.Thread(target=_encode_worker, daemon=True,
                                          name="sc-encoder")
            enc_thread.start()

            # Clock starts when we begin capturing frames — trim audio lead to match
            self._recording_start = time.perf_counter()
            last_pts = -1  # Ensure strictly increasing PTS (duplicates cause mux errno 22)

            with mss.mss() as sct:
                while not self._stop_event.is_set():
                    # Block here while paused
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break

                    t0 = time.perf_counter()

                    screenshot = sct.grab(self.region)
                    frame = np.array(screenshot)[:, :, :3][:, :, ::-1]  # BGRA -> RGB

                    # Overlay mouse cursor
                    if cursor_black is not None:
                        try:
                            pos = NSEvent.mouseLocation()
                            cx = int((pos.x - self.logical_origin[0]) * self.dpr)
                            cy = int(((main_screen_h - pos.y) - self.logical_origin[1]) * self.dpr)
                            _overlay_cursor(frame, cx, cy, cursor_black, cursor_white)
                        except Exception:
                            pass

                    # NOTE: the webcam is NOT composited here. The on-screen
                    # circular preview window is already part of the captured
                    # framebuffer, so compositing again produced a SECOND,
                    # offset circle. Capturing the live preview (WYSIWYG) gives
                    # exactly one circle, right where the user placed it.

                    # Composite annotations
                    if self._annotation_overlay:
                        try:
                            ann_image = self._annotation_overlay.get_annotation_image()
                            if ann_image is not None:
                                _composite_annotation(frame, ann_image, self.dpr)
                        except Exception:
                            pass

                    video_frame = av.VideoFrame.from_ndarray(frame, format='rgb24')
                    # Wall-clock PTS for lip sync; max() ensures monotonic (avoids mux errno 22)
                    active_elapsed = (time.perf_counter()
                                      - self._recording_start
                                      - self._total_pause_duration)
                    target_pts = int(active_elapsed * self.fps)
                    pts = max(last_pts + 1, target_pts)
                    video_frame.pts = pts

                    # Hand off to the encoder thread. The queue absorbs short
                    # encode bursts; if it's momentarily full, drop this frame
                    # (playback just holds the previous one) rather than stall
                    # capture — which keeps motion smooth.
                    try:
                        frame_q.put_nowait(video_frame)
                        last_pts = pts
                    except _queue.Full:
                        pass

                    if _enc_err:
                        raise _enc_err["e"]

                    elapsed = time.perf_counter() - t0
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            # Signal the encoder to flush + finish, then close the container.
            frame_q.put(None)
            enc_thread.join(timeout=30)
            container.close()
            if _enc_err:
                raise _enc_err["e"]

            # --- Stop audio ---
            if sys_capture:
                sys_capture.stop()
            if mic_capture:
                mic_capture.stop()

            # --- Mix, process, and mux audio ---
            has_audio = False
            if sys_capture or mic_capture:
                from audio_helper import mix_and_master, remove_paused_segments

                sys_stereo = sys_capture.get_audio_stereo() if sys_capture else np.zeros((0, 2), dtype=np.float32)
                mic_mono = mic_capture.get_audio_mono() if mic_capture else np.array([], dtype=np.float32)

                print(f"Audio captured: system={len(sys_stereo)} frames, mic={len(mic_mono)} samples")

                mixed_stereo = mix_and_master(sys_stereo, mic_mono, sample_rate=audio_sample_rate)

                # Trim audio lead: audio starts before first video frame; remove excess for sync
                audio_lead = max(0.0, self._recording_start - t_audio_start)
                trim_samples = int(audio_lead * audio_sample_rate)
                if trim_samples > 0 and len(mixed_stereo) > trim_samples:
                    mixed_stereo = mixed_stereo[trim_samples:]

                # Remove paused segments from audio
                if self._pause_intervals:
                    mixed_stereo = remove_paused_segments(
                        mixed_stereo, self._pause_intervals, audio_sample_rate
                    )

                # --- Lip-sync offset (user-tunable, like OBS "sync offset") ---
                # +ms: delay audio (pad silence in front). -ms: advance audio
                # (trim from front) — use this if your voice lags your lips.
                offset_samples = int(self.audio_offset_ms / 1000.0 * audio_sample_rate)
                if offset_samples > 0:
                    mixed_stereo = np.concatenate(
                        [np.zeros((offset_samples, 2), dtype=np.float32), mixed_stereo]
                    )
                elif offset_samples < 0:
                    cut = min(-offset_samples, len(mixed_stereo))
                    mixed_stereo = mixed_stereo[cut:]

                # --- Anti-drift: force audio length == actual video length so
                # the two can't drift apart over a long recording (ends stay
                # locked). last_pts is in 1/fps units. ---
                if last_pts >= 0:
                    video_samples = int(((last_pts + 1) / self.fps) * audio_sample_rate)
                    if video_samples > 0:
                        if len(mixed_stereo) > video_samples:
                            mixed_stereo = mixed_stereo[:video_samples]
                        elif len(mixed_stereo) < video_samples:
                            pad = video_samples - len(mixed_stereo)
                            mixed_stereo = np.concatenate(
                                [mixed_stereo, np.zeros((pad, 2), dtype=np.float32)]
                            )

                if len(mixed_stereo) > 0:
                    has_audio = True
                    audio_int16 = (mixed_stereo * 32767).clip(-32768, 32767).astype(np.int16)
                    with wave.open(temp_audio, 'wb') as wf:
                        wf.setnchannels(2)
                        wf.setsampwidth(2)
                        wf.setframerate(audio_sample_rate)
                        wf.writeframes(audio_int16.tobytes())

            # temp_container is MKV; ffmpeg remuxes to MP4 (with or without audio)
            ffmpeg = _get_ffmpeg_path()
            if has_audio:
                cmd = [
                    ffmpeg, '-y',
                    '-i', temp_container,
                    '-i', temp_audio,
                    '-c:v', 'copy',
                    '-c:a', 'aac',
                    '-b:a', '256k',
                    '-ar', str(audio_sample_rate),
                    '-ac', '2',
                    '-shortest',
                    self.output_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    stderr = result.stderr.decode(errors='replace')
                    print(f"[audio] ffmpeg muxing failed (rc={result.returncode}): {stderr[:500]}")
                    # Fallback: video only (mkv to mp4)
                    cmd = [ffmpeg, '-y', '-i', temp_container, '-c:v', 'copy', self.output_path]
                    subprocess.run(cmd, capture_output=True, timeout=60)
            else:
                cmd = [ffmpeg, '-y', '-i', temp_container, '-c:v', 'copy', self.output_path]
                subprocess.run(cmd, capture_output=True, timeout=60)

            self.recording_stopped.emit(self.output_path)

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[recorder] ERROR: {e}\n{tb}")
            self.recording_error.emit(str(e))
        finally:
            for f in [temp_container, temp_audio]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass


class _DragHandle(QWidget):
    """Small pill-shaped grip for dragging the recording region.

    Separate top-level widget that sits above the recording border.
    Uses a window mask so only the visible pill area receives mouse
    events — avoids the macOS NSView crash that occurs when transparent
    pixels receive events on frameless WA_TranslucentBackground windows.
    """

    HANDLE_W = 60
    HANDLE_H = 20

    moved = pyqtSignal(int, int)  # dx, dy in screen pixels

    def __init__(self):
        super().__init__()
        self._drag_start = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.HANDLE_W, self.HANDLE_H)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        # Mask to pill shape — only the visible area receives events
        path = QPainterPath()
        path.addRoundedRect(
            0.0, 0.0, float(self.HANDLE_W), float(self.HANDLE_H), 6.0, 6.0
        )
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def showEvent(self, event):
        super().showEvent(event)
        # Defer NSWindow config to after the event loop processes the show
        QTimer.singleShot(0, self._apply_nswindow)

    def _apply_nswindow(self):
        _configure_nswindow(self, click_through=False, level=26)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(230, 57, 70, 220))
        p.drawRoundedRect(0, 0, self.width(), self.height(), 6, 6)
        # Three-dot grip indicator
        p.setBrush(QColor(255, 255, 255, 200))
        cx, cy = self.width() // 2, self.height() // 2
        for dx in [-8, 0, 8]:
            p.drawEllipse(cx + dx - 2, cy - 2, 4, 4)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            pos = event.globalPosition().toPoint()
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            self._drag_start = pos
            self.moved.emit(dx, dy)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class RecordingFrame(QWidget):
    """Click-through overlay showing the recording region border.

    The border itself is purely visual and fully click-through so the
    user can interact with the content beneath. A separate _DragHandle
    widget provides a small grip above the border for repositioning
    the recording area during recording.
    """

    region_moved = pyqtSignal(QRect)  # emits new logical region rect

    def __init__(self, region_rect: QRect):
        super().__init__()
        self._border = 3
        pad = self._border + 2
        self._pad = pad
        self._region_rect = QRect(region_rect)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setGeometry(
            region_rect.x() - pad,
            region_rect.y() - pad,
            region_rect.width() + 2 * pad,
            region_rect.height() + 2 * pad,
        )

        # Draggable grip above the border
        self._handle = _DragHandle()
        self._handle.moved.connect(self._on_handle_dragged)
        self._position_handle()

    def _position_handle(self):
        """Place the drag handle at the top-center of the recording region."""
        hw = _DragHandle.HANDLE_W
        x = self._region_rect.x() + (self._region_rect.width() - hw) // 2
        y = self._region_rect.y() - _DragHandle.HANDLE_H - 4
        self._handle.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        # Click-through: events pass to content beneath (no NSView crash)
        _configure_nswindow(self, click_through=True, level=25)
        self._handle.show()

    def _on_handle_dragged(self, dx, dy):
        """Move the entire recording frame and handle by (dx, dy)."""
        self._handle.move(self._handle.x() + dx, self._handle.y() + dy)
        self.move(self.x() + dx, self.y() + dy)
        self._region_rect.translate(dx, dy)
        self.region_moved.emit(QRect(self._region_rect))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#e63946"), self._border))
        p.setBrush(Qt.BrushStyle.NoBrush)
        off = self._border // 2
        p.drawRect(off, off, self.width() - self._border, self.height() - self._border)
        p.end()

    def close(self):
        if hasattr(self, '_handle') and self._handle:
            self._handle.close()
        super().close()


class StopRecordingButton(QWidget):
    """Floating stop button shown during recording."""

    stop_clicked = pyqtSignal()

    def __init__(self, screen_geo):
        super().__init__()
        self._drag_pos = None
        self._elapsed = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(140, 44)

        # Position at top-center of the screen
        x = screen_geo.x() + (screen_geo.width() - 140) // 2
        y = screen_geo.y() + 20
        self.move(x, y)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        self._elapsed += 1
        self.update()

    def _format_time(self) -> str:
        m, s = divmod(self._elapsed, 60)
        return f"{m}:{s:02d}"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Red rounded background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#e63946"))
        p.drawRoundedRect(0, 0, self.width(), self.height(), 10, 10)

        # White stop square
        p.setBrush(QColor("white"))
        p.drawRoundedRect(14, 14, 16, 16, 2, 2)

        # Elapsed time text
        p.setPen(QColor("white"))
        p.setFont(QFont(SYSTEM_FONT, 16, QFont.Weight.Bold))
        p.drawText(42, 0, self.width() - 48, self.height(),
                   Qt.AlignmentFlag.AlignVCenter, self._format_time())

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.pos().x() < 40:
                self.stop_clicked.emit()
                return
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self.stop_clicked.emit()


class RecordingAnnotationOverlay(QWidget):
    """Transparent overlay for drawing annotations during recording.

    When drawing mode is active, intercepts mouse events.
    When inactive, is click-through so the user can interact with content.
    """

    def __init__(self, region_rect: QRect):
        super().__init__()
        self._region = region_rect
        self._drawing_active = False
        self._lock = threading.Lock()

        from tools import (
            PenTool, ArrowTool, RectangleTool, HighlighterTool, TextTool,
            DrawingAction, draw_action as _draw_action
        )
        self._draw_action = _draw_action
        self._tool_classes = {
            "pen": PenTool, "arrow": ArrowTool,
            "rectangle": RectangleTool, "highlighter": HighlighterTool,
        }

        self.actions = []
        self.current_tool = None
        self._current_color = QColor("#e63946")
        self._is_drawing = False

        # Annotation image for compositing (ARGB32 for alpha)
        self._annotation_image = QImage(
            region_rect.width(), region_rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self._annotation_image.fill(Qt.GlobalColor.transparent)

        # Numpy buffer for recorder thread (no Qt cross-thread access)
        self._annotation_buffer = np.zeros((region_rect.height(), region_rect.width(), 4), dtype=np.uint8)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(region_rect)
        self.setMouseTracking(True)

    def showEvent(self, event):
        super().showEvent(event)
        _configure_nswindow(self, click_through=True, level=25)

    def _set_macos_properties(self, click_through=True):
        _configure_nswindow(self, click_through=click_through, level=25)

    def set_drawing_active(self, active: bool):
        """Toggle drawing mode on/off."""
        self._drawing_active = active
        if IS_MACOS:
            self._set_macos_properties(click_through=not active)
        elif IS_WINDOWS:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if active:
                style &= ~WS_EX_TRANSPARENT
            else:
                style |= WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

        if active:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_tool(self, tool_name: str):
        """Set the current drawing tool by name."""
        tool_class = self._tool_classes.get(tool_name)
        if tool_class:
            if tool_name == "highlighter":
                self.current_tool = tool_class(QColor(255, 255, 0), 20)
            else:
                self.current_tool = tool_class(self._current_color)

    def set_color(self, color: QColor):
        self._current_color = color
        if self.current_tool:
            self.current_tool.color = color

    def clear_annotations(self):
        with self._lock:
            self.actions.clear()
        self._update_annotation_image()
        self.update()

    def get_annotation_image(self):
        """Thread-safe getter: returns numpy (h,w,4) BGRA or None. No Qt objects."""
        with self._lock:
            if not self.actions:
                return None
            return self._annotation_buffer.copy()

    def _update_annotation_image(self):
        """Re-render all annotations to the internal QImage and numpy buffer."""
        with self._lock:
            self._annotation_image.fill(Qt.GlobalColor.transparent)
            if self.actions:
                p = QPainter(self._annotation_image)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                for action in self.actions:
                    self._draw_action(p, action)
                p.end()
                # Copy to numpy buffer for recorder thread (main thread only)
                ptr = self._annotation_image.bits()
                if ptr is not None:
                    ptr.setsize(self._annotation_image.sizeInBytes())
                    np.copyto(
                        self._annotation_buffer,
                        np.frombuffer(ptr, dtype=np.uint8).reshape(
                            self._annotation_image.height(),
                            self._annotation_image.width(), 4
                        )
                    )

    def paintEvent(self, event):
        if not self.actions and not self._is_drawing:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for action in self.actions:
            self._draw_action(p, action)
        if self.current_tool and self._is_drawing:
            self.current_tool.draw_preview(p)
        p.end()

    def mousePressEvent(self, event):
        if not self._drawing_active or not self.current_tool:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.current_tool.on_mouse_press(event.pos())
            self._is_drawing = True
            self.update()

    def mouseMoveEvent(self, event):
        if not self._drawing_active or not self.current_tool or not self._is_drawing:
            return
        self.current_tool.on_mouse_move(event.pos())
        self.update()

    def mouseReleaseEvent(self, event):
        if not self._drawing_active or not self.current_tool:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            action = self.current_tool.on_mouse_release(event.pos())
            if action:
                with self._lock:
                    self.actions.append(action)
                self._update_annotation_image()
            self._is_drawing = False
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.set_drawing_active(False)
