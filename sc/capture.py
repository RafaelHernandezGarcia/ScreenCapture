"""Native screen capture via CGWindowListCreateImage.

One-shot, synchronous, fast (<10 ms for 1080p). Returns the pixels of
the *current* screen state — no Qt activation, no NSApp.activateIgnoring,
no Spaces dance. The image is in physical pixels so high-DPR displays
get true Retina resolution.
"""
import numpy as np
from PIL import Image
from AppKit import NSEvent, NSScreen
from Quartz import (
    CGWindowListCreateImage,
    CGRectMake,
    CGImageGetWidth, CGImageGetHeight,
    CGImageGetBytesPerRow, CGImageGetDataProvider,
    CGDataProviderCopyData,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowImageDefault,
    kCGNullWindowID,
)


def screens():
    """Return list of (NSScreen, top-left-origin frame dict)."""
    out = []
    if not NSScreen.screens():
        return out
    main_h = NSScreen.screens()[0].frame().size.height
    for s in NSScreen.screens():
        f = s.frame()
        # NSScreen origins are bottom-left; convert to top-left.
        top = main_h - (f.origin.y + f.size.height)
        out.append((s, {
            "x": int(f.origin.x), "y": int(top),
            "w": int(f.size.width), "h": int(f.size.height),
            "scale": float(s.backingScaleFactor()),
        }))
    return out


def screen_at_cursor():
    """Return (NSScreen, top-left-origin frame dict) under the mouse."""
    pos = NSEvent.mouseLocation()
    main_h = NSScreen.screens()[0].frame().size.height
    cursor_top = main_h - pos.y
    for s, fr in screens():
        if (fr["x"] <= pos.x <= fr["x"] + fr["w"]
                and fr["y"] <= cursor_top <= fr["y"] + fr["h"]):
            return s, fr
    return screens()[0]


def grab(rect: dict) -> Image.Image:
    """Grab a screen rect (top-left origin, logical points) as a PIL.Image.

    rect: {"x": int, "y": int, "w": int, "h": int}
    Returns image at native pixel resolution (Retina = 2x logical).
    Returns a small black placeholder if the OS denies the capture
    (Screen Recording permission missing).
    """
    cgrect = CGRectMake(rect["x"], rect["y"], rect["w"], rect["h"])
    cg = CGWindowListCreateImage(
        cgrect,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
        kCGWindowImageDefault,
    )
    if cg is None:
        return Image.new("RGB", (rect["w"], rect["h"]), (0, 0, 0))
    return _cg_to_pil(cg)


def _cg_to_pil(cg) -> Image.Image:
    w = CGImageGetWidth(cg)
    h = CGImageGetHeight(cg)
    bpr = CGImageGetBytesPerRow(cg)
    data = CGDataProviderCopyData(CGImageGetDataProvider(cg))
    buf = bytes(data)
    # CGImage is BGRA premultiplied. Drop alpha, swap to RGB.
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, bpr // 4, 4))[:, :w, :]
    rgb = np.ascontiguousarray(arr[:, :, [2, 1, 0]])
    return Image.fromarray(rgb)
