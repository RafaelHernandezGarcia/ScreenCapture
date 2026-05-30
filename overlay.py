"""
Overlay Window - LightShot clone with full resizing, moving, and exact styling
"""
import sys
import os
import json
import re
from enum import Enum

IS_MACOS = sys.platform == "darwin"
SYSTEM_FONT = ".AppleSystemUIFont" if IS_MACOS else "Segoe UI"

from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QFont, QImage, QIcon,
    QPainterPath, QBrush, QAction, QCursor, QRegularExpressionValidator
)
from PyQt6.QtWidgets import (
    QWidget, QApplication, QToolButton, QHBoxLayout,
    QVBoxLayout, QFrame, QColorDialog, QFileDialog, QInputDialog, QButtonGroup,
    QLabel, QLineEdit, QCheckBox, QDialog, QPushButton, QGraphicsDropShadowEffect
)
from PIL import Image

# Shared config path (same as main.py)
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _load_config():
    try:
        with open(_CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_config(cfg):
    with open(_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# Qt maps the macOS Command key to Qt.ControlModifier by default (it swaps
# Ctrl/Cmd on macOS, so physical Control arrives as MetaModifier). Accept
# BOTH so ⌘C / ⌘S / ⌘Z work on macOS and Ctrl+… works on Windows —
# regardless of the swap setting.
MODIFIER_KEY = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier

# Import tools from your existing tools.py
from tools import (
    ArrowTool, RectangleTool, CircleTool, LineTool,
    PenTool, HighlighterTool, TextTool, DrawingAction, draw_action
)

# --- Constants for Hit Testing ---
HANDLE_SIZE = 10
BORDER_WIDTH = 1

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
    """Generates LightShot-style icons programmatically"""
    
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
        
        # Scale factor for 40px icons (was 32px)
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
            painter.drawEllipse(int(8*s), int(8*s), int(16*s), int(16*s))
            
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
            
        elif name == "undo":
            path = QPainterPath()
            path.moveTo(22*s, 12*s)
            path.quadTo(16*s, 12*s, 12*s, 16*s)
            path.quadTo(12*s, 22*s, 18*s, 24*s)
            painter.drawPath(path)
            painter.drawLine(int(22*s), int(12*s), int(18*s), int(8*s))
            painter.drawLine(int(22*s), int(12*s), int(18*s), int(16*s))
            
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
            # Top-left corner bracket
            painter.drawLine(int(4*s), int(4*s), int(11*s), int(4*s))
            painter.drawLine(int(4*s), int(4*s), int(4*s), int(11*s))
            # Top-right corner bracket
            painter.drawLine(int(21*s), int(4*s), int(28*s), int(4*s))
            painter.drawLine(int(28*s), int(4*s), int(28*s), int(11*s))
            # Bottom-left corner bracket
            painter.drawLine(int(4*s), int(21*s), int(4*s), int(28*s))
            painter.drawLine(int(4*s), int(28*s), int(11*s), int(28*s))
            # Bottom-right corner bracket
            painter.drawLine(int(28*s), int(21*s), int(28*s), int(28*s))
            painter.drawLine(int(21*s), int(28*s), int(28*s), int(28*s))
            # Red filled circle in center
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e63946"))
            painter.drawEllipse(int(10*s), int(10*s), int(12*s), int(12*s))

        painter.end()
        return QIcon(pixmap)


class HoverButton(QToolButton):
    """Button that turns blue on hover"""
    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setFixedSize(40, 40)
        self.setCheckable(True)
        
        # FIX 1: Explicitly force Arrow Cursor on the button itself
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        # Pre-generate icons — clean ink at rest, brand purple when active.
        self.icon_normal = IconFactory.create_icon(icon_name, QColor("#3C3C43"))
        self.icon_active = IconFactory.create_icon(icon_name, QColor("#7A1FA6"))

        self.setIcon(self.icon_normal)
        self.setIconSize(QSize(40, 40))

        self.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                border-radius: 9px;
            }
            QToolButton:hover {
                background: #F2F2F7;
            }
            QToolButton:checked {
                background: #EFE8FB;
            }
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
    """Color picker popup: native macOS Colors (round wheel, tabs) + hex, copy, default.
    Keeps the sidebar slim — only the swatch is visible until clicked.
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

        # Open native macOS Colors (round wheel, sliders, palettes tabs)
        open_btn = QPushButton("Open color picker…", self)
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

        # Quick-pick palette
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

        # Current color swatch + hex + copy
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
        row.addWidget(self._hex_edit)

        copy_btn = QPushButton("Copy", self)
        copy_btn.setFixedWidth(50)
        copy_btn.clicked.connect(self._copy_hex)
        row.addWidget(copy_btn)
        row.addStretch()
        layout.addLayout(row)

        # Default checkbox
        cfg = _load_config()
        is_default = cfg.get("default_color", "").upper() == self._current.name().upper()
        self._default_cb = QCheckBox("Use as default for next session", self)
        self._default_cb.setChecked(is_default)
        layout.addWidget(self._default_cb)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK", self)
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

    def _open_native_picker(self):
        """Open native macOS Colors window (round wheel, tabs)."""
        color = QColorDialog.getColor(self._current, self, "Colors")
        if color.isValid():
            self._current = color
            self._hex_edit.blockSignals(True)
            self._hex_edit.setText(color.name().upper())
            self._hex_edit.blockSignals(False)
            self._update_swatch()

    def _pick_palette(self, hex_val: str):
        """One-click select from palette."""
        c = QColor(hex_val)
        if c.isValid():
            self._current = c
            self._hex_edit.blockSignals(True)
            self._hex_edit.setText(c.name().upper())
            self._hex_edit.blockSignals(False)
            self._update_swatch()

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
        if self._default_cb.isChecked():
            cfg = _load_config()
            cfg["default_color"] = self._current.name().upper()
            _save_config(cfg)
        else:
            cfg = _load_config()
            cfg.pop("default_color", None)
            _save_config(cfg)
        self.selected_color = self._current
        self.accept()


class OverlayWindow(QWidget):
    region_selected = pyqtSignal(QRect)
    selection_cancelled = pyqtSignal()
    image_copied = pyqtSignal()
    image_saved = pyqtSignal(str)
    recording_requested = pyqtSignal(QRect)
    
    def __init__(self, screenshot: Image.Image, offset_x: int = 0, offset_y: int = 0, capture_dpr: float = 1.0):
        super().__init__()
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.capture_dpr = capture_dpr

        self.screenshot = self._pil_to_pixmap(screenshot)
        self.original_image = screenshot
        
        self.start_point = None
        self.current_point = None
        self.selection_rect: QRect | None = None
        self.selection_complete = False
        self.resize_mode = ResizeMode.NONE
        
        self.current_tool = None
        # Load default color from config, fall back to black
        cfg = _load_config()
        default_hex = cfg.get("default_color", "#000000")
        self.current_color = QColor(default_hex)
        self.actions = []
        
        self.tool_toolbar = None
        self.action_toolbar = None
        self.tool_group = None
        
        # Inline text editing state
        self.text_editing = False
        self.text_position = None  # QPoint where text is being typed
        self.text_content = ""  # Current text being typed
        self.text_cursor_visible = True  # For blinking cursor
        self.editing_action_index = None  # Index of action being edited (None = new text)
        
        self._setup_window()
        
    def _pil_to_pixmap(self, pil_image: Image.Image) -> QPixmap:
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(qimage)
    
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setGeometry(
            self.offset_x,
            self.offset_y,
            self.screenshot.width(),
            self.screenshot.height()
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        # Must be focusable or keyPressEvent (Esc / ⌘C / ⌘S / ⌘Z) never fires.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _add_card_shadow(self, widget):
        """Soft drop shadow for a floating toolbar card (depth, Loom-like)."""
        eff = QGraphicsDropShadowEffect(widget)
        eff.setBlurRadius(36)
        eff.setColor(QColor(0, 0, 0, 80))
        eff.setOffset(0, 8)
        widget.setGraphicsEffect(eff)

    def _create_toolbars(self):
        # Vertical Toolbar (Tools)
        self.tool_toolbar = QFrame(self)
        # FIX 2: Explicitly force Arrow Cursor on the toolbar frame
        self.tool_toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        
        self.tool_toolbar.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5E5EA;
                border-radius: 18px;
            }
        """)
        self._add_card_shadow(self.tool_toolbar)

        tool_layout = QVBoxLayout(self.tool_toolbar)
        tool_layout.setContentsMargins(6, 6, 6, 6)
        tool_layout.setSpacing(3)
        
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        
        tools = [
            ("pen", "Pen", "pen"),
            ("line", "Line", "line"),
            ("arrow", "Arrow", "arrow"),
            ("rectangle", "Rectangle", "rectangle"),
            ("highlighter", "Highlighter", "highlighter"),
            ("text", "Text", "text"),
        ]
        
        self.tool_buttons = {}
        for icon_key, tooltip, tool_id in tools:
            btn = HoverButton(icon_key, tooltip, self.tool_toolbar)
            btn.setProperty("tool_id", tool_id)
            self.tool_group.addButton(btn)
            self.tool_buttons[tool_id] = btn
            tool_layout.addWidget(btn)
            
        # Color swatch only — hex, copy, Default move to popup on click
        self.color_btn = QToolButton(self.tool_toolbar)
        self.color_btn.setFixedSize(24, 24)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_color_button()
        self.color_btn.clicked.connect(self._pick_color)

        color_container = QWidget()
        color_layout = QVBoxLayout(color_container)
        color_layout.setContentsMargins(3, 5, 3, 2)
        color_layout.setSpacing(2)
        color_layout.addWidget(self.color_btn, 0, Qt.AlignmentFlag.AlignCenter)
        tool_layout.addWidget(color_container)
        
        # Undo
        undo_btn = HoverButton("undo", "Undo", self.tool_toolbar)
        undo_btn.setCheckable(False)
        undo_btn.clicked.connect(self._undo)
        tool_layout.addWidget(undo_btn)
        
        self.tool_toolbar.adjustSize()
        self.tool_group.buttonClicked.connect(self._on_tool_selected)
        
        # Horizontal Toolbar (Actions)
        self.action_toolbar = QFrame(self)
        # FIX 3: Explicitly force Arrow Cursor on the action toolbar
        self.action_toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        
        self.action_toolbar.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5E5EA;
                border-radius: 18px;
            }
        """)
        self._add_card_shadow(self.action_toolbar)

        action_layout = QHBoxLayout(self.action_toolbar)
        action_layout.setContentsMargins(6, 6, 6, 6)
        action_layout.setSpacing(3)
        
        actions = [
            ("record", "Record Screen", self._record),
            ("copy", "Copy", self._copy),
            ("save", "Save", self._save),
            ("close", "Close", self._cancel),
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
            
        if ty + self.tool_toolbar.height() > self.height():
            ty = self.height() - self.tool_toolbar.height() - margin
        if ty < 0: ty = margin
            
        self.tool_toolbar.move(tx, ty)
        self.tool_toolbar.show()
        
        ax = rect.right() - self.action_toolbar.width()
        ay = rect.bottom() + margin
        
        if ay + self.action_toolbar.height() > self.height():
            ay = rect.top() - self.action_toolbar.height() - margin
            
        if ax < 0: ax = margin
            
        self.action_toolbar.move(ax, ay)
        self.action_toolbar.show()

    def _update_color_button(self):
        self.color_btn.setStyleSheet(f"""
            QToolButton {{
                background-color: {self.current_color.name()};
                border: 1px solid #999;
                border-radius: 0px;
            }}
        """)

    def _set_overlay_level(self, level: int):
        """Set this overlay's underlying NSWindow stacking level (macOS).

        The overlay normally sits at level 25 (above normal windows) so it
        covers everything during capture. But that also hides any child
        dialog — like the color picker — *behind* it. We drop the level
        while a dialog is open, then restore it.
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
        # Drop the overlay below normal level so the picker popup (and the
        # native Apple Colors panel it can open) appear ABOVE it and stay
        # clickable. Without this the picker opens hidden behind the
        # full-screen overlay and nothing seems to happen.
        self._set_overlay_level(0)
        try:
            popup = _ColorPickerPopup(self.current_color, self)
            popup.setWindowModality(Qt.WindowModality.ApplicationModal)
            if popup.exec() == QDialog.DialogCode.Accepted and popup.selected_color.isValid():
                self.current_color = popup.selected_color
                self._update_color_button()
                if self.current_tool:
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
            "rectangle": RectangleTool, "highlighter": HighlighterTool, "text": TextTool
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
        if self.actions:
            self.actions.pop()
            self.update()

    # --- Hit Testing & Interaction ---
    
    def _get_hit_test(self, pos: QPoint):
        # FIX 4: Safety Check - if over toolbar, ignore resize logic completely
        if (self.tool_toolbar and self.tool_toolbar.isVisible() and self.tool_toolbar.geometry().contains(pos)) or \
           (self.action_toolbar and self.action_toolbar.isVisible() and self.action_toolbar.geometry().contains(pos)):
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
        
        t  = QRect(x + hw, y - hw, w - hs, hs)
        b  = QRect(x + hw, y + h - hw, w - hs, hs)
        l  = QRect(x - hw, y + hw, hs, h - hs)
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
            
        # FIX 5: Explicitly check toolbar overlap again
        if (self.tool_toolbar and self.tool_toolbar.isVisible() and self.tool_toolbar.geometry().contains(pos)) or \
           (self.action_toolbar and self.action_toolbar.isVisible() and self.action_toolbar.geometry().contains(pos)):
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
        if event.button() == Qt.MouseButton.LeftButton:
            # Finish any ongoing text editing first
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
                    self.selection_complete = False
                    self.selection_rect = None
                    self.tool_toolbar.hide()
                    self.action_toolbar.hide()
                    self.resize_mode = ResizeMode.NONE
                    self.tool_group.setExclusive(False)
                    for btn in self.tool_buttons.values(): btn.setChecked(False)
                    self.tool_group.setExclusive(True)
                    self.current_tool = None
            else:
                self.resize_mode = ResizeMode.NONE
                
            self.update()

    def mouseMoveEvent(self, event):
        self.current_point = event.pos()
        self._update_cursor(event.pos())
        
        if self.start_point:
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
                
                if self.resize_mode == ResizeMode.RIGHT: r.setRight(r.right() + dx)
                elif self.resize_mode == ResizeMode.LEFT: r.setLeft(r.left() + dx)
                elif self.resize_mode == ResizeMode.BOTTOM: r.setBottom(r.bottom() + dy)
                elif self.resize_mode == ResizeMode.TOP: r.setTop(r.top() + dy)
                elif self.resize_mode == ResizeMode.BOTTOM_RIGHT: 
                    r.setRight(r.right() + dx)
                    r.setBottom(r.bottom() + dy)
                elif self.resize_mode == ResizeMode.TOP_LEFT:
                    r.setLeft(r.left() + dx)
                    r.setTop(r.top() + dy)
                elif self.resize_mode == ResizeMode.TOP_RIGHT:
                    r.setRight(r.right() + dx)
                    r.setTop(r.top() + dy)
                elif self.resize_mode == ResizeMode.BOTTOM_LEFT:
                    r.setLeft(r.left() + dx)
                    r.setBottom(r.bottom() + dy)
                    
                self.selection_rect = r.normalized()
                self._position_toolbars()
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.selection_complete:
                if self.selection_rect and self.selection_rect.width() > 10 and self.selection_rect.height() > 10:
                    self.selection_complete = True
                    self._create_toolbars()
                else:
                    self.selection_rect = None
                self.start_point = None
                self.update()
                return
            
            if self.resize_mode == ResizeMode.NONE and self.current_tool:
                action = self.current_tool.on_mouse_release(event.pos())
                if action:
                    self.actions.append(action)
            
            self.start_point = None
            self.resize_mode = ResizeMode.NONE
            self.update()

    def _add_text(self, point):
        """Start inline text editing at the clicked point, or edit existing text if clicked on it"""
        from PyQt6.QtGui import QFontMetrics
        
        # Check if clicking on an existing text action (reverse order to select topmost)
        for i in range(len(self.actions) - 1, -1, -1):
            action = self.actions[i]
            if action.tool_type == "text" and action.points and action.text:
                text_pos = action.points[0]
                # Create bounding box using same font as rendering
                font = QFont(SYSTEM_FONT, action.font_size or 18, QFont.Weight.Bold)
                metrics = QFontMetrics(font)
                text_width = metrics.horizontalAdvance(action.text)
                text_height = metrics.height()
                
                # Larger hit area for easier clicking
                padding = 8
                hit_rect = QRect(
                    text_pos.x() - padding, 
                    text_pos.y() - text_height - padding, 
                    text_width + padding * 2, 
                    text_height + padding * 2
                )
                
                if hit_rect.contains(point):
                    # Edit existing text
                    self.text_editing = True
                    self.text_position = text_pos
                    self.text_content = action.text
                    self.editing_action_index = i
                    self.update()
                    return
        
        # Start new text at clicked position
        self.text_editing = True
        self.text_position = point
        self.text_content = ""
        self.editing_action_index = None
        self.update()

    def _finish_text_editing(self):
        """Finish text editing and save the text as an action"""
        if not self.text_editing:
            return
            
        if self.text_content.strip():
            if self.editing_action_index is not None:
                # Update existing action
                self.actions[self.editing_action_index].text = self.text_content
            else:
                # Create new action
                self.actions.append(DrawingAction(
                    tool_type="text", 
                    color=self.current_color, 
                    points=[self.text_position], 
                    text=self.text_content, 
                    font_size=18
                ))
        elif self.editing_action_index is not None and not self.text_content.strip():
            # If editing existing and text is empty, remove it
            del self.actions[self.editing_action_index]
        
        self.text_editing = False
        self.text_position = None
        self.text_content = ""
        self.editing_action_index = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        painter.drawPixmap(0, 0, self.screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        if self.selection_rect:
            rect = self.selection_rect
            
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            painter.drawPixmap(rect, self.screenshot, rect)
            
            painter.setClipRect(rect)
            for i, action in enumerate(self.actions):
                # Skip drawing the action that's currently being edited
                if self.text_editing and self.editing_action_index == i:
                    continue
                draw_action(painter, action)
            
            # Draw inline text being edited (with cursor)
            if self.text_editing and self.text_position:
                font = QFont(SYSTEM_FONT, 18, QFont.Weight.Bold)
                painter.setFont(font)
                painter.setPen(self.current_color)
                
                # Draw the text
                text_to_draw = self.text_content
                painter.drawText(self.text_position, text_to_draw)
                
                # Draw cursor (blinking line after text)
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(text_to_draw)
                text_height = metrics.height()
                cursor_x = self.text_position.x() + text_width + 1
                cursor_y = self.text_position.y()
                
                # Draw cursor line
                cursor_pen = QPen(self.current_color, 2)
                painter.setPen(cursor_pen)
                painter.drawLine(cursor_x, cursor_y - text_height + 4, cursor_x, cursor_y + 3)
            
            if self.current_tool and self.start_point and self.resize_mode == ResizeMode.NONE and self.selection_complete:
                self.current_tool.draw_preview(painter)
            
            painter.setClipping(False)
            
            # Clean solid purple selection border (brand color), replacing the
            # old dashed black/white outline.
            painter.setPen(QPen(QColor("#7A1FA6"), 2))
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
        
        painter.setPen(QColor(0,0,0, 50))
        painter.setBrush(Qt.GlobalColor.white)
        
        for p in points:
            painter.drawRect(int(p.x() - hw), int(p.y() - hw), hs, hs)

    def _draw_dimensions(self, painter, rect):
        text = f"{rect.width()} x {rect.height()}"
        font = QFont("Arial", 9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        
        x = rect.left()
        y = rect.top() - 20
        if y < 0: y = rect.top() + 5
        
        t_rect = metrics.boundingRect(text)
        painter.fillRect(x, y, t_rect.width() + 10, t_rect.height() + 5, QColor(0,0,0, 150))
        
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(x + 5, y + t_rect.height(), text)

    def _draw_instructions(self, painter):
        text = "Select area"
        font = QFont("Arial", 12)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(text)
        
        x = (self.width() - w) // 2
        y = 100
        
        painter.setPen(QColor(255,255,255, 100))
        painter.drawText(x, y, text)

    def _get_result_image(self) -> Image.Image:
        # Commit any text still being edited so it lands in the output.
        # Clicking the Copy/Save toolbar buttons doesn't pass through the
        # canvas mousePress that normally finalizes text, so without this
        # a lone text annotation would be silently dropped.
        if self.text_editing:
            self._finish_text_editing()
        if not self.selection_rect: return self.original_image
        rect = self.selection_rect
        dpr = self.capture_dpr

        # Create result at full physical resolution
        phys_w = int(rect.width() * dpr)
        phys_h = int(rect.height() * dpr)
        result = QPixmap(phys_w, phys_h)
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Scale painter so logical drawing commands map to physical pixels
        painter.scale(dpr, dpr)

        # Draw the screenshot region at physical resolution
        src_rect = QRect(
            int(rect.x() * dpr), int(rect.y() * dpr),
            phys_w, phys_h
        )
        painter.drawPixmap(0, 0, rect.width(), rect.height(),
                           self.screenshot, src_rect.x(), src_rect.y(),
                           src_rect.width(), src_rect.height())

        # Draw annotations at logical coordinates (painter is already scaled)
        painter.translate(-rect.x(), -rect.y())
        for action in self.actions:
            draw_action(painter, action)
        painter.end()

        qimage = result.toImage()
        buffer = qimage.bits().asstring(qimage.sizeInBytes())
        return Image.frombytes("RGBA", (qimage.width(), qimage.height()), buffer, "raw", "BGRA")

    def _copy(self):
        try:
            result = self._get_result_image()
            if IS_MACOS:
                self._copy_macos(result)
            else:
                if result.mode != "RGBA": result = result.convert("RGBA")
                data = result.tobytes("raw", "RGBA")
                qimage = QImage(data, result.width, result.height, QImage.Format.Format_RGBA8888).copy()
                QApplication.clipboard().setImage(qimage)
            self.image_copied.emit()
            self.close()
        except Exception as e:
            print(f"Copy error: {e}")
            self.close()

    def _copy_macos(self, pil_image):
        """Use native macOS NSPasteboard for universal clipboard compatibility"""
        import io
        import AppKit
        import Foundation

        # Convert to PNG bytes
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        png_data = Foundation.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))

        # Write to macOS pasteboard with proper types
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setData_forType_(png_data, AppKit.NSPasteboardTypePNG)

    def _save(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "", "PNG (*.png);;JPG (*.jpg)")
        if file_path:
            result = self._get_result_image()
            if not file_path.lower().endswith(('.png', '.jpg')): file_path += '.png'
            if file_path.lower().endswith('.jpg'): result = result.convert('RGB')
            result.save(file_path)
            self.image_saved.emit(file_path)
            self.close()

    def _record(self):
        if self.selection_rect:
            self.recording_requested.emit(QRect(self.selection_rect))
            self.close()

    def _cancel(self):
        self.selection_cancelled.emit()
        self.close()

    def keyPressEvent(self, event):
        # Handle inline text editing
        if self.text_editing:
            key = event.key()
            
            if key == Qt.Key.Key_Escape:
                # Cancel text editing without saving
                self.text_editing = False
                self.text_position = None
                self.text_content = ""
                self.editing_action_index = None
                self.update()
                return
                
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Finish editing and save text
                self._finish_text_editing()
                return
                
            elif key == Qt.Key.Key_Backspace:
                # Delete last character
                if self.text_content:
                    self.text_content = self.text_content[:-1]
                    self.update()
                return
                
            elif key == Qt.Key.Key_Delete:
                # Clear all text
                self.text_content = ""
                self.update()
                return
            
            # Handle Ctrl+V paste
            elif event.modifiers() & MODIFIER_KEY and key == Qt.Key.Key_V:
                clipboard = QApplication.clipboard()
                paste_text = clipboard.text()
                if paste_text:
                    self.text_content += paste_text
                    self.update()
                return
            
            # Handle Ctrl+C: finish text and copy screenshot
            elif event.modifiers() & MODIFIER_KEY and key == Qt.Key.Key_C:
                self._finish_text_editing()
                self._copy()
                return
            
            # Handle Ctrl+S: finish text and save screenshot
            elif event.modifiers() & MODIFIER_KEY and key == Qt.Key.Key_S:
                self._finish_text_editing()
                self._save()
                return
                
            else:
                # Add typed character (including space)
                text = event.text()
                if text and (text.isprintable() or text == ' '):
                    self.text_content += text
                    self.update()
                return
        
        # Normal keyboard shortcuts when not editing text
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._copy()
        elif event.modifiers() & MODIFIER_KEY:
            if event.key() == Qt.Key.Key_C:
                self._copy()
            elif event.key() == Qt.Key.Key_S:
                self._save()
            elif event.key() == Qt.Key.Key_Z:
                self._undo()

def show_overlay(screenshot: Image.Image, offset_x: int = 0, offset_y: int = 0) -> OverlayWindow:
    overlay = OverlayWindow(screenshot, offset_x, offset_y)
    overlay.showFullScreen()
    return overlay