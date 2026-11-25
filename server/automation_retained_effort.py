"""Source-pinned effort measurements from explicit numeric event attributes.

Do not substitute elapsed case duration for employee effort. A complete
measurement uses one exact case notion and a reviewed effort-bearing activity.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from . import automation_benefits as benefits
from . import automation_pipeline as pipeline
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

MAX_LINKS = 250_000
MAX_EVENTS = 250_000
MAX_VALUE = Decimal("1000000000")
FORMAT = "openworkgraph.retained-effort-measurement.v1"


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    log_id: str = Field(pattern=r"^log_[a-f0-9]{16}$")
    case_type: str = Field(min_length=1,max_length=200)
    period: Literal["baseline","after"]
    start: str
    end: str
    completion_activity: str = Field(min_length=1,max_length=200)
    effort_activity: str = Field(min_length=1,max_length=200)
    effort_attribute: str = Field(min_length=1,max_length=120)
    unit: Literal["seconds","minutes","hours"]
    effort_semantics_confirmed: StrictBool
    expected_preview: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")

    @field_validator("start","end")
    @classmethod
    def dates(cls,value):
        parsed=date.fromisoformat(value)
        if parsed.isoformat()!=value:
            raise ValueError("use YYYY-MM-DD")
        return value


def _parse(value):
    try:
        spec=Spec.model_validate(value)
    except (ValidationError,ValueError):
        raise benefits.BenefitError("invalid retained effort measurement settings") from None
    start,end=date.fromisoformat(spec.start),date.fromisoformat(spec.end)
    if end<start or (end-start).days>=366 or end>datetime.now(timezone.utc).date():
        raise benefits.BenefitError("select a measured period of 1 to 366 days, not in the future")
    if not spec.effort_semantics_confirmed:
        raise benefits.BenefitError("confirm the attribute measures additive effort, not elapsed or waiting time")
    return spec,start,end


def _idea(idea_id,spec,start,end):
    try:
        idea=pipeline.get(idea_id)
    except pipeline.PipelineError as exc:
        raise benefits.BenefitError(str(exc)) from None
    if idea["revision"]!=spec.expected_revision:
        raise benefits.BenefitError("idea revision changed; reopen the idea")
    if idea["state"] not in ("approved","implemented"):
        raise benefits.BenefitError("select an approved or implemented idea")
    implemented=benefits._implemented_on(idea)
    if spec.period=="after" and (idea["state"]!="implemented" or implemented is None or start<implemented):
        raise benefits.BenefitError("after measurements need an implemented idea and dates on or after implementation")
    if spec.period=="baseline" and implemented is not None and end>=implemented:
        raise benefits.BenefitError("baseline must end before implementation")
    return idea


def _number(attributes,key):
    try:
        value=json.loads(attributes).get(key)
    except (ValueError,AttributeError):
        return None,"invalid"
    if value is None:
        return None,"missing"
    if type(value) not in (str,int,float) or len(str(value))>80:
        return None,"invalid"
    try:
        n=Decimal(str(value))
    except InvalidOperation:
        return None,"invalid"
    if not n.is_finite() or n<0 or n>MAX_VALUE or (n and n.adjusted() < -12):
        return None,"invalid"
    return n,"valid"


def _hash(digest,value):
    digest.update(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False).encode())
    digest.update(b"\n")


def preview(idea_id,value):
    spec,start,end=_parse(value)
    _idea(idea_id,spec,start,end)
    lo=datetime.combine(start,datetime.min.time())
    hi=datetime.combine(end+timedelta(days=1),datetime.min.time())
    settings=spec.model_dump(exclude={"expected_preview"})
    digest=sha256()
    _hash(digest,{"idea_id":idea_id,"settings":settings})
    totals={"started_cases":0,"complete_cases":0,"incomplete_cases":0,
        "cases_without_effort_activity":0,"effort_events":0,"missing_effort_events":0,
        "invalid_effort_events":0,"shared_effort_events":0}
    raw_total=Decimal(0)
    with workspace._LOCK, closing(workspace._connect()) as conn:
        info=workspace._check_log(conn,spec.log_id)
        notion=workspace._case_type(conn,spec.log_id,spec.case_type)
        if info["events"]>MAX_EVENTS:
            raise benefits.BenefitError("retained event population exceeds effort review bound")
        links=conn.execute("""SELECT count(*) FROM event_objects eo JOIN objects o
            ON o.log_id=eo.log_id AND o.object_id=eo.object_id
            WHERE eo.log_id=? AND o.object_type=?""",[spec.log_id,notion]).fetchone()[0]
        if links>MAX_LINKS:
            raise benefits.BenefitError("selected case projection exceeds effort review bound")
        revision=relationships._revision(conn,spec.log_id)
        _hash(digest,{"log_revision":revision})
        conn.execute("CREATE TEMP TABLE effort_ce AS "+workspace._CASE_EVENTS,[spec.log_id,notion])
        for label in {spec.completion_activity,spec.effort_activity}:
            if not conn.execute("SELECT 1 FROM effort_ce WHERE activity=? LIMIT 1",[label]).fetchone():
                raise benefits.BenefitError("selected stored activity is unavailable in this case notion")
        # Count shared events across the entire selected case notion, not just
        # the period cohort. Do not allocate one event's effort to two cases.
        rows=conn.execute("""WITH cohort AS (
              SELECT case_id FROM effort_ce GROUP BY case_id
              HAVING min(ts)>=? AND min(ts)<?),
            multiplicity AS (SELECT event_id,count(DISTINCT case_id) AS n
              FROM effort_ce GROUP BY event_id)
            SELECT e.case_id,e.event_id,e.activity,e.ts,e.attributes,m.n
            FROM effort_ce e JOIN cohort c USING(case_id)
            JOIN multiplicity m USING(event_id)
            ORDER BY e.case_id,e.ts,e.event_id""",[lo,hi]).fetchall()
        from itertools import groupby
        shared_ids=set()
        with localcontext() as ctx:
            ctx.prec=80
            for case_id,group in groupby(rows,key=lambda r:r[0]):
                case=list(group)
                totals["started_cases"]+=1
                finished=case[-1][2]==spec.completion_activity and case[-1][3]<hi
                totals["complete_cases" if finished else "incomplete_cases"]+=1
                effort=[r for r in case if r[2]==spec.effort_activity]
                if not effort:
                    totals["cases_without_effort_activity"]+=1
                for _,ident,activity,stamp,attrs,multiplicity in case:
                    # Digest the full ordered activity sequence and relevant
                    # numeric evidence, but never return event/case IDs.
                    if activity!=spec.effort_activity:
                        _hash(digest,[case_id,ident,activity,stamp.isoformat(),multiplicity])
                        continue
                    n,state=_number(attrs,spec.effort_attribute)
                    _hash(digest,[case_id,ident,activity,stamp.isoformat(),multiplicity,state,str(n) if n is not None else None])
                    totals["effort_events"]+=1
                    if multiplicity!=1:
                        shared_ids.add(ident)
                    if state!="valid":
                        totals[state+"_effort_events"]+=1
                    else:
                        raw_total+=n
            totals["shared_effort_events"]=len(shared_ids)
            hours=raw_total/{"seconds":Decimal(3600),"minutes":Decimal(60),"hours":Decimal(1)}[spec.unit]
    blockers=[]
    for key,code in (("incomplete_cases","incomplete_or_cross_boundary_cases"),
                     ("cases_without_effort_activity","cases_without_effort_activity"),
                     ("missing_effort_events","missing_effort_values"),
                     ("invalid_effort_events","invalid_effort_values"),
                     ("shared_effort_events","shared_event_attribution")):
        if totals[key]:
            blockers.append(code)
    if not totals["started_cases"]:
        blockers.append("empty_cohort")
    if hours>Decimal(10_000_000):
        blockers.append("effort_total_exceeds_measurement_bound")
    token=digest.hexdigest()
    return {"format":FORMAT,"idea_id":idea_id,"settings":settings,"log_revision":revision,
        "counts":totals,"preview_sha256":token,"blockers":blockers,"recordable":not blockers,
        "total_hours":float(hours) if not blockers else None,
        "minutes_per_case":float(hours*60/totals["started_cases"]) if not blockers else None,
        "source_reference":f"owg:effort:{spec.log_id}:{revision}:{token}",
        "source_system_validated":False,"employee_effort_independently_verified":False,
        "causal_effect_verified":False,"execution_authorized":False,
        "interpretation":"Complete raw stored case-start cohort; exact final completion label and selected additive effort attribute. No hidden activity or relabeling filters. The attribute's business meaning and completeness are reviewer-declared. Elapsed cycle time is never treated as effort."}


def record(idea_id,value):
    spec,start,end=_parse(value)
    if spec.expected_preview is None:
        raise benefits.BenefitError("preview the exact retained cohort before recording")
    result=preview(idea_id,value)
    if spec.expected_preview!=result["preview_sha256"]:
        raise benefits.BenefitError("retained effort preview changed; review again")
    if not result["recordable"]:
        raise benefits.BenefitError("measurement has unresolved coverage or attribution blockers")
    # Release the event workspace lock before acquiring pipeline locks. The
    # reference describes the analyzed historical snapshot, not live data.
    with pipeline._LOCK:
        _idea(idea_id,spec,start,end)
        with benefits._LOCK:
            with closing(benefits._connect()) as conn:
                rows=conn.execute("SELECT body FROM idea_measurements WHERE idea_id=?",[idea_id]).fetchall()
            if any(json.loads(body).get("source_reference")==result["source_reference"] for (body,) in rows):
                raise benefits.BenefitError("this exact retained effort measurement is already recorded")
            note=json.dumps({"format":FORMAT,"settings":result["settings"],"log_revision":result["log_revision"]},
                            ensure_ascii=False,separators=(",",":"))
            if len(note)>1000:
                raise benefits.BenefitError("measurement provenance exceeds the retained note bound")
            return benefits.record(idea_id,{"period":spec.period,"start":spec.start,"end":spec.end,
                "cases":result["counts"]["started_cases"],"total_hours":result["total_hours"],
                "source":"event_log","source_reference":result["source_reference"],"note":note})
