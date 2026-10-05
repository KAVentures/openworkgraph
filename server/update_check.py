from __future__ import annotations

"""Best-effort public GitHub release check.

No work evidence, identifiers, settings, or file data are sent. The request is a
plain GET to the public OpenWorkGraph releases API and failures are non-fatal.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import threading
import time
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RELEASE_API = "https://api.github.com/repos/KAVentures/openworkgraph/releases/latest"
_RELEASE_PREFIX = "https://github.com/KAVentures/openworkgraph/releases/"
_CACHE_SECONDS = 6 * 60 * 60
_LOCK = threading.RLock()
_CACHE: tuple[float, dict[str, Any]] | None = None


def _version_parts(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?", text)
    if not match:
        return ()
    return tuple(int(piece or 0) for piece in match.groups(default="0"))


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _version_parts(latest)
    current_parts = _version_parts(current)
    return bool(latest_parts and current_parts and latest_parts > current_parts)


def current_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def installer_url() -> str:
    system = platform.system()
    if system == "Darwin":
        return "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.pkg"
    if system == "Windows":
        return "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Setup.exe"
    return "https://github.com/KAVentures/openworkgraph/releases/latest"


def _fetch() -> dict[str, Any]:
    current = current_version()
    request = Request(
        RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenWorkGraph-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read(256_000).decode("utf-8"))
        tag = str(payload.get("tag_name") or "").strip()
        url = str(payload.get("html_url") or "").strip()
        if url and not url.startswith(_RELEASE_PREFIX):
            url = ""
        return {
            "checked": True,
            "current_version": current,
            "latest_version": tag.lstrip("vV"),
            "update_available": is_newer_version(tag, current),
            "release_url": url,
            "installer_url": installer_url(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": "public_github_release",
            "request_contains_work_evidence": False,
        }
    except Exception:
        return {
            "checked": False,
            "current_version": current,
            "latest_version": "",
            "update_available": False,
            "release_url": "",
            "installer_url": installer_url(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": "public_github_release",
            "request_contains_work_evidence": False,
        }


def update_status(*, force: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.monotonic()
    with _LOCK:
        if not force and _CACHE is not None and now - _CACHE[0] < _CACHE_SECONDS:
            return dict(_CACHE[1])
    value = _fetch()
    with _LOCK:
        _CACHE = (now, value)
    return dict(value)


__all__ = ["current_version", "installer_url", "is_newer_version", "update_status"]
