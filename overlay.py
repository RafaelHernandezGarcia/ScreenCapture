"""
Overlay Window - LightShot-style region selection + annotation, drawn over a
frozen screenshot of the screen under the mouse.

Coordinate spaces (this matters on high-DPI Windows and Retina macs):
- The widget works in LOGICAL pixels (Qt geometry of the screen).
- The screenshot pixmap holds PHYSICAL pixels and is tagged with
  setDevicePixelRatio(capture_dpr), so Qt draws it crisp at logical size.
- The result image is rendered at PHYSICAL resolution (see _get_result_image)
  so what you copy/save has every real pixel.
"""
import os
import re
from datetime import datetime
from enum import Enum

from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal, QSize
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QFont, QImage, QIcon,
    QPainterPath, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QWidget, QApplication, QToolButton, QHBoxLayout,
    QVBoxLayout, QFrame, QColorDialog, QFileDialog, QButtonGroup,
    QLabel, QLineEdit, QCheckBox, QDialog, QPushButton, QGraphicsDropShadowEffect
)
from PIL import Image

from platform_utils import (
    IS_MACOS, IS_WINDOWS, SYSTEM_FONT, BRAND_PURPLE, BRAND_PURPLE_SOFT,
    INK_SOFT, CARD_BORDER, screenshots_dir, copy_image_to_clipboard,
)
import app_config
from tools import (
    ArrowTool, RectangleTool, CircleTool, LineTool, BlurTool,
    PenTool, HighlighterTool, TextTool, DrawingAction, draw_action
)

# Qt maps the macOS Command key to Qt.ControlModifier by default (it swaps
# Ctrl/Cmd on macOS, so physical Control arrives as MetaModifier). Accept
# BOTH so Cmd+C / Cmd+S / Cmd+Z work on macOS and Ctrl+... works on Windows -
# regardless of the swap setting.
MODIFIER_KEY = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier

# --- Constants for Hit Testing ---
HANDLE_SIZE = 10
BORDER_WIDTH = 1
MIN_SELECTION = 10


class ResizeMode(Enum):
    NONE = 0
    TOP_LEFT = 1
    TOP = 2
    TOP_RIGHT = 3
    LEFT = 4
    RIGHT = 5
    BOTTOM_LEFT = 6
    BOTTOM = 7
    BOTTOM_RIGHT = 8
    MOVE = 9


class IconFactory:
    """Generates crisp line icons programmatically (no image assets needed)."""

    @staticmethod
    def create_icon(name: str, color: QColor) -> QIcon:
        size = 40
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # Scale factor for 40px icons (drawn on a 32px grid)
        s = 1.25

        if name == "pen":
            painter.drawLine(int(10*s), int(22*s), int(13*s), int(22*s))
            painter.drawLine(int(10*s), int(22*s), int(22*s), int(10*s))
            painter.drawLine(int(13*s), int(22*s), int(25*s), int(13*s))
            painter.drawLine(int(22*s), int(10*s), int(25*s), int(13*s))
            painter.drawLine(int(10*s), int(22*s), int(8*s), int(24*s))

        elif name == "line":
            painter.drawLine(int(8*s), int(24*s), int(24*s), int(8*s))

        elif name == "arrow":
            painter.drawLine(int(8*s), int(24*s), int(24*s), int(8*s))
            painter.drawLine(int(24*s), int(8*s), int(16*s), int(8*s))
            painter.drawLine(int(24*s), int(8*s), int(24*s), int(16*s))

        elif name == "rectangle":
            painter.drawRect(int(8*s), int(10*s), int(16*s), int(12*s))

        elif name == "circle":
            painter.drawEllipse(int(8*s), int(9*s), int(16*s), int(14*s))

        elif name == "highlighter":
            pen.setWidth(8)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            if color.name() != "#000000":
                c = QColor(color)
                c.setAlpha(150)
                pen.setColor(c)
            painter.setPen(pen)
            painter.drawLine(int(8*s), int(20*s), int(24*s), int(12*s))

        elif name == "text":
            font = QFont("Georgia", 16, QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "T")

        elif name == "blur":
            # Mosaic: a 3x3 checker of filled squares
            painter.setPen(Qt.PenStyle.NoPen)
            cell = int(5 * s)
            x0, y0 = int(8.5 * s), int(8.5 * s)
            for r in range(3):
                for c in range(3):
                    if (r + c) % 2 == 0:
                        painter.setBrush(color)
                    else:
                        cc = QColor(color)
                        cc.setAlpha(90)
                        painter.setBrush(cc)
                    painter.drawRect(x0 + c * cell, y0 + r * cell, cell - 1, cell - 1)

        elif name == "undo":
            path = QPainterPath()
            path.moveTo(22*s, 12*s)
            path.quadTo(16*s, 12*s, 12*s, 16*s)
            path.quadTo(12*s, 22*s, 18*s, 24*s)
            painter.drawPath(path)
            painter.drawLine(int(22*s), int(12*s), int(18*s), int(8*s))
            painter.drawLine(int(22*s), int(12*s), int(18*s), int(16*s))

        elif name == "redo":
            path = QPainterPath()
            path.moveTo(10*s, 12*s)
            path.quadTo(16*s, 12*s, 20*s, 16*s)
            path.quadTo(20*s, 22*s, 14*s, 24*s)
            painter.drawPath(path)
            painter.drawLine(int(10*s), int(12*s), int(14*s), int(8*s))
            painter.drawLine(int(10*s), int(12*s), int(14*s), int(16*s))

        elif name == "copy":
            painter.drawRect(int(14*s), int(8*s), int(12*s), int(14*s))
            painter.drawLine(int(10*s), int(12*s), int(10*s), int(28*s))
            painter.drawLine(int(10*s), int(28*s), int(22*s), int(28*s))

        elif name == "save":
            painter.drawRect(int(8*s), int(6*s), int(18*s), int(22*s))
            painter.drawLine(int(12*s), int(6*s), int(12*s), int(14*s))
            painter.drawLine(int(24*s), int(6*s), int(24*s), int(14*s))
            painter.drawLine(int(12*s), int(14*s), int(24*s), int(14*s))

        elif name == "close":
            painter.drawLine(int(12*s), int(12*s), int(26*s), int(26*s))
            painter.drawLine(int(26*s), int(12*s), int(12*s), int(26*s))

        elif name == "record":
            # Viewfinder-style record icon with red dot
            painter.setPen(QPen(color, 2 * s))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(int(4*s), int(4*s), int(11*s), int(4*s))
            painter.drawLine(int(4*s), int(4*s), int(4*s), int(11*s))
            painter.drawLine(int(21*s), int(4*s), int(28*s), int(4*s))
            painter.drawLine(int(28*s), int(4*s), int(28*s), int(11*s))
            painter.drawLine(int(4*s), int(21*s), int(4*s), int(28*s))
            painter.drawLine(int(4*s), int(28*s), int(11*s), int(28*s))
            painter.drawLine(int(28*s), int(21*s), int(28*s), int(28*s))
            painter.drawLine(int(21*s), int(28*s), int(28*s), int(28*s))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e63946"))
            painter.drawEllipse(int(10*s), int(10*s), int(12*s), int(12*s))

        painter.end()
        return QIcon(pixmap)


