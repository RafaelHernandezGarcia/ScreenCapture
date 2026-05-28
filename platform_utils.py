"""Central platform detection and cross-platform helpers.

Import this module instead of re-deriving IS_MACOS / IS_WINDOWS from sys.platform
in every file. Keeps platform-specific constants in one place.
"""
import os
import sys

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

SYSTEM_FONT = ".AppleSystemUIFont" if IS_MACOS else ("Segoe UI" if IS_WINDOWS else "Sans Serif")


def modifier_key():
    """Return Qt modifier for the platform's standard shortcut prefix (Cmd on mac, Ctrl elsewhere)."""
    from PyQt6.QtCore import Qt
    return Qt.KeyboardModifier.MetaModifier if IS_MACOS else Qt.KeyboardModifier.ControlModifier


def recordings_dir():
    """Per-platform user-writable directory for recorded videos."""
    if IS_MACOS:
        base = os.path.expanduser("~/Movies/ScreenCapture")
    elif IS_WINDOWS:
        base = os.path.expanduser("~/Videos/ScreenCapture")
    else:
        base = os.path.expanduser("~/Videos/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def has_screen_recording_permission():
    """Best-effort check for screen recording permission.

    macOS: uses CoreGraphics CGPreflightScreenCaptureAccess (10.15+).
    Returns True on other OSes (no equivalent gate).
    """
    if not IS_MACOS:
        return True
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        if hasattr(cg, "CGPreflightScreenCaptureAccess"):
            cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
            return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        pass
    return True


def has_camera_permission():
    """Check macOS Camera (AVFoundation) permission. True on other OSes."""
    if not IS_MACOS:
        return True
    return camera_permission_status() == "authorized"


def camera_permission_status():
    """Return one of: 'authorized', 'denied', 'restricted', 'not_determined'.

    Lets callers branch:
    - 'authorized' → use the camera, no prompts
    - 'not_determined' → call request_camera_permission to show OS prompt
    - 'denied' / 'restricted' → don't prompt; point user to Settings
    Other OSes return 'authorized'.
    """
    if not IS_MACOS:
        return "authorized"
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo)
        # 0=NotDetermined 1=Restricted 2=Denied 3=Authorized
        return {0: "not_determined", 1: "restricted",
                2: "denied", 3: "authorized"}.get(int(status), "authorized")
    except Exception:
        return "authorized"


def request_camera_permission(callback=None):
    """Trigger the macOS Camera permission prompt (no-op elsewhere).

    The system prompt only fires once per app — afterwards the user must
    flip the toggle in System Settings → Privacy & Security → Camera.
    """
    if not IS_MACOS:
        if callback:
            callback(True)
        return
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
        def _cb(granted):
            if callback:
                callback(bool(granted))
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeVideo, _cb
        )
    except Exception:
        if callback:
            callback(False)


def has_accessibility_permission():
    """Best-effort check for macOS Accessibility (required by pynput global hotkeys)."""
    if not IS_MACOS:
        return True
    try:
        import ctypes
        import ctypes.util
        ax = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    except Exception:
        return True  # fail-open so we don't block non-macOS launches


def request_accessibility_permission():
    """Prompt the user to grant Accessibility permission (macOS only).

    Uses AXIsProcessTrustedWithOptions via PyObjC to trigger the system
    prompt that pushes the user into Settings with the right app checked.
    """
    if not IS_MACOS:
        return
    try:
        # HIServices.framework exposes AXIsProcessTrustedWithOptions
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        # Fall back: open System Settings directly
        try:
            import subprocess
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            ])
        except Exception:
            pass


def request_screen_recording_permission():
    """Trigger the macOS permission prompt (no-op on other OSes)."""
    if not IS_MACOS:
        return
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        if hasattr(cg, "CGRequestScreenCaptureAccess"):
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            cg.CGRequestScreenCaptureAccess()
    except Exception:
        pass


