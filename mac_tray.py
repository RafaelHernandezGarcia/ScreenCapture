"""Native macOS menu-bar status item (NSStatusItem) for the Qt app.

Why this exists: PyQt6's QSystemTrayIcon fails to *render* for an
LSUIElement (menu-bar-only) app launched via LaunchServices — it reports
isSystemTrayAvailable()=True and isVisible()=True, but no icon appears on
the menu bar. NSStatusItem always renders. Qt's event loop on macOS is
the Cocoa run loop, so an NSStatusItem coexists with QApplication and its
menu actions fire as plain Python callables on the main thread.

Supports: plain items (with optional title/hidden updates), separators,
and a dynamic submenu rebuilt each time it opens (for the camera list).
"""
import objc
from AppKit import (
    NSStatusBar, NSVariableStatusItemLength, NSMenu, NSMenuItem, NSImage,
)
from Foundation import NSObject, NSMakeSize


class _MenuTarget(NSObject):
    """Routes NSMenuItem clicks and submenu-open events to Python callables."""

    def init(self):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._handlers = {}          # id(NSMenuItem) -> callable
        self._submenu_builders = {}  # id(NSMenu) -> callable(_SubMenu)
        return self

    def fire_(self, sender):
        cb = self._handlers.get(id(sender))
        if cb:
            try:
                cb()
            except Exception:
                import traceback
                traceback.print_exc()

    # NSMenuDelegate — called right before a submenu is displayed.
    def menuNeedsUpdate_(self, menu):
        builder = self._submenu_builders.get(id(menu))
        if builder:
            try:
                builder(_SubMenu(menu, self))
            except Exception:
                import traceback
                traceback.print_exc()


class _Item:
    def __init__(self, mi):
        self._mi = mi

    def set_title(self, title):
        self._mi.setTitle_(title)

    def set_hidden(self, hidden):
        self._mi.setHidden_(bool(hidden))


class _SubMenu:
    """Handed to submenu builders to (re)populate items on open."""

    def __init__(self, menu, target):
        self._menu = menu
        self._target = target

    def clear(self):
        # Drop stale handler refs for the items we're removing.
        for i in range(self._menu.numberOfItems()):
            it = self._menu.itemAtIndex_(i)
            self._target._handlers.pop(id(it), None)
        self._menu.removeAllItems()

    def add_item(self, title, callback=None, checked=False, enabled=True):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, b"fire:", ""
        )
        if enabled and callback is not None:
            mi.setTarget_(self._target)
            self._target._handlers[id(mi)] = callback
        else:
            mi.setEnabled_(False)
        if checked:
            mi.setState_(1)  # NSControlStateValueOn
        self._menu.addItem_(mi)


class MacTray:
    """A native menu-bar status item with a menu."""

    def __init__(self, icon_path=None, tooltip=""):
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._target = _MenuTarget.alloc().init()
        btn = self._item.button()
        if icon_path:
            img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if img is not None:
                img.setSize_(NSMakeSize(18, 18))
                img.setTemplate_(False)  # keep the colored purple feather
                if btn is not None:
                    btn.setImage_(img)
        if tooltip and btn is not None:
            btn.setToolTip_(tooltip)
        self._menu = NSMenu.alloc().init()
        self._menu.setAutoenablesItems_(False)
        self._item.setMenu_(self._menu)

    def add_item(self, title, callback):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, b"fire:", ""
        )
        mi.setTarget_(self._target)
        self._target._handlers[id(mi)] = callback
        self._menu.addItem_(mi)
        return _Item(mi)

    def add_separator(self):
        self._menu.addItem_(NSMenuItem.separatorItem())

    def add_submenu(self, title, builder):
        """builder: callable(_SubMenu) invoked each time the submenu opens."""
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, b"", ""
        )
        sub = NSMenu.alloc().init()
        sub.setAutoenablesItems_(False)
        sub.setDelegate_(self._target)
        self._target._submenu_builders[id(sub)] = builder
        parent.setSubmenu_(sub)
        self._menu.addItem_(parent)

    def set_tooltip(self, tooltip):
        btn = self._item.button()
        if btn is not None:
            btn.setToolTip_(tooltip)
