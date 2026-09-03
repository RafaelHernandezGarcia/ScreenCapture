"""
ScreenCapture - Main Application Entry Point
A LightShot / Loom-style screenshot + screen recording tray app for
Windows and macOS.

Flow: global hotkey -> freeze the screen under the mouse -> OverlayWindow
(select, annotate, copy / save / record) -> optional recording pipeline
(SetupPanel -> CountdownOverlay -> ScreenRecorder + RecordingToolbar).
"""
import sys
import os
import threading
from typing import Optional

LOCK_PORT = int(os.environ.get("SCREENCAPTURE_LOCK_PORT", "47392"))


def _send_command(cmd: str) -> bool:
    """Talk to the running instance over the single-instance port.

    `python main.py --quit`     asks the running copy to exit cleanly
    `python main.py --capture`  triggers a screenshot in the running copy
    Returns True if a running instance accepted the command. Pure sockets,
    so this works before PyQt6 is imported (and from any Python).
    """
    import socket
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=1.0) as s:
            s.sendall(cmd.encode("ascii") + b"\n")
            return True
    except OSError:
        return False


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in ("--quit", "--capture"):
    _ok = _send_command(sys.argv[1].lstrip("-"))
    print(("sent" if _ok else "no running instance") + f" ({sys.argv[1]})")
    raise SystemExit(0 if _ok else 1)

from platform_utils import (
    IS_WINDOWS, IS_MACOS, SYSTEM_FONT, BRAND_PURPLE, BRAND_PURPLE_SOFT,
    INK, CARD_BORDER, physical_screen_rect, reveal_in_file_manager,
    open_folder, recordings_dir, screenshots_dir, set_startup_enabled,
    startup_enabled, force_foreground,
)
import app_config

# OpenCV camera auth on macOS - must be set before cv2 import
if IS_MACOS:
    os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

# Hide Python's Dock icon on macOS (we're a menu-bar-only app)
if IS_MACOS:
    try:
        import AppKit
        info = AppKit.NSBundle.mainBundle().infoDictionary()
        info["LSUIElement"] = "1"
        info.setdefault(
            "NSCameraUsageDescription",
            "ScreenCapture uses the camera for the webcam picture-in-picture "
            "overlay during recordings.",
        )
        info.setdefault(
            "NSMicrophoneUsageDescription",
            "ScreenCapture records microphone audio for your recordings.",
        )
    except Exception:
        pass

# NOTE: this is the full LightShot-style Qt app. A leaner pure-PyObjC
# screenshot core also exists in sc/ (run `python -m sc`) but it does NOT
# have the annotation toolbar, so the Qt app remains the default on macOS.
# Set SCREENCAPTURE_USE_NATIVE=1 to opt into the native core.
if __name__ == "__main__" and IS_MACOS and os.environ.get("SCREENCAPTURE_USE_NATIVE") == "1":
    from sc.app import main as _native_main
    raise SystemExit(_native_main())

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

# --- Config (shared store, see app_config.py) ---
CONFIG_PATH = app_config.CONFIG_PATH
load_config = app_config.load_config
save_config = app_config.save_config

# --- Windows DPI ---
# Default (windows_dpi_scaling = true): let Qt scale the UI to the monitor
# (toolbars look right on a 150% laptop screen; mixed-DPI setups work) and
# map logical <-> physical pixels ourselves via platform_utils.
# Legacy (false): force 1:1 physical pixels, the behaviour of the first
# Windows build. Keep as an escape hatch.
if IS_WINDOWS and not app_config.get("windows_dpi_scaling", True):
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["QT_SCREEN_SCALE_FACTORS"] = "1"

from PyQt6.QtCore import Qt, QRect, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QAction, QCursor, QGuiApplication, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox,
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)

from capture import capture_region
from overlay import OverlayWindow
from recorder import (
    ScreenRecorder, StopRecordingButton, RecordingFrame,
    RecordingAnnotationOverlay, get_recordings_dir, generate_filename
)
from recording_toolbar import RecordingToolbar, DrawingSubPanel
from webcam import WebcamCapture, WebcamPreviewWidget, list_cameras
from countdown import CountdownOverlay

APP_NAME = "ScreenCapture"
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


# ---------------------------------------------------------------------------
# Hotkey name parsing (shared by the dialog and both platform backends)
# ---------------------------------------------------------------------------

# Windows virtual-key codes for the keys we let people pick.
_WIN_VK = {
    "PRINT": 0x2C, "PRINTSCREEN": 0x2C, "SYSREQ": 0x2C,
    "PAUSE": 0x13, "SCROLLLOCK": 0x91, "NUMLOCK": 0x90,
    "INSERT": 0x2D, "INS": 0x2D, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PAGEUP": 0x21, "PGDOWN": 0x22, "PAGEDOWN": 0x22,
    "DEL": 0x2E, "DELETE": 0x2E, "SPACE": 0x20, "TAB": 0x09,
    "BACKSPACE": 0x08, "ENTER": 0x0D, "RETURN": 0x0D,
}
for _i in range(1, 25):
    _WIN_VK[f"F{_i}"] = 0x70 + _i - 1
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _WIN_VK[_c] = ord(_c)
for _d in "0123456789":
    _WIN_VK[_d] = ord(_d)

_MOD_ALIASES = {
    "CTRL": "Ctrl", "CONTROL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt",
    "OPTION": "Alt", "META": "Meta", "WIN": "Meta", "WINDOWS": "Meta",
    "CMD": "Meta", "COMMAND": "Meta", "SUPER": "Meta",
}


