"""Finance reconciliation of one retained monitoring window, not causal ROI.

An accountant's declaration of period-level booked amounts is distinct
from telemetry. No invoice or financial source system is fetched.
"""
from __future__ import annotations

from fractions import Fraction
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_router_rehearsal import _money_bounds, _rational
from shared.process_shadow_review import Hash
from . import agent_procurement_monitor_journal as journal
from . import db

Amount=Annotated[StrictInt,Field(ge=0,le=10**18)]
MAX_RECONCILIATIONS=12
FORMAT="openworkgraph.procurement-finance-reconciliation.v1"
COSTS=("provider","external_services","incremental_payroll","implementation","other")
ARMS=("incumbent","challenger")


class Booked(Strict):
    arm: Literal["incumbent","challenger"]
    category: Literal["provider","external_services","incremental_payroll","implementation","other","revenue"]
    amount_microunits: Amount
    source_reference_sha256: Hash


class FinanceRequest(Strict):
    monitor_id: Alias
    window_id: Alias
    expected_head_sha256: Hash
    expected_report_sha256: Hash
    currency: Annotated[str,Field(pattern=r"^[A-Z]{3}$")]
    reviewer_alias: Alias
    accounting_basis: Literal["cash","accrual"]
    period_start_ms: Annotated[StrictInt,Field(ge=0)]
    period_end_exclusive_ms: Annotated[StrictInt,Field(ge=1)]
    booked: list[Booked]=Field(min_length=12,max_length=12)
    confirm_complete_period_bookings: StrictBool
    confirm_disjoint_arm_and_category_accounting: StrictBool
    confirm_exclude_imputed_labor_from_booked_cost: StrictBool
    confirmed: StrictBool

    @model_validator(mode="after")
    def check(self):
        if not (self.confirmed and self.confirm_complete_period_bookings and
                self.confirm_disjoint_arm_and_category_accounting and
                self.confirm_exclude_imputed_labor_from_booked_cost):
            raise ValueError("explicit accounting and retention confirmation required")
        if self.period_end_exclusive_ms<=self.period_start_ms:
            raise ValueError("invalid financial period")
        if {(b.arm,b.category) for b in self.booked}!={(a,c) for a in ARMS for c in (*COSTS,"revenue")}:
            raise ValueError("explicit amount for every arm/category required")
        if len({b.source_reference_sha256 for b in self.booked})!=len(self.booked):
            raise ValueError("duplicate finance source reference")
        return self


def _amount(x):
    return _money_bounds((x,x))["lower_microunits"]


def _calculate(req,window,observations):
    """Booked totals and a descriptive per-enrolled-case bridge.

    All scheduled cases count, including failures and unresolved cases.
    Telemetry direct costs and imputed time are not added to booked costs.
    """
    booked={(b.arm,b.category):b.amount_microunits for b in req.booked}
    slots={s["slot_id"]:s for s in window["plan"]["slots"]}
    arms={}
    for arm in ARMS:
        population={k for k,s in slots.items() if s["arm"]==arm}
        n=len(population)
        if not n:raise ValueError("empty arm")
        obs=[o for o in observations if o["slot_id"] in population]
        direct=sum(o["direct_cost_microunits"] or 0 for o in obs)
        direct_complete=len(obs)==n and all(o["direct_cost_microunits"] is not None and
            o["status"] in ("completed","failed") for o in obs)
        person=sum(o["human_person_ms"] or 0 for o in obs)
        person_complete=len(obs)==n and all(o["human_person_ms"] is not None and
            o["status"] in ("completed","failed") for o in obs)
        costs=sum(booked[arm,c] for c in COSTS)
        revenue=booked[arm,"revenue"]
        net=revenue-costs
        # Diagnostic gap only: provider+external invoices may not equal all
        # telemetry direct charges. Never auto-reclassify or net the gap.
        direct_booked=booked[arm,"provider"]+booked[arm,"external_services"]
        gap=(direct_booked-direct if direct_complete else None,direct_booked-direct)
        arms[arm]={"scheduled_cases":n,"recorded_cases":len(obs),
            "booked_by_category_microunits":{c:str(booked[arm,c]) for c in (*COSTS,"revenue")},
            "declared_booked_cost_microunits":str(costs),"declared_booked_revenue_microunits":str(revenue),
            "declared_booked_net_microunits":str(net),
            "declared_booked_net_per_enrolled_case":_amount(Fraction(net,n)),
            "telemetry_direct_cost":_money_bounds((direct,direct if direct_complete else None)),
            "telemetry_person_ms":_rational((person,person if person_complete else None)),
            "booked_provider_external_minus_telemetry_direct":_money_bounds(gap),
            "telemetry_complete":direct_complete and person_complete}
    a,b=(arms[k] for k in ARMS)
    delta=Fraction(int(b["declared_booked_net_microunits"]),b["scheduled_cases"])-Fraction(
        int(a["declared_booked_net_microunits"]),a["scheduled_cases"])
    return arms,_amount(delta)


