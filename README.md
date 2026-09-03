# ScreenCapture

## Project Standards
This project follows the RM Science & Innovation Python project standards.
See: C:\Users\AC245293\OneDrive - Air Canada\RM Science & Innovation\04_Science_Team_Members\Rafael\INSTRUCTIONS.txt

(It is a desktop app, not a notebook analysis, so the notebook / queries /
data layout does not apply. The rest does: plain-English README, ASCII-only
code, .vscode/settings.json, hard-won lessons written down the same day.)

## 1. What is this?

A lightweight screenshot and screen-recording tool that lives in the system
tray (Windows) or menu bar (macOS). Press one key anywhere, drag a rectangle,
then copy it, annotate it, save it, or record that area as a video with your
voice, the computer's audio and a small round webcam picture. Think LightShot
for the screenshot part and Loom for the recording part, written in Python
with PyQt6. One codebase runs on both laptops.

## 2. Who asked for it and why

Personal productivity tool for Rafael's Windows work laptop and personal Mac.
The corporate laptop cannot install LightShot / Loom, and the built-in
Snipping Tool has no annotations worth using and no quick recording flow.

## 3. Features

| Feature | Windows | macOS |
|---|---|---|
| Global hotkey (default PrintScreen / F13), changeable, modifiers allowed | yes | yes |
| Region selection with move / resize handles, exact pixel size shown | yes | yes |
| Tools: pen, line, arrow, rectangle, ellipse, highlighter, blur, text | yes | yes |
| Color palette + hex + native picker, "use as default" | yes | yes |
| Undo / redo, whole-screen select (Ctrl+A), copy (Enter), save (Ctrl+S) | yes | yes |
| Crisp UI and full-resolution output on 125-200 percent scaled screens | yes | yes (Retina) |
| Recording: setup card, 3-2-1 countdown, floating toolbar, pause, timer | yes | yes |
| Microphone (clean Loom-style mix) | yes | yes |
| Computer audio (what you hear) | yes (WASAPI loopback) | yes (macOS 13+, native helper) |
| Round draggable webcam picture, real camera names | yes | yes |
| Draw on screen while recording, mouse cursor in the video | yes | yes |
| Drag the recording area while recording | yes | yes |
| Saved file revealed in Explorer / Finder when you stop | yes | yes |
| Start at login toggle in the tray menu | yes | Login Item / LaunchAgent |

## 4. Quick start

### Windows (this laptop)

Install (copies the app to %LOCALAPPDATA%\ScreenCapture with its own private
Python environment, adds a Start Menu entry and starts it at login):

```
install.bat
```

Then press PrintScreen. The purple feather icon in the tray has everything
else (right-click). The installer needs Python 3.10+ from python.org and
internet access for pip; it takes 1-3 minutes the first time.

Run from source instead (development):

```
python main.py
```

using the global Python 3.12 that has the packages from requirements.txt
installed (`python -m pip install -r requirements.txt` once). run.bat does the
same and prefers a local .venv if one exists; run_hidden.bat starts it with
no console window. Do NOT keep a .venv inside this OneDrive folder: OneDrive
tries to sync its thousands of files. The installer's venv lives in
%LOCALAPPDATA%, outside OneDrive.

If anything does not work, run the self-check first:

```
python doctor.py
```

It prints one ok / warn / FAIL line per dependency, screen, audio device and
camera, and says whether another copy of the app is already running.

To remove: `uninstall.bat`.

### macOS

```
./install.sh          # source install to ~/Applications + LaunchAgent
./build.sh            # OR: signed PyInstaller .app (see CLAUDE.md)
```

First run: grant Screen Recording (and Camera / Microphone when asked) under
System Settings > Privacy & Security. Full macOS details, the two-copy sync
problem, code-signing and every Mac-specific gotcha are in [CLAUDE.md](CLAUDE.md).

## 5. How to use it

1. Press the hotkey (PrintScreen on Windows, F13 on macOS; change it from the
   tray menu, combinations such as Ctrl+Shift+S work).
