from __future__ import annotations

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
from .analytics import (search_events, search_operational_events, summary, timeline, operational_timeline, semantic_activity, candidate_tasks)
from .db import init_db, insert_events
from .exporter import build_export_payload, csv_zip_bytes, export_filename, json_bytes, xlsx_bytes

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"

app = FastAPI(title="OpenWorkGraph / Workflow Observer API", version="0.8.0-v27")
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
    device_id: str
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
    session_id: str
    observed_at: str
    app: str | None = None
    window_title: str | None = None
    focus_elapsed_seconds: float = 0
    activity: dict[str, Any] = Field(default_factory=dict)
    keyboard_sensor: bool = False


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
    observed_at: str
    action: str
    page: BrowserPage = Field(default_factory=BrowserPage)
    target: BrowserTarget = Field(default_factory=BrowserTarget)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserHeartbeat(BaseModel):
    observed_at: str
    status: str = "connected"


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": os.getenv("WORKFLOW_OBSERVER_MODE", "observe")}


@app.post("/v1/events")
def ingest(batch: EventBatch) -> dict[str, int]:
    return {"inserted": insert_events([e.model_dump() for e in batch.events])}


@app.post("/v1/heartbeat")
def heartbeat(status: Heartbeat) -> dict[str, str]:
    COLLECTOR_STATUS.clear()
    COLLECTOR_STATUS.update(status.model_dump())
    COLLECTOR_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "ok"}


def _browser_context() -> tuple[str, str, str]:
    app_name = str(COLLECTOR_STATUS.get("app") or "Browser")
    if not is_browser_app(app_name):
        app_name = "Browser"
    device_id = str(COLLECTOR_STATUS.get("device_id") or "browser-extension")
    session_id = str(COLLECTOR_STATUS.get("session_id") or "browser-extension")
    return app_name, device_id, session_id


@app.post("/v1/browser-events")
def browser_event(event: BrowserEvent) -> dict[str, int | str]:
    app_name, device_id, session_id = _browser_context()
    page = event.page.model_dump(exclude_none=True)
    target = event.target.model_dump(exclude_none=True)
    metadata = dict(event.metadata or {})
    metadata.update({
        "source": "browser_extension",
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
        "event_id": str(uuid.uuid4()),
        "observed_at": event.observed_at,
        "device_id": device_id,
        "session_id": session_id,
        "app": app_name,
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
    })
    return {"inserted": inserted, "status": "ok"}


@app.post("/v1/browser-heartbeat")
def browser_heartbeat(status: BrowserHeartbeat) -> dict[str, str]:
    BROWSER_STATUS.clear()
    BROWSER_STATUS.update(status.model_dump())
    BROWSER_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "ok"}


@app.get("/v1/summary")
def get_summary(limit: int = 10000, scope: str = "all"):
    since = None
    if scope == "current":
        since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result = summary(limit, since=since)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result["collector"] = dict(COLLECTOR_STATUS) if COLLECTOR_STATUS else None
    result["browser_sensor"] = dict(BROWSER_STATUS) if BROWSER_STATUS else None
    return result


@app.get("/v1/operational-summary")
def get_operational_summary(limit: int = 10000, scope: str = "all"):
    """Privacy-safe summary intended for MCP/AI and long-lived analytics."""
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    result = summary(limit, since=since, operational=True)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    return result


@app.get("/v1/events")
def get_events(query: str = "", app_name: str | None = None, limit: int = 100):
    """Rich local evidence. Do not expose this endpoint to external AI by default."""
    return {"events": search_events(query=query, app=app_name, limit=limit), "data_layer": "raw_local_evidence"}


@app.get("/v1/operational-events")
def get_operational_events(query: str = "", surface: str | None = None, limit: int = 100):
    """Content-minimized operational events safe for normal MCP/AI use."""
    return {"events": search_operational_events(query=query, surface=surface, limit=limit), "data_layer": "operational_normalized"}


@app.get("/v1/semantic-activity")
def get_semantic_activity(limit: int = 200, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return {"events": semantic_activity(limit=limit, since=since)}


@app.get("/v1/operational-semantic-activity")
def get_operational_semantic_activity(limit: int = 200, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return {"events": semantic_activity(limit=limit, since=since, operational=True), "data_layer": "operational_normalized"}


@app.get("/v1/tasks")
def get_candidate_tasks(limit: int = 25000, scope: str = "current"):
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return candidate_tasks(limit=limit, since=since)


@app.get("/v1/operational-sessions/{session_id}")
def get_operational_session(session_id: str, limit: int = 1000):
    events = operational_timeline(session_id, limit)
    if not events:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "events": events, "data_layer": "operational_normalized"}


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, limit: int = 1000):
    events = timeline(session_id, limit)
    if not events:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "events": events}



@app.get("/v1/export/{fmt}")
def export_session(fmt: str, scope: str = "current", include_raw: bool = False):
    """Download the captured session as JSON, XLSX, or a ZIP of CSV tables.

    AI-safe normalized data is the default. Raw local evidence is included only
    when the user explicitly asks for it.
    """
    fmt = fmt.lower().strip()
    if fmt not in {"json", "xlsx", "csvzip"}:
        raise HTTPException(status_code=400, detail="format must be json, xlsx, or csvzip")
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    payload = build_export_payload(scope=scope, include_raw=include_raw)
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
