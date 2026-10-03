from __future__ import annotations

"""The "never record" lists: apps, websites and words in a window title.

The lists live in the local config file next to the other capture settings
(``excluded_apps``, ``excluded_browser_host_patterns``,
``excluded_title_patterns``). The desktop recorder reads them when it starts;
the server applies them again when activity arrives, so a change made in the
dashboard takes effect immediately without restarting the recorder. Browser
events are filtered by the existing browser privacy path, which reads the same
config on every request.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from collector.privacy import should_exclude

DEFAULTS: dict[str, list[str]] = {
    "excluded_apps": ["1Password", "Bitwarden", "KeePass", "Keychain Access"],
    "excluded_title_patterns": ["password", "private", "incognito", "bank"],
    "excluded_browser_host_patterns": [],
}
_PUBLIC = {"apps": "excluded_apps", "title_words": "excluded_title_patterns", "hosts": "excluded_browser_host_patterns"}
MAX_ENTRIES = 100
MAX_LENGTH = 120
_LOCK = threading.Lock()
# Desktop metadata that can carry visible text from the excluded window.
_TEXT_METADATA = ("target", "target_observation_phase", "page", "ui", "document", "label")


def _config_path() -> Path:
    from .main import CONFIG_PATH

    return Path(CONFIG_PATH)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _clean(values: Any, *, host: bool = False) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("each list must be an array of strings")
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = " ".join(str(raw or "").split())[:MAX_LENGTH]
        if host:
            text = text.lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} entries per list")
    return out


def current() -> dict[str, Any]:
    cfg = _read(_config_path())
    result = {public: list(cfg.get(key, DEFAULTS[key]) or []) for public, key in _PUBLIC.items()}
    result["defaults"] = {public: list(DEFAULTS[key]) for public, key in _PUBLIC.items()}
    result["applies_to"] = "new activity from now on"
    return result


def update(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    path = _config_path()
    with _LOCK:
        cfg = _read(path)
        for public, key in _PUBLIC.items():
            if public in payload:
                cfg[key] = _clean(payload[public], host=public == "hosts")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
    return current()


def apply_to_desktop_event(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Blank an incoming desktop event the way the recorder would have.

    Agent and browser-extension evidence is left alone (it has its own
    allowlists and privacy path). Already-excluded events are returned as is.
    """
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    source = str(event.get("source") or meta.get("source") or "desktop").lower()
    if source in {"agent", "browser_extension"} or str(event.get("event_type") or "").startswith("browser_"):
        return event
    if meta.get("excluded") or str(event.get("app") or "") == "Excluded":
        return event
    if not should_exclude(
        str(event.get("app") or ""),
        str(event.get("window_title") or ""),
        config.get("excluded_apps") or [],
        config.get("excluded_title_patterns") or [],
    ):
        return event
    out = dict(event)
    out["app"] = "Excluded"
    out["window_title"] = ""
    out["screenshot_path"] = None
    safe = {k: v for k, v in meta.items() if k not in _TEXT_METADATA}
    safe["excluded"] = True
    safe["excluded_by"] = "never_record_list"
    out["metadata"] = safe
    return out


__all__ = ["DEFAULTS", "apply_to_desktop_event", "current", "update"]
