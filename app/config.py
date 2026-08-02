"""Config loading / saving. Everything lives in one JSON file on /data."""

import copy
import json
import os

DEFAULTS = {
    "max_slots": 50,
    "min_slots": 50,
    "max_slots_limit": 200,
    "seed_hours": 72.5,
    "poll_interval_seconds": 30,
    "rss_scan_interval_minutes": 15,
    "data_folder": "/data/torrents",
    "instances": [],
    "feeds": [],
}


def config_path() -> str:
    env = os.environ.get("CONFIG_FILE")
    if env:
        return env
    return os.path.join(os.environ.get("DATA_DIR", "./data"), "config.json")


def default_config() -> dict:
    return copy.deepcopy(DEFAULTS)


def load_config(path: str | None = None) -> dict:
    path = path or config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    cfg = default_config()
    cfg.update(data)
    # normalize slots to the allowed range
    cfg["max_slots"] = max(1, min(cfg.get("max_slots_limit", 200), int(cfg.get("max_slots", 50))))
    return cfg


def save_config(cfg: dict, path: str | None = None) -> None:
    path = path or config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def public_config(cfg: dict) -> dict:
    """Config as exposed to the GUI (passwords included - it is a local tool)."""
    out = copy.deepcopy(cfg)
    return out