def parse_hotkey(name: str):
    """'Ctrl+Shift+S' -> (['Ctrl', 'Shift'], 'S'). Key is upper-cased, spaces removed."""
    parts = [p.strip() for p in str(name or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        return [], ""
    mods = []
    for p in parts[:-1]:
        m = _MOD_ALIASES.get(p.upper())
        if m and m not in mods:
            mods.append(m)
    key = parts[-1].upper()
    if key == "PRINT SCREEN" or key == "PRINTSCREEN":
        key = "PRINT"
    return mods, key


def pretty_hotkey(name: str) -> str:
    """Human label, e.g. 'PrintScreen', 'Ctrl+Shift+S'."""
    mods, key = parse_hotkey(name)
    label = {"PRINT": "PrintScreen", "SCROLLLOCK": "Scroll Lock",
             "PGUP": "Page Up", "PGDOWN": "Page Down"}.get(key, key.title() if len(key) > 1 else key)
    if IS_MACOS:
        # Qt records Command as "Ctrl" and Control as "Meta" on macOS
        mods = [{"Ctrl": "Cmd", "Meta": "Ctrl"}.get(m, m) for m in mods]
    else:
        mods = ["Win" if m == "Meta" else m for m in mods]
    return "+".join(mods + [label])


class WindowsHotkey:
    """Process-global hotkey on Windows via RegisterHotKey.

    The hotkey must be registered on the thread that pumps its messages, so
    a small daemon thread owns it. re-register() posts WM_QUIT to that
    thread first so the previous key is released (the earlier build leaked
    the old registration every time the shortcut was changed).
    """
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
    WM_HOTKEY, WM_QUIT, PM_NOREMOVE = 0x0312, 0x0012, 0x0000
    HOTKEY_ID = 1

    def __init__(self, on_press):
        self._on_press = on_press
        self._thread = None
        self._thread_id = None
        self.last_error = ""

    def register(self, name: str) -> bool:
        self.unregister()
        mods, key = parse_hotkey(name)
        vk = _WIN_VK.get(key)
        if vk is None:
            self.last_error = f"Unsupported key: {key}"
            return False
        mod_flags = self.MOD_NOREPEAT
        for m in mods:
            mod_flags |= {"Ctrl": self.MOD_CONTROL, "Shift": self.MOD_SHIFT,
                          "Alt": self.MOD_ALT, "Meta": self.MOD_WIN}[m]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        ready = threading.Event()
        result = {}

        def _loop():
            self._thread_id = kernel32.GetCurrentThreadId()
            msg = ctypes.wintypes.MSG()
            # Force-create this thread's message queue so PostThreadMessage works.
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, self.PM_NOREMOVE)
            ok = user32.RegisterHotKey(None, self.HOTKEY_ID, mod_flags, vk)
            result["ok"] = bool(ok)
            if not ok:
                result["err"] = kernel32.GetLastError()
            ready.set()
            if not ok:
                return
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                    try:
                        self._on_press()
                    except Exception as e:
                        print(f"[hotkey] callback error: {e}")
            user32.UnregisterHotKey(None, self.HOTKEY_ID)

        self._thread = threading.Thread(target=_loop, daemon=True, name="sc-hotkey")
        self._thread.start()
        ready.wait(timeout=3)
        if not result.get("ok"):
            err = result.get("err", "?")
            self.last_error = (f"Could not register {pretty_hotkey(name)} (error {err}). "
                               "Another app may already use it.")
            self._thread = None
            return False
        print(f"[hotkey] {pretty_hotkey(name)} registered")
        return True

    def unregister(self):
        if self._thread is not None and self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
                self._thread.join(timeout=2)
            except Exception:
                pass
        self._thread = None
        self._thread_id = None


class HotkeyDialog(QDialog):
    """Dialog to record a new hotkey (modifiers allowed: Ctrl+Shift+S, Win+F9...)."""

    def __init__(self, current_key_name="Print", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set capture shortcut")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(360, 170)
        self.recorded_key_name = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 16)

        self.label = QLabel(f"Current shortcut: <b>{pretty_hotkey(current_key_name)}</b>")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.instruction = QLabel("Press the key (or key combination) you want to use...")
        self.instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.instruction)

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        self.setFocus()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                   Qt.Key.Key_unknown):
            return
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        mods = event.modifiers()
        seq = QKeySequence(int(mods.value) | key).toString()
        if not seq:
            return
        # Normalise Qt spellings to our parser's vocabulary
        seq = seq.replace("Print Screen", "Print").replace("ScrollLock", "Scroll Lock")
        mods_list, k = parse_hotkey(seq)
        if IS_WINDOWS and k not in _WIN_VK:
            self.instruction.setText(f"'{seq}' cannot be used as a global shortcut. Try another key.")
            self.instruction.setStyleSheet("color: #b00020;")
            return
        self.recorded_key_name = "+".join(mods_list + [k if len(k) > 1 else k])
        self.instruction.setText(f"Selected: <b>{pretty_hotkey(self.recorded_key_name)}</b>")
        self.instruction.setStyleSheet("color: #007700; font-weight: bold;")
        QTimer.singleShot(400, self.accept)


class SignalEmitter(QObject):
    """Helper to emit signals from non-Qt threads"""
    capture_requested = pyqtSignal()
    camera_result = pyqtSignal(bool)  # camera permission grant result


def _menu_stylesheet():
    """Clean, modern tray menu on Windows (Qt's default looks dated there)."""
    if not IS_WINDOWS:
        return ""
    return f"""
        QMenu {{
            background: #FFFFFF;
            border: 1px solid {CARD_BORDER};
            padding: 6px;
            font-family: '{SYSTEM_FONT}';
            font-size: 13px;
            color: {INK};
        }}
        QMenu::item {{
            padding: 7px 30px 7px 12px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {BRAND_PURPLE_SOFT};
            color: {BRAND_PURPLE};
        }}
        QMenu::item:disabled {{
            color: #9A9AA0;
        }}
        QMenu::separator {{
            height: 1px;
            background: {CARD_BORDER};
            margin: 6px 8px;
        }}
        QMenu::indicator {{
            width: 14px; height: 14px; margin-left: 6px;
        }}
        QMenu::right-arrow {{
            margin-right: 8px;
        }}
    """


