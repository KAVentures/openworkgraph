from __future__ import annotations

"""AI context detail level: what Context MCP tools may show an AI.

Three layers, one source of truth (the local database). The AI detail setting
never modifies it; since v0.108 detected personal details are tokenized before
storage, and rows stored earlier are protected once by a privacy migration
(server.db.protect_existing_titles). Until that migration completes, Full is
served as Redacted.

* **Raw**: stored locally, used by local analysis. Not sent to AI by default.
* **Redacted** (default for AI): the original text with only sensitive spans
  replaced by typed stable tokens (PERSON_, EMAIL_, PHONE_, PERSONNUMMER_, ID_).
* **Safe allowlist**: dashboard glance views and organization sharing
  (server.dashboard_privacy_policy, unchanged).

Requests from the MCP server carry ``X-OpenWorkGraph-Context: ai``. For those
requests the per-route display redaction is deferred and the whole JSON
response is transformed once, here, according to the effective detail level:

* ``redacted``: presentation pipeline + contextual name redaction on every string;
* ``full``: raw labels and titles (only if the user chose it and no organization
  policy forces Redacted).

Settings live in config.json under ``ai_context``::

    "ai_context": {"detail": "redacted", "never_redact": ["Acme AB"], "always_redact": []}

An organization can force Redacted through Gateway policy
(``force_redacted_ai_context``) or an administrator-managed config key
(``organization_ai_context_detail: "redacted"``).
"""

import contextvars
import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from . import presentation as _presentation
from .contextual_redaction import CachedRedactor, Detector, Settings

DETAIL_REDACTED = "redacted"
DETAIL_FULL = "full"
DETAILS = (DETAIL_REDACTED, DETAIL_FULL)
AI_CONTEXT_HEADER = "x-openworkgraph-context"
DETAIL_HEADER = "X-OpenWorkGraph-Detail-Level"
MAX_LIST_ITEMS = 200
MAX_PHRASE_LENGTH = 120

_AI_REQUEST: contextvars.ContextVar[bool] = contextvars.ContextVar("owg_ai_context_request", default=False)
_CONFIG_LOCK = threading.RLock()
_REDACTOR = CachedRedactor()
_REGISTRY_CACHE: dict[str, Any] = {"key": None, "data": {}}

# Fields whose string values are identifiers, timestamps or enums. Locator
# fields are handled separately: they may contain human names in path/query
# components even though the host itself is structural.
_SKIP_FIELDS = {
    "event_id", "session_id", "device_id", "sensor_id", "organization_id", "actor_id",
    "schema_version", "browser_session_id", "work_session_id", "observed_at", "generated_at",
    "run_started_at", "started_at", "ended_at", "created_at", "updated_at", "range_start",
    "range_end", "hostname", "host", "page_host", "event_type", "source", "data_layer",
    "detail_level", "family_key", "execution_id", "run_id", "trace_id", "span_id",
    "evidence_event_id", "fingerprint", "sha256",
}
_LOCATOR_FIELDS = {
    "url", "href", "uri", "frame_url", "resource_locator", "origin", "pathname", "page_path",
}
_TOKEN_MARKER_RE = re.compile(
    r"\b(?:OWNER(?:_EMAIL|_PHONE)?|PERSON(?:_[0-9A-F]{4,})?|EMAIL_[0-9A-F]{4,}|PHONE_[0-9A-F]{4,}|PERSONNUMMER_[0-9A-F]{4,}|ID_[0-9A-F]{4,})\b"
)


# --- request scope ----------------------------------------------------------------

def ai_request_active() -> bool:
    return _AI_REQUEST.get()


def enter_ai_request() -> contextvars.Token:
    return _AI_REQUEST.set(True)


def exit_ai_request(token: contextvars.Token) -> None:
    _AI_REQUEST.reset(token)


# --- settings ----------------------------------------------------------------------

def config_path() -> Path:
    return _presentation._config_path()


