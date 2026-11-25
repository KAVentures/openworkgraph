"""Version-scoped step reliability on retained reports, not live execution."""
from datetime import datetime,timedelta
from pydantic import BaseModel,ConfigDict,Field
from . import automation_monitoring as m
from . import automation_run_evidence as e
from .event_log_quality import _set_digest

FORMAT="openworkgraph.step-reliability.v1"
MAX_STEP_IDS=5000


class Request(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    reported_spec_sha256: str | None = Field(pattern=e.HASH_PATTERN)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    expected_population_sha256: str | None = Field(default=None,pattern=e.HASH_PATTERN)
    step_id: str | None = Field(default=None,pattern=e.ID_PATTERN)
    offset: int = Field(default=0,ge=0,le=e.MAX_MATCHING_RUNS)
    limit: int = Field(default=30,ge=1,le=100)


def review(deployment_id,spec):
    s=e._parse(Request,spec)
    if (s.offset or s.step_id is not None) and not s.expected_population_sha256:
        raise m.MonitoringError("review the population before requesting step evidence")
    try:
        start,end=datetime.fromisoformat(s.start_date),datetime.fromisoformat(s.end_date)
    except ValueError:
        raise m.MonitoringError("select valid UTC dates") from None
    if not timedelta(0)<end-start<=timedelta(days=366):
        raise m.MonitoringError("select a positive UTC window of at most 366 days")
    with m._LOCK:
        conn=m._connect()
        try:
            info=m._check(conn,deployment_id)
            where="deployment_id=? AND started_at>=? AND started_at<? AND reported_spec_sha256 IS NOT DISTINCT FROM ?"
            args=[deployment_id,start,end,s.reported_spec_sha256]
            n=conn.execute("SELECT count(*) FROM runs WHERE "+where,args).fetchone()[0]
            if n>e.MAX_MATCHING_RUNS:
                raise m.MonitoringError("too many retained runs; narrow the review window")
            conn.execute("CREATE TEMP TABLE selected_runs AS SELECT * FROM runs WHERE "+where,args)
            scope="FROM steps s JOIN selected_runs r ON s.deployment_id=r.deployment_id AND s.run_id=r.run_id"
            total_steps=conn.execute("SELECT count(*) "+scope).fetchone()[0]
            if total_steps>e.MAX_MATCHING_STEPS:
                raise m.MonitoringError("too many retained steps; narrow the review window")
            distinct=conn.execute("SELECT count(DISTINCT s.step_id) "+scope).fetchone()[0]
            if distinct>MAX_STEP_IDS:
                raise m.MonitoringError("too many distinct step IDs; narrow the review window")
            if conn.execute("SELECT 1 "+scope+" GROUP BY s.run_id HAVING count(*)>? OR count(*)<>count(DISTINCT s.seq) LIMIT 1",[m.MAX_STEPS_PER_RUN]).fetchone():
                raise m.MonitoringError("retained step index is ambiguous or oversized")
            run_hash=_set_digest(conn,"""SELECT sha256(to_json(struct_pack(
                id:=run_id,started:=started_at,finished:=finished_at,state:=status,
                verified:=outcome_reported_verified,source:=outcome_source,spec:=reported_spec_sha256))) AS h FROM selected_runs""",[])
            step_hash=_set_digest(conn,"""SELECT sha256(to_json(struct_pack(
                id:=s.run_id,seq:=s.seq,step:=s.step_id,activity:=s.activity,
                state:=s.status,at_time:=s.observed_at))) AS h """+scope,[])
            basis={"format":FORMAT,"deployment_id":deployment_id,"reported_spec_sha256":s.reported_spec_sha256,
                   "start_date":s.start_date,"end_date":s.end_date,"runs_sha256":run_hash,"steps_sha256":step_hash}
            pin=e._digest(basis)
            if s.expected_population_sha256 and s.expected_population_sha256!=pin:
                raise m.MonitoringError("step reliability evidence changed; rerun review")
            conn.execute("""CREATE TEMP TABLE selected_steps AS
                SELECT s.*,min(s.seq) FILTER (WHERE s.status IN ('failed','handed_to_human'))
                    OVER (PARTITION BY s.run_id) AS first_issue_seq """+scope)
            conn.execute("""CREATE TEMP TABLE run_step AS SELECT run_id,step_id,
                count(*) AS occurrences,
                count(*) FILTER (WHERE status='completed') AS completed,
                count(*) FILTER (WHERE status='failed') AS failed,
                count(*) FILTER (WHERE status='skipped') AS skipped,
                count(*) FILTER (WHERE status='handed_to_human') AS handoff,
                count(DISTINCT status) AS states,
                bool_or(seq=first_issue_seq) FILTER (WHERE status IN ('failed','handed_to_human')) AS first_issue
                FROM selected_steps GROUP BY run_id,step_id""")
            aggregate=conn.execute("""SELECT step_id,count(*),sum(occurrences),sum(completed),sum(failed),sum(skipped),sum(handoff),
                count(*) FILTER (WHERE completed>0),count(*) FILTER (WHERE failed>0),
                count(*) FILTER (WHERE skipped>0),count(*) FILTER (WHERE handoff>0),
                count(*) FILTER (WHERE failed>0 OR handoff>0),
                count(*) FILTER (WHERE occurrences>1),count(*) FILTER (WHERE states>1),
                count(*) FILTER (WHERE first_issue),
                count(*) FILTER (WHERE failed>0 AND completed>0)
                FROM run_step GROUP BY step_id ORDER BY step_id""").fetchall()
            labels=dict(conn.execute("SELECT step_id,count(DISTINCT activity) FROM selected_steps GROUP BY step_id").fetchall())
            groups=[]
            for row in aggregate:
                key,runs,occ,done,fail,skip,hand,rd,rf,rs,rh,issues,repeat,mixed,first,fail_done=row
                groups.append({"step_id":key,"distinct_reported_activity_labels":labels[key],
                    "runs_with_step":runs,"runs_without_step":n-runs,"occurrences":occ,
                    "occurrences_by_status":{"completed":done,"failed":fail,"skipped":skip,"handed_to_human":hand},
                    "runs_with_status":{"completed":rd,"failed":rf,"skipped":rs,"handed_to_human":rh},
                    "runs_with_reported_issue":issues,"runs_with_repeated_step_id":repeat,
                    "runs_with_mixed_statuses":mixed,"runs_with_failed_and_completed_occurrences":fail_done,
                    "runs_where_first_reported_issue_is_step":first,
                    "reported_issue_share_among_runs_with_step":issues/runs,
                    "reported_issue_share_among_all_selected_runs":issues/n})
            status_counts=dict.fromkeys(m.RUN_STATUSES,0)
            status_counts.update(dict(conn.execute("SELECT status,count(*) FROM selected_runs GROUP BY status").fetchall()))
            coverage=conn.execute("""SELECT count(DISTINCT run_id),
                count(DISTINCT run_id) FILTER (WHERE status IN ('failed','handed_to_human'))
                FROM selected_steps""").fetchone()
            if sum(g["occurrences"] for g in groups)!=total_steps or sum(g["runs_where_first_reported_issue_is_step"] for g in groups)!=coverage[1]:
                raise m.MonitoringError("step reliability accounting failed")
            result={**basis,"population_sha256":pin,"deployment_name":info["name"],
                "matching_runs":n,"matching_steps":total_steps,"distinct_step_ids":distinct,
                "runs_without_reported_steps":n-coverage[0],"runs_with_reported_issue_step":coverage[1],
                "run_status_counts":status_counts,"unknown_attribution_selected":s.reported_spec_sha256 is None,
                "reported_spec_is_verified_runtime_identity":False,**e.CLAIMS,
                "interpretation":[
                    "Only runs with the exact declared specification hash, or explicitly selected missing hashes, are included. Unknown attribution may mix versions.",
                    "Run status counts for a step can overlap when an ID repeats. Missing steps do not prove skipped work or successful execution.",
                    "First issue is the first reported failed or human-handoff step by retained sequence, not a root cause. Failed and completed occurrences do not prove a successful retry.",
                    "No provider call, live runtime check, causal inference or execution authorization occurs."]}
            if s.step_id is None:
                page=groups[s.offset:s.offset+s.limit]
                result["page"]={"kind":"step_groups","matching":len(groups),"offset":s.offset,"rows":page,
                    "next_offset":s.offset+len(page) if page and s.offset+len(page)<len(groups) else None}
            else:
                selected=next((g for g in groups if g["step_id"]==s.step_id),None)
                if selected is None:
                    raise m.MonitoringError("select a step ID from the reviewed population")
                rows=conn.execute("""SELECT r.run_id,r.occurrences,r.completed,r.failed,r.skipped,r.handoff,
                    r.first_issue FROM run_step r JOIN selected_runs p ON p.run_id=r.run_id
                    WHERE r.step_id=? ORDER BY p.started_at DESC,r.run_id LIMIT ? OFFSET ?""",
                    [s.step_id,s.limit,s.offset]).fetchall()
                evidence=[]
                for rid,occ,done,failed,skipped,handoff,first in rows:
                    item=e._detail(conn,deployment_id,rid,info)
                    evidence.append({"reference":item["reference"],"run_status":item["run"]["status"],
                        "started_at":item["run"]["started_at"],"occurrences":occ,
                        "status_counts":{"completed":done,"failed":failed,"skipped":skipped,"handed_to_human":handoff},
                        "first_reported_issue":bool(first),
                        "matching_step_occurrences":[x for x in item["run"]["steps"] if x["step_id"]==s.step_id]})
                result["selected_step"]=selected
                result["page"]={"kind":"step_runs","matching":selected["runs_with_step"],"offset":s.offset,"rows":evidence,
                    "next_offset":s.offset+len(evidence) if evidence and s.offset+len(evidence)<selected["runs_with_step"] else None}
            return result
        finally:
            conn.close()