class ScreenCaptureApp:
    """Main application class managing the screenshot tool"""

    def __init__(self):
        # Legacy 1:1 mode only (Qt6 ignores the attribute otherwise)
        if IS_WINDOWS and not app_config.get("windows_dpi_scaling", True) \
                and hasattr(Qt.ApplicationAttribute, 'AA_DisableHighDpiScaling'):
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_DisableHighDpiScaling, True)

        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setOrganizationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)
        if IS_WINDOWS:
            # Taskbar / toast identity: shows "ScreenCapture", not "Python".
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RMScience.ScreenCapture")
            except Exception:
                pass

        # Pin NSApp to Accessory so macOS treats us as a menu-bar utility.
        if IS_MACOS:
            try:
                import AppKit
                if AppKit.NSApp is not None:
                    AppKit.NSApp.setActivationPolicy_(1)  # Accessory
            except Exception:
                pass

        self.overlay: Optional[OverlayWindow] = None
        self._win_hotkey: Optional[WindowsHotkey] = None
        self._hotkey_mgr = None  # macOS Carbon manager

        # Recording state
        self._recorder: Optional[ScreenRecorder] = None
        self._stop_btn: Optional[StopRecordingButton] = None
        self._toolbar: Optional[RecordingToolbar] = None
        self._draw_panel: Optional[DrawingSubPanel] = None
        self._countdown: Optional[CountdownOverlay] = None
        self._setup_panel = None
        self._is_recording = False
        self._last_screen_geo: Optional[QRect] = None
        self._last_capture_dpr: float = 1.0
        self._last_phys_origin = (0, 0)
        self._pending_record_region: Optional[dict] = None
        self._pending_logical_rect: Optional[QRect] = None
        self._recording_frame: Optional[RecordingFrame] = None
        self._webcam: Optional[WebcamCapture] = None
        self._webcam_preview: Optional[WebcamPreviewWidget] = None
        self._annotation_overlay: Optional[RecordingAnnotationOverlay] = None
        self._last_notified_path: Optional[str] = None

        self._camera_index = 0
        self._available_cameras = []

        self.config = load_config()
        self.hotkey_name = self.config.get("hotkey_name", "Print")
        self._camera_index = self.config.get("camera_index", 0)

        self.signal_emitter = SignalEmitter()
        self.signal_emitter.capture_requested.connect(self.start_capture)
        self.signal_emitter.camera_result.connect(self._on_camera_result)

        self._setup_tray()
        self._setup_hotkey()
        self._setup_control_channel()

    # ------------------------------------------------------- control channel

    def _setup_control_channel(self):
        """Accept 'quit' / 'capture' commands on the single-instance socket
        (see _send_command). Polled from the Qt loop; no extra thread."""
        self._control_sock = _lock_socket
        if self._control_sock is None:
            return
        try:
            self._control_sock.listen(2)
            self._control_sock.setblocking(False)
        except OSError:
            return
        self._control_timer = QTimer()
        self._control_timer.timeout.connect(self._poll_control_channel)
        self._control_timer.start(400)

    def _poll_control_channel(self):
        try:
            conn, _ = self._control_sock.accept()
        except (BlockingIOError, OSError):
            return
        try:
            conn.settimeout(0.5)
            data = conn.recv(64).decode("ascii", errors="ignore").strip().lower()
        except OSError:
            data = ""
        finally:
            try:
                conn.close()
            except OSError:
                pass
        if data == "quit":
            print("[control] quit requested")
            self._quit()
        elif data == "capture":
            self.start_capture()

    # ------------------------------------------------------------------ tray

    def _app_icon(self) -> QIcon:
        for name in (("icon.ico",) if IS_WINDOWS else ("icon_tray.png", "icon.png")):
            p = os.path.join(ASSETS_DIR, name)
            if os.path.exists(p):
                return QIcon(p)
        p = os.path.join(ASSETS_DIR, "icon.png")
        if os.path.exists(p):
            return QIcon(p)
        return self.app.style().standardIcon(self.app.style().StandardPixmap.SP_ComputerIcon)

    def _setup_tray(self):
        """System tray / menu-bar icon and its menu."""
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self._app_icon())
        self.tray.setToolTip(f"{APP_NAME} - press {pretty_hotkey(self.hotkey_name)} to capture")

        menu = QMenu()
        menu.setStyleSheet(_menu_stylesheet())
        self._menu = menu

        self.capture_action = QAction("Take Screenshot", menu)
        self.capture_action.triggered.connect(self.start_capture)
        menu.addAction(self.capture_action)
        self._stop_recording_action = QAction("Stop Recording", menu)
        self._stop_recording_action.triggered.connect(self._stop_recording)
        self._stop_recording_action.setVisible(False)
        menu.addAction(self._stop_recording_action)
        menu.addSeparator()

        # Settings
        self.hotkey_menu_action = QAction(f"Capture Shortcut:  {pretty_hotkey(self.hotkey_name)}", menu)
        self.hotkey_menu_action.triggered.connect(self._change_hotkey)
        menu.addAction(self.hotkey_menu_action)

        self._camera_menu = QMenu("Webcam", menu)
        self._camera_menu.setStyleSheet(_menu_stylesheet())
        self._camera_menu.aboutToShow.connect(self._populate_camera_menu)
        menu.addMenu(self._camera_menu)

        self._rec_size_menu = QMenu("Recording Quality", menu)
        self._rec_size_menu.setStyleSheet(_menu_stylesheet())
        self._rec_size_menu.aboutToShow.connect(self._populate_rec_size_menu)
        menu.addMenu(self._rec_size_menu)

        self._sys_audio_action = QAction("Record Computer Audio", menu)
        self._sys_audio_action.setCheckable(True)
        self._sys_audio_action.setChecked(bool(self.config.get("system_audio", True)))
        self._sys_audio_action.toggled.connect(lambda on: self._set_bool("system_audio", on))
        try:
            from audio_helper import system_audio_available
            if not system_audio_available():
                self._sys_audio_action.setEnabled(False)
                self._sys_audio_action.setText("Record Computer Audio (unavailable)")
        except Exception:
            pass
        menu.addAction(self._sys_audio_action)

        self._cursor_action = QAction("Show Mouse Cursor in Recordings", menu)
        self._cursor_action.setCheckable(True)
        self._cursor_action.setChecked(bool(self.config.get("show_cursor", True)))
        self._cursor_action.toggled.connect(lambda on: self._set_bool("show_cursor", on))
        menu.addAction(self._cursor_action)

        if IS_WINDOWS:
            self._startup_action = QAction("Start with Windows", menu)
            self._startup_action.setCheckable(True)
            self._startup_action.setChecked(startup_enabled())
            self._startup_action.toggled.connect(self._toggle_startup)
            menu.addAction(self._startup_action)
        menu.addSeparator()

        open_recordings_action = QAction("Open Recordings Folder", menu)
        open_recordings_action.triggered.connect(lambda: open_folder(get_recordings_dir()))
        menu.addAction(open_recordings_action)
        open_shots_action = QAction("Open Screenshots Folder", menu)
        open_shots_action.triggered.connect(lambda: open_folder(screenshots_dir()))
        menu.addAction(open_shots_action)
        about_action = QAction(f"About {APP_NAME}", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)
        menu.addSeparator()
        quit_action = QAction(f"Quit {APP_NAME}", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_notification_clicked)
        self.tray.show()

        # Native NSStatusItem path (mac_tray.py) is unused; keep attrs for compat.
        self._mac_tray = None
        self._stop_item = None
        self._hotkey_item = None

    def _set_bool(self, key, value):
        self.config[key] = bool(value)
        save_config(self.config)

    def _toggle_startup(self, on: bool):
        exe = sys.executable
        if IS_WINDOWS and exe.lower().endswith("python.exe"):
            candidate = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(candidate):
                exe = candidate
        main_py = os.path.abspath(__file__)
        ok = set_startup_enabled(
            on, exe, f'"{main_py}"', os.path.dirname(main_py),
            icon_path=os.path.join(ASSETS_DIR, "icon.ico"),
        )
        if not ok:
            self._notify("Could not change the startup setting.", critical=True)
            self._startup_action.blockSignals(True)
            self._startup_action.setChecked(startup_enabled())
            self._startup_action.blockSignals(False)

    def _notify(self, text, title=APP_NAME, ms=3000, critical=False, path=None):
        """Tray balloon. If `path` is given, clicking the balloon reveals it."""
        self._last_notified_path = path
        icon = (QSystemTrayIcon.MessageIcon.Critical if critical
                else QSystemTrayIcon.MessageIcon.Information)
        try:
            self.tray.showMessage(title, text, icon, ms)
        except Exception:
            pass

    def _on_notification_clicked(self):
        if self._last_notified_path:
            reveal_in_file_manager(self._last_notified_path)

    def _populate_rec_size_menu(self):
        """Choose the recorded video resolution."""
        self._rec_size_menu.clear()
        current = self.config.get("recording_size", "native")
        hint = QAction("Scales the video; aspect ratio kept", self._rec_size_menu)
        hint.setEnabled(False)
        self._rec_size_menu.addAction(hint)
        self._rec_size_menu.addSeparator()
        for label, key in [("Native (selection size)", "native"),
                           ("1080p", "1080p"), ("720p", "720p")]:
            act = QAction(label, self._rec_size_menu)
            act.setCheckable(True)
            act.setChecked(key == current)
            act.triggered.connect(lambda checked, k=key: self._set_rec_size(k))
            self._rec_size_menu.addAction(act)

    def _set_rec_size(self, key: str):
        self.config["recording_size"] = key
        save_config(self.config)
        self._notify(f"Recording size: {key} - applies to your next recording.", ms=2500)

    def _set_stop_visible(self, visible: bool):
        if getattr(self, "_stop_recording_action", None) is not None:
            self._stop_recording_action.setVisible(visible)
        if getattr(self, "capture_action", None) is not None:
            self.capture_action.setEnabled(not visible)

    def _set_hotkey_label(self, text: str):
        if getattr(self, "hotkey_menu_action", None) is not None:
            self.hotkey_menu_action.setText(text)

    # --------------------------------------------------------------- hotkey

    def _setup_hotkey(self):
        """Register global hotkey based on config"""
        try:
            if IS_WINDOWS:
                self._setup_hotkey_windows()
            elif IS_MACOS:
                self._setup_hotkey_macos()
        except Exception as e:
            print(f"Warning: Could not register hotkey: {e}")

    def _setup_hotkey_windows(self):
        if self._win_hotkey is None:
            self._win_hotkey = WindowsHotkey(lambda: self.signal_emitter.capture_requested.emit())
        if not self._win_hotkey.register(self.hotkey_name):
            self._notify(self._win_hotkey.last_error +
                         " Pick another one under Capture Shortcut.", ms=6000, critical=True)

    def _setup_hotkey_macos(self):
        """Register hotkey using Carbon RegisterEventHotKey (no Accessibility needed)."""
        from sc.hotkey import HotkeyManager, VK, CMD_KEY, SHIFT_KEY, OPTION_KEY, CONTROL_KEY
        mods, key = parse_hotkey(self.hotkey_name)
        vk = VK.get(key.capitalize(), 105)  # "F13" -> "F13", "PRINT" -> "Print"; default F13
        # Qt swaps Ctrl/Cmd on macOS: the dialog records Command as "Ctrl"
        # and physical Control as "Meta".
        carbon_mods = 0
        for m in mods:
            carbon_mods |= {"Ctrl": CMD_KEY, "Shift": SHIFT_KEY,
                            "Alt": OPTION_KEY, "Meta": CONTROL_KEY}[m]
        self._hotkey_mgr = HotkeyManager()
        self._hotkey_mgr.register(
            vk=vk, modifiers=carbon_mods,
            on_press=lambda: self.signal_emitter.capture_requested.emit(),
            signature="scrn",
        )

    def _change_hotkey(self):
        """Show dialog to change the hotkey"""
        dlg = HotkeyDialog(self.hotkey_name)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.recorded_key_name:
            previous = self.hotkey_name
            self.hotkey_name = dlg.recorded_key_name

            if IS_MACOS and self._hotkey_mgr is not None:
                self._hotkey_mgr.unregister_all()
                self._hotkey_mgr = None
                self._setup_hotkey()
            elif IS_WINDOWS:
                if not self._win_hotkey.register(self.hotkey_name):
                    self._notify(self._win_hotkey.last_error, ms=5000, critical=True)
                    self.hotkey_name = previous
                    self._win_hotkey.register(self.hotkey_name)
                    return

            self.config["hotkey_name"] = self.hotkey_name
            save_config(self.config)
            self._set_hotkey_label(f"Capture Shortcut:  {pretty_hotkey(self.hotkey_name)}")
            self.tray.setToolTip(f"{APP_NAME} - press {pretty_hotkey(self.hotkey_name)} to capture")
            self._notify(f"Shortcut changed to {pretty_hotkey(self.hotkey_name)}", ms=2000)

    # --------------------------------------------------------------- camera

    def _populate_camera_menu(self):
        """Populate the camera submenu with available cameras."""
        self._camera_menu.clear()
        try:
            self._available_cameras = list_cameras()
        except Exception:
            self._available_cameras = []

        if not self._available_cameras:
            no_cam = QAction("No cameras detected", self._camera_menu)
            no_cam.setEnabled(False)
            self._camera_menu.addAction(no_cam)
        for idx, name in self._available_cameras:
            action = QAction(name, self._camera_menu)
            action.setCheckable(True)
            action.setChecked(idx == self._camera_index)
            action.triggered.connect(lambda checked, i=idx, n=name: self._select_camera(i, n))
            self._camera_menu.addAction(action)
        self._camera_menu.addSeparator()
        refresh = QAction("Refresh list", self._camera_menu)
        refresh.triggered.connect(lambda: list_cameras(refresh=True))
        self._camera_menu.addAction(refresh)

    def _select_camera(self, index: int, name: str):
        self._camera_index = index
        self.config["camera_index"] = index
        save_config(self.config)
        self._notify(f"Camera set to: {name}", ms=2000)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.start_capture()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger and IS_WINDOWS:
            # Single left click on Windows: capture too (that's what people try first)
            self.start_capture()

    # -------------------------------------------------------------- capture

    def start_capture(self):
        """Start the screen capture process, or stop recording if active."""
        if self._is_recording:
            self._stop_recording()
            return
        if self._setup_panel is not None or self._countdown is not None:
            return  # a recording is being set up; ignore the hotkey
        if IS_MACOS and not self._ensure_screen_recording():
            return
        if self.overlay:
            self.overlay.close()
            self.overlay = None
        # Grab on the same runloop tick as the trigger (a delay let the menu
        # dismiss animation or the desktop show up in the screenshot).
        self._do_capture()

    def _ensure_screen_recording(self) -> bool:
        """macOS: True if we can capture real screen content, else guide the user."""
        try:
            from platform_utils import (
                has_screen_recording_permission,
                request_screen_recording_permission,
                open_settings_pane,
            )
        except Exception:
            return True
        if has_screen_recording_permission():
            return True
        request_screen_recording_permission()
        msg = QMessageBox()
        msg.setWindowTitle("Screen Recording permission needed")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "ScreenCapture needs Screen Recording permission to capture your "
            "windows. Without it, screenshots show only the desktop wallpaper.\n\n"
            "Enable ScreenCapture under System Settings > Privacy & Security > "
            "Screen Recording, then quit and reopen the app."
        )
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()
        open_settings_pane("ScreenCapture")
        return False

    def _do_capture(self):
        """Freeze the screen under the mouse and open the selection overlay."""
        try:
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
            geo = screen.geometry()  # logical
            px, py, pw, ph = physical_screen_rect(screen)

            screenshot = None
            if IS_MACOS and self.config.get("high_res_screenshots"):
                try:
                    from sc.capture import grab as _cg_grab
                    screenshot = _cg_grab({"x": geo.x(), "y": geo.y(),
                                           "w": geo.width(), "h": geo.height()})
                    if screenshot is None or screenshot.width < geo.width():
                        screenshot = None
                except Exception as e:
                    print(f"[capture] CG grab failed ({e}); using mss")
                    screenshot = None
            if screenshot is None:
                screenshot = capture_region(px, py, pw, ph)

            capture_dpr = screenshot.width / geo.width() if geo.width() > 0 else 1.0
            self._last_screen_geo = geo
            self._last_capture_dpr = capture_dpr
            self._last_phys_origin = (px, py)

            self.overlay = OverlayWindow(screenshot, geo.x(), geo.y(), capture_dpr)
            self.overlay.selection_cancelled.connect(self._on_overlay_closed)
            self.overlay.image_copied.connect(self._on_overlay_closed)
            self.overlay.image_saved.connect(self._on_image_saved)
            self.overlay.recording_requested.connect(self._on_recording_requested)
            self.overlay.setGeometry(geo)

            if IS_MACOS:
                _prepare_overlay_window(self.overlay)
            self.overlay.show()
            if IS_MACOS:
                _make_key_without_activating(self.overlay)
            else:
                self.overlay.activateWindow()
                force_foreground(self.overlay)  # keyboard focus even without the foreground lock
                self.overlay.setFocus()
            self.overlay.raise_()
        except Exception as e:
            QMessageBox.critical(None, "Capture Error", f"Failed to capture screen: {e}")

    def _on_overlay_closed(self):
        self.overlay = None

    def _on_image_saved(self, path: str):
        self.overlay = None
        # Windows: a click-to-open toast is the natural feedback. macOS: none
        # (menu-bar apps there stay quiet; the Save dialog already showed where).
        if IS_WINDOWS:
            self._notify(f"Saved {os.path.basename(path)}  (click to show in folder)",
                         title="Screenshot saved", ms=4000, path=path)

    # --------------------------------------------------- recording lifecycle

    def _to_physical(self, logical_rect: QRect) -> dict:
        """Logical screen rect -> physical mss region (uses the captured screen's origin)."""
        geo = self._last_screen_geo
        dpr = self._last_capture_dpr
        px, py = self._last_phys_origin
        return {
            "left": px + int(round((logical_rect.x() - geo.x()) * dpr)),
            "top": py + int(round((logical_rect.y() - geo.y()) * dpr)),
            "width": int(round(logical_rect.width() * dpr)),
            "height": int(round(logical_rect.height() * dpr)),
        }

    def _on_recording_requested(self, selection_rect: QRect):
        """Called when user clicks the record button in the overlay."""
        self.overlay = None  # overlay already closed itself
        geo = self._last_screen_geo

        self._pending_logical_rect = QRect(
            geo.x() + selection_rect.x(), geo.y() + selection_rect.y(),
            selection_rect.width(), selection_rect.height(),
        )
        self._pending_record_region = self._to_physical(self._pending_logical_rect)

        # --- Loom-style setup phase ---
        self._recording_frame = RecordingFrame(self._pending_logical_rect)
        self._recording_frame.region_moved.connect(self._on_region_moved)
        self._recording_frame.show()

        cam_default = bool(self.config.get("webcam_default", False))
        mic_default = bool(self.config.get("mic_default", True))
        if cam_default and self._camera_authorized():
            self._show_webcam_preview()

        sys_audio_on = None
        try:
            from audio_helper import system_audio_available
            if system_audio_available():
                sys_audio_on = bool(self.config.get("system_audio", True))
        except Exception:
            pass

        from setup_panel import SetupPanel
        self._setup_panel = SetupPanel(
            geo,
            camera_name=self._current_camera_name(),
            mic_name="Microphone",
            webcam_on=cam_default,
            mic_on=mic_default,
            system_audio_on=sys_audio_on,
        )
        self._setup_panel.webcam_toggled.connect(self._on_webcam_toggled)
        self._setup_panel.mic_toggled.connect(self._on_setup_mic)
        self._setup_panel.system_audio_toggled.connect(self._on_setup_sys_audio)
        self._setup_panel.start_clicked.connect(self._begin_countdown)
        self._setup_panel.cancel_clicked.connect(self._cancel_setup)
        self._setup_panel.show()

    def _camera_authorized(self) -> bool:
        if not IS_MACOS:
            return True
        try:
            from platform_utils import camera_permission_status
            return camera_permission_status() == "authorized"
        except Exception:
            return True

    def _current_camera_name(self) -> str:
        try:
            cams = self._available_cameras or list_cameras()
            for idx, name in cams:
                if idx == self._camera_index:
                    return name
            if cams:
                return cams[0][1]
        except Exception:
            pass
        return "Webcam"

    def _on_setup_mic(self, on: bool):
        self._set_bool("mic_default", on)

    def _on_setup_sys_audio(self, on: bool):
        self._set_bool("system_audio", on)
        if getattr(self, "_sys_audio_action", None) is not None:
            self._sys_audio_action.blockSignals(True)
            self._sys_audio_action.setChecked(bool(on))
            self._sys_audio_action.blockSignals(False)

    def _begin_countdown(self):
        if self._setup_panel:
            self._setup_panel.close()
            self._setup_panel = None
        geo = self._last_screen_geo
        self._countdown = CountdownOverlay(geo, region_rect=self._pending_logical_rect)
        self._countdown.countdown_finished.connect(self._start_recording)
        self._countdown.show()

    def _cancel_setup(self):
        if self._setup_panel:
            self._setup_panel.close()
            self._setup_panel = None
        self._hide_webcam_preview()
        if self._recording_frame:
            self._recording_frame.close()
            self._recording_frame = None
        self._pending_record_region = None
        self._pending_logical_rect = None

    def _start_recording(self):
        """Start the actual screen recording after the countdown finishes."""
        self._countdown = None
        region = self._pending_record_region
        if not region:
            return

        output_dir = get_recordings_dir()
        output_path = os.path.join(output_dir, generate_filename())

        logical_origin = (self._pending_logical_rect.x(), self._pending_logical_rect.y())
        target_height = {"1080p": 1080, "720p": 720}.get(
            self.config.get("recording_size"), None)
        self._recorder = ScreenRecorder(
            region, output_path,
            dpr=self._last_capture_dpr,
            logical_origin=logical_origin,
            target_height=target_height,
            mic_muted=not bool(self.config.get("mic_default", True)),
            webcam_latency_ms=(160 if self._webcam_preview is not None else 0),
            system_audio=bool(self.config.get("system_audio", True)),
            show_cursor=bool(self.config.get("show_cursor", True)),
        )
        self._recorder.recording_stopped.connect(self._on_recording_stopped)
        self._recorder.recording_error.connect(self._on_recording_error)

        if self._recording_frame is None:
            self._recording_frame = RecordingFrame(self._pending_logical_rect)
            self._recording_frame.region_moved.connect(self._on_region_moved)
            self._recording_frame.show()

        try:
            self._annotation_overlay = RecordingAnnotationOverlay(self._pending_logical_rect)
            self._annotation_overlay.show()
            self._recorder.set_annotation_overlay(self._annotation_overlay)
        except Exception as e:
            print(f"[recording] annotation overlay failed: {e}")

        try:
            self._toolbar = RecordingToolbar(self._last_screen_geo,
                                             recording_rect=self._pending_logical_rect)
            self._toolbar.stop_clicked.connect(self._stop_recording)
            self._toolbar.pause_clicked.connect(self._on_pause_recording)
            self._toolbar.resume_clicked.connect(self._on_resume_recording)
            self._toolbar.mic_toggled.connect(self._on_mic_toggled)
            self._toolbar.webcam_toggled.connect(self._on_webcam_toggled)
            self._toolbar.draw_toggled.connect(self._on_draw_toggled)
            self._toolbar.show()
        except Exception as e:
            print(f"[recording] toolbar failed: {e}")

        if self._toolbar:
            if self._webcam_preview is not None:
                self._toolbar.set_webcam_on()
            if not bool(self.config.get("mic_default", True)):
                try:
                    self._toolbar.set_mic_muted(True)
                except Exception:
                    pass

        self._is_recording = True
        self._set_stop_visible(True)
        self._recorder.start()
        print("[recording] started")

    def _on_pause_recording(self):
        if self._recorder:
            self._recorder.pause()

    def _on_resume_recording(self):
        if self._recorder:
            self._recorder.resume()

    def _on_mic_toggled(self, muted: bool):
        if self._recorder:
            self._recorder.set_mic_muted(muted)

    def _on_webcam_toggled(self, on: bool):
        self._set_bool("webcam_default", on)
        if on:
            if IS_MACOS:
                from platform_utils import camera_permission_status, request_camera_permission
                status = camera_permission_status()
                if status == "authorized":
                    self._show_webcam_preview()
                elif status == "not_determined":
                    request_camera_permission(
                        lambda g: self.signal_emitter.camera_result.emit(bool(g))
                    )
                else:
                    self._show_camera_denied()
            else:
                self._show_webcam_preview()
        else:
            self._hide_webcam_preview()

    def _show_webcam_preview(self):
        """Open the camera (if needed) and show the draggable circular PiP."""
        if self._webcam_preview is not None:
            return
        if self._pending_logical_rect is None:
            return
        if self._webcam is None:
            self._webcam = WebcamCapture(device_index=self._camera_index)
            self._webcam.start()
        self._webcam_preview = WebcamPreviewWidget(
            self._webcam, self._pending_logical_rect, dpr=self._last_capture_dpr,
        )
        self._webcam_preview.position_changed.connect(self._on_webcam_position)
        self._webcam_preview.show()
        if self._toolbar:
            try:
                self._toolbar.set_webcam_on()
            except Exception:
                pass

    def _hide_webcam_preview(self):
        if self._webcam_preview:
            self._webcam_preview.close()
            self._webcam_preview = None
        if self._webcam:
            self._webcam.stop()
            self._webcam.wait(2000)
            self._webcam = None

    def _on_camera_result(self, granted: bool):
        if granted:
            self._show_webcam_preview()
        else:
            self._show_camera_denied()

    def _show_camera_denied(self):
        from platform_utils import open_settings_pane
        if self._toolbar:
            try:
                self._toolbar.reset_webcam_button()
            except Exception:
                pass
        msg = QMessageBox()
        msg.setWindowTitle("Camera access needed")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "ScreenCapture needs Camera permission for the webcam overlay.\n\n"
            "Enable it under System Settings > Privacy & Security > Camera, "
            "then toggle the webcam again."
        )
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()
        if IS_MACOS:
            open_settings_pane("Camera")

    def _on_webcam_position(self, x: int, y: int, radius: int):
        if self._recorder:
            self._recorder.set_webcam_position(x, y, radius)

    def _on_region_moved(self, new_rect: QRect):
        """Called when the user drags the red recording border (setup or live)."""
        self._pending_logical_rect = QRect(new_rect)
        phys = self._to_physical(new_rect)
        if self._recorder is not None:
            self._recorder.region = {
                "left": phys["left"], "top": phys["top"],
                "width": self._recorder.region["width"],   # keep the encoder size
                "height": self._recorder.region["height"],
            }
            self._recorder.logical_origin = (new_rect.x(), new_rect.y())
        elif self._pending_record_region:
            self._pending_record_region["left"] = phys["left"]
            self._pending_record_region["top"] = phys["top"]

        if self._annotation_overlay:
            self._annotation_overlay.setGeometry(new_rect)
        if self._webcam_preview is not None:
            self._webcam_preview._recording_rect = QRect(new_rect)

    def _on_draw_toggled(self, active: bool):
        if self._annotation_overlay:
            self._annotation_overlay.set_drawing_active(active)
        if active:
            if not self._draw_panel:
                self._draw_panel = DrawingSubPanel(self._toolbar)
                self._draw_panel.tool_selected.connect(self._on_draw_tool_selected)
                self._draw_panel.color_changed.connect(self._on_draw_color_changed)
                self._draw_panel.clear_clicked.connect(self._on_draw_clear)
            self._draw_panel.position_near_toolbar()
            self._draw_panel.show()
        elif self._draw_panel:
            self._draw_panel.hide()

    def _on_draw_tool_selected(self, tool_name: str):
        if self._annotation_overlay:
            self._annotation_overlay.set_tool(tool_name)

    def _on_draw_color_changed(self, color):
        if self._annotation_overlay:
            self._annotation_overlay.set_color(color)

    def _on_draw_clear(self):
        if self._annotation_overlay:
            self._annotation_overlay.clear_annotations()

    def _stop_recording(self):
        """Stop recording - and clear the UI INSTANTLY so it feels reactive.

        Finalizing the file (flush encoder + mix audio + ffmpeg remux) takes a
        beat. The overlays vanish the moment Stop is pressed and the recorder
        finalizes on its own thread; _on_recording_stopped does the teardown
        and reveals the saved file.
        """
        if not self._recorder:
            return
        for w in (self._toolbar, self._stop_btn, self._draw_panel,
                  self._recording_frame, self._annotation_overlay,
                  self._webcam_preview, self._setup_panel):
            if w is not None:
                try:
                    w.hide()
                except Exception:
                    pass
        self._set_stop_visible(False)
        self._recorder.stop()

    def _on_recording_stopped(self, file_path: str):
        self._cleanup_recording()
        # Reveal the new file in Finder / Explorer: quiet, professional feedback.
        if os.path.exists(file_path):
            reveal_in_file_manager(file_path)
        else:
            open_folder(get_recordings_dir())

    def _on_recording_error(self, error_msg: str):
        self._cleanup_recording()
        self._notify(error_msg, title="Recording error", ms=5000, critical=True)

    def _cleanup_recording(self):
        for attr in ("_setup_panel", "_stop_btn", "_toolbar", "_draw_panel",
                     "_recording_frame", "_annotation_overlay", "_webcam_preview"):
            w = getattr(self, attr)
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._webcam:
            self._webcam.stop()
            self._webcam.wait(2000)
            self._webcam = None
        self._recorder = None
        self._is_recording = False
        self._pending_record_region = None
        self._pending_logical_rect = None
        self._set_stop_visible(False)

    # ---------------------------------------------------------------- misc

    def _show_about(self):
        msg = QMessageBox()
        msg.setWindowTitle(APP_NAME)
        msg.setText(f"<b style='font-size:15px'>{APP_NAME}</b>")
        msg.setInformativeText(
            "Screenshots and screen recordings, LightShot / Loom style.\n\n"
            f"Press {pretty_hotkey(self.hotkey_name)} anywhere: drag to select, then copy, "
            "save, annotate (arrow, box, blur, text...) or record with webcam and audio.\n\n"
            "Shortcuts inside the overlay: Enter = copy, Ctrl+S = save, "
            "Ctrl+Z / Ctrl+Y = undo / redo, Ctrl+A = whole screen, Esc = close."
        )
        icon_path = os.path.join(ASSETS_DIR, "icon.png")
        if os.path.exists(icon_path):
            pix = QPixmap(icon_path).scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            msg.setIconPixmap(pix)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()

    def _quit(self):
        if self._recorder:
            try:
                self._recorder.stop()
                self._recorder.wait(8000)
            except Exception:
                pass
        if self._win_hotkey:
            self._win_hotkey.unregister()
        self.tray.hide()
        self.app.quit()

    def run(self):
        return self.app.exec()


