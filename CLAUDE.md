# CLAUDE.md - working notes for ScreenCapture

A LightShot / Loom-style screenshot + screen-recording tray app for Windows
and macOS. PyQt6 UI, native bits via ctypes (Windows) and PyObjC (macOS).
ONE codebase for both laptops: every OS branch lives in platform_utils.py
(IS_WINDOWS / IS_MACOS) and the few helpers there. Do not add sys.platform
checks elsewhere.

This file is the fast path. It captures the non-obvious things that cost
hours to rediscover. Read it before changing capture, the overlay, the tray,
recording, audio or packaging. Plain ASCII only in code and docs (project
standard).

---

## Run it (development)

Windows (this laptop, global Python 3.12 has the packages):
```
python main.py            # or run.bat; run_hidden.bat = no console
python doctor.py          # dependency / screen / audio / camera self-check
```
macOS:
```
cd ~/Documents/ScreenCapture
.venv/bin/python main.py  # launch from a SHELL - see "menu-bar icon" below
```

- Single instance is enforced by binding TCP 127.0.0.1:47392 (override with
  env SCREENCAPTURE_LOCK_PORT for a side-by-side test instance). A second
  launch shows a "already running" balloon and exits.
- Windows: NEVER use tasklist / Get-Process / taskkill to find or stop a
  running copy (corporate endpoint security tickets, see INSTRUCTIONS.txt
  section 16). Quit it from the tray menu; scripts check the lock port.
- Logs: stdout/stderr. LaunchAgent on macOS: /tmp/sc_agent.log.

## Dependencies

requirements.txt, with platform markers. Windows extras: pywin32 (Startup
shortcut via WScript.Shell), SoundCard (WASAPI loopback), pygrabber +
comtypes (DirectShow camera names). macOS extras: pyobjc frameworks. ffmpeg
comes from imageio-ffmpeg (no system ffmpeg needed). cv2 can be either
opencv-python or opencv-python-headless, never both in one environment.

Windows install layout: install.bat copies the app to %LOCALAPPDATA%\
ScreenCapture and creates a private .venv THERE (outside OneDrive), then
Start Menu + Startup shortcuts pointing at .venv\Scripts\pythonw.exe main.py.
No PyInstaller exe by default: unsigned executables get flagged by
SentinelOne; python.exe is signed by the PSF.

---

## Architecture

1. The Qt app (both platforms): main.py + overlay.py + tools.py + recorder.py
   + recording_toolbar.py + setup_panel.py + countdown.py + webcam.py +
   audio_helper.py + platform_utils.py + app_config.py + capture.py.
2. sc/ - a partial pure-PyObjC rewrite (macOS). NOT the app. main.py only
   borrows sc/hotkey.py (Carbon global hotkey). `python -m sc` runs the
   native screenshot-only core; it lacks annotations and recording.
3. mac_tray.py - unused native NSStatusItem helper, kept for reference.

Settings: app_config.py is the single store (config.json next to main.py).
The overlay's "default color" and the recording draw-panel color share the
same key now (they used to be two stores).

---

## Coordinate spaces (Windows high-DPI and Retina)

- Qt geometry is LOGICAL (scaled) pixels. mss captures PHYSICAL pixels.
- Windows, verified 2026-09-03 on a 150 percent laptop screen + two 100
  percent monitors: each QScreen's logical top-left EQUALS its physical
  top-left; only the size differs by devicePixelRatio(). So
  platform_utils.physical_screen_rect() matches the mss monitor by origin
  and returns its physical rect. Never multiply a screen origin by the DPR.
- main._do_capture: capture the physical rect, capture_dpr = shot.width /
  geo.width(), remember (physical origin, dpr). main._to_physical() converts
  any logical rect: phys_origin + (logical - geo.origin) * dpr.
- overlay: screenshot.setDevicePixelRatio(capture_dpr) -> painter.drawPixmap
  (0, 0, pixmap) draws it at logical size, crisp. _get_result_image renders
  at physical size: translate(-phys_x, -phys_y) in device pixels THEN
  scale(dpr), integer crop origin (no half-pixel resampling), annotations in
  logical units on top. The pixel-size label shows the physical size.
- Legacy escape hatch: config windows_dpi_scaling = false restores the old
  1:1 physical-pixel Qt (tiny UI, dpr 1).
