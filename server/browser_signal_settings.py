from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .db import DATA_DIR

SETTINGS_PATH = DATA_DIR / "browser_signal_settings.json"
DEFAULTS: dict[str, bool] = {
    "performance_timing": True,
    "file_upload_category": False,
}


def normalize_settings(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    return {key: bool(source.get(key, default)) for key, default in DEFAULTS.items()}


def load_settings() -> dict[str, bool]:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return normalize_settings(raw)


def save_settings(value: Any) -> dict[str, bool]:
    settings = normalize_settings(value)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)
    return settings


def public_settings() -> dict[str, Any]:
    settings = load_settings()
    return {
        "settings": settings,
        "defaults": dict(DEFAULTS),
        "captured_signals": {
            "performance_timing": {
                "enabled": settings["performance_timing"],
                "content": False,
                "description": "Rounded top-frame navigation timing only; no resource URLs or page contents.",
            },
            "file_upload_category": {
                "enabled": settings["file_upload_category"],
                "content": False,
                "description": "Optional coarse MIME category only; no filename, path, exact size, hash, or file contents.",
            },
        },
        "derived_without_new_sensor": [
            "fragmentation",
            "manual_transfer_patterns",
            "rapid_click_candidates",
            "auth_flow_candidates",
            "daily_rhythm",
            "ai_tool_usage",
            "navigation_hunting_candidates",
        ],
        "never_captured_by_these_signals": [
            "typed_text",
            "clipboard_contents",
            "file_names",
            "file_paths",
            "exact_file_sizes",
            "file_contents",
            "microphone_state",
            "ordinary_key_identities",
        ],
    }
