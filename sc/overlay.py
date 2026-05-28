"""Selection overlay — pure PyObjC NSPanel + NSView.

Why NSPanel and not NSWindow:
- NSPanel supports the NSWindowStyleMaskNonactivatingPanel style mask.
- A non-activating panel can become the key window WITHOUT activating
  the owning app. macOS therefore never switches Spaces to the app's
  "home" Space — the panel just appears on the user's current Space.
- Combined with NSWindowCollectionBehaviorCanJoinAllSpaces +
  Stationary + Transient, the panel is invisible to Mission Control,
  Cmd+Tab, and the Dock.

Why pure PyObjC and not Qt:
- Qt's QWidget.show() calls makeKeyAndOrderFront: with the *default*
  window collection behavior, which forces the Spaces switch we've
  been fighting. Configuring the collection behavior in Qt's showEvent
  fires too late.
- Native NSView.drawRect: paints in microseconds. Mouse events arrive
  with no Qt translation overhead.
- No PyQt6 startup cost (PyQt6 alone is ~9 s of cold start in the
  PyInstaller bundle).
"""
import io
import os
from datetime import datetime

import objc
import numpy as np
from AppKit import (
    NSPanel, NSView, NSColor, NSBezierPath, NSEvent, NSImage,
    NSGraphicsContext, NSAffineTransform, NSCompositingOperationCopy,
    NSCompositingOperationDestinationOut,
    NSScreen, NSApp,
    NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
    NSAttributedString, NSCursor,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered, NSPopUpMenuWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorTransient,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSSavePanel,
    NSEventTypeKeyDown, NSEventModifierFlagCommand,
)
from Foundation import (
    NSMakeRect, NSMakePoint, NSMakeSize, NSData, NSPointInRect,
)

from . import clipboard
from . import paths


_DIM_OPACITY = 0.55          # Outside-selection veil
_SELECTION_BORDER = 1.0
_HANDLE = 6.0                # Corner/edge handle size (px)
_HINT_FONT_SIZE = 12

_CURRENT = None  # singleton: only one overlay open at a time


def show(pil_image, screen_frame: dict, on_close=None):
    """Open a fullscreen selection overlay on the given screen.

    pil_image:    PIL.Image at native pixel resolution
    screen_frame: top-left-origin dict from sc.capture.screens()
    on_close:     called when overlay closes (any reason)
    """
    global _CURRENT
    if _CURRENT is not None:
        _CURRENT.close()
        _CURRENT = None

    panel = _make_panel(screen_frame)
    view = _SelectionView.alloc().initWithFrame_image_screen_(
        panel.contentView().frame(), pil_image, screen_frame
    )
    view.set_on_close(on_close)
    panel.setContentView_(view)
    panel.makeKeyAndOrderFront_(None)
    # The screenshot was already grabbed before this panel appeared, so
    # activating the app here is safe — it can't change what we captured,
    # and the panel is already CanJoinAllSpaces so macOS won't switch
    # Spaces. Activation is what makes the key window actually receive
    # keyDown: (Esc / Cmd+C / Cmd+S). Without it a background app's panel
    # stays unfocused and the user gets trapped with no keyboard.
    try:
        NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        pass
    panel.makeFirstResponder_(view)
    NSCursor.crosshairCursor().push()
    _CURRENT = panel


class _OverlayPanel(NSPanel):
    """Borderless panel that CAN become key.

    A plain borderless NSWindow/NSPanel returns NO from canBecomeKeyWindow,
    so it never receives keyDown: — that was the bug that trapped the user
    (Esc did nothing). Overriding these two selectors fixes it.
    """

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