- The old (Jan 2026) Windows build forced Qt to 1:1 via QT_* env vars; the
  old overlay drew `drawPixmap(rect, screenshot, rect)` which is wrong for any
  dpr != 1 - that is why macOS high_res_screenshots looked "zoomed".

## Windows gotchas (each cost real time on 2026-09-03)

- Click-through overlays (red recording border, drawing layer) need
  WS_EX_TRANSPARENT on the native window; Qt's WA_TransparentForMouseEvents
  does nothing for top-level windows. platform_utils.set_click_through().
- Floating controls must not steal focus from the app being recorded:
  Qt.WindowType.WindowDoesNotAcceptFocus (= WS_EX_NOACTIVATE), set BEFORE
  show. platform_utils.make_non_activating(). Buttons still get clicks.
- RegisterHotKey must be called on the thread that pumps its messages; the
  WindowsHotkey class owns a daemon thread and posts WM_QUIT to it before
  re-registering. The old build leaked the previous key on every change.
  Modifiers are supported (Ctrl+Shift+S, Win+F9). MOD_NOREPEAT avoids
  auto-repeat firing. PrintScreen (VK 0x2C) wins over the Snipping Tool
  binding because RegisterHotKey is processed before the shell.
- WASAPI loopback (SoundCard) delivers NOTHING while no app is playing
  audio, so the captured track would be shorter than the video and drift.
  audio_helper._WindowsLoopbackAudio plays digital silence on the default
  output (sounddevice OutputStream, blocking writes on a thread) for the
  whole recording, and get_audio_stereo() pads any remaining stall with
  zeros from wall-clock chunk timestamps. Loopback thread needs
  pythoncom.CoInitialize().
- pygrabber (DirectShow device names) must run on ITS OWN thread with
  CoInitialize: after SoundCard has initialised COM in a different apartment
  you get "Cannot change thread mode after it is set". webcam.
  _list_cameras_windows() does that; results are cached (enumeration is
  slow) with a "Refresh list" menu item.
- cv2.VideoCapture(idx, CAP_DSHOW) opens in ~3-4 s and honours 720p30 +
  BUFFERSIZE=1; the default MSMF backend is slower and ignores the buffer
  size. The preview shows "Starting camera..." until the first frame.
- mss on Windows uses BitBlt with CAPTUREBLT, so layered (translucent) Qt
  windows ARE captured: the webcam circle and the drawings appear in the
  video by being on screen (same WYSIWYG design as macOS).
- Videos / Pictures folders: use SHGetKnownFolderPath (platform_utils).
  Corporate OneDrive "Known Folder Move" redirects Pictures into OneDrive;
  ~/Videos may not exist.
- Toast identity: SetCurrentProcessExplicitAppUserModelID so balloons say
  ScreenCapture, not Python. Clicking the balloon reveals the saved file
  (tray.messageClicked).
- Tray menu styling: QMenu stylesheet in main._menu_stylesheet(); Qt's
  default Windows menu looks dated. Keep it opaque (no translucent rounded
  menus: artifacts on Windows 11).
- Do not put a .venv inside the OneDrive project folder (sync storm). Dev
  copy runs on the global Python 3.12 like every other project; the
  installed copy has its venv in %LOCALAPPDATA%.

## Testing without pressing the hotkey

A harness that renders the overlay to PNG without showing it (used for the
DPI work; adapt as needed):
```python
from PyQt6.QtWidgets import QApplication; app = QApplication([])
from PyQt6.QtGui import QGuiApplication, QColor
from PyQt6.QtCore import QRect, QPoint
from platform_utils import physical_screen_rect
from capture import capture_region
from overlay import OverlayWindow
from tools import DrawingAction
s = QGuiApplication.screens()[0]; geo = s.geometry()
px, py, pw, ph = physical_screen_rect(s); shot = capture_region(px, py, pw, ph)
ov = OverlayWindow(shot, geo.x(), geo.y(), shot.width / geo.width()); ov.setGeometry(geo)
ov.selection_rect = QRect(180, 140, 640, 420); ov.selection_complete = True; ov._create_toolbars()
ov.actions.append(DrawingAction("blur", QColor("#000"), rect=QRect(300, 300, 200, 80)))
ov.grab().save("overlay.png"); ov._get_result_image().save("result.png")
```
The recorder can be exercised synchronously: build ScreenRecorder(region,
path, system_audio=True), threading.Timer(4, rec.stop).start(), rec.run(),
then `ffmpeg -i path` (binary from imageio_ffmpeg.get_ffmpeg_exe()) should
list h264 video + aac stereo audio.

