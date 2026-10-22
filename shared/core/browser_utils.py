from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

# Browser detection is deliberately name/pattern based rather than hard-coded to
# one vendor. This covers the common enterprise browsers and lets admins add
# internal/niche browsers through config without a code release.
DEFAULT_BROWSER_APP_PATTERNS = (
    # Chromium / Chrome channels
    "google chrome", "chrome", "chromium", "chrome canary",
    # Microsoft Edge channels
    "microsoft edge", "msedge", "edge beta", "edge dev", "edge canary",
    # Mozilla family / forks
    "firefox", "firefox developer edition", "firefox nightly", "waterfox",
    "librewolf", "floorp", "zen browser", "mullvad browser", "tor browser",
    # WebKit/Safari family
    "safari", "safari technology preview", "orion", "duckduckgo",
    # Other Chromium browsers / enterprise variants
    "brave", "brave browser", "arc", "opera", "opera gx", "vivaldi",
    "yandex", "wavebox", "sidekick", "shift", "ghost browser",
)


def _normalize_app_name(app: str) -> str:
    value = (app or "").strip().lower()
    # Windows reports process executable names in the prototype.
    if value.endswith(".exe"):
        value = value[:-4]
    value = re.sub(r"\s+", " ", value)
    return value


def is_browser_app(app: str, custom_patterns: Iterable[str] | None = None) -> bool:
    value = _normalize_app_name(app)
    if not value:
        return False
    # Analytics calls this millions of times on large histories; normalizing
    # the pattern list per call dominated 300k-event workflow queries.
    custom = tuple(str(p) for p in custom_patterns if p) if custom_patterns else ()
    return _is_browser_name(value, custom)


@lru_cache(maxsize=64)
def _normalized_patterns(custom: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_normalize_app_name(p) for p in (*DEFAULT_BROWSER_APP_PATTERNS, *custom) if p)


@lru_cache(maxsize=4096)
def _is_browser_name(value: str, custom: tuple[str, ...]) -> bool:
    normalized = _normalized_patterns(custom)
    # Exact match first, then phrase containment for channel/build names such as
    # "Google Chrome Beta" or "Firefox ESR".
    if value in normalized:
        return True
    return any(p and (p in value or value in p) for p in normalized if len(p) >= 3)


def normalized_browser_title(title: str) -> str:
    """Reduce common title-only noise while preserving tab/page identity."""
    value = (title or "").strip()
    # Mail/chat web apps often prepend unread counters without the user changing
    # page or tab. Ignore those counters.
    value = re.sub(r"^\(\d+\)\s*", "", value)
    value = re.sub(r"^\[\d+\]\s*", "", value)
    return value
