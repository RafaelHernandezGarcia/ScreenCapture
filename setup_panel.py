"""Pre-recording setup panel — Loom-style.

After you pick a region, this panel lets you calmly set things up: toggle the
webcam (and drag the circle where you want it), toggle the mic, then press
"Start recording" to kick off the 3-2-1 countdown. Frameless, rounded, soft
shadow, hand-drawn crisp icons.
"""
import sys

from PyQt6.QtCore import Qt, QRect, QSize, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPainterPath, QPen, QBrush, QIcon, QPixmap,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGraphicsDropShadowEffect,
)

SYSTEM_FONT = ".AppleSystemUIFont" if sys.platform == "darwin" else "Segoe UI"

_CORAL = "#F24E2E"      # Loom-ish record color
_CORAL_HOVER = "#D8401F"
_GREEN = "#2EB67D"
_INK = "#1D1D1F"
_SUBTLE = "#6E6E73"


def _icon(kind: str, color="#1D1D1F", size=22) -> QIcon:
    """Crisp line icons drawn with QPainter (camera / mic / close)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size / 24.0
    if kind == "camera":
        p.drawRoundedRect(QRect(int(2*s), int(7*s), int(13*s), int(10*s)), 3*s, 3*s)
        path = QPainterPath()
        path.moveTo(15*s, 10*s); path.lineTo(21*s, 7*s)
        path.lineTo(21*s, 17*s); path.lineTo(15*s, 14*s); path.closeSubpath()
        p.drawPath(path)
    elif kind == "mic":
        p.drawRoundedRect(QRect(int(9*s), int(3*s), int(6*s), int(11*s)), 3*s, 3*s)
        path = QPainterPath()
        path.moveTo(5*s, 11*s)
        path.cubicTo(5*s, 17*s, 19*s, 17*s, 19*s, 11*s)
        p.drawPath(path)
        p.drawLine(int(12*s), int(17*s), int(12*s), int(21*s))
        p.drawLine(int(8*s), int(21*s), int(16*s), int(21*s))
    elif kind == "close":
        p.drawLine(int(6*s), int(6*s), int(18*s), int(18*s))
        p.drawLine(int(18*s), int(6*s), int(6*s), int(18*s))
    p.end()
    return QIcon(pm)


def _blend(c1, c2, t):
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
    )


class _Switch(QPushButton):
    """An animated iOS-style sliding switch (green on, gray off)."""
    def __init__(self, on=True, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(48, 28)
        self._pos = 1.0 if on else 0.0          # 0 = off (left), 1 = on (right)
        from PyQt6.QtCore import QPropertyAnimation
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(150)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def getKnob(self):
        return self._pos

    def setKnob(self, v):
        self._pos = v
        self.update()

    from PyQt6.QtCore import pyqtProperty as _pp
    knobPos = _pp(float, fget=getKnob, fset=setKnob)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = _blend(QColor("#D1D1D6"), QColor(_GREEN), self._pos)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, w, h, h // 2, h // 2)
        d = h - 6
        x = 3 + (w - d - 6) * self._pos
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(x), 3, d, d)
        p.end()


class _Row(QFrame):
    """A setup row: icon + label + toggle."""
    def __init__(self, icon_kind, label, on, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setStyleSheet("QFrame{background:#F5F5F7; border-radius:12px;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(_icon(icon_kind, _INK, 22).pixmap(22, 22))
        lay.addWidget(ic)
        from PyQt6.QtGui import QFontMetrics
        font = QFont(SYSTEM_FONT, 13, QFont.Weight.DemiBold)
        elided = QFontMetrics(font).elidedText(label, Qt.TextElideMode.ElideRight, 180)
        text = QLabel(elided)
        text.setStyleSheet(f"color:{_INK}; font-size:14px; font-weight:600; background:transparent;")
        text.setFont(font)
        lay.addWidget(text)
        lay.addStretch()
        self.toggle = _Switch(on)
        lay.addWidget(self.toggle)


class SetupPanel(QWidget):
    """The pre-recording setup card."""

    start_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    webcam_toggled = pyqtSignal(bool)
    mic_toggled = pyqtSignal(bool)

    def __init__(self, screen_geo: QRect, camera_name="Webcam", mic_name="Microphone",
                 webcam_on=True, mic_on=True):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Card container (so we can round it + shadow it inside a transparent window)
        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("""
            QFrame#card { background: #FFFFFF; border-radius: 18px; }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40); shadow.setColor(QColor(0, 0, 0, 80)); shadow.setOffset(0, 10)
        card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)  # room for the shadow
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 18)
        lay.setSpacing(12)

        # header
        head = QHBoxLayout()
        title = QLabel("Set up your recording")
        title.setStyleSheet(f"color:{_INK}; background:transparent;")
        title.setFont(QFont(SYSTEM_FONT, 15, QFont.Weight.Bold))
        head.addWidget(title)
        head.addStretch()
        close = QPushButton()
        close.setIcon(_icon("close", _SUBTLE, 18))
        close.setFixedSize(28, 28)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet("QPushButton{border:none;border-radius:14px;background:transparent;}"
                            "QPushButton:hover{background:#EFEFF4;}")
        close.clicked.connect(self.cancel_clicked.emit)
        head.addWidget(close)
        lay.addLayout(head)

        hint = QLabel("Place your camera, then start when ready.")
        hint.setStyleSheet(f"color:{_SUBTLE}; font-size:12px; background:transparent;")
        hint.setFont(QFont(SYSTEM_FONT, 11))
        lay.addWidget(hint)

        self._cam_row = _Row("camera", camera_name, webcam_on)
        self._cam_row.toggle.toggled.connect(self.webcam_toggled.emit)
        lay.addWidget(self._cam_row)

        self._mic_row = _Row("mic", mic_name, mic_on)
        self._mic_row.toggle.toggled.connect(self.mic_toggled.emit)
        lay.addWidget(self._mic_row)

        lay.addSpacing(4)
        start = QPushButton("Start recording")
        start.setCursor(Qt.CursorShape.PointingHandCursor)
        start.setFixedHeight(48)
        start.setFont(QFont(SYSTEM_FONT, 15, QFont.Weight.Bold))
        start.setStyleSheet(f"""
            QPushButton {{ background:{_CORAL}; color:white; border:none;
                           border-radius:12px; font-size:15px; font-weight:700; }}
            QPushButton:hover {{ background:{_CORAL_HOVER}; }}
        """)
        start.clicked.connect(self.start_clicked.emit)
        lay.addWidget(start)

        self.setFixedWidth(360)
        self.adjustSize()
        # position: top-right of the chosen screen, like Loom
        x = screen_geo.x() + screen_geo.width() - self.width() - 28
        y = screen_geo.y() + 70
        self.move(x, y)

    def webcam_on(self):
        return self._cam_row.toggle.isChecked()

    def mic_on(self):
        return self._mic_row.toggle.isChecked()

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin":
            try:
                from recorder import _configure_clickable_panel
                _configure_clickable_panel(self, level=25)
            except Exception:
                pass