# ---------------------------------------------------------------------------
# macOS overlay window helpers (no-ops elsewhere)
# ---------------------------------------------------------------------------

def _prepare_overlay_window(widget):
    """Realize a Qt widget's underlying NSWindow and configure it for
    macOS before the first show(): CanJoinAllSpaces (no Space switch) plus
    the non-activating panel mask (keyboard works without activating)."""
    if not IS_MACOS:
        return
    try:
        import ctypes as _ct
        import objc
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorStationary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorTransient,
            NSWindowStyleMaskNonactivatingPanel,
        )
        widget.create()
        ptr = int(widget.winId())
        if ptr == 0:
            return
        nsview = objc.objc_object(c_void_p=_ct.c_void_p(ptr))
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setLevel_(25)
        nswindow.setHidesOnDeactivate_(False)
        nswindow.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorTransient
        )
        try:
            current_mask = int(nswindow.styleMask())
            nswindow.setStyleMask_(current_mask | NSWindowStyleMaskNonactivatingPanel)
        except Exception:
            pass
    except Exception as e:
        print(f"_prepare_overlay_window: {e}")


def _make_key_without_activating(widget):
    """Give the overlay keyboard focus on macOS (activates the app; safe
    because the window already joins all Spaces)."""
    if not IS_MACOS:
        return
    try:
        import ctypes as _ct
        import objc
        from AppKit import NSApp
        ptr = int(widget.winId())
        if ptr == 0:
            widget.activateWindow()
            widget.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            return
        nsview = objc.objc_object(c_void_p=_ct.c_void_p(ptr))
        nswindow = nsview.window()
        if nswindow is not None:
            nswindow.makeKeyAndOrderFront_(None)
        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass
        widget.activateWindow()
        widget.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
    except Exception as e:
        print(f"_make_key_without_activating: {e}")
        widget.activateWindow()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def show_already_running_notification():
    """Tell the user the app is already in the tray, then exit."""
    temp_app = QApplication(sys.argv)
    temp_app.setQuitOnLastWindowClosed(False)
    icon_path = os.path.join(ASSETS_DIR, "icon.ico" if IS_WINDOWS else "icon.png")
    tray = QSystemTrayIcon()
    if os.path.exists(icon_path):
        tray.setIcon(QIcon(icon_path))
    tray.show()
    tray.showMessage(
        APP_NAME,
        "ScreenCapture is already running - look for its icon in the system tray.",
        QSystemTrayIcon.MessageIcon.Information,
        3000
    )
    QTimer.singleShot(3500, temp_app.quit)
    temp_app.exec()


_lock_socket = None  # kept alive for the process lifetime


def main():
    global _lock_socket
    import socket

    # Single-instance lock: binding a localhost port is reliable on every OS.
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', LOCK_PORT))
    except socket.error:
        show_already_running_notification()
        sys.exit(0)

    app = ScreenCaptureApp()
    # No startup toast - the tray icon is enough.
    sys.exit(app.run())


if __name__ == "__main__":
    main()
