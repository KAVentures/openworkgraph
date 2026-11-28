"""Exact snapshot calibration review and independently pinned evidence pages."""
from datetime import datetime
import json
from threading import BoundedSemaphore
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import event_log_prediction_snapshots as snapshots
from . import event_log_predictions as predictions
from . import event_log_workspace as workspace
from . import event_log_prediction_calibration_math as math

Error=workspace.EventLogWorkspaceError
_GUARD=BoundedSemaphore(1)
HASH=r"^[a-f0-9]{64}$"


class Settings(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    bins: int=10

    @field_validator('bins')
    @classmethod
    def valid_bins(cls,value):
        if value not in (5,10,20):raise ValueError('choose 5, 10 or 20 bins')
        return value
    minimum_cases: int=Field(default=5,ge=5,le=5000)
    group_by: Literal["none","activity","basis"]="activity"


class Evidence(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    expected_review_sha256: str=Field(pattern=HASH)
    group_sha256: str|None=Field(default=None,pattern=HASH)
    arm: Literal["model","baseline"]|None=None
    bin_index: int|None=Field(default=None,ge=0,le=19)
    state: Literal["all","finished","still_open","missing","inconsistent"]="all"
    offset: int=Field(default=0,ge=0,le=5000)
    limit: int=Field(default=25,ge=1,le=50)


def _observe(log_id,stored):
    _,_,as_of_text,settings_text,_,cases_text=stored
    settings=predictions.parse(json.loads(settings_text))
    frozen=json.loads(cases_text)
    if type(frozen) is not list or len(frozen)>snapshots.MAX_SNAPSHOT_CASES:
        raise Error("invalid frozen snapshot population")
    ids=[r["case_id"] for r in frozen]
    if len(set(ids))!=len(ids):raise Error("duplicate frozen case identity")
    snap_at=datetime.fromisoformat(as_of_text.rstrip("Z"))
    def query(conn):
        predictions._prepare(conn,settings,log_id)
        now=conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
        outcome_sql,params=predictions._outcome_sql(settings.outcome)
        rows=conn.execute(f"""SELECT c.case_id,c.complete,epoch(c.ended-?) AS remaining,{outcome_sql}
                    FROM pc c WHERE c.case_id IN (SELECT unnest(?)) ORDER BY c.case_id""",
                    [snap_at,*params,ids]).fetchall()
        return {"_calibration_observation":(now,rows)}
    result=workspace._query(log_id,settings.case_type,query)
    now,rows=result["_calibration_observation"]
    current={key:(complete,remaining,outcome) for key,complete,remaining,outcome in rows}
    horizon_valid=now is not None and now>=snap_at
    elapsed=(now-snap_at).total_seconds() if horizon_valid else None
    observed=[]
    for item in sorted(frozen,key=lambda r:r["case_id"]):
        state=current.get(item["case_id"])
        remaining=label=None
        if state is None:kind="missing"
        elif not horizon_valid or state[0] and not math.finite(state[1]):kind="inconsistent"
        elif state[0]:
            kind="finished";remaining=float(state[1]);label=state[2]
        else:kind="still_open"
        observed.append({"frozen":item,"state":kind,"remaining_seconds":remaining,
                         "elapsed_seconds":elapsed if kind=="still_open" else None,"outcome":label})
    return observed,snapshots._iso(now),settings.outcome is not None,result.get("log")


def _parse(payload,evidence):
    keys={"settings","evidence"} if evidence else {"settings"}
    if type(payload) is not dict or set(payload)!=keys:raise Error("provide exact calibration settings"+(" and evidence controls" if evidence else ""))
    try:
        s=Settings.model_validate(payload["settings"])
        e=Evidence.model_validate(payload["evidence"]) if evidence else None
    except ValidationError:raise Error("invalid bounded calibration settings or evidence controls") from None
    if e and ((e.arm is None)!=(e.bin_index is None) or e.bin_index is not None and e.bin_index>=s.bins):
        raise Error("select a valid exact probability bin and arm together")
    return s,e


def _run(log_id,snapshot_id,payload,evidence):
    settings,e=_parse(payload,evidence)
    if not _GUARD.acquire(blocking=False):raise Error("another local calibration review is running")
    try:
        stored=snapshots._row(log_id,snapshot_id)
        rows,now,outcome,log= _observe(log_id,stored)
        if snapshots._row(log_id,snapshot_id)!=stored:raise Error("frozen snapshot changed; reopen review")
        # Digest exact frozen row, settings and currently relevant observation.
        pin=math.digest({"format":math.FORMAT,"log_id":log_id,"snapshot":stored,"settings":settings.model_dump(),
                         "observation_horizon":now,"rows":rows})
        report=math.build(rows,settings,outcome)
        if not e:
            return {"format":math.FORMAT,"log_id":log_id,"snapshot_id":snapshot_id,"snapshot_as_of":stored[2],
                    "evaluated_against":now,"review_sha256":pin,"settings":settings.model_dump(),**report,
                    "source_completeness_verified":False,"future_calibration_verified":False,
                    "recalibration_applied":False,"execution_authorized":False,
                    "interpretation":"Quantile targets are compared with the empirical strict/inclusive probability bracket; tied completions are not automatically miscalibration. "
                        "Open cases contribute only known nonmembership at the elapsed horizon. Missing and inconsistent cases retain uncertainty. "
                        "Outcome probabilities use fixed equal-width bins on paired model and baseline probabilities. These are frozen-population identification bounds, not confidence intervals. No model is changed."}
        if e.expected_review_sha256!=pin:raise Error("calibration evidence changed; reopen the review")
        if e.group_sha256 is not None and e.group_sha256 not in {g["group_sha256"] for g in report["groups"]}:
            raise Error("group is not visible in this reviewed population")
        if e.arm is not None and not outcome:raise Error("snapshot does not contain outcome estimates")
        def match(row):
            if e.state!="all" and row["state"]!=e.state:return False
            if e.group_sha256 is not None and math.digest(math.group_key(row,settings.group_by))!=e.group_sha256:return False
            if e.arm is not None:
                f=row["frozen"]
                if not math.probability(f.get("outcome_probability")) or not math.probability(f.get("base_rate")):return False
                field="outcome_probability" if e.arm=="model" else "base_rate"
                if math.bin_index(f[field],settings.bins)!=e.bin_index:return False
            return True
        selected=[r for r in rows if match(r)]
        page=selected[e.offset:e.offset+e.limit]
        return {"format":math.FORMAT,"log_id":log_id,"snapshot_id":snapshot_id,"review_sha256":pin,
                "controls":e.model_dump(),"matching_cases":len(selected),"cases":[math.evidence_row(r) for r in page],
                "next_offset":e.offset+len(page) if page and e.offset+len(page)<len(selected) else None}
    finally:_GUARD.release()


def review(log_id,snapshot_id,payload):
    return _run(log_id,snapshot_id,payload,False)


def evidence(log_id,snapshot_id,payload):
    return _run(log_id,snapshot_id,payload,True)
