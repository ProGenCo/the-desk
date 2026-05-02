"""Shared I/O + validation for The Desk.

Both update.py and sync.py consume this. Single source of truth for
file paths, JSON load/save, and shape validation.
"""
import json, os

ROOT      = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(ROOT, "portfolio_data.json")
DASH_PATH = os.path.join(ROOT, "dashboard-v4.html")
INDEX_PATH = os.path.join(ROOT, "index.html")


def load_json():
    with open(JSON_PATH) as f:
        return json.load(f)


def save_json(data):
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)


def validate_data_shape(data):
    """Sanity-check loaded JSON has the keys downstream code depends on.
    Fail loud, fail early."""
    required = ["accounts", "daily_log", "momentum"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"portfolio_data.json missing required keys: {missing}")
    if not isinstance(data["accounts"], dict) or not data["accounts"]:
        raise ValueError("portfolio_data.json: 'accounts' must be a non-empty dict")
    return True
