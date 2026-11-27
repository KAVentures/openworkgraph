"""Read-only implementation history, exact forecast bridges and calendar review."""
from collections import Counter
from datetime import date,datetime,timezone
from decimal import Decimal
from typing import Annotated
from pydantic import Field,StrictInt,field_validator,model_validator
from .process_hierarchy import Strict,Digest
from .process_decision_tables import validate_date
from .analysis_snapshot import content_digest

FORMAT="openworkgraph.implementation-history-review.v1"
MAX_REVISIONS=50
ZERO=Decimal(0)
METRICS={
    "cost":("cost","cost_to_date","remaining_cost_estimate"),
    "effort":("effort_hours","effort_hours_to_date","remaining_effort_hours_estimate"),
}


class ReviewRequest(Strict):
    before_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    after_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_history_sha256: Digest
    as_of_date: str
    @field_validator("as_of_date")
    @classmethod
    def date_valid(cls,value):
        value=validate_date(value)
        if date.fromisoformat(value)>datetime.now(timezone.utc).date():
            raise ValueError("review date must not be in the future")
        return value
    @model_validator(mode="after")
    def ordered(self):
        if self.before_revision>=self.after_revision:
            raise ValueError("select two ordered distinct revisions")
        return self


def money(value):
    return format(value,".2f") if value is not None else None


def dec(value):
    return Decimal(value) if value is not None else None


def difference(a,b):
    return b-a if a is not None and b is not None else None


def total(a,b):
    return a+b if a is not None and b is not None else None


def index(record):
    return ({p["id"]:p for p in record["baseline"]["phases"]},
            {r["phase_id"]:r for r in record["reports"]},
            {r["phase_id"]:r for r in record["report_attributions"]})


def ref(r):
    return {"revision":r["revision"],"content_sha256":r["content_sha256"],
            "baseline_sha256":r["baseline_sha256"],"assessed_in_revision":r["assessed_in_revision"]}


def history_pin(records,root,access):
    return content_digest({"format":FORMAT,"root":root,"access_revision":access,"revisions":[ref(r) for r in records]})


def measure(p,r,keys):
    if p is None:return None
    baseline,actual,remaining=keys
    a,b=dec((r or {}).get(actual)),dec((r or {}).get(remaining))
    return {"baseline":dec(p[baseline]),"actual":a,"remaining":b,"forecast":total(a,b)}


def metric_snapshot(record,keys):
    phases,reports,_=index(record)
    values=[measure(p,reports.get(k),keys) for k,p in phases.items()]
    out={}
    for field in ("baseline","actual","remaining","forecast"):
        known=[x[field] for x in values if x[field] is not None]
        lower=sum(known,ZERO)
        # For forecast, even one known component contributes to the lower bound.
        if field=="forecast":
            lower=sum(((x["actual"] or ZERO)+(x["remaining"] or ZERO) for x in values),ZERO)
        out[field]={"complete_total":money(lower) if len(known)==len(values) else None,
                    "nonnegative_known_lower_bound":money(lower),"complete_phases":len(known),
                    "unknown_phases":len(values)-len(known)}
    out["forecast_variance_lower_bound"]=money(dec(out["forecast"]["nonnegative_known_lower_bound"])-dec(out["baseline"]["complete_total"]))
    return out


def _bridge(before,after,keys,comparable):
    bp,br,_=index(before);ap,ar,_=index(after)
    if not comparable:return {"comparable":False,"reason":"currency_changed"}
    amounts={}
    for field in ("baseline","actual","remaining","forecast"):
        changed=added=removed=ZERO
        paired=0;unknown=[]
        for k in sorted(bp.keys()|ap.keys()):
            old=measure(bp.get(k),br.get(k),keys)
            new=measure(ap.get(k),ar.get(k),keys)
            a=old[field] if old else ZERO
            b=new[field] if new else ZERO
            if a is None or b is None:unknown.append(k);continue
            if old is None:added+=b
            elif new is None:removed-=a
            else:changed+=b-a;paired+=1
        exact=not unknown
        amounts[field]={"retained_phase_change":money(changed),"added_phase_amount":money(added),
                        "removed_phase_amount_signed":money(removed),"known_comparable_phase_count":paired,
                        "unresolved_phase_ids":unknown,"complete_change":money(changed+added+removed) if exact else None,
                        "partial_components_are_not_total_change":not exact}
    return {"comparable":True,"amounts":amounts}


