"""
Webcam Capture - Circular PiP webcam overlay for screen recording.

Captures webcam frames in a thread and provides a draggable circular
preview widget. The ScreenRecorder composites the webcam into video frames.
"""
import sys
import threading
import numpy as np

from PyQt6.QtCore import Qt, QRect, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import (
    QPainter, QColor, QImage, QPixmap, QPen, QRegion, QPainterPath
)
from PyQt6.QtWidgets import QWidget

from platform_utils import IS_MACOS, IS_WINDOWS, make_non_activating

DEFAULT_RADIUS = 80  # pixels (logical)

_CAMERA_CACHE = None  # (index, name) list; enumeration is slow, do it once


def _list_cameras_windows():
    """Windows: real device names via DirectShow (pygrabber), in DirectShow
    index order - the same order cv2.VideoCapture(idx, CAP_DSHOW) uses.

    Runs on its own thread with its own COM apartment: pygrabber needs an
    STA, and the audio stack (soundcard) may already have put the calling
    thread in a different mode ("Cannot change thread mode after it is set").
    """
    result = {}

    def _enum():
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            from pygrabber.dshow_graph import FilterGraph
            names = FilterGraph().get_input_devices()
            result["cams"] = [(i, str(n)) for i, n in enumerate(names)]
        except Exception as e:
            result["err"] = e

    t = threading.Thread(target=_enum, daemon=True, name="sc-cam-enum")
    t.start()
    t.join(timeout=8)
    return result.get("cams", [])


def list_cameras(max_check=5, refresh=False):
    """Detect available cameras as (index, name) tuples.

    On macOS we enumerate via AVFoundation, which lists devices WITHOUT
    Camera permission and without opening them. Probing with OpenCV (the
    old approach) silently fails when Camera permission hasn't been granted
    yet - so it reported "no cameras" even though cameras exist. The actual
    permission prompt happens later, when the webcam is turned on.

    On Windows we ask DirectShow for the device names (fast, no camera is
    opened). Elsewhere, or as a fallback, we probe with OpenCV.
    """
    global _CAMERA_CACHE
    if _CAMERA_CACHE is not None and not refresh:
        return list(_CAMERA_CACHE)

    cams = []
    if IS_MACOS:
        try:
            from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
            devices = AVCaptureDevice.devicesWithMediaType_(AVMediaTypeVideo)
            cams = [(i, str(dev.localizedName())) for i, dev in enumerate(devices)]
        except Exception:
            cams = []
    elif IS_WINDOWS:
        cams = _list_cameras_windows()

    if not cams:
        import cv2
        backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY
        for idx in range(max_check):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    cams.append((idx, f"Camera {idx}"))
            else:
                cap.release()

    _CAMERA_CACHE = list(cams)
    return cams


