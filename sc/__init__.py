"""ScreenCapture — clean rewrite.

v1 surface:
- sc.hotkey      Carbon RegisterEventHotKey wrapper (no Accessibility).
- sc.capture     CGWindowListCreateImage screen grab.
- sc.overlay     NSPanel selection overlay (non-activating, all-Spaces).
- sc.tray        NSStatusItem menu bar.
- sc.clipboard   NSPasteboard image copy.
- sc.permissions Lazy TCC checks + Settings deeplinks.
- sc.config      JSON config under ~/Library/Application Support.
- sc.paths       Recordings dir, log dir.
"""
