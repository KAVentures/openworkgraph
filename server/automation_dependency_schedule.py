"""Read-only portfolio schedule/dependency consistency review (R15).

The quarter labels are reviewer plans, not a resource-loaded schedule. A
dependent planned earlier than an unfinished prerequisite is a candidate
contradiction, not a verified delay. Rejected, missing and unscheduled
prerequisites are disclosed; no plan or review state is changed.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from . import automation_pipeline as pipeline

FORMAT = "openworkgraph.automation-dependency-schedule-review.v1"
MAX_RISKS = 100
MAX_PATH = 20


def _quarter(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) != 7 or value[4:6] != "-Q":
        return None
    if not (value[:4].isdigit() and value[6:] in ("1", "2", "3", "4")):
        return None
    return int(value[:4]) * 4 + int(value[6])


def _planning(idea: dict[str, Any]) -> dict[str, Any]:
    value = idea.get("planning")
    return value if isinstance(value, dict) else {}


def review() -> dict[str, Any]:
    """No provider reads, writes, inferred completion, or savings arithmetic."""
    all_ideas = pipeline.pipeline()["ideas"]
    by_id = {idea["idea_id"]: idea for idea in all_ideas}
    active = sorted((i for i in all_ideas if i["state"] not in {"implemented", "rejected"}),
                    key=lambda i: i["idea_id"])
    risks: list[dict[str, Any]] = []
    truncated = False
    with_targets = 0
    with_dependencies = 0

    def add(kind: str, root: dict[str, Any], dep_id: str, path: list[str],
            dep: dict[str, Any] | None) -> bool:
        nonlocal truncated
        if len(risks) >= MAX_RISKS:
            truncated = True
            return False
        risks.append({
            "kind": kind,
            "idea_id": root["idea_id"],
            "dependency_id": dep_id,
            "path": path,
            "idea_target_quarter": _planning(root).get("target_quarter") or None,
            "dependency_target_quarter": (_planning(dep).get("target_quarter") or None) if dep else None,
            "dependency_state": dep["state"] if dep else "missing",
        })
        return True

    for root in active:
        declared = _planning(root)
        target = _quarter(declared.get("target_quarter"))
        if target is not None:
            with_targets += 1
        direct = declared.get("depends_on") or []
        if direct:
            with_dependencies += 1
        queue = deque(([dep, [root["idea_id"], dep]] for dep in sorted(set(direct))))
        visited = {root["idea_id"]}
        while queue:
            dep_id, path = queue.popleft()
            dep = by_id.get(dep_id)
            if dep_id in path[:-1]:
                if not add("dependency_cycle", root, dep_id, path, dep):
                    break
                continue
            if dep_id in visited:
                continue
            visited.add(dep_id)
            if dep is None:
                if not add("deleted_or_unknown_dependency", root, dep_id, path, None):
                    break
                continue
            if dep["state"] == "implemented":
                continue  # An already implemented predecessor is not a current planned blocker.
            if dep["state"] in {"rejected", "on_hold"}:
                if not add("blocked_dependency", root, dep_id, path, dep):
                    break
            predecessor_target = _quarter(_planning(dep).get("target_quarter"))
            if target is not None:
                if predecessor_target is None:
                    if not add("unscheduled_dependency", root, dep_id, path, dep):
                        break
                elif predecessor_target > target:
                    if not add("dependency_target_after_dependent", root, dep_id, path, dep):
                        break
            children = _planning(dep).get("depends_on") or []
            if children:
                if len(path) >= MAX_PATH:
                    if not add("review_depth_limit", root, dep_id, path, dep):
                        break
                else:
                    for child in sorted(set(children)):
                        queue.append((child, [*path, child]))
        if truncated:
            break

    return {
        "format": FORMAT,
        "active_ideas": len(active),
        "active_ideas_with_target_quarter": with_targets,
        "active_ideas_with_dependencies": with_dependencies,
        "risk_candidates": risks,
        "risk_candidates_truncated": truncated,
        "review_complete": not truncated and not any(
            r["kind"] in {"review_depth_limit", "dependency_cycle", "deleted_or_unknown_dependency"}
            for r in risks),
        "status": "review_candidates" if risks else "no_declared_conflicts_detected",
        "all_ideas_evaluated": not truncated,
        "source_systems_queried": False,
        "planned_quarters_are_verified": False,
        "schedule_feasibility_verified": False,
        "review_authorizes_implementation": False,
        "interpretation": (
            "Targets and dependencies are reviewer-entered portfolio planning labels. "
            "A prerequisite planned for a later quarter may contradict the dependent's "
            "target, but no delivery date or business outcome is predicted. A missing "
            "warning does not prove that a deadline is achievable. Historical, deleted "
            "or corrupt links and truncated traversal require human review."
        ),
    }


__all__ = ["review", "FORMAT", "MAX_RISKS", "MAX_PATH"]
