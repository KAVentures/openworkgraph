from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from connector.control import set_sharing, status as gateway_status
from connector.runtime import restart_sync_worker, start_sync_worker, status as worker_status, stop_sync_worker
from connector.service import disconnect_endpoint, enroll_endpoint
from shared.capture_control import (
    filter_recordable,
    initialize_run,
    public_status,
    read_state,
    set_state,
)
from shared.lifespan import extend_lifespan
from . import main as main_module
from .context_layers import candidate_tasks, factual_context_timeline
from .main import CONFIG_PATH, ROOT
from .secure_app import app


class GatewayEnrollmentRequest(BaseModel):
    gateway_url: str
    organization_id: str
    actor_id: str = ""
    enrollment_token: str
    verify_tls: bool = True
    allow_insecure_http: bool = False


class GatewaySharingRequest(BaseModel):
    enabled: bool


class GatewayDisconnectRequest(BaseModel):
    force_local: bool = False


def _demo_mode() -> bool:
    return os.getenv("WORKFLOW_OBSERVER_MODE", "observe") == "demo"


def _combined_status() -> dict[str, Any]:
    value = gateway_status(CONFIG_PATH)
    value.update(worker_status())
    value["local_capture_continues_when_paused"] = True
    value["cloud_account_required"] = False
    value["raw_rich_evidence_canonical"] = True
    if _demo_mode():
        value["mode"] = "demo_local_only"
        value["worker_running"] = False
    return value


def _start_optional_gateway_worker() -> None:
    if not _demo_mode():
        start_sync_worker(CONFIG_PATH)


def _stop_optional_gateway_worker() -> None:
    stop_sync_worker()