def record(workflow_id,revision,document):
    req=FinanceRequest.model_validate(document)
    key=journal._key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");journal._schema(conn)
        state,history=journal._read(conn,key)
        if state is None:raise journal.JournalError("Monitoring journal not found",404)
        journal._check_head(history,req.expected_head_sha256)
        journal._require_current(conn,workflow_id,revision)
        report=state["report"]
        if report["report_sha256"]!=req.expected_report_sha256 or report["currency"]!=req.currency:
            raise journal.JournalError("Financial review report or currency changed",409)
        plan=state.get("monitor_plan")
        if plan is None:raise journal.JournalError("Verified retained plan required",409)
        window=next((w for w in plan["windows"] if w["window_id"]==req.window_id),None)
        if window is None:raise ValueError("unknown monitoring window")
        bounds=window["plan"]["request"]
        if (req.period_start_ms,req.period_end_exclusive_ms)!=(bounds["window_start_ms"],bounds["window_end_exclusive_ms"]):
            raise ValueError("finance period must match frozen window")
        if min(report["as_of_ms"],int(time.time()*1000))<req.period_end_exclusive_ms:
            raise ValueError("finance reconciliation requires closed window")
        if req.reviewer_alias.casefold()==state["coordinator_alias"].casefold():
            raise ValueError("distinct finance reviewer alias required")
        prior=state.get("finance_reconciliations",[])
        if len(prior)>=MAX_RECONCILIATIONS or any(r["window_id"]==req.window_id for r in prior):
            raise journal.JournalError("Window already reconciled or capacity reached",409)
        refs={b.source_reference_sha256 for b in req.booked}
        if refs & {ref for r in prior for ref in r["source_reference_sha256"]}:
            raise ValueError("finance source reference reused")
        # Check all retained journals, including historical revisions, for
        # exact finance evidence reuse. No cross-journal sums are generated.
        for other in conn.execute("SELECT workflow_id,revision,monitor_id FROM procurement_monitor_journals"):
            other_key=tuple(other)
            if other_key==key:continue
            other_state,_=journal._read(conn,other_key)
            if refs & {ref for r in other_state.get("finance_reconciliations",[]) for ref in r["source_reference_sha256"]}:
                raise ValueError("finance source reference reused across journals")
        obs=next((w["observations"] for w in state["snapshot"]["windows"] if w["window_id"]==req.window_id),[])
        arms,delta=_calculate(req,window,obs)
        result={"format":FORMAT,"window_id":req.window_id,"currency":req.currency,
            "period_start_ms":req.period_start_ms,"period_end_exclusive_ms":req.period_end_exclusive_ms,
            "accounting_basis":req.accounting_basis,
            "monitor_report_sha256":report["report_sha256"],"snapshot_sha256":report["snapshot_sha256"],
            "request_sha256":digest(req.model_dump()),"reviewer_alias":req.reviewer_alias,
            "source_reference_sha256":sorted(refs),"booked_declarations":[{**b.model_dump(),
                "amount_microunits":str(b.amount_microunits)} for b in req.booked],
            "arms":arms,"challenger_minus_incumbent_booked_net_per_case":delta,
            "sticky_monitoring_incidents":state["sticky_rollback_triggers"],
            "rollback_referral_recorded":state["rollback_referral_recorded"],
            "source_financial_documents_verified":False,"reviewer_identity_verified":False,
            "causal_savings_verified":False,"realized_roi_verified":False,"execution_authorized":False,
            "limitations":["Amounts are explicit reviewer declarations, including zero amounts; no invoice or payroll source is authenticated.",
                "Booked amounts on the declared accounting basis exclude imputed person-time; telemetry is a separate diagnostic and never added to booked costs.",
                "Arms have disjoint enrolled populations. Per-case net differences are descriptive, not causal or annualized savings.",
                "A later monitor snapshot does not rewrite this historical financial reconciliation; current incident status is separate."]}
        result["reconciliation_sha256"]=digest(result)
        state["finance_reconciliations"]=[*prior,result]
        return journal._save(conn,key,state,history,"finance_reconciliation",{
            "window_id":req.window_id,"reconciliation_sha256":result["reconciliation_sha256"],
            "report_sha256":report["report_sha256"],"reviewer_alias":req.reviewer_alias})
