"""Exact bounded portfolio selection over reviewer-declared priorities and costs.

No benefit summation, currency conversion, scheduling forecast, or state change.
Uses the existing pipeline snapshot; dependency closure is explicit.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError

from . import automation_pipeline as pipeline
from . import automation_portfolio_frontier as frontier

MAX_CANDIDATES = 18
MAX_ALTERNATIVES = 5
ID = Annotated[str, Field(pattern=r"^idea_[a-f0-9]{16}$")]
MONEY = Annotated[StrictInt | StrictFloat, Field(ge=0, le=10**12)]
ACTIVE = {"idea", "assessing", "approved"}
CLAIMS = {"modeled_benefits_summed": False, "savings_claimed": False,
          "budget_approval_granted": False, "execution_authorized": False,
          "delivery_feasibility_verified": False, "source_systems_queried": False}


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idea_id: ID
    revision: int = Field(ge=1)
    priority: int = Field(ge=0, le=100)
    required: StrictBool = False


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    budget_basis: Literal["estimate", "approved_budget"]
    budget_limit: MONEY
    max_selected: int = Field(ge=1, le=MAX_CANDIDATES)
    candidates: list[Candidate] = Field(min_length=1, max_length=MAX_CANDIDATES)
    frontier_review: frontier.FrontierSpec | None = None


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _planning(idea):
    p = idea.get("planning") or {}
    return {"budget": p.get("budget"), "depends_on": sorted(set(p.get("depends_on") or [])),
            "team": p.get("team") or "", "owner": p.get("owner") or "",
            "target_quarter": p.get("target_quarter") or ""}


def _snapshot():
    ideas = pipeline.pipeline()["ideas"]
    rows = [{"idea_id": i["idea_id"], "revision": i["revision"], "title": i["title"],
             "state": i["state"], **_planning(i)} for i in ideas]
    rows.sort(key=lambda i: i["idea_id"])
    return {"snapshot_sha256": _digest(rows), "ideas": rows}


def inventory():
    return {"format": "openworkgraph.portfolio-budget-inventory.v1",
            **_snapshot(), "max_candidates": MAX_CANDIDATES, **CLAIMS}


def _parse(value):
    try:
        result = Scenario.model_validate(value)
    except ValidationError:
        raise pipeline.PipelineError("invalid bounded portfolio scenario") from None
    ids = [c.idea_id for c in result.candidates]
    if len(set(ids)) != len(ids):
        raise pipeline.PipelineError("select distinct opportunity IDs")
    if result.frontier_review is not None:
        try:
            frontier.check(result.frontier_review)
            frontier.preferences.validate(result.frontier_review.preference_profiles,ids)
        except ValueError as exc:raise pipeline.PipelineError(str(exc)) from None
    return result


def _money(value):
    return Decimal(str(value))


def _decimal(value):
    # JSON numbers are for display only; exact decimal strings preserve the
    # budget comparison even for fractional currency units.
    return format(value, "f")


def solve(value):
    # Bounded JSON floats may include very small subnormal values. Preserve
    # the full decimal range when adding up to 18 costs.
    with localcontext() as context:
        context.prec = 400
        if not frontier.GUARD.acquire(blocking=False):
            raise pipeline.PipelineError("another local portfolio search is running")
        try:return _solve(value)
        finally:frontier.GUARD.release()


def _solve(value):
    spec = _parse(value)
    snapshot = _snapshot()
    if snapshot["snapshot_sha256"] != spec.snapshot_sha256:
        raise pipeline.PipelineError("portfolio changed; reload and review the current snapshot")
    by_id = {i["idea_id"]: i for i in snapshot["ideas"]}
    choices = sorted(spec.candidates, key=lambda c: c.idea_id)
    ids = [c.idea_id for c in choices]
    index = {idea_id: i for i, idea_id in enumerate(ids)}
    for c in choices:
        if c.idea_id not in by_id or by_id[c.idea_id]["revision"] != c.revision:
            raise pipeline.PipelineError("selected opportunity changed; reload the portfolio")
    blocked = {}
    costs = []
    dependencies = []
    required = 0
    for j, choice in enumerate(choices):
        idea = by_id[choice.idea_id]
        reasons = []
        if choice.required:
            required |= 1 << j
        if idea["state"] not in ACTIVE:
            reasons.append({"code":"ineligible_state","state":idea["state"]})
        budget = idea["budget"]
        if budget is None:
            reasons.append({"code":"missing_budget"})
        elif budget["currency"] != spec.currency or budget["basis"] != spec.budget_basis:
            reasons.append({"code":"different_currency_or_basis"})
        cost = (_money(budget["amount"]) if budget is not None and
                budget["currency"] == spec.currency and budget["basis"] == spec.budget_basis
                else Decimal(0))
        costs.append(cost)
        mask = 0
        for dep in idea["depends_on"]:
            other = by_id.get(dep)
            if other and other["state"] == "implemented":
                continue
            if other is None or dep not in index:
                reasons.append({"code":"unfinished_dependency_not_selected","dependency_id":dep})
            else:
                mask |= 1 << index[dep]
        dependencies.append(mask)
        if reasons:
            blocked[j] = reasons

    # Transitive closure, including explicit cycle detection. Blockers propagate
    # through every selected prerequisite; missing external prerequisites are
    # never implicitly added with an assumed zero cost.
    closure = list(dependencies)
    for k in range(len(ids)):
        for j in range(len(ids)):
            if closure[j] & (1 << k):
                closure[j] |= closure[k]
    for j in range(len(ids)):
        if closure[j] & (1 << j):
            blocked.setdefault(j, []).append({"code":"dependency_cycle"})
    bad = sum(1 << j for j in blocked)
    for j in range(len(ids)):
        if closure[j] & bad:
            blocked.setdefault(j, []).append({"code":"blocked_selected_prerequisite",
                "dependency_ids":[ids[k] for k in range(len(ids)) if closure[j] & bad & (1 << k)]})
    bad = sum(1 << j for j in blocked)
    cap = _money(spec.budget_limit)
    n = len(ids)
    priorities = [c.priority for c in choices]
    review=frontier.Frontier(ids,cap,spec.frontier_review.priority_targets,spec.frontier_review.budget_checkpoints,spec.frontier_review.preference_profiles) if spec.frontier_review is not None else None

    def packet(mask, total, points):
        return {"selected_idea_ids":[ids[j] for j in range(n) if mask & (1 << j)],
                "selected_count":mask.bit_count(), "priority_points":points,
                "budget_used":_decimal(total), "budget_remaining":_decimal(cap-total)}

    # Enumerate each subset exactly once, using include/exclude recursion.
    # Only bounded top candidates and count-level optima are retained.
    feasible = 0
    best = []
    by_count = {}
    considered = 0
    rejected = {"missing_required":0,"blocked":0,"dependency":0,"count_limit":0,"budget_limit":0}
    def visit(j, mask, total, points):
        nonlocal considered, feasible
        if j != n:
            visit(j+1,mask,total,points)
            visit(j+1,mask|(1<<j),total+costs[j],points+priorities[j])
            return
        considered += 1
        if mask & required != required:
            rejected["missing_required"] += 1
            return
        if mask & bad:
            rejected["blocked"] += 1
            return
        if any(mask & (1<<k) and closure[k] & mask != closure[k] for k in range(n)):
            rejected["dependency"] += 1
            return
        count = mask.bit_count()
        if count > spec.max_selected:
            rejected["count_limit"] += 1
            return
        if review is not None:
            review.visit(mask,total,points)
        if total > cap:
            rejected["budget_limit"] += 1
            return
        feasible += 1
        # Explicit objective: maximize reviewer priority; then minimize cost;
        # then fewer ideas; then lexicographic stable IDs. No modeled ROI.
        key = (-points,total,count,tuple(ids[k] for k in range(n) if mask & (1<<k)))
        row = (key,mask,total,points)
        if len(best) < MAX_ALTERNATIVES or key < best[-1][0]:
            best.append(row)
            best.sort(key=lambda item:item[0])
            del best[MAX_ALTERNATIVES:]
        if count not in by_count or key < by_count[count][0]:
            by_count[count] = row

    visit(0,0,Decimal(0),0)
    required_closure = required
    for j in range(n):
        if required & (1<<j):
            required_closure |= closure[j]
    required_cost = sum((costs[j] for j in range(n) if required_closure & (1<<j)),Decimal(0))
    result = {"format":"openworkgraph.portfolio-budget-scenario.v1",
            "snapshot_sha256":snapshot["snapshot_sha256"],"scenario_sha256":_digest(spec.model_dump(exclude={"frontier_review"} if review is None else set())),
            "currency":spec.currency,"budget_basis":spec.budget_basis,"budget_limit":_decimal(cap),
            "max_selected":spec.max_selected,"candidates":[{**by_id[c.idea_id], "priority":c.priority,
                    "required":c.required,"blockers":blocked.get(j,[]),
                    "unfinished_selected_prerequisite_ids":[ids[k] for k in range(n) if closure[j] & (1<<k)]}
                    for j,c in enumerate(choices)],
            "required_closure_ids":[ids[j] for j in range(n) if required_closure & (1<<j)],
            "required_closure_known_budget":_decimal(required_cost),
            "required_closure_cost_complete":not bool(required_closure & bad),
            "subsets_considered":considered,"feasible_subsets":feasible,"rejected_by_first_reason":rejected,
            "search_complete":True,
            "status":"feasible" if feasible else "no_feasible_portfolio",
            "alternatives":[packet(mask,total,points) for _,mask,total,points in best],
            "best_by_selected_count":[packet(mask,total,points) for _,mask,total,points in
                                      (by_count[k] for k in sorted(by_count))],
            "objective":"maximize_reviewed_priority_points_then_minimize_cost_then_count_then_ids",
            "priority_points_are_user_entered_not_measured_benefits":True,
            "no_currency_conversion":True,**CLAIMS}

    if review is not None:
        result["frontier_review"]=review.report()
    if _snapshot()["snapshot_sha256"]!=spec.snapshot_sha256:
        raise pipeline.PipelineError("portfolio changed during search; reload and review")
    return result
