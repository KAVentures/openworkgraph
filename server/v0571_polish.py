from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from connector.config import load_device_token, load_gateway_settings
from connector.policy import merge_policies, normalize_policy
from . import enterprise_app
from .context_layers import candidate_tasks, factual_context_timeline
from .main import CONFIG_PATH, ROOT
from .secure_app import app


_PASSIVE_STEP_ACTIONS = {
    "page view",
    "page_view",
    "tab activated",
    "tab_activated",
    "navigation",
    "navigation started",
    "navigation_started",
    "navigation requested",
    "navigation_requested",
    "navigation committed",
    "navigation_committed",
    "dom ready",
    "dom_ready",
    "pageshow",
    "heartbeat",
    "scroll",
    "mouse move",
    "mouse_move",
}
_CAPTURE_PATHS = {
    "/v1/capture/status",
    "/v1/capture/pause",
    "/v1/capture/resume",
    "/v1/capture/stop",
    "/v1/capture/start",
}


def _parse_ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _display_action(values: list[Any] | tuple[Any, ...] | None) -> str:
    """Choose a conservative action label that is actually tied to one context span."""
    for raw in reversed(list(values or [])):
        value = re.sub(r"\s+", " ", str(raw or "").strip()).replace("_", " ")
        low = value.lower()
        if not value or low in _PASSIVE_STEP_ACTIONS:
            continue
        if low.startswith(("scroll ", "mouse move ", "heartbeat ")):
            continue
        return value[:77] + "…" if len(value) > 80 else value
    return ""


