"""Native app controller — wires tray + hotkey + capture + overlay.

This is the single entry point for the macOS build. It is pure PyObjC:
no Qt, no Spaces dance, no DPI gymnastics. The flow is

    hotkey / menu click
        -> capture.screen_at_cursor()        (which screen is the mouse on)
        -> capture.grab(frame)               (CGWindowListCreateImage, sync)
        -> overlay.show(image, frame)        (non-activating NSPanel)
        -> Cmd+C copy / Cmd+S save / Esc cancel

The capture happens on the SAME runloop tick as the trigger and BEFORE
any window of ours appears, so macOS never switches Spaces and never
reveals the desktop wallpaper — the bug that plagued the Qt build.
"""
import socket
import sys

from AppKit import (
    NSApplication, NSApp, NSAlert, NSAlertFirstButtonReturn,
    NSAlertSecondButtonReturn, NSImage,
)

from . import tray, capture, overlay, permissions, config
from .hotkey import HotkeyManager

# Accessory activation policy: menu-bar-only, no Dock icon, no app switch
# on activation. PyObjC exposes the constant but fall back to its raw
# value (1) if a stripped build is missing it.
try:
    from AppKit import NSApplicationActivationPolicyAccessory as _ACCESSORY
except Exception:  # pragma: no cover
    _ACCESSORY = 1

# Single-instance lock port (arbitrary high port). Held for process life.
_LOCK_PORT = 47392
_lock_socket = None       # module-level: never GC the lock
_controller = None        # module-level: never GC the controller / target


class AppController:
    """Owns the tray, the hotkey registration, and the capture flow."""

    def __init__(self):
        self.cfg = config.load()
        self.hotkeys = HotkeyManager()
        self._build_tray()
        self._register_hotkey()

    # -- setup ------------------------------------------------------------

    def _build_tray(self):
        label = self.cfg.get("hotkey_label", "F13")
        items = [
            (f"Capture Screen   ({label})", self.capture),
            ("-", None),
            ("Open Screenshots Folder", self.open_screenshots_folder),
            ("Screen Recording Permission…",
             lambda: permissions.open_settings("ScreenRecording")),
            ("-", None),
            ("About ScreenCapture", self.about),
            ("Quit ScreenCapture", self.quit),
        ]
        tray.install(items)

    def _register_hotkey(self):
        vk = int(self.cfg.get("hotkey_vk", 105))            # F13 / PrintScreen
        mods = int(self.cfg.get("hotkey_modifiers", 0))
        try:
            self.hotkeys.register(
                vk=vk, modifiers=mods,
                on_press=self.capture, signature="scrn",
            )
        except Exception as e:
            # Most common cause: the key is already claimed system-wide.
            print(f"[hotkey] registration failed: {e}", file=sys.stderr)

    # -- capture flow -----------------------------------------------------

    def capture(self):
        """Grab the screen under the cursor and open the selection overlay."""
        if not permissions.has_screen_recording():
            self._prompt_screen_recording()
            return
        try:
            _ns_screen, frame = capture.screen_at_cursor()
            image = capture.grab(frame)
        except Exception as e:
            print(f"[capture] failed: {e}", file=sys.stderr)
            return
        overlay.show(image, frame, on_close=None)

    # -- menu actions -----------------------------------------------------

    def open_screenshots_folder(self):
        from .paths import screenshots_dir
        tray.show_recordings_folder(screenshots_dir())

    def about(self):
        self._activate()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("ScreenCapture")
        alert.setInformativeText_(
            "A fast, native screenshot tool.\n\n"
            f"Press {self.cfg.get('hotkey_label', 'F13')} anywhere to capture "
            "the screen under your cursor, drag to select, then ⌘C to copy "
            "or ⌘S to save."
        )
        icon = _app_icon()
        if icon is not None:
            alert.setIcon_(icon)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def quit(self):
        try:
            self.hotkeys.unregister_all()
        except Exception:
            pass
        NSApp.terminate_(None)

    # -- permission UX ----------------------------------------------------

    def _prompt_screen_recording(self):
        """Fire the system prompt (first run) and guide the user to Settings."""
        permissions.request_screen_recording()
        self._activate()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Screen Recording permission needed")
        alert.setInformativeText_(
            "macOS requires Screen Recording permission to capture other "
            "apps' windows. Without it, screenshots show only the desktop "
            "wallpaper.\n\n"
            "Enable ScreenCapture under Privacy & Security → Screen "
            "Recording, then quit and reopen the app."
        )
        alert.addButtonWithTitle_("Open Settings")
        alert.addButtonWithTitle_("Later")
        if alert.runModal() == NSAlertFirstButtonReturn:
            permissions.open_settings("ScreenRecording")

    # -- helpers ----------------------------------------------------------

    def _activate(self):
        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass


def _app_icon():
    path = tray._icon_path()
    if not path:
        return None
    return NSImage.alloc().initWithContentsOfFile_(path)


def main():
    """Process entry point for the native macOS app."""
    global _lock_socket, _controller

    # Single-instance guard: bind a localhost port. If it's taken, another
    # instance already owns the menu bar — just exit quietly.
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_socket.bind(("127.0.0.1", _LOCK_PORT))
    except OSError:
        print("ScreenCapture is already running.", file=sys.stderr)
        return 0

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(_ACCESSORY)   # menu-bar-only, no Dock icon

    _controller = AppController()

    app.run()
    return 0
