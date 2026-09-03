"""Persistent settings for ScreenCapture - ONE store for every module.

The file is config.json next to this module (gitignored; see
config.example.json). main.py, overlay.py and recording_toolbar.py all read
and write through here so a preference set in one place is seen everywhere.

Keys in use:
    hotkey_name          "Print", "F13", "Ctrl+Shift+S" ... (see main.py)
    camera_index         int, OpenCV device index of the preferred webcam
    recording_size       "native" | "1080p" | "720p"
    webcam_default       bool, start recordings with the webcam circle on
    mic_default          bool, start recordings with the mic on
    system_audio         bool, also record what the computer plays (Windows/macOS)
    default_color        "#RRGGBB", annotation color used by new overlays
    last_save_dir        folder the Save dialog opens in
    high_res_screenshots macOS only, capture Retina 2x
    windows_dpi_scaling  Windows only, False = legacy 1:1 physical-pixel UI
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "hotkey_name": "Print",
    "camera_index": 0,
    "recording_size": "native",
    "webcam_default": False,
    "mic_default": True,
    "system_audio": True,
    "default_color": "#000000",
    "last_save_dir": "",
    "high_res_screenshots": False,
    "windows_dpi_scaling": True,
}


def load_config():
    """Return the saved settings merged over DEFAULTS (never raises)."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg):
    """Write settings to disk (only keys that differ from nothing - the whole dict)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print(f"[config] could not save {CONFIG_PATH}: {e}")


def get(key, default=None):
    """Read one setting."""
    return load_config().get(key, DEFAULTS.get(key, default))


def set_value(key, value):
    """Update one setting and persist it."""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
    return cfg
