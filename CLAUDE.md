# CLAUDE.md — working notes for ScreenCapture

A LightShot/ScreenPal-style screenshot + screen-recording menu-bar app for
macOS (primary) and Windows. PyQt6 UI, native macOS bits via PyObjC.

This file is the fast path. It captures the **non-obvious** things that cost
hours to rediscover. Read it before changing capture, the overlay, the tray,
recording, or packaging.

---

## Run it (development)

```bash
cd ~/Documents/ScreenCapture
.venv/bin/python main.py        # launch from a SHELL — see "menu-bar icon" below
```

- Single instance is enforced by binding TCP `127.0.0.1:47392`. A second
  launch exits immediately. To restart: `pkill -f "[m]ain.py"` then relaunch.
- It's a menu-bar-only app (LSUIElement / NSApp Accessory) — no Dock icon, no
  window. Look for the **purple feather** in the menu bar.
- Logs: it prints to stdout/stderr. When launched via the LaunchAgent, see
  `/tmp/sc_agent.log`.

## Dependencies

All installed in `.venv` (Python 3.14): `PyQt6`, `mss`, `av` (PyAV), `cv2`
(opencv), `numpy`, `Pillow`, `sounddevice`, `imageio-ffmpeg`, and `pyobjc`
(AppKit/Quartz/AVFoundation/Foundation/objc). ffmpeg comes from
`imageio-ffmpeg` (no system ffmpeg needed). System audio uses the compiled
native helper `sc_audio_helper` (build from `sc_audio_helper.m`, see README).

---

## Architecture reality (IMPORTANT — there are two codebases)

1. **The Qt monolith (this is the live macOS app):** `main.py` + `overlay.py`
   + `recorder.py` + `recording_toolbar.py` + `webcam.py` + `countdown.py` +
   `tools.py` + `capture.py` + `audio_helper.py`. This is what runs.
2. **`sc/` — a partial pure-PyObjC rewrite** (native NSStatusItem tray,
   CGWindowListCreateImage capture, NSPanel overlay). It is **NOT the default
   app.** `main.py` only borrows `sc/hotkey.py` (Carbon global hotkey) and
   `sc/permissions.py`/`sc/capture.py` are handy for diagnostics. You can run
   the native screenshot-only core with `python -m sc` or
   `SCREENCAPTURE_USE_NATIVE=1 python main.py`, but it lacks the annotation
   toolbar and recording. **Don't confuse the two.**
3. `mac_tray.py` — a native NSStatusItem helper written as a workaround; it is
   **currently unused** (QSystemTrayIcon works once launched correctly — see
   below). Kept for reference.
4. `platform_utils.py` — central macOS permission checks + Settings deep-links
   + NSWindow configuration helpers.

---

## macOS gotchas (every one of these cost real debugging time)

### Menu-bar icon does not appear when launched (historical)
- **Symptom:** app runs (holds the lock port) but no menu-bar icon.
- **Causes seen:** (1) a `.app` whose `MacOS/ScreenCapture` is a **bash script**
  that `exec`s Python — LaunchServices launch doesn't attach the GUI session, so
  `QSystemTrayIcon` reports visible but renders nothing; (2) a PyInstaller
  **onefile** `.app` — cold-starts too slowly / unpacks to a random `/tmp` dir.
- **Solution (current):** ship a **PyInstaller onedir, ad-hoc-signed `.app`**
  (compiled bootloader at a fixed path). Its tray icon renders on double-click,
  AND the fixed-path signed binary gives a stable TCC identity so Screen
  Recording / Camera grants stick. Build with `pyinstaller ScreenCapture.spec`
  (the spec is onedir — see the COLLECT step). See "Building the app" below.

### Capture shows the desktop wallpaper, not the windows
- **Cause:** missing **Screen Recording** permission. macOS silently returns
  only wallpaper + own windows. `main.py` now checks
  `platform_utils.has_screen_recording_permission()` before capturing
  (`_ensure_screen_recording`) and guides the user to Settings.
- TCC ties the grant to the binary identity. Running as bare `python3` means
  the grant attaches to the Python runtime, not "ScreenCapture".

### ⌘C / ⌘S / ⌘Z don't fire in the overlay
- **Cause:** Qt swaps Ctrl/Cmd on macOS — the **Command** key arrives as
  `Qt.ControlModifier`, not `MetaModifier`. `overlay.py` defines
  `MODIFIER_KEY = ControlModifier | MetaModifier` (accept both) and sets
  `StrongFocus` so `keyPressEvent` fires.

### Color picker / any dialog opens behind the overlay
- **Cause:** the capture overlay sits at NSWindow level 25; child dialogs open
  at the normal level → hidden underneath. **Fix:** drop the overlay's window
  level to 0 while a dialog is open, restore to 25 after (see
  `overlay._pick_color` / `_set_overlay_level`).

### A lone text annotation is missing from the saved screenshot
- **Cause:** in-progress text lives in `text_editing`/`text_content` and is
  only committed by clicking the canvas / Enter / ⌘C-S. Clicking the toolbar
  Copy/Save buttons skipped that. **Fix:** `_get_result_image()` calls
  `_finish_text_editing()` first.

### Webcam fails / "No cameras detected"
- `main.py` sets `OPENCV_AVFOUNDATION_SKIP_AUTH=1`, so OpenCV never triggers
  the camera prompt. We request **Camera** permission ourselves via
  AVFoundation (`platform_utils.request_camera_permission`) when the webcam is
  toggled, and only open `cv2.VideoCapture` once granted.
- **`list_cameras()` enumerates via AVFoundation, NOT by opening cv2 devices.**
  The old cv2-probe approach failed (returned `[]` → "No cameras detected")
  whenever Camera permission wasn't granted yet, because opening a device
  needs permission but *listing* via AVFoundation does not.
