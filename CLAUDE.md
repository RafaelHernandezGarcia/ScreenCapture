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

### Webcam looks laggy / jaggy even with a good camera
- **Root cause #1 — capture mode.** `cv2.VideoCapture(index)` with NO config
  opens the camera in its *default* mode, which is usually its **highest native
  resolution** (1080p/4K on a good cam) at a sluggish delivered rate → choppy
  motion. The better the camera, the worse it gets. Google Meet / WhatsApp are
  smooth because they negotiate **720p @ 30fps with a shallow buffer**. So
  `WebcamCapture._open_and_configure` now sets `CAP_PROP_FRAME_WIDTH/HEIGHT`
  (1280×720), `CAP_PROP_FPS` (30) and `CAP_PROP_BUFFERSIZE=1` (return the
  freshest frame, low latency). 720p is plenty for the small circular PiP.
  The capture loop just `read()`s in a tight loop — `read()` blocks until the
  next sensor frame, so it self-paces to ~30fps; the old artificial `sleep`
  only added latency.
- **Root cause #2 — preview repaint beating.** The live circle used a fixed
  33ms `QTimer` to repaint, running independently of the camera's 30fps → the
  two rates *beat*, repeating/skipping frames → visible judder in the **live
  self-view** (the recording itself was fine). Fix: `WebcamCapture` emits a
  `frame_ready` signal and `WebcamPreviewWidget` repaints on *that* (event
  driven). The timer is kept only as a slow 200ms fallback.
- The webcam appears in the recording by being **screen-captured** (the live
  preview circle is part of the framebuffer mss grabs), NOT composited from the
  camera feed — that's deliberate (compositing too gave a SECOND offset circle).
  So webcam smoothness depends on the preview being smooth, which the two fixes
  above ensure. mss grab is fast (~17ms for a large region → ~59fps ceiling), so
  the screen-capture side is not the bottleneck.

### Frameless overlay lands ~20px too high (pre-show `move()` drifts on macOS)
- **Symptom:** the webcam circle tucked into the bottom-right looked even on the
  right but had a big gap at the bottom — not symmetric.
- **Cause:** a `move(x, y)` issued **before the native window is shown** drifts
  upward ~20px on macOS (Qt later reports the window ~20px above where you asked;
  confirmed by reading the live `NSWindow.frame()`). x was fine, y lost ~20px.
- **Fix:** stash the target as `self._target_pos` and **re-assert it in
  `_apply_nswindow`** (the `singleShot(0)` after `showEvent`), once the native
  window exists — there `move()` sticks exactly. See `WebcamPreviewWidget`.
  Any frameless overlay that must land pixel-precise should do the same.

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

**Recording size:** `config["recording_size"]` (`native`/`1080p`/`720p`, tray
"Recording Size" menu) → `ScreenRecorder(target_height=...)`. Each frame is
scaled to that height (aspect preserved, even dims via cv2) before encoding;
`native` keeps the captured selection size. Capture itself stays mss/1x, so
1080p of a sub-1080p area upscales — fine for a standard output size.

**Webcam:** the last on/off choice is saved (`config["webcam_default"]`); if it
was on, `_start_recording` auto-enables the circle from the start (no clicking
mid-recording). `_start_webcam` reuses an existing capture if present.

**A/V sync (lip-sync) — automatic, NO manual offset menu.** The old "Audio
Sync" tray submenu / `audio_offset_ms` was removed (the user wanted it to "just
be in sync"). Three independent clocks (mss video, SCK system audio, sounddevice
mic) are aligned automatically:
- **Per-source start-time alignment** (`_align` in `recorder.run`): each source
  records its real `time.perf_counter()` start; we trim its lead-in (or pad with
  silence) to line it up with the video's start. No human tuning.
- **`webcam_latency_ms`** (passed to `ScreenRecorder`, default 160 when the
  webcam is on): delays the audio so the voice lands on the (pipeline-delayed)
  on-screen lips. NOTE: the webcam-capture fixes above (shallow buffer, no
  artificial sleep) *reduced* the webcam's latency, so this 160 may now be a
  touch high — recalibrate from user feedback (voice **before** lips → lower it;
  **after** → raise it). It is NOT a user-facing setting.
- **Anti-drift:** the mixed audio is padded/trimmed to exactly the video's
  length (`last_pts/fps`) so the ends can't drift apart on long recordings.