def transition(before,after):
    bp,br,ba=index(before);ap,ar,aa=index(after)
    comparable=before["baseline"]["currency"]==after["baseline"]["currency"]
    rows=[]
    for k in sorted(bp.keys()|ap.keys()):
        old,new=bp.get(k),ap.get(k)
        a,b=br.get(k),ar.get(k)
        fields=[f for f in ("name","cost","effort_hours","starts_on","finishes_on")
                if old is not None and new is not None and old[f]!=new[f]]
        report_fields=[f for f in sorted(set(a or {})|set(b or {})) if (a or {}).get(f)!=(b or {}).get(f)]
        metric_rows={}
        for name,keys in METRICS.items():
            left,right=measure(old,a,keys),measure(new,b,keys)
            metric_rows[name]={f:{"before":money(left[f]) if left else None,"after":money(right[f]) if right else None,
                                "change":money(difference(left[f],right[f])) if left and right and (name!="cost" or comparable) else None}
                               for f in ("baseline","actual","remaining","forecast")}
        rows.append({"phase_id":k,"before_name":old["name"] if old else None,"after_name":new["name"] if new else None,
                     "membership":"added" if old is None else "removed" if new is None else "retained",
                     "baseline_changed_fields":fields,"report_changed_fields":report_fields,
                     "before_status":a["status"] if a else "not_reported","after_status":b["status"] if b else "not_reported",
                     "before_attribution":ba.get(k),"after_attribution":aa.get(k),"attribution_changed":ba.get(k)!=aa.get(k),
                     "planned_start_shift_days":(date.fromisoformat(new["starts_on"])-date.fromisoformat(old["starts_on"])).days if old and new else None,
                     "planned_finish_shift_days":(date.fromisoformat(new["finishes_on"])-date.fromisoformat(old["finishes_on"])).days if old and new else None,
                     "metrics":metric_rows})
    return {"before":ref(before),"after":ref(after),"before_currency":before["baseline"]["currency"],"after_currency":after["baseline"]["currency"],
            "baseline_content_changed":before["baseline_sha256"]!=after["baseline_sha256"],
            "baseline_reassessed":before["assessed_in_revision"]!=after["assessed_in_revision"],
            "opportunity_reference_changed":before["baseline"]["opportunity"]!=after["baseline"]["opportunity"],
            "stored_review_transition":"added" if before["review"] is None and after["review"] is not None else
                 "cleared" if before["review"] is not None and after["review"] is None else "changed" if before["review"]!=after["review"] else "unchanged",
            "before_stored_review":before["review"],"after_stored_review":after["review"],
            "cost_bridge":_bridge(before,after,METRICS["cost"],comparable),
            "effort_bridge":_bridge(before,after,METRICS["effort"],True),"phases":rows}


def calendar(record,as_of):
    phases,reports,attributions=index(record)
    d=date.fromisoformat(as_of)
    rows=[]
    for k,p in phases.items():
        r=reports.get(k)
        a=attributions.get(k)
        status=r["status"] if r else "not_reported"
        start=date.fromisoformat(p["starts_on"]);finish=date.fromisoformat(p["finishes_on"])
        reported_start=date.fromisoformat(r["started_on"]) if r and r["started_on"] else None
        reported_finish=date.fromisoformat(r["finished_on"]) if r and r["finished_on"] else None
        # The selected revision can contain a report recorded after the
        # hypothetical date. Do not present it as known at that earlier date.
        flags=[]
        recorded=date.fromisoformat(a["at"][:10]) if a else None
        future_report=bool(recorded and recorded>d)
        future_dates=bool((reported_start and reported_start>d) or (reported_finish and reported_finish>d))
        if future_report:flags.append("report_recorded_after_review_date")
        if future_dates:flags.append("reported_date_after_review_date")
        baseline_future=record["saved_at"][:10]>as_of
        if baseline_future:flags.append("selected_revision_saved_after_review_date")
        usable=not future_report and not future_dates and not baseline_future
        terminal=status in ("completed","cancelled")
        if usable:
            if d>start and reported_start is None and not terminal:flags.append("planned_start_passed_without_reported_start")
            if d>finish and not terminal:flags.append("planned_finish_passed_without_terminal_report")
            if reported_start and reported_start>start:flags.append("reported_start_after_plan")
            if reported_finish and reported_finish>finish:flags.append("reported_finish_after_plan")
            if status=="on_hold":flags.append("reported_on_hold")
            if status=="cancelled":flags.append("reported_cancelled")
        rows.append({"phase_id":k,"name":p["name"],"planned_start":p["starts_on"],"planned_finish":p["finishes_on"],
                     "reported_status":status,"reported_start":r["started_on"] if r else None,"reported_finish":r["finished_on"] if r else None,
                     "report_recorded_at":a["at"] if a else None,"report_usable_for_date_review":usable,
                     "flags":flags,"days_past_planned_finish_without_terminal_report":(d-finish).days if usable and d>finish and not terminal else None,
                     "reported_start_delta_days":(reported_start-start).days if usable and reported_start else None,
                     "reported_finish_delta_days":(reported_finish-finish).days if usable and reported_finish else None})
    return {"as_of_date":as_of,"phase_count":len(rows),"flag_counts":dict(sorted(Counter(f for r in rows for f in r["flags"]).items())),
            "phases":rows,"historical_knowledge_reconstructed":False,
            "baseline_saved_after_review_date":record["saved_at"][:10]>as_of,
            "interpretation":"Calendar comparisons use the selected saved revision. Reports recorded after the selected date and reports with future actual dates are flagged and excluded from lateness findings. Absence of a report is not proof of missing work. This does not reconstruct historical knowledge or predict completion."}


