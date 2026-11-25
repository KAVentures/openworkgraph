"""Scenario accounting over exact retained effort periods and entered quality/costs.

No durable new store, automatic source reads, causal or verified savings claims.
All additional observations are reviewer-supplied; missing data never becomes zero.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictFloat, ValidationError

from . import automation_benefits as benefits
from . import automation_pipeline as pipeline

FORMAT = "openworkgraph.quality-adjusted-benefit-scenario.v1"
NUM = Annotated[StrictInt | StrictFloat, Field(ge=0, le=10**12)]
COUNT = Annotated[StrictInt, Field(ge=0, le=100_000_000)]
ID = Annotated[str, Field(pattern=r"^meas_[a-f0-9]{16}$")]
CLAIMS = {"measurement_sources_independently_verified":False,
          "causal_effect_verified":False,"savings_claimed":False,
          "financial_return_verified":False,"business_outcomes_verified":False,
          "execution_authorized":False}


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    measurement_id: ID
    eligible_cases: COUNT
    automation_attempted_cases: COUNT
    automation_handoff_cases: COUNT
    quality_checked_cases: COUNT
    quality_failed_cases: COUNT
    extra_review_hours: NUM
    review_effort_basis: Literal["already_in_retained_effort","additional_to_retained_effort"]
    nonlabor_cost: NUM


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    labor_cost_per_hour: NUM
    one_time_cost: NUM
    minimum_quality_coverage: Annotated[StrictInt|StrictFloat,Field(ge=0,le=1)]
    minimum_after_adoption: Annotated[StrictInt|StrictFloat,Field(ge=0,le=1)]
    maximum_quality_failure_rate_increase: Annotated[StrictInt|StrictFloat,Field(ge=0,le=1)]
    periods: list[Period] = Field(min_length=2,max_length=benefits.MAX_MEASUREMENTS_PER_IDEA)


def _d(value):
    return Decimal(str(value))


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
                             separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _snapshot(idea_id):
    # Same SQLite database as the pipeline and measurement table. A single
    # explicit read transaction avoids a mixed idea/period snapshot.
    # _connect initializes existing schemas; no new persistent tables.
    with pipeline._LOCK, benefits._LOCK, closing(pipeline._connect()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS idea_measurements (
            measurement_id TEXT PRIMARY KEY,idea_id TEXT NOT NULL,created_at TEXT NOT NULL,body TEXT NOT NULL)""")
        conn.commit()
        conn.execute("BEGIN")
        try:
            idea = pipeline._public(pipeline._get(conn,idea_id))
            rows = conn.execute("""SELECT measurement_id,created_at,body FROM idea_measurements
                                  WHERE idea_id=? ORDER BY measurement_id""",[idea_id]).fetchall()
        except pipeline.PipelineError as exc:
            raise benefits.BenefitError(str(exc)) from None
        finally:
            conn.rollback()
    if len(rows)>benefits.MAX_MEASUREMENTS_PER_IDEA:
        raise benefits.BenefitError("retained measurement count exceeds supported bounds")
    periods = [{"measurement_id":mid,"recorded_at":stamp,**json.loads(body)}
               for mid,stamp,body in rows]
    snap = {"idea_id":idea_id,"idea_revision":idea["revision"],"state":idea["state"],
            "implemented_on":str(benefits._implemented_on(idea)) if benefits._implemented_on(idea) else None,
            "measurements":periods}
    return {**snap,"snapshot_sha256":_digest(snap)}


def inventory(idea_id):
    snap = _snapshot(idea_id)
    # Source references and notes are not needed for the scenario UI.
    return {"format":"openworkgraph.quality-benefit-inventory.v1",**snap,
            "measurements":[{k:r[k] for k in ("measurement_id","period","start","end","cases",
                                             "minutes_per_case","total_hours","source")}
                            for r in snap["measurements"]],**CLAIMS}


def _ratio(n,d):
    return n/d if d else None


