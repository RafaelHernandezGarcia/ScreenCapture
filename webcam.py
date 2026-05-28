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

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

DEFAULT_RADIUS = 80  # pixels (logical)


def list_cameras(max_check=5):
    """Detect available cameras as (index, name) tuples.

    On macOS we enumerate via AVFoundation, which lists devices WITHOUT
    Camera permission and without opening them. Probing with OpenCV (the
    old approach) silently fails when Camera permission hasn't been granted
    yet — so it reported "no cameras" even though cameras exist. The actual
    permission prompt happens later, when the webcam is turned on.

    On other platforms we probe with OpenCV.
    """
    if IS_MACOS:
        try:
            from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
            devices = AVCaptureDevice.devicesWithMediaType_(AVMediaTypeVideo)
            cams = [(i, str(dev.localizedName())) for i, dev in enumerate(devices)]
            if cams:
                return cams
        except Exception:
            pass
        # fall through to OpenCV probing if AVFoundation found nothing

    import cv2
    cameras = []
    for idx in range(max_check):
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                cameras.append((idx, f"Camera {idx}"))
        else:
            cap.release()
    return cameras


class WebcamCapture(QThread):
    """Captures webcam frames in a background thread."""

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

    def run(self):
        import cv2
        import time

        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            print(f"[webcam] Camera index {self._device_index} could not be opened")
            # Try fallback indices
            for fallback in range(5):
                if fallback == self._device_index:
                    continue
                cap = cv2.VideoCapture(fallback)
                if cap.isOpened():
                    print(f"[webcam] Fell back to camera index {fallback}")
                    break
                cap.release()
            else:
                print("[webcam] No working camera found")
                return

        frame_interval = 1.0 / self._fps

        try:
            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                ret, frame = cap.read()
                if ret and frame is not None:
                    # Flip horizontally for mirror effect
                    frame = cv2.flip(frame, 1)
                    with self._lock:
                        self._latest_frame = frame

                elapsed = time.perf_counter() - t0
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
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
        self.setFixedSize(diameter + 6, diameter + 6)  # +6 for border

        # Position at bottom-right of recording region
        x = recording_rect.right() - diameter - 20
        y = recording_rect.bottom() - diameter - 20
        self.move(x, y)

        # Circular mask
        self._update_mask()

        # Refresh timer
        self._refresh = QTimer(self)
        self._refresh.timeout.connect(self.update)
        self._refresh.start(33)  # ~30fps

    def _update_mask(self):
        diameter = self._radius * 2 + 6
        region = QRegion(0, 0, diameter, diameter, QRegion.RegionType.Ellipse)
        self.setMask(region)

    def showEvent(self, event):
        super().showEvent(event)
        self._emit_position()
        # Defer NSWindow config to after the event loop processes the show,
        # ensuring the native window is fully constructed first.
        QTimer.singleShot(0, self._apply_nswindow)

    def _apply_nswindow(self):
        from recorder import _configure_nswindow
        _configure_nswindow(self, click_through=False, level=25)

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
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._emit_position()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def enterEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