End-to-end on Windows: start `python main.py` with env
SCREENCAPTURE_LOCK_PORT=47393 and a config hotkey nobody uses (F9), then
send the key with SendInput from another script; the overlay appears on the
screen under the mouse. Esc closes it.

---

## macOS gotchas (every one of these cost real debugging time)

### Menu-bar icon does not appear when launched (historical)
- Symptom: app runs (holds the lock port) but no menu-bar icon.
- Causes seen: (1) a .app whose MacOS/ScreenCapture is a bash script that
  execs Python - LaunchServices launch doesn't attach the GUI session, so
  QSystemTrayIcon reports visible but renders nothing; (2) a PyInstaller
  onefile .app - cold-starts too slowly / unpacks to a random /tmp dir.
- Solution (current): ship a PyInstaller onedir, self-signed .app (compiled
  bootloader at a fixed path). Its tray icon renders on double-click, AND
  the fixed-path signed binary gives a stable TCC identity so Screen
  Recording / Camera grants stick. Build with `./build.sh`.

### Capture shows the desktop wallpaper, not the windows
- Cause: missing Screen Recording permission. macOS silently returns only
  wallpaper + own windows. main.py checks platform_utils.
  has_screen_recording_permission() before capturing and guides the user.
- TCC ties the grant to the binary identity. Running as bare python3 means
  the grant attaches to the Python runtime, not "ScreenCapture".

### Screenshot resolution (Retina quality)
- Default capture = mss -> screenshots at the familiar on-screen size.
- Opt-in config high_res_screenshots = true switches _do_capture to
  sc.capture.grab (CGWindowListCreateImage), true 2x. The overlay now tags
  the pixmap with the DPR so it displays at the right size; the PNG is still
  2x the pixel dimensions (that was the "zoomed" complaint). Not re-verified
  on a Mac since the 2026-09-03 refactor - check before relying on it.

### Cmd+C / Cmd+S / Cmd+Z don't fire in the overlay
- Qt swaps Ctrl/Cmd on macOS: Command arrives as Qt.ControlModifier.
  overlay.MODIFIER_KEY accepts both, the overlay sets StrongFocus.
- Same swap in the hotkey dialog: a recorded "Ctrl+..." means Command on
  macOS and is mapped to Carbon CMD_KEY (main._setup_hotkey_macos);
  "Meta" is the physical Control key.

### Color picker / any dialog opens behind the overlay
- The overlay sits at NSWindow level 25; dialogs open at the normal level.
  overlay._set_overlay_level(0) while a dialog is open, 25 after.

### A lone text annotation is missing from the saved screenshot
- _get_result_image() commits in-progress text first.

### Webcam fails / "No cameras detected"
- OPENCV_AVFOUNDATION_SKIP_AUTH=1 is set, so we request Camera permission
  ourselves via AVFoundation when the webcam is toggled.
- list_cameras() enumerates via AVFoundation (works without permission);
  pyobjc-framework-AVFoundation MUST be installed.
- Camera permission is per binary identity: reliable only under the signed
  bundle.

### Webcam looks laggy / jaggy
- Ask the camera for 720p30 with a 1-frame buffer (WebcamCapture.
  _open_and_configure); the preview repaints on frame_ready, not a timer.
- The webcam is screen-captured (the preview circle is part of the
  framebuffer), NOT composited: compositing gave a second offset circle.

### Frameless overlay lands ~20px too high (pre-show move() drifts)
- A move() issued before the native window is shown drifts upward ~20px.
  WebcamPreviewWidget re-asserts _target_pos in _apply_nswindow. macOS
  only: do not port the re-assert blindly (Windows placement is exact).

### Overlays revealing the desktop / switching Spaces
- Every overlay shown during capture/recording is created with
  WA_ShowWithoutActivating and its NSWindow set to CanJoinAllSpaces (+ level)
  before / at show. Capture the pixels BEFORE showing any window.

