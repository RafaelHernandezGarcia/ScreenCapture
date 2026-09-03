"""
ScreenCapture doctor - checks this machine can run every feature.

Run it from the project folder when something does not work:

    .venv\\Scripts\\python doctor.py      (Windows)
    .venv/bin/python doctor.py           (macOS)

It never opens the overlay or records anything; it only imports the
libraries, lists screens / audio devices / cameras, and confirms ffmpeg is
reachable. Every line ends with ok / warn / FAIL and a one-line hint.
"""
import os
import sys

W = 60


def _row(label, status, detail=""):
    print(f"  {label:<28} {status:<5} {detail}")


def main():
    print("=" * W)
    print("  ScreenCapture doctor")
    print("-" * W)
    _row("Python", "ok", f"{sys.version.split()[0]}  {sys.executable}")
    _row("Platform", "ok", sys.platform)

    # --- core imports -------------------------------------------------------
    for mod, hint in [
        ("PyQt6.QtWidgets", "pip install PyQt6"),
        ("mss", "pip install mss"),
        ("PIL", "pip install Pillow"),
        ("numpy", "pip install numpy"),
        ("av", "pip install av (video encoding)"),
        ("cv2", "pip install opencv-python-headless (webcam / scaling)"),
        ("sounddevice", "pip install sounddevice (microphone)"),
        ("imageio_ffmpeg", "pip install imageio-ffmpeg (MP4 muxing)"),
    ]:
        try:
            __import__(mod)
            _row(mod, "ok")
        except Exception as e:
            _row(mod, "FAIL", f"{hint}  [{str(e)[:60]}]")

    if sys.platform == "win32":
        for mod, hint in [
            ("win32com.client", "pip install pywin32 (Start-with-Windows toggle)"),
            ("soundcard", "pip install SoundCard (record computer audio)"),
            ("pygrabber.dshow_graph", "pip install pygrabber comtypes (webcam names)"),
        ]:
            try:
                __import__(mod)
                _row(mod, "ok")
            except Exception as e:
                _row(mod, "warn", f"{hint}  [{str(e)[:60]}]")

    # --- ffmpeg ---------------------------------------------------------------
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        _row("ffmpeg binary", "ok" if os.path.exists(exe) else "FAIL", exe)
    except Exception as e:
        _row("ffmpeg binary", "FAIL", str(e)[:70])

    # --- screens --------------------------------------------------------------
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QGuiApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from platform_utils import physical_screen_rect
        for s in QGuiApplication.screens():
            g = s.geometry()
            px = physical_screen_rect(s)
            _row(f"screen {s.name()[:20]}", "ok",
                 f"logical {g.width()}x{g.height()} @ ({g.x()},{g.y()})  "
                 f"physical {px[2]}x{px[3]}  scale {s.devicePixelRatio():.2f}")
    except Exception as e:
        _row("screens", "FAIL", str(e)[:70])

    # --- capture --------------------------------------------------------------
    try:
        from capture import capture_region
        img = capture_region(0, 0, 64, 64)
        _row("screen capture (mss)", "ok", f"{img.size[0]}x{img.size[1]} test grab")
    except Exception as e:
        _row("screen capture (mss)", "FAIL", str(e)[:70])

    # --- audio ----------------------------------------------------------------
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        _row("microphone", "ok", dev["name"][:45])
    except Exception as e:
        _row("microphone", "warn", f"no input device: {str(e)[:50]}")
    try:
        from audio_helper import system_audio_available
        if system_audio_available():
            detail = ""
            if sys.platform == "win32":
                import soundcard as sc
                detail = f"loopback of: {sc.default_speaker().name[:40]}"
            _row("computer audio", "ok", detail)
        else:
            _row("computer audio", "warn", "not available (mic only)")
    except Exception as e:
        _row("computer audio", "warn", str(e)[:70])

    # --- cameras --------------------------------------------------------------
    try:
        from webcam import list_cameras
        cams = list_cameras()
        if cams:
            _row("cameras", "ok", ", ".join(n for _, n in cams)[:50])
        else:
            _row("cameras", "warn", "none detected (webcam circle disabled)")
    except Exception as e:
        _row("cameras", "warn", str(e)[:70])

    # --- folders --------------------------------------------------------------
    try:
        from platform_utils import recordings_dir, screenshots_dir
        _row("recordings folder", "ok", recordings_dir())
        _row("screenshots folder", "ok", screenshots_dir())
    except Exception as e:
        _row("folders", "FAIL", str(e)[:70])

    # --- single instance ------------------------------------------------------
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47392))
        _row("app running?", "ok", "no other copy running (port 47392 free)")
    except OSError:
        _row("app running?", "warn", "another copy is running (quit it from the tray first)")
    finally:
        s.close()

    print("=" * W)


if __name__ == "__main__":
    main()
