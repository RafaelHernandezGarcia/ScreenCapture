"""Filesystem locations the app writes to."""
import os
import sys


def recordings_dir() -> str:
    base = (os.path.expanduser("~/Movies/ScreenCapture")
            if sys.platform == "darwin"
            else os.path.expanduser("~/Videos/ScreenCapture"))
    os.makedirs(base, exist_ok=True)
    return base


def screenshots_dir() -> str:
    base = os.path.expanduser("~/Pictures/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def log_dir() -> str:
    base = os.path.expanduser("~/Library/Logs/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


def config_path() -> str:
    base = os.path.expanduser("~/Library/Application Support/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "config.json")