### Notifications
- Rare and meaningful. No startup toast. Recording end reveals the file in
  Finder instead of a banner. (Windows shows a click-to-open toast after
  Save; macOS does not.)

---

## Recording pipeline (works; verified end-to-end on both platforms)

recorder.ScreenRecorder (QThread): mss grabs frames -> stamps cursor
(NSEvent.mouseLocation on macOS, GetCursorPos on Windows, both mapped into
the physical region) and drawings -> queue -> encoder thread (PyAV libx264
into a temp MKV; the MP4 muxer hits errno 22 on macOS) -> ffmpeg remuxes to
MP4 and muxes AAC. Audio: computer audio via sc_audio_helper (macOS
ScreenCaptureKit) or WASAPI loopback (Windows), mic via sounddevice blocking
reads, mixed in audio_helper.mix_and_master. Output: ~/Movies/ScreenCapture
or Videos\ScreenCapture, revealed on stop.

Smoothness: capture loop only grabs + composites + queues; a bounded queue
absorbs encode bursts; a momentarily full queue drops the frame.

Audio mix - deliberately simple / Loom-style, for CLEAN audio. Do NOT re-add
fancy dynamics; each caused a real complaint:
- NO sidechain ducking (pumps). NO hard noise gate (clips word endings).
- NO tiny mic buffer (drops samples). Mic uses a roomy 2048-sample buffer.
- Gentle mic compression + system audio at a fixed 0.45 + limiter + ONE
  static normalize.

A/V sync - automatic, NO manual offset menu. Per-source start-time alignment
(_align in recorder.run), webcam_latency_ms (default 160 when the webcam is
on; recalibrate from feedback: voice BEFORE lips -> lower), anti-drift pad /
trim to the video length.

Recording size: config recording_size (native / 1080p / 720p).

Recording UI flow (Loom-grade, explicit asks): SetupPanel (camera / mic /
computer-audio toggles, draggable card) -> CountdownOverlay centered on the
region -> RecordingToolbar. Stop must feel INSTANT: hide every overlay
immediately, finalize on the recorder thread, tear down in
_on_recording_stopped. RecordingFrame._handle is a separate top-level
window; hideEvent hides it in lockstep. StopButton is hand-painted;
QLinearGradient needs float coords.

---

## The icon (purple feather)

Source of truth: assets/icon.svg. Rendered PNGs: icon.png (512), icon_tray
(44 / 88). icon.ico for Windows (tray + shortcuts), icon.icns for the .app.
It's a colored icon: never setIsMask(True).

---

## Building the app (macOS, the shipped artifact)

PyInstaller onedir .app signed with a stable self-signed certificate so TCC
grants persist across rebuilds for free.

One-time cert (GUI): Keychain Access > Certificate Assistant > Create a
Certificate: name LocalAppDev, Self-Signed Root, Code Signing, ~3650 days,
then Trust > Code Signing: Always Trust.

```
./build.sh          # icns -> PyInstaller onedir -> sign_app.sh -> ~/Applications
tccutil reset ScreenCapture com.screencapture.app   # first run only
tccutil reset Camera        com.screencapture.app
open ~/Applications/ScreenCapture.app
```
onedir is essential; run from ~/Applications, never dist/; keep the spec's
hiddenimports in sync when adding lazy imports. Auto-start: Login Item OR
LaunchAgent, never both (two instances fight over the hotkey).

Dev iteration on the Mac: the LaunchAgent runs ~/Documents/ScreenCapture/
.venv/bin/python3 main.py directly from the repo; `launchctl kickstart -k
gui/$(id -u)/com.screencapture.app` restarts it; tail -f /tmp/sc_agent.log.

Windows build (optional): `pyinstaller ScreenCapture.spec` -> dist/
ScreenCapture/ (onedir). Unsigned exe = endpoint-security risk; prefer
install.bat.

---

## Known remaining work
- macOS side of the 2026-09-03 refactor (platform_utils.set_click_through
  keeps the old _configure_nswindow recipe; overlay DPR tagging; hotkey
  modifiers) is untested on a Mac until the next pull there. Smoke test:
  hotkey, overlay, Cmd+C, color picker, record with webcam, stop.
- Lip-sync calibration: webcam_latency_ms 160 may be a touch high.
- Windows: countdown Esc does not cancel (non-activating overlay).
- The sc/ native rewrite is incomplete (no annotation / recording).
