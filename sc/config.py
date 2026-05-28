"""JSON config persistence."""
import json
from .paths import config_path

DEFAULTS = {
    "hotkey_vk": 105,        # F13
    "hotkey_modifiers": 0,   # plain key, no cmd/shift/etc
    "hotkey_label": "F13",
}


def load() -> dict:
    try:
        with open(config_path(), "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    out = dict(DEFAULTS)
    out.update(data)
    return out


def save(cfg: dict) -> None:
    with open(config_path(), "w") as f:
        json.dump(cfg, f, indent=2)