2. Drag a rectangle. Drag the white handles to resize, drag inside to move,
   click outside to start over. The label above shows the exact pixel size of
   the image you will get.
3. Use the vertical toolbar to annotate. Blur pixelates a rectangle (hide a
   name or a number before sharing). Text: click, type, Enter.
4. Bottom toolbar: Record, Copy, Save, Close.

Keyboard inside the overlay:

| Key | Action |
|---|---|
| Enter or Ctrl+C | Copy to clipboard and close |
| Ctrl+S | Save as PNG / JPG (default folder Pictures\ScreenCapture, timestamped name) |
| Ctrl+Z / Ctrl+Y | Undo / redo an annotation |
| Ctrl+A | Select the whole screen |
| Esc | Cancel |

Recording: press Record in the overlay. A card lets you turn the webcam,
microphone and computer audio on or off; drag the round camera picture
anywhere inside the red area and turn the mouse wheel over it to make it
bigger or smaller (the size is remembered); press Start recording. After 3-2-1 the floating
bar has pause, stop, timer, mic mute, webcam and a drawing mode. Drag the red
grip above the border to move the recorded area. Stop reveals the MP4 in
Videos\ScreenCapture (Movies/ScreenCapture on macOS).

Tray menu: Take Screenshot, Capture Shortcut, Webcam (camera list), Recording
Quality (native / 1080p / 720p), Record Computer Audio, Show Mouse Cursor in
Recordings, Start with Windows, Open Recordings / Screenshots Folder, About,
Quit.

## 6. Settings file

Everything the tray menu changes is stored in config.json next to main.py
(gitignored; config.example.json lists every key with its default). Two keys
have no menu item:

| Key | Meaning |
|---|---|
| windows_dpi_scaling | Windows. true (default) = UI scaled per monitor, screenshots keep every physical pixel. false = the old 1:1 physical-pixel behaviour (tiny UI on a 150 percent laptop screen). |
| high_res_screenshots | macOS. true = Retina 2x capture via CoreGraphics. Off by default because the PNG opens "zoomed". |

## 7. Project structure

```
ScreenCapture/
  main.py                 Entry point: tray/menu-bar app, hotkey, capture, recording orchestration
  overlay.py              Selection + annotation UI drawn over a frozen screenshot
  tools.py                Drawing tools (pen, line, arrow, rect, ellipse, highlighter, blur, text)
  capture.py              Screen grab (mss)
  recorder.py             Recording engine: mss frames -> H.264 (PyAV) -> MP4 + AAC (ffmpeg)
  recording_toolbar.py    Floating recording bar + drawing sub-panel
  setup_panel.py          Pre-recording card (camera / mic / computer audio toggles)
  countdown.py            3-2-1 overlay
  webcam.py               Camera capture thread + round draggable preview
  audio_helper.py         Mic (sounddevice), computer audio (WASAPI loopback / ScreenCaptureKit), mixing
  platform_utils.py       Every OS-specific shim: folders, click-through, non-activating windows,
                          logical <-> physical screen geometry, macOS permissions
  app_config.py           The one settings store (config.json)
  doctor.py               Self-check for a new machine
  sc/                     macOS-only: Carbon hotkey (used) and a partial native rewrite (not the app)
  sc_audio_helper.m       macOS-only native system-audio helper (compile once, see CLAUDE.md)
  assets/                 Feather icon (svg source, png, ico, icns, tray pngs)
  install.bat / uninstall.bat / run.bat / run_hidden.bat     Windows
  install.sh / build.sh / sign_app.sh / sync.sh / run.sh      macOS
  ScreenCapture.spec      PyInstaller spec (both platforms)
  CLAUDE.md               Developer notes and gotchas (read before changing capture, overlays, audio)
```

## 8. How it works (the non-obvious parts)

