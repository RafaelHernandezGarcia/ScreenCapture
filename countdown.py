"""
Countdown Overlay - Shows 3, 2, 1 before screen recording starts
"""
import sys
from PyQt6.QtCore import Qt, QTimer, QRect, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFont
from PyQt6.QtWidgets import QWidget

SYSTEM_FONT = ".AppleSystemUIFont" if sys.platform == "darwin" else "Segoe UI"


class CountdownOverlay(QWidget):
    """Fullscreen transparent overlay showing a 3-2-1 countdown."""

    countdown_finished = pyqtSignal()

    def __init__(self, screen_geo: QRect, region_rect: QRect = None):
        super().__init__()
        self._count = 3
        self._screen_geo = screen_geo
        # The number is centered on the selected recording region (if given),
        # not the whole screen — it should sit over what you're recording.
        self._region_rect = region_rect

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Show without activating so macOS doesn't switch to the app's home
        # Space and reveal the desktop wallpaper behind the countdown.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen_geo)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(800)

    def showEvent(self, event):
        super().showEvent(event)
        # Keep the countdown on every Space and above normal windows so it
        # doesn't pull the user to another Space (desktop-reveal bug).
        try:
            from recorder import _configure_nswindow
            _configure_nswindow(self, click_through=False, level=25)
        except Exception:
            pass

    def _tick(self):
        self._count -= 1
        if self._count <= 0:
            self._timer.stop()
            self.close()
            self.countdown_finished.emit()
        else:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent background
        p.fillRect(self.rect(), QColor(0, 0, 0, 80))

        # Center on the selected region (mapped into widget-local coords),
        # falling back to the screen center if no region was provided.
        if self._region_rect is not None:
            cx = self._region_rect.center().x() - self._screen_geo.x()
            cy = self._region_rect.center().y() - self._screen_geo.y()
        else:
            cx = self.width() // 2
            cy = self.height() // 2
        radius = 80
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        # Number
        p.setPen(QColor("white"))
        p.setFont(QFont(SYSTEM_FONT, 72, QFont.Weight.Bold))
        p.drawText(
            QRect(cx - radius, cy - radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter,
            str(self._count),
        )

        p.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._timer.stop()
            self.close()
            # Don't emit countdown_finished — cancels recording