def _run_steps(run: dict[str, Any], factual: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build surface/action steps only from ordered factual spans inside this run.

    This deliberately does not zip task.surfaces to task.semantic_actions: those
    arrays summarize different evidence and are not positionally aligned.
    """
    start = _parse_ts(run.get("started_at"))
    end = _parse_ts(run.get("ended_at"))
    session_id = str(run.get("session_id") or "")
    steps: list[dict[str, str]] = []

    if start is not None and end is not None:
        for row in factual:
            if session_id and str(row.get("session_id") or "") != session_id:
                continue
            row_start = _parse_ts(row.get("started_at"))
            row_end = _parse_ts(row.get("ended_at"))
            if row_start is None or row_end is None or row_end < start or row_start > end:
                continue
            surface = str(row.get("work_surface") or row.get("container_app") or "Unknown").strip() or "Unknown"
            action = _display_action(row.get("semantic_actions") or [])
            step = {"surface": surface, "action": action}
            if steps and steps[-1] == step:
                continue
            if steps and steps[-1]["surface"] == surface and not action:
                continue
            steps.append(step)

    if not steps:
        for surface in run.get("surfaces") or []:
            value = str(surface or "").strip()
            if not value:
                continue
            if steps and steps[-1]["surface"] == value:
                continue
            steps.append({"surface": value, "action": ""})
    return steps[:24]


def _pattern_steps(runs: list[dict[str, Any]], fallback_surfaces: list[str]) -> tuple[list[dict[str, str]], str]:
    candidates: list[tuple[tuple[str, str], ...]] = []
    for run in runs:
        sequence = tuple(
            (str(step.get("surface") or "Unknown"), str(step.get("action") or ""))
            for step in run.get("steps") or []
        )
        if sequence:
            candidates.append(sequence)
    if candidates:
        sequence = Counter(candidates).most_common(1)[0][0]
        steps = [{"surface": surface, "action": action} for surface, action in sequence]
        source = "ordered_factual_context"
    else:
        steps = [{"surface": str(surface), "action": ""} for surface in fallback_surfaces if surface]
        source = "surface_only_fallback"
    return steps, source


def _build_patterns_payload(scope: str, derived: dict[str, Any], factual: list[dict[str, Any]], since: str | None) -> dict[str, Any]:
    tasks = list(derived.get("tasks") or [])
    output: list[dict[str, Any]] = []
    for pattern in derived.get("patterns") or []:
        members = [
            task
            for task in tasks
            if task.get("completion_observed") and enterprise_app._task_matches_pattern(task, pattern)
        ]
        members.sort(key=lambda x: str(x.get("started_at") or ""))
        surfaces = [str(x) for x in (pattern.get("surfaces") or []) if x]
        name = (
            " → ".join(surfaces)
            if len(surfaces) > 1
            else str(
                pattern.get("suggested_label")
                or pattern.get("task_family")
                or pattern.get("signature")
                or "Repeated workflow"
            )
        )
        runs: list[dict[str, Any]] = []
        for task in members:
            run = {
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
                "event_ids": enterprise_app._event_ids_for_task(task, factual),
            }
            run["steps"] = _run_steps(run, factual)
            run["ends_with"] = _display_action(task.get("action_skeleton") or []) or _display_action(task.get("semantic_actions") or [])
            runs.append(run)

        steps, step_source = _pattern_steps(runs, surfaces)
        ends_with = next((str(step.get("action") or "") for step in reversed(steps) if step.get("action")), "")
        if not ends_with:
            ends_with = _display_action(pattern.get("action_skeleton") or [])
        output.append(
            {
                **pattern,
                "name": name,
                "steps": steps,
                "step_source": step_source,
                "ends_with": ends_with,
                "runs": runs,
                "run_count_with_evidence": len(runs),
                "labels_are_suggestions": True,
            }
        )
    return {
        "scope": scope,
        "run_started_at": since,
        "patterns": output,
        "needs_review": True,
        "derived_task_inference_authoritative": False,
        "inference": {
            **(derived.get("inference") or {}),
            "pattern_steps_source": "ordered_factual_context",
            "surface_action_arrays_zipped_positionally": False,
        },
    }


def corrected_patterns(scope: str = "current") -> dict[str, Any]:
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    since = enterprise_app._current_run_start() if scope == "current" else None
    derived = candidate_tasks(limit=100000, since=since)
    factual = factual_context_timeline(limit=100000, since=since)
    return _build_patterns_payload(scope, derived, factual, since)


def _capture_markers(value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    state = str(payload.get("state") or "recording")
    changed = payload.get("state_changed_at")
    payload["paused_at"] = changed if state == "paused" else None
    payload["stopped_at"] = changed if state == "stopped" else None
    return payload


def corrected_capture_status() -> dict[str, Any]:
    return _capture_markers(enterprise_app.get_capture_status())


def corrected_pause_capture() -> dict[str, Any]:
    return _capture_markers(enterprise_app.pause_capture())


def corrected_resume_capture() -> dict[str, Any]:
    return _capture_markers(enterprise_app.resume_capture())


def corrected_stop_capture() -> dict[str, Any]:
    return _capture_markers(enterprise_app.stop_capture())


def corrected_start_capture() -> dict[str, Any]:
    return _capture_markers(enterprise_app.start_new_capture_run())


def _auth_dir() -> Path:
    root = CONFIG_PATH.resolve().parent
    return Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", root / "data" / "auth"))


def _public_policy(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "share_excluded": bool(value.get("share_excluded", False)),
        "share_window_titles": bool(value.get("share_window_titles", False)),
        "share_metadata": bool(value.get("share_metadata", False)),
        "allowed_event_types": list(value.get("allowed_event_types") or []),
        "deny_all_event_types": bool(value.get("_deny_all_event_types", False)),
        "strip_metadata_keys": list(value.get("strip_metadata_keys") or []),
    }


def sharing_policy_snapshot() -> dict[str, Any]:
    settings = load_gateway_settings(CONFIG_PATH, auth_dir=_auth_dir())
    local = normalize_policy(settings.local_policy)
    token = load_device_token(settings)
    connected = bool(settings.enabled and settings.url and token)
    base = {
        "connected": connected,
        "local_policy": _public_policy(local),
        "organization_policy": None,
        "effective_policy": _public_policy(merge_policies(local, {})) if not connected else None,
        "policy_current": not connected,
        "policy_source": "local_only" if not connected else "gateway_live",
        "sync_fails_closed_if_policy_unavailable": True,
        "never_shared": [
            "typed_text",
            "clipboard_contents",
            "ordinary_key_identities",
            "password_values",
            "screenshot_bytes",
        ],
    }
    if not connected or enterprise_app._demo_mode():
        return base

    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=4,
            verify=settings.verify_tls,
        ) as client:
            response = client.get(f"{settings.url}/v1/device-policy")
            response.raise_for_status()
            data = response.json()
        remote = data.get("policy") if isinstance(data.get("policy"), dict) else {}
        effective = merge_policies(local, remote)
        base.update(
            {
                "organization_policy": _public_policy(normalize_policy(remote)),
                "effective_policy": _public_policy(effective),
                "policy_current": True,
            }
        )
    except Exception:
        # Match sync semantics: inability to obtain the organization policy must
        # not be presented as a broader effective policy. The sync worker also
        # fails closed rather than uploading under an assumed/stale policy.
        base["policy_current"] = False
        base["policy_source"] = "gateway_unavailable"
        base["effective_policy"] = None
    return base


def get_sharing_policy() -> dict[str, Any]:
    return sharing_policy_snapshot()


def dashboard_polish_script() -> Response:
    path = ROOT / "dashboard" / "v0571_polish.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


def _replace_route(path: str, method: str, endpoint: Any) -> None:
    wanted = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and wanted in {str(x).upper() for x in (getattr(route, "methods", None) or set())}
        )
    ]
    app.add_api_route(path, endpoint, methods=[wanted])


def _install_routes() -> None:
    _replace_route("/v1/patterns", "GET", corrected_patterns)
    _replace_route("/v1/capture/status", "GET", corrected_capture_status)
    _replace_route("/v1/capture/pause", "POST", corrected_pause_capture)
    _replace_route("/v1/capture/resume", "POST", corrected_resume_capture)
    _replace_route("/v1/capture/stop", "POST", corrected_stop_capture)
    _replace_route("/v1/capture/start", "POST", corrected_start_capture)
    app.add_api_route("/v1/sharing-policy", get_sharing_policy, methods=["GET"])
    app.add_api_route("/v0571-polish.js", dashboard_polish_script, methods=["GET"])


async def _inject_polish_script(request: Request, call_next):
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
    marker = '<script src="/v0571-polish.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


if not getattr(app.state, "owg_v0571_polish_installed", False):
    _install_routes()
    app.middleware("http")(_inject_polish_script)
    app.state.owg_v0571_polish_installed = True


__all__ = [
    "corrected_patterns",
    "sharing_policy_snapshot",
    "_build_patterns_payload",
    "_run_steps",
]