- **Ultimate fix (future):** capture video AND audio through ScreenCaptureKit
  so every frame/sample shares ONE clock (what CleanShot/Loom/Screen Studio do)
  → frame-accurate sync, no offset needed. `sc_audio_helper.m` already uses SCK
  for audio; extending it to video is the real "copy the pros" rewrite.

**Recording UI flow & feel (Loom/cal.com-grade — these were explicit asks):**
- **Setup phase:** pick region → `SetupPanel` (white card: toggle cam/mic, drag
  the circle) → "Start recording" → `CountdownOverlay` (3-2-1) → record.
- **Countdown** dims the whole screen but centers the number on the **selected
  region** (`CountdownOverlay(screen_geo, region_rect=...)`), not the screen.
- **Stop must feel INSTANT.** Finalizing (encoder flush → audio mix → ffmpeg
  remux) takes ~1s, so `_stop_recording` **hides every overlay immediately**
  (not close — destroy happens later in `_cleanup_recording` when
  `recording_stopped` fires) and lets the recorder finalize on its thread. Don't
  reintroduce "wait for the file before clearing the UI" — it reads as lag.
- **Drag handle is a SEPARATE top-level window.** `RecordingFrame._handle`
  (the three red dots) is its own window, so hiding the frame doesn't hide it →
  it lingered after Stop. `RecordingFrame.hideEvent` now hides the handle in
  lockstep. Anything that hides/shows the frame must keep the handle paired.
- **Stop button** is `recording_toolbar.StopButton` — a hand-painted raised red
  pill (gradient + top sheen at rest, brighter fill + white ring on hover, a
  real "sink" on press). Painted, not stylesheet, because stylesheet hover gave
  too-weak contrast and no press affordance. `QLinearGradient` needs **float**
  coords / `QPointF` (passing `QRect.topLeft()` → `QPoint` raised TypeError and
  crashed the first paint = record start).

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

**Current dev setup (what's actually running):** a LaunchAgent runs
`~/Documents/ScreenCapture/.venv/bin/python3 main.py` **directly from this
repo** (RunAtLoad), so editing the source here IS editing the running app. To
reload after a change:

```bash
launchctl kickstart -k gui/$(id -u)/com.screencapture.app   # restart in place
tail -f /tmp/sc_agent.log                                   # stdout/stderr + tracebacks
```

The menu-bar icon renders because the LaunchAgent launches python in the GUI
session (a double-clicked bash-script `.app` does not — see the icon gotcha).
`.pyc` caches under `__pycache__/` may be owned by the agent's user context, so
syntax-check edits with `python -B -c "import ast; ast.parse(open('f.py').read())"`
rather than `py_compile` (which tries to write the cache).

To update the **shipped** signed `.app` instead, you must rebuild (above); the
signed `.app` does not read your source tree.

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
- **Lip-sync calibration:** revisit `webcam_latency_ms` (default 160) now that
  the webcam capture is lower-latency — tune from user feedback.
- The `sc/` native rewrite is incomplete (no annotation/recording).

### Cross-platform: making this run on Windows too (a wanted future goal)
The architecture is already mostly portable; the platform-specific bits are
isolated. What a Windows pass needs:
- **Permissions/TCC:** macOS-only. `platform_utils` already branches; Windows
  has no Screen Recording/Camera consent gate, so those checks no-op.
- **Window/overlay config:** `_configure_nswindow` / `_configure_clickable_panel`
  / `setHasShadow_` / NSWindow levels are AppKit. `RecordingAnnotationOverlay`
  already has a Windows click-through branch (`WS_EX_TRANSPARENT` via ctypes);
  the other overlays (webcam, toolbar, countdown, frame) need equivalent
  always-on-top / non-activating handling on Windows (or just rely on
  `WindowStaysOnTopHint`, which mostly works).
- **The ~20px pre-show `move()` drift is macOS-only** — don't port the
  re-assert hack blindly; verify Windows placement separately.
- **Capture:** mss is cross-platform (Windows already 1x). The webcam
  `cv2.VideoCapture` config (720p30, buffersize=1) is portable (DSHOW/MSMF
  backends honor it).
- **System audio:** macOS uses `sc_audio_helper` (ScreenCaptureKit). Windows
  has only mic today; system audio needs WASAPI loopback (e.g. `soundcard`
  loopback or a small native helper). Mic via `sounddevice` is cross-platform.
- **Hotkey:** macOS uses `sc/hotkey.py` (Carbon). Windows needs a `RegisterHotKey`
  equivalent.
- **Packaging:** PyInstaller is cross-platform; the signing flow is macOS-only.
- Output dir already branches (`~/Movies` vs `~/Videos`).
