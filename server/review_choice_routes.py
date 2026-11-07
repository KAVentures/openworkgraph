"""Authenticated, read-only choice inspection for local human handoff review."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from .local_auth import dashboard_session_valid
from . import opportunity_journey, automation_pipeline as pipeline

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "review_choice_workbench.js"

@router.get("/review-choice-workbench.js")
def script():
    return FileResponse(_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})

@router.get("/v1/review-choice-workbench/{idea_id}")
def choices(request: Request, idea_id: str):
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")
    if len(idea_id) > 128:
        raise HTTPException(422, "Invalid idea identifier")
    try:
        journey = opportunity_journey.journey(idea_id)
    except pipeline.PipelineError as exc:
        raise HTTPException(404 if str(exc) == "unknown idea" else 422, str(exc)) from None
    process = journey["process_status"]
    projects = []
    for project in journey["projects"]:
        blockers = []
        if process.get("status") != "current":
            blockers.append("saved_process_not_current")
        if journey["idea"]["state"] not in ("approved", "implemented"):
            blockers.append("idea_not_approved")
        if not project["idea_revision_is_current"]:
            blockers.append("historical_idea_revision")
        if journey["projects_truncated"]:
            blockers.append("project_history_truncated")
        if not journey["dataset_origins"]:
            blockers.append("dataset_origin_not_linked")
        elif not journey["dataset_origin_check_available"] or any(o.get("status") != "current" for o in journey["dataset_origins"]):
            blockers.append("dataset_origin_not_current_or_unavailable")
        if not project["reported_specifications"]:
            blockers.append("no_customer_report")
        elif any(r.get("status") != "matching_reported_specification" for r in project["reported_specifications"]):
            blockers.append("customer_report_not_matching")
        projects.append({"project_id": project["project_id"], "idea_revision": project["idea_revision"],
                         "spec_sha256": project["spec_sha256"], "archive_sha256": project["archive_sha256"],
                         "blockers": blockers, "review_required": True, "execution_authorized": False})
    return JSONResponse({"format": "openworkgraph.review-choices.v1",
                         "idea": journey["idea"], "process_status": process.get("status", "unknown"),
                         "process_reference": process.get("process_reference"),
                         "dataset_origins": [{"status": o.get("status"), "source_content_sha256": o.get("source_content_sha256"), "selection_sha256": o.get("selection_sha256")} for o in journey["dataset_origins"][:64]],
                         "dataset_origin_check_available": journey["dataset_origin_check_available"],
                         "projects": projects, "projects_truncated": journey["projects_truncated"],
                         "execution_authorized": False, "source_read_authorized": False,
                         "verified_business_outcomes": False}, headers={"Cache-Control": "no-store"})
