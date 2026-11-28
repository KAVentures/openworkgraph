"""Retained reported run interval concurrency, never reconstructed live state."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from shared import run_concurrency as core
from . import automation_monitoring as m
from . import automation_run_evidence as e

FORMAT="openworkgraph.automation-run-concurrency.v1"
MAX_RUNS=10000


class Request(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    reported_spec_sha256: str|None=Field(pattern=e.HASH_PATTERN)
    start_date: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    capacity: int=Field(ge=1,le=MAX_RUNS)
    view: Literal["segments","runs","excluded"]="segments"
    offset: int=Field(default=0,ge=0,le=2*MAX_RUNS+1)
    limit: int=Field(default=50,ge=1,le=100)
    expected_review_sha256: str|None=Field(default=None,pattern=e.HASH_PATTERN)


def micros(delta):
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def review(deployment_id,payload):
    s=e._parse(Request,payload)
    if s.offset and not s.expected_review_sha256:
        raise m.MonitoringError("Review page one before paging")
    try:
        start,end=datetime.fromisoformat(s.start_date),datetime.fromisoformat(s.end_date)
    except ValueError:
        raise m.MonitoringError("Choose valid UTC dates") from None
    if not timedelta(0)<end-start<=timedelta(days=366) or end>datetime.now(timezone.utc).replace(tzinfo=None):
        raise m.MonitoringError("Choose a completed UTC window of at most 366 days")
    with m._LOCK:
        conn=m._connect()
        try:
            info=m._check(conn,deployment_id)
            rows=conn.execute("""SELECT run_id,started_at,finished_at,status,reported_spec_sha256
                FROM runs WHERE deployment_id=? AND started_at<? AND
                (finished_at IS NULL OR finished_at>=?) AND reported_spec_sha256 IS NOT DISTINCT FROM ?
                ORDER BY started_at,run_id LIMIT ?""",
                [deployment_id,end,start,s.reported_spec_sha256,MAX_RUNS+1]).fetchall()
        finally:conn.close()
    if len(rows)>MAX_RUNS:
        raise m.MonitoringError("Retained interval population exceeds the review budget; narrow the window")
    interval_rows=[]
    excluded=[]
    pins=[]
    metadata={}
    carry_in=carry_out=0
    for rid,a,b,status,version in rows:
        meta={"run_id":rid,"started_at":m._iso(a),"finished_at":m._iso(b),"status":status,
              "reported_spec_sha256":version}
        pins.append(meta)
        if rid in metadata or status not in m.RUN_STATUSES or (b is not None and b<a):
            raise m.MonitoringError("Invalid retained run interval population")
        metadata[rid]=meta
        if status=="running" or b is None:
            excluded.append({**meta,"reason":"no_terminal_interval"})
        elif max(start,a)>=min(end,b):
            excluded.append({**meta,"reason":"no_positive_intersection"})
        else:
            carry_in+=int(a<start)
            carry_out+=int(b>end)
            interval_rows.append((rid,micros(max(a,start)-start),micros(min(b,end)-start)))
    pin=e._digest({"format":FORMAT,"deployment_id":deployment_id,"attached_design_sha256":info["design_sha256"],
        "start":s.start_date,"end":s.end_date,"reported_spec_sha256":s.reported_spec_sha256,
        "capacity":s.capacity,"view":s.view,"interval_records":pins})
    if s.expected_review_sha256 and pin!=s.expected_review_sha256:
        raise m.MonitoringError("Retained interval evidence or review settings changed; restart from page one")
    try:
        result=core.summarize(interval_rows,micros(end-start),s.capacity)
    except ValueError as exc:
        raise m.MonitoringError(str(exc)) from None
    summary=result["summary"]
    # Aggregate run-microseconds can exceed the JS safe-integer range.
    for k in ("reported_run_elapsed_sum_us","excess_run_us"):
        summary[k]=str(summary[k])
    summary.update(selected_records=len(rows),excluded_records=len(excluded),
        missing_terminal_interval=sum(x["reason"]=="no_terminal_interval" for x in excluded),
        no_positive_intersection=sum(x["reason"]=="no_positive_intersection" for x in excluded),
        carry_in_intervals=carry_in,carry_out_intervals=carry_out)
    if summary["measured_intervals"]+summary["excluded_records"]!=len(rows):
        raise m.MonitoringError("Retained interval partition failed")
    if s.view=="segments":
        population=result["segments"]
    elif s.view=="runs":
        population=[{**metadata[x["run_id"]],**x} for x in result["runs"]]
    else:
        population=excluded
    page=population[s.offset:s.offset+s.limit]
    return {"format":FORMAT,"review_sha256":pin,"deployment_id":deployment_id,
        "start_date":s.start_date,"end_date_exclusive":s.end_date,"reported_spec_sha256":s.reported_spec_sha256,
        "capacity":s.capacity,"view":s.view,"summary":summary,"histogram":result["histogram"],
        "offset":s.offset,"matching_rows":len(population),"rows":page,
        "next_offset":s.offset+len(page) if page and s.offset+len(page)<len(population) else None,
        "complete_bounded_retained_interval_population":True,"step_evidence_pinned":False,
        "live_concurrency_verified":False,"capacity_verified":False,**e.CLAIMS,
        "interpretation":[
            "Retrospective concurrency of currently retained finished run reports with positive half-open intersection with the selected UTC window. Includes carry-in and carry-out clipped to the window. Runs without terminal timestamps and zero-duration or boundary-only records are excluded and counted.",
            "A zero measured count means no overlapping measured interval, not verified idle capacity or absence of other runs. Unfinished runs may overlap the window but are never assigned a duration. Unknown attribution may mix versions.",
            "Reported elapsed time is not CPU time, human effort, queue delay, or resource utilization. Capacity is a declared threshold only. Above-capacity time and excess run-microseconds are descriptive, not service loss or a staffing recommendation.",
            "The review pins only interval records and settings, not step evidence, source completeness or historical reporting state. No runtime is contacted or changed."]}
