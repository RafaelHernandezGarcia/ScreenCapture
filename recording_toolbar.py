"""
Recording Toolbar - ScreenPal-style floating control bar during recording.

Replaces the simple StopRecordingButton with a full toolbar containing:
pause/resume, stop, timer, mic mute, webcam toggle, draw toggle.
"""
import sys
from PyQt6.QtCore import Qt, QRect, QSize, QTimer, pyqtSignal, QSettings
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QIcon, QPixmap, QPainterPath
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QLabel,
    QFrame, QButtonGroup, QColorDialog, QDialog, QLineEdit,
    QCheckBox, QPushButton, QApplication
)

# Quick-pick palette (shared with overlay)
_PALETTE_COLORS = [
    "#000000", "#ffffff", "#e63946", "#2a9d8f", "#e9c46a", "#264653",
    "#f4a261", "#2ec4b6", "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4",
    "#ffeaa7", "#dfe6e9", "#a29bfe", "#fd79a8", "#636e72", "#b2bec3",
]

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
SYSTEM_FONT = ".AppleSystemUIFont" if IS_MACOS else "Segoe UI"

# --- Icon factory for toolbar buttons ---

def _make_icon(draw_func, size=28, color="#ffffff"):
    """Create a QIcon by calling draw_func(painter, size, color)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_func(p, size, QColor(color))
    p.end()
    return QIcon(pixmap)


def _draw_pause(p, s, c):
    pen = QPen(c, 0)
    p.setPen(pen)
    p.setBrush(c)
    bw = max(3, s // 6)
    gap = max(3, s // 6)
    h = int(s * 0.55)
    y = (s - h) // 2
    x1 = (s - 2 * bw - gap) // 2
    p.drawRoundedRect(x1, y, bw, h, 1, 1)
    p.drawRoundedRect(x1 + bw + gap, y, bw, h, 1, 1)


def _draw_resume(p, s, c):
    pen = QPen(c, 0)
    p.setPen(pen)
    p.setBrush(c)
    margin = s // 4
    path = QPainterPath()
    path.moveTo(margin, margin)
    path.lineTo(s - margin, s // 2)
    path.lineTo(margin, s - margin)
    path.closeSubpath()
    p.drawPath(path)


def _draw_stop(p, s, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    margin = s // 4
    sq = s - 2 * margin
    p.drawRoundedRect(margin, margin, sq, sq, 2, 2)


def _draw_mic_on(p, s, c):
    pen = QPen(c, max(1, s // 14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx = s // 2
    # Mic body
    bw = s // 4
    bh = int(s * 0.35)
    p.drawRoundedRect(cx - bw // 2, s // 5, bw, bh, bw // 2, bw // 2)
    # Arc
    arc_w = int(s * 0.45)
    arc_h = int(s * 0.35)
    p.drawArc(cx - arc_w // 2, int(s * 0.25), arc_w, arc_h, 0, -180 * 16)
    # Stem
    p.drawLine(cx, int(s * 0.25) + arc_h // 2, cx, int(s * 0.8))
    # Base
    p.drawLine(cx - s // 6, int(s * 0.8), cx + s // 6, int(s * 0.8))


def _draw_mic_off(p, s, c):
    _draw_mic_on(p, s, c)
    # Strike-through
    pen = QPen(QColor("#e63946"), max(2, s // 10))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    margin = s // 5
    p.drawLine(margin, margin, s - margin, s - margin)


def _draw_webcam_on(p, s, c):
    pen = QPen(c, max(1, s // 14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Camera body
    bw = int(s * 0.5)
    bh = int(s * 0.4)
    bx = int(s * 0.12)
    by = (s - bh) // 2
    p.drawRoundedRect(bx, by, bw, bh, 3, 3)
    # Lens triangle
    tx = bx + bw + 2
    p.setBrush(c)
    path = QPainterPath()
    path.moveTo(tx, by + 3)
    path.lineTo(s - int(s * 0.12), s // 2)
    path.lineTo(tx, by + bh - 3)
    path.closeSubpath()
    p.drawPath(path)


def _draw_webcam_off(p, s, c):
    _draw_webcam_on(p, s, c)
    pen = QPen(QColor("#e63946"), max(2, s // 10))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    margin = s // 5
    p.drawLine(margin, margin, s - margin, s - margin)


def _draw_pen(p, s, c):
    pen = QPen(c, max(1, s // 14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    m = int(s * 0.2)
    p.drawLine(m, s - m, int(s * 0.4), s - m)
    p.drawLine(m, s - m, s - m, m)
    p.drawLine(int(s * 0.4), s - m, s - int(s * 0.15), int(s * 0.25))
    p.drawLine(s - m, m, s - int(s * 0.15), int(s * 0.25))


class ToolbarButton(QToolButton):
    """Styled button for the recording toolbar."""

    def __init__(self, icon, tooltip, parent=None, checkable=False):
        super().__init__(parent)
        self.setIcon(icon)
        self.setToolTip(tooltip)
        self.setIconSize(QSize(22, 22))
        self.setFixedSize(36, 36)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.15);
            }
            QToolButton:checked {
                background: rgba(255, 255, 255, 0.25);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 0.10);
            }
        """)


