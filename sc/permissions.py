"""TCC permission checks and Settings deep-links.

Lazy is the rule: NEVER call request_*() at startup. Only call when
the user has explicitly initiated an action that needs the permission.
"""
import ctypes
import ctypes.util
import subprocess


def _settings_url(pane: str) -> str:
    return ("x-apple.systempreferences:com.apple.preference.security?"
            f"Privacy_{pane}")


_SETTINGS_PANES = {
    "ScreenRecording": "ScreenCapture",
    "Camera":          "Camera",
    "Microphone":      "Microphone",
    "Accessibility":   "Accessibility",
}


def open_settings(category: str) -> None:
    pane = _SETTINGS_PANES.get(category)
    if not pane:
        return
    subprocess.Popen(
        ["open", _settings_url(pane)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# --- Screen Recording -----------------------------------------------------

def has_screen_recording() -> bool:
    """True if the process can capture screen pixels."""
    try:
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        return True  # fail-open; capture itself will reveal failure


def request_screen_recording() -> None:
    """Trigger the system Screen Recording prompt (one-shot, first time only)."""
    try:
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        cg.CGRequestScreenCaptureAccess()
    except Exception:
        pass


# --- Camera / Microphone -------------------------------------------------

def _av_status(media_type: str) -> str:
    """Return 'authorized' | 'denied' | 'restricted' | 'not_determined'."""
    try:
        from AVFoundation import (
            AVCaptureDevice, AVMediaTypeVideo, AVMediaTypeAudio,
        )
        mt = AVMediaTypeVideo if media_type == "video" else AVMediaTypeAudio
        s = int(AVCaptureDevice.authorizationStatusForMediaType_(mt))
        return {0: "not_determined", 1: "restricted",
                2: "denied", 3: "authorized"}.get(s, "authorized")
    except Exception:
        return "authorized"


def camera_status() -> str:
    return _av_status("video")


def microphone_status() -> str:
    return _av_status("audio")


def request_camera(callback=None) -> None:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeVideo
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeVideo,
            (lambda granted: callback and callback(bool(granted))),
        )
    except Exception:
        if callback:
            callback(False)


def request_microphone(callback=None) -> None:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio,
            (lambda granted: callback and callback(bool(granted))),
        )
    except Exception:
        if callback:
            callback(False)