def _initialize_capture_control() -> None:
    initialize_run(os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT"))


# Keep server/main.py's established route contracts, but harden the actual insert
# and identity-learning sinks used by those routes. This means desktop batches and
# browser events both obey the same pause/stop intervals without duplicating the
# ingestion routes or weakening secure_app authentication.
if not getattr(main_module, "_owg_capture_control_wrapped", False):
    _original_insert_events = main_module.insert_events
    _original_learn_identities = main_module.learn_persistent_identities

    def _controlled_insert_events(events: list[dict[str, Any]]) -> int:
        kept, _suppressed = filter_recordable([dict(item) for item in events])
        return _original_insert_events(kept) if kept else 0

    def _controlled_learn_identities(value: Any) -> None:
        if isinstance(value, list):
            kept, _suppressed = filter_recordable([dict(item) for item in value if isinstance(item, dict)])
            if kept:
                _original_learn_identities(kept)
            return
        if isinstance(value, dict):
            kept, _suppressed = filter_recordable([dict(value)])
            if kept:
                _original_learn_identities(kept[0])
            return
        _original_learn_identities(value)

    main_module.insert_events = _controlled_insert_events
    main_module.learn_persistent_identities = _controlled_learn_identities
    main_module._owg_capture_control_wrapped = True


extend_lifespan(
    app,
    startup=lambda: (_initialize_capture_control(), _start_optional_gateway_worker()),
    shutdown=_stop_optional_gateway_worker,
)


def _parse(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _current_run_start() -> str | None:
    return str(read_state().get("run_started_at") or os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or "") or None


def _capture_status() -> dict[str, Any]:
    since = _current_run_start()
    engaged = 0.0
    try:
        engaged = float(main_module.summary(25000, since=since).get("total_engaged_seconds") or 0.0)
    except Exception:
        pass
    value = public_status(engaged_seconds=engaged)
    value["collector_alive"] = bool(main_module.COLLECTOR_STATUS)
    value["browser_sensor_alive"] = bool(main_module.BROWSER_STATUS)
    value["demo"] = _demo_mode()
    return value


def _require_live_capture() -> None:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Capture controls are disabled in demo mode")


@app.get("/v1/capture/status")
def get_capture_status() -> dict[str, Any]:
    return _capture_status()


@app.post("/v1/capture/pause")
def pause_capture() -> dict[str, Any]:
    _require_live_capture()
    set_state("pause")
    return _capture_status()


@app.post("/v1/capture/resume")
def resume_capture() -> dict[str, Any]:
    _require_live_capture()
    set_state("resume")
    return _capture_status()


@app.post("/v1/capture/stop")
def stop_capture() -> dict[str, Any]:
    _require_live_capture()
    set_state("stop")
    return _capture_status()


@app.post("/v1/capture/start")
def start_new_capture_run() -> dict[str, Any]:
    _require_live_capture()
    value = set_state("start")
    # server/main.py resolves current-run summary scope from this environment
    # value on every request. Update the API process so a Start new run resets the
    # dashboard/exports without restarting the authenticated local server.
    os.environ["WORKFLOW_OBSERVER_RUN_STARTED_AT"] = str(value["run_started_at"])
    main_module.COLLECTOR_STATUS.clear()
    return _capture_status()


@app.get("/v1/timeline")
def get_work_surface_timeline(scope: str = "current") -> dict[str, Any]:
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    since = _current_run_start() if scope == "current" else None
    factual = factual_context_timeline(limit=100000, since=since)
    merged: list[dict[str, Any]] = []
    for row in factual:
        surface = str(row.get("work_surface") or row.get("container_app") or "Unknown")
        start = str(row.get("started_at") or "")
        end = str(row.get("ended_at") or "")
        item = {
            "surface": surface,
            "start": start,
            "end": end,
            "engaged_seconds": round(float(row.get("engaged_seconds") or 0.0), 3),
            "foreground_seconds": round(float(row.get("foreground_seconds") or 0.0), 3),
            "session_id": str(row.get("session_id") or ""),
            "evidence_event_ids": list(row.get("evidence_event_ids") or []),
        }
        if merged and merged[-1]["surface"] == surface and merged[-1]["session_id"] == item["session_id"]:
            previous_end = _parse(merged[-1]["end"])
            current_start = _parse(start)
            if previous_end is not None and current_start is not None and 0 <= current_start - previous_end <= 3.0:
                merged[-1]["end"] = end
                merged[-1]["engaged_seconds"] = round(merged[-1]["engaged_seconds"] + item["engaged_seconds"], 3)
                merged[-1]["foreground_seconds"] = round(merged[-1]["foreground_seconds"] + item["foreground_seconds"], 3)
                merged[-1]["evidence_event_ids"] = list(dict.fromkeys([*merged[-1]["evidence_event_ids"], *item["evidence_event_ids"]]))[:200]
                continue
        merged.append(item)
    return {
        "scope": scope,
        "run_started_at": since,
        "spans": merged,
        "temporal_source": "focus_spans",
        "browser_context_role": "surface_attribution_only",
        "double_count_browser_events": False,
    }


def _task_matches_pattern(task: dict[str, Any], pattern: dict[str, Any]) -> bool:
    family = str(task.get("task_family") or "")
    if family and family == str(pattern.get("task_family") or ""):
        return True
    surfaces = ">".join(str(x).lower() for x in (task.get("surfaces") or [])[:4])
    skeleton = ">".join(str(x) for x in (task.get("action_skeleton") or []))
    fallback = f"{surfaces}|{skeleton}".strip("|")
    return fallback == str(pattern.get("signature") or "")


def _event_ids_for_task(task: dict[str, Any], factual: list[dict[str, Any]]) -> list[str]:
    start = _parse(task.get("started_at"))
    end = _parse(task.get("ended_at"))
    session = str(task.get("session_id") or "")
    ids: list[str] = [str(x) for x in (task.get("anchor_event_ids") or []) if x]
    if start is None or end is None:
        return list(dict.fromkeys(ids))
    for row in factual:
        if session and str(row.get("session_id") or "") != session:
            continue
        row_start = _parse(row.get("started_at"))
        row_end = _parse(row.get("ended_at"))
        if row_start is None or row_end is None or row_end < start or row_start > end:
            continue
        ids.extend(str(x) for x in (row.get("evidence_event_ids") or []) if x)
    return list(dict.fromkeys(ids))[:1000]


@app.get("/v1/patterns")
def get_repeated_patterns(scope: str = "current") -> dict[str, Any]:
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    since = _current_run_start() if scope == "current" else None
    derived = candidate_tasks(limit=100000, since=since)
    factual = factual_context_timeline(limit=100000, since=since)
    tasks = list(derived.get("tasks") or [])
    output: list[dict[str, Any]] = []
    for pattern in derived.get("patterns") or []:
        members = [task for task in tasks if task.get("completion_observed") and _task_matches_pattern(task, pattern)]
        members.sort(key=lambda x: str(x.get("started_at") or ""))
        surfaces = [str(x) for x in (pattern.get("surfaces") or []) if x]
        actions = [str(x) for x in (pattern.get("action_skeleton") or []) if x]
        name = " → ".join(surfaces) if len(surfaces) > 1 else str(pattern.get("suggested_label") or pattern.get("task_family") or pattern.get("signature") or "Repeated workflow")
        steps = [
            {"surface": surface, "action": actions[index] if index < len(actions) else ""}
            for index, surface in enumerate(surfaces)
        ]
        runs = []
        for task in members:
            runs.append({
                "task_id": task.get("task_id"),
                "session_id": task.get("session_id"),
                "started_at": task.get("started_at"),
                "ended_at": task.get("ended_at"),
                "surfaces": list(task.get("surfaces") or []),
                "semantic_actions": list(task.get("semantic_actions") or []),
                "engaged_seconds": float(task.get("engaged_seconds") or 0.0),
                "elapsed_seconds": float(task.get("elapsed_seconds") or 0.0),
                "boundary": dict(task.get("boundary") or {}),
                "outcomes": list(task.get("outcomes") or []),
                "event_ids": _event_ids_for_task(task, factual),
            })
        output.append({
            **pattern,
            "name": name,
            "steps": steps,
            "runs": runs,
            "run_count_with_evidence": len(runs),
            "labels_are_suggestions": True,
        })
    return {
        "scope": scope,
        "run_started_at": since,
        "patterns": output,
        "needs_review": True,
        "derived_task_inference_authoritative": False,
        "inference": derived.get("inference") or {},
    }


@app.get("/v1/gateway-status")
def get_gateway_status() -> dict[str, Any]:
    return _combined_status()


@app.post("/v1/gateway-enroll")
def enroll_gateway(request: GatewayEnrollmentRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway enrollment is disabled in demo mode")
    try:
        result = enroll_endpoint(
            CONFIG_PATH,
            gateway_url=request.gateway_url,
            organization_id=request.organization_id,
            actor_id=request.actor_id,
            enrollment_token=request.enrollment_token,
            verify_tls=request.verify_tls,
            allow_insecure_http=request.allow_insecure_http,
        )
        restart_sync_worker(CONFIG_PATH)
        return {**result, **_combined_status()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gateway enrollment failed: {str(exc)[:300]}") from exc


@app.post("/v1/gateway-sharing")
def update_gateway_sharing(request: GatewaySharingRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway sharing is disabled in demo mode")
    try:
        result = set_sharing(CONFIG_PATH, request.enabled)
        if request.enabled:
            start_sync_worker(CONFIG_PATH)
        return {**result, **worker_status()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc


@app.post("/v1/gateway-disconnect")
def disconnect_gateway(request: GatewayDisconnectRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway controls are disabled in demo mode")
    try:
        stop_sync_worker()
        result = disconnect_endpoint(CONFIG_PATH, require_remote_revoke=not request.force_local)
        return {**result, **worker_status()}
    except Exception as exc:
        start_sync_worker(CONFIG_PATH)
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not revoke the device credential at the Gateway. Sharing remains configured. "
                "Pause sharing instead, or retry when the Gateway is reachable. "
                f"Details: {str(exc)[:220]}"
            ),
        ) from exc


@app.get("/gateway-panel.js")
def gateway_panel_script() -> Response:
    path = ROOT / "dashboard" / "gateway_panel.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


@app.middleware("http")
async def inject_gateway_panel(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response
    marker = '<script src="/gateway-panel.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["app"]
