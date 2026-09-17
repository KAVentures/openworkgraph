from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from browser_utils import is_browser_app
from .analytics import (
    search_events, search_operational_events, summary, timeline,
    operational_timeline, semantic_activity, candidate_tasks,
)
from .context import search_context, recent_context, context_timeline
from .db import init_db, insert_events
from .exporter import build_export_payload, csv_zip_bytes, export_filename, json_bytes, xlsx_bytes
from .presentation import redact_for_display

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "0.34.0"
try:
    _manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    EXPECTED_BROWSER_SENSOR_VERSION = str(_manifest.get("version_name") or _manifest.get("version") or "")
except Exception:
    EXPECTED_BROWSER_SENSOR_VERSION = ""

app = FastAPI(title="OpenWorkGraph / Workflow Observer API", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8787", "http://localhost:8787"],
    allow_origin_regex=r"^(chrome-extension|moz-extension|safari-web-extension)://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "authorization"],
)

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


class EventBatch(BaseModel):
    events: list[Event]


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


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": os.getenv("WORKFLOW_OBSERVER_MODE", "observe"),
        "version": VERSION,
    }


@app.post("/v1/events")
def ingest(batch: EventBatch) -> dict[str, int]:
    return {"inserted": insert_events([e.model_dump() for e in batch.events])}


@app.post("/v1/heartbeat")
def heartbeat(status: Heartbeat) -> dict[str, str]:
    COLLECTOR_STATUS.clear()
    COLLECTOR_STATUS.update(status.model_dump())
    COLLECTOR_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "ok"}


@app.get("/v1/browser-context")
def browser_context() -> dict[str, str]:
    """Return local capture identity so the browser can stamp events before queuing.

    This is intentionally loopback-only with the rest of the prototype API. It
    prevents an event captured during one observer session from being attributed
    to a later session when an offline browser queue is replayed.
    """
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
        # Prefer capture-time identity carried by the event. Current collector
        # status is only a fallback for old extension versions.
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
        },
    })
    normalized = {
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
    inserted = insert_events([normalized])
    BROWSER_STATUS.clear()
    BROWSER_STATUS.update({
        "observed_at": event.observed_at,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": "connected",
        "hostname": page.get("hostname"),
        "last_action": event.action,
        "sensor_id": ctx["sensor_id"],
        "sensor_version": event.sensor_version,
        "expected_sensor_version": EXPECTED_BROWSER_SENSOR_VERSION,
        "version_ok": bool(event.sensor_version and event.sensor_version == EXPECTED_BROWSER_SENSOR_VERSION),
    })
    return {"inserted": inserted, "status": "ok"}


@app.post("/v1/browser-heartbeat")
def browser_heartbeat(status: BrowserHeartbeat) -> dict[str, str]:
    BROWSER_STATUS.clear()
    BROWSER_STATUS.update(status.model_dump())
    BROWSER_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    BROWSER_STATUS["expected_sensor_version"] = EXPECTED_BROWSER_SENSOR_VERSION
    BROWSER_STATUS["version_ok"] = bool(
        status.sensor_version and status.sensor_version == EXPECTED_BROWSER_SENSOR_VERSION
    )
    return {"status": "ok"}


@app.get("/v1/summary")
def get_summary(limit: int = 10000, scope: str = "all"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    result = summary(limit, since=since)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result["version"] = VERSION
    result["collector"] = dict(COLLECTOR_STATUS) if COLLECTOR_STATUS else None
    result["browser_sensor"] = dict(BROWSER_STATUS) if BROWSER_STATUS else None
    result["expected_browser_sensor_version"] = EXPECTED_BROWSER_SENSOR_VERSION
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
def export_session(fmt: str, scope: str = "current", include_raw: bool = False):
    """Download the captured session as JSON, XLSX, or a ZIP of CSV tables.

    ``include_raw`` retains the raw event structure and fields, but presentation
    redaction is still applied to names/email/phone text before bytes leave the
    local API. The database itself is never rewritten by this endpoint.
    """
    fmt = fmt.lower().strip()
    if fmt not in {"json", "xlsx", "csvzip"}:
        raise HTTPException(status_code=400, detail="format must be json, xlsx, or csvzip")
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    payload = redact_for_display(build_export_payload(scope=scope, include_raw=include_raw))
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
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD.read_text(encoding="utf-8")
