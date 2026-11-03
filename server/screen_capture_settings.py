from __future__ import annotations

"""The user's choice of full screen capture, stored in the local recorder config.

``screen_capture_mode`` is ``"off"`` (default) or ``"full"``: every monitor at
native resolution, on device only (see ``collector/screen_capture.py``).
Turning it on requires an explicit acknowledgement that everything visible on
screen, including other people's messages and personal content, will be
captured. The recorder hot-reloads this setting, so turning it off stops new
captures at the next poll. The never-record lists keep applying.
"""

import json
import os
import tempfile
from typing import Any

from collector.screen_capture import MODES, MIN_INTERVAL_SECONDS

from .capture_exclusions import _LOCK, _config_path, _read

ACK_VERSION = "full-screen-capture-v1"
MAX_INTERVAL_SECONDS = 3600


def ocr_available() -> bool:
    import importlib.util
    import shutil

    return importlib.util.find_spec("pytesseract") is not None and shutil.which("tesseract") is not None


def current() -> dict[str, Any]:
    cfg = _read(_config_path())
    mode = cfg.get("screen_capture_mode") if cfg.get("screen_capture_mode") in MODES else "off"
    try:
        interval = float(cfg.get("screenshot_interval_seconds", 20))
    except (TypeError, ValueError):
        interval = 20.0
    return {
        "mode": mode,
        "modes": list(MODES),
        "ocr_enabled": cfg.get("screen_text_ocr_enabled") is True,
        "ocr_available": ocr_available(),
        "interval_seconds": max(MIN_INTERVAL_SECONDS, min(interval, MAX_INTERVAL_SECONDS)),
        "acknowledgement_version": ACK_VERSION,
        "acknowledged": cfg.get("screen_capture_ack_version") == ACK_VERSION,
        "captures": "every monitor at native resolution: on focus change, each click/scroll and on the interval",
        "stored": "local_only",
        "never_record_lists_apply": True,
        "applies_to": "new activity from now on",
    }


def update(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    path = _config_path()
    with _LOCK:
        cfg = _read(path)
        if "mode" in payload:
            mode = payload["mode"]
            if mode not in MODES:
                raise ValueError(f"mode must be one of {', '.join(MODES)}")
            if mode == "full":
                if payload.get("acknowledge") != ACK_VERSION:
                    raise PermissionError(
                        "Full screen capture records everything visible on every screen. "
                        f"Send acknowledge={ACK_VERSION!r} to confirm.")
                cfg["screen_capture_ack_version"] = ACK_VERSION
            cfg["screen_capture_mode"] = mode
        if "ocr_enabled" in payload:
            if not isinstance(payload["ocr_enabled"], bool):
                raise ValueError("ocr_enabled must be true or false")
            cfg["screen_text_ocr_enabled"] = payload["ocr_enabled"]
        if "interval_seconds" in payload:
            value = payload["interval_seconds"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
                    MIN_INTERVAL_SECONDS <= value <= MAX_INTERVAL_SECONDS):
                raise ValueError(f"interval_seconds must be {MIN_INTERVAL_SECONDS:g}-{MAX_INTERVAL_SECONDS}")
            cfg["screenshot_interval_seconds"] = float(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            from pathlib import Path
            Path(tmp).unlink(missing_ok=True)
            raise
    return current()


__all__ = ["ACK_VERSION", "current", "update"]
