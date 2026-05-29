"""
ScreenCapture - Main Application Entry Point
A LightShot-like screenshot tool for Windows and macOS
"""
import sys
import os
import json
from typing import Optional

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# OpenCV camera auth on macOS — must be set before cv2 import
if IS_MACOS:
    os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

# Hide Python's Dock icon on macOS (we're a menu-bar-only app)
if IS_MACOS:
    try:
        import AppKit
        info = AppKit.NSBundle.mainBundle().infoDictionary()
        info["LSUIElement"] = "1"
        # Usage descriptions so the camera/mic permission prompts can appear.
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

# NOTE: this is the full LightShot-style Qt app (rich annotation overlay:
# pen/line/arrow/rect/highlighter/text, color picker, undo, resize/move
# handles, record/copy/save/close action bar). A leaner pure-PyObjC
# screenshot core also exists in sc/ (run `python -m sc`) but it does NOT
# yet have the annotation toolbar, so the Qt app remains the default on
# macOS. Set SCREENCAPTURE_USE_NATIVE=1 to opt into the native core.
if __name__ == "__main__" and IS_MACOS and os.environ.get("SCREENCAPTURE_USE_NATIVE") == "1":
    from sc.app import main as _native_main
    raise SystemExit(_native_main())

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

# --- Config ---
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# --- 1. THE NUCLEAR FIX: DISABLE ALL SCALING (Windows only) ---
# We must set these BEFORE importing PyQt6.
# This forces the app to run in 1:1 physical pixels, preventing the "Zoom" effect.
if IS_WINDOWS:
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["QT_SCREEN_SCALE_FACTORS"] = "1"
# -----------------------------------------------

import threading

from PyQt6.QtCore import Qt, QRect, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QAction, QCursor, QGuiApplication, QKeySequence
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


