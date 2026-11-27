"""Exact sensitivity from the existing complete portfolio subset enumeration.

No new optimization constraints, cost estimates or benefit aggregation.
The collector keeps O(total priority + candidate count) rows, not subsets.
Each row is the existing solver's objective key, mask, exact cost and points.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


FORMAT = "openworkgraph.portfolio-budget-sensitivity.v1"


class Sensitivity:
    def __init__(self, ids):
        self.ids = tuple(ids)
        self.count = 0
        self.included = [0] * len(ids)
        self.best_in = [None] * len(ids)
        self.best_out = [None] * len(ids)
        self.best_by_points = {}
        self.optimum = None
        self.optimal_priority = None
        self.optimal_priority_count = 0
        self.optimal_priority_included = [0] * len(ids)
        self.optimal_cost = None
        self.optimal_cost_count = 0
        self.optimal_cost_included = [0] * len(ids)

    @staticmethod
    def _better(old, new):
        return new if old is None or new[0] < old[0] else old

    def observe(self, row):
        """One feasible subset after ALL existing solver constraints."""
        key, mask, cost, points = row
        self.count += 1
        self.optimum = self._better(self.optimum, row)
        self.best_by_points[points] = self._better(self.best_by_points.get(points), row)
        new_priority = self.optimal_priority is None or points > self.optimal_priority
        if new_priority:
            self.optimal_priority = points
            self.optimal_priority_count = 0
            self.optimal_priority_included = [0] * len(self.ids)
        priority_tie = points == self.optimal_priority
        if priority_tie:
            self.optimal_priority_count += 1
            if new_priority or self.optimal_cost is None or cost < self.optimal_cost:
                self.optimal_cost = cost
                self.optimal_cost_count = 0
                self.optimal_cost_included = [0] * len(self.ids)
        cost_tie = priority_tie and cost == self.optimal_cost
        if cost_tie:
            self.optimal_cost_count += 1
        for i in range(len(self.ids)):
            if mask & (1 << i):
                self.included[i] += 1
                self.best_in[i] = self._better(self.best_in[i], row)
                if priority_tie:
                    self.optimal_priority_included[i] += 1
                if cost_tie:
                    self.optimal_cost_included[i] += 1
            else:
                self.best_out[i] = self._better(self.best_out[i], row)

    def _packet(self, row):
        if row is None:
            return None
        _, mask, cost, points = row
        return {"selected_idea_ids": [v for i, v in enumerate(self.ids) if mask & (1 << i)],
                "selected_count": mask.bit_count(), "priority_points": points,
                "budget_used": format(cost, "f")}

    @staticmethod
    def _membership(included, total):
        if not total:
            return "no_feasible_portfolio"
        if not included:
            return "none"
        if included == total:
            return "all"
        return "some"

    def _conditional(self, row):
        if row is None:
            return {"status": "infeasible", "best": None,
                    "priority_loss": None, "additional_budget_at_best": None}
        _, _, cost, points = row
        _, _, best_cost, best_points = self.optimum
        return {"status": "feasible", "best": self._packet(row),
                "priority_loss": best_points-points,
                "additional_budget_at_best": format(cost-best_cost, "f")}

    def report(self, cap: Decimal, targets: list[int]):
        # A point is dominated if the same or higher points can be achieved
        # at no greater cost. Keep only the best row for each cost; scan
        # increasing costs and retain strictly increasing priority.
        by_cost = {}
        for row in self.best_by_points.values():
            cost = row[2]
            by_cost[cost] = self._better(by_cost.get(cost), row)
        frontier = []
        previous = -1
        for cost, row in sorted(by_cost.items()):
            if row[3] > previous:
                frontier.append(row)
                previous = row[3]
        # All attainable priority thresholds in the entered cap, including
        # mandatory idea and closure constraints; no inference above the cap.
        thresholds = []
        for target in targets:
            row = next((r for r in frontier if r[3] >= target), None)
            thresholds.append({"target_priority_points": target,
                               "status": "attainable_within_reviewed_cap" if row else "not_attainable_within_reviewed_cap",
                               "minimum_budget": format(row[2], "f") if row else None,
                               "witness": self._packet(row)})
        candidates = []
        for i, idea_id in enumerate(self.ids):
            candidates.append({
                "idea_id": idea_id, "feasible_with_count": self.included[i],
                "feasible_without_count": self.count-self.included[i],
                "membership_in_all_feasible": self._membership(self.included[i], self.count),
                "membership_among_max_priority": self._membership(
                    self.optimal_priority_included[i], self.optimal_priority_count),
                "membership_among_max_priority_min_cost": self._membership(
                    self.optimal_cost_included[i], self.optimal_cost_count),
                "max_priority_with_count": self.optimal_priority_included[i],
                "max_priority_min_cost_with_count": self.optimal_cost_included[i],
                "force_include": self._conditional(self.best_in[i]),
                "force_exclude": self._conditional(self.best_out[i]),
            })
        return {
            "format": FORMAT, "reviewed_budget_cap": format(cap, "f"),
            "feasible_subset_count": self.count, "frontier_point_count": len(frontier),
            "frontier": [self._packet(r) for r in frontier],
            "priority_targets": thresholds, "candidate_sensitivity": candidates,
            "maximum_priority_points": self.optimal_priority,
            "minimum_cost_at_maximum_priority": (format(self.optimal_cost, "f")
                                                if self.optimal_cost is not None else None),
            "maximum_priority_subset_count": self.optimal_priority_count,
            "maximum_priority_min_cost_subset_count": self.optimal_cost_count,
            "frontier_complete_within_reviewed_cap": True,
            "constraints_relaxed": False, "above_cap_extrapolated": False,
            "priority_is_user_declared_not_benefit": True,
            "interpretation": [
                "Every feasible subset under the same reviewed constraints contributes once. The frontier contains non-dominated exact cost and priority pairs within the entered cap.",
                "Each point has one deterministic witness, not necessarily its only valid subset. Maximum-priority membership counts include every tied subset, not just displayed alternatives.",
                "Force-in and force-out keep every existing prerequisite, required-idea, count and budget constraint. Infeasibility does not suggest automatic deletion or removal of requirements.",
                "Additional budget is the conditional best cost minus the original best cost; it can be negative when priority is lost. No above-cap plan, probability, measured benefit or authorization is inferred.",
            ],
        }