def open_settings_pane(pane: str):
    """Open a specific Privacy & Security pane in System Settings (macOS).

    pane: "ScreenCapture", "Microphone", "Camera", "Accessibility"
    """
    if not IS_MACOS:
        return
    urls = {
        "ScreenCapture": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "Microphone":    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "Camera":        "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
        "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    }
    url = urls.get(pane)
    if not url:
        return
    try:
        import subprocess
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


def trigger_all_permission_prompts():
    """Fire every macOS TCC prompt the app needs in one sweep, so the user
    can deal with permissions in a single sitting instead of one at a time."""
    if not IS_MACOS:
        return
    # Camera (async — system shows prompt if NSCameraUsageDescription is set)
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeVideo, lambda granted: None
        )
    except Exception:
        pass
    # Microphone
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: None
        )
    except Exception:
        pass
    # Screen Recording
    request_screen_recording_permission()
    # Accessibility
    if not has_accessibility_permission():
        request_accessibility_permission()


def configure_nswindow(widget, click_through=False, level=25,
                       can_join_all_spaces=True):
    """Configure a Qt widget's underlying NSWindow on macOS.

    - can_join_all_spaces=True keeps the window visible in every Space and
      prevents macOS from switching to the app's "home" Space when the
      widget is shown. THIS is the fix for the bug where pressing the
      hotkey would jump the user to the desktop.
    - click_through makes the window pass mouse events to whatever is
      beneath it (used for the recording border / annotation overlay).
    - level controls window stacking; 25 sits above normal windows but
      below alerts and the menu bar.

    No-op on non-macOS.

    IMPORTANT: this MUST be called BEFORE widget.show() to prevent the
    initial Space switch — see prepare_for_current_space() below.
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
        )
        nsview_ptr = int(widget.winId())
        if nsview_ptr == 0:
            return
        nsview = objc.objc_object(c_void_p=_ct.c_void_p(nsview_ptr))
        nswindow = nsview.window()
        if nswindow is None:
            return
        nswindow.setIgnoresMouseEvents_(click_through)
        nswindow.setLevel_(level)
        nswindow.setHidesOnDeactivate_(False)
        if can_join_all_spaces:
            # CanJoinAllSpaces: window appears on every Space, so macOS
            #   never has to switch Spaces to show it.
            # Stationary: window doesn't get pulled along during
            #   Mission Control / Spaces transitions.
            # IgnoresCycle: excluded from Cmd+Tab / Cmd+`.
            # FullScreenAuxiliary: also visible over fullscreen apps.
            # Transient: tells macOS this is a short-lived window — no
            #   activation, no Spaces dance.
            nswindow.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorIgnoresCycle
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorTransient
            )
    except Exception as e:
        print(f"configure_nswindow: {e}")


def prepare_for_current_space(widget, click_through=False, level=25):
    """Set NSWindow flags BEFORE the first show() — prevents Space switch.

    The previous bug: configure_nswindow was called from showEvent, which
    runs AFTER Qt has already called [NSWindow makeKeyAndOrderFront:].
    With the default collection behavior at that moment, macOS switches
    to the window's "home" Space, revealing the desktop wallpaper of
    that Space instead of where the user was.

    Calling create() forces Qt to instantiate the underlying NSView and
    NSWindow without showing them, so we can set the right collection
    behavior FIRST, then call show().

    Use this on every overlay/popup created during a capture or
    recording. No-op on non-macOS.
    """
    if not IS_MACOS:
        return
    try:
        widget.create()  # realize the underlying NSView/NSWindow
    except Exception:
        # Fall back to winId() which also forces realization
        try:
            _ = int(widget.winId())
        except Exception:
            pass
    configure_nswindow(widget, click_through=click_through, level=level,
                       can_join_all_spaces=True)


def copy_image_to_clipboard(qimage):
    """Cross-platform clipboard copy of a QImage.

    On macOS, uses NSPasteboard with PNG data (better app compatibility).
    On Windows/Linux, uses Qt's clipboard.
    """
    from PyQt6.QtWidgets import QApplication

    if IS_MACOS:
        try:
            from AppKit import NSPasteboard, NSPasteboardTypePNG
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            qimage.save(buf, "PNG")
            buf.close()

            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setData_forType_(bytes(ba.data()), NSPasteboardTypePNG)
            return True
        except Exception:
            pass

    QApplication.clipboard().setImage(qimage)
    return True
