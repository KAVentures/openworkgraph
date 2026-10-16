from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from shared.core.browser_privacy import harden_browser_event, sanitize_browser_page, browser_event_is_excluded
from shared.core.browser_utils import is_browser_app
from .analytics import (
    search_events, search_operational_events, summary, timeline,
    operational_timeline, semantic_activity,
    _friendly_browser_surface,
)
from .context_layers import candidate_tasks, apply_to_summary
from .context import search_context, recent_context, context_timeline
from .db import init_db, insert_events, harden_existing_browser_events
from .context_exporter import build_export_payload, csv_zip_bytes, export_filename, json_bytes, xlsx_bytes
from .privacy_pipeline import (
    redact_for_display,
    initialize_privacy_state,
    learn_persistent_identities,
    reset_persistent_identities,
)
from .v46_migration import harden_existing_sensitive_identifiers_v46

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"
CONFIG_PATH = ROOT / "config.json"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "unknown"
try:
    _manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    EXPECTED_BROWSER_SENSOR_VERSION = str(_manifest.get("version_name") or _manifest.get("version") or "")
except Exception:
    EXPECTED_BROWSER_SENSOR_VERSION = ""


def _runtime_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "excluded_apps": ["1Password", "Bitwarden", "KeePass", "Keychain Access", "Messages", "iMessage", "WhatsApp", "Signal", "Telegram"],
        "excluded_title_patterns": ["password", "private", "incognito", "bank"],
        "excluded_browser_host_patterns": [],
    }
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            cfg.update(value)
    except Exception:
        pass
    return cfg


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize local privacy/storage state before the first request.

    This preserves the exact v0.55 initialization order while using FastAPI's
    supported lifespan interface instead of the deprecated startup decorator.
    """
    initialize_privacy_state()
    init_db()
    # v0.46 storage hardening is idempotent and repairs legacy rows before any
    # API response can expose them.
    harden_existing_sensitive_identifiers_v46()
    # Idempotent local migration: remove legacy URL secrets and retroactively
    # apply browser exclusions before any API response can expose old rows.
    harden_existing_browser_events(_runtime_config())
    from .db import (
        mark_title_protection_complete_if_empty,
        protect_existing_titles_in_background,
    )
    # A brand-new database needs no migration and can keep the original Full
    # AI-context behavior immediately, without delaying startup.
    mark_title_protection_complete_if_empty()

    # Upgrades with 300k+ rows must not compete with the launcher's first
    # /health probe (60s deadline). Schedule the strictly background worker
    # only after ASGI startup can finish and the service can respond; it uses
    # short, checkpointed transactions without blocking the event loop.
    # During the delay and migration, Full AI context / raw export still
    # downgrade to contextual redaction until the migration marker is set.
    migration_timer = asyncio.get_running_loop().call_later(
        3.0, protect_existing_titles_in_background,
    )
    try:
        yield
    finally:
        migration_timer.cancel()


app = FastAPI(title="OpenWorkGraph / Workflow Observer API", version=VERSION, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Starlette's default error body includes raw invalid input. That can
    # contain NaN/Infinity, which JSON cannot serialize and previously turned
    # a rejected event into a 500. Never echo untrusted capture payloads.
    safe = [
        {
            "loc": [str(part) for part in item.get("loc", ())][:12],
            "msg": str(item.get("msg") or "invalid value")[:200],
            "type": str(item.get("type") or "value_error")[:80],
        }
        for item in exc.errors()[:20]
    ]
    return JSONResponse({"detail": safe}, status_code=422)
# Host validation closes DNS-rebinding requests whose Host header is not the
# loopback endpoint. testserver is retained solely for FastAPI/Starlette tests.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8787", "http://localhost:8787"],
    allow_origin_regex=r"^(chrome-extension|moz-extension|safari-web-extension)://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "authorization"],
)

LOCAL_WEB_ORIGINS = {"http://127.0.0.1:8787", "http://localhost:8787"}
EXTENSION_PREFIXES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
EXTENSION_SENSOR_ROUTES = {
    ("GET", "/v1/browser-context"),
    ("POST", "/v1/browser-events"),
    ("POST", "/v1/browser-heartbeat"),
}


@app.middleware("http")
async def local_api_origin_guard(request: Request, call_next):
    """Prevent webpages/extensions from reading the local work-history API.

    Local non-browser clients (MCP, CLI, exporter) normally send no Origin and
    continue to work. Browser extensions are restricted to the three sensor
    endpoints they actually need; they cannot read summaries, events or exports.
    """
    origin = str(request.headers.get("origin") or "")
    method = request.method.upper()
    path = request.url.path
    if not origin:
        return await call_next(request)
    if origin in LOCAL_WEB_ORIGINS:
        return await call_next(request)
    if origin.startswith(EXTENSION_PREFIXES):
        requested = str(request.headers.get("access-control-request-method") or method).upper()
        if (requested, path) not in EXTENSION_SENSOR_ROUTES:
            return JSONResponse({"detail": "browser extension origin not allowed for this endpoint"}, status_code=403)
        return await call_next(request)
    return JSONResponse({"detail": "non-local browser origin not allowed"}, status_code=403)


COLLECTOR_STATUS: dict[str, Any] = {}
BROWSER_STATUS: dict[str, Any] = {}


class Event(BaseModel):
    event_id: str
    observed_at: str
    schema_version: str = "1.0"
    organization_id: str = ""
    actor_id: str = ""
    device_id: str
    sensor_id: str = ""
    source: str = "desktop"
    session_id: str
    app: str | None = None
    window_title: str | None = None
    event_type: str
    duration_seconds: float = 0
    screenshot_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def valid_time(cls, value: str) -> str:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("timestamp must include timezone")
        except (TypeError, ValueError) as exc:
            raise ValueError("observed_at must be a timezone-aware ISO-8601 timestamp") from exc
        if moment.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=10):
            raise ValueError("observed_at is too far in the future")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def valid_duration(cls, value: float) -> float:
        if not math.isfinite(value) or not 0 <= value <= 86400:
            raise ValueError("duration_seconds must be finite and between 0 and 86400")
        return value

    @field_validator("metadata")
    @classmethod
    def valid_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        def check(item: Any, depth: int = 0) -> None:
            if depth > 16:
                raise ValueError("metadata nesting exceeds 16 levels")
            if isinstance(item, dict):
                if len(item) > 1024:
                    raise ValueError("metadata has too many keys")
                for entry in item.values():
                    check(entry, depth + 1)
            elif isinstance(item, list):
                if len(item) > 1024:
                    raise ValueError("metadata array too long")
                for entry in item:
                    check(entry, depth + 1)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("metadata contains a non-finite number")
        check(value)
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
            raise ValueError("metadata exceeds 128 KiB")
        return value


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=500)


class Heartbeat(BaseModel):
    device_id: str
    sensor_id: str = ""
    organization_id: str = ""
    actor_id: str = ""
    session_id: str
    observed_at: str
    app: str | None = None
    window_title: str | None = None
    focus_elapsed_seconds: float = 0
    activity: dict[str, Any] = Field(default_factory=dict)
    keyboard_sensor: bool = False
    outbox_pending: int = 0


class BrowserPage(BaseModel):
    origin: str | None = None
    hostname: str | None = None
    pathname: str | None = None
    title: str | None = None


class BrowserTarget(BaseModel):
    tag: str | None = None
    role: str | None = None
    type: str | None = None
    name: str | None = None
    label: str | None = None


class BrowserEvent(BaseModel):
    event_id: str | None = None
    observed_at: str
    sensor_id: str = ""
    sensor_version: str = ""
    browser_session_id: str = ""
    organization_id: str = ""
    actor_id: str = ""
    device_id: str = ""
    work_session_id: str = ""
    action: str
    page: BrowserPage = Field(default_factory=BrowserPage)
    target: BrowserTarget = Field(default_factory=BrowserTarget)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserHeartbeat(BaseModel):
    observed_at: str
    status: str = "connected"
    sensor_id: str = ""
    sensor_version: str = ""
    browser_session_id: str = ""
    work_session_id: str = ""
    page: BrowserPage = Field(default_factory=BrowserPage)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": os.getenv("WORKFLOW_OBSERVER_MODE", "observe"),
        "version": VERSION,
    }


@app.get("/v1/collector-health")
def collector_health() -> dict[str, Any]:
    """Content-free local status for the enrolled Gateway sync worker.

    A fresh in-memory collector heartbeat is stronger than the persistent
    capture-control file, which can still say "recording" after a crash.
    No window title, work contents or device identifiers leave this endpoint.
    """
    from shared.capture_control import read_state

    now = datetime.now(timezone.utc)
    raw = str(COLLECTOR_STATUS.get("received_at") or "")
    try:
        seen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = max(0, int((now - seen).total_seconds())) if seen.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        age = None
    active = age is not None and age <= 45
    local_state = str(read_state().get("state") or "unknown")
    if active and local_state == "recording":
        state = "recording"
    elif active and local_state in {"paused", "stopped"}:
        state = local_state
    else:
        state = "no_recent_collector_heartbeat"
    return {
        "collector_state": state,
        "collector_heartbeat_age_seconds": age,
        "recent_collector_heartbeat": bool(active),
        "local_capture_policy": local_state if local_state in {"recording", "paused", "stopped"} else "unknown",
        "evidence_contents_included": False,
        "source": "local_api_in_memory_collector_heartbeat",
    }


@app.post("/v1/events")
def ingest(batch: EventBatch) -> dict[str, int]:
    # The "never record" lists apply here too, so a change made in the
    # dashboard takes effect without restarting the desktop recorder.
    from .capture_exclusions import apply_to_desktop_event

    config = _runtime_config()
    from shared.core.browser_title_privacy import cap_event_title_surfaces
    events = [
        cap_event_title_surfaces(apply_to_desktop_event(e.model_dump(), config))
        for e in batch.events
    ]
    learn_persistent_identities(events)
    return {"inserted": insert_events(events)}


@app.post("/v1/heartbeat")
def heartbeat(status: Heartbeat) -> dict[str, str]:
    COLLECTOR_STATUS.clear()
    COLLECTOR_STATUS.update(status.model_dump())
    COLLECTOR_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "ok"}


@app.post("/v1/privacy/reset-learned-names")
def reset_learned_names() -> dict[str, bool | str]:
    removed = reset_persistent_identities()
    return {
        "status": "ok",
        "removed_existing_registry": removed,
    }


@app.get("/v1/browser-context")
def browser_context() -> dict[str, str]:
    return {
        "organization_id": str(COLLECTOR_STATUS.get("organization_id") or ""),
        "actor_id": str(COLLECTOR_STATUS.get("actor_id") or ""),
        "device_id": str(COLLECTOR_STATUS.get("device_id") or ""),
        "work_session_id": str(COLLECTOR_STATUS.get("session_id") or ""),
    }


def _browser_context(event: BrowserEvent) -> dict[str, str]:
    app_name = str(COLLECTOR_STATUS.get("app") or "Browser")
    if not is_browser_app(app_name):
        app_name = "Browser"
    return {
        "app": app_name,
        "device_id": str(event.device_id or COLLECTOR_STATUS.get("device_id") or "browser-local"),
        "session_id": str(event.work_session_id or COLLECTOR_STATUS.get("session_id") or event.browser_session_id or "browser-session"),
        "organization_id": str(event.organization_id or COLLECTOR_STATUS.get("organization_id") or ""),
        "actor_id": str(event.actor_id or COLLECTOR_STATUS.get("actor_id") or ""),
        "sensor_id": str(event.sensor_id or "browser:unknown"),
    }


@app.post("/v1/browser-events")
def browser_event(event: BrowserEvent) -> dict[str, int | str]:
    ctx = _browser_context(event)
    page = event.page.model_dump(exclude_none=True)
    target = event.target.model_dump(exclude_none=True)
    metadata = dict(event.metadata or {})
    metadata.update({
        "source": "browser_extension",
        "browser_sensor_id": ctx["sensor_id"],
        "browser_sensor_version": event.sensor_version,
        "browser_session_id": event.browser_session_id,
        "capture_work_session_id": event.work_session_id,
        "action": event.action,
        "page": page,
        "target": target,
        "privacy": {
            "typed_values": False,
            "clipboard_contents": False,
            "url_query": False,
            "url_fragment": False,
            "token_like_path_segments": False,
        },
    })
    raw_event = {
        "event_id": event.event_id or str(uuid.uuid4()),
        "observed_at": event.observed_at,
        "schema_version": "1.0",
        "organization_id": ctx["organization_id"],
        "actor_id": ctx["actor_id"],
        "device_id": ctx["device_id"],
        "sensor_id": ctx["sensor_id"],
        "source": "browser_extension",
        "session_id": ctx["session_id"],
        "app": ctx["app"],
        "window_title": page.get("title") or page.get("hostname") or "",
        "event_type": f"browser_{event.action}",
        "duration_seconds": 0.0,
        "screenshot_path": None,
        "metadata": metadata,
    }
    safe_event = harden_browser_event(raw_event, _runtime_config())
    # Browser ingestion learns identities before insert_events; apply the same
    # title limit here so untrusted page text cannot stall that earlier pass.
    from shared.core.browser_title_privacy import cap_event_title_surfaces
    safe_event = cap_event_title_surfaces(safe_event)
    learn_persistent_identities(safe_event)
    inserted = insert_events([safe_event])
    safe_meta = safe_event.get("metadata") or {}
    safe_page = safe_meta.get("page") if isinstance(safe_meta.get("page"), dict) else {}
    BROWSER_STATUS.clear()
    browser_surface = _friendly_browser_surface(
        str(safe_page.get("hostname") or ""),
        str(safe_page.get("pathname") or ""),
        str(safe_page.get("title") or ""),
    ) if safe_page.get("hostname") else ""
    BROWSER_STATUS.update({
        "observed_at": event.observed_at,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": "connected",
        "hostname": safe_page.get("hostname"),
        "pathname": safe_page.get("pathname"),
        "page_title": safe_page.get("title"),
        "work_surface": browser_surface,
        "work_session_id": ctx["session_id"],
        "last_action": event.action,
        "sensor_id": ctx["sensor_id"],
        "sensor_version": event.sensor_version,
        "expected_sensor_version": EXPECTED_BROWSER_SENSOR_VERSION,
        "version_ok": bool(event.sensor_version and event.sensor_version == EXPECTED_BROWSER_SENSOR_VERSION),
        "excluded": bool(safe_meta.get("excluded")),
    })
    return {"inserted": inserted, "status": "ok"}


@app.post("/v1/browser-heartbeat")
def browser_heartbeat(status: BrowserHeartbeat) -> dict[str, str]:
    previous = dict(BROWSER_STATUS)
    safe_page = sanitize_browser_page(status.page.model_dump(exclude_none=True))
    excluded_page = bool(safe_page.get("hostname") or safe_page.get("title")) and browser_event_is_excluded(
        app="Browser",
        title=str(safe_page.get("title") or ""),
        hostname=str(safe_page.get("hostname") or ""),
        config=_runtime_config(),
    )
    if excluded_page:
        safe_page = {}
    BROWSER_STATUS.clear()
    BROWSER_STATUS.update(status.model_dump(exclude={"page"}))
    BROWSER_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    BROWSER_STATUS["expected_sensor_version"] = EXPECTED_BROWSER_SENSOR_VERSION
    BROWSER_STATUS["version_ok"] = bool(
        status.sensor_version and status.sensor_version == EXPECTED_BROWSER_SENSOR_VERSION
    )
    BROWSER_STATUS["excluded"] = excluded_page
    if safe_page.get("hostname"):
        BROWSER_STATUS["hostname"] = safe_page.get("hostname")
        BROWSER_STATUS["pathname"] = safe_page.get("pathname")
        BROWSER_STATUS["page_title"] = safe_page.get("title")
        BROWSER_STATUS["work_surface"] = _friendly_browser_surface(
            str(safe_page.get("hostname") or ""),
            str(safe_page.get("pathname") or ""),
            str(safe_page.get("title") or ""),
        )
    elif not excluded_page and previous.get("browser_session_id") == status.browser_session_id:
        # A transient tabs.query failure must not erase a still-valid active page.
        for key in ("hostname", "pathname", "page_title", "work_surface"):
            if previous.get(key):
                BROWSER_STATUS[key] = previous[key]
    return {"status": "ok"}


def _parse_observed_at(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _apply_live_browser_surface(result: dict[str, Any]) -> None:
    """Fill only unresolved *live* desktop browser rows from confirmed active-tab state.

    The heartbeat/browser-event timestamp is a lower bound: rows older than the
    current active-tab confirmation are left untouched, so switching tabs cannot
    retroactively relabel earlier desktop evidence.
    """
    hostname = str(BROWSER_STATUS.get("hostname") or "").strip()
    surface = str(BROWSER_STATUS.get("work_surface") or "").strip()
    confirmed_at = _parse_observed_at(BROWSER_STATUS.get("observed_at"))
    if not hostname or not surface or confirmed_at is None:
        return

    status_session = str(BROWSER_STATUS.get("work_session_id") or "")
    pathname = str(BROWSER_STATUS.get("pathname") or "")
    page_title = str(BROWSER_STATUS.get("page_title") or "")
    for item in result.get("recent_evidence") or []:
        if not isinstance(item, dict) or item.get("source") != "desktop" or item.get("work_surface"):
            continue
        if not is_browser_app(str(item.get("app") or "")):
            continue
        item_session = str(item.get("session_id") or "")
        if status_session and item_session and item_session != status_session:
            continue
        item_at = _parse_observed_at(item.get("observed_at"))
        if item_at is None or item_at < confirmed_at - 1.0:
            continue
        item["work_surface"] = surface
        item["container_app"] = str(item.get("app") or "")
        item["browser_hostname"] = hostname
        item["browser_pathname"] = pathname
        item["page_title"] = page_title
        item["browser_context_join"] = "live_active_tab"


@app.get("/v1/summary")
def get_summary(limit: int = 10000, scope: str = "all"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    result = apply_to_summary(summary(limit, since=since), limit=limit, since=since)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result["version"] = VERSION
    result["collector"] = dict(COLLECTOR_STATUS) if COLLECTOR_STATUS else None
    result["browser_sensor"] = dict(BROWSER_STATUS) if BROWSER_STATUS else None
    result["expected_browser_sensor_version"] = EXPECTED_BROWSER_SENSOR_VERSION
    if scope == "current":
        _apply_live_browser_surface(result)
    return redact_for_display(result)


@app.get("/v1/operational-summary")
def get_operational_summary(limit: int = 10000, scope: str = "all"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    result = summary(limit, since=since, operational=True)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result["version"] = VERSION
    return redact_for_display(result)


@app.get("/v1/events")
def get_events(query: str = "", app_name: str | None = None, limit: int = 100):
    return redact_for_display({"events": search_events(query=query, app=app_name, limit=limit), "data_layer": "raw_local_evidence"})


@app.get("/v1/context-events")
def get_context_events(query: str = "", surface: str | None = None, actor_id: str | None = None, limit: int = 100):
    events = search_context(query, surface=surface, actor_id=actor_id, limit=limit) if (query or surface or actor_id) else recent_context(limit)
    return redact_for_display({
        "events": events,
        "data_layer": "customer_context",
        "notice": "Customer-controlled context is presentation-redacted for email addresses, phone numbers and best-effort person names. Typed values and clipboard contents are not captured; rich local evidence used for inference is not destructively rewritten.",
    })


@app.get("/v1/operational-events")
def get_operational_events(query: str = "", surface: str | None = None, limit: int = 100):
    return redact_for_display({"events": search_operational_events(query=query, surface=surface, limit=limit), "data_layer": "operational_normalized"})


@app.get("/v1/semantic-activity")
def get_semantic_activity(limit: int = 200, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return redact_for_display({"events": semantic_activity(limit=limit, since=since)})


@app.get("/v1/operational-semantic-activity")
def get_operational_semantic_activity(limit: int = 200, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return redact_for_display({"events": semantic_activity(limit=limit, since=since, operational=True), "data_layer": "operational_normalized"})


@app.get("/v1/tasks")
def get_candidate_tasks(limit: int = 25000, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return redact_for_display(candidate_tasks(limit=limit, since=since))


@app.get("/v1/operational-sessions/{session_id}")
def get_operational_session(session_id: str, limit: int = 1000):
    events = operational_timeline(session_id, limit)
    if not events:
        raise HTTPException(status_code=404, detail="session not found")
    return redact_for_display({"session_id": session_id, "events": events, "data_layer": "operational_normalized"})


@app.get("/v1/context-sessions/{session_id}")
def get_context_session(session_id: str, limit: int = 1000):
    events = context_timeline(session_id, limit)
    if not events:
        raise HTTPException(status_code=404, detail="session not found")
    return redact_for_display({"session_id": session_id, "events": events, "data_layer": "customer_context"})


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, limit: int = 1000):
    events = timeline(session_id, limit)
    if not events:
        raise HTTPException(status_code=404, detail="session not found")
    return redact_for_display({"session_id": session_id, "events": events, "data_layer": "raw_local_evidence"})


@app.get("/v1/export/{fmt}")
def export_session(fmt: str, scope: str = "current", include_raw: bool = False, redact_names: bool = False, since: str | None = None, until: str | None = None):
    fmt = fmt.lower().strip()
    if fmt not in {"json", "xlsx", "csvzip"}:
        raise HTTPException(status_code=400, detail="format must be json, xlsx, or csvzip")
    from .export_window import selection
    try:
        window = selection(scope, since, until)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    bounds = {"since": window["since"], "until": window["until"]} if scope == "range" else {}
    from .db import title_protection_complete

    if not title_protection_complete():
        redact_names = True  # older rows may still hold names until the v0.108 migration finishes
    if redact_names:
        # Same redactor as Redacted AI context: names/identifiers replaced in place.
        from .ai_context import redact_contextually
        payload = redact_contextually(build_export_payload(scope=scope, include_raw=include_raw, **bounds))
    else:
        payload = redact_for_display(build_export_payload(scope=scope, include_raw=include_raw, **bounds))
    if fmt == "json":
        body = json_bytes(payload)
        media = "application/json"
    elif fmt == "xlsx":
        body = xlsx_bytes(payload)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        body = csv_zip_bytes(payload)
        media = "application/zip"
    filename = export_filename(fmt, include_raw=include_raw)
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD.read_text(encoding="utf-8")