def _make_panel(frame: dict) -> NSPanel:
    # NSPanel uses bottom-left-origin coordinates. Convert.
    main_h = NSScreen.screens()[0].frame().size.height
    rect = NSMakeRect(
        frame["x"],
        main_h - frame["y"] - frame["h"],
        frame["w"], frame["h"],
    )
    panel = _OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect,
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(False)
    panel.setLevel_(NSPopUpMenuWindowLevel)
    panel.setAcceptsMouseMovedEvents_(True)
    panel.setIgnoresMouseEvents_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle
        | NSWindowCollectionBehaviorTransient
        | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    # Floating panels can become key without app activation. This is
    # what lets Cmd+C / Esc / Cmd+S go to our keyDown: handler.
    panel.setBecomesKeyOnlyIfNeeded_(False)
    return panel


def _close_current():
    global _CURRENT
    if _CURRENT is not None:
        try:
            NSCursor.pop()
        except Exception:
            pass
        try:
            _CURRENT.orderOut_(None)
        except Exception:
            pass
        _CURRENT = None


# -------------------------------------------------------------------------

class _SelectionView(NSView):
    """Custom NSView that:
       - paints the captured screenshot under a dim veil
       - punches the selection rectangle through to the live screen
         (well — to the dimmed-but-otherwise-untouched screenshot)
       - tracks mouseDown/Dragged/Up to set the selection
       - handles Cmd+C / Cmd+S / Esc / Return
    """

    # -- init / wiring ----------------------------------------------------

    def initWithFrame_image_screen_(self, frame, pil_image, screen_frame):
        self = objc.super(_SelectionView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._pil = pil_image
        self._screen = screen_frame
        self._scale = pil_image.width / max(1, screen_frame["w"])
        self._ns_image = _pil_to_nsimage(pil_image)
        self._origin = None      # mouseDown point (logical, view coords)
        self._current = None     # current drag point
        self._sel = None         # finalized selection rect (NSRect, logical)
        self._on_close = None
        return self

    def set_on_close(self, cb):
        self._on_close = cb

    def acceptsFirstResponder(self):
        return True

    def isFlipped(self):
        # Easier math: y grows downward, matching the captured image
        return True

    # -- paint ------------------------------------------------------------

    def drawRect_(self, dirty):
        bounds = self.bounds()
        # Draw the screenshot at full extent
        self._ns_image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            bounds, NSMakeRect(0, 0, 0, 0),
            NSCompositingOperationCopy, 1.0, True, None,
        )
        # Dim veil over everything
        NSColor.colorWithCalibratedWhite_alpha_(0, _DIM_OPACITY).set()
        NSBezierPath.fillRect_(bounds)

        sel = self._live_selection()
        if sel is not None and sel.size.width > 0 and sel.size.height > 0:
            # "Cut" the dim veil over the selection by re-drawing the
            # screenshot region at full opacity.
            self._ns_image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                sel,
                NSMakeRect(
                    sel.origin.x * self._scale,
                    sel.origin.y * self._scale,
                    sel.size.width * self._scale,
                    sel.size.height * self._scale,
                ),
                NSCompositingOperationCopy, 1.0, True, None,
            )
            # White border
            NSColor.whiteColor().setStroke()
            NSBezierPath.bezierPathWithRect_(sel).stroke()
            # Dimensions label
            self._draw_dimensions(sel)
            # Handles when selection is finalized
            if self._sel is not None and self._origin is None:
                self._draw_handles(sel)
        else:
            # Pre-selection hint
            self._draw_hint(bounds)

        # On-screen action buttons (always at least Cancel) — the mouse
        # fallback so the user can always escape, even if keyboard focus
        # somehow fails.
        self._draw_buttons()

    def _draw_dimensions(self, sel):
        text = f"{int(sel.size.width)} × {int(sel.size.height)}"
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(_HINT_FONT_SIZE),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        size = astr.size()
        pad = 6
        # Place above the selection by default; below if not enough room
        x = sel.origin.x
        y = sel.origin.y - size.height - pad - 4
        if y < 0:
            y = sel.origin.y + sel.size.height + 4
        bg = NSMakeRect(x, y, size.width + pad * 2, size.height + pad)
        NSColor.colorWithCalibratedWhite_alpha_(0, 0.7).set()
        NSBezierPath.fillRect_(bg)
        astr.drawAtPoint_(NSMakePoint(x + pad, y + pad / 2))

    def _draw_handles(self, sel):
        h = _HANDLE
        pts = [
            (sel.origin.x, sel.origin.y),
            (sel.origin.x + sel.size.width, sel.origin.y),
            (sel.origin.x, sel.origin.y + sel.size.height),
            (sel.origin.x + sel.size.width, sel.origin.y + sel.size.height),
            (sel.origin.x + sel.size.width / 2, sel.origin.y),
            (sel.origin.x + sel.size.width / 2, sel.origin.y + sel.size.height),
            (sel.origin.x, sel.origin.y + sel.size.height / 2),
            (sel.origin.x + sel.size.width, sel.origin.y + sel.size.height / 2),
        ]
        NSColor.whiteColor().set()
        for (x, y) in pts:
            NSBezierPath.fillRect_(NSMakeRect(x - h / 2, y - h / 2, h, h))
        NSColor.blackColor().setStroke()
        for (x, y) in pts:
            NSBezierPath.bezierPathWithRect_(
                NSMakeRect(x - h / 2, y - h / 2, h, h)
            ).stroke()

    def _draw_hint(self, bounds):
        text = "Drag to select  ·  Esc to cancel  ·  ⌘C to copy  ·  ⌘S to save"
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(13),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.85),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        size = astr.size()
        x = (bounds.size.width - size.width) / 2
        y = bounds.size.height * 0.10
        bg = NSMakeRect(x - 14, y - 8, size.width + 28, size.height + 16)
        NSColor.colorWithCalibratedWhite_alpha_(0, 0.55).set()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bg, 8, 8
        )
        path.fill()
        astr.drawAtPoint_(NSMakePoint(x, y))

    # -- action buttons ---------------------------------------------------

    def _action_buttons(self):
        """List of (NSRect, name, label) for the current state.

        Cancel is always present. Copy/Save appear once a selection is
        finalized (mouse released on a real rect).
        """
        b = self.bounds()
        out = []
        bh = 30
        margin = 18
        # Always-available Cancel in the top-right corner.
        cw = 96
        out.append((
            NSMakeRect(b.size.width - cw - margin, margin, cw, bh),
            "cancel", "✕  Cancel",
        ))
        sel = self._sel
        if sel is not None and self._origin is None \
                and sel.size.width > 4 and sel.size.height > 4:
            bw, gap = 92, 10
            total = bw * 2 + gap
            # Prefer just below the selection, right-aligned to it.
            x = sel.origin.x + sel.size.width - total
            if x < 4:
                x = 4
            y = sel.origin.y + sel.size.height + 10
            if y + bh > b.size.height - 4:        # no room below -> inside
                y = sel.origin.y + sel.size.height - bh - 10
            out.append((NSMakeRect(x, y, bw, bh), "copy", "Copy  ⌘C"))
            out.append((NSMakeRect(x + bw + gap, y, bw, bh), "save", "Save  ⌘S"))
        return out

    def _draw_buttons(self):
        font = NSFont.systemFontOfSize_(13)
        for rect, name, label in self._action_buttons():
            NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.92).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, 7, 7
            ).fill()
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.25).setStroke()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, 7, 7
            ).stroke()
            attrs = {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: NSColor.whiteColor(),
            }
            astr = NSAttributedString.alloc().initWithString_attributes_(
                label, attrs
            )
            ts = astr.size()
            astr.drawAtPoint_(NSMakePoint(
                rect.origin.x + (rect.size.width - ts.width) / 2,
                rect.origin.y + (rect.size.height - ts.height) / 2,
            ))

    def _button_at(self, point):
        for rect, name, _label in self._action_buttons():
            if NSPointInRect(point, rect):
                return name
        return None

    # -- selection helpers -----------------------------------------------

    def _live_selection(self):
        """The rect the user is currently drawing, or the finalized one."""
        if self._sel is not None:
            return self._sel
        if self._origin is None or self._current is None:
            return None
        x = min(self._origin.x, self._current.x)
        y = min(self._origin.y, self._current.y)
        w = abs(self._origin.x - self._current.x)
        h = abs(self._origin.y - self._current.y)
        return NSMakeRect(x, y, w, h)

    # -- mouse / key ------------------------------------------------------

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        # A click on an action button takes priority over starting a new
        # selection.
        action = self._button_at(p)
        if action == "cancel":
            self._cancel()
            return
        if action == "copy":
            self._copy()
            return
        if action == "save":
            self._save()
            return
        self._origin = p
        self._current = p
        self._sel = None
        self.setNeedsDisplay_(True)

    def rightMouseDown_(self, event):
        # Right-click anywhere cancels — last-resort escape hatch.
        self._cancel()

    def mouseDragged_(self, event):
        if self._origin is None:
            return
        self._current = self.convertPoint_fromView_(
            event.locationInWindow(), None
        )
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        if self._origin is None:
            return
        self._current = self.convertPoint_fromView_(
            event.locationInWindow(), None
        )
        sel = self._live_selection()
        self._origin = None
        if sel is not None and sel.size.width > 4 and sel.size.height > 4:
            self._sel = sel
        else:
            self._sel = None
        self.setNeedsDisplay_(True)

    def keyDown_(self, event):
        chars = event.charactersIgnoringModifiers() or ""
        cmd = bool(event.modifierFlags() & NSEventModifierFlagCommand)
        if event.keyCode() == 53:  # Escape
            self._cancel()
            return
        if chars in ("\r", "\n") or chars == " ":
            self._copy()
            return
        if cmd and chars.lower() == "c":
            self._copy()
            return
        if cmd and chars.lower() == "s":
            self._save()
            return

    # -- actions ----------------------------------------------------------

    def _cropped_image(self):
        sel = self._sel or self._live_selection()
        if sel is None or sel.size.width < 1 or sel.size.height < 1:
            return None
        s = self._scale
        x0 = max(0, int(sel.origin.x * s))
        y0 = max(0, int(sel.origin.y * s))
        x1 = min(self._pil.width, int((sel.origin.x + sel.size.width) * s))
        y1 = min(self._pil.height, int((sel.origin.y + sel.size.height) * s))
        if x1 <= x0 or y1 <= y0:
            return None
        return self._pil.crop((x0, y0, x1, y1))

    def _copy(self):
        img = self._cropped_image()
        if img is None:
            self._cancel()
            return
        clipboard.copy_image(img)
        self._dismiss()

    def _save(self):
        img = self._cropped_image()
        if img is None:
            self._cancel()
            return
        ts = datetime.now().strftime("%Y-%m-%d at %H.%M.%S")
        suggested = f"Screenshot {ts}.png"
        # Bring the app forward so the save sheet appears in front (we're
        # an accessory/background app the rest of the time).
        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(suggested)
        panel.setCanCreateDirectories_(True)
        panel.setAllowedFileTypes_(["png"])
        # Default to the user's screenshots dir
        from Foundation import NSURL
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(paths.screenshots_dir()))
        rc = panel.runModal()
        if rc == 1:  # NSModalResponseOK
            url = panel.URL().path()
            try:
                img.save(url)
            except Exception:
                pass
        self._dismiss()

    def _cancel(self):
        self._dismiss()

    def _dismiss(self):
        cb = self._on_close
        _close_current()
        if cb:
            try:
                cb()
            except Exception:
                pass


# -------------------------------------------------------------------------

def _pil_to_nsimage(pil) -> NSImage:
    """Convert PIL.Image → NSImage via PNG bytes (lossless, ~1ms)."""
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    img = NSImage.alloc().initWithData_(data)
    return img
