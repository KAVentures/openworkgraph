from __future__ import annotations

"""AI context detail level: what Context MCP tools may show an AI.

Three layers, one source of truth (the raw local database, never modified):

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
import threading
from pathlib import Path
from typing import Any

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

# Fields whose string values are identifiers, timestamps, enums or already
# structured locators: never run name detection on them.
_SKIP_FIELDS = {
    "event_id", "session_id", "device_id", "sensor_id", "organization_id", "actor_id",
    "schema_version", "browser_session_id", "work_session_id", "observed_at", "generated_at",
    "run_started_at", "started_at", "ended_at", "created_at", "updated_at", "range_start",
    "range_end", "url", "href", "uri", "frame_url", "resource_locator", "origin", "pathname",
    "hostname", "host", "page_host", "page_path", "event_type", "source", "data_layer",
    "detail_level", "family_key", "execution_id", "run_id", "trace_id", "span_id",
    "evidence_event_id", "fingerprint", "sha256",
}


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


def _gateway_forces_redacted(cfg: dict[str, Any]) -> bool:
    gateway = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
    if not gateway.get("enabled"):
        return False
    try:
        from connector.state import SyncState
        path = _presentation._data_dir() / "gateway_sync_state.db"
        if not path.exists():
            return False
        return SyncState(path).get_bool("org_force_redacted_ai_context", False)
    except Exception:
        return False


def organization_lock() -> str:
    """Return why the organization forces Redacted, or '' when it does not."""
    cfg = _read_config()
    managed = str(cfg.get("organization_ai_context_detail") or "").strip().lower()
    if managed == DETAIL_REDACTED:
        return "managed_config"
    if _gateway_forces_redacted(cfg):
        return "gateway_policy"
    return ""


def effective_detail() -> dict[str, Any]:
    settings = user_settings()
    lock = organization_lock()
    level = DETAIL_REDACTED if lock else settings["detail"]
    return {
        "detail_level": level,
        "user_setting": settings["detail"],
        "locked_by_organization": bool(lock),
        "lock_source": lock or None,
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


def _apply_contextual(value: Any, redact, field: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _apply_contextual(v, redact, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_contextual(v, redact, field) for v in value]
    if isinstance(value, tuple):
        return tuple(_apply_contextual(v, redact, field) for v in value)
    if isinstance(value, str) and field.casefold() not in _SKIP_FIELDS:
        return redact(value)
    return value


def redact_contextually(value: Any, settings: dict[str, Any] | None = None) -> Any:
    """Presentation pipeline + contextual names. Used for Redacted AI context and export."""
    from .privacy_pipeline import redact_for_display_now

    settings = settings or user_settings()
    return _apply_contextual(redact_for_display_now(value), contextual_text_redactor(settings))


def redact_for_ai(value: Any) -> tuple[Any, str]:
    """Return (payload, detail_level) for an AI-context response."""
    detail = effective_detail()
    if detail["detail_level"] == DETAIL_FULL:
        return value, DETAIL_FULL
    return redact_contextually(value, {
        "never_redact": detail["never_redact"],
        "always_redact": detail["always_redact"],
    }), DETAIL_REDACTED
