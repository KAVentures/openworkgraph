"""Local automation opportunity pipeline (roadmap #326 F4).

Automation Hub-style review of candidate automations on this computer:
ideas move through explicit states (idea, assessing, approved, rejected, on
hold, implemented) with a recorded history, an ease questionnaire and an impact
estimate modeled only from the reviewer's own assumptions.

What this is not: "approved" records a person's decision in this local list. It
never authorizes an agent, changes a process, contacts anyone or claims
savings. Modeled hours come from the entered assumptions, not measurement.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
import secrets
import sqlite3
import threading
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from .db import DATA_DIR

MAX_IDEAS = 1000
STATES = ("idea", "assessing", "approved", "rejected", "on_hold", "implemented")
TRANSITIONS = {
    "idea": {"assessing", "rejected", "on_hold"},
    "assessing": {"approved", "rejected", "on_hold"},
    "on_hold": {"assessing", "rejected"},
    "approved": {"implemented", "on_hold"},
    "rejected": {"idea"},
    "implemented": set(),
}
EASE_QUESTIONS = ("rules_based", "structured_inputs", "process_stability", "digital_inputs", "low_exception_rate")
# Quadrant thresholds, stated with every result rather than hidden.
EASE_THRESHOLD = 60
IMPACT_THRESHOLD_HOURS = 20
_LOCK = threading.RLock()


class PipelineError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    kind: Literal["manual", "process_catalog", "event_log", "repeated_workflow", "communications"] = "manual"
    reference: str = Field(default="", max_length=200)


class ProcessReference(_Strict):
    workflow_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    revision: StrictInt = Field(ge=1, le=2**63-1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NewIdea(_Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    source: Source = Field(default_factory=Source)


class Assessment(_Strict):
    ease: dict[str, Annotated[StrictInt, Field(ge=1, le=5)]]
    cases_per_month: Annotated[StrictInt | StrictFloat, Field(ge=0, le=10_000_000)]
    minutes_per_case: Annotated[StrictInt | StrictFloat, Field(ge=0, le=10_000)]
    eligible_share: Annotated[StrictInt | StrictFloat, Field(ge=0, le=1)]
    compliance_review_needed: bool = False
    notes: str = Field(default="", max_length=2000)
    expected_revision: StrictInt = Field(ge=1)


class Transition(_Strict):
    to: Literal["idea", "assessing", "approved", "rejected", "on_hold", "implemented"]
    note: str = Field(default="", max_length=1000)
    expected_revision: StrictInt = Field(ge=1)


def _connect() -> sqlite3.Connection:
    path = DATA_DIR / "automation_pipeline.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS ideas (
        idea_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL,
        state TEXT NOT NULL, body TEXT NOT NULL, assessment TEXT, history TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS idea_projects (
        project_id TEXT PRIMARY KEY, idea_id TEXT NOT NULL, idea_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, process_reference TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
        archive_sha256 TEXT NOT NULL, base_archive_sha256 TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idea_projects_by_idea ON idea_projects(idea_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS project_report_links (
        project_id TEXT NOT NULL, deployment_id TEXT NOT NULL, linked_at TEXT NOT NULL,
        PRIMARY KEY(project_id, deployment_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS project_source_exports (
        export_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, idea_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, document_sha256 TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS project_source_exports_by_project ON project_source_exports(project_id)")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate(model, payload: Any):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise PipelineError(".".join(str(p) for p in first["loc"]) + ": " + first["msg"]) from None


def score(assessment: dict[str, Any]) -> dict[str, Any]:
    """Ease 0–100 from the questionnaire; impact modeled from stated assumptions."""
    answers = assessment["ease"]
    ease = round(sum(answers[q] - 1 for q in EASE_QUESTIONS) / (4 * len(EASE_QUESTIONS)) * 100)
    hours = assessment["cases_per_month"] * assessment["minutes_per_case"] * assessment["eligible_share"] / 60
    high_ease, high_impact = ease >= EASE_THRESHOLD, hours >= IMPACT_THRESHOLD_HOURS
    quadrant = {(True, True): "quick_win", (False, True): "major_project",
                (True, False): "fill_in", (False, False): "deprioritize"}[(high_ease, high_impact)]
    return {"ease_score": ease, "modeled_hours_per_month": round(hours, 2), "quadrant": quadrant,
            "thresholds": {"ease": EASE_THRESHOLD, "modeled_hours_per_month": IMPACT_THRESHOLD_HOURS},
            "hours_are_modeled_from_entered_assumptions": True, "savings_claimed": False}


def _public(row) -> dict[str, Any]:
    idea_id, created, updated, revision, state, body, assessment, history = row
    out = {"idea_id": idea_id, "created_at": created, "updated_at": updated, "revision": revision, "state": state,
           **json.loads(body), "assessment": None, "score": None, "history": json.loads(history),
           "allowed_transitions": sorted(TRANSITIONS[state]), "execution_authorized": False}
    if assessment:
        data = json.loads(assessment)
        out["assessment"], out["score"] = data, score(data)
    return out


def _get(conn, idea_id: str):
    row = conn.execute("SELECT * FROM ideas WHERE idea_id = ?", [str(idea_id)]).fetchone()
    if not row:
        raise PipelineError("unknown idea")
    return row


def _check_revision(row, expected: int) -> None:
    if row[3] != expected:
        raise PipelineError(f"this idea changed (now revision {row[3]}); reload it before editing")


def create(payload: Any) -> dict[str, Any]:
    return _create(_validate(NewIdea, payload))


def _catalog():
    from .local_process_catalog import catalog
    return catalog


def create_from_process(payload: Any) -> dict[str, Any]:
    """Resolve a selected local revision; clients cannot assert verified lineage."""
    ref = _validate(ProcessReference, payload)
    try:
        record = _catalog().get(ref.workflow_id, ref.revision)
    except LookupError:
        raise PipelineError("selected process revision is unavailable") from None
    except Exception:
        raise PipelineError("selected process could not be checked; try again") from None
    if record["content_sha256"] != ref.content_sha256:
        raise PipelineError("selected process changed; reload it before adding an idea")
    idea = NewIdea(title=record["title"].strip()[:200] or "Saved process opportunity", description=record["process_model"]["goal"],
                   source=Source(kind="process_catalog", reference=f"{ref.workflow_id}@{ref.revision}"))
    return _create(idea, ref.model_dump())


def _create(idea: NewIdea, process_reference: dict[str, Any] | None = None) -> dict[str, Any]:
    if not idea.title.strip():
        raise PipelineError("title must not be blank")
    with _LOCK, closing(_connect()) as conn, conn:
        if conn.execute("SELECT count(*) FROM ideas").fetchone()[0] >= MAX_IDEAS:
            raise PipelineError(f"the pipeline holds at most {MAX_IDEAS} ideas; delete some first")
        idea_id, now = "idea_" + secrets.token_hex(8), _now()
        history = [{"state": "idea", "at": now, "note": "created"}]
        body = idea.model_dump()
        if process_reference is not None:
            body["process_reference"] = process_reference
        conn.execute("INSERT INTO ideas VALUES (?, ?, ?, 1, 'idea', ?, NULL, ?)",
                     [idea_id, now, now, json.dumps(body), json.dumps(history)])
        return _public(_get(conn, idea_id))


def process_status(idea_id: str) -> dict[str, Any]:
    return _process_status(get(idea_id).get("process_reference"))


def _process_status(reference: dict[str, Any] | None) -> dict[str, Any]:
    result = {"format": "openworkgraph.opportunity-process-status.v1",
              "status": "unbound_reference", "process_reference": reference,
              "latest_revision": None, "reference_is_current": False,
              "execution_authorized": False, "business_outcomes_verified": False,
              "independent_design_review_verified": False}
    if reference is None:
        return result
    try:
        ref = ProcessReference.model_validate(reference)
        catalog = _catalog()
        pinned = catalog.get(ref.workflow_id, ref.revision)
        if pinned["content_sha256"] != ref.content_sha256:
            return result | {"status": "reference_mismatch"}
        latest = catalog.get(ref.workflow_id)
        current = latest["revision"] == ref.revision and latest["content_sha256"] == ref.content_sha256
        return result | {"status": "current" if current else "changed",
                         "latest_revision": latest["revision"], "reference_is_current": current}
    except LookupError:
        return result | {"status": "unavailable"}
    except Exception:
        # The check is a refusal boundary, not a reason to expose source data or
        # treat an unavailable database as a fresh design. Reads never mutate it.
        return result | {"status": "check_failed"}


def assess(idea_id: str, payload: Any) -> dict[str, Any]:
    assessment = _validate(Assessment, payload)
    if set(assessment.ease) != set(EASE_QUESTIONS):
        raise PipelineError("answer every ease question: " + ", ".join(EASE_QUESTIONS))
    with _LOCK, closing(_connect()) as conn, conn:
        row = _get(conn, idea_id)
        _check_revision(row, assessment.expected_revision)
        if row[4] in {"implemented", "rejected"}:
            raise PipelineError(f"a {row[4]} idea cannot be reassessed")
        data = assessment.model_dump(exclude={"expected_revision"})
        now = _now()
        # An approval reviewed the previous assessment. Resubmitting it must not
        # silently retain that decision, even when the caller still has a fresh
        # optimistic revision. Keep the old basis too, including legacy rows
        # whose previous assessment was never recorded in history.
        invalidated = row[4] == "approved"
        state = "assessing" if invalidated else row[4]
        history = json.loads(row[7]) + [{
            "action": "assessment_saved", "state": state, "at": now,
            "revision": row[3] + 1, "assessment": data,
            "previous_assessment": json.loads(row[6]) if row[6] else None,
            "approval_invalidated": invalidated,
            "note": "Approval cleared; review the new assessment." if invalidated else "Assessment saved.",
        }]
        conn.execute("UPDATE ideas SET assessment = ?, state = ?, history = ?, "
                     "revision = revision + 1, updated_at = ? WHERE idea_id = ?",
                     [json.dumps(data), state, json.dumps(history[-100:]), now, row[0]])
        return _public(_get(conn, idea_id))


def transition(idea_id: str, payload: Any) -> dict[str, Any]:
    move = _validate(Transition, payload)
    with _LOCK, closing(_connect()) as conn, conn:
        row = _get(conn, idea_id)
        _check_revision(row, move.expected_revision)
        if move.to not in TRANSITIONS[row[4]]:
            raise PipelineError(f"cannot move from {row[4]} to {move.to}")
        if move.to == "approved" and not row[6]:
            raise PipelineError("assess the idea before approving it")
        reference = json.loads(row[5]).get("process_reference")
        if reference is not None and move.to in {"approved", "implemented"}:
            if not _process_status(reference)["reference_is_current"]:
                raise PipelineError("linked process changed or is unavailable; open its current revision and create a new idea")
        now = _now()
        entry = {"state": move.to, "at": now, "note": move.note.strip()}
        if move.to == "approved":
            # Pin the local decision's exact assumptions in its history entry;
            # this snapshot grants no tool or business-execution authority.
            entry.update(revision=row[3] + 1, assessment=json.loads(row[6]))
            if reference is not None:
                entry["process_reference"] = reference
        history = json.loads(row[7]) + [entry]
        conn.execute("UPDATE ideas SET state = ?, history = ?, revision = revision + 1, updated_at = ? WHERE idea_id = ?",
                     [move.to, json.dumps(history[-100:]), now, row[0]])
        return _public(_get(conn, idea_id))


def delete(idea_id: str) -> dict[str, Any]:
    with _LOCK, closing(_connect()) as conn, conn:
        _get(conn, idea_id)
        conn.execute("DELETE FROM project_source_exports WHERE project_id IN "
                     "(SELECT project_id FROM idea_projects WHERE idea_id = ?)", [str(idea_id)])
        conn.execute("DELETE FROM project_report_links WHERE project_id IN "
                     "(SELECT project_id FROM idea_projects WHERE idea_id = ?)", [str(idea_id)])
        conn.execute("DELETE FROM idea_projects WHERE idea_id = ?", [str(idea_id)])
        conn.execute("DELETE FROM ideas WHERE idea_id = ?", [str(idea_id)])
    return {"deleted": True, "idea_id": idea_id}


def get(idea_id: str) -> dict[str, Any]:
    with _LOCK, closing(_connect()) as conn:
        return _public(_get(conn, idea_id))


def pipeline(state: str | None = None) -> dict[str, Any]:
    if state is not None and state not in STATES:
        raise PipelineError("unknown state")
    with _LOCK, closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM ideas ORDER BY updated_at DESC, idea_id").fetchall()
    ideas = [_public(row) for row in rows]
    counts = {name: sum(1 for i in ideas if i["state"] == name) for name in STATES}
    if state:
        ideas = [i for i in ideas if i["state"] == state]
    order = {"quick_win": 0, "major_project": 1, "fill_in": 2, "deprioritize": 3}
    ideas.sort(key=lambda i: (i["score"] is None, order.get((i["score"] or {}).get("quadrant"), 4),
                              -((i["score"] or {}).get("modeled_hours_per_month") or 0)))
    return {"ideas": ideas, "counts": counts, "max_ideas": MAX_IDEAS,
            "ease_questions": list(EASE_QUESTIONS), "transitions": {k: sorted(v) for k, v in TRANSITIONS.items()},
            "approval_authorizes_execution": False, "savings_claimed": False,
            "interpretation": ("States record the reviewers' own decisions in this local list. Modeled hours come from "
                               "entered assumptions and are not measured savings.")}


def export_csv(state: str | None = None) -> str:
    import csv
    import io
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["idea_id", "title", "state", "source_kind", "source_reference", "ease_score",
                     "modeled_hours_per_month", "quadrant", "compliance_review_needed", "updated_at"])
    for idea in pipeline(state)["ideas"]:
        s, a = idea["score"] or {}, idea["assessment"] or {}
        writer.writerow([idea["idea_id"], _cell(idea["title"]), idea["state"], idea["source"]["kind"],
                         _cell(idea["source"]["reference"]), s.get("ease_score", ""), s.get("modeled_hours_per_month", ""),
                         s.get("quadrant", ""), a.get("compliance_review_needed", ""), idea["updated_at"]])
    return out.getvalue()


def _cell(value: str) -> str:
    # Spreadsheet formula injection guard for user-entered text.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


__all__ = ["assess", "create", "delete", "export_csv", "get", "pipeline", "score", "transition"]
