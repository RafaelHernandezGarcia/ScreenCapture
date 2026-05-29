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

### Screenshot resolution (Retina quality)
- Default capture = **mss** → screenshots at the familiar on-screen size.
- **Opt-in** `config["high_res_screenshots"] = true` switches `_do_capture` to
  **`sc.capture.grab` (CGWindowListCreateImage)**, which grabs Retina displays
  at true **2x** (e.g. 2992×1934 vs mss's 1496×967). It's sharper but the PNG
  is 2x the pixel dimensions, so it opens larger — a user found that
  "zoomed-in", hence it's off by default. Non-Retina (1080p) displays are 1x
  either way, so the flag makes no difference there.
- Saves are **lossless PNG**; clipboard copy is PNG too. Possible refinement:
  embed the display's ICC / Display-P3 profile for exact wide-gamut color.

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
`sounddevice` (low-latency InputStream), mixed/mastered in `audio_helper.py`.
Output → `~/Movies/ScreenCapture/`, revealed in Finder on stop.

**Smoothness:** the capture loop only grabs + composites + queues frames; a
separate **encoder thread** does `stream.encode`/`container.mux` (PyAV touched
only there). A bounded `queue.Queue` absorbs encode bursts so a slow encode
can't stall capture (less stutter); if the queue is momentarily full the frame
is dropped (playback holds the previous one).

**Audio mix (`mix_and_master`) — deliberately simple / Loom-style, for CLEAN
audio. Do NOT re-add fancy dynamics here; they each caused a real complaint:**
- NO sidechain ducking → dynamic ducking audibly *pumps* the volume up/down.
- NO hard noise gate → it clips quiet word-endings (audio "cuts").
- NO tiny mic buffer → a small `InputStream` blocksize starves on hiccups and
  drops samples (audio "cuts"). Mic uses a roomy 2048-sample buffer.
- The mix is: gentle mic compression (consistent voice, no gate) + system
  audio held at a **fixed 0.45 level** (steady background, no pumping) + voice
  forward + peak limiter + ONE static normalize (loudness, not dynamic).
- Lip-sync delay from the roomy buffer is handled by the "Audio Sync" offset,
  never by shrinking the buffer.

**Webcam:** the last on/off choice is saved (`config["webcam_default"]`); if it
was on, `_start_recording` auto-enables the circle from the start (no clicking
mid-recording). `_start_webcam` reuses an existing capture if present.

**A/V sync (lip-sync):** three independent clocks (mss video, SCK system audio,
sounddevice mic) are aligned by start-time arithmetic. Two safeguards:
- **`audio_offset_ms`** (config + tray "Audio Sync" submenu): OBS-style manual
  sync offset. −ms advances the voice (fixes "voice lags lips"), +ms delays it.
- **Anti-drift:** the mixed audio is padded/trimmed to exactly the video's
  length (`last_pts/fps`) so the ends can't drift apart on long recordings.
- **Ultimate fix (future):** capture video AND audio through ScreenCaptureKit
  so every frame/sample shares ONE clock (what CleanShot/Loom/Screen Studio do)
  → frame-accurate sync, no offset needed. `sc_audio_helper.m` already uses SCK
  for audio; extending it to video is the real "copy the pros" rewrite.

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

The deliverable is a **PyInstaller onedir `.app`, signed with a stable
self-signed certificate** so TCC grants (Screen Recording / Camera) persist
across rebuilds for FREE — no $99 Apple Developer account.

### One-time: create the signing certificate (GUI; user must do this)
Keychain Access → Certificate Assistant → **Create a Certificate…**
- Name: **LocalAppDev**  ·  Identity Type: **Self-Signed Root**  ·  Type: **Code Signing**
- Check "Let me override defaults", set validity to ~3650 days.
- After creating: double-click it → Trust → **Code Signing: Always Trust**.

Why this works: ad-hoc signing keys TCC to the per-build **cdhash** (changes
every build → grant lost). A stable cert gives a constant **Authority**; TCC
anchors the grant to (bundle id + cert), so it survives rebuilds.

### Build + sign + install (one command)
```bash
./build.sh          # render icns -> PyInstaller onedir -> sign_app.sh -> install to ~/Applications
```
Or the pieces: `pyinstaller ScreenCapture.spec --noconfirm --clean` then
`./sign_app.sh dist/ScreenCapture.app`. Signing is **inside-out, never
`--deep`** (signs every `.so`/`.dylib`, nested `.framework`s, the embedded
python binary, the bootloader, then the outer `.app`) using
`entitlements.plist` (camera/mic + `cs.disable-library-validation` so the
cert-signed app can load wheel C-extensions; NO app-sandbox — it would block
AVFoundation / CGWindowListCreateImage).

### First run (per machine, once)
```bash
tccutil reset ScreenCapture com.screencapture.app   # clear any stale ad-hoc entries
tccutil reset Camera        com.screencapture.app
open ~/Applications/ScreenCapture.app
```
Then grant Screen Recording + Camera once — it now shows as **ScreenCapture**
(not python3.14) and the grant sticks across future `./build.sh` runs (same
cert). The app also self-prompts (`_ensure_screen_recording` + webcam toggle).

Auto-start: add as a **Login Item** (`System Events make login item …
{path:".../ScreenCapture.app", hidden:true}`). Do NOT also run a LaunchAgent —
pick one, or two instances fight over the F13 hotkey.

### Notes / gotchas
- **onedir is essential** (onefile unpacks to a random `/tmp` dir → TCC treats
  it as a rogue path; also breaks the stable identity).
- Run/install from `~/Applications`, never from `dist/` (Gatekeeper path
  randomization muddies TCC).
- If you only ever ad-hoc sign (skip the cert), grants reset every rebuild and
  the Privacy list shows **python3.14** instead of ScreenCapture.
- Keep `hiddenimports` in the spec in sync when adding dynamically-imported
  modules (`sc/` submodules, `mac_tray`, `platform_utils`, `cv2/av/mss`, pyobjc
  frameworks). `sc_audio_helper` is bundled via `binaries`.
- Files: `build.sh` (pipeline), `sign_app.sh` (signing), `entitlements.plist`.

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
- **Self-signed cert flow** (build.sh/sign_app.sh) is the free fix for stable
  TCC identity + double-click icon + "ScreenCapture" label. Requires the
  one-time `LocalAppDev` cert (see "Building the app"). A paid Developer ID +
  notarization would additionally satisfy Gatekeeper for *distribution* — not
  needed for personal use.
- Windows system-audio capture (only mic today).
- The `sc/` native rewrite is incomplete (no annotation/recording).
