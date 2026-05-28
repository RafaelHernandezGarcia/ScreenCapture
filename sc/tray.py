"""Menu-bar tray (NSStatusItem) — pure PyObjC.

Holds module-level references to the status item and menu target so
nothing gets garbage-collected. Without those, the icon disappears
seconds after launch.
"""
import os
import sys
import objc
from AppKit import (
    NSStatusBar, NSVariableStatusItemLength,
    NSMenu, NSMenuItem, NSImage,
    NSWorkspace, NSApp,
)
from Foundation import NSObject, NSURL


_status_item = None
_menu_target = None


def _bundle_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _icon_path():
    base = _bundle_dir()
    for name in ("icon_tray.png", "icon.png"):
        p = os.path.join(base, "assets", name)
        if os.path.exists(p):
            return p
    return None


class _Target(NSObject):
    """Receives NSMenuItem actions and routes them to Python callables."""

    def init(self):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._handlers = {}  # NSMenuItem (id) -> callable
        return self

    def bind_(self, item_and_callable):
        item, fn = item_and_callable
        self._handlers[id(item)] = fn

    def fire_(self, sender):
        cb = self._handlers.get(id(sender))
        if cb:
            cb()


def install(items):
    """Build and show the tray icon + menu.

    items: list of (title, callable_or_None). Use ("-", None) for a
           separator. Callable can be None to disable the item.
    """
    global _status_item, _menu_target

    bar = NSStatusBar.systemStatusBar()
    item = bar.statusItemWithLength_(NSVariableStatusItemLength)
    btn = item.button()
    icon_path = _icon_path()
    if icon_path:
        img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if img is not None:
            img.setTemplate_(True)
            img.setSize_((18, 18))
            btn.setImage_(img)
        else:
            btn.setTitle_("📷")
    else:
        btn.setTitle_("📷")

    target = _Target.alloc().init()
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)
    for title, cb in items:
        if title == "-":
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, b"fire:", "",
        )
        if cb is None:
            mi.setEnabled_(False)
        else:
            mi.setTarget_(target)
            target.bind_((mi, cb))
        menu.addItem_(mi)

    item.setMenu_(menu)
    _status_item = item
    _menu_target = target
    return item


def show_recordings_folder(path: str):
    """Reveal a path in Finder."""
    NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(path))