def _display(value):
    if value is None:
        return None
    number = float(value)
    # Extremely small positive net values can imply payback outside the
    # finite JSON float range. Keep a decimal string rather than infinity.
    return round(number,8) if math.isfinite(number) else format(value,'.8E')


def _period(row, entered, implemented):
    start,end = date.fromisoformat(row["start"]),date.fromisoformat(row["end"])
    if end<start or end>datetime.now(timezone.utc).date():
        raise benefits.BenefitError("invalid retained calendar period")
    if (row["period"]=="baseline" and end>=implemented or
            row["period"]=="after" and start<implemented):
        raise benefits.BenefitError("selected periods must be entirely before or after implementation")
    cases = row["cases"]
    if cases<=0:
        raise benefits.BenefitError("selected effort periods need positive case denominators")
    if not (entered.automation_handoff_cases<=entered.automation_attempted_cases
            <=entered.eligible_cases<=cases):
        raise benefits.BenefitError("handoffs must not exceed attempts, eligible cases or retained cases")
    if not entered.quality_failed_cases<=entered.quality_checked_cases<=cases:
        raise benefits.BenefitError("quality failures must not exceed checked or retained cases")
    if entered.review_effort_basis=="already_in_retained_effort" and entered.extra_review_hours!=0:
        raise benefits.BenefitError("already included review effort must have zero additional hours")
    hours = (_d(row["total_hours"]) if row["total_hours"] is not None
             else _d(cases)*_d(row["minutes_per_case"])/60)
    return {"measurement_id":row["measurement_id"],"period":row["period"],
            "start":start,"end":end,"days":(end-start).days+1,"cases":cases,
            "retained_hours":hours,"extra_hours":_d(entered.extra_review_hours),
            "cost":_d(entered.nonlabor_cost),"eligible":entered.eligible_cases,
            "attempts":entered.automation_attempted_cases,"handoffs":entered.automation_handoff_cases,
            "checked":entered.quality_checked_cases,"failed":entered.quality_failed_cases}


def _pool(rows):
    out = {key:sum(r[key] for r in rows) for key in ("days","cases","retained_hours",
          "extra_hours","cost","eligible","attempts","handoffs","checked","failed")}
    out["total_hours"]=out["retained_hours"]+out["extra_hours"]
    out["effort_hours_per_case"]=out["total_hours"]/_d(out["cases"])
    out["nonlabor_cost_per_case"]=out["cost"]/_d(out["cases"])
    out["quality_coverage"]=_ratio(_d(out["checked"]),_d(out["cases"]))
    out["quality_failure_rate"]=_ratio(_d(out["failed"]),_d(out["checked"]))
    out["attempted_share_of_eligible"]=_ratio(_d(out["attempts"]),_d(out["eligible"]))
    out["handoff_share_of_attempts"]=_ratio(_d(out["handoffs"]),_d(out["attempts"]))
    out["non_handoff_share_of_eligible"]=_ratio(_d(out["attempts"]-out["handoffs"]),_d(out["eligible"]))
    return out


