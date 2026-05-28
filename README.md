# ScreenCapture

A lightweight, native screen capture and recording app for macOS and Windows. Think LightShot + ScreenPal — built entirely in Python with PyQt6, zero Electron, zero Node.js.

## Features

**Screenshot**
- Global hotkey capture (default: F13 / PrintScreen)
- Interactive region selection with resizable handles
- Annotation toolbar: arrow, rectangle, circle, line, pen, highlighter, text
- Color picker, adjustable line width
- Copy to clipboard or save to file
- Undo/redo support

**Screen Recording**
- Region-based MP4 video recording with 3-2-1 countdown
- System audio capture via native ScreenCaptureKit helper (macOS 13+)
- Microphone capture with pro audio chain (noise gate, compression, limiter)
- Floating toolbar: pause/resume, stop, timer, mic mute, webcam toggle, draw toggle
- Live annotation overlay (draw on screen while recording)
- Circular PiP webcam overlay (draggable, camera-selectable via tray menu)
- Draggable recording region — reposition by dragging the red border
- Wall-clock PTS for perfect audio-video sync
- Mouse cursor composited into video

**Platform**
- **macOS**: menu bar app (no dock icon), Spotlight-searchable, login item auto-start. System audio via ScreenCaptureKit (macOS 13+).
- **Windows**: system tray app, Start Menu shortcut, startup shortcut. System audio not yet supported (mic only).
- Multi-monitor support

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| GUI | PyQt6 (vanilla, no QML) |
| Screen capture | mss |
| Image processing | Pillow, NumPy |
| Video encoding | PyAV (av), imageio-ffmpeg |
| Microphone | sounddevice (PortAudio) |
| System audio | Native Objective-C binary (ScreenCaptureKit) |
| Webcam | OpenCV (headless) |
| Hotkeys | pynput (macOS), ctypes/pywin32 (Windows) |
| macOS native | PyObjC (AppKit, NSWindow management) |

---

## Project Structure

> **Start here if you're new (or an AI agent): read [CLAUDE.md](CLAUDE.md)** —
> it lists the non-obvious macOS pitfalls (menu-bar icon, permissions,
> Ctrl/Cmd, dialog z-order) and how the app is actually launched.

```
ScreenCapture/
├── CLAUDE.md                # ⭐ Fast-path dev notes + macOS gotchas (read first)
├── main.py                  # Entry point — tray app, hotkey listener, orchestrator (Qt)
├── capture.py               # Low-level screen capture (mss)
├── overlay.py               # LightShot-style selection + annotation UI
├── tools.py                 # Drawing tools (arrow, rect, circle, line, pen, text)
├── recorder.py              # Video recording engine (MP4 + audio + cursor + webcam)
├── recording_toolbar.py     # Floating ScreenPal-style control bar
├── countdown.py             # 3-2-1 countdown overlay
├── webcam.py                # Circular PiP webcam capture
├── audio_helper.py          # Audio capture + processing (system + mic)
├── platform_utils.py        # macOS permission checks + Settings links + NSWindow helpers
├── mac_tray.py              # Native NSStatusItem helper (currently UNUSED; see CLAUDE.md)
├── sc/                      # Partial pure-PyObjC rewrite (NOT the live app).
│   ├── hotkey.py            #   Carbon global hotkey — USED by main.py on macOS
│   ├── capture.py           #   CGWindowListCreateImage capture (diagnostics)
│   ├── overlay.py tray.py   #   Native overlay/tray (screenshot-only; run via `python -m sc`)
│   ├── permissions.py ...   #   permissions / paths / config / clipboard
├── sc_audio_helper.m        # Native ObjC helper source (ScreenCaptureKit, macOS only)
├── sc_audio_helper          # Compiled binary (macOS only; build from .m)
├── config.json              # Persistent settings (created on first run; see config.example.json)
├── config.example.json      # Example config
├── requirements.txt         # Python dependencies
├── .gitignore
├── sync.sh                  # Sync source → app bundle (does NOT copy sc/ — see CLAUDE.md)
├── assets/
│   ├── icon.svg             # ⭐ Source of truth for the feather icon (render to PNGs)
│   ├── icon.png             # App icon (rendered from svg)
│   ├── icon_tray.png        # Menu bar icon (44px)
│   ├── icon_tray@2x.png     # Menu bar icon (88px retina)
│   └── icon.ico             # Windows icon
├── install.sh               # macOS installer
├── install.bat              # Windows installer
├── install_universal.sh     # Cross-platform installer
├── run.sh                   # macOS dev launcher
├── run.bat                  # Windows dev launcher
└── ScreenCapture.spec       # PyInstaller build spec
```

