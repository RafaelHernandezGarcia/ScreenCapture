"""Process-global hotkeys via Carbon RegisterEventHotKey.

Why Carbon and not pynput / NSEvent global monitor:
- pynput -> CGEventTap -> requires Accessibility permission, which TCC
  invalidates on every app rebuild (cdhash changes).
- NSEvent.addGlobalMonitor -> also requires Accessibility for
  non-modifier keys (F13 etc).
- RegisterEventHotKey -> process-scoped registration with the WindowServer.
  Zero permission needed. Survives rebuilds. Works on every Space.
  This is what Shottr / CleanShot X / iTerm2 / Spectacle all use.

Apple marks the API "deprecated" in macOS 11 docs but it still works on
macOS 15+. There is no replacement for one-shot key registration.

Carbon dispatches the handler ON THE MAIN THREAD via the AppKit run
loop, so callbacks can touch UI directly without thread-marshalling.
"""
import ctypes
from ctypes import (
    c_int32, c_uint32, c_size_t, c_void_p,
    byref, Structure, POINTER, CFUNCTYPE,
)

_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(s: str) -> int:
    """4-char string -> big-endian uint32 (OSType)."""
    s = (s + "    ")[:4]
    return int.from_bytes(s.encode("ascii"), "big")


# Carbon constants
_kEventClassKeyboard      = _fourcc("keyb")
_kEventHotKeyPressed      = 5
_kEventParamDirectObject  = _fourcc("----")
_typeEventHotKeyID        = _fourcc("hkid")

# Modifier flag bits (Carbon Events.h)
CMD_KEY     = 1 << 8     # 0x0100
SHIFT_KEY   = 1 << 9     # 0x0200
OPTION_KEY  = 1 << 11    # 0x0800
CONTROL_KEY = 1 << 12    # 0x1000


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


_HandlerProc = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)

# Function prototypes - must be set before any call so ctypes does the
# right thing with pointer/integer widths on arm64.
_carbon.GetApplicationEventTarget.restype = c_void_p
_carbon.GetApplicationEventTarget.argtypes = []

_carbon.InstallEventHandler.restype = c_int32
_carbon.InstallEventHandler.argtypes = [
    c_void_p, _HandlerProc, c_uint32,
    POINTER(_EventTypeSpec), c_void_p, POINTER(c_void_p),
]

_carbon.RegisterEventHotKey.restype = c_int32
_carbon.RegisterEventHotKey.argtypes = [
    c_uint32, c_uint32, _EventHotKeyID,
    c_void_p, c_uint32, POINTER(c_void_p),
]

_carbon.UnregisterEventHotKey.restype = c_int32
_carbon.UnregisterEventHotKey.argtypes = [c_void_p]

_carbon.GetEventParameter.restype = c_int32
_carbon.GetEventParameter.argtypes = [
    c_void_p, c_uint32, c_uint32,
    POINTER(c_uint32),  # outActualType (may be NULL)
    c_size_t,           # inBufferSize
    POINTER(c_size_t),  # outActualSize (may be NULL)
    c_void_p,           # outData
]


