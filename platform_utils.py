"""Central platform detection and cross-platform helpers.

Import this module instead of re-deriving IS_MACOS / IS_WINDOWS from sys.platform
in every file. Keeps platform-specific constants and the small native shims
(click-through windows, non-activating panels, "reveal in file manager",
physical-vs-logical screen geometry) in one place.

Windows notes (the parts that cost time to get right):
- Qt6 runs per-monitor DPI aware. QScreen.geometry() is LOGICAL (scaled),
  mss works in PHYSICAL pixels. Each screen's logical top-left equals its
  physical top-left, only the size differs by devicePixelRatio(). See
  physical_screen_rect().
- A frameless top-level widget is only click-through on Windows if the native
  window carries WS_EX_TRANSPARENT (plus WS_EX_LAYERED, which Qt sets for
  WA_TranslucentBackground). Qt's WA_TransparentForMouseEvents is not enough
  for top-level windows. See set_click_through().
- Floating control bars must NOT steal focus from the app being recorded:
  WS_EX_NOACTIVATE (Qt: WindowDoesNotAcceptFocus) does that. See
  make_non_activating().
"""
import os
import sys

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

SYSTEM_FONT = ".AppleSystemUIFont" if IS_MACOS else ("Segoe UI" if IS_WINDOWS else "Sans Serif")

# Brand colors shared by every surface (overlay toolbars, tray menu, dialogs).
BRAND_PURPLE = "#7A1FA6"
BRAND_PURPLE_SOFT = "#EFE8FB"
INK = "#1D1D1F"
INK_SOFT = "#3C3C43"
SUBTLE = "#6E6E73"
CARD_BORDER = "#E5E5EA"
RECORD_RED = "#E63946"


def modifier_key():
    """Return Qt modifier for the platform's standard shortcut prefix (Cmd on mac, Ctrl elsewhere)."""
    from PyQt6.QtCore import Qt
    return Qt.KeyboardModifier.MetaModifier if IS_MACOS else Qt.KeyboardModifier.ControlModifier


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def _known_folder_windows(folder_id_guid, fallback):
    """Resolve a Windows known folder (Videos, Pictures) via SHGetKnownFolderPath.

    Users who redirect their libraries (OneDrive "Known Folder Move" is common
    on corporate laptops) do not have ~/Videos at all; the shell API returns
    the real location.
    """
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        u = uuid.UUID(folder_id_guid)
        g = GUID()
        g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        path_ptr = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(path_ptr))
        if res == 0 and path_ptr.value:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return fallback


