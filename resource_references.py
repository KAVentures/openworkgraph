from __future__ import annotations

"""Validation and privacy minimization for observed business-resource references.

Browser sensors may recognize an allowlisted SaaS object before generic URL
sanitization removes its identifier. This module accepts only narrowly structured
references and never accepts or returns a full URL, query string, fragment, page
content, or arbitrary label.
"""

import hashlib
import re
from typing import Any

_ALLOWED_KINDS: dict[str, set[str]] = {
    "gmail": {"thread_locator"},
    "google_drive": {"document", "spreadsheet", "presentation", "file"},
    "github": {"pull_request", "issue"},
    "salesforce": {"record"},
    "jira": {"issue"},
    "linear": {"issue"},
}

_RESOURCE_REF_RE = re.compile(r"^owg:r:[0-9a-f]{24}$")
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$")
_SALESFORCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}:[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_GITHUB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}/(?:pull|issues)/[1-9][0-9]{0,9}$")
_LINEAR_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}:[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$")


def _host_allowed(provider: str, host: str) -> bool:
    if not host or len(host) > 253 or not _HOST_RE.fullmatch(host):
        return False
    if provider == "gmail":
        return host == "mail.google.com"
    if provider == "google_drive":
        return host in {"docs.google.com", "drive.google.com"}
    if provider == "github":
        return host == "github.com"
    if provider == "salesforce":
        return host.endswith(".salesforce.com") or host.endswith(".force.com")
    if provider == "jira":
        return host.endswith(".atlassian.net")
    if provider == "linear":
        return host == "linear.app"
    return False


def _locator_valid(provider: str, kind: str, locator: str) -> bool:
    if not locator or len(locator) > 320 or any(ch in locator for ch in "?#\r\n"):
        return False
    if provider == "gmail" and kind == "thread_locator":
        return locator.startswith("web-thread:") and bool(re.fullmatch(r"web-thread:[A-Za-z0-9_-]{12,80}", locator))
    if provider == "google_drive":
        prefix = {
            "document": "document:",
            "spreadsheet": "spreadsheet:",
            "presentation": "presentation:",
            "file": "file:",
        }.get(kind, "")
        return bool(prefix and locator.startswith(prefix) and _SAFE_ID_RE.fullmatch(locator[len(prefix):]))
    if provider == "github":
        return bool(_GITHUB_RE.fullmatch(locator))
    if provider == "salesforce" and kind == "record":
        return bool(_SALESFORCE_RE.fullmatch(locator))
    if provider == "jira" and kind == "issue":
        return bool(_ISSUE_KEY_RE.fullmatch(locator))
    if provider == "linear" and kind == "issue":
        return bool(_LINEAR_RE.fullmatch(locator))
    return False


def _canonical(provider: str, kind: str, host: str, locator: str) -> str:
    return "|".join((provider, kind, host, locator))


def _resource_ref(provider: str, kind: str, host: str, locator: str) -> str:
    digest = hashlib.sha256(_canonical(provider, kind, host, locator).encode("utf-8")).hexdigest()
    return f"owg:r:{digest[:24]}"


def normalize_resource_reference(value: Any, *, include_locator: bool) -> dict[str, str] | None:
    """Return a bounded structured reference or ``None`` for anything untrusted.

    When a validated locator is present, the stable OWG correlation token is
    recomputed locally rather than trusting the sender. In context-only mode an
    already minimized OWG token may be accepted without the resolver locator.
    """
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or "").strip().lower()
    kind = str(value.get("resource_kind") or "").strip().lower()
    host = str(value.get("host") or "").strip().lower()
    if provider not in _ALLOWED_KINDS or kind not in _ALLOWED_KINDS[provider] or not _host_allowed(provider, host):
        return None

    locator = str(value.get("resolver_locator") or "").strip()
    if locator:
        if not _locator_valid(provider, kind, locator):
            return None
        ref = _resource_ref(provider, kind, host, locator)
    else:
        ref = str(value.get("resource_ref") or "").strip().lower()
        if not _RESOURCE_REF_RE.fullmatch(ref):
            return None

    out = {
        "provider": provider,
        "resource_kind": kind,
        "host": host,
        "resource_ref": ref,
        "resolution": "observed",
    }
    if include_locator and locator:
        out["resolver_locator"] = locator
    return out


__all__ = ["normalize_resource_reference"]