class HotkeyManager:
    """Manage one or more process-global hotkeys.

    The handler must be retained for the lifetime of the app - Carbon
    keeps the function pointer but the Python proxy will be garbage
    collected if you don't.
    """

    def __init__(self):
        self._handler_ref = c_void_p()
        self._proc = _HandlerProc(self._dispatch)  # retained
        self._installed = False
        self._next_id = 1
        self._hotkeys = {}  # id -> (callback, hotkey_ref)

    def _install(self):
        if self._installed:
            return
        spec = _EventTypeSpec(_kEventClassKeyboard, _kEventHotKeyPressed)
        rc = _carbon.InstallEventHandler(
            _carbon.GetApplicationEventTarget(),
            self._proc, 1, byref(spec), None, byref(self._handler_ref),
        )
        if rc != 0:
            raise RuntimeError(f"InstallEventHandler failed: {rc}")
        self._installed = True

    def register(self, vk: int, modifiers: int = 0, *, on_press=None,
                 signature: str = "scrn") -> int:
        """Register a hotkey. Returns an opaque id for unregister().

        vk: Carbon virtual key code. F13 = 105, F14 = 107, etc.
        modifiers: bitwise OR of CMD_KEY/SHIFT_KEY/OPTION_KEY/CONTROL_KEY.
        on_press: zero-arg callable, runs on main thread.
        """
        self._install()
        hk_id = self._next_id
        self._next_id += 1
        ehk_id = _EventHotKeyID(_fourcc(signature), hk_id)
        ref = c_void_p()
        rc = _carbon.RegisterEventHotKey(
            vk, modifiers, ehk_id,
            _carbon.GetApplicationEventTarget(), 0, byref(ref),
        )
        if rc != 0:
            raise RuntimeError(
                f"RegisterEventHotKey vk={vk} mods={modifiers:#x} "
                f"failed (rc={rc}). Likely already taken by another app."
            )
        self._hotkeys[hk_id] = (on_press, ref)
        return hk_id

    def unregister(self, hk_id: int):
        cb, ref = self._hotkeys.pop(hk_id, (None, None))
        if ref is not None:
            _carbon.UnregisterEventHotKey(ref)

    def unregister_all(self):
        for hk_id in list(self._hotkeys):
            self.unregister(hk_id)

    def _dispatch(self, _next_handler, event, _user_data):
        out = _EventHotKeyID()
        actual_size = c_size_t()
        rc = _carbon.GetEventParameter(
            event,
            _kEventParamDirectObject,
            _typeEventHotKeyID,
            None,
            ctypes.sizeof(_EventHotKeyID),
            byref(actual_size),
            ctypes.cast(byref(out), c_void_p),
        )
        if rc == 0:
            entry = self._hotkeys.get(out.id)
            if entry and entry[0] is not None:
                try:
                    entry[0]()
                except Exception as e:
                    # Never let a callback crash kill the runloop
                    import traceback
                    traceback.print_exc()
        return 0  # noErr


# Mac virtual key codes (Events.h kVK_*), keyed by the UPPERCASE names that
# main.parse_hotkey produces - the same vocabulary as the Windows table, so a
# shortcut recorded in the dialog (Cmd+Shift+S, F13, Home...) maps 1:1.
VK = {
    # letters (ANSI layout)
    "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8,
    "V": 9, "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15, "Y": 16, "T": 17,
    "O": 31, "U": 32, "I": 34, "P": 35, "L": 37, "J": 38, "K": 40, "N": 45,
    "M": 46,
    # digits
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28,
    "9": 25, "0": 29,
    # editing / navigation
    "RETURN": 36, "ENTER": 36, "TAB": 48, "SPACE": 49, "BACKSPACE": 51,
    "INSERT": 114, "INS": 114, "HOME": 115, "END": 119,
    "PGUP": 116, "PAGEUP": 116, "PGDOWN": 121, "PAGEDOWN": 121,
    "DEL": 117, "DELETE": 117,
    "LEFT": 123, "RIGHT": 124, "DOWN": 125, "UP": 126,
    # function keys
    "F1": 122, "F2": 120, "F3": 99,  "F4": 118,
    "F5": 96,  "F6": 97,  "F7": 98,  "F8": 100,
    "F9": 101, "F10": 109,"F11": 103,"F12": 111,
    "F13": 105,"F14": 107,"F15": 113,"F16": 106,
    "F17": 64, "F18": 79, "F19": 80, "F20": 90,
    # MS extended keyboards send F13 for Print Screen
    "PRINT": 105, "PRINTSCREEN": 105, "Print": 105,
}

# Keys that are safe to register WITHOUT a modifier. A bare letter / digit /
# Space would hijack that key system-wide (you could no longer type it).
BARE_OK = {k for k in VK if k[0] == "F" and k[1:].isdigit()} | {
    "PRINT", "PRINTSCREEN", "Print", "INSERT", "INS",
}


def lookup(name: str):
    """Virtual key code for a parse_hotkey key name (case-insensitive), or None."""
    return VK.get(str(name).upper(), VK.get(str(name)))
