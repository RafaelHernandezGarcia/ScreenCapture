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
    except Exception:
        pass

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


class ScreenCaptureApp:
    """Main application class managing the screenshot tool"""
    
    def __init__(self):
        # Prevent any leftover scaling attributes (Windows only)
        if IS_WINDOWS and hasattr(Qt.ApplicationAttribute, 'AA_DisableHighDpiScaling'):
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_DisableHighDpiScaling, True)

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

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

        self._setup_tray()
        self._setup_hotkey()
    
    def _setup_tray(self):
        """Set up the system tray icon and menu"""
        self.tray = QSystemTrayIcon()
        
        # Try to load icon — use smaller tray icon on macOS if available
        base_dir = os.path.dirname(__file__)
        tray_icon_path = os.path.join(base_dir, "assets", "icon_tray.png")
        icon_path = os.path.join(base_dir, "assets", "icon.png")
        if IS_MACOS and os.path.exists(tray_icon_path):
            self.tray.setIcon(QIcon(tray_icon_path))
        elif os.path.exists(icon_path):
            self.tray.setIcon(QIcon(icon_path))
        else:
            self.tray.setIcon(self.app.style().standardIcon(
                self.app.style().StandardPixmap.SP_ComputerIcon
            ))
        
        self.tray.setToolTip(f"ScreenCapture - Press {self.hotkey_name} to capture")

        # Create context menu
        menu = QMenu()

        capture_action = QAction("Capture Screen", menu)
        capture_action.triggered.connect(self.start_capture)
        menu.addAction(capture_action)

        self._stop_recording_action = QAction("Stop Recording", menu)
        self._stop_recording_action.triggered.connect(self._stop_recording)
        self._stop_recording_action.setVisible(False)
        menu.addAction(self._stop_recording_action)

        menu.addSeparator()

        self.hotkey_menu_action = QAction(f"Hotkey: {self.hotkey_name}  (click to change)", menu)
        self.hotkey_menu_action.triggered.connect(self._change_hotkey)
        menu.addAction(self.hotkey_menu_action)

        # Camera selection submenu
        self._camera_menu = QMenu("Camera", menu)
        self._camera_menu.aboutToShow.connect(self._populate_camera_menu)
        menu.addMenu(self._camera_menu)

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
        """Register hotkey using pynput on macOS"""
        from pynput import keyboard
        from pynput.keyboard import KeyCode

        # Map Qt key name to pynput Key (use getattr to avoid crashes on macOS
        # where some keys like print_screen don't exist)
        PYNPUT_KEY_MAP = {}
        _key_attrs = {
            "Print": "print_screen",
            "F1": "f1", "F2": "f2", "F3": "f3",
            "F4": "f4", "F5": "f5", "F6": "f6",
            "F7": "f7", "F8": "f8", "F9": "f9",
            "F10": "f10", "F11": "f11", "F12": "f12",
            "F13": "f13", "F14": "f14", "F15": "f15", "F16": "f16",
            "F17": "f17", "F18": "f18", "F19": "f19", "F20": "f20",
            "Pause": "pause", "Scroll Lock": "scroll_lock",
            "Insert": "insert", "Home": "home",
        }
        for name, attr in _key_attrs.items():
            k = getattr(keyboard.Key, attr, None)
            if k is not None:
                PYNPUT_KEY_MAP[name] = k

        # F13-F20 fallback raw virtual key codes (in case pynput doesn't have them as Key enums)
        MAC_FN_KEYCODES = {
            "F13": 105, "F14": 107, "F15": 113, "F16": 106,
            "F17": 64, "F18": 79, "F19": 80, "F20": 90,
        }

        target_key = PYNPUT_KEY_MAP.get(self.hotkey_name)
        mac_vk = MAC_FN_KEYCODES.get(self.hotkey_name)

        if target_key and mac_vk:
            def on_press(key):
                if key == target_key:
                    self.signal_emitter.capture_requested.emit()
                elif isinstance(key, KeyCode) and hasattr(key, 'vk') and key.vk == mac_vk:
                    self.signal_emitter.capture_requested.emit()
        elif target_key:
            def on_press(key):
                if key == target_key:
                    self.signal_emitter.capture_requested.emit()
        elif mac_vk:
            def on_press(key):
                if isinstance(key, KeyCode) and hasattr(key, 'vk') and key.vk == mac_vk:
                    self.signal_emitter.capture_requested.emit()
        else:
            target_char = self.hotkey_name.lower()
            def on_press(key):
                if hasattr(key, 'char') and key.char == target_char:
                    self.signal_emitter.capture_requested.emit()

        self._hotkey_listener = keyboard.Listener(on_press=on_press)
        self._hotkey_listener.daemon = True
        # Suppress macOS "This process is not trusted!" message from IOKit.
        # The message is printed to fd 2 from the listener's background
        # thread, so we redirect fd 2 and hold it for a moment.
        _saved_fd = os.dup(2)
        _devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_devnull, 2)
        self._hotkey_listener.start()
        import time as _time
        _time.sleep(0.3)  # let the bg thread print its message to /dev/null
        os.dup2(_saved_fd, 2)
        os.close(_saved_fd)
        os.close(_devnull)

    def _change_hotkey(self):
        """Show dialog to change the hotkey"""
        dlg = HotkeyDialog(self.hotkey_name)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.recorded_key_name:
            self.hotkey_name = dlg.recorded_key_name
            self.config["hotkey_name"] = self.hotkey_name
            save_config(self.config)

            # Update UI
            self.hotkey_menu_action.setText(f"Hotkey: {self.hotkey_name}  (click to change)")
            self.tray.setToolTip(f"ScreenCapture - Press {self.hotkey_name} to capture")

            # Restart hotkey listener
            if IS_MACOS and self._hotkey_listener:
                self._hotkey_listener.stop()
                self._hotkey_listener = None
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

        if self.overlay:
            self.overlay.close()
            self.overlay = None

        QTimer.singleShot(100, self._do_capture)
    
    def _do_capture(self):
        """Perform capture on the screen under the mouse"""
        try:
            # 1. Find the screen where the mouse is located
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos)
            
            if not screen:
                screen = QGuiApplication.primaryScreen()
            
            # 2. Get Geometry (Now guaranteed to be Physical Pixels due to os.environ fix)
            geo = screen.geometry()
            
            # 3. Capture region (mss returns physical pixels on Retina)
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
            self.overlay.show()
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
        self._stop_recording_action.setVisible(True)
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
        else:
            self._recorder.set_webcam(None)
            if self._webcam_preview:
                self._webcam_preview.close()
                self._webcam_preview = None
            if self._webcam:
                self._webcam.stop()
                self._webcam.wait(2000)
                self._webcam = None

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
        self.tray.showMessage(
            "Recording Saved",
            f"Saved to {file_path}",
            QSystemTrayIcon.MessageIcon.Information,
            4000
        )

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
        self._stop_recording_action.setVisible(False)

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
    
    app.tray.showMessage(
        "ScreenCapture",
        f"Ready! Press {app.hotkey_name} to capture.",
        QSystemTrayIcon.MessageIcon.Information,
        3000
    )
    
    sys.exit(app.run())


if __name__ == "__main__":
    main()