class RecordingToolbar(QWidget):
    """ScreenPal-style floating recording toolbar.

    Layout: [ Pause ] [ Stop ] [ 0:00 ] | [ Mic ] [ Cam ] [ Draw ]
    """

    stop_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    resume_clicked = pyqtSignal()
    mic_toggled = pyqtSignal(bool)      # True = muted
    webcam_toggled = pyqtSignal(bool)   # True = on
    draw_toggled = pyqtSignal(bool)     # True = active

    def __init__(self, screen_geo: QRect, recording_rect: QRect = None):
        super().__init__()
        self._drag_pos = None
        self._elapsed = 0
        self._is_paused = False
        self._mic_muted = False
        self._webcam_on = False
        self._draw_active = False
        self._recording_rect = recording_rect

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._build_ui()

        # Position below the recording region, centered
        toolbar_w = self.sizeHint().width()
        toolbar_h = self.sizeHint().height()

        if recording_rect:
            # Place centered below the recording region with a small gap
            x = recording_rect.x() + (recording_rect.width() - toolbar_w) // 2
            y = recording_rect.bottom() + 12

            # If that goes off-screen, place above the region instead
            if y + toolbar_h > screen_geo.y() + screen_geo.height() - 10:
                y = recording_rect.y() - toolbar_h - 12
                # If still off-screen, place at bottom of screen with safe margin
                if y < screen_geo.y():
                    y = screen_geo.y() + screen_geo.height() - toolbar_h - 80
        else:
            # Fallback: bottom-center with generous margin above dock
            x = screen_geo.x() + (screen_geo.width() - toolbar_w) // 2
            y = screen_geo.y() + screen_geo.height() - toolbar_h - 80

        # Ensure x stays on screen
        x = max(screen_geo.x() + 10, min(x, screen_geo.x() + screen_geo.width() - toolbar_w - 10))

        self.move(x, y)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)

        # Pause/Resume button
        self._pause_icon = _make_icon(_draw_pause)
        self._resume_icon = _make_icon(_draw_resume)
        self._pause_btn = ToolbarButton(self._pause_icon, "Pause", self)
        self._pause_btn.clicked.connect(self._on_pause)
        layout.addWidget(self._pause_btn)

        # Stop button
        stop_icon = _make_icon(_draw_stop, color="#e63946")
        self._stop_btn = ToolbarButton(stop_icon, "Stop Recording", self)
        self._stop_btn.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self._stop_btn)

        # Separator
        layout.addSpacing(4)
        sep1 = QFrame(self)
        sep1.setFixedSize(1, 24)
        sep1.setStyleSheet("background: rgba(255, 255, 255, 0.3);")
        layout.addWidget(sep1)
        layout.addSpacing(4)

        # Timer label
        self._timer_label = QLabel("0:00", self)
        self._timer_label.setStyleSheet(
            f"color: white; font-family: '{SYSTEM_FONT}'; "
            "font-size: 14px; font-weight: bold; background: transparent;"
        )
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer_label.setFixedWidth(50)
        layout.addWidget(self._timer_label)

        # Separator
        layout.addSpacing(4)
        sep2 = QFrame(self)
        sep2.setFixedSize(1, 24)
        sep2.setStyleSheet("background: rgba(255, 255, 255, 0.3);")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        # Mic toggle
        self._mic_on_icon = _make_icon(_draw_mic_on)
        self._mic_off_icon = _make_icon(_draw_mic_off)
        self._mic_btn = ToolbarButton(self._mic_on_icon, "Mute Microphone", self, checkable=True)
        self._mic_btn.clicked.connect(self._on_mic)
        layout.addWidget(self._mic_btn)

        # Webcam toggle
        self._cam_on_icon = _make_icon(_draw_webcam_on)
        self._cam_off_icon = _make_icon(_draw_webcam_off)
        self._cam_btn = ToolbarButton(self._cam_off_icon, "Enable Webcam", self, checkable=True)
        self._cam_btn.clicked.connect(self._on_webcam)
        layout.addWidget(self._cam_btn)

        # Draw toggle
        draw_icon = _make_icon(_draw_pen)
        self._draw_btn = ToolbarButton(draw_icon, "Drawing Tools", self, checkable=True)
        self._draw_btn.clicked.connect(self._on_draw)
        layout.addWidget(self._draw_btn)

    def showEvent(self, event):
        super().showEvent(event)
        from recorder import _configure_nswindow
        _configure_nswindow(self, click_through=False, level=25)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Semi-transparent dark rounded background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 30, 30, 220))
        p.drawRoundedRect(self.rect(), 12, 12)
        # Subtle border
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        p.end()

    def _tick(self):
        self._elapsed += 1
        m, s = divmod(self._elapsed, 60)
        self._timer_label.setText(f"{m}:{s:02d}")

    def _on_pause(self):
        if self._is_paused:
            self._is_paused = False
            self._pause_btn.setIcon(self._pause_icon)
            self._pause_btn.setToolTip("Pause")
            self._timer.start(1000)
            self.resume_clicked.emit()
        else:
            self._is_paused = True
            self._pause_btn.setIcon(self._resume_icon)
            self._pause_btn.setToolTip("Resume")
            self._timer.stop()
            self.pause_clicked.emit()

    def _on_mic(self):
        self._mic_muted = self._mic_btn.isChecked()
        if self._mic_muted:
            self._mic_btn.setIcon(self._mic_off_icon)
            self._mic_btn.setToolTip("Unmute Microphone")
        else:
            self._mic_btn.setIcon(self._mic_on_icon)
            self._mic_btn.setToolTip("Mute Microphone")
        self.mic_toggled.emit(self._mic_muted)

    def _on_webcam(self):
        self._webcam_on = self._cam_btn.isChecked()
        if self._webcam_on:
            self._cam_btn.setIcon(self._cam_on_icon)
            self._cam_btn.setToolTip("Disable Webcam")
        else:
            self._cam_btn.setIcon(self._cam_off_icon)
            self._cam_btn.setToolTip("Enable Webcam")
        self.webcam_toggled.emit(self._webcam_on)

    def reset_webcam_button(self):
        """Force the webcam button back to the off state (e.g. permission denied)."""
        self._webcam_on = False
        self._cam_btn.setChecked(False)
        self._cam_btn.setIcon(self._cam_off_icon)
        self._cam_btn.setToolTip("Enable Webcam")

    def _on_draw(self):
        self._draw_active = self._draw_btn.isChecked()
        self.draw_toggled.emit(self._draw_active)

    # --- Dragging ---

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# --- Default annotation color persistence ---
_DEFAULT_COLOR_KEY = "annotation_default_color"