---

## Quick Start (Development)

### Prerequisites
- Python 3.10+ (3.14 recommended)
- **macOS**: 13+ (for system audio), Xcode Command Line Tools (for compiling the audio helper)
- **Windows**: 10+ (mic capture only; system audio capture not yet implemented)

### Run from source

```bash
cd /path/to/ScreenCapture

# Create venv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Compile the system audio helper (macOS only, one-time)
clang -O2 -fobjc-arc \
  -framework Foundation \
  -framework ScreenCaptureKit \
  -framework CoreMedia \
  -framework CoreAudio \
  sc_audio_helper.m -o sc_audio_helper

# Launch
python main.py
```

Or use the launcher script:
```bash
./run.sh    # macOS
run.bat     # Windows
```

---

## Installation (End User)

### macOS
```bash
./install.sh
```

This will:
1. Copy files to `~/Applications/ScreenCapture/`
2. Create a Python venv with all dependencies
3. Create `~/Applications/ScreenCapture.app` (Spotlight-searchable)
4. Register as a login item (auto-start on boot)

After installation, macOS will prompt for **Screen Recording** permission on first use.
Go to **System Settings > Privacy & Security > Screen Recording** and enable it.

### Windows
```cmd
install.bat
```

This will:
1. Copy files to `%LOCALAPPDATA%\ScreenCapture\`
2. Create a Start Menu shortcut (searchable)
3. Create a Startup shortcut (auto-start on boot)

---

## macOS App Bundle Architecture

> **This is important if you're developing the app.**

There are **two copies** of the codebase on macOS after installation:

| Location | Purpose |
|----------|---------|
| `~/Documents/ScreenCapture/` | **Development source** — where you edit code |
| `/Applications/ScreenCapture.app/Contents/Resources/app/` | **Deployed app bundle** — what runs when you open the app |

### How the app bundle works

```
/Applications/ScreenCapture.app/
  Contents/
    Info.plist                    # Bundle metadata (LSUIElement = true)
    MacOS/
      ScreenCapture               # Bash launcher (runs .venv/bin/python3 main.py)
    Resources/
      icon.png
      app/                        # <-- Full copy of the project
        *.py                      #     Python source files
        sc_audio_helper           #     Compiled native binary
        config.json
        assets/
        .venv/                    #     Self-contained virtual environment
```

The launcher script at `Contents/MacOS/ScreenCapture` runs Python with the process name "ScreenCapture" (so it appears as a native app, not "Python"):
```bash
DIR="$(dirname "$0")/../Resources/app"
cd "$DIR"
exec -a "ScreenCapture" "$DIR/.venv/bin/python3" main.py
```

### The sync problem

When you edit files in the **development source**, the **app bundle does NOT update automatically**. You're editing one copy but running another. This causes the classic "I changed the code but nothing happened" problem.

### How to sync after making changes

**Option 1: Manual sync** (quick)
```bash
# Copy updated files from source to app bundle
SRC="$HOME/Documents/ScreenCapture"
APP="/Applications/ScreenCapture.app/Contents/Resources/app"

