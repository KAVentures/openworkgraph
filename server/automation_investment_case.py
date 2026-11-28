"""Read-only phased investment scenarios over exact retained effort measurements."""
from __future__ import annotations

from datetime import date,datetime,timezone
from fractions import Fraction

from pydantic import ValidationError

from shared import phased_investment_business_case as core
from . import automation_benefits as benefits
from . import automation_quality_benefit as quality

CLAIMS={"measurement_sources_independently_verified":False,"causal_effect_verified":False,
        "actual_cash_savings_verified":False,"financial_return_verified":False,
        "execution_authorized":False,"portfolio_modified":False}


def _snapshot(idea_id):
    try:
        snap=quality._snapshot(idea_id)
        rows=[]
        for raw in snap["measurements"]:
            fields={k:v for k,v in raw.items() if k not in {"measurement_id","recorded_at"}}
            m=benefits.Measurement.model_validate(fields)
            rows.append({"measurement_id":raw["measurement_id"],**m.model_dump(mode="json")})
        if len({r["measurement_id"] for r in rows})!=len(rows):
            raise ValueError
        return {**snap,"measurements":rows}
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        if isinstance(exc,benefits.BenefitError):
            raise
        raise benefits.BenefitError("retained measurement inventory is invalid; inspect the original records") from None


def inventory(idea_id):
    s=_snapshot(idea_id)
    return {"format":"openworkgraph.phased-investment-inventory.v1",
            **{k:s[k] for k in ("idea_id","idea_revision","snapshot_sha256","state","implemented_on")},
            "measurements":[{k:r[k] for k in ("measurement_id","period","start","end","cases",
                            "minutes_per_case","total_hours","source")} for r in s["measurements"]],
            "maximum_designs":core.MAX_DESIGNS,"maximum_months":core.MAX_MONTHS,**CLAIMS}


def _pool(rows):
    hours=sum((core.q(r["total_hours"]) if r["total_hours"] is not None else
               core.q(r["minutes_per_case"])*r["cases"]/60 for r in rows),Fraction(0))
    cases=sum(r["cases"] for r in rows)
    days=sum((date.fromisoformat(r["end"])-date.fromisoformat(r["start"])).days+1 for r in rows)
    return hours/cases,{"periods":len(rows),"cases":cases,"calendar_days":days,
        "hours":core.display(hours),"hours_per_case":core.display(hours/cases,10),
        "minutes_per_case":core.display(hours*60/cases),
        "normalized_reported_monthly_cases":core.display(Fraction(cases)*core.q(benefits.DAYS_PER_MONTH)/days),
        "declared_source_kinds":sorted({r["source"] for r in rows})}


def _review_basis(idea_id,payload):
    try:
        spec=core.Scenario.model_validate(payload)
        core.validate(spec)
    except (ValidationError,ValueError) as exc:
        raise benefits.BenefitError("invalid bounded investment assumptions" if isinstance(exc,ValidationError) else str(exc)) from None
    snapshot=_snapshot(idea_id)
    if snapshot["snapshot_sha256"]!=spec.snapshot_sha256:
        raise benefits.BenefitError("retained idea or measurement snapshot changed; reload")
    if snapshot["state"]!="implemented" or not snapshot["implemented_on"]:
        raise benefits.BenefitError("investment comparison requires an implemented idea with retained before/after measurements")
    by_id={r["measurement_id"]:r for r in snapshot["measurements"]}
    selected=set(spec.measurement_ids)
    if not selected<=by_id.keys():
        raise benefits.BenefitError("select exact retained measurement IDs")
    groups={k:sorted((by_id[i] for i in selected if by_id[i]["period"]==k),
                     key=lambda r:(r["start"],r["end"],r["measurement_id"])) for k in ("baseline","after")}
    implemented=snapshot["implemented_on"]
    today=datetime.now(timezone.utc).date().isoformat()
    for kind,rows in groups.items():
        if not rows:
            raise benefits.BenefitError("select at least one baseline and one after period")
        for r in rows:
            if r["cases"]<=0 or r["end"]>today or (kind=="baseline" and r["end"]>=implemented) or (kind=="after" and r["start"]<implemented):
                raise benefits.BenefitError("selected measurements need positive cases and entirely before/after implementation dates")
        if any(a["end"]>=b["start"] for a,b in zip(rows,rows[1:])):
            raise benefits.BenefitError("selected calendar periods overlap")
    b,b_info=_pool(groups["baseline"])
    a,a_info=_pool(groups["after"])
    return spec,snapshot,selected,by_id,b-a,b_info,a_info


def evaluate(idea_id,payload):
    spec,snapshot,selected,by_id,delta,b_info,a_info=_review_basis(idea_id,payload)
    result=core.evaluate(spec,delta)
    # Reject an edit during calculation rather than returning a report as if
    # it still referred to the current retained snapshot.
    if _snapshot(idea_id)["snapshot_sha256"]!=snapshot["snapshot_sha256"]:
        raise benefits.BenefitError("retained measurements changed during review; reload")
    return {**result,"idea_id":idea_id,"idea_revision":snapshot["idea_revision"],
            "snapshot_sha256":snapshot["snapshot_sha256"],
            "selected_measurement_ids":sorted(selected),"excluded_measurement_ids":sorted(by_id.keys()-selected),
            "retained_effort_basis":{"baseline":b_info,"after":a_info,
                "baseline_minus_after_hours_per_case":core.display(delta,10),
                "baseline_minus_after_minutes_per_case":core.display(delta*60),
                "reported_effort_direction":"lower_after" if delta>0 else "higher_after" if delta<0 else "unchanged",
                "source_kind_sets_match":b_info["declared_source_kinds"]==a_info["declared_source_kinds"],
                "quality_and_adoption_not_evaluated":True,"retained_effort_is_not_verified_cash":True},
            **CLAIMS}
