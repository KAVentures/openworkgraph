from __future__ import annotations

"""Authenticated local API for temporary, employee-reviewed workflow discovery."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import zipfile
from typing import Any

from fastapi import HTTPException, Request, Response

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
    set_gateway_guard,
    start_session,
)
from connector.control import set_sharing as set_gateway_sharing, status as gateway_status
from shared.lifespan import extend_lifespan
from .ai_context import redact_contextually
from .db import connect
from .main import CONFIG_PATH
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
        bundles.append(bundle)
    return families, bundles


def _suggested_questions(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundle in bundles:
        executions = bundle.get("executions") or []
        ids = [str(x.get("execution_id") or "") for x in executions if isinstance(x, dict)]
        total = len(ids)
        alignment = bundle.get("structural_alignment") if isinstance(bundle.get("structural_alignment"), dict) else {}
        for item in (alignment.get("less_common_observed_steps") or [])[:4]:
            if not isinstance(item, dict):
                continue
            step = str(item.get("step") or "").strip()
            support = int(item.get("support_runs") or 0)
            if not step or support <= 0 or not total:
                continue
            key = f"{step}|{support}|{total}"
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "question_key": key,
                "question": (
                    f"We observed {step.replace('_', ' ')} in {support} of {total} selected runs. "
                    "What caused that variation, and does it represent a business rule, exception, or noise?"
                ),
                "reason": "observed_structural_variation",
                "related_execution_ids": ids[:12],
                "derived": True,
                "authoritative": False,
            })
            if len(rows) >= 12:
                return rows
    return rows


def _package(state: dict[str, Any]) -> dict[str, Any]:
    families, bundles = _selected_family_runs(state)
    saved = list(state.get("questions") or [])
    answered = [x for x in saved if str(x.get("answer") or "").strip()]
    unanswered = [x for x in saved if not str(x.get("answer") or "").strip()]
    suggested = _suggested_questions(bundles)
    selected_runs = sum(len((b.get("selector") or {}).get("execution_ids") or (b.get("executions") or [])) for b in bundles)
    return {
        "format": "openworkgraph.discovery-package.v1",
        "study": {
            "session_id": state.get("session_id"),
            "name": state.get("name"),
            "purpose": state.get("purpose"),
            "starts_at": state.get("starts_at"),
            "ends_at": state.get("ends_at"),
            "status": public_state().get("status"),
            "capture_scope": {
                "allowed_apps": state.get("allowed_apps") or [],
                "allowed_browser_hosts": state.get("allowed_browser_hosts") or [],
                "allow_unresolved_browser_container": bool(state.get("allow_unresolved_browser_container")),
                "positive_allowlist_enforced_before_persistence": True,
            },
        },
        "coverage": {
            "candidate_workflow_families": len(families.get("families") or []),
            "workflow_bundles_included": len(bundles),
            "selected_execution_count": selected_runs,
            "excluded_execution_count": len(state.get("excluded_execution_ids") or []),
            "limitations": [
                "Only work observed during the stated study window and positive capture scope is represented.",
                "Rare, seasonal, quarterly, off-device, or otherwise unobserved exceptions may be missing.",
                "Structural traces do not contain typed values or clipboard contents and are not complete business test fixtures.",
                "Observed repetition does not establish policy, permission, or authorization.",
            ],
        },
        "workflow_evidence": bundles,
        "human_statements": answered,
        "saved_unanswered_questions": unanswered,
        "suggested_targeted_questions": suggested,
        "review": {
            "employee_review_required": True,
            "share_approved_at": state.get("share_approved_at"),
            "automatic_sharing": False,
            "excluded_execution_ids": state.get("excluded_execution_ids") or [],
        },
        "handoff": {
            "recommended_next_step": (
                "Give this package to the authorized AI/implementation team. Reconstruct outcomes from canonical evidence, "
                "use human statements as attributed business context, resolve remaining questions, then draft an agent-neutral "
                "workflow specification. Do not infer authorization from observed actions."
            ),
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
    try:
        state = start_session(
            name=str(body.get("name") or "Workflow discovery"),
            purpose=str(body.get("purpose") or ""),
            allowed_apps=list(body.get("allowed_apps") or []),
            allowed_browser_hosts=list(body.get("allowed_browser_hosts") or []),
            duration_days=float(body.get("duration_days") or 5),
            ends_at=str(body.get("ends_at") or "") or None,
            allow_unresolved_browser_container=bool(body.get("allow_unresolved_browser_container", False)),
            retention_days_after_end=int(body.get("retention_days_after_end") or 14),
        )
        was_paused, paused_by_discovery = _pause_gateway_if_connected()
        state = set_gateway_guard(
            was_paused=was_paused,
            paused_by_discovery=paused_by_discovery,
        )
        return state
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Fail safe: do not claim review-before-sharing if a connected Gateway
        # could not be paused.
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
    if not isinstance(values, list):
        raise HTTPException(status_code=422, detail="excluded_execution_ids must be a list")
    return set_excluded_execution_ids([str(x) for x in values])


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
    payload["export_representation"] = "contextually_redacted" if mode == "redacted" else "stored_privacy_hardened"
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    guide = (
        "# OpenWorkGraph Discovery Package\n\n"
        "This package contains purpose-scoped observed workflow evidence plus separately attributed employee answers. "
        "It is not an authorization record and does not claim unobserved exceptions are absent.\n\n"
        "Use the evidence to draft an agent-neutral workflow specification, resolve unanswered business rules, and obtain "
        "live test inputs from the source systems before treating structural cases as executable evaluations.\n"
    ).encode("utf-8")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DISCOVERY_PACKAGE.json", body)
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