Coordinate spaces. Qt reports LOGICAL pixels (scaled by the monitor's DPI);
mss captures PHYSICAL pixels. On Windows each screen's logical origin is
the same point as its physical origin and only the size differs, so
platform_utils.physical_screen_rect() matches the mss monitor by origin.
The overlay tags the screenshot pixmap with the capture ratio
(setDevicePixelRatio) so Qt draws it crisp at logical size, and renders the
result at physical resolution with an integer crop origin. The recorder
region is physical: screen physical origin + selection x ratio.

Recording pipeline. A capture loop grabs frames with mss, stamps the mouse
cursor, composites the on-screen drawings, and queues them; an encoder thread
writes H.264 into a temporary MKV with wall-clock timestamps (so a slow frame
never desyncs the audio). On stop, mic and computer audio are aligned to the
video start, mixed (gentle voice compression, background at a fixed level,
limiter, one normalize), written as WAV, and ffmpeg (bundled by
imageio-ffmpeg) remuxes MKV + WAV into the final MP4. The webcam circle is
captured from the screen, not composited, so it is recorded exactly where you
see it.

Computer audio on Windows. WASAPI loopback (package SoundCard) only delivers
buffers while an audio session is active, so the app plays digital silence on
the default output during a recording and pads any remaining stall with zeros
from the wall clock. Otherwise the audio track would be shorter than the
video and drift.

Overlays that must not eat clicks or steal focus. The red recording border
and the drawing layer are click-through (WS_EX_TRANSPARENT on Windows,
NSWindow.ignoresMouseEvents on macOS); every floating control is
non-activating (WS_EX_NOACTIVATE via Qt's WindowDoesNotAcceptFocus) so the
app you are recording keeps keyboard focus.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Hotkey does nothing | Another app owns the key (a second copy of ScreenCapture, a Snipping Tool binding). Quit the other copy from its tray icon, or pick another key under Capture Shortcut. The app tells you at startup if it could not register the key. |
| "ScreenCapture is already running" | It is in the tray. Double-click the icon or press the hotkey. |
| Tiny toolbars on the laptop screen | config.json has windows_dpi_scaling = false. Set it to true (default) and restart. |
| Recording has no computer audio | Tray menu: Record Computer Audio must be on and the doctor must show "computer audio ok". Some USB docks expose a loopback only for the active output device: check Windows Sound settings. |
| Webcam looks blurry in the video | The circle is recorded from the screen, so its size on screen is its size in the video. Make it bigger (mouse wheel over it) and record a larger area, or 1080p output from a small area upscales everything. |
| Webcam takes 3-4 seconds to appear | Normal for DirectShow on Windows; the circle shows "Starting camera" meanwhile. Turn the webcam on in the setup card, not mid-recording. |
| "No cameras detected" | Another app (Teams) holds the camera, or the Camera privacy toggle is off. Webcam > Refresh list after fixing. |
| Video plays but audio cuts | Do not shrink the mic buffer or re-add a noise gate (see CLAUDE.md). Check the doctor's microphone line. |
| Saved screenshot is bigger than it looked | That is the real pixel count on a scaled screen; the label above the selection shows it before you save. |
| SentinelOne / IT questions | The app runs as plain python.exe from %LOCALAPPDATA% (no unsigned .exe), registers one global hotkey, reads the screen, and writes to Pictures and Videos. It never lists or kills processes. |

macOS symptoms (menu-bar icon missing, wallpaper-only capture, permissions,
audio helper, lip-sync) are covered in CLAUDE.md.

## 10. Development notes

- Both laptops pull the same GitHub repo. Windows-specific and macOS-specific
  code is isolated behind IS_WINDOWS / IS_MACOS in platform_utils.py; do not
  put sys.platform checks anywhere else.
- Test a UI change without pressing the hotkey: create OverlayWindow with a
  capture and call grab() / _get_result_image() from a script (the harness
  used during the Windows port is described in CLAUDE.md).
- Rule from INSTRUCTIONS.txt applies here too: never list, inspect or kill
  processes from scripts on the corporate laptop. The single-instance check is
  a localhost port (47392), the installer asks the user to quit a running copy.
- Optional Windows .exe: `pyinstaller ScreenCapture.spec` builds a onedir
  bundle in dist/. Unsigned executables get flagged by corporate endpoint
  security, so the venv-based install.bat is the recommended path.

## License

Private / All rights reserved.