def _prepare_overlay_window(widget):
    """Realize a Qt widget's underlying NSWindow and configure it for
    macOS before the first show().

    - CanJoinAllSpaces + Stationary + Transient before show:
      prevents macOS from switching to the app's "home" Space and
      revealing the desktop wallpaper.
    - NSWindowStyleMaskNonactivatingPanel: lets the panel become the
      key window (and receive keyDown events like Cmd+C / Esc) WITHOUT
      activating the app, so we don't have to choose between "Space
      stays put" and "keyboard shortcuts work".
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
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorTransient,
            NSWindowStyleMaskNonactivatingPanel,
        )
        widget.create()  # force Qt to instantiate the NSView + NSWindow
        ptr = int(widget.winId())
        if ptr == 0:
            return
        nsview = objc.objc_object(c_void_p=_ct.c_void_p(ptr))
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setLevel_(25)  # above normal windows, below menu bar
        nswindow.setHidesOnDeactivate_(False)
        nswindow.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorTransient
        )
        # Qt.Tool already makes this an NSPanel on macOS. Adding the
        # non-activating mask lets it become key without NSApp.activate.
        try:
            current_mask = int(nswindow.styleMask())
            nswindow.setStyleMask_(
                current_mask | NSWindowStyleMaskNonactivatingPanel
            )
        except Exception:
            pass
    except Exception as e:
        print(f"_prepare_overlay_window: {e}")


def _make_key_without_activating(widget):
    """Give the overlay full keyboard focus.

    We ALSO activate the app (NSApp.activateIgnoringOtherApps) — that's
    what actually makes Qt dispatch keyDown events to the widget's
    keyPressEvent. The Spaces-switch bug we feared from activation
    doesn't happen anymore because _prepare_overlay_window already set
    CanJoinAllSpaces on the NSWindow BEFORE show — the overlay exists
    on every Space, so macOS never switches to find it.
    """
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
        # Activate the app so Qt's keyboard dispatch works. Safe here
        # because the overlay has CanJoinAllSpaces set — macOS has
        # nowhere to switch to; the window is already on every Space.
        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass
        widget.activateWindow()
        widget.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
    except Exception as e:
        print(f"_make_key_without_activating: {e}")
        widget.activateWindow()


class HotkeyDialog(QDialog):
    """Dialog to record a new hotkey"""
    def __init__(self, current_key_name="PrintScreen", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Hotkey")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(320, 140)
        self.recorded_key = None
        self.recorded_key_name = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.label = QLabel(f"Current hotkey: <b>{current_key_name}</b>")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.instruction = QLabel("Press any key to set as the new hotkey...")
        self.instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        # Ignore bare modifier keys
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return
        key_name = QKeySequence(key).toString()
        if key_name:
            self.recorded_key = key
            self.recorded_key_name = key_name
            self.instruction.setText(f"Selected: <b>{key_name}</b>")
            self.instruction.setStyleSheet("color: #007700; font-weight: bold;")
            QTimer.singleShot(400, self.accept)


class SignalEmitter(QObject):
    """Helper to emit signals from non-Qt threads"""
    capture_requested = pyqtSignal()
    camera_result = pyqtSignal(bool)  # camera permission grant result


class ScreenCaptureApp:
    """Main application class managing the screenshot tool"""
    
    def __init__(self):
        # Prevent any leftover scaling attributes (Windows only)
        if IS_WINDOWS and hasattr(Qt.ApplicationAttribute, 'AA_DisableHighDpiScaling'):
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_DisableHighDpiScaling, True)

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # Pin NSApp to Accessory so macOS treats us as a menu-bar
        # utility: no Dock icon, no Space switch on activation.
        if IS_MACOS:
            try:
                import AppKit
                if AppKit.NSApp is not None:
                    AppKit.NSApp.setActivationPolicy_(1)  # Accessory
            except Exception:
                pass

        self.overlay: Optional[OverlayWindow] = None
        self._hotkey_listener = None

        # Recording state
        self._recorder: Optional[ScreenRecorder] = None
        self._stop_btn: Optional[StopRecordingButton] = None
        self._toolbar: Optional[RecordingToolbar] = None
        self._draw_panel: Optional[DrawingSubPanel] = None
        self._countdown: Optional[CountdownOverlay] = None
        self._is_recording = False
        self._last_screen_geo: Optional[QRect] = None
        self._last_capture_dpr: float = 1.0
        self._pending_record_region: Optional[dict] = None
        self._pending_logical_rect: Optional[QRect] = None
        self._recording_frame: Optional[RecordingFrame] = None
        self._webcam: Optional[WebcamCapture] = None
        self._webcam_preview: Optional[WebcamPreviewWidget] = None
        self._annotation_overlay: Optional[RecordingAnnotationOverlay] = None

        # Webcam camera selection
        self._camera_index = 0
        self._available_cameras = []  # populated on first webcam toggle

        # Load saved hotkey config
        self.config = load_config()
        self.hotkey_name = self.config.get("hotkey_name", "Print")
        self._camera_index = self.config.get("camera_index", 0)

        # Signal emitter for thread-safe capture triggering
        self.signal_emitter = SignalEmitter()
        self.signal_emitter.capture_requested.connect(self.start_capture)
        self.signal_emitter.camera_result.connect(self._on_camera_result)

        self._setup_tray()
        self._setup_hotkey()
    
    def _setup_tray(self):
        """Set up the menu-bar icon and menu.

        On macOS the visible icon + menu come from a native NSStatusItem
        (QSystemTrayIcon doesn't render for LSUIElement apps launched via
        LaunchServices). A hidden QSystemTrayIcon is still created for its
        showMessage() balloon notifications.
        """
        base_dir = os.path.dirname(__file__)
        tray_icon_path = os.path.join(base_dir, "assets", "icon_tray.png")
        icon_path = os.path.join(base_dir, "assets", "icon.png")

        self.tray = QSystemTrayIcon()
        if os.path.exists(tray_icon_path):
            self.tray.setIcon(QIcon(tray_icon_path))
        elif os.path.exists(icon_path):
            self.tray.setIcon(QIcon(icon_path))
        else:
            self.tray.setIcon(self.app.style().standardIcon(
                self.app.style().StandardPixmap.SP_ComputerIcon
            ))
        self.tray.setToolTip(f"ScreenCapture - Press {self.hotkey_name} to capture")

        # Qt context menu — used on Windows. On macOS the native menu mirrors it.
        menu = QMenu()
        capture_action = QAction("Capture Screen", menu)
        capture_action.triggered.connect(self.start_capture)
        menu.addAction(capture_action)
        self._stop_recording_action = QAction("Stop Recording", menu)
        self._stop_recording_action.triggered.connect(self._stop_recording)
        self._stop_recording_action.setVisible(False)
        menu.addAction(self._stop_recording_action)
        menu.addSeparator()
        self.hotkey_menu_action = QAction(
            f"Hotkey: {self.hotkey_name}  (click to change)", menu)
        self.hotkey_menu_action.triggered.connect(self._change_hotkey)
        menu.addAction(self.hotkey_menu_action)
        self._camera_menu = QMenu("Camera", menu)
        self._camera_menu.aboutToShow.connect(self._populate_camera_menu)
        menu.addMenu(self._camera_menu)
        self._audio_sync_menu = QMenu("Audio Sync (lip-sync)", menu)
        self._audio_sync_menu.aboutToShow.connect(self._populate_audio_sync_menu)
        menu.addMenu(self._audio_sync_menu)
        open_recordings_action = QAction("Open Recordings Folder", menu)
        open_recordings_action.triggered.connect(self._open_recordings_folder)
        menu.addAction(open_recordings_action)
        menu.addSeparator()
        about_action = QAction("About", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)
        menu.addSeparator()
        quit_action = QAction("Exit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        # NOTE: QSystemTrayIcon renders correctly when the app is launched
        # in the user's GUI session (LaunchAgent / shell), which is how this
        # app is meant to start. It does NOT render when launched through a
        # bash-script .app launcher via LaunchServices — that's why the app
        # installs a LaunchAgent (see install) instead of relying on a
        # double-clickable bundle. These attrs stay None (the optional native
        # NSStatusItem path in mac_tray.py is unused now).
        self._mac_tray = None
        self._stop_item = None
        self._hotkey_item = None

    def _build_camera_submenu(self, submenu):
        """Populate the native Camera submenu (rebuilt each time it opens)."""
        submenu.clear()
        try:
            self._available_cameras = list_cameras()
        except Exception:
            self._available_cameras = []
        if not self._available_cameras:
            submenu.add_item("No cameras detected", None, enabled=False)
            return
        for idx, name in self._available_cameras:
            submenu.add_item(
                name,
                lambda i=idx, n=name: self._select_camera(i, n),
                checked=(idx == self._camera_index),
            )

    def _populate_audio_sync_menu(self):
        """Lip-sync offset chooser (like OBS's audio sync offset)."""
        self._audio_sync_menu.clear()
        current = int(self.config.get("audio_offset_ms", 0))
        hint = QAction("If your voice LAGS your lips, pick 'earlier'", self._audio_sync_menu)
        hint.setEnabled(False)
        self._audio_sync_menu.addAction(hint)
        self._audio_sync_menu.addSeparator()
        # negative = advance the voice (fixes the common "audio is late" lag)
        options = [
            ("Voice much earlier  (−200 ms)", -200),
            ("Voice earlier  (−120 ms)", -120),
            ("Voice slightly earlier  (−60 ms)", -60),
            ("In sync  (0 ms)", 0),
            ("Voice slightly later  (+60 ms)", 60),
            ("Voice later  (+120 ms)", 120),
        ]
        for label, ms in options:
            act = QAction(label, self._audio_sync_menu)
            act.setCheckable(True)
            act.setChecked(ms == current)
            act.triggered.connect(lambda checked, m=ms: self._set_audio_offset(m))
            self._audio_sync_menu.addAction(act)

    def _set_audio_offset(self, ms: int):
        self.config["audio_offset_ms"] = int(ms)
        save_config(self.config)
        self.tray.showMessage(
            "ScreenCapture",
            f"Audio sync set to {ms:+d} ms — applies to your next recording.",
            QSystemTrayIcon.MessageIcon.Information, 2500,
        )

    def _set_stop_visible(self, visible: bool):
        """Show/hide the Stop Recording item in whichever menu is active."""
        if getattr(self, "_stop_recording_action", None) is not None:
            self._stop_recording_action.setVisible(visible)
        if getattr(self, "_stop_item", None) is not None:
            self._stop_item.set_hidden(not visible)

    def _set_hotkey_label(self, text: str):
        if getattr(self, "hotkey_menu_action", None) is not None:
            self.hotkey_menu_action.setText(text)
        if getattr(self, "_hotkey_item", None) is not None:
            self._hotkey_item.set_title(text)
    
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
        """Register hotkey using Windows native API"""
        MOD_NOREPEAT = 0x4000
        WM_HOTKEY = 0x0312
        HOTKEY_ID = 1

        # Map Qt key name to Windows virtual key code
        VK_MAP = {
            "Print": 0x2C, "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
            "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78,
            "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
            "F13": 0x7C, "F14": 0x7D, "F15": 0x7E, "F16": 0x7F,
            "Pause": 0x13, "Scroll Lock": 0x91, "Insert": 0x2D, "Home": 0x24,
        }
        vk_code = VK_MAP.get(self.hotkey_name, 0x2C)

        user32 = ctypes.windll.user32

        def hotkey_thread():
            if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, vk_code):
                print(f"Warning: Could not register hotkey, error code: {ctypes.windll.kernel32.GetLastError()}")
                return

            print(f"{self.hotkey_name} hotkey registered successfully!")

            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.signal_emitter.capture_requested.emit()

            user32.UnregisterHotKey(None, HOTKEY_ID)

        self.hotkey_thread = threading.Thread(target=hotkey_thread, daemon=True)
        self.hotkey_thread.start()

    def _setup_hotkey_macos(self):
        """Register hotkey using Carbon RegisterEventHotKey.

        No Accessibility permission needed (Carbon registers with the
        WindowServer directly). Grants survive app rebuilds — they're
        process-scoped, not TCC-gated.
        """
        from sc.hotkey import HotkeyManager, VK
        vk = VK.get(self.hotkey_name, 105)  # default F13 / Print
        self._hotkey_mgr = HotkeyManager()
        self._hotkey_mgr.register(
            vk=vk, modifiers=0,
            on_press=lambda: self.signal_emitter.capture_requested.emit(),
            signature="scrn",
        )

    def _change_hotkey(self):
        """Show dialog to change the hotkey"""
        dlg = HotkeyDialog(self.hotkey_name)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.recorded_key_name:
            self.hotkey_name = dlg.recorded_key_name
            self.config["hotkey_name"] = self.hotkey_name
            save_config(self.config)

            # Update UI
            self._set_hotkey_label(f"Hotkey: {self.hotkey_name}  (click to change)")
            self.tray.setToolTip(f"ScreenCapture - Press {self.hotkey_name} to capture")

            # Restart hotkey listener
            if IS_MACOS and getattr(self, "_hotkey_mgr", None) is not None:
                self._hotkey_mgr.unregister_all()
                self._hotkey_mgr = None
            self._setup_hotkey()

            self.tray.showMessage(
                "ScreenCapture",
                f"Hotkey changed to {self.hotkey_name}",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
    
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
            return

        for idx, name in self._available_cameras:
            action = QAction(name, self._camera_menu)
            action.setCheckable(True)
            action.setChecked(idx == self._camera_index)
            action.triggered.connect(lambda checked, i=idx, n=name: self._select_camera(i, n))
            self._camera_menu.addAction(action)

    def _select_camera(self, index: int, name: str):
        """Select a camera by index."""
        self._camera_index = index
        self.config["camera_index"] = index
        save_config(self.config)
        self.tray.showMessage(
            "ScreenCapture",
            f"Camera set to: {name}",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def _on_tray_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.start_capture()
    
    def start_capture(self):
        """Start the screen capture process, or stop recording if active."""
        if self._is_recording:
            self._stop_recording()
            return

        # Without Screen Recording permission macOS hands back only the
        # desktop wallpaper. Detect that and guide the user instead of
        # silently capturing a useless screenshot.
        if IS_MACOS and not self._ensure_screen_recording():
            return

        if self.overlay:
            self.overlay.close()
            self.overlay = None

        # No 100ms delay: the gap was long enough for macOS to dismiss
        # the menu and reveal the desktop wallpaper, which then got
        # captured instead of the user's actual windows. Grab on the
        # same runloop tick as the trigger.
        self._do_capture()
    
    def _ensure_screen_recording(self) -> bool:
        """Return True if we can capture real screen content. If permission
        is missing, fire the system prompt + guide the user to Settings."""
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
        request_screen_recording_permission()  # one-shot system prompt
        msg = QMessageBox()
        msg.setWindowTitle("Screen Recording permission needed")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "ScreenCapture needs Screen Recording permission to capture your "
            "windows. Without it, screenshots show only the desktop wallpaper.\n\n"
            "Enable ScreenCapture under System Settings → Privacy & Security → "
            "Screen Recording, then quit and reopen the app."
        )
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()
        open_settings_pane("ScreenCapture")
        return False

    def _do_capture(self):
        """Perform capture on the screen under the mouse"""
        try:
            # 1. Find the screen where the mouse is located
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos)
            
            if not screen:
                screen = QGuiApplication.primaryScreen()
            
            # 2. Get Geometry (logical points)
            geo = screen.geometry()

            # 3. Capture the screen region.
            #    Default = mss, which produces screenshots at the familiar
            #    on-screen size. Retina displays have 2x the physical pixels;
            #    capturing all of them (via CGWindowListCreateImage) makes the
            #    image sharper BUT also 2x the pixel dimensions, so it opens
            #    larger. Some users find that "zoomed". It's therefore opt-in
            #    via config "high_res_screenshots": true.
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
                screenshot = capture_region(geo.x(), geo.y(), geo.width(), geo.height())

            # 4. Compute capture DPR (physical pixels / logical pixels)
            capture_dpr = screenshot.width / geo.width() if geo.width() > 0 else 1.0

            # 5. Store for recording use
            self._last_screen_geo = geo
            self._last_capture_dpr = capture_dpr

            # 6. Create overlay
            self.overlay = OverlayWindow(screenshot, geo.x(), geo.y(), capture_dpr)
            self.overlay.selection_cancelled.connect(self._on_overlay_closed)
            self.overlay.image_copied.connect(self._on_overlay_closed)
            self.overlay.image_saved.connect(self._on_overlay_closed)
            self.overlay.recording_requested.connect(self._on_recording_requested)
            
            self.overlay.setGeometry(geo)
            # Realize the underlying NSWindow BEFORE showing so we can
            # mark it CanJoinAllSpaces + non-activating. If the window
            # is shown first with the default collection behavior,
            # macOS switches to the app's "home" Space and shows the
            # desktop there — the bug that plagued the previous build.
            if IS_MACOS:
                _prepare_overlay_window(self.overlay)
            self.overlay.show()
            if IS_MACOS:
                _make_key_without_activating(self.overlay)
            else:
                self.overlay.activateWindow()
            self.overlay.raise_()
            
        except Exception as e:
            QMessageBox.critical(None, "Capture Error", f"Failed to capture screen: {e}")
    
    def _on_overlay_closed(self):
        """Handle overlay close"""
        self.overlay = None

    # --- Recording lifecycle ---

    def _on_recording_requested(self, selection_rect: QRect):
        """Called when user clicks the record button in the overlay."""
        self.overlay = None  # overlay already closed itself

        geo = self._last_screen_geo
        dpr = self._last_capture_dpr

        # Convert logical selection rect to physical mss region
        phys_region = {
            "left": int((geo.x() + selection_rect.x()) * dpr),
            "top": int((geo.y() + selection_rect.y()) * dpr),
            "width": int(selection_rect.width() * dpr),
            "height": int(selection_rect.height() * dpr),
        }
        self._pending_record_region = phys_region
        self._pending_logical_rect = QRect(
            geo.x() + selection_rect.x(),
            geo.y() + selection_rect.y(),
            selection_rect.width(),
            selection_rect.height(),
        )

        # Show countdown on the screen where capture happened
        self._countdown = CountdownOverlay(geo)
        self._countdown.countdown_finished.connect(self._start_recording)
        self._countdown.show()

    def _start_recording(self):
        """Start the actual screen recording after countdown finishes."""
        self._countdown = None
        region = self._pending_record_region
        if not region:
            return

        output_dir = get_recordings_dir()
        output_path = os.path.join(output_dir, generate_filename())

        logical_origin = (self._pending_logical_rect.x(), self._pending_logical_rect.y())
        self._recorder = ScreenRecorder(
            region, output_path,
            dpr=self._last_capture_dpr,
            logical_origin=logical_origin,
            audio_offset_ms=self.config.get("audio_offset_ms", 0),
        )
        self._recorder.recording_stopped.connect(self._on_recording_stopped)
        self._recorder.recording_error.connect(self._on_recording_error)

        # Show recording frame border (draggable via grip handle)
        self._recording_frame = RecordingFrame(self._pending_logical_rect)
        self._recording_frame.region_moved.connect(self._on_region_moved)
        self._recording_frame.show()

        # Create annotation overlay (starts click-through / inactive)
        try:
            self._annotation_overlay = RecordingAnnotationOverlay(self._pending_logical_rect)
            self._annotation_overlay.show()
            self._recorder.set_annotation_overlay(self._annotation_overlay)
            print("[DEBUG] Annotation overlay created OK")
        except Exception as e:
            print(f"[DEBUG] Annotation overlay FAILED: {e}")
            import traceback; traceback.print_exc()

        # Show ScreenPal-style recording toolbar
        try:
            self._toolbar = RecordingToolbar(
                self._last_screen_geo,
                recording_rect=self._pending_logical_rect
            )
            self._toolbar.stop_clicked.connect(self._stop_recording)
            self._toolbar.pause_clicked.connect(self._on_pause_recording)
            self._toolbar.resume_clicked.connect(self._on_resume_recording)
            self._toolbar.mic_toggled.connect(self._on_mic_toggled)
            self._toolbar.webcam_toggled.connect(self._on_webcam_toggled)
            self._toolbar.draw_toggled.connect(self._on_draw_toggled)
            self._toolbar.show()
            print(f"[DEBUG] RecordingToolbar created at {self._toolbar.pos()}, size={self._toolbar.size()}, visible={self._toolbar.isVisible()}")
        except Exception as e:
            print(f"[DEBUG] RecordingToolbar FAILED: {e}")
            import traceback; traceback.print_exc()

        self._is_recording = True
        self._set_stop_visible(True)
        self._recorder.start()
        print("[DEBUG] Recording started")

    # --- Recording control handlers ---

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
        if on:
            # Camera needs explicit TCC permission. OpenCV is told to skip
            # its own auth request (OPENCV_AVFOUNDATION_SKIP_AUTH), so we
            # request via AVFoundation and only open the camera once granted.
            if IS_MACOS:
                from platform_utils import camera_permission_status, request_camera_permission
                status = camera_permission_status()
                if status == "authorized":
                    self._start_webcam()
                elif status == "not_determined":
                    # Async system prompt; result comes back on another
                    # thread, so marshal it to the main thread via a signal.
                    request_camera_permission(
                        lambda g: self.signal_emitter.camera_result.emit(bool(g))
                    )
                else:  # denied / restricted
                    self._show_camera_denied()
            else:
                self._start_webcam()
        else:
            self._stop_webcam()

    def _start_webcam(self):
        """Open the camera and show the circular PiP preview."""
        if self._webcam or not self._recorder:
            return
        self._webcam = WebcamCapture(device_index=self._camera_index)
        self._webcam.start()
        self._webcam_preview = WebcamPreviewWidget(
            self._webcam,
            self._pending_logical_rect,
            dpr=self._last_capture_dpr
        )
        self._webcam_preview.position_changed.connect(self._on_webcam_position)
        self._webcam_preview.show()
        self._recorder.set_webcam(self._webcam)

    def _stop_webcam(self):
        if self._recorder:
            self._recorder.set_webcam(None)
        if self._webcam_preview:
            self._webcam_preview.close()
            self._webcam_preview = None
        if self._webcam:
            self._webcam.stop()
            self._webcam.wait(2000)
            self._webcam = None

    def _on_camera_result(self, granted: bool):
        """Main-thread handler for the async camera permission result."""
        if granted:
            self._start_webcam()
        else:
            self._show_camera_denied()

    def _show_camera_denied(self):
        """Tell the user how to enable the camera and open Settings."""
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
            "Enable it under System Settings → Privacy & Security → Camera, "
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
        """Called when the user drags the red recording border."""
        dpr = self._last_capture_dpr

        # Update the mss capture region (physical pixels)
        new_phys = {
            "left": int(new_rect.x() * dpr),
            "top": int(new_rect.y() * dpr),
            "width": self._recorder.region["width"],   # keep same size
            "height": self._recorder.region["height"],
        }
        self._recorder.region = new_phys
        self._recorder.logical_origin = (new_rect.x(), new_rect.y())
        self._pending_logical_rect = QRect(new_rect)

        # Move annotation overlay to match
        if self._annotation_overlay:
            self._annotation_overlay.setGeometry(new_rect)

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
        else:
            if self._draw_panel:
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
        """Signal the recorder to stop."""
        if self._recorder:
            self._recorder.stop()

    def _on_recording_stopped(self, file_path: str):
        """Called when the recorder finishes saving."""
        self._cleanup_recording()
        # Reveal the new recording in Finder — quiet, professional feedback
        # instead of a notification banner.
        try:
            import subprocess
            if os.path.exists(file_path):
                subprocess.Popen(["open", "-R", file_path])
            else:
                subprocess.Popen(["open", get_recordings_dir()])
        except Exception:
            pass

    def _open_recordings_folder(self):
        """Open the recordings folder in Finder."""
        try:
            import subprocess
            subprocess.Popen(["open", get_recordings_dir()])
        except Exception:
            pass

    def _on_recording_error(self, error_msg: str):
        """Called on recording error."""
        self._cleanup_recording()
        self.tray.showMessage(
            "Recording Error",
            error_msg,
            QSystemTrayIcon.MessageIcon.Critical,
            4000
        )

    def _cleanup_recording(self):
        """Clean up recording state."""
        if self._stop_btn:
            self._stop_btn.close()
            self._stop_btn = None
        if self._toolbar:
            self._toolbar.close()
            self._toolbar = None
        if self._draw_panel:
            self._draw_panel.close()
            self._draw_panel = None
        if self._recording_frame:
            self._recording_frame.close()
            self._recording_frame = None
        if self._annotation_overlay:
            self._annotation_overlay.close()
            self._annotation_overlay = None
        if self._webcam_preview:
            self._webcam_preview.close()
            self._webcam_preview = None
        if self._webcam:
            self._webcam.stop()
            self._webcam.wait(2000)
            self._webcam = None
        self._recorder = None
        self._is_recording = False
        self._pending_record_region = None
        self._pending_logical_rect = None
        self._set_stop_visible(False)

    def _show_about(self):
        """Show about dialog on top of all windows"""
        msg = QMessageBox()
        msg.setWindowTitle("About ScreenCapture")
        msg.setText("ScreenCapture v1.1\n\nA LightShot-like screenshot tool.")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()
    
    def _quit(self):
        """Quit the application"""
        self.tray.hide()
        self.app.quit()
    
    def run(self):
        """Run the application"""
        return self.app.exec()