cp "$SRC"/*.py "$APP/"
cp "$SRC/sc_audio_helper" "$APP/"
cp "$SRC/requirements.txt" "$APP/"
```

**Option 2: Use sync.sh** (recommended)
```bash
./sync.sh
```
This script quits the app, copies all files, reinstalls deps, and is ready for relaunch. Pass a custom path: `./sync.sh /path/to/ScreenCapture`

**Option 3: Develop directly in the app bundle**

Skip the two-copy problem entirely by editing files in place:
```bash
cd /Applications/ScreenCapture.app/Contents/Resources/app
# Edit files here, then just relaunch the app
```

**Option 4: Symlink the app bundle to source** (advanced)

Replace the app bundle's `app/` folder with a symlink to your dev source:
```bash
# Back up the bundle's venv first
mv /Applications/ScreenCapture.app/Contents/Resources/app/.venv /tmp/screencap-venv

# Replace with symlink
rm -rf /Applications/ScreenCapture.app/Contents/Resources/app
ln -s "$HOME/Documents/ScreenCapture" /Applications/ScreenCapture.app/Contents/Resources/app

# Restore venv into source dir (if it doesn't already have one)
mv /tmp/screencap-venv "$HOME/Documents/ScreenCapture/.venv"
```

Now editing the source and running the app always use the same files. The `.venv` lives inside your source directory and is used by both `run.sh` and the app bundle.

> **Recommendation:** Option 4 (symlink) is the cleanest for active development. Option 2 (sync.sh) is best for occasional changes.

---

## Windows Support

Windows is partially supported. The following works today:

| Feature | Status |
|---------|--------|
| Screenshot (region select, annotations, copy/save) | ✅ Works |
| Screen recording (video + cursor) | ✅ Works |
| Microphone capture | ✅ Works |
| Webcam PiP overlay | ✅ Works |
| Live drawing during recording | ✅ Works |
| Global hotkey (via ctypes/pywin32) | ✅ Works |
| System tray, Start Menu, Startup shortcut | ✅ Works |
| **System audio capture** (browser, apps, etc.) | ❌ Not implemented |

### What still needs to be done (Windows)

**1. System audio capture**

On macOS, system audio uses ScreenCaptureKit via a native Objective-C helper. Windows has no direct equivalent. Options for implementation:

- **Windows Audio Session API (WASAPI)** — `pywin32` or `comtypes` can access `IAudioCaptureClient` to capture the default loopback device (what you hear). Libraries like `sounddevice` or `pyaudioworkpiece` may help.
- **OBS-style approach** — Use `obs-python` or similar, but adds heavy dependencies.
- **NAudio** — .NET library; would require a separate helper process or Python bindings.
- **Reference**: The macOS flow in `audio_helper.py` → `sc_audio_helper` subprocess → raw PCM file. A Windows helper could follow the same pattern: subprocess writes raw PCM, `audio_helper.py` reads and mixes with mic.

**2. Platform-specific code locations**

| File | Windows-specific logic |
|------|------------------------|
| `main.py` | `_setup_hotkey_windows()` (RegisterHotKey), DPI scaling env vars, `SetProcessDpiAwarenessContext` |
| `recorder.py` | `set_drawing_active()` uses `WS_EX_TRANSPARENT` via `ctypes.windll.user32` for click-through overlay |
| `audio_helper.py` | `SystemAudioCapture` raises `ImportError` on non-macOS; add a Windows implementation that returns empty or uses a loopback capture |

**3. Testing on Windows**

- Run `install.bat` to install to `%LOCALAPPDATA%\ScreenCapture\`
- No native binary to compile (unlike macOS)
- Use `run.bat` for development
- Check that `config.json` is created in the install directory

**4. DPI / scaling**

`main.py` sets `QT_AUTO_SCREEN_SCALE_FACTOR=0` and related env vars on Windows to avoid zoomed/blurry UI. If you see scaling issues, verify these are applied before `QApplication` is created.

---

## System Audio on macOS

System audio capture (recording sounds from other apps, browser videos, etc.) uses a **native Objective-C helper binary** instead of PyObjC.

### Why not PyObjC?

PyObjC's ScreenCaptureKit audio bindings are broken on macOS 15 (error -3805). The workaround is a small native binary that interfaces with ScreenCaptureKit directly.

### How it works

1. `audio_helper.py` launches `sc_audio_helper` as a subprocess
2. The helper captures system audio via `SCStream` — ScreenCaptureKit delivers **non-interleaved (planar) Float32** audio
3. The helper detects the format via `CMAudioFormatDescriptionGetStreamBasicDescription`, interleaves the channels `[L,R,L,R,...]`, and writes raw float32 stereo PCM (48 kHz) to a temp file
4. When recording stops, `audio_helper.py` sends SIGTERM to the helper
5. The raw PCM is read back, processed (noise gate, compression, limiting), and muxed into the MP4

> **Important**: The audio format detection is critical. ScreenCaptureKit always delivers planar audio — without interleaving, the output sounds distorted ("alien voice"). The helper logs the actual format on the first sample via stderr for debugging.

### Recompiling the helper

If you modify `sc_audio_helper.m`:
```bash
clang -O2 -fobjc-arc \
  -framework Foundation \
  -framework ScreenCaptureKit \
  -framework CoreMedia \
  -framework CoreAudio \
  sc_audio_helper.m -o sc_audio_helper
```

Requires Xcode Command Line Tools (`xcode-select --install`).

---

## Audio Processing Chain

The microphone audio goes through a professional-grade processing pipeline (similar to OBS/ScreenFlow):

```
Microphone → Noise Gate (threshold: -50dB, hold: 200ms)
           → Soft Compressor (threshold: -24dB, ratio: 2.5:1, makeup: +12dB)
           → Mix with System Audio (mic at 1.2x, system at 1.0x)
           → Stereo Limiter (ceiling: -1dB)
           → Peak Normalize (-0.5dB)
           → AAC encode → MP4
```

---

## macOS Window Management

The recording overlay (red border), toolbar, and webcam preview all need special NSWindow configuration to:
- Stay visible above all other windows
- Not steal focus from the app being recorded
- Allow click-through (for the border overlay) or accept clicks (for the toolbar)
- Not disappear when clicking other apps

This is handled by `_configure_nswindow()` in `recorder.py`:
```python
nswindow.setIgnoresMouseEvents_(click_through)   # Click-through for overlays
nswindow.setLevel_(25)                            # Above all windows
nswindow.setHidesOnDeactivate_(False)             # Stay visible always
nswindow.setCollectionBehavior_(                  # Visible on all Spaces
    CanJoinAllSpaces | Stationary | IgnoresCycle
)
```

### Critical: Transparent windows must NOT accept mouse events

On macOS, a frameless `QWidget` with `WA_TranslucentBackground` that accepts mouse events (`setIgnoresMouseEvents_(False)`) will crash with `SIGSEGV` in `-[NSView window]` because transparent pixels receive events but the underlying `NSView` has no valid window reference.

**Rule**: Any overlay with large transparent areas (recording border, annotation overlay) must be **click-through**. Interactive widgets (toolbar, webcam preview, drag handles) must either:
1. Be fully opaque, or
2. Use `QWidget.setMask(QRegion)` to clip the window shape to only the visible area, or
3. Defer `_configure_nswindow` via `QTimer.singleShot(0, ...)` to ensure the native window is fully constructed

---

## Closing / Stopping the App

**Always prefer the normal quit path** — it avoids killing other apps by mistake.

### Proper way to close ScreenCapture

1. **Tray menu → Exit** — Click the ScreenCapture icon in the menu bar (macOS) or system tray (Windows), then choose **Exit**. This shuts down the app cleanly.

2. **Graceful quit from terminal** (macOS):
   ```bash
   osascript -e 'quit app "ScreenCapture"'
   ```
   This sends a normal quit to ScreenCapture only. Safe to run even if the app is not running.

### ⚠️ Never use broad `pkill` patterns

**Do NOT run** `pkill -f "main.py"` — it matches any process with `main.py` in its command line, including Cursor, VS Code extension hosts, and other Python apps. It can crash your IDE or other tools.

### If you must force-quit from terminal (e.g. for sync scripts)

Use a pattern that matches **only** ScreenCapture:

```bash
# macOS app bundle — matches the .app path
pkill -f "ScreenCapture.app" 2>/dev/null

# Or quit gracefully first (recommended)
osascript -e 'quit app "ScreenCapture"' 2>/dev/null
sleep 2
# Force-quit only if still running
pkill -f "ScreenCapture.app" 2>/dev/null
```

When writing scripts, avoid `pkill -f "main.py"` or `pkill -f "Python"` — these can terminate Cursor, Claude, and other applications.

---

## Configuration

Settings are stored in `config.json`:
```json
{
  "hotkey_name": "F13"
}
```

Available hotkeys (configurable via tray menu):
`Print`, `F1`–`F20`, `Pause`, `Scroll Lock`, `Insert`, `Home`

---

## Permissions (macOS)

The app needs these permissions (prompted automatically on first use):

| Permission | Why |
|-----------|-----|
| Screen Recording | Capture screen content |
| Microphone | Record voice audio |
| Accessibility | Global hotkey listener (pynput) |
| Camera | Webcam PiP overlay |

Go to **System Settings > Privacy & Security** to manage these.

---

## Troubleshooting

### "I changed the code but nothing happened"
You're probably editing the source in `~/Documents/ScreenCapture/` but running from the app bundle at `/Applications/ScreenCapture.app/`. See [the sync section](#the-sync-problem) above.

### The red recording border disappears when I click
This was a bug caused by `NSWindow.hidesOnDeactivate` defaulting to `True`. Fixed by explicitly setting `setHidesOnDeactivate_(False)` in `_configure_nswindow()`.

### SIGSEGV crash when clicking during recording (NSView window null)
This was caused by making the `RecordingFrame` (the red border overlay) accept mouse events (`setIgnoresMouseEvents_(False)`). On macOS, a frameless window with `WA_TranslucentBackground` that accepts mouse events crashes in `-[NSView window]` because transparent pixels receive events but the NSView's window reference is null. **Fix**: The recording border is now fully click-through. A separate small drag handle widget (with a proper window mask) sits above the border for repositioning — only the opaque grip area receives events.

### SIGSEGV in PortAudio / ffi_closure on audio IO thread
**Root cause**: sounddevice's callback mode wraps a Python function via cffi into a C function pointer. CoreAudio's real-time IO thread calls this C pointer. If the Python closure is garbage-collected, or during teardown races, the pointer becomes dangling → `SIGSEGV` in `ffi_closure_SYSV_inner` at address `0x4` (null deref). Both crash reports (Thread 6 and Thread 13) showed the identical backtrace: `ffi_closure_SYSV_inner → AdaptingInputOnlyProcess → PaUtil_EndBufferProcessing → AudioIOProc → HALC_ProxyIOContext::IOWorkLoop`.

**Fix (current)**: Replaced callback mode entirely with **blocking reads on a Python thread**. `MicCapture` now opens a `sounddevice.InputStream` *without* a callback and runs `stream.read(1024)` in a `_read_loop()` on a daemon thread. No cffi closure = no C function pointer = no SIGSEGV. The `stop()` method sets a flag, joins the thread, then calls `stream.abort()` + `stream.close()`.

**Previous partial fix (insufficient)**: Removed `threading.Lock` from the callback (CPython's GIL makes `list.append` atomic). This helped with deadlocks but did not prevent the cffi pointer invalidation crash.

### System audio sounds distorted / "alien voice"
ScreenCaptureKit delivers audio as **non-interleaved (planar) Float32**. If the helper writes raw bytes without interleaving, the Python side misreads the channel layout. Fixed in `sc_audio_helper.m` by detecting the format via `CMAudioFormatDescriptionGetStreamBasicDescription` and interleaving before writing. Look for the `FORMAT:` line in stderr output to verify the detected format.

### System audio not captured
- Requires macOS 13+ (Ventura or later)
- The `sc_audio_helper` binary must be compiled and present next to `main.py`
- Check that Screen Recording permission is granted
- Run from terminal to see error output: `.venv/bin/python3 main.py`

### Voice audio drops to silence after the first second
This was caused by aggressive per-chunk `peak_normalize` — a single loud transient at recording start would set the normalization ceiling too high, making subsequent quiet speech inaudible. Fixed by only normalizing the final mixed audio, not individual chunks.

### Voice too quiet / drowned out by system audio
The mic goes through a noise gate, compressor, and mix stage. Current tuning: gate at -50dB (conservative), compressor makeup +12dB, mic mixed at 1.2x vs system at 1.0x. Adjust `mix_and_master()` in `audio_helper.py` if the balance is off for your setup.

### Video plays faster than audio (out of sync)
This was caused by the video encoder assuming fixed 30fps timing while actual screen capture ran slower (~25fps due to encoding overhead). Fixed by setting explicit PTS (presentation timestamps) on each video frame based on wall-clock elapsed time instead of sequential frame numbers. The key line in `recorder.py` is:
```python
video_frame.pts = int(active_elapsed * self.fps)
```

### Webcam doesn't show face
- Check **Camera** submenu in the tray menu to select the correct camera
- On macOS, grant Camera permission in System Settings > Privacy & Security
- If index 0 fails, the app automatically tries other camera indices as fallback
- Run from terminal to see `[webcam]` debug messages

### "This process is not trusted!" on launch
This message comes from macOS IOKit when pynput tries to create a Quartz event tap without Accessibility permission. It's printed to stderr from C code — Python `redirect_stderr` can't catch it. **Fix**: `main.py` temporarily redirects OS-level fd 2 to `/dev/null` around `keyboard.Listener.start()` with a 0.3s hold for the background thread. The hotkey may still work via IOHIDManager fallback even without Accessibility permission. To fully fix: grant Accessibility permission in **System Settings > Privacy & Security > Accessibility** for the Python binary or app bundle.

### Duplicate ObjC class warnings (AVFFrameReceiver)
You may see: `Class AVFFrameReceiver is implemented in both ...av/.dylibs/libavdevice... and ...cv2/.dylibs/libavdevice...`. This is a conflict between PyAV and OpenCV bundling different versions of FFmpeg's libavdevice. It can cause "spurious casting failures and mysterious crashes." **Status: unresolved.** Potential fixes: (1) use `opencv-python-headless` to avoid FFmpeg conflicts, (2) pin compatible versions, or (3) remove one library's dylib symlink.

### OpenCV camera not authorized
`OpenCV: not authorized to capture video (status 0)` means the Python process hasn't been granted Camera permission. When running from source (`python main.py`), grant Camera access to the `Python` binary in **System Settings > Privacy & Security > Camera**. When running from the app bundle, grant it to `ScreenCapture.app`.

### System audio helper doesn't signal READY
`sc_audio_helper did not signal READY` means the native helper exited or timed out. Common causes:
- Binary not compiled — run the `clang` command from Quick Start
- Screen Recording permission not granted to the Python process
- macOS version < 13 (ScreenCaptureKit unavailable)
- Binary compiled for wrong architecture (Intel vs ARM) — recompile on your machine

### Mouse cursor not visible in recording
The cursor is composited as an overlay on each frame. Ensure the recording region coordinates are correct and that `DPR` (device pixel ratio) is being detected properly for Retina displays.

### App doesn't appear in Dock
By design. `LSUIElement = true` in `Info.plist` makes it a menu bar-only app. Look for the tray icon in the macOS menu bar (top-right).

### ⭐ Menu-bar icon doesn't appear when I double-click the app (but the app is running)
This is the #1 historical gotcha. When the `.app` is launched via Finder/`open`, its `Contents/MacOS/ScreenCapture` **bash launcher** `exec`s Python, and the resulting process doesn't attach to the GUI session properly — so `QSystemTrayIcon` reports itself visible but renders **nothing** (this also affected the PyInstaller build). Launched **directly from a shell or via a LaunchAgent**, the icon renders fine.

**Fix in use:** a LaunchAgent (`~/Library/LaunchAgents/com.screencapture.app.plist`, `RunAtLoad=true`) runs Python directly in the user's session and auto-starts at login. Do **not** rely on double-clicking the bundle. Manage with:
```bash
launchctl bootstrap  gui/$(id -u) ~/Library/LaunchAgents/com.screencapture.app.plist
launchctl kickstart -k gui/$(id -u)/com.screencapture.app   # restart after a code change
launchctl bootout    gui/$(id -u)/com.screencapture.app     # stop/uninstall
```
The clean long-term fix is a **code-signed `.app` with a compiled (non-script) launcher**. See [CLAUDE.md](CLAUDE.md).

### "python3 can run in the background" macOS notice
Shown once by macOS when the LaunchAgent is first registered (it's a new login item). Harmless. It says "python3" rather than "ScreenCapture" because we run the Python runtime directly — a signed bundle would fix the label. Manage under System Settings → General → Login Items & Extensions.

### ⌘C / ⌘S / ⌘Z do nothing in the capture overlay
Qt swaps Ctrl/Cmd on macOS — Command arrives as `Qt.ControlModifier`. `overlay.py` now accepts both modifiers and sets `StrongFocus`. (Fixed 2026-05-28.)

### Color picker (or any dialog) opens but I can't see it
It was opening *behind* the full-screen overlay (window level 25). The overlay now drops to level 0 while a dialog is open and restores after. (Fixed 2026-05-28.)

### A text annotation alone isn't in the saved screenshot
In-progress text wasn't committed when clicking the toolbar Copy/Save buttons. `_get_result_image()` now finalizes text first. (Fixed 2026-05-28.)

---

## Known Issues / Remaining Work

> Several issues below were addressed on **2026-05-28** (⌘-shortcuts, color-picker
> z-order, lone-text save, camera-permission request flow, screen-recording
> permission guard, menu-bar icon via LaunchAgent, feather icon). See
> [CLAUDE.md](CLAUDE.md) for current state. The remaining open items:

These were open issues as of 2026-03-17; some are now fixed (see note above):

### Windows
- **System audio capture** — Not implemented. See [Windows Support](#windows-support) above for implementation options (WASAPI, loopback capture).

### Stability
1. **Duplicate ObjC class conflict** — PyAV and OpenCV both bundle `libavdevice` with the same ObjC class names (`AVFFrameReceiver`, `AVFAudioReceiver`). macOS warns this "may cause spurious casting failures and mysterious crashes." This could be the source of remaining instability. Fix: switch to `opencv-python-headless` or resolve the dylib conflict.
2. **PortAudio SIGSEGV** — Fixed by switching to blocking reads (no cffi callback). If crashes recur, check crash reports for `ffi_closure_SYSV_inner` — it means the old callback approach was reverted somehow.
3. **NSView window null crash** — Fixed by making `RecordingFrame` click-through with a separate masked `_DragHandle`. If crashes recur, check for any widget with `WA_TranslucentBackground` + `setIgnoresMouseEvents_(False)` that has transparent areas.

### Permissions (macOS)
4. **Camera permission** — OpenCV requires Camera access for the Python binary (or app bundle). Without it, webcam toggle silently fails.
5. **Screen Recording permission** — Required for both mss screen capture and the `sc_audio_helper`. Without it, the helper can't capture system audio.
6. **Accessibility permission** — Required for pynput global hotkey. Without it, hotkeys won't work (but the app is still usable via tray menu).

### Features to polish
7. **Recording region drag** — The `_DragHandle` widget works but could be more discoverable. Consider adding a tooltip or visual indicator.
8. **Recording save** — If the process crashes mid-recording, no video is saved. Consider writing to a recoverable temp file.
9. **Error feedback** — Many failures (camera denied, audio helper missing) are logged to stderr but not shown to the user. Add tray notifications for permission errors.

### Development workflow
10. **Two-copy problem** — See [the sync section](#the-sync-problem). Consider using the symlink approach (Option 4) for active development.
11. **Process detection** — When stopping ScreenCapture from scripts, use `osascript -e 'quit app "ScreenCapture"'` or `pkill -f "ScreenCapture.app"`. **Never use** `pkill -f "main.py"` — it kills Cursor, Claude, and other Python apps.

---

## License

Private / All rights reserved.
