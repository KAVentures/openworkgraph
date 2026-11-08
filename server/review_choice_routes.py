"""Authenticated, read-only choice inspection for local human handoff review."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import json
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
    # A partial or ambiguous provenance set cannot establish a review basis.
    # The UI must not silently accept the first 64 origins or a duplicate export.
    origins = journey["dataset_origins"]
    if len(origins) > 64:
        raise HTTPException(409, "Dataset provenance exceeds review limit; narrow selection")
    if len(journey["projects"]) > 64:
        raise HTTPException(409, "Project history exceeds review limit; narrow selection")
    project_ids = [p["project_id"] for p in journey["projects"]]
    if len(project_ids) != len(set(project_ids)):
        raise HTTPException(409, "Duplicate project identities; re-export before review")
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
                         "dataset_origins": [{"status": o.get("status"), "source_content_sha256": o.get("source_content_sha256"), "selection_sha256": o.get("selection_sha256")} for o in origins],
                         "dataset_origin_check_available": journey["dataset_origin_check_available"],
                         "projects": projects, "projects_truncated": journey["projects_truncated"],
                         "execution_authorized": False, "source_read_authorized": False,
                         "verified_business_outcomes": False}, headers={"Cache-Control": "no-store"})


class ReviewBasis(BaseModel):
    """An exact, non-authorizing review preflight; never an execution approval."""
    project_id: str = Field(min_length=1, max_length=128)
    idea_revision: int = Field(ge=0)
    spec_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    archive_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    process_status: str
    process_reference: dict | None = None
    dataset_origins: list[dict]
    dataset_origin_check_available: bool
    blockers: list[str]


@router.post("/v1/review-choice-workbench/{idea_id}/preflight")
def preflight(request: Request, idea_id: str, basis: ReviewBasis):
    """Recheck a human-selected export against live local evidence, fail closed.

    This endpoint deliberately does not persist an approval, issue an agent grant,
    or claim any verified outcome. The caller must explicitly review blockers.
    """
    response = choices(request, idea_id)  # same dashboard-session enforcement
    snapshot = json.loads(response.body)
    if (snapshot["process_status"] != basis.process_status or
            snapshot["process_reference"] != basis.process_reference or
            snapshot["dataset_origins"] != basis.dataset_origins or
            snapshot["dataset_origin_check_available"] != basis.dataset_origin_check_available):
        raise HTTPException(409, "Review provenance changed; re-inspect")
    if snapshot["projects_truncated"]:
        raise HTTPException(409, "Review history truncated; re-inspect before proceeding")
    if snapshot["idea"]["revision"] != basis.idea_revision:
        raise HTTPException(409, "Opportunity revision changed; re-inspect")
    matches = [p for p in snapshot["projects"] if p["project_id"] == basis.project_id]
    if len(matches) != 1:
        raise HTTPException(409, "Project no longer uniquely available; re-inspect")
    project = matches[0]
    if project["blockers"] != basis.blockers:
        raise HTTPException(409, "Review blockers changed; re-inspect")
    if (project["spec_sha256"].lower() != basis.spec_sha256.lower() or
            project["archive_sha256"].lower() != basis.archive_sha256.lower() or
            project["idea_revision"] != basis.idea_revision):
        raise HTTPException(409, "Project evidence changed; re-inspect")
    return JSONResponse({"format": "openworkgraph.review-preflight.v1",
                         "idea_id": idea_id, "idea_revision": basis.idea_revision,
                         "project_id": basis.project_id,
                         "spec_sha256": project["spec_sha256"],
                         "archive_sha256": project["archive_sha256"],
                         "blockers": project["blockers"],
                         "review_required": True, "execution_authorized": False,
                         "source_read_authorized": False,
                         "verified_business_outcomes": False},
                        headers={"Cache-Control": "no-store"})
