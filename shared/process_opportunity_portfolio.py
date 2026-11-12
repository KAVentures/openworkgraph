"""A read-only, exact-revision budget preview for selected shared opportunities.

This is not a company budget, an authorization to spend, or actual expenditure.
All amounts are human-entered modeled implementation budgets. There is no
currency conversion and no portfolio-wide data scan.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .process_hierarchy import Strict, ID, Digest
from .process_benefits import Currency

MAX_ITEMS = 100
Money = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,11})(\\.[0-9]{1,2})?$")]


class Selection(Strict):
    workflow_id: ID
    revision: Annotated[StrictInt, Field(ge=1, le=50)]
    content_sha256: Digest


class Preview(Strict):
    format: Literal["openworkgraph.selected-opportunity-budget-request.v1"] = "openworkgraph.selected-opportunity-budget-request.v1"
    currency: Currency
    ceiling: Money
    selections: list[Selection] = Field(min_length=1, max_length=MAX_ITEMS)
    user_confirmed: StrictBool = False

    @model_validator(mode="after")
    def no_duplicated_roots(self):
        ids = [ref.workflow_id for ref in self.selections]
        if len(set(ids)) != len(ids):
            raise ValueError("Each process opportunity can appear only once")
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