def build(records,root,access,request,current_source):
    pin=history_pin(records,root,access)
    if pin!=request.expected_history_sha256:raise ValueError("implementation history changed; load exact revisions again")
    by_revision={r["revision"]:r for r in records}
    if request.before_revision not in by_revision or request.after_revision not in by_revision:raise ValueError("selected implementation revision is unavailable")
    before,after=by_revision[request.before_revision],by_revision[request.after_revision]
    timeline=[]
    for i,r in enumerate(records):
        cost=metric_snapshot(r,METRICS["cost"]);effort=metric_snapshot(r,METRICS["effort"])
        prev=records[i-1] if i else None
        timeline.append({**ref(r),"saved_at":r["saved_at"],"currency":r["baseline"]["currency"],
                         "stored_independent_review":r["review"] is not None,"baseline_author_actor_id":r["baseline_author_actor_id"],
                         "author_actor_id":r["author_actor_id"],"phase_count":len(r["baseline"]["phases"]),"reported_phases":len(r["reports"]),
                         "cost":cost,"effort":effort,
                         "baseline_reassessed_since_previous":prev is not None and prev["assessed_in_revision"]!=r["assessed_in_revision"],
                         "stored_review_changed_since_previous":prev is not None and prev["review"]!=r["review"]})
    currency_history={r["baseline"]["currency"] for r in records}
    # The existing report schema does not store a separate currency on each
    # report. Across currency changes, withhold budget conclusions.
    budget=None
    if len(currency_history)==1 and current_source and after["revision"]==records[-1]["revision"] and current_source["currency"]==after["baseline"]["currency"] and current_source["implementation_budget"] is not None:
        cap=dec(current_source["implementation_budget"])
        values=metric_snapshot(after,METRICS["cost"])
        lower=dec(values["forecast"]["nonnegative_known_lower_bound"])
        budget={"budget_context":money(cap),"forecast_lower_bound":money(lower),"forecast_complete":values["forecast"]["unknown_phases"]==0,
                "known_lower_bound_exceeds_budget":lower>cap,"minimum_excess_over_budget":money(max(ZERO,lower-cap)),
                "headroom_not_established_if_unknown":values["forecast"]["unknown_phases"]>0,
                "current_approved_assessment":current_source["current_approved_assessment"],
                "source_blockers":current_source["blockers"]}
    comparison=transition(before,after)
    if len(currency_history)>1:
        comparison["cost_bridge"]={"comparable":False,"reason":"ambiguous_report_currency_history"}
        for phase in comparison["phases"]:
            for field in phase["metrics"]["cost"].values():field["change"]=None
    return {"format":FORMAT,"root_workflow_id":root,"access_revision":access,"history_sha256":pin,
            "latest_revision":records[-1]["revision"],"timeline":timeline,"comparison":comparison,
            "calendar":calendar(after,request.as_of_date),"current_budget_review":budget,
            "multiple_currencies_in_history":len(currency_history)>1,
            "currency_warning":"Reported costs lack separate currency provenance; do not aggregate or compare monetary reports across currency changes." if len(currency_history)>1 else None,
            "review_sha256":content_digest({"history":pin,"request":request.model_dump(mode="json"),"source":current_source}),
            "actual_values_independently_verified":False,"spending_authorized":False,"execution_authorized":False,
            "realized_savings_verified":False,
            "interpretation":"Saved revisions are human declarations. Forecasts are actual-to-date plus remaining estimate only where both are known. Nonnegative known sums give lower bounds, never a complete forecast with missing fields. A negative reported actual change is a revision/correction, not a refund. Removed or added scope is kept distinct from retained phase changes. Historical stored reviews are not current approvals. No execution, earned value, resource capacity or outcome is verified."}