class WebcamCapture(QThread):
    """Captures webcam frames in a background thread."""

    # Fired every time a fresh frame is stored, so the preview can repaint
    # exactly when a new frame is ready (event-driven) instead of on a
    # free-running timer that beats against the camera's rate and judders.
    frame_ready = pyqtSignal()

    # Capture mode we ASK the camera for. Without this, OpenCV opens the
    # camera in its default mode - usually the highest native resolution -
    # which delivers full-res uncompressed frames at a sluggish rate and
    # makes motion look laggy (the better the camera, the worse it gets).
    # 720p30 is exactly what Google Meet / WhatsApp request: smooth, light,
    # and far more than enough for a small circular PiP.
    CAPTURE_WIDTH = 1280
    CAPTURE_HEIGHT = 720

    def __init__(self, device_index=0, fps=30):
        super().__init__()
        self._device_index = device_index
        self._fps = fps
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest_frame = None  # BGR numpy array

    def get_latest_frame(self):
        """Thread-safe getter for the most recent webcam frame (BGR)."""
        with self._lock:
            if self._latest_frame is not None:
                return self._latest_frame.copy()
            return None

    def stop(self):
        self._stop_event.set()

    def _open_and_configure(self, cv2, index):
        """Open a camera at `index` and ask it for a smooth, low-latency mode.

        Returns an opened VideoCapture or None. Setting the capture mode is
        what keeps motion fluid: a fixed 720p30 with a 1-frame buffer means
        we always read the FRESHEST frame at a steady rate, instead of
        draining a deep buffer of stale full-res frames.
        """
        cap = None
        if IS_WINDOWS:
            # DirectShow opens in a few seconds and honours the 720p30 /
            # buffer-size requests below; the default MSMF backend is often
            # much slower to open and ignores CAP_PROP_BUFFERSIZE.
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = None
        if cap is None:
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.CAPTURE_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        # Keep the internal buffer shallow so read() returns the latest frame
        # (low latency) rather than a queued, already-stale one. Not every
        # backend honors this, so it's best-effort.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def run(self):
        import cv2
        import time

        cap = self._open_and_configure(cv2, self._device_index)
        if cap is None:
            print(f"[webcam] Camera index {self._device_index} could not be opened")
            # Try fallback indices
            for fallback in range(5):
                if fallback == self._device_index:
                    continue
                cap = self._open_and_configure(cv2, fallback)
                if cap is not None:
                    print(f"[webcam] Fell back to camera index {fallback}")
                    break
            if cap is None:
                print("[webcam] No working camera found")
                return

        try:
            while not self._stop_event.is_set():
                # read() blocks until the next sensor frame, so it paces the
                # loop to the camera's real frame rate (~30fps) on its own -
                # no artificial sleep needed, which only added latency.
                ret, frame = cap.read()
                if ret and frame is not None:
                    # Flip horizontally for mirror effect
                    frame = cv2.flip(frame, 1)
                    with self._lock:
                        self._latest_frame = frame
                    self.frame_ready.emit()  # wake the preview to repaint
                else:
                    time.sleep(0.005)  # transient read failure; back off briefly
        finally:
            cap.release()