def recordings_dir():
    """Per-platform user-writable directory for recorded videos."""
    if IS_MACOS:
        base = os.path.expanduser("~/Movies/ScreenCapture")
    elif IS_WINDOWS:
        videos = _known_folder_windows("18989B1D-99B5-455B-841C-AB7C74E4DDFC",
                                       os.path.expanduser("~/Videos"))
        base = os.path.join(videos, "ScreenCapture")
    else:
        base = os.path.expanduser("~/Videos/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def screenshots_dir():
    """Default folder offered by the Save dialog for screenshots."""
    if IS_MACOS:
        base = os.path.expanduser("~/Pictures/ScreenCapture")
    elif IS_WINDOWS:
        pictures = _known_folder_windows("33E28130-4E1E-4676-835A-98395C3BC3BB",
                                         os.path.expanduser("~/Pictures"))
        base = os.path.join(pictures, "ScreenCapture")
    else:
        base = os.path.expanduser("~/Pictures/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def open_folder(path):
    """Open a folder in Finder / Explorer / the desktop file manager."""
    try:
        import subprocess
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - user-initiated, local path
        elif IS_MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def reveal_in_file_manager(path):
    """Show a file selected in Finder / Explorer. Falls back to opening its folder."""
    try:
        import subprocess
        if not os.path.exists(path):
            open_folder(os.path.dirname(path) or ".")
            return
        if IS_WINDOWS:
            # explorer wants backslashes and the /select, switch glued to the path
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", path])
        else:
            open_folder(os.path.dirname(path))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Screen geometry (logical <-> physical)
# ---------------------------------------------------------------------------

def physical_screen_rect(qscreen):
    """Return (left, top, width, height) of a QScreen in PHYSICAL pixels.

    Qt reports logical (DPI-scaled) geometry; mss captures physical pixels.
    On Windows each screen's logical origin is the same point as its physical
    origin, so we match the mss monitor whose top-left equals the QScreen's
    top-left and take its physical size. Falls back to scaling the logical
    size by devicePixelRatio() when no monitor matches (single screen, or a
    platform where mss and Qt disagree on origins).
    """
    geo = qscreen.geometry()
    dpr = float(qscreen.devicePixelRatio() or 1.0)
    fallback = (geo.x(), geo.y(), int(round(geo.width() * dpr)), int(round(geo.height() * dpr)))
    try:
        import mss
        with mss.mss() as sct:
            for mon in sct.monitors[1:]:
                if mon["left"] == geo.x() and mon["top"] == geo.y():
                    return (mon["left"], mon["top"], mon["width"], mon["height"])
    except Exception:
        pass
    return fallback


def cursor_pos_physical():
    """Mouse position in physical screen pixels (None if unavailable)."""
    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return (int(pt.x), int(pt.y))
        except Exception:
            return None
    if IS_MACOS:
        try:
            from AppKit import NSEvent, NSScreen
            pos = NSEvent.mouseLocation()
            main_h = NSScreen.mainScreen().frame().size.height
            return (int(pos.x), int(main_h - pos.y))
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Window behaviour shims
# ---------------------------------------------------------------------------

def set_click_through(widget, enabled=True):
    """Make a top-level widget pass mouse events to whatever is beneath it.

    Windows: toggles WS_EX_TRANSPARENT on the native window (requires a
    layered window, which WA_TranslucentBackground already gives us).
    macOS: NSWindow.setIgnoresMouseEvents_. No-op elsewhere.
    Safe to call before or after show(); call again after show() to be sure
    the native handle existed.
    """
    if IS_WINDOWS:
        try:
            import ctypes
            hwnd = int(widget.winId())
            if not hwnd:
                return
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED
            if enabled:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception as e:
            print(f"set_click_through: {e}")
    elif IS_MACOS:
        configure_nswindow(widget, click_through=enabled, level=25)


def make_non_activating(widget):
    """Keep a floating panel from stealing keyboard focus when clicked.

    Must be called BEFORE the widget is shown (it changes window flags).
    Windows: WS_EX_NOACTIVATE via Qt's WindowDoesNotAcceptFocus flag.
    macOS: handled by the NSPanel non-activating mask in the recorder helpers.
    """
    if IS_WINDOWS:
        try:
            from PyQt6.QtCore import Qt
            widget.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        except Exception as e:
            print(f"make_non_activating: {e}")


def force_foreground(widget):
    """Windows: bring a just-shown window to the foreground and give it the
    keyboard, even when the trigger was not user input in OUR process.

    Windows refuses SetForegroundWindow to a process that did not receive the
    last input (the "foreground lock"): a capture started from the tray menu
    or the hotkey is fine, but one started by `main.py --capture` (another
    process) would open the overlay WITHOUT keyboard focus, so Esc / Enter
    did nothing. Attaching our input queue to the current foreground thread
    for the duration of the call is the documented workaround. No-op elsewhere.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(widget.winId())
        if not hwnd:
            return
        fg = user32.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception as e:
        print(f"force_foreground: {e}")


def set_startup_enabled(enabled, target_exe, arguments, working_dir, icon_path=None,
                        shortcut_name="ScreenCapture"):
    """Windows: create/remove a Startup-folder shortcut so the app auto-starts
    at login. Returns True on success. No-op (False) on other platforms."""
    if not IS_WINDOWS:
        return False
    try:
        startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                               "Start Menu", "Programs", "Startup")
        link = os.path.join(startup, f"{shortcut_name}.lnk")
        if not enabled:
            if os.path.exists(link):
                os.remove(link)
            return True
        from win32com.client import Dispatch  # pywin32
        shell = Dispatch("WScript.Shell")
        sc = shell.CreateShortCut(link)
        sc.Targetpath = target_exe
        sc.Arguments = arguments
        sc.WorkingDirectory = working_dir
        sc.Description = "ScreenCapture - auto start"
        if icon_path and os.path.exists(icon_path):
            sc.IconLocation = icon_path
        sc.save()
        return True
    except Exception as e:
        print(f"set_startup_enabled: {e}")
        return False


def startup_enabled(shortcut_name="ScreenCapture"):
    """Windows: is there a Startup-folder shortcut for the app?"""
    if not IS_WINDOWS:
        return False
    startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
    return os.path.exists(os.path.join(startup, f"{shortcut_name}.lnk"))


# ---------------------------------------------------------------------------
# macOS permission helpers (no-ops elsewhere)
# ---------------------------------------------------------------------------

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
    - 'authorized' -> use the camera, no prompts
    - 'not_determined' -> call request_camera_permission to show OS prompt
    - 'denied' / 'restricted' -> don't prompt; point user to Settings
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

    The system prompt only fires once per app - afterwards the user must
    flip the toggle in System Settings > Privacy & Security > Camera.
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
    """Prompt the user to grant Accessibility permission (macOS only)."""
    if not IS_MACOS:
        return
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
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
    """Fire every macOS TCC prompt the app needs in one sweep."""
    if not IS_MACOS:
        return
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeVideo, lambda granted: None
        )
    except Exception:
        pass
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: None
        )
    except Exception:
        pass
    request_screen_recording_permission()
    if not has_accessibility_permission():
        request_accessibility_permission()


def configure_nswindow(widget, click_through=False, level=25,
                       can_join_all_spaces=True):
    """Configure a Qt widget's underlying NSWindow on macOS.

    - can_join_all_spaces=True keeps the window visible in every Space and
      prevents macOS from switching to the app's "home" Space when the
      widget is shown.
    - click_through makes the window pass mouse events to whatever is
      beneath it (used for the recording border / annotation overlay).
    - level controls window stacking; 25 sits above normal windows but
      below alerts and the menu bar.

    No-op on non-macOS. Call BEFORE widget.show() to prevent the initial
    Space switch - see prepare_for_current_space() below.
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
    """Set NSWindow flags BEFORE the first show() - prevents Space switch.

    Calling create() forces Qt to instantiate the underlying NSView and
    NSWindow without showing them, so we can set the right collection
    behavior FIRST, then call show(). No-op on non-macOS.
    """
    if not IS_MACOS:
        return
    try:
        widget.create()
    except Exception:
        try:
            _ = int(widget.winId())
        except Exception:
            pass
    configure_nswindow(widget, click_through=click_through, level=level,
                       can_join_all_spaces=True)


def copy_image_to_clipboard(qimage):
    """Cross-platform clipboard copy of a QImage.

    On macOS, uses NSPasteboard with PNG data (better app compatibility).
    On Windows/Linux, uses Qt's clipboard (Qt publishes PNG + DIB, so Teams,
    Outlook, Word and Paint all paste it).
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