def _read_config() -> dict[str, Any]:
    try:
        value = json.loads(config_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _phrases(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value[:MAX_LIST_ITEMS]:
        phrase = " ".join(str(item or "").split())[:MAX_PHRASE_LENGTH]
        if phrase and phrase.casefold() not in seen:
            seen.add(phrase.casefold())
            out.append(phrase)
    return out


def user_settings() -> dict[str, Any]:
    section = _read_config().get("ai_context")
    section = section if isinstance(section, dict) else {}
    detail = str(section.get("detail") or DETAIL_REDACTED).strip().lower()
    return {
        "detail": detail if detail in DETAILS else DETAIL_REDACTED,
        "never_redact": _phrases(section.get("never_redact")),
        "always_redact": _phrases(section.get("always_redact")),
    }


def save_user_settings(
    *,
    detail: str | None = None,
    never_redact: list[str] | None = None,
    always_redact: list[str] | None = None,
) -> dict[str, Any]:
    if detail is not None and detail not in DETAILS:
        raise ValueError("detail must be 'redacted' or 'full'")
    with _CONFIG_LOCK:
        path = config_path()
        cfg = _read_config()
        section = cfg.get("ai_context") if isinstance(cfg.get("ai_context"), dict) else {}
        section = dict(section)
        if detail is not None:
            section["detail"] = detail
        if never_redact is not None:
            section["never_redact"] = _phrases(never_redact)
        if always_redact is not None:
            section["always_redact"] = _phrases(always_redact)
        cfg["ai_context"] = section
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, path)
    return effective_detail()


def _gateway_redaction_lock(cfg: dict[str, Any]) -> str:
    """Return the Gateway lock state, failing closed while policy is unknown.

    Once a machine is enrolled, Full must never be exposed merely because the
    policy cache has not been created/refreshed yet. A successful policy fetch
    writes an explicit true/false value into SyncState; until that happens the
    effective AI representation remains Redacted.
    """
    gateway = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
    if not gateway.get("enabled"):
        return ""
    try:
        from connector.state import SyncState
        path = _presentation._data_dir() / "gateway_sync_state.db"
        if not path.exists():
            return "gateway_policy_unknown"
        state = SyncState(path)
        raw = state.get("org_force_redacted_ai_context", "").strip().lower()
        if not raw:
            return "gateway_policy_unknown"
        return "gateway_policy" if raw in {"1", "true", "yes", "on"} else ""
    except Exception:
        return "gateway_policy_unknown"


def organization_lock() -> str:
    """Return why the organization forces Redacted, or '' when Full is allowed."""
    cfg = _read_config()
    managed = str(cfg.get("organization_ai_context_detail") or "").strip().lower()
    if managed == DETAIL_REDACTED:
        return "managed_config"
    return _gateway_redaction_lock(cfg)


def effective_detail() -> dict[str, Any]:
    from .db import title_protection_complete

    settings = user_settings()
    lock = organization_lock()
    # While the one-time v0.108 title protection is still running, older rows
    # may hold names; Full would return them as stored, so it waits.
    migrating = not title_protection_complete()
    level = DETAIL_REDACTED if lock or migrating else settings["detail"]
    return {
        "detail_level": level,
        "user_setting": settings["detail"],
        "locked_by_organization": bool(lock),
        "lock_source": lock or None,
        "migration_in_progress": migrating,
        "never_redact": settings["never_redact"],
        "always_redact": settings["always_redact"],
    }


# --- redaction -----------------------------------------------------------------------

def _registry_stamp() -> tuple[Any, ...]:
    from .first_name_policy import memory_version

    path = _presentation._people_registry_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    return (str(path), mtime, memory_version())


def _registry() -> dict[str, set[str]]:
    """Learned identities: the persisted registry plus this process's memory overlay."""
    from .first_name_policy import memory_registry

    path = _presentation._people_registry_path()
    stamp = _registry_stamp()
    if _REGISTRY_CACHE["key"] == stamp:
        return _REGISTRY_CACHE["data"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    data: dict[str, set[str]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            tokens = {value} if isinstance(value, str) else {str(v) for v in value} if isinstance(value, list) else set()
            if tokens:
                data[str(key)] = tokens
    for key, tokens in memory_registry(_presentation).items():
        data.setdefault(key, set()).update(tokens)
    _REGISTRY_CACHE.update(key=stamp, data=data)
    return data


def _detector_state(settings: dict[str, Any]) -> tuple[Any, ...]:
    owner_aliases, _emails, _phones = _presentation._owner_identity()
    return (
        str(_presentation._data_dir()),
        _presentation._local_key(),
        _registry_stamp(),
        tuple(sorted(owner_aliases)),
        tuple(settings["never_redact"]),
        tuple(settings["always_redact"]),
    )


def _build_detector(settings: dict[str, Any]) -> Detector:
    registry = _registry()
    owner_aliases, _emails, _phones = _presentation._owner_identity()

    def is_learned(name: str) -> bool:
        return _presentation._alias_hash(name) in registry

    def learned_token(name: str) -> str | None:
        tokens = registry.get(_presentation._alias_hash(name)) or set()
        # A bare first name or a colliding alias is never linked to one identity.
        if len(tokens) == 1 and " " in name.strip():
            return next(iter(tokens))
        return None

    return Detector(
        token=_presentation._token,
        is_learned=is_learned,
        learned_token=learned_token,
        owner_aliases=frozenset(owner_aliases),
        settings=Settings(
            never_redact=tuple(settings["never_redact"]),
            always_redact=tuple(settings["always_redact"]),
        ),
    )


def contextual_text_redactor(settings: dict[str, Any] | None = None):
    settings = settings or user_settings()
    return _REDACTOR.get(_detector_state(settings), lambda: _build_detector(settings))


def _redact_slug_component(component: str, redact) -> str:
    """Redact a path/query component, including lower-case name slugs."""
    decoded = unquote(str(component or ""))
    if not decoded:
        return component
    direct = redact(decoded)
    if direct != decoded:
        return quote(direct, safe="@:+,._~-")

    # Browser paths commonly lower-case names (anna-svensson). The general
    # detector intentionally requires capitalization, so probe a title-cased
    # separator-normalized copy only for locator components. Keep the original
    # component unchanged unless the probe actually yields a privacy token.
    probe = re.sub(r"[-_]+", " ", decoded).strip()
    if not probe:
        return component
    probe = " ".join(word[:1].upper() + word[1:] for word in probe.split())
    detected = redact(probe)
    if detected == probe or not _TOKEN_MARKER_RE.search(detected):
        return component
    return quote(detected.replace(" ", "-"), safe="@:+,._~-")


def _redact_locator(value: str, redact) -> str:
    """Preserve locator structure while redacting sensitive path/query values."""
    text = str(value or "")
    if not text:
        return text
    try:
        parts = urlsplit(text)
        path = "/".join(_redact_slug_component(segment, redact) for segment in parts.path.split("/"))
        query_items = []
        for key, child in parse_qsl(parts.query, keep_blank_values=True):
            query_items.append((key, unquote(_redact_slug_component(quote(child, safe=""), redact))))
        query = urlencode(query_items, doseq=True) if parts.query else ""
        fragment = unquote(_redact_slug_component(quote(parts.fragment, safe=""), redact)) if parts.fragment else ""
        return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    except Exception:
        # If parsing fails, the fallback still refuses to skip the string.
        return redact(text)


def _apply_contextual(value: Any, redact, field: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _apply_contextual(v, redact, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_contextual(v, redact, field) for v in value]
    if isinstance(value, tuple):
        return tuple(_apply_contextual(v, redact, field) for v in value)
    if isinstance(value, str):
        lowered = field.casefold()
        if lowered in _LOCATOR_FIELDS:
            return _redact_locator(value, redact)
        if lowered not in _SKIP_FIELDS:
            return redact(value)
    return value


def redact_contextually(value: Any, settings: dict[str, Any] | None = None) -> Any:
    """Presentation pipeline + contextual names. Used for Redacted AI context and export."""
    from .privacy_pipeline import redact_for_display_now

    settings = settings or user_settings()
    return _apply_contextual(redact_for_display_now(value), contextual_text_redactor(settings))


def _annotate_representation(value: Any, level: str) -> Any:
    """Keep canonical provenance distinct from the text representation sent to AI."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if result.get("data_layer") == "privacy_hardened_raw_rich_evidence":
        result["evidence_origin"] = "canonical_local_event_store"
        if level == DETAIL_REDACTED:
            result["data_layer"] = "privacy_hardened_contextually_redacted_rich_evidence"
            result["text_representation"] = "contextually_redacted"
            result["stored_text_modified_for_ai"] = True
        else:
            result["text_representation"] = "stored_privacy_hardened"
            result["stored_text_modified_for_ai"] = False
    return result


def redact_for_ai(value: Any) -> tuple[Any, str]:
    """Return (payload, detail_level) for an AI-context response."""
    detail = effective_detail()
    if detail["detail_level"] == DETAIL_FULL:
        return _annotate_representation(value, DETAIL_FULL), DETAIL_FULL
    safe = redact_contextually(value, {
        "never_redact": detail["never_redact"],
        "always_redact": detail["always_redact"],
    })
    return _annotate_representation(safe, DETAIL_REDACTED), DETAIL_REDACTED
