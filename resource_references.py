from __future__ import annotations

"""Validation and privacy minimization for observed business-resource references.

Browser sensors may recognize an allowlisted SaaS object before generic URL
sanitization removes its identifier. This module accepts only narrowly structured
references and never accepts or returns a full URL, query string, fragment, page
content, or arbitrary label.

The browser-side reference is deliberately not the persisted correlation token.
The paired browser derives a bounded sensor fingerprint with the installation's
browser-pairing secret. Before persistence the local server HMACs that fingerprint
again with a separate installation-local API secret. This prevents a copied local
DB token from being reversed by enumerating short Jira/GitHub/Linear identifiers.
"""

import hashlib
import hmac
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

_SENSOR_REF_RE = re.compile(r"^owg:e:[0-9a-f]{24}$")
_STORED_REF_RE = re.compile(r"^owg:r:[0-9a-f]{32}$")
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$")
_SALESFORCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}:[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_GITHUB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}/(?:pull|issues)/[1-9][0-9]{0,9}$")
_LINEAR_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}:[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$")

_SENSOR_NAMESPACE = "openworkgraph-resource-reference-sensor-v1:"
_STORE_NAMESPACE = "openworkgraph-resource-reference-store-v1:"


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


def _browser_pairing_secret() -> bytes:
    # Lazy imports avoid coupling the standalone validation module to server
    # startup while still reusing OWG's existing high-entropy installation key.
    from server.local_auth import ensure_browser_secret

    return ensure_browser_secret().encode("utf-8")


def _storage_secret() -> bytes:
    from server.local_auth import ensure_api_token

    return ensure_api_token().encode("utf-8")


def _sensor_resource_ref(provider: str, kind: str, host: str, locator: str) -> str:
    canonical = _canonical(provider, kind, host, locator)
    digest = hmac.new(
        _browser_pairing_secret(),
        (_SENSOR_NAMESPACE + canonical).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"owg:e:{digest[:24]}"


def _stored_resource_ref(sensor_ref: str) -> str:
    digest = hmac.new(
        _storage_secret(),
        (_STORE_NAMESPACE + sensor_ref).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"owg:r:{digest[:32]}"


def normalize_resource_reference(
    value: Any,
    *,
    include_locator: bool,
    allow_persisted: bool = False,
) -> dict[str, str] | None:
    """Return a bounded persistence-safe reference or ``None`` for untrusted input.

    A browser-originated ``owg:e:…`` fingerprint is never persisted verbatim.
    The server converts it into an installation-keyed ``owg:r:…`` token. When a
    validated locator is available (Rich enterprise mode), the server derives the
    expected browser fingerprint itself and rejects a conflicting supplied value.

    Persisted-format ``owg:r:…`` values are rejected by default. They are accepted
    unchanged only when a caller explicitly marks the value as already persisted,
    which keeps local storage re-hardening idempotent without allowing a browser
    producer to bypass the re-keying boundary.
    """
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or "").strip().lower()
    kind = str(value.get("resource_kind") or "").strip().lower()
    host = str(value.get("host") or "").strip().lower()
    if provider not in _ALLOWED_KINDS or kind not in _ALLOWED_KINDS[provider] or not _host_allowed(provider, host):
        return None

    locator = str(value.get("resolver_locator") or "").strip()
    supplied_ref = str(value.get("resource_ref") or "").strip().lower()

    if locator:
        if not _locator_valid(provider, kind, locator):
            return None
        sensor_ref = _sensor_resource_ref(provider, kind, host, locator)
        if supplied_ref and supplied_ref != sensor_ref:
            return None
        stored_ref = _stored_resource_ref(sensor_ref)
    elif _SENSOR_REF_RE.fullmatch(supplied_ref):
        stored_ref = _stored_resource_ref(supplied_ref)
    elif allow_persisted and _STORED_REF_RE.fullmatch(supplied_ref):
        stored_ref = supplied_ref
    else:
        return None

    out = {
        "provider": provider,
        "resource_kind": kind,
        "host": host,
        "resource_ref": stored_ref,
        "resolution": "observed",
    }
    if include_locator and locator:
        out["resolver_locator"] = locator
    return out


def remember_normalized_reference(reference: dict[str, str] | None, raw_value: Any) -> None:
    """Keep the validated real locator in the local-only resolver dictionary."""
    if not reference or not isinstance(raw_value, dict):
        return
    locator = str(raw_value.get("resolver_locator") or "").strip()
    if not locator:
        return
    provider = str(reference.get("provider") or "")
    kind = str(reference.get("resource_kind") or "")
    host = str(reference.get("host") or "")
    if not _locator_valid(provider, kind, locator) or not _host_allowed(provider, host):
        return
    from server.local_reference_lookup import remember_resource_reference

    remember_resource_reference(reference, locator)


def resource_reference_from_url(
    raw_url: str,
    *,
    include_locator: bool = False,
    remember_locator: bool = False,
) -> dict[str, str] | None:
    """Parse an allowlisted business object without retaining the full URL."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(str(raw_url or ""))
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    provider = kind = locator = ""

    if host == "docs.google.com" and len(parts) >= 3 and parts[1] == "d":
        mapping = {
            "document": "document",
            "spreadsheets": "spreadsheet",
            "presentation": "presentation",
        }
        kind = mapping.get(parts[0], "")
        if kind and _SAFE_ID_RE.fullmatch(parts[2]):
            provider, locator = "google_drive", f"{kind}:{parts[2]}"
    elif (
        host == "drive.google.com"
        and len(parts) >= 3
        and parts[:2] == ["file", "d"]
        and _SAFE_ID_RE.fullmatch(parts[2])
    ):
        provider, kind, locator = "google_drive", "file", f"file:{parts[2]}"
    elif (
        host == "github.com"
        and len(parts) >= 4
        and re.fullmatch(r"[1-9][0-9]{0,9}", parts[3])
        and parts[2] in {"pull", "issues"}
    ):
        provider = "github"
        kind = "pull_request" if parts[2] == "pull" else "issue"
        locator = f"{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}"
    elif (host.endswith(".salesforce.com") or host.endswith(".force.com")) and "r" in parts:
        index = parts.index("r")
        if index + 2 < len(parts):
            candidate = f"{parts[index + 1]}:{parts[index + 2]}"
            if _SALESFORCE_RE.fullmatch(candidate):
                provider, kind, locator = "salesforce", "record", candidate
    elif host.endswith(".atlassian.net") and "browse" in parts:
        index = parts.index("browse")
        if index + 1 < len(parts) and _ISSUE_KEY_RE.fullmatch(parts[index + 1]):
            provider, kind, locator = "jira", "issue", parts[index + 1]
    elif host == "linear.app" and "issue" in parts:
        index = parts.index("issue")
        if index > 0 and index + 1 < len(parts):
            candidate = f"{parts[index - 1]}:{parts[index + 1]}"
            if _LINEAR_RE.fullmatch(candidate):
                provider, kind, locator = "linear", "issue", candidate
    elif host == "mail.google.com":
        fragment = str(parsed.fragment or "").lstrip("#")
        fragments = [part for part in fragment.split("/") if part]
        direct = {
            "inbox", "all", "sent", "drafts", "spam", "trash",
            "starred", "snoozed", "important",
        }
        token = ""
        if len(fragments) == 2 and fragments[0].lower() in direct:
            token = fragments[1]
        elif len(fragments) >= 3 and fragments[0].lower() == "search":
            token = fragments[-1]
        if re.fullmatch(r"[A-Za-z0-9_-]{12,80}", token or ""):
            provider, kind = "gmail", "thread_locator"
            locator = f"web-thread:{token}"

    if not (provider and kind and locator):
        return None
    sensor_ref = _sensor_resource_ref(provider, kind, host, locator)
    reference = normalize_resource_reference(
        {
            "provider": provider,
            "resource_kind": kind,
            "host": host,
            "resolver_locator": locator,
            "resource_ref": sensor_ref,
        },
        include_locator=include_locator,
    )
    if remember_locator:
        remember_normalized_reference(reference, {"resolver_locator": locator})
    return reference


__all__ = [
    "normalize_resource_reference",
    "remember_normalized_reference",
    "resource_reference_from_url",
]
