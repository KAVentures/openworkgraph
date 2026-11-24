"""Review exact retained run reports, not live execution state or provider facts.

Uses the existing monitoring database and lock. No second run store, runtime
calls, execution grants, or changes to imported reports.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import automation_monitoring as m
from .event_log_quality import _set_digest

MAX_MATCHING_RUNS = 50_000
MAX_MATCHING_STEPS = 2_000_000
HASH_PATTERN = r"^[a-f0-9]{64}$"
ID_PATTERN = r"^[\x20-\x7e]{1,128}$"
DEP_PATTERN = r"^dep_[a-f0-9]{16}$"
CLAIMS = {**m.REPORTED_ONLY, "business_outcomes_verified": False,
          "source_state_verified": False, "runtime_contacted": False}


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["succeeded", "failed", "handed_to_human", "cancelled", "running"] | None = None
    run_id: str | None = Field(default=None, pattern=ID_PATTERN)
    step_id: str | None = Field(default=None, pattern=ID_PATTERN)
    step_status: Literal["completed", "failed", "skipped", "handed_to_human"] | None = None
    started_from: str | None = Field(default=None, max_length=50)
    started_before: str | None = Field(default=None, max_length=50)
    attribution: Literal["any", "attached_design", "declared_other", "unknown"] = "any"
    expected_population_sha256: str | None = Field(default=None, pattern=HASH_PATTERN)
    offset: int = Field(default=0, ge=0, le=MAX_MATCHING_RUNS)
    limit: int = Field(default=30, ge=1, le=100)


class RunPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    deployment_id: str = Field(pattern=DEP_PATTERN)
    run_id: str = Field(pattern=ID_PATTERN)
    run_sha256: str = Field(pattern=HASH_PATTERN)


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    left: RunPin
    right: RunPin


def _parse(cls, value):
    try:
        return cls.model_validate(value)
    except ValidationError:
        raise m.MonitoringError("invalid bounded run evidence request") from None


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _run(row, steps):
    run_id, started, finished, status, verified, source, reported_spec = row
    return {"run_id": run_id, "started_at": m._iso(started), "finished_at": m._iso(finished),
            "status": status, "outcome_reported_verified": verified, "outcome_source": source,
            "reported_spec_sha256": reported_spec,
            "steps": [{"seq": seq, "step_id": step, "activity": activity, "status": state,
                       "at": m._iso(stamp)} for seq, step, activity, state, stamp in steps]}


_RUN_COLUMNS = """run_id,started_at,finished_at,status,outcome_reported_verified,
                  outcome_source,reported_spec_sha256"""


def _detail(conn, deployment_id, run_id):
    info = m._check(conn, deployment_id)
    row = conn.execute("SELECT "+_RUN_COLUMNS+" FROM runs WHERE deployment_id=? AND run_id=?",
                       [deployment_id, run_id]).fetchone()
    if row is None:
        raise m.MonitoringError("selected run is not retained in this deployment")
    steps = conn.execute("""SELECT seq,step_id,activity,status,observed_at FROM steps
                            WHERE deployment_id=? AND run_id=? ORDER BY seq LIMIT 101""",
                         [deployment_id, run_id]).fetchall()
    if len(steps) > m.MAX_STEPS_PER_RUN or len({s[0] for s in steps}) != len(steps):
        raise m.MonitoringError("retained run step index exceeds supported bounds or is ambiguous")
    run = _run(row, steps)
    ref = {"deployment_id": deployment_id, "run_id": run_id,
           "run_sha256": _digest({"deployment_id": deployment_id, "run": run})}
    attached = info["design_sha256"]
    declared = run["reported_spec_sha256"]
    attribution = ("unknown" if declared is None else "no_attached_design" if attached is None
                   else "attached_design" if attached == declared else "declared_other")
    return {"reference": ref, "run": run,
            "attached_design_sha256": attached, "attribution": attribution, **CLAIMS}


def detail(deployment_id, run_id, expected_sha256=None):
    m._identifier(run_id, "run_id")
    with m._LOCK:
        conn = m._connect()
        try:
            result = _detail(conn, deployment_id, run_id)
            if expected_sha256 is not None and result["reference"]["run_sha256"] != expected_sha256:
                raise m.MonitoringError("retained run report changed; inspect and review it again")
            return result
        finally:
            conn.close()


def search(deployment_id, spec):
    settings = _parse(Search, spec)
    if settings.offset and not settings.expected_population_sha256:
        raise m.MonitoringError("inspect page one before requesting further evidence")
    start = m._time(settings.started_from, "started_from") if settings.started_from is not None else None
    end = m._time(settings.started_before, "started_before") if settings.started_before is not None else None
    if start is not None and end is not None and start >= end:
        raise m.MonitoringError("start must precede the exclusive end")
    with m._LOCK:
        conn = m._connect()
        try:
            info = m._check(conn, deployment_id)
            where, args = ["r.deployment_id=?"], [deployment_id]
            for field in ("status", "run_id"):
                value = getattr(settings, field)
                if value is not None:
                    where.append("r."+field+"=?")
                    args.append(value)
            if start is not None:
                where.append("r.started_at>=?")
                args.append(start)
            if end is not None:
                where.append("r.started_at<?")
                args.append(end)
            if settings.step_id is not None or settings.step_status is not None:
                parts = ["s.deployment_id=r.deployment_id", "s.run_id=r.run_id"]
                for field, column in (("step_id", "step_id"), ("step_status", "status")):
                    value = getattr(settings, field)
                    if value is not None:
                        parts.append("s."+column+"=?")
                        args.append(value)
                where.append("EXISTS (SELECT 1 FROM steps s WHERE "+" AND ".join(parts)+")")
            if settings.attribution == "unknown":
                where.append("r.reported_spec_sha256 IS NULL")
            elif settings.attribution in ("attached_design", "declared_other"):
                if info["design_sha256"] is None:
                    raise m.MonitoringError("attach a design before filtering by its digest")
                where.append("r.reported_spec_sha256"+("=" if settings.attribution == "attached_design" else "<>")+"?")
                args.append(info["design_sha256"])
            predicate = " AND ".join(where)
            total = conn.execute("SELECT count(*) FROM runs r WHERE "+predicate, args).fetchone()[0]
            if total > MAX_MATCHING_RUNS:
                raise m.MonitoringError("too many matching runs; narrow the time or status filters")
            conn.execute("CREATE TEMP TABLE selected_runs AS SELECT r.* FROM runs r WHERE "+predicate,args)
            step_scope = """FROM steps s JOIN selected_runs r
                ON s.deployment_id=r.deployment_id AND s.run_id=r.run_id"""
            step_count = conn.execute("SELECT count(*) "+step_scope).fetchone()[0]
            if step_count > MAX_MATCHING_STEPS:
                raise m.MonitoringError("too many matching steps; narrow the run selection")
            # Row hashes include NULL distinctly, exact timestamps and the complete
            # ordered step index. No aggregated concatenation of user-controlled strings.
            run_hash = _set_digest(conn, """SELECT sha256(to_json(struct_pack(
                id:=run_id, started:=started_at, finished:=finished_at, state:=status,
                verified:=outcome_reported_verified, source:=outcome_source,
                spec:=reported_spec_sha256))) AS h FROM selected_runs""", [])
            step_hash = _set_digest(conn, """SELECT sha256(to_json(struct_pack(
                id:=s.run_id, seq:=s.seq, step:=s.step_id, activity:=s.activity,
                state:=s.status, at_time:=s.observed_at))) AS h """+step_scope, [])
            basis = settings.model_dump(exclude={"offset","limit","expected_population_sha256"})
            population = _digest({"deployment_id":deployment_id, "filter":basis,
                                  "attached_design_sha256":info["design_sha256"],
                                  "runs_sha256":run_hash, "steps_sha256":step_hash})
            if settings.expected_population_sha256 and settings.expected_population_sha256 != population:
                raise m.MonitoringError("matching run evidence changed; restart from page one")
            counts = dict.fromkeys(m.RUN_STATUSES, 0)
            counts.update(dict(conn.execute("SELECT status,count(*) FROM selected_runs GROUP BY status").fetchall()))
            page = conn.execute("""SELECT run_id FROM selected_runs
                     ORDER BY started_at DESC,run_id LIMIT ? OFFSET ?""",
                     [settings.limit,settings.offset]).fetchall()
            rows = []
            for (rid,) in page:
                item = _detail(conn,deployment_id,rid)
                run = item["run"]
                first_issue = next(({"step_id":s["step_id"], "status":s["status"], "seq":s["seq"]}
                                    for s in run["steps"] if s["status"] in ("failed","handed_to_human")),None)
                rows.append({"reference":item["reference"], "attribution":item["attribution"],
                             **{key:run[key] for key in ("run_id","started_at","finished_at","status",
                                                        "reported_spec_sha256","outcome_reported_verified")},
                             "step_count":len(run["steps"]),"first_reported_issue":first_issue})
            return {"format":"openworkgraph.retained-run-evidence.v1",
                    "deployment_id":deployment_id,"deployment_name":info["name"],
                    "attached_design_sha256":info["design_sha256"],
                    "population_sha256":population,"filters":basis,"matching_runs":total,
                    "matching_steps":step_count,"status_counts":counts,"rows":rows,
                    "offset":settings.offset,
                    "next_offset":settings.offset+len(rows) if settings.offset+len(rows)<total else None,
                    **CLAIMS}
        finally:
            conn.close()


def _alignment(left,right):
    """Deterministic minimum insert/delete alignment on stable reported step IDs.

    Repeated IDs preserve their occurrences. An equal-cost alternative may
    exist. Matching IDs do not establish that two activities share business
    identity; timestamps, labels and statuses remain visible on both sides.
    """
    n,k=len(left),len(right)
    cost=[[0]*(k+1) for _ in range(n+1)]
    for i in range(n,-1,-1):
        cost[i][k]=n-i
    for j in range(k,-1,-1):
        cost[n][j]=k-j
    for i in range(n-1,-1,-1):
        for j in range(k-1,-1,-1):
            cost[i][j]=min(1+cost[i+1][j],1+cost[i][j+1],
                           cost[i+1][j+1] if left[i]["step_id"]==right[j]["step_id"] else n+k+1)
    i=j=0
    rows=[]
    while i<n or j<k:
        if i<n and j<k and left[i]["step_id"]==right[j]["step_id"] and cost[i][j]==cost[i+1][j+1]:
            a,b=left[i],right[j]
            rows.append({"kind":"matched_step_id","left":a,"right":b,
                         "status_changed":a["status"]!=b["status"],
                         "activity_label_changed":a["activity"]!=b["activity"]})
            i+=1;j+=1
        elif i<n and cost[i][j]==1+cost[i+1][j]:
            rows.append({"kind":"left_only","left":left[i],"right":None})
            i+=1
        else:
            rows.append({"kind":"right_only","left":None,"right":right[j]})
            j+=1
    return cost[0][0],rows


def compare(spec):
    settings=_parse(Comparison,spec)
    if (settings.left.deployment_id,settings.left.run_id)==(settings.right.deployment_id,settings.right.run_id):
        raise m.MonitoringError("select two distinct retained runs")
    with m._LOCK:
        conn=m._connect()
        try:
            sides=[]
            for pin in (settings.left,settings.right):
                result=_detail(conn,pin.deployment_id,pin.run_id)
                if result["reference"]["run_sha256"]!=pin.run_sha256:
                    raise m.MonitoringError("a compared run report changed; inspect both runs again")
                sides.append(result)
        finally:
            conn.close()
    left,right=sides
    edits,rows=_alignment(left["run"]["steps"],right["run"]["steps"])
    counts=Counter(row["kind"] for row in rows)
    a,b=left["run"]["reported_spec_sha256"],right["run"]["reported_spec_sha256"]
    return {"format":"openworkgraph.retained-run-comparison.v1",
            "left":left,"right":right,
            "same_reported_spec":None if a is None or b is None else a==b,
            "minimum_step_id_edits":edits,"alignment":rows,
            "counts":{kind:counts[kind] for kind in ("matched_step_id","left_only","right_only")},
            "matched_status_changes":sum(row.get("status_changed",False) for row in rows),
            "matched_activity_label_changes":sum(row.get("activity_label_changed",False) for row in rows),
            "alignment_basis":"one_minimum_insert_delete_alignment_of_reported_step_ids",
            "business_case_identity_verified":False,**CLAIMS}
