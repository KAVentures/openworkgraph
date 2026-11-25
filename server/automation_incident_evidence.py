"""Exact retained run evidence for automation incidents (R18).

The existing incident reconciliation defines membership by first failing or
handed-off activity. This read-only view recomputes that membership and pins
the complete retained report population, not just the sample IDs. No channel
secrets, provider calls, retry actions, or new persistence.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import automation_incidents as incidents
from . import automation_monitoring as m
from . import automation_run_evidence as evidence
from .event_log_quality import _set_digest

MAX_RUNS = 50_000
MAX_STEPS = 2_000_000
FORMAT = "openworkgraph.incident-run-evidence.v1"
RUN_KINDS = {"run_failures": "failed", "run_handoffs": "handed_to_human"}


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_last_seen: str = Field(min_length=1, max_length=64)
    expected_review_revision: int = Field(ge=0)
    expected_membership_sha256: str = Field(pattern=evidence.HASH_PATTERN)
    expected_population_sha256: str | None = Field(default=None, pattern=evidence.HASH_PATTERN)
    offset: int = Field(default=0, ge=0, le=MAX_RUNS)
    limit: int = Field(default=20, ge=1, le=100)


def _select(conn, deployment, state, fingerprint):
    """Mirror incident membership exactly; labels and IDs are never SQL syntax."""
    conn.execute("""
        CREATE TEMP TABLE incident_members AS
        WITH grouped AS (
            SELECT r.run_id, arg_min(s.activity, s.seq) FILTER (WHERE s.status=?)
                AS first_problem_activity
            FROM runs r LEFT JOIN steps s
                ON r.deployment_id=s.deployment_id AND r.run_id=s.run_id
            WHERE r.deployment_id=? AND r.status=?
            GROUP BY r.run_id
        )
        SELECT r.* FROM runs r JOIN grouped g ON r.run_id=g.run_id
        WHERE r.deployment_id=? AND
            CASE WHEN g.first_problem_activity IS NULL THEN 'no_step_reported'
                 ELSE 'step:' || g.first_problem_activity END = ?
    """, [state, deployment, state, deployment, fingerprint])


def _membership(conn):
    """Identical sorted run ID digest to the incident reconciliation."""
    h = sha256()
    cursor = conn.execute("SELECT run_id FROM incident_members ORDER BY run_id")
    while rows := cursor.fetchmany(1024):
        for (run_id,) in rows:
            h.update(run_id.encode("utf-8") + b"\x00")
    return h.hexdigest()


def review(incident_id, spec):
    settings = evidence._parse(Selection, spec)
    if settings.offset and not settings.expected_population_sha256:
        raise m.MonitoringError("inspect page one before requesting further incident evidence")
    # Lock order is shared with reconciliation and deployment deletion.
    with m._LOCK, incidents._LOCK:
        item = incidents._load()["incidents"].get(incident_id)
        if item is None:
            raise m.MonitoringError("selected incident is not retained")
        if item["kind"] not in RUN_KINDS:
            raise m.MonitoringError("channel occurrences are not retained run membership")
        if item.get("count_basis") != "distinct_retained_run_ids":
            raise m.MonitoringError("incident has no reconciled run membership; refresh after reconciliation")
        if (item["last_seen"] != settings.expected_last_seen
                or item.get("review_revision", 0) != settings.expected_review_revision
                or item.get("run_membership_sha256") != settings.expected_membership_sha256):
            raise m.MonitoringError("incident changed; refresh before inspecting evidence")
        deployment = item["deployment_id"]
        state = RUN_KINDS[item["kind"]]
        conn = m._connect()
        try:
            info = m._check(conn, deployment)
            _select(conn, deployment, state, item["fingerprint"])
            total = conn.execute("SELECT count(*) FROM incident_members").fetchone()[0]
            if total > MAX_RUNS:
                raise m.MonitoringError("incident exceeds the supported complete run evidence limit")
            member = _membership(conn)
            if total != item.get("current_run_count") or member != item["run_membership_sha256"]:
                raise m.MonitoringError("retained run membership differs from incident; reconcile before reviewing")
            step_scope = """FROM steps s JOIN incident_members r
                ON s.deployment_id=r.deployment_id AND s.run_id=r.run_id"""
            steps = conn.execute("SELECT count(*) " + step_scope).fetchone()[0]
            if steps > MAX_STEPS:
                raise m.MonitoringError("incident exceeds the supported complete step evidence limit")
            run_hash = _set_digest(conn, """SELECT sha256(to_json(struct_pack(
                id:=run_id, started:=started_at, finished:=finished_at, state:=status,
                verified:=outcome_reported_verified, source:=outcome_source,
                spec:=reported_spec_sha256))) AS h FROM incident_members""", [])
            step_hash = _set_digest(conn, """SELECT sha256(to_json(struct_pack(
                id:=s.run_id, seq:=s.seq, step:=s.step_id, activity:=s.activity,
                state:=s.status, at_time:=s.observed_at))) AS h """ + step_scope, [])
            population = evidence._digest({
                "incident": incident_id, "deployment": deployment,
                "review_revision": item.get("review_revision", 0), "last_seen": item["last_seen"],
                "membership": member, "runs": run_hash, "steps": step_hash,
                "attached_design": info["design_sha256"],
            })
            if (settings.expected_population_sha256 is not None
                    and settings.expected_population_sha256 != population):
                raise m.MonitoringError("incident evidence changed; restart from page one")
            ids = conn.execute("""SELECT run_id FROM incident_members
                ORDER BY started_at DESC, run_id LIMIT ? OFFSET ?""",
                [settings.limit, settings.offset]).fetchall()
            rows = []
            for (rid,) in ids:
                detail = evidence._detail(conn, deployment, rid, info)
                run = detail["run"]
                problem = next((s for s in run["steps"] if s["status"] == state), None)
                rows.append({
                    "reference": detail["reference"], "run_id": rid,
                    "started_at": run["started_at"], "finished_at": run["finished_at"],
                    "status": run["status"], "step_count": len(run["steps"]),
                    "first_reported_issue": problem, "attribution": detail["attribution"],
                    "reported_spec_sha256": run["reported_spec_sha256"],
                })
            return {
                "format": FORMAT, "incident_id": incident_id, "deployment_id": deployment,
                "kind": item["kind"], "fingerprint": item["fingerprint"],
                "membership_sha256": member, "population_sha256": population,
                "matching_runs": total, "matching_steps": steps, "offset": settings.offset,
                "next_offset": settings.offset + len(rows) if settings.offset + len(rows) < total else None,
                "rows": rows, **evidence.CLAIMS, **incidents.CLAIMS,
                "interpretation": "Current retained reports, not independently verified outcomes or a historical incident archive.",
            }
        finally:
            conn.close()


class PinnedTriage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_last_seen: str = Field(min_length=1, max_length=64)
    expected_review_revision: int = Field(ge=0)
    expected_membership_sha256: str = Field(pattern=evidence.HASH_PATTERN)
    expected_population_sha256: str = Field(pattern=evidence.HASH_PATTERN)
    status: Literal["open", "acknowledged", "resolved"]
    note: str = Field(default="", max_length=500)


def triage_reviewed(incident_id, payload):
    """Record a human decision only while the inspected evidence still matches.

    Reuse the existing triage lifecycle and optimistic review checks. A digest
    establishes content equality, not that a person read every report.
    """
    request = evidence._parse(PinnedTriage, payload)
    with m._LOCK, incidents._LOCK:
        current = review(incident_id, request.model_dump(exclude={"status", "note"}) | {"limit": 1})
        try:
            decision = incidents.triage(incident_id, {
                "status": request.status, "note": request.note,
                "expected_last_seen": request.expected_last_seen,
                "expected_review_revision": request.expected_review_revision,
            })
        except incidents.IncidentError as exc:
            raise m.MonitoringError(str(exc)) from None
        return {"incident": decision, "reviewed_population_sha256": current["population_sha256"],
                "reviewed_membership_sha256": current["membership_sha256"],
                "reviewed_run_count": current["matching_runs"],
                "reviewed_step_count": current["matching_steps"],
                "human_reading_independently_verified": False, **incidents.CLAIMS, **evidence.CLAIMS}