class HoverButton(QToolButton):
    """Toolbar button: clean ink at rest, brand purple on hover / when checked."""

    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setFixedSize(40, 40)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self.icon_normal = IconFactory.create_icon(icon_name, QColor(INK_SOFT))
        self.icon_active = IconFactory.create_icon(icon_name, QColor(BRAND_PURPLE))

        self.setIcon(self.icon_normal)
        self.setIconSize(QSize(40, 40))

        self.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 9px;
            }}
            QToolButton:hover {{
                background: #F2F2F7;
            }}
            QToolButton:checked {{
                background: {BRAND_PURPLE_SOFT};
            }}
        """)

    def enterEvent(self, event):
        self.setIcon(self.icon_active)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.isChecked():
            self.setIcon(self.icon_normal)
        super().leaveEvent(event)

    def checkStateSet(self):
        if self.isChecked():
            self.setIcon(self.icon_active)
        else:
            self.setIcon(self.icon_normal)
        super().checkStateSet()


# Quick-pick palette colors (common annotation colors)
_PALETTE_COLORS = [
    "#000000", "#ffffff", "#e63946", "#2a9d8f", "#e9c46a", "#264653",
    "#f4a261", "#2ec4b6", "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4",
    "#ffeaa7", "#dfe6e9", "#a29bfe", "#fd79a8", "#636e72", "#b2bec3",
]


class _ColorPickerPopup(QDialog):
    """Color picker popup: quick palette + hex + native dialog + "use as default".
    Keeps the sidebar slim - only the swatch is visible until clicked.
    """
    def __init__(self, initial_color: QColor, parent=None):
        super().__init__(parent)
        self.selected_color = QColor(initial_color)
        self._current = QColor(initial_color)
        self.setWindowTitle("Pick Color")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        open_btn = QPushButton("Open color picker...", self)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet("""
            QPushButton {
                background: #e0e0e0; color: #333; border: 1px solid #999;
                border-radius: 4px; padding: 6px 12px; font-size: 12px;
            }
            QPushButton:hover { background: #d0d0d0; }
        """)
        open_btn.clicked.connect(self._open_native_picker)
        layout.addWidget(open_btn)

        palette_label = QLabel("Quick colors", self)
        palette_label.setStyleSheet("font-size: 10px; color: #555;")
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
                    border: 1px solid #999;
                    border-radius: 2px;
                }}
                QToolButton:hover {{ border: 2px solid #333; }}
            """)
            btn.clicked.connect(lambda checked, h=hex_val: self._pick_palette(h))
            palette_layout.addWidget(btn)
        palette_layout.addStretch()
        layout.addLayout(palette_layout)

        row = QHBoxLayout()
        swatch = QToolButton(self)
        swatch.setFixedSize(28, 28)
        swatch.setEnabled(False)
        self._swatch = swatch
        row.addWidget(swatch)

        self._hex_edit = QLineEdit(self._current.name().upper(), self)
        self._hex_edit.setPlaceholderText("#RRGGBB")
        self._hex_edit.setMaxLength(7)
        self._hex_edit.setFixedWidth(90)
        self._hex_edit.returnPressed.connect(self._apply_hex)
        self._hex_edit.editingFinished.connect(self._apply_hex)
        row.addWidget(self._hex_edit)

        copy_btn = QPushButton("Copy", self)
        copy_btn.setFixedWidth(50)
        copy_btn.clicked.connect(self._copy_hex)
        row.addWidget(copy_btn)
        row.addStretch()
        layout.addLayout(row)

        is_default = str(app_config.get("default_color", "")).upper() == self._current.name().upper()
        self._default_cb = QCheckBox("Use as default for next session", self)
        self._default_cb.setChecked(is_default)
        layout.addWidget(self._default_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK", self)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_swatch()

    def _update_swatch(self):
        self._swatch.setStyleSheet(f"""
            QToolButton {{
                background: {self._current.name()};
                border: 1px solid #999;
                border-radius: 4px;
            }}
        """)

    def _set_current(self, c: QColor):
        self._current = c
        self._hex_edit.blockSignals(True)
        self._hex_edit.setText(c.name().upper())
        self._hex_edit.blockSignals(False)
        self._update_swatch()

    def _open_native_picker(self):
        color = QColorDialog.getColor(self._current, self, "Colors")
        if color.isValid():
            self._set_current(color)

    def _pick_palette(self, hex_val: str):
        c = QColor(hex_val)
        if c.isValid():
            self._set_current(c)

    def _apply_hex(self):
        text = self._hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        if re.match(r'^#[0-9A-Fa-f]{6}$', text):
            c = QColor(text)
            if c.isValid():
                self._current = c
                self._update_swatch()

    def _copy_hex(self):
        QApplication.clipboard().setText(self._current.name().upper())

    def _on_ok(self):
        self._apply_hex()
        if self._default_cb.isChecked():
            app_config.set_value("default_color", self._current.name().upper())
        self.selected_color = self._current
        self.accept()


class OverlayWindow(QWidget):
    region_selected = pyqtSignal(QRect)
    selection_cancelled = pyqtSignal()
    image_copied = pyqtSignal()
    image_saved = pyqtSignal(str)
    recording_requested = pyqtSignal(QRect)

    # Tools shown in the vertical toolbar, top to bottom.
    TOOLS = [
        ("pen", "Pen", "pen"),
        ("line", "Line", "line"),
        ("arrow", "Arrow", "arrow"),
        ("rectangle", "Rectangle", "rectangle"),
        ("circle", "Ellipse", "circle"),
        ("highlighter", "Highlighter", "highlighter"),
        ("blur", "Blur (hide sensitive info)", "blur"),
        ("text", "Text (click, type, Enter)", "text"),
    ]

    def __init__(self, screenshot: Image.Image, offset_x: int = 0, offset_y: int = 0,
                 capture_dpr: float = 1.0):
        super().__init__()
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.capture_dpr = float(capture_dpr) if capture_dpr else 1.0

        self.original_image = screenshot
        self.screenshot = self._pil_to_pixmap(screenshot)
        # Physical pixels tagged with the capture ratio: Qt draws the pixmap
        # at LOGICAL size (crisp on 150% Windows / Retina) without any manual
        # scaling in paintEvent.
        self.screenshot.setDevicePixelRatio(self.capture_dpr)
        self._pixelated = None  # built lazily by the blur tool

        self.start_point = None
        self.current_point = None
        self.selection_rect: QRect | None = None
        self.selection_complete = False
        self.resize_mode = ResizeMode.NONE
        self.origin_rect = None

        self.current_tool = None
        default_hex = app_config.get("default_color", "#000000") or "#000000"
        self.current_color = QColor(default_hex)
        if not self.current_color.isValid():
            self.current_color = QColor("#000000")
        self.actions = []
        self._redo_stack = []

        self.tool_toolbar = None
        self.action_toolbar = None
        self.tool_group = None
        self.tool_buttons = {}

        # Inline text editing state
        self.text_editing = False
        self.text_position = None
        self.text_content = ""
        self.editing_action_index = None

        self._setup_window()

    # ------------------------------------------------------------------ setup

    def _pil_to_pixmap(self, pil_image: Image.Image) -> QPixmap:
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(data, pil_image.width, pil_image.height,
                        QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(qimage)

    def _logical_size(self):
        """Screen size in logical pixels (what the widget covers)."""
        return (int(round(self.screenshot.width() / self.capture_dpr)),
                int(round(self.screenshot.height() / self.capture_dpr)))

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        w, h = self._logical_size()
        self.setGeometry(self.offset_x, self.offset_y, w, h)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        # Must be focusable or keyPressEvent (Esc / Ctrl+C / Ctrl+S / Ctrl+Z) never fires.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _add_card_shadow(self, widget):
        """Soft drop shadow for a floating toolbar card (depth, Loom-like)."""
        eff = QGraphicsDropShadowEffect(widget)
        eff.setBlurRadius(36)
        eff.setColor(QColor(0, 0, 0, 80))
        eff.setOffset(0, 8)
        widget.setGraphicsEffect(eff)

    def _card_style(self):
        return f"""
            QFrame {{
                background: #FFFFFF;
                border: 1px solid {CARD_BORDER};
                border-radius: 18px;
            }}
        """

    def _create_toolbars(self):
        # Vertical Toolbar (Tools)
        self.tool_toolbar = QFrame(self)
        self.tool_toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        self.tool_toolbar.setStyleSheet(self._card_style())
        self._add_card_shadow(self.tool_toolbar)

        tool_layout = QVBoxLayout(self.tool_toolbar)
        tool_layout.setContentsMargins(6, 6, 6, 6)
        tool_layout.setSpacing(3)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        self.tool_buttons = {}
        for icon_key, tooltip, tool_id in self.TOOLS:
            btn = HoverButton(icon_key, tooltip, self.tool_toolbar)
            btn.setProperty("tool_id", tool_id)
            self.tool_group.addButton(btn)
            self.tool_buttons[tool_id] = btn
            tool_layout.addWidget(btn)

        # Color swatch only - palette, hex and "default" live in the popup
        self.color_btn = QToolButton(self.tool_toolbar)
        self.color_btn.setFixedSize(24, 24)
        self.color_btn.setToolTip("Color")
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_color_button()
        self.color_btn.clicked.connect(self._pick_color)

        color_container = QWidget()
        color_layout = QVBoxLayout(color_container)
        color_layout.setContentsMargins(3, 5, 3, 2)
        color_layout.setSpacing(2)
        color_layout.addWidget(self.color_btn, 0, Qt.AlignmentFlag.AlignCenter)
        tool_layout.addWidget(color_container)

        undo_btn = HoverButton("undo", "Undo (Ctrl+Z)", self.tool_toolbar)
        undo_btn.setCheckable(False)
        undo_btn.clicked.connect(self._undo)
        tool_layout.addWidget(undo_btn)

        redo_btn = HoverButton("redo", "Redo (Ctrl+Y)", self.tool_toolbar)
        redo_btn.setCheckable(False)
        redo_btn.clicked.connect(self._redo)
        tool_layout.addWidget(redo_btn)

        self.tool_toolbar.adjustSize()
        self.tool_group.buttonClicked.connect(self._on_tool_selected)

        # Horizontal Toolbar (Actions)
        self.action_toolbar = QFrame(self)
        self.action_toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        self.action_toolbar.setStyleSheet(self._card_style())
        self._add_card_shadow(self.action_toolbar)

        action_layout = QHBoxLayout(self.action_toolbar)
        action_layout.setContentsMargins(6, 6, 6, 6)
        action_layout.setSpacing(3)

        actions = [
            ("record", "Record this area (video)", self._record),
            ("copy", "Copy to clipboard (Enter)", self._copy),
            ("save", "Save as file (Ctrl+S)", self._save),
            ("close", "Close (Esc)", self._cancel),
        ]
        for icon_key, tooltip, callback in actions:
            btn = HoverButton(icon_key, tooltip, self.action_toolbar)
            btn.setCheckable(False)
            btn.clicked.connect(callback)
            action_layout.addWidget(btn)

        self.action_toolbar.adjustSize()
        self._position_toolbars()

    def _position_toolbars(self):
        if not self.selection_rect or not self.tool_toolbar:
            return

        rect = self.selection_rect
        margin = 5

        tx = rect.right() + margin
        ty = rect.top()
        if tx + self.tool_toolbar.width() > self.width():
            tx = rect.left() - self.tool_toolbar.width() - margin
        if tx < 0:
            tx = max(margin, rect.right() - self.tool_toolbar.width() - margin)
        if ty + self.tool_toolbar.height() > self.height():
            ty = self.height() - self.tool_toolbar.height() - margin
        if ty < 0:
            ty = margin
        self.tool_toolbar.move(tx, ty)
        self.tool_toolbar.show()

        ax = rect.right() - self.action_toolbar.width()
        ay = rect.bottom() + margin
        if ay + self.action_toolbar.height() > self.height():
            ay = rect.top() - self.action_toolbar.height() - margin
        if ay < 0:
            ay = max(margin, rect.bottom() - self.action_toolbar.height() - margin)
        if ax < 0:
            ax = margin
        self.action_toolbar.move(ax, ay)
        self.action_toolbar.show()

    def _update_color_button(self):
        self.color_btn.setStyleSheet(f"""
            QToolButton {{
                background-color: {self.current_color.name()};
                border: 1px solid #999;
                border-radius: 12px;
            }}
        """)

    def _set_overlay_level(self, level: int):
        """Set this overlay's underlying NSWindow stacking level (macOS).

        The overlay normally sits at level 25 (above normal windows) so it
        covers everything during capture. But that also hides any child
        dialog - like the color picker - behind it. We drop the level while
        a dialog is open, then restore it. No-op on Windows (child dialogs
        stack above their parent there).
        """
        if not IS_MACOS:
            return
        try:
            import ctypes as _ct
            import objc
            ptr = int(self.winId())
            if ptr == 0:
                return
            nswindow = objc.objc_object(c_void_p=_ct.c_void_p(ptr)).window()
            if nswindow is not None:
                nswindow.setLevel_(level)
        except Exception as e:
            print(f"_set_overlay_level: {e}")

    def _pick_color(self):
        self._set_overlay_level(0)
        try:
            popup = _ColorPickerPopup(self.current_color, self)
            popup.setWindowModality(Qt.WindowModality.ApplicationModal)
            if popup.exec() == QDialog.DialogCode.Accepted and popup.selected_color.isValid():
                self.current_color = popup.selected_color
                self._update_color_button()
                if self.current_tool and not isinstance(self.current_tool, HighlighterTool):
                    self.current_tool.color = self.current_color
        finally:
            self._set_overlay_level(25)
            self.activateWindow()
            self.setFocus()
            self.raise_()
            self.update()

    def _on_tool_selected(self, button):
        tool_id = button.property("tool_id")
        tool_map = {
            "pen": PenTool, "line": LineTool, "arrow": ArrowTool,
            "rectangle": RectangleTool, "circle": CircleTool,
            "highlighter": HighlighterTool, "blur": BlurTool, "text": TextTool,
        }
        tool_class = tool_map.get(tool_id)
        if tool_class:
            if tool_id == "highlighter":
                self.current_tool = tool_class(QColor(255, 255, 0), 20)
            else:
                self.current_tool = tool_class(self.current_color)
            if tool_id == "text":
                self.setCursor(Qt.CursorShape.IBeamCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

    def _undo(self):
        if self.text_editing:
            self._finish_text_editing()
        if self.actions:
            self._redo_stack.append(self.actions.pop())
            self.update()

    def _redo(self):
        if self._redo_stack:
            self.actions.append(self._redo_stack.pop())
            self.update()

    def _push_action(self, action):
        self.actions.append(action)
        self._redo_stack.clear()

    # ------------------------------------------------- hit testing / mouse

    def _over_toolbar(self, pos: QPoint) -> bool:
        for tb in (self.tool_toolbar, self.action_toolbar):
            if tb and tb.isVisible() and tb.geometry().contains(pos):
                return True
        return False

    def _get_hit_test(self, pos: QPoint):
        if self._over_toolbar(pos):
            return ResizeMode.NONE
        if not self.selection_rect:
            return ResizeMode.NONE

        r = self.selection_rect
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        hs = HANDLE_SIZE
        hw = hs // 2

        tl = QRect(x - hw, y - hw, hs, hs)
        tr = QRect(x + w - hw, y - hw, hs, hs)
        bl = QRect(x - hw, y + h - hw, hs, hs)
        br = QRect(x + w - hw, y + h - hw, hs, hs)

        t = QRect(x + hw, y - hw, w - hs, hs)
        b = QRect(x + hw, y + h - hw, w - hs, hs)
        l = QRect(x - hw, y + hw, hs, h - hs)
        ri = QRect(x + w - hw, y + hw, hs, h - hs)

        if tl.contains(pos): return ResizeMode.TOP_LEFT
        if tr.contains(pos): return ResizeMode.TOP_RIGHT
        if bl.contains(pos): return ResizeMode.BOTTOM_LEFT
        if br.contains(pos): return ResizeMode.BOTTOM_RIGHT
        if t.contains(pos): return ResizeMode.TOP
        if b.contains(pos): return ResizeMode.BOTTOM
        if l.contains(pos): return ResizeMode.LEFT
        if ri.contains(pos): return ResizeMode.RIGHT
        if r.contains(pos): return ResizeMode.MOVE
        return ResizeMode.NONE

    def _update_cursor(self, pos: QPoint):
        if self.resize_mode != ResizeMode.NONE and self.start_point:
            return
        if self._over_toolbar(pos):
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        mode = self._get_hit_test(pos)
        if self.current_tool and mode == ResizeMode.MOVE:
            if isinstance(self.current_tool, TextTool):
                self.setCursor(Qt.CursorShape.IBeamCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return

        cursor_map = {
            ResizeMode.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
            ResizeMode.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
            ResizeMode.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
            ResizeMode.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
            ResizeMode.TOP: Qt.CursorShape.SizeVerCursor,
            ResizeMode.BOTTOM: Qt.CursorShape.SizeVerCursor,
            ResizeMode.LEFT: Qt.CursorShape.SizeHorCursor,
            ResizeMode.RIGHT: Qt.CursorShape.SizeHorCursor,
            ResizeMode.MOVE: Qt.CursorShape.SizeAllCursor,
            ResizeMode.NONE: Qt.CursorShape.CrossCursor
        }
        self.setCursor(cursor_map.get(mode, Qt.CursorShape.ArrowCursor))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.text_editing:
            self._finish_text_editing()

        self.start_point = event.pos()
        self.current_point = event.pos()
        hit = self._get_hit_test(event.pos())

        if self.selection_complete:
            if hit != ResizeMode.NONE and hit != ResizeMode.MOVE:
                self.resize_mode = hit
                self.origin_rect = QRect(self.selection_rect)
            elif hit == ResizeMode.MOVE:
                if self.current_tool:
                    self.resize_mode = ResizeMode.NONE
                    if isinstance(self.current_tool, TextTool):
                        self._add_text(event.pos())
                    else:
                        self.current_tool.on_mouse_press(event.pos())
                else:
                    self.resize_mode = ResizeMode.MOVE
                    self.origin_rect = QRect(self.selection_rect)
            else:
                # Click outside the selection: start a fresh one
                self.selection_complete = False
                self.selection_rect = None
                self.tool_toolbar.hide()
                self.action_toolbar.hide()
                self.resize_mode = ResizeMode.NONE
                self.tool_group.setExclusive(False)
                for btn in self.tool_buttons.values():
                    btn.setChecked(False)
                self.tool_group.setExclusive(True)
                self.current_tool = None
                self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.resize_mode = ResizeMode.NONE
        self.update()

    def mouseMoveEvent(self, event):
        self.current_point = event.pos()
        self._update_cursor(event.pos())

        if not self.start_point:
            return
        if not self.selection_complete:
            self.selection_rect = QRect(self.start_point, self.current_point).normalized()
            self.update()
            return

        if self.resize_mode == ResizeMode.NONE and self.current_tool:
            self.current_tool.on_mouse_move(event.pos())
            self.update()
            return

        if self.resize_mode == ResizeMode.MOVE:
            dx = self.current_point.x() - self.start_point.x()
            dy = self.current_point.y() - self.start_point.y()
            self.selection_rect = self.origin_rect.translated(dx, dy)
            self._position_toolbars()
            self.update()

        elif self.resize_mode != ResizeMode.NONE:
            r = QRect(self.origin_rect)
            dx = self.current_point.x() - self.start_point.x()
            dy = self.current_point.y() - self.start_point.y()
            m = self.resize_mode
            if m in (ResizeMode.RIGHT, ResizeMode.BOTTOM_RIGHT, ResizeMode.TOP_RIGHT):
                r.setRight(r.right() + dx)
            if m in (ResizeMode.LEFT, ResizeMode.TOP_LEFT, ResizeMode.BOTTOM_LEFT):
                r.setLeft(r.left() + dx)
            if m in (ResizeMode.BOTTOM, ResizeMode.BOTTOM_RIGHT, ResizeMode.BOTTOM_LEFT):
                r.setBottom(r.bottom() + dy)
            if m in (ResizeMode.TOP, ResizeMode.TOP_LEFT, ResizeMode.TOP_RIGHT):
                r.setTop(r.top() + dy)
            self.selection_rect = r.normalized()
            self._position_toolbars()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.selection_complete:
            if (self.selection_rect and self.selection_rect.width() > MIN_SELECTION
                    and self.selection_rect.height() > MIN_SELECTION):
                self.selection_complete = True
                if self.tool_toolbar is None:
                    self._create_toolbars()
                else:
                    self._position_toolbars()
            else:
                self.selection_rect = None
            self.start_point = None
            self.update()
            return

        if self.resize_mode == ResizeMode.NONE and self.current_tool:
            action = self.current_tool.on_mouse_release(event.pos())
            if action:
                self._push_action(action)

        self.start_point = None
        self.resize_mode = ResizeMode.NONE
        self.update()

    # -------------------------------------------------------------- text

    def _text_font(self, size=18):
        return QFont(SYSTEM_FONT, size, QFont.Weight.Bold)

    def _add_text(self, point):
        """Start inline text editing at the clicked point, or edit existing text if clicked on it."""
        for i in range(len(self.actions) - 1, -1, -1):
            action = self.actions[i]
            if action.tool_type == "text" and action.points and action.text:
                text_pos = action.points[0]
                metrics = QFontMetrics(self._text_font(action.font_size or 18))
                text_width = metrics.horizontalAdvance(action.text)
                text_height = metrics.height()
                padding = 8
                hit_rect = QRect(
                    text_pos.x() - padding,
                    text_pos.y() - text_height - padding,
                    text_width + padding * 2,
                    text_height + padding * 2
                )
                if hit_rect.contains(point):
                    self.text_editing = True
                    self.text_position = text_pos
                    self.text_content = action.text
                    self.editing_action_index = i
                    self.update()
                    return

        self.text_editing = True
        self.text_position = point
        self.text_content = ""
        self.editing_action_index = None
        self.update()

    def _finish_text_editing(self):
        """Finish text editing and save the text as an action."""
        if not self.text_editing:
            return
        if self.text_content.strip():
            if self.editing_action_index is not None:
                self.actions[self.editing_action_index].text = self.text_content
            else:
                self._push_action(DrawingAction(
                    tool_type="text",
                    color=QColor(self.current_color),
                    points=[QPoint(self.text_position)],
                    text=self.text_content,
                    font_size=18
                ))
        elif self.editing_action_index is not None:
            del self.actions[self.editing_action_index]

        self.text_editing = False
        self.text_position = None
        self.text_content = ""
        self.editing_action_index = None
        self.update()

    # ----------------------------------------------------------- painting

    def _blur_source(self) -> QPixmap:
        """Pixelated copy of the screenshot (built once, same DPR tag)."""
        if self._pixelated is None:
            img = self.screenshot.toImage()
            block = max(4, int(round(12 * self.capture_dpr)))  # mosaic cell in physical px
            small = img.scaled(max(1, img.width() // block), max(1, img.height() // block),
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.FastTransformation)
            big = small.scaled(img.width(), img.height(),
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.FastTransformation)
            pm = QPixmap.fromImage(big)
            pm.setDevicePixelRatio(self.capture_dpr)
            self._pixelated = pm
        return self._pixelated

    def _needs_blur_source(self):
        if any(a.tool_type == "blur" for a in self.actions):
            return True
        return isinstance(self.current_tool, BlurTool)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Frozen screen, dimmed
        painter.drawPixmap(0, 0, self.screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self.selection_rect:
            rect = self.selection_rect
            blur_src = self._blur_source() if self._needs_blur_source() else None

            # Undimmed selection: redraw the screenshot clipped to the rect
            painter.save()
            painter.setClipRect(rect)
            painter.drawPixmap(0, 0, self.screenshot)

            for i, action in enumerate(self.actions):
                if self.text_editing and self.editing_action_index == i:
                    continue
                draw_action(painter, action, blur_source=blur_src)

            if self.text_editing and self.text_position:
                painter.setFont(self._text_font(18))
                painter.setPen(self.current_color)
                painter.drawText(self.text_position, self.text_content)
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(self.text_content)
                text_height = metrics.height()
                cursor_x = self.text_position.x() + text_width + 1
                cursor_y = self.text_position.y()
                painter.setPen(QPen(self.current_color, 2))
                painter.drawLine(cursor_x, cursor_y - text_height + 4, cursor_x, cursor_y + 3)

            if (self.current_tool and self.start_point and self.resize_mode == ResizeMode.NONE
                    and self.selection_complete):
                self.current_tool.draw_preview(painter)
            painter.restore()

            # Solid brand-purple selection border
            painter.setPen(QPen(QColor(BRAND_PURPLE), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            if self.selection_complete:
                self._draw_handles(painter, rect)
            self._draw_dimensions(painter, rect)

        elif not self.start_point:
            self._draw_instructions(painter)

    def _draw_handles(self, painter, rect):
        hs = HANDLE_SIZE
        hw = hs / 2
        points = [
            rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight(),
            QPoint(rect.center().x(), rect.top()),
            QPoint(rect.center().x(), rect.bottom()),
            QPoint(rect.left(), rect.center().y()),
            QPoint(rect.right(), rect.center().y())
        ]
        painter.setPen(QPen(QColor(BRAND_PURPLE), 1))
        painter.setBrush(Qt.GlobalColor.white)
        for p in points:
            painter.drawRect(int(p.x() - hw), int(p.y() - hw), hs, hs)

    def _draw_dimensions(self, painter, rect):
        # Show the REAL pixel size of the image you will get.
        pw = int(round(rect.width() * self.capture_dpr))
        ph = int(round(rect.height() * self.capture_dpr))
        text = f"{pw} x {ph}"
        painter.setFont(QFont(SYSTEM_FONT, 9))
        metrics = painter.fontMetrics()
        t_rect = metrics.boundingRect(text)
        pad_x, pad_y = 7, 3
        bw, bh = t_rect.width() + pad_x * 2, t_rect.height() + pad_y * 2

        x = rect.left()
        y = rect.top() - bh - 6
        if y < 0:
            y = rect.top() + 6
        if x + bw > self.width():
            x = self.width() - bw - 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(x, y, bw, bh, 5, 5)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QRect(x, y, bw, bh), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_instructions(self, painter):
        lines = [
            ("Drag to select an area", 15, QFont.Weight.DemiBold),
            ("Enter  copy      Ctrl+S  save      Esc  cancel", 10, QFont.Weight.Normal),
        ]
        widths, heights = [], []
        for text, size, weight in lines:
            painter.setFont(QFont(SYSTEM_FONT, size, weight))
            m = painter.fontMetrics()
            widths.append(m.horizontalAdvance(text))
            heights.append(m.height())
        bw = max(widths) + 44
        bh = sum(heights) + 26
        x = (self.width() - bw) // 2
        y = 60

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 22, 200))
        painter.drawRoundedRect(x, y, bw, bh, 14, 14)

        cy = y + 13
        for (text, size, weight), h in zip(lines, heights):
            painter.setFont(QFont(SYSTEM_FONT, size, weight))
            painter.setPen(QColor(255, 255, 255, 235 if size > 12 else 170))
            painter.drawText(QRect(x, cy, bw, h), Qt.AlignmentFlag.AlignCenter, text)
            cy += h

    # ------------------------------------------------------------- output

    def _get_result_image(self) -> Image.Image:
        # Commit any text still being edited so it lands in the output.
        if self.text_editing:
            self._finish_text_editing()
        if not self.selection_rect:
            return self.original_image
        rect = self.selection_rect
        dpr = self.capture_dpr

        # Integer physical crop origin (avoids half-pixel resampling)
        phys_x = int(round(rect.x() * dpr))
        phys_y = int(round(rect.y() * dpr))
        phys_w = max(1, int(round(rect.width() * dpr)))
        phys_h = max(1, int(round(rect.height() * dpr)))

        result = QPixmap(phys_w, phys_h)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Device pixels: shift so the crop lands at (0, 0), then draw in
        # logical units. The DPR-tagged pixmap maps 1:1 onto device pixels.
        painter.translate(-phys_x, -phys_y)
        painter.scale(dpr, dpr)
        painter.drawPixmap(0, 0, self.screenshot)

        blur_src = self._blur_source() if self._needs_blur_source() else None
        for action in self.actions:
            draw_action(painter, action, blur_source=blur_src)
        painter.end()

        qimage = result.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = qimage.bits()
        ptr.setsize(qimage.sizeInBytes())
        raw = bytes(ptr)
        # Drop any per-row padding Qt may add (bytesPerLine > width * 4)
        stride = qimage.bytesPerLine()
        if stride != qimage.width() * 4:
            rows = [raw[i * stride:i * stride + qimage.width() * 4] for i in range(qimage.height())]
            raw = b"".join(rows)
        return Image.frombytes("RGBA", (qimage.width(), qimage.height()), raw, "raw", "RGBA")

    def _copy(self):
        try:
            result = self._get_result_image()
            if result.mode != "RGBA":
                result = result.convert("RGBA")
            data = result.tobytes("raw", "RGBA")
            qimage = QImage(data, result.width, result.height,
                            QImage.Format.Format_RGBA8888).copy()
            copy_image_to_clipboard(qimage)
            QApplication.processEvents()
            self.image_copied.emit()
            self.close()
        except Exception as e:
            print(f"Copy error: {e}")
            self.close()

    def _save(self):
        last_dir = app_config.get("last_save_dir", "") or ""
        if not last_dir or not os.path.isdir(last_dir):
            last_dir = screenshots_dir()
        default_name = datetime.now().strftime("Screenshot_%Y-%m-%d_%H%M%S.png")
        self._set_overlay_level(0)
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save Screenshot", os.path.join(last_dir, default_name),
                "PNG image (*.png);;JPEG image (*.jpg)")
        finally:
            self._set_overlay_level(25)
        if not file_path:
            self.activateWindow()
            self.setFocus()
            return
        result = self._get_result_image()
        if not file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
            file_path += '.png'
        if file_path.lower().endswith(('.jpg', '.jpeg')):
            result = result.convert('RGB')
        result.save(file_path)
        app_config.set_value("last_save_dir", os.path.dirname(file_path))
        self.image_saved.emit(file_path)
        self.close()

    def _record(self):
        if self.selection_rect:
            self.recording_requested.emit(QRect(self.selection_rect))
            self.close()

    def _cancel(self):
        self.selection_cancelled.emit()
        self.close()

    # ----------------------------------------------------------- keyboard

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if self.text_editing:
            if key == Qt.Key.Key_Escape:
                self.text_editing = False
                self.text_position = None
                self.text_content = ""
                self.editing_action_index = None
                self.update()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_text_editing()
            elif key == Qt.Key.Key_Backspace:
                if self.text_content:
                    self.text_content = self.text_content[:-1]
                    self.update()
            elif key == Qt.Key.Key_Delete:
                self.text_content = ""
                self.update()
            elif mods & MODIFIER_KEY and key == Qt.Key.Key_V:
                paste_text = QApplication.clipboard().text()
                if paste_text:
                    self.text_content += paste_text.replace("\n", " ")
                    self.update()
            elif mods & MODIFIER_KEY and key == Qt.Key.Key_C:
                self._finish_text_editing()
                self._copy()
            elif mods & MODIFIER_KEY and key == Qt.Key.Key_S:
                self._finish_text_editing()
                self._save()
            else:
                text = event.text()
                if text and (text.isprintable() or text == ' '):
                    self.text_content += text
                    self.update()
            return

        if key == Qt.Key.Key_Escape:
            self._cancel()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.selection_rect:
                self._copy()
        elif mods & MODIFIER_KEY:
            if key == Qt.Key.Key_C:
                if self.selection_rect:
                    self._copy()
            elif key == Qt.Key.Key_S:
                if self.selection_rect:
                    self._save()
            elif key == Qt.Key.Key_Z and (mods & Qt.KeyboardModifier.ShiftModifier):
                self._redo()
            elif key == Qt.Key.Key_Z:
                self._undo()
            elif key == Qt.Key.Key_Y:
                self._redo()
            elif key == Qt.Key.Key_A and self.selection_complete is False:
                # Select the whole screen
                self.selection_rect = QRect(0, 0, self.width(), self.height())
                self.selection_complete = True
                if self.tool_toolbar is None:
                    self._create_toolbars()
                else:
                    self._position_toolbars()
                self.update()
        elif key == Qt.Key.Key_Delete and self.selection_complete:
            self._undo()


def show_overlay(screenshot: Image.Image, offset_x: int = 0, offset_y: int = 0,
                 capture_dpr: float = 1.0) -> OverlayWindow:
    overlay = OverlayWindow(screenshot, offset_x, offset_y, capture_dpr)
    overlay.show()
    return overlay