def analyze(idea_id, value):
    try:
        spec=Scenario.model_validate(value)
    except ValidationError:
        raise benefits.BenefitError("invalid bounded quality and adoption scenario") from None
    snap=_snapshot(idea_id)
    if snap["snapshot_sha256"]!=spec.snapshot_sha256:
        raise benefits.BenefitError("retained measurements or idea changed; inspect the current snapshot again")
    if snap["state"]!="implemented" or snap["implemented_on"] is None:
        raise benefits.BenefitError("quality and adoption comparison needs an implemented idea")
    implemented=date.fromisoformat(snap["implemented_on"])
    by_id={r["measurement_id"]:r for r in snap["measurements"]}
    ids=[p.measurement_id for p in spec.periods]
    if len(ids)!=len(set(ids)) or not set(ids)<=by_id.keys():
        raise benefits.BenefitError("select distinct retained measurement IDs")
    with localcontext() as context:
        context.prec=400
        rows=[_period(by_id[p.measurement_id],p,implemented) for p in spec.periods]
        cohorts={kind:sorted((r for r in rows if r["period"]==kind),key=lambda r:(r["start"],r["end"]))
                 for kind in ("baseline","after")}
        for group in cohorts.values():
            if not group:
                raise benefits.BenefitError("select at least one baseline and one after measurement")
            if any(a["end"]>=b["start"] for a,b in zip(group,group[1:])):
                raise benefits.BenefitError("selected calendar periods overlap")
        b,a=(_pool(cohorts[k]) for k in ("baseline","after"))
        after_volume=_d(a["cases"])*_d(benefits.DAYS_PER_MONTH)/_d(a["days"])
        hourly=_d(spec.labor_cost_per_hour)
        raw_effort=(b["retained_hours"]/_d(b["cases"])-a["retained_hours"]/_d(a["cases"]))*after_volume
        adjusted_effort=(b["effort_hours_per_case"]-a["effort_hours_per_case"])*after_volume
        cost_change=(b["nonlabor_cost_per_case"]-a["nonlabor_cost_per_case"])*after_volume
        modeled_net=adjusted_effort*hourly+cost_change
        quality_delta=(None if b["quality_failure_rate"] is None or a["quality_failure_rate"] is None
                       else a["quality_failure_rate"]-b["quality_failure_rate"])
        gates=[]
        def gate(name,status,observed,threshold):
            gates.append({"gate":name,"status":status,"observed":_display(observed),
                          "threshold":_display(threshold)})
        for name,data in (("baseline",b),("after",a)):
            cov=data["quality_coverage"]
            minimum=_d(spec.minimum_quality_coverage)
            gate(name+"_quality_coverage","pass" if cov>=minimum and data["checked"]>0 else "insufficient",
                 cov,minimum)
        adoption=a["attempted_share_of_eligible"]
        min_adoption=_d(spec.minimum_after_adoption)
        gate("after_attempted_adoption","unknown" if adoption is None else
             "pass" if adoption>=min_adoption else "below_threshold",adoption,min_adoption)
        max_failure=_d(spec.maximum_quality_failure_rate_increase)
        quality_covered=all(g["status"]=="pass" for g in gates[:2])
        gate("quality_failure_rate_change","unknown" if quality_delta is None or not quality_covered else
             "pass" if quality_delta<=max_failure else "above_threshold",quality_delta,max_failure)
        flags=Counter(g["status"] for g in gates)
        financial={"after_monthly_case_volume":after_volume,
                   "retained_effort_change_hours_at_after_volume":raw_effort,
                   "effort_change_with_extra_review_hours_at_after_volume":adjusted_effort,
                   "nonlabor_cost_change_at_after_volume":cost_change,
                   "modeled_labor_value_change_at_after_volume":adjusted_effort*hourly,
                   "modeled_net_monthly_value_change":modeled_net,
                   "modeled_simple_payback_months":_d(spec.one_time_cost)/modeled_net if modeled_net>0 else None}
        pool=lambda d:{k:_display(v) if isinstance(v,Decimal) or v is None else v for k,v in d.items()}
        return {"format":FORMAT,"idea_id":idea_id,"idea_revision":snap["idea_revision"],
                "snapshot_sha256":snap["snapshot_sha256"],"scenario_sha256":_digest(spec.model_dump()),
                "assumptions":spec.model_dump(),"selected_measurement_ids":sorted(ids),
                "excluded_measurement_ids":sorted(by_id.keys()-set(ids)),
                "baseline":pool(b),"after":pool(a),"currency":spec.currency,
                "financial_scenario":{k:_display(v) for k,v in financial.items()},
                "review_gates":gates,"all_declared_gates_met":flags["pass"]==len(gates),
                "quality_adjusted_financial_estimate":False,
                "quality_is_separate_review_gate_not_monetized":True,
                "statistical_uncertainty_estimated":False,"no_currency_conversion":True,
                "interpretation":"Entered period totals and costs, normalized to after volume. Quality checks may be selective; adoption is attempted share, not successful autonomous completion. Financial arithmetic is conditional and not verified savings.",
                **CLAIMS}