class WebcamPreviewWidget(QWidget):
    """Draggable circular webcam preview overlay.

    Shows a live circular webcam feed that the user can drag around.
    Reports its position so the ScreenRecorder composites the webcam
    into the correct location in the video.
    """

    position_changed = pyqtSignal(int, int, int)  # x, y, radius (logical)

    def __init__(self, webcam_capture: WebcamCapture,
                 recording_rect: QRect, dpr: float = 1.0):
        super().__init__()
        self._webcam = webcam_capture
        self._recording_rect = recording_rect
        self._dpr = dpr
        self._radius = DEFAULT_RADIUS
        self._drag_pos = None

        diameter = self._radius * 2

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        make_non_activating(self)  # dragging the circle must not steal focus
        self.setFixedSize(diameter + 6, diameter + 6)  # +6 for border

        # Start tucked into the bottom-right corner, hugging the recording
        # border. The visible ring sits 3px inside the widget box, so we offset
        # by that. Right and bottom margins are computed identically, but a
        # tiny extra downward bias balances a sub-pixel corner-rounding
        # asymmetry that reads as "closer to the right than the bottom".
        margin = 12
        bottom_bias = 3            # nudge the circle a hair lower to look even
        ring_inset = 3
        x = recording_rect.right() - self.width() + ring_inset - margin
        y = recording_rect.bottom() - self.height() + ring_inset - margin + bottom_bias
        cx, cy = self._clamp_pos(x, y)
        # A move() issued before the native window is realized drifts upward
        # ~20px on macOS (Qt reports it lower than requested), which made the
        # circle sit too high - tight on the right but loose on the bottom. We
        # stash the target and re-assert it in _apply_nswindow once the window
        # exists, where the position sticks exactly.
        self._target_pos = (int(cx), int(cy))
        self.move(int(cx), int(cy))

        # Circular mask
        self._update_mask()

        # Repaint when a fresh camera frame lands (smooth, no timer beating).
        # A slow fallback timer keeps it alive if the camera stalls.
        if self._webcam is not None:
            self._webcam.frame_ready.connect(self.update)
        self._opening = True  # show a "Starting camera" hint until frames arrive
        self._refresh = QTimer(self)
        self._refresh.timeout.connect(self.update)
        self._refresh.start(200)  # fallback only; frames drive the repaint

    def _update_mask(self):
        diameter = self._radius * 2 + 6
        region = QRegion(0, 0, diameter, diameter, QRegion.RegionType.Ellipse)
        self.setMask(region)

    def _clamp_pos(self, x, y):
        """Keep the whole circle inside the recording region."""
        r = self._recording_rect
        w, h = self.width(), self.height()
        max_x = r.left() + max(0, r.width() - w)
        max_y = r.top() + max(0, r.height() - h)
        x = min(max(int(x), r.left()), max_x)
        y = min(max(int(y), r.top()), max_y)
        return x, y

    def showEvent(self, event):
        super().showEvent(event)
        self._emit_position()
        # Defer NSWindow config to after the event loop processes the show,
        # ensuring the native window is fully constructed first.
        QTimer.singleShot(0, self._apply_nswindow)

    def _apply_nswindow(self):
        from recorder import _configure_nswindow
        _configure_nswindow(self, click_through=False, level=25)
        # Re-assert the target position now that the native window is realized;
        # the pre-show move() drifted ~20px up on macOS. This makes it land
        # exactly, so the circle's right and bottom gaps match.
        if getattr(self, "_target_pos", None) is not None:
            self.move(*self._target_pos)
        # Drop the window shadow: a circular translucent window leaves a
        # shadow "ghost" trailing behind it while you drag it on macOS.
        try:
            import ctypes as _ct
            import objc
            ptr = int(self.winId())
            if ptr:
                win = objc.objc_object(c_void_p=_ct.c_void_p(ptr)).window()
                if win is not None:
                    win.setHasShadow_(False)
        except Exception:
            pass

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = self.width() // 2
        radius = self._radius

        # Draw border circle
        p.setPen(QPen(QColor(255, 255, 255, 200), 3))
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawEllipse(3, 3, radius * 2, radius * 2)

        # Draw webcam frame
        frame = self._webcam.get_latest_frame() if self._webcam else None
        if frame is not None:
            import cv2
            # Resize and crop to square
            h, w = frame.shape[:2]
            side = min(h, w)
            y_off = (h - side) // 2
            x_off = (w - side) // 2
            square = frame[y_off:y_off+side, x_off:x_off+side]
            resized = cv2.resize(square, (radius * 2, radius * 2))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

            qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                          rgb.strides[0], QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)

            # Clip to circle
            path = QPainterPath()
            path.addEllipse(3, 3, radius * 2, radius * 2)
            p.setClipPath(path)
            p.drawPixmap(3, 3, pixmap)
            p.setClipping(False)
            self._opening = False
        elif self._opening:
            # Camera still warming up (DirectShow takes a few seconds):
            # say so instead of showing a black hole.
            from PyQt6.QtGui import QFont
            from platform_utils import SYSTEM_FONT
            p.setPen(QColor(255, 255, 255, 210))
            p.setFont(QFont(SYSTEM_FONT, 10))
            p.drawText(QRect(3, 3, radius * 2, radius * 2),
                       Qt.AlignmentFlag.AlignCenter, "Starting\ncamera...")

        # Redraw border on top
        p.setPen(QPen(QColor(255, 255, 255, 200), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(3, 3, radius * 2, radius * 2)

        p.end()

    def _emit_position(self):
        """Emit position in physical pixels relative to the recording region."""
        # Center of the webcam circle in screen coordinates
        cx = self.x() + self.width() // 2
        cy = self.y() + self.height() // 2
        # Convert to relative to recording region, then to physical pixels
        rx = int((cx - self._recording_rect.x()) * self._dpr)
        ry = int((cy - self._recording_rect.y()) * self._dpr)
        rr = int(self._radius * self._dpr)
        self.position_changed.emit(rx, ry, rr)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            target = event.globalPosition().toPoint() - self._drag_pos
            x, y = self._clamp_pos(target.x(), target.y())  # stay inside region
            self.move(x, y)
            self._emit_position()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def enterEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