def _load_default_color():
    """Load saved default color from settings, or None."""
    s = QSettings("ScreenCapture", "ScreenCapture")
    hex_val = s.value(_DEFAULT_COLOR_KEY, None)
    if hex_val and isinstance(hex_val, str):
        c = QColor(hex_val)
        if c.isValid():
            return c
    return None


def _save_default_color(color: QColor):
    """Save color as default for next session."""
    s = QSettings("ScreenCapture", "ScreenCapture")
    s.setValue(_DEFAULT_COLOR_KEY, color.name())
    s.sync()


class ColorPickerPopup(QDialog):
    """Color picker popup: native macOS Colors (round wheel, tabs) + hex, copy, default.

    Shown when user clicks the color swatch. Keeps the sidebar slim.
    """

    color_selected = pyqtSignal(object)  # QColor

    def __init__(self, initial_color: QColor, parent=None):
        super().__init__(parent)
        self._current_color = QColor(initial_color)
        self.setWindowTitle("Pick Color")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet("""
            QDialog { background: #1e1e1e; }
        """)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Open native macOS Colors (round wheel, sliders, palettes tabs)
        open_btn = QPushButton("Open color picker…", self)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.2); color: white;
                border: 1px solid #555; border-radius: 4px;
                padding: 6px 12px; font-size: 12px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.3); }
        """)
        open_btn.clicked.connect(self._open_native_picker)
        layout.addWidget(open_btn)

        # Quick-pick palette
        palette_label = QLabel("Quick colors", self)
        palette_label.setStyleSheet("font-size: 10px; color: #aaa;")
        layout.addWidget(palette_label)
        palette_layout = QHBoxLayout()
        palette_layout.setSpacing(2)
        for hex_val in _PALETTE_COLORS:
            btn = QToolButton(self)
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QToolButton {{
                    background: {hex_val};
                    border: 1px solid #555;
                    border-radius: 2px;
                }}
                QToolButton:hover {{ border: 2px solid #fff; }}
            """)
            btn.clicked.connect(lambda checked, h=hex_val: self._pick_palette(h))
            palette_layout.addWidget(btn)
        palette_layout.addStretch()
        layout.addLayout(palette_layout)

        # Current color swatch + hex + copy
        row = QHBoxLayout()
        swatch = QToolButton(self)
        swatch.setFixedSize(28, 28)
        swatch.setEnabled(False)
        self._swatch = swatch
        row.addWidget(swatch)

        self._hex_edit = QLineEdit(self)
        self._hex_edit.setPlaceholderText("#RRGGBB")
        self._hex_edit.setMaxLength(7)
        self._hex_edit.setFixedWidth(90)
        self._hex_edit.setStyleSheet(f"""
            QLineEdit {{
                background: #2a2a2a; color: #eee; border: 1px solid #444;
                border-radius: 4px; padding: 4px 8px;
                font-family: '{SYSTEM_FONT}'; font-size: 12px;
            }}
        """)
        self._hex_edit.setText(self._current_color.name())
        self._hex_edit.editingFinished.connect(self._apply_hex)
        row.addWidget(self._hex_edit)

        copy_btn = QPushButton("Copy", self)
        copy_btn.setFixedWidth(50)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.15); color: white;
                border: none; border-radius: 4px; padding: 4px;
                font-size: 11px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.25); }
        """)
        copy_btn.clicked.connect(self._copy_hex)
        row.addWidget(copy_btn)
        row.addStretch()
        layout.addLayout(row)

        # Default checkbox
        self._default_cb = QCheckBox("Use as default for next session", self)
        self._default_cb.setStyleSheet("color: #ccc; font-size: 11px;")
        self._default_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._default_cb)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK", self)
        ok_btn.setFixedWidth(70)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setFixedWidth(70)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_swatch()

    def _update_swatch(self):
        self._swatch.setStyleSheet(f"""
            QToolButton {{
                background: {self._current_color.name()};
                border: 1px solid #555;
                border-radius: 4px;
            }}
        """)

    def _open_native_picker(self):
        """Open native macOS Colors window (round wheel, tabs)."""
        color = QColorDialog.getColor(self._current_color, self, "Colors")
        if color.isValid():
            self._current_color = color
            self._hex_edit.blockSignals(True)
            self._hex_edit.setText(color.name())
            self._hex_edit.blockSignals(False)
            self._update_swatch()

    def _pick_palette(self, hex_val: str):
        """One-click select from palette."""
        c = QColor(hex_val)
        if c.isValid():
            self._current_color = c
            self._hex_edit.blockSignals(True)
            self._hex_edit.setText(c.name())
            self._hex_edit.blockSignals(False)
            self._update_swatch()

    def _apply_hex(self):
        text = self._hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        c = QColor(text)
        if c.isValid():
            self._current_color = c
            self._update_swatch()

    def _apply_hex(self):
        text = self._hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        c = QColor(text)
        if c.isValid():
            self._current_color = c
            self._color_dialog.setCurrentColor(c)

    def _copy_hex(self):
        text = self._current_color.name()
        cb = QApplication.clipboard()
        if cb:
            cb.setText(text)

    def _on_ok(self):
        if self._default_cb.isChecked():
            _save_default_color(self._current_color)
        self.color_selected.emit(self._current_color)
        self.accept()


class DrawingSubPanel(QWidget):
    """Small popup panel with drawing tool buttons, shown when draw is toggled."""

    tool_selected = pyqtSignal(str)   # tool name
    color_changed = pyqtSignal(object)  # QColor
    clear_clicked = pyqtSignal()

    def __init__(self, parent_toolbar: RecordingToolbar):
        super().__init__()
        self._toolbar = parent_toolbar
        saved = _load_default_color()
        self._current_color = saved if saved else QColor("#e63946")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        tools = [
            ("Pen", "pen"),
            ("Arrow", "arrow"),
            ("Rect", "rectangle"),
            ("Mark", "highlighter"),
        ]

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for label, tool_id in tools:
            btn = QToolButton(self)
            btn.setText(label)
            btn.setFixedSize(40, 28)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("tool_id", tool_id)
            btn.setStyleSheet("""
                QToolButton {
                    color: white; background: transparent; border: none;
                    border-radius: 4px; font-size: 11px;
                    font-family: '%s';
                }
                QToolButton:hover { background: rgba(255,255,255,0.15); }
                QToolButton:checked { background: rgba(255,255,255,0.25); }
            """ % SYSTEM_FONT)
            self._group.addButton(btn)
            layout.addWidget(btn)

        self._group.buttonClicked.connect(
            lambda btn: self.tool_selected.emit(btn.property("tool_id"))
        )

        # Color button
        self._color_btn = QToolButton(self)
        self._color_btn.setFixedSize(22, 22)
        self._color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_color_btn()
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)

        # Clear button
        clear_btn = QToolButton(self)
        clear_btn.setText("X")
        clear_btn.setFixedSize(28, 28)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setToolTip("Clear All")
        clear_btn.setStyleSheet("""
            QToolButton {
                color: #e63946; background: transparent; border: none;
                border-radius: 4px; font-size: 12px; font-weight: bold;
            }
            QToolButton:hover { background: rgba(255,255,255,0.15); }
        """)
        clear_btn.clicked.connect(self.clear_clicked.emit)
        layout.addWidget(clear_btn)

    def _update_color_btn(self):
        self._color_btn.setStyleSheet(f"""
            QToolButton {{
                background: {self._current_color.name()};
                border: 2px solid rgba(255,255,255,0.5);
                border-radius: 4px;
            }}
        """)

    def _pick_color(self):
        popup = ColorPickerPopup(self._current_color, self)
        popup.color_selected.connect(self._apply_color)
        popup.adjustSize()
        # Position near the color button so it feels like a popover
        btn_global = self._color_btn.mapToGlobal(self._color_btn.rect().bottomLeft())
        x = btn_global.x() - popup.width() // 2 + self._color_btn.width() // 2
        y = btn_global.y() + 4
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.x(), min(x, screen.right() - popup.width()))
        y = max(screen.y(), min(y, screen.bottom() - popup.height()))
        popup.move(x, y)
        popup.exec()

    def _apply_color(self, color: QColor):
        self._current_color = color
        self._update_color_btn()
        self.color_changed.emit(color)

    def showEvent(self, event):
        super().showEvent(event)
        from recorder import _configure_nswindow
        _configure_nswindow(self, click_through=False, level=25)

        # Auto-select pen tool
        for btn in self._group.buttons():
            if btn.property("tool_id") == "pen":
                btn.setChecked(True)
                self.tool_selected.emit("pen")
                break

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 30, 30, 220))
        p.drawRoundedRect(self.rect(), 10, 10)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        p.end()

    def position_near_toolbar(self):
        """Position this panel above the toolbar."""
        tb = self._toolbar
        x = tb.x() + (tb.width() - self.sizeHint().width()) // 2
        y = tb.y() - self.sizeHint().height() - 6
        if y < 0:
            y = tb.y() + tb.height() + 6
        self.move(x, y)