def show_already_running_notification():
    """Show a notification that the app is already running"""
    # Quick QApplication just to show the notification
    temp_app = QApplication(sys.argv)
    temp_app.setQuitOnLastWindowClosed(False)
    
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
    tray = QSystemTrayIcon()
    if os.path.exists(icon_path):
        tray.setIcon(QIcon(icon_path))
    tray.show()
    tray.showMessage(
        "ScreenCapture",
        "ScreenCapture is already running! Look for the icon in the system tray.",
        QSystemTrayIcon.MessageIcon.Information,
        3000
    )
    # Give time for notification to show
    QTimer.singleShot(3500, temp_app.quit)
    temp_app.exec()


# Global lock socket to prevent garbage collection
_lock_socket = None

def main():
    """Entry point"""
    global _lock_socket
    import socket
    
    # Use socket binding as single-instance lock (very reliable on Windows)
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 47392))  # Arbitrary high port
    except socket.error:
        # Port already in use = another instance is running
        show_already_running_notification()
        sys.exit(0)

    # Ensure Windows recognizes us as DPI Aware (Backup to os.environ)
    if IS_WINDOWS:
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except:
            pass
    
    app = ScreenCaptureApp()

    # No startup notification — the menu-bar icon is enough. Professional
    # menu-bar apps don't toast on launch.
    sys.exit(app.run())


if __name__ == "__main__":
    main()