- **`pyobjc-framework-AVFoundation` MUST be installed.** It's easy to miss —
  the installed venv shipped without it once and every camera feature broke
  silently (status checks fell into except-returns-"authorized", enumeration
  returned nothing). It's now in `requirements.txt`; verify with
  `python -c "import AVFoundation"`.
- Camera permission is per-binary-identity, so it's reliable only under a
  stable app identity (signed bundle); under bare-Python it can read
  `not_determined`.

### Overlays revealing the desktop / switching Spaces
- Any overlay shown during capture/recording must be created with
  `WA_ShowWithoutActivating` and have its NSWindow set to
  CanJoinAllSpaces (+ level) **before/at show** — otherwise macOS switches to
  the app's home Space and reveals the wallpaper. See
  `platform_utils.configure_nswindow` / `recorder._configure_nswindow` and
  `countdown.py`. Capture the pixels BEFORE showing any window.

### Notifications
- Keep them rare and meaningful (errors, user-initiated confirmations). No
  startup toast. When run as bare Python the notification icon is the generic
  Python rocket (cosmetic; a signed bundle fixes it).

---

## Recording pipeline (works; verified end-to-end)

`recorder.ScreenRecorder` (QThread): mss grabs frames → composites cursor,
webcam PiP, annotations → PyAV encodes H.264 into a temp **MKV** (the MP4 muxer
hits errno 22 on macOS via PyAV) → ffmpeg remuxes to MP4 and muxes AAC audio.
Audio: system audio via `sc_audio_helper` (ScreenCaptureKit), mic via
`sounddevice`, mixed/mastered in `audio_helper.py`. Output →
`~/Movies/ScreenCapture/`, revealed in Finder on stop.

---

## The icon (purple feather)

Source of truth: `assets/icon.svg`. Re-render to the PNGs with QtSvg:

```python
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtCore import Qt
# render assets/icon.svg -> 1024 ARGB, crop to content, pad square,
# resize to 44 (icon_tray.png), 88 (icon_tray@2x.png), 512 (icon.png)
```

It's a **colored** icon (not a template/mask), so don't call `setIsMask(True)`
— that would strip the purple. For the .app, build `icon.icns` from `icon.png`
with `iconutil`.

---

## Building the app (the shipped artifact)

The deliverable is a **PyInstaller onedir, ad-hoc-signed `.app`**:

```bash
# 1. (re)generate the icns from the feather
ICONSET=/tmp/sc.iconset; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do sips -z $s $s assets/icon.png --out "$ICONSET/icon_${s}x${s}.png"; \
  d=$((s*2)); sips -z $d $d assets/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png"; done
iconutil -c icns "$ICONSET" -o assets/icon.icns

# 2. build (onedir — the spec uses EXE(exclude_binaries=True)+COLLECT+BUNDLE)
rm -rf build dist
.venv/bin/python -m PyInstaller ScreenCapture.spec --noconfirm --clean
# PyInstaller ad-hoc signs the bundle automatically. Result: dist/ScreenCapture.app

# 3. install (remove old copies first to avoid bundle-id conflicts)
rm -rf ~/Applications/ScreenCapture.app
cp -R dist/ScreenCapture.app ~/Applications/ScreenCapture.app
```

Auto-start: add as a **Login Item** (`System Events make login item ...
{path:".../ScreenCapture.app", hidden:true}`). Do NOT also run a LaunchAgent —
pick one, or two instances fight over the F13 hotkey.

**TCC after a rebuild:** ad-hoc signing means the cdhash changes each build, so
macOS may require re-granting Screen Recording / Camera after a rebuild. A paid
Developer ID cert (`codesign --sign "Developer ID Application: ..."`) would make
grants survive rebuilds — not set up here. On first run the app fires the
Screen Recording prompt (`_ensure_screen_recording`) and the Camera prompt (on
webcam toggle), and deep-links to the right Settings pane.

**Spec notes:** onedir is essential (onefile breaks stable identity). Keep
`hiddenimports` in sync when adding modules that are imported inside function
bodies (the `sc/` submodules, `mac_tray`, `platform_utils`, `cv2/av/mss`, the
pyobjc frameworks). `sc_audio_helper` is bundled via `binaries`.

## Updating the installed app (dev iteration without a full rebuild)

For quick dev iteration you can still run from source (`/.venv/bin/python
main.py` from a shell) — the icon shows when launched from a shell. To update
the **shipped** app you must rebuild (above); the signed `.app` does not read
your source tree.

## (legacy) source-copy install + LaunchAgent

The installed copy lives at `~/Applications/ScreenCapture/` (its own `.venv`,
already has all deps). To deploy changes:

```bash
cp -f *.py requirements.txt ~/Applications/ScreenCapture/
rm -rf ~/Applications/ScreenCapture/sc && cp -R sc ~/Applications/ScreenCapture/sc
cp -R assets ~/Applications/ScreenCapture/
launchctl kickstart -k gui/$(id -u)/com.screencapture.app   # restart the agent
```

`sync.sh` does most of this but **does not copy `sc/` or `mac_tray.py`** — copy
them manually (main.py imports `sc.hotkey`).

LaunchAgent: `~/Library/LaunchAgents/com.screencapture.app.plist`. Manage with
`launchctl bootstrap|bootout|kickstart|print gui/$(id -u)[/com.screencapture.app]`.

---

## Known remaining work
- **Signed `.app` bundle** with a compiled launcher: fixes (a) double-click
  showing the icon, (b) camera/screen-recording grants attaching to a stable
  "ScreenCapture" identity instead of "python3", (c) the rocket notification
  icon and the "python3 can run in the background" label.
- Windows system-audio capture (only mic today).
- The `sc/` native rewrite is incomplete (no annotation/recording).
