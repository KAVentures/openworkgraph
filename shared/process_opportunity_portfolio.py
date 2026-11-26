"""A read-only, exact-revision budget preview for selected shared opportunities.

This is not a company budget, an authorization to spend, or actual expenditure.
All amounts are human-entered modeled implementation budgets. There is no
currency conversion and no portfolio-wide data scan.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .process_hierarchy import Strict, ID, Digest
from .process_benefits import Currency

MAX_ITEMS = 100
Money = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")]


class Selection(Strict):
    workflow_id: ID
    revision: Annotated[StrictInt, Field(ge=1, le=50)]
    content_sha256: Digest


Multiplier = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]?)(\.[0-9]{1,4})?$")]


class StressScenario(Strict):
    label: Annotated[str, Field(strict=True, min_length=1,max_length=60,pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")]
    approved_multiplier: Multiplier
    unapproved_multiplier: Multiplier
    contingency: Money


class Preview(Strict):
    format: Literal["openworkgraph.selected-opportunity-budget-request.v1"] = "openworkgraph.selected-opportunity-budget-request.v1"
    currency: Currency
    ceiling: Money
    selections: list[Selection] = Field(min_length=1, max_length=MAX_ITEMS)
    user_confirmed: StrictBool = False
    stress_scenarios: list[StressScenario] | None = Field(default=None,min_length=1,max_length=8)

    @model_validator(mode="after")
    def no_duplicated_roots(self):
        ids = [ref.workflow_id for ref in self.selections]
        if len(set(ids)) != len(ids):
            raise ValueError("Each process opportunity can appear only once")
        if self.stress_scenarios:
            labels=[s.label.strip().casefold() for s in self.stress_scenarios]
            if len(set(labels))!=len(labels):
                raise ValueError("Stress scenario labels must be distinct")
        return self


def summarize(rows: list[dict], ceiling: str, currency: str) -> dict:
    """Add only fully resolved current snapshots; otherwise withhold every total."""
    blockers = [dict(workflow_id=r["workflow_id"], reasons=r["blockers"])
                for r in rows if r["blockers"]]
    result = {
        "format": "openworkgraph.selected-opportunity-budget-preview.v1",
        "status": "withheld" if blockers else "descriptive_budget_preview_only",
        "currency": currency, "declared_envelope": ceiling,
        "selected_count": len(rows), "rows": rows,
        "blockers": blockers, "totals": None,
        "selection_scope": "explicit_pinned_opportunities_only_not_the_entire_organization",
        "historical_budget_currency_conversion": False,
        "amounts_are_human_declared_estimates": True,
        "approved_stage_is_not_spending_approval": True,
        "actual_expenditure_verified": False,
        "realized_savings_verified": False,
        "execution_authorized": False,
        "spending_authorized": False,
        "possible_business_scope_overlap_not_deduplicated": True,
    }
    if blockers:
        return result
    total = sum((Decimal(r["implementation_budget"]) for r in rows), Decimal(0))
    approved = sum((Decimal(r["implementation_budget"]) for r in rows if r["current_approved_assessment"]), Decimal(0))
    proposed = total - approved
    slack = Decimal(ceiling) - total
    def value(amount):
        return format(amount.quantize(Decimal("0.01")), "f")
    result["totals"] = {
        "selected_budget_estimate": value(total),
        "currently_approved_stage_budget_estimate": value(approved),
        "unapproved_stage_budget_estimate": value(proposed),
        "envelope_headroom": value(slack),
        "within_declared_envelope": slack >= 0,
        "currency": currency,
        "spending_authorized": False,
        "actual_expenditure_verified": False,
    }
    return result


def _money(amount):
    return format(amount.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP),"f")


def stress_review(rows, ceiling, currency, scenarios):
    """Compare finite user-entered cost assumptions, never allocate budgets.

    Each revised item estimate is rounded to minor units before summation.
    Contingency is an explicit additive amount, not a percentage of an
    unknown cost base. Use integer-cent arithmetic for the break-even
    uniform multiplier on the declared 0.0001 grid.
    """
    if any(r["blockers"] for r in rows):
        return {"format":"openworkgraph.selected-budget-stress.v1","status":"withheld",
                "scenarios":None,"spending_authorized":False}
    cap=Decimal(ceiling)
    amounts=[(r,Decimal(r["implementation_budget"])) for r in rows]
    total=sum((a for _,a in amounts),Decimal(0))
    def uniform_cost(ticks):
        m=Decimal(ticks)/10000
        return sum(((a*m).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
                    for _,a in amounts),Decimal(0))
    reports=[]
    for scenario in scenarios:
        if not isinstance(scenario,StressScenario):
            scenario=StressScenario.model_validate(scenario)
        contingency=Decimal(scenario.contingency)
        approved=Decimal(scenario.approved_multiplier)
        unapproved=Decimal(scenario.unapproved_multiplier)
        items=[]
        for row,amount in amounts:
            multiplier=approved if row["current_approved_assessment"] else unapproved
            modeled=(amount*multiplier).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
            items.append({"workflow_id":row["workflow_id"],
                "current_approved_assessment":row["current_approved_assessment"],
                "original_estimate":_money(amount),"applied_multiplier":str(multiplier),
                "scenario_estimate":_money(modeled),"estimate_change":_money(modeled-amount)})
        approved_total=sum((Decimal(r["scenario_estimate"]) for r in items
                            if r["current_approved_assessment"]),Decimal(0))
        unapproved_total=sum((Decimal(r["scenario_estimate"]) for r in items
                              if not r["current_approved_assessment"]),Decimal(0))
        estimate=approved_total+unapproved_total+contingency
        slack=cap-estimate
        # Highest uniform multiplier (both stages) on the supported
        # 0.0001 grid, holding this scenario's contingency fixed.
        # Monotone integer search gives exact cent-rounded feasibility.
        maximum=999999
        feasible=None
        if contingency<=cap:
            low,high=0,maximum
            while low<high:
                mid=(low+high+1)//2
                if uniform_cost(mid)+contingency<=cap:low=mid
                else:high=mid-1
            feasible=low
        reports.append({"label":scenario.label,"approved_multiplier":str(approved),
            "unapproved_multiplier":str(unapproved),"contingency":_money(contingency),
            "approved_stage_estimate":_money(approved_total),
            "unapproved_stage_estimate":_money(unapproved_total),
            "scenario_total_including_contingency":_money(estimate),
            "change_from_original_estimate":_money(estimate-total),
            "headroom":_money(slack),"within_declared_envelope":slack>=0,
            "largest_feasible_uniform_multiplier_on_grid":
                format(Decimal(feasible)/10000,".4f") if feasible is not None else None,
            "uniform_multiplier_search_capped":feasible==maximum,
            "uniform_multiplier_grid_step":"0.0001",
            "items":items})
    worst=min(Decimal(r["headroom"]) for r in reports)
    best=max(Decimal(r["headroom"]) for r in reports)
    return {"format":"openworkgraph.selected-budget-stress.v1",
            "status":"descriptive_scenario_review_only","currency":currency,
            "declared_envelope":_money(cap),"original_selected_estimate":_money(total),
            "scenario_count":len(reports),"scenarios":reports,
            "all_tested_scenarios_within_envelope":all(r["within_declared_envelope"] for r in reports),
            "headroom_range":{"lower":_money(worst),"upper":_money(best)},
            "worst_headroom_scenario_labels":[r["label"] for r in reports if Decimal(r["headroom"])==worst],
            "scenario_probabilities_supplied":False,"scope_overlap_deduplicated":False,
            "actual_expenditure_verified":False,"spending_authorized":False,
            "execution_authorized":False,
            "interpretation":[
                "Stage multipliers are explicit scenario assumptions, not estimated probabilities or actual spending.",
                "Each revised item is rounded half up to cents before summing; the contingency is a fixed added amount.",
                "The uniform multiplier diagnostic holds contingency fixed and tests the same multiplier for every selected item on the 0.0001 grid from zero to 99.9999. It is not the stage-specific scenario or a recommendation. Null means even zero cannot fit; a capped result does not establish an unrestricted maximum.",
                "Only selected current opportunity snapshots are included. A scenario within the envelope does not grant spending authority or establish financial benefit."]}
