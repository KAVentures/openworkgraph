from __future__ import annotations

"""Authenticated local API for temporary, employee-reviewed workflow discovery."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import zipfile
from typing import Any

from fastapi import HTTPException, Request, Response

from shared.discovery_package import observed_tools_inventory
from shared.discovery_scope import (
    answer_question,
    approve_share,
    deactivate_session,
    finish_session,
    mark_purged,
    public_state,
    read_state,
    save_question,
    set_excluded_execution_ids,
    set_excluded_event_ids,
    set_implementation_context,
    set_handoff_purpose,
    start_session,
)
from connector.control import (
    _max_local_agent_message_id,
    _max_local_event_id,
    _paths as gateway_paths,
    set_sharing as set_gateway_sharing,
    status as gateway_status,
)
from shared.lifespan import extend_lifespan
from .ai_context import redact_contextually
from .db import connect
from .main import CONFIG_PATH, COLLECTOR_STATUS
from .local_reference_lookup import expand_resource_references
from .procedural_feedback import (
    _discovery_handoff_bundle,
    _review_candidates_with_readable_steps,
    _scope_gap_question,
    _suggested_questions,
    _safe_event_action,
    _safe_event_surface,
)
from .secure_app import app
from .workflow_evidence import build_workflow_evidence, list_workflow_evidence_candidates

ROOT = Path(__file__).resolve().parents[1]


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _capture_running() -> bool:
    """Discovery should never start while the desktop collector is absent/stale."""
    received = _parse_time(COLLECTOR_STATUS.get("received_at"))
    if received is None:
        return False
    return (datetime.now(timezone.utc) - received).total_seconds() <= 20


def _filter_excluded_events(bundle: dict[str, Any], excluded: set[str]) -> dict[str, Any]:
    if not excluded:
        return bundle
    for group in bundle.get("canonical_evidence") or []:
        if not isinstance(group, dict):
            continue
        group["events"] = [
            event for event in (group.get("events") or [])
            if str((event or {}).get("event_id") or "") not in excluded
        ]
    return bundle


def _review_steps(bundles: list[dict[str, Any]], excluded: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_browser_surface = ""
    for bundle in bundles:
        for group in bundle.get("canonical_evidence") or []:
            execution_id = str(group.get("execution_id") or "")
            for event in group.get("events") or []:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("event_id") or "")
                event_type = str(event.get("event_type") or "")
                if not event_id or not event_type.startswith(("browser_", "screen_")):
                    continue
                surface = _safe_event_surface(event, last_browser_surface=last_browser_surface)
                if event_type.startswith("browser_") and surface and surface != "Browser":
                    last_browser_surface = surface
                action = _safe_event_action(event)
                if not action:
                    continue
                rows.append({
                    "event_id": event_id,
                    "execution_id": execution_id,
                    "step": f"{surface} · {action}",
                    "observed_at": event.get("observed_at"),
                    "included": event_id not in excluded,
                })
                if len(rows) >= 500:
                    return rows
    return rows


_HANDOFF_INSTRUCTIONS = {
    "understand": "Use the package to understand how the work actually happens. Separate observed evidence from employee-provided explanations and identify material unknowns.",
    "improve": "Use the package to identify friction, redundant handoffs, and process improvements. Verify downstream dependencies before proposing that an observed step be removed.",
    "automate": "Use the package to design automation against the current capability frontier. Check live tools/connectors, treat missing historical payload as potentially fetchable at execution time, and use TEST where feasibility is uncertain.",
    "build_tool": "Use the package as implementation evidence for a tool that supports this workflow. Preserve human-provided goals separately from observations, define interfaces around the real tools/files/records observed, and validate with live source-system test data.",
}


def _handoff_instruction(state: dict[str, Any]) -> str:
    return _HANDOFF_INSTRUCTIONS.get(
        str(state.get("handoff_purpose") or "automate"),
        _HANDOFF_INSTRUCTIONS["automate"],
    )


def _pause_gateway_if_connected() -> tuple[bool | None, bool]:
    """Pause an already-connected Gateway so review happens before any upload."""
    try:
        before = gateway_status(CONFIG_PATH)
    except Exception:
        return None, False
    was_paused = bool(before.get("sharing_paused"))
    if was_paused:
        return True, False
    if before.get("mode") != "connected":
        return False, False
    set_gateway_sharing(CONFIG_PATH, False)
    return False, True


def _restore_gateway_if_we_paused(state: dict[str, Any]) -> dict[str, Any] | None:
    if not state.get("gateway_paused_by_discovery"):
        return None
    try:
        return set_gateway_sharing(CONFIG_PATH, True)
    except Exception as exc:
        return {"error": str(exc), "sharing_remains_paused": True}


def _purge_discovery_evidence(*, force: bool = False) -> dict[str, Any]:
    state = read_state()
    if state.get("purged_at"):
        return {
            "purged": True,
            "purged_at": state.get("purged_at"),
            "deleted_event_rows": int(state.get("purged_event_rows") or 0),
        }
    start = _parse_time(state.get("starts_at"))
    end = _parse_time(state.get("ends_at"))
    if start is None or end is None:
        return {"purged": False, "reason": "no_completed_discovery_window"}
    if not force:
        due = end + timedelta(days=int(state.get("retention_days_after_end") or 14))
        if datetime.now(timezone.utc) < due:
            return {"purged": False, "reason": "retention_not_due", "purge_due_at": due.isoformat()}
    if str(state.get("status") or "") == "active" and datetime.now(timezone.utc) < end:
        return {"purged": False, "reason": "discovery_still_active"}

    with connect() as conn:
        rows = conn.execute(
            "SELECT event_id FROM events WHERE observed_at >= ? AND observed_at < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        event_ids = [str(row["event_id"]) for row in rows]
        for offset in range(0, len(event_ids), 500):
            chunk = event_ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(f"DELETE FROM context_events WHERE event_id IN ({placeholders})", tuple(chunk))
            conn.execute(f"DELETE FROM normalized_events WHERE event_id IN ({placeholders})", tuple(chunk))
            conn.execute(f"DELETE FROM events WHERE event_id IN ({placeholders})", tuple(chunk))
    marked = mark_purged(len(event_ids))
    return {
        "purged": True,
        "purged_at": marked.get("purged_at"),
        "deleted_event_rows": len(event_ids),
        "scope": "canonical events observed inside the Discovery Mode window",
        "agent_session_message_retention": "separate existing opt-in policy",
    }


def _startup_discovery_cleanup() -> None:
    try:
        _purge_discovery_evidence(force=False)
    except Exception:
        pass


extend_lifespan(app, startup=_startup_discovery_cleanup)


async def _payload(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return value


def _window(state: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        str(state.get("starts_at") or "") or None,
        str(state.get("ends_at") or "") or None,
    )


def _selected_family_runs(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    since, until = _window(state)
    families = list_workflow_evidence_candidates(
        since=since,
        until=until,
        source_event_limit=25_000,
        min_runs=2,
        limit=20,
    )
    excluded = {str(x) for x in state.get("excluded_execution_ids") or []}
    bundles: list[dict[str, Any]] = []
    for family in families.get("families") or []:
        ids = [
            str(x) for x in family.get("execution_ids") or []
            if str(x) and str(x) not in excluded
        ]
        if not ids:
            continue
        try:
            bundle = build_workflow_evidence(
                execution_ids=ids,
                since=since,
                until=until,
                source_event_limit=25_000,
                max_runs=min(12, len(ids)),
                max_events_per_run=100,
                include_canonical_evidence=True,
            )
        except Exception:
            continue
        bundle = _filter_excluded_events(
            bundle,
            {str(x) for x in state.get("excluded_event_ids") or []},
        )
        bundles.append(bundle)
    return families, bundles


def _package(state: dict[str, Any]) -> dict[str, Any]:
    families, bundles = _selected_family_runs(state)
    saved = list(state.get("questions") or [])
    answered = [x for x in saved if str(x.get("answer") or "").strip()]
    unanswered = [x for x in saved if not str(x.get("answer") or "").strip()]
    suggested = _suggested_questions(bundles)
    handoff_bundles = [_discovery_handoff_bundle(bundle) for bundle in bundles]
    observed_tools = observed_tools_inventory(bundles)
    selected_runs = sum(len((b.get("selector") or {}).get("execution_ids") or (b.get("executions") or [])) for b in bundles)
    return {
        "format": "openworkgraph.discovery-package.v1",
        "study": {
            "session_id": state.get("session_id"),
            "name": state.get("name"),
            "purpose": state.get("purpose"),
            "implementation_context": {
                **(state.get("implementation_context") or {}),
                "source": "human_provided",
            },
            "handoff_purpose": state.get("handoff_purpose") or "automate",
            "starts_at": state.get("starts_at"),
            "ends_at": state.get("ends_at"),
            "status": public_state().get("status"),
            "capture_scope": {
                "allowed_apps": state.get("allowed_apps") or [],
                "allowed_browser_hosts": state.get("allowed_browser_hosts") or [],
                "allow_unresolved_browser_container": bool(state.get("allow_unresolved_browser_container")),
                "positive_allowlist_enforced_before_persistence": True,
                "out_of_scope_content_replaced_with_timing_only_markers_when_provable": True,
            },
        },
        "coverage": {
            "candidate_workflow_families": len(families.get("families") or []),
            "workflow_bundles_included": len(bundles),
            "selected_execution_count": selected_runs,
            "excluded_execution_count": len(state.get("excluded_execution_ids") or []),
            "limitations": [
                "Only in-scope work plus timing-only markers for provable scope departures during the stated study window is represented.",
                "Rare, seasonal, quarterly, off-device, or otherwise unobserved exceptions may be missing.",
                "Structural traces do not contain typed values or clipboard contents and are not complete business test fixtures.",
                "Observed repetition does not establish policy, permission, or authorization.",
            ],
        },
        "workflow_evidence": handoff_bundles,
        "observed_tools": observed_tools,
        "human_statements": answered,
        "saved_unanswered_questions": unanswered,
        "suggested_targeted_questions": suggested,
        "review": {
            "employee_review_required": True,
            "share_approved_at": state.get("share_approved_at"),
            "automatic_sharing": False,
            "excluded_execution_ids": state.get("excluded_execution_ids") or [],
            "excluded_event_ids": state.get("excluded_event_ids") or [],
            "redaction_notice": (
                "People and obvious personal identifiers are contextually redacted, but organization/company names may remain. "
                "Review the redacted preview before approval when company/customer names are sensitive."
            ),
        },
        "handoff": {
            "purpose": state.get("handoff_purpose") or "automate",
            "recommended_next_step": _handoff_instruction(state),
            "structural_cases_are_replayable_business_inputs": False,
            "source_system_test_data_required_for_execution_evals": True,
        },
        "derived": True,
        "authoritative": False,
        "needs_human_and_implementation_review": True,
    }


@app.get("/v1/discovery")
def get_discovery() -> dict[str, Any]:
    _purge_discovery_evidence(force=False)
    state = public_state()
    if state.get("enabled"):
        try:
            since, until = _window(state)
            families = list_workflow_evidence_candidates(
                since=since,
                until=until,
                source_event_limit=25_000,
                min_runs=2,
                limit=20,
            )
            state["candidate_workflow_families"] = len(families.get("families") or [])
            reviewing = state.get("status") == "review" or bool(state.get("expired"))
            if reviewing:
                _families, bundles = _selected_family_runs(state)
                state["review_candidates"] = _review_candidates_with_readable_steps(families, bundles)
                state["review_steps"] = _review_steps(
                    bundles,
                    {str(x) for x in state.get("excluded_event_ids") or []},
                )
                saved_questions = {str(x.get("question") or "") for x in state.get("questions") or []}
                state["suggested_targeted_questions"] = [
                    item for item in _suggested_questions(bundles)
                    if str(item.get("question") or "") not in saved_questions
                ]
            else:
                state["suggested_targeted_questions"] = []
        except Exception:
            state["candidate_workflow_families"] = 0
            state["suggested_targeted_questions"] = []
    return state


@app.post("/v1/discovery/start")
async def start_discovery(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    if read_state().get("enabled"):
        raise HTTPException(status_code=409, detail="an existing Discovery Mode session must be reviewed/exited before starting another")
    if not _capture_running():
        raise HTTPException(
            status_code=409,
            detail="Discovery capture is not running yet. Start/keep OpenWorkGraph live observation running, then try again.",
        )

    data_dir, _auth_dir = gateway_paths(CONFIG_PATH)
    event_boundary_id = _max_local_event_id(data_dir)
    agent_message_boundary_id = _max_local_agent_message_id(data_dir)
    was_paused: bool | None = None
    paused_by_discovery = False
    try:
        # Establish the no-upload boundary before Discovery becomes active. The
        # first enabled state write then contains the cursor boundaries atomically,
        # so a concurrent sync worker cannot observe enabled=true with boundary=0.
        was_paused, paused_by_discovery = _pause_gateway_if_connected()
        return start_session(
            name=str(body.get("name") or "Workflow discovery"),
            purpose=str(body.get("purpose") or ""),
            implementation_goal=str(body.get("implementation_goal") or ""),
            implementation_description=str(body.get("implementation_description") or ""),
            handoff_purpose=str(body.get("handoff_purpose") or "automate"),
            allowed_apps=list(body.get("allowed_apps") or []),
            allowed_browser_hosts=list(body.get("allowed_browser_hosts") or []),
            duration_days=float(body.get("duration_days") or 5),
            ends_at=str(body.get("ends_at") or "") or None,
            allow_unresolved_browser_container=bool(body.get("allow_unresolved_browser_container", False)),
            retention_days_after_end=int(body.get("retention_days_after_end") or 14),
            gateway_sharing_was_paused=was_paused,
            gateway_paused_by_discovery=paused_by_discovery,
            gateway_event_boundary_id=event_boundary_id,
            gateway_agent_message_boundary_id=agent_message_boundary_id,
        )
    except (TypeError, ValueError) as exc:
        if paused_by_discovery:
            try:
                set_gateway_sharing(CONFIG_PATH, True)
            except Exception:
                pass
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        if paused_by_discovery:
            try:
                set_gateway_sharing(CONFIG_PATH, True)
            except Exception:
                pass
        deactivate_session()
        raise HTTPException(status_code=503, detail=f"could not establish Discovery sharing guard: {exc}") from exc


@app.post("/v1/discovery/finish")
def finish_discovery() -> dict[str, Any]:
    return finish_session()


@app.post("/v1/discovery/deactivate")
def deactivate_discovery() -> dict[str, Any]:
    before = read_state()
    result = deactivate_session()
    restored = _restore_gateway_if_we_paused(before)
    if restored is not None:
        result["gateway_restore"] = restored
    return result


@app.post("/v1/discovery/selection")
async def update_discovery_selection(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    values = body.get("excluded_execution_ids")
    event_values = body.get("excluded_event_ids", [])
    if not isinstance(values, list):
        raise HTTPException(status_code=422, detail="excluded_execution_ids must be a list")
    if not isinstance(event_values, list):
        raise HTTPException(status_code=422, detail="excluded_event_ids must be a list")
    state = set_excluded_execution_ids([str(x) for x in values])
    state = set_excluded_event_ids([str(x) for x in event_values])
    return state


@app.post("/v1/discovery/implementation-context")
async def update_discovery_implementation_context(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    return set_implementation_context(
        goal=str(body.get("goal") or ""),
        description=str(body.get("description") or ""),
    )


@app.post("/v1/discovery/handoff-purpose")
async def update_discovery_handoff_purpose(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    try:
        return set_handoff_purpose(str(body.get("handoff_purpose") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/discovery/questions")
async def create_discovery_question(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    try:
        return save_question(
            question=str(body.get("question") or ""),
            answer=str(body.get("answer") or ""),
            question_id=str(body.get("question_id") or "") or None,
            source=str(body.get("source") or "derived_prompt"),
            related_execution_ids=list(body.get("related_execution_ids") or []),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/discovery/questions/{question_id}")
async def update_discovery_answer(question_id: str, request: Request) -> dict[str, Any]:
    body = await _payload(request)
    try:
        return answer_question(question_id, str(body.get("answer") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 422, detail=str(exc)) from exc


@app.post("/v1/discovery/approve-share")
def approve_discovery_share() -> dict[str, Any]:
    try:
        return approve_share()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/discovery/purge")
async def purge_discovery(request: Request) -> dict[str, Any]:
    body = await _payload(request)
    if str(body.get("confirm") or "") != "DELETE DISCOVERY":
        raise HTTPException(status_code=422, detail='confirm must equal "DELETE DISCOVERY"')
    state = read_state()
    end = _parse_time(state.get("ends_at"))
    if str(state.get("status") or "") == "active" and end and datetime.now(timezone.utc) < end:
        raise HTTPException(status_code=409, detail="finish the Discovery study before deleting its evidence")
    return _purge_discovery_evidence(force=True)


@app.get("/v1/discovery/package")
def preview_discovery_package(representation: str = "redacted") -> dict[str, Any]:
    state = public_state()
    if not state.get("enabled"):
        raise HTTPException(status_code=404, detail="no Discovery Mode session is available")
    package = _package(state)
    mode = str(representation or "redacted").strip().lower()
    if mode == "redacted":
        result = redact_contextually(package)
        result["export_representation"] = "contextually_redacted"
        return result
    if mode == "stored":
        package["export_representation"] = "stored_privacy_hardened"
        return package
    raise HTTPException(status_code=422, detail="representation must be redacted or stored")


@app.get("/v1/discovery/export")
def export_discovery_package(representation: str = "redacted") -> Response:
    state = public_state()
    if not state.get("enabled"):
        raise HTTPException(status_code=404, detail="no Discovery Mode session is available")
    if not state.get("share_approved_at"):
        raise HTTPException(status_code=409, detail="employee review/approval is required before Discovery Package export")
    mode = str(representation or "redacted").strip().lower()
    if mode not in {"redacted", "stored"}:
        raise HTTPException(status_code=422, detail="representation must be redacted or stored")
    package = _package(state)
    payload = redact_contextually(package) if mode == "redacted" else package
    # Approval is the boundary for exposing real provider locators from the
    # installation-local lookup. Pre-approval previews remain opaque.
    payload = expand_resource_references(payload)
    payload["approved_local_object_locators_included"] = True
    payload["export_representation"] = "contextually_redacted" if mode == "redacted" else "stored_privacy_hardened"
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    study = payload.get("study") if isinstance(payload.get("study"), dict) else {}
    implementation = study.get("implementation_context") if isinstance(study.get("implementation_context"), dict) else {}
    inventory = payload.get("observed_tools") if isinstance(payload.get("observed_tools"), dict) else {}
    app_lines = "\n".join(
        f"- {item.get('name')}: {int(item.get('observations') or 0)} observations"
        for item in inventory.get("apps") or []
        if isinstance(item, dict)
    ) or "- No native app count available"
    site_lines = "\n".join(
        f"- {item.get('hostname')}: {int(item.get('observations') or 0)} observations"
        for item in inventory.get("sites") or []
        if isinstance(item, dict)
    ) or "- No browser host count available"
    build_brief = (
        "# Build brief\n\n"
        "## Human-provided implementation context\n\n"
        f"**Goal:** {implementation.get('goal') or '(not provided)'}\n\n"
        f"**Worker description:** {implementation.get('description') or '(not provided)'}\n\n"
        "These statements are human-provided context, not facts inferred from the observed trace.\n\n"
        "## Observed tools\n\n"
        "### Apps\n" + app_lines + "\n\n"
        "### Sites\n" + site_lines + "\n\n"
        f"> {inventory.get('caveat') or 'Observed use does not prove API access, licensing, or permission.'}\n\n"
        "## Handoff purpose\n\n"
        f"{payload.get('handoff', {}).get('purpose') or 'automate'}\n\n"
        f"{payload.get('handoff', {}).get('recommended_next_step') or ''}\n"
    ).encode("utf-8")
    guide = (
        "# Start here\n\n"
        "This package is evidence for an authorized AI or implementation team. Start with BUILD_BRIEF.md, "
        "then inspect DISCOVERY_PACKAGE.json for the observed runs and canonical event evidence.\n\n"
        f"**Purpose-specific instruction:** {payload.get('handoff', {}).get('recommended_next_step') or ''}\n\n"
        "Important boundaries:\n"
        "- Observed work is descriptive evidence, not permission or policy.\n"
        "- Human statements are explicitly attributed and separate from captured evidence.\n"
        "- Unobserved exceptions may exist.\n"
        "- Missing historical content may be available from the live source system at implementation time.\n"
        "- Obtain live source-system test data before treating structural cases as executable evaluations.\n"
    ).encode("utf-8")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DISCOVERY_PACKAGE.json", body)
        archive.writestr("BUILD_BRIEF.md", build_brief)
        archive.writestr("START_HERE.md", guide)
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="openworkgraph-discovery-package.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/discovery-mode.js")
def discovery_mode_script() -> Response:
    return Response((ROOT / "dashboard" / "discovery_mode.js").read_text(encoding="utf-8"), media_type="application/javascript")


__all__ = ["get_discovery", "preview_discovery_package"]
