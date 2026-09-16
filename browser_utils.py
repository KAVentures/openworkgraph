from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_BROWSER_APP_PATTERNS = (
    "google chrome", "chrome", "chromium", "chrome canary",
    "microsoft edge", "msedge", "edge beta", "edge dev", "edge canary",
    "firefox", "firefox developer edition", "firefox nightly", "waterfox",
    "librewolf", "floorp", "zen browser", "mullvad browser", "tor browser",
    "safari", "safari technology preview", "orion", "duckduckgo",
    "brave", "brave browser", "arc", "opera", "opera gx", "vivaldi",
    "yandex", "wavebox", "sidekick", "shift", "ghost browser",
)


def _normalize_app_name(app: str) -> str:
    value = (app or "").strip().lower()
    if value.endswith(".exe"):
        value = value[:-4]
    value = re.sub(r"\s+", " ", value)
    return value


def is_browser_app(app: str, custom_patterns: Iterable[str] | None = None) -> bool:
    value = _normalize_app_name(app)
    if not value:
        return False
    patterns = list(DEFAULT_BROWSER_APP_PATTERNS)
    if custom_patterns:
        patterns.extend(custom_patterns)
    normalized = [_normalize_app_name(p) for p in patterns if p]
    if value in normalized:
        return True
    return any(p and (p in value or value in p) for p in normalized if len(p) >= 3)


def normalized_browser_title(title: str) -> str:
    value = (title or "").strip()
    value = re.sub(r"^\(\d+\)\s*", "", value)
    value = re.sub(r"^\[\d+\]\s*", "", value)
    return value
