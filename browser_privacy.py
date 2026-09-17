from __future__ import annotations

"""Browser-ingest privacy hardening shared by the API and DB migration.

The browser sensor should never persist URL query values/fragments. This module
also minimizes token-like path segments and applies the same user-configured
exclusions to browser events that desktop capture already uses.
"""

import copy
import re
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from collector.privacy import should_exclude

SENSITIVE_PATH_PREDECESSORS = {
    "auth", "authenticate", "callback", "confirm", "invite", "invitation",
    "login", "magic", "oauth", "recover", "recovery", "reset", "signin",
    "token", "verify", "verification",
}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_LONG_HEX_RE = re.compile(r"^[0-9a-f]{20,}$", re.I)
_LONG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,}$")
_LONG_DIGITS_RE = re.compile(r"^\d{7,}$")


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if re.search(pattern, value or "", flags=re.IGNORECASE):
                return True
        except re.error:
            if pattern.casefold() in (value or "").casefold():
                return True
    return False


def sanitize_pathname(pathname: str) -> str:
    """Drop query/fragment and minimize likely secret/identifier path segments."""
    raw = str(pathname or "")
    raw = raw.split("#", 1)[0].split("?", 1)[0]
    if not raw:
        return "/"
    leading = raw.startswith("/")
    trailing = raw.endswith("/") and raw != "/"
    parts = raw.split("/")
    cleaned: list[str] = []
    previous = ""
    for part in parts:
        if part == "":
            if not cleaned and leading:
                cleaned.append("")
            continue
        replacement = part
        low_prev = previous.casefold()
        if low_prev in SENSITIVE_PATH_PREDECESSORS and len(part) >= 6:
            replacement = ":token"
        elif _UUID_RE.fullmatch(part) or _LONG_DIGITS_RE.fullmatch(part):
            replacement = ":id"
        elif _LONG_HEX_RE.fullmatch(part) or _LONG_TOKEN_RE.fullmatch(part):
            replacement = ":token"
        cleaned.append(replacement)
        previous = part
    result = "/".join(cleaned) or "/"
    if leading and not result.startswith("/"):
        result = "/" + result
    if trailing and not result.endswith("/"):
        result += "/"
    return result


def sanitize_url_value(value: str) -> str:
    """Return a URL-like value without query/fragment or secret-like path data.

    Besides ordinary absolute URLs, presentation/export layers sometimes carry a
    schemeless locator such as ``ehr.example/patient/123?token=...``. Treat those
    as structured locators too instead of sending them through prose redaction.
    """
    raw = str(value or "")
    try:
        parsed = urlsplit(raw)
    except Exception:
        parsed = None
    if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, sanitize_pathname(parsed.path or "/"), "", ""))
    if raw.startswith("/"):
        return sanitize_pathname(raw)

    clean = raw.split("#", 1)[0].split("?", 1)[0]
    if "/" in clean:
        head, tail = clean.split("/", 1)
        if "." in head and not any(ch.isspace() for ch in head):
            return head + sanitize_pathname("/" + tail)
    return clean


def sanitize_browser_metadata(value: Any, *, key: str = "") -> Any:
    """Recursively sanitize URL-bearing metadata while preserving semantics."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for child_key, child in value.items():
            name = str(child_key)
            low = name.casefold()
            if low == "pathname" and isinstance(child, str):
                out[name] = sanitize_pathname(child)
            elif isinstance(child, str) and ("url" in low or low in {"href", "uri", "origin", "frame_url"}):
                out[name] = sanitize_url_value(child)
            else:
                out[name] = sanitize_browser_metadata(child, key=name)
        return out
    if isinstance(value, list):
        return [sanitize_browser_metadata(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_browser_metadata(item, key=key) for item in value)
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return sanitize_url_value(value)
    return value


def sanitize_browser_page(page: dict[str, Any] | None) -> dict[str, Any]:
    page = dict(page or {})
    out: dict[str, Any] = {}
    if page.get("origin"):
        out["origin"] = sanitize_url_value(str(page.get("origin")))
    if page.get("hostname"):
        out["hostname"] = str(page.get("hostname") or "").strip().lower()
    out["pathname"] = sanitize_pathname(str(page.get("pathname") or "/"))
    if page.get("title") is not None:
        out["title"] = str(page.get("title") or "")[:240]
    return out


def browser_event_is_excluded(*, app: str, title: str, hostname: str, config: dict[str, Any]) -> bool:
    if should_exclude(
        app,
        title,
        config.get("excluded_apps") or [],
        config.get("excluded_title_patterns") or [],
    ):
        return True
    return _matches_any(hostname, config.get("excluded_browser_host_patterns") or [])


def harden_browser_event(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return a safe copy suitable for browser-event persistence.

    Excluded pages retain only structural event/action evidence. Query values,
    fragments and token-like paths are removed even for non-excluded pages.
    """
    e = copy.deepcopy(event)
    meta = sanitize_browser_metadata(e.get("metadata") or {})
    if not isinstance(meta, dict):
        meta = {}
    page = sanitize_browser_page(meta.get("page") if isinstance(meta.get("page"), dict) else {})
    title = str(page.get("title") or e.get("window_title") or "")
    hostname = str(page.get("hostname") or "")
    excluded = browser_event_is_excluded(
        app=str(e.get("app") or "Browser"),
        title=title,
        hostname=hostname,
        config=config,
    )

    privacy = dict(meta.get("privacy") or {}) if isinstance(meta.get("privacy"), dict) else {}
    privacy.update({
        "typed_values": False,
        "clipboard_contents": False,
        "url_query": False,
        "url_fragment": False,
        "token_like_path_segments": False,
    })

    if excluded:
        e["app"] = "Excluded"
        e["window_title"] = ""
        e["screenshot_path"] = None
        e["metadata"] = {
            "source": "browser_extension",
            "action": str(meta.get("action") or str(e.get("event_type") or "").replace("browser_", "")),
            "excluded": True,
            "privacy": privacy,
        }
        return e

    if page:
        meta["page"] = page
        e["window_title"] = str(page.get("title") or page.get("hostname") or e.get("window_title") or "")
    meta["privacy"] = privacy
    meta["excluded"] = False
    e["metadata"] = meta
    return e
