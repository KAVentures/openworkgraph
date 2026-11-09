"""Authenticated, read-only choice inspection for local human handoff review."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import json
import hashlib
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
        # Never approve a partial customer-report basis; downstream review
        # helpers cap these lists at 64 and could otherwise omit blockers.
        reports = project.get("reported_specifications")
        if not isinstance(reports, list) or len(reports) > 64:
            raise HTTPException(409, "Project report history incomplete; re-inspect")
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
    # Bind the review to the complete bounded customer-report evidence, not just
    # derived blocker labels. A report may change while remaining 'matching'.
    # Do not return report contents to the browser; only their digest.
    fingerprint_basis = [
        {"project": summary, "reported_specifications": raw["reported_specifications"]}
        for summary, raw in zip(projects, journey["projects"])
    ]
    project_set_sha256 = hashlib.sha256(json.dumps(
        fingerprint_basis, sort_keys=True, separators=(',', ':'), default=str
    ).encode('utf-8')).hexdigest()
    return JSONResponse({"format": "openworkgraph.review-choices.v1",
                         "idea": journey["idea"], "process_status": process.get("status", "unknown"),
                         "process_reference": process.get("process_reference"),
                         "dataset_origins": [{"status": o.get("status"), "source_content_sha256": o.get("source_content_sha256"), "selection_sha256": o.get("selection_sha256")} for o in origins],
                         "dataset_origin_check_available": journey["dataset_origin_check_available"],
                         "projects": projects, "project_set_sha256": project_set_sha256, "projects_truncated": journey["projects_truncated"],
                         "execution_authorized": False, "source_read_authorized": False,
                         "verified_business_outcomes": False}, headers={"Cache-Control": "no-store"})


class ReviewBasis(BaseModel):
    """An exact, non-authorizing review preflight; never an execution approval."""
    project_id: str = Field(min_length=1, max_length=128)
    project_set_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    idea_revision: int = Field(ge=0)
    idea_state: str = Field(min_length=1, max_length=64)
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
    if snapshot["project_set_sha256"] != basis.project_set_sha256.lower():
        raise HTTPException(409, "Project history changed; re-inspect")
    if snapshot["projects_truncated"]:
        raise HTTPException(409, "Review history truncated; re-inspect before proceeding")
    if snapshot["idea"]["state"] != basis.idea_state:
        raise HTTPException(409, "Opportunity state changed; re-inspect")
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
                         "project_set_sha256": snapshot["project_set_sha256"],
                         "spec_sha256": project["spec_sha256"],
                         "archive_sha256": project["archive_sha256"],
                         "blockers": project["blockers"],
                         "review_required": True, "execution_authorized": False,
                         "source_read_authorized": False,
                         "verified_business_outcomes": False},
                        headers={"Cache-Control": "no-store"})


class ReviewDecision(BaseModel):
    """Human review decision, explicitly distinct from agent execution consent."""
    basis: ReviewBasis
    expected_review_revision: int = Field(ge=0)
    decision: str = Field(pattern=r"^(draft|approved|revoked)$")
    reviewer_notes: str = Field(default="", max_length=2000)


@router.get("/v1/review-choice-workbench/{idea_id}/review/{project_id}")
def read_review(request: Request, idea_id: str, project_id: str):
    """Read local durable review state; never return an execution capability."""
    # A revoked or stale project may no longer be inspectable through choices().
    # Reading its saved revision must still work so the reviewer can revoke it.
    # Authenticate directly, without requiring current opportunity provenance.
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")
    if not idea_id or len(idea_id) > 128 or not project_id or len(project_id) > 128:
        raise HTTPException(422, "Invalid review identifier")
    with pipeline._LOCK, pipeline.closing(pipeline._connect()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        row = conn.execute(
            "SELECT revision, decision, basis_sha256, reviewer_notes, updated_at "
            "FROM project_human_reviews WHERE idea_id=? AND project_id=?",
            (idea_id, project_id)).fetchone()
    return JSONResponse({"format": "openworkgraph.human-review.v1",
        "idea_id": idea_id, "project_id": project_id,
        "revision": row[0] if row else 0,
        "decision": row[1] if row else "unreviewed",
        "basis_sha256": row[2] if row else None,
        "reviewer_notes": row[3] if row else "",
        "updated_at": row[4] if row else None,
        "execution_authorized": False}, headers={"Cache-Control": "no-store"})


@router.post("/v1/review-choice-workbench/{idea_id}/review")
def save_review(request: Request, idea_id: str, payload: ReviewDecision):
    """Optimistic, evidence-bound human decision. No agent grants are issued.

    Stale evidence and concurrent reviewer edits both fail closed. An approval
    here records a reviewed choice only; the execution API must separately
    enforce explicit action-scoped consent and authorization.
    """
    current = json.loads(preflight(request, idea_id, payload.basis).body)
    if payload.decision == "approved" and current["blockers"]:
        raise HTTPException(409, "Resolve review blockers before approval")
    canonical = json.dumps(payload.basis.model_dump(mode="json"),
                           sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with pipeline._LOCK, pipeline.closing(pipeline._connect()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        row = conn.execute("SELECT revision FROM project_human_reviews "
                           "WHERE idea_id=? AND project_id=?",
                           (idea_id, payload.basis.project_id)).fetchone()
        revision = row[0] if row else 0
        if revision != payload.expected_review_revision:
            raise HTTPException(409, "Review changed; reload before editing")
        next_revision = revision + 1
        conn.execute("""INSERT INTO project_human_reviews
            (idea_id, project_id, revision, decision, basis_sha256, reviewer_notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idea_id, project_id) DO UPDATE SET
            revision=excluded.revision, decision=excluded.decision,
            basis_sha256=excluded.basis_sha256,
            reviewer_notes=excluded.reviewer_notes, updated_at=excluded.updated_at""",
            (idea_id, payload.basis.project_id, next_revision, payload.decision,
             digest, payload.reviewer_notes, pipeline._now()))
        conn.commit()
    return JSONResponse({"format": "openworkgraph.human-review.v1",
        "idea_id": idea_id, "project_id": payload.basis.project_id,
        "revision": next_revision, "decision": payload.decision,
        "basis_sha256": digest, "execution_authorized": False,
        "review_required_for_execution": True},
        headers={"Cache-Control": "no-store"})


class ReviewRevocation(BaseModel):
    """Emergency revocation does not require an unchanged evidence snapshot."""
    expected_review_revision: int = Field(ge=1)


@router.post("/v1/review-choice-workbench/{idea_id}/review/{project_id}/revoke")
def revoke_review(request: Request, idea_id: str, project_id: str, payload: ReviewRevocation):
    """Revoke a previously saved decision even if its provenance is now stale.

    The ordinary review-save endpoint deliberately requires fresh evidence.
    Revocation must remain possible after an opportunity, project or origin
    changes; otherwise a stale approval can remain recorded indefinitely.
    This operation grants no capability and uses the same local session check.
    """
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")
    if not idea_id or len(idea_id) > 128 or not project_id or len(project_id) > 128:
        raise HTTPException(422, "Invalid review identifier")
    with pipeline._LOCK, pipeline.closing(pipeline._connect()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        # A missing review cannot be revoked or used to create a new one.
        row = conn.execute(
            "SELECT revision, decision FROM project_human_reviews WHERE idea_id=? AND project_id=?",
            (idea_id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, "No saved review to revoke")
        if row[0] != payload.expected_review_revision:
            raise HTTPException(409, "Review changed; reload before revoking")
        if row[1] == "revoked":
            raise HTTPException(409, "Review already revoked; reload")
        next_revision = row[0] + 1
        conn.execute(
            "UPDATE project_human_reviews SET revision=?, decision='revoked', updated_at=? "
            "WHERE idea_id=? AND project_id=? AND revision=?",
            (next_revision, pipeline._now(), idea_id, project_id, row[0]))
        conn.commit()
    return JSONResponse({"format": "openworkgraph.human-review.v1",
        "idea_id": idea_id, "project_id": project_id,
        "revision": next_revision, "decision": "revoked",
        "execution_authorized": False}, headers={"Cache-Control": "no-store"})


@router.post("/v1/review-choice-workbench/{idea_id}/reviewed-handoff-preflight")
def reviewed_handoff_preflight(request: Request, idea_id: str, basis: ReviewBasis):
    """Require a durable, still-current human approval before opening handoff.

    This is a navigation gate, NOT an execution capability. The execution
    boundary must separately check action-scoped authorization and revocation.
    """
    current = json.loads(preflight(request, idea_id, basis).body)
    if current["blockers"]:
        raise HTTPException(409, "Unresolved review blockers")
    canonical = json.dumps(basis.model_dump(mode="json"),
                           sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with pipeline._LOCK, pipeline.closing(pipeline._connect()) as conn:
        # Missing table means no approval, never an implicit approval.
        conn.execute("""CREATE TABLE IF NOT EXISTS project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        row = conn.execute(
            "SELECT revision, decision, basis_sha256 FROM project_human_reviews "
            "WHERE idea_id=? AND project_id=?",
            (idea_id, basis.project_id)).fetchone()
    if row is None or row[1] != "approved" or row[2] != digest:
        raise HTTPException(409, "Current evidence requires explicit human approval")
    return JSONResponse({
        "format": "openworkgraph.reviewed-handoff-preflight.v1",
        "idea_id": idea_id, "project_id": basis.project_id,
        "review_revision": row[0], "project_set_sha256": basis.project_set_sha256,
        "human_review_approved": True, "execution_authorized": False,
        "source_read_authorized": False
    }, headers={"Cache-Control": "no-store"})
