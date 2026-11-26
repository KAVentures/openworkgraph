"""Read-only portfolio schedule/dependency consistency review (R15).

The quarter labels are reviewer plans, not a resource-loaded schedule. A
dependent planned earlier than an unfinished prerequisite is a candidate
contradiction, not a verified delay. Rejected, missing and unscheduled
prerequisites are disclosed; no plan or review state is changed.
"""
from __future__ import annotations

from collections import deque, defaultdict
import hashlib
import json
from typing import Any

from . import automation_pipeline as pipeline
from shared.portfolio_dependency_cycles import cycle_inventory

FORMAT = "openworkgraph.automation-dependency-schedule-review.v1"
MAX_RISKS = 100
MAX_PATH = 20


def _quarter(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) != 7 or value[4:6] != "-Q":
        return None
    if not (value[:4].isascii() and value[:4].isdigit() and value[6:] in ("1", "2", "3", "4")):
        return None
    return int(value[:4]) * 4 + int(value[6])


def _planning(idea: dict[str, Any]) -> dict[str, Any]:
    value = idea.get("planning")
    return value if isinstance(value, dict) else {}


def dependency_waves(ideas: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact prerequisite layers, independent of the bounded risk-path display.

    One node per retained idea. Implemented prerequisites are satisfied solely
    by declaration. Rejected, on-hold and unknown dependencies are blocked,
    including all downstream dependents. A residual cycle or dependency on a
    cycle is never assigned a wave. Layers are precedence, not durations.
    """
    by_id = {i["idea_id"]: i for i in ideas}
    active = {k: i for k, i in by_id.items() if i["state"] not in {"implemented", "rejected"}}
    deps = {k: sorted(set(_planning(i).get("depends_on") or [])) for k, i in by_id.items()}
    snapshot = [[k, i["revision"], i["state"], deps[k],
                 _planning(i).get("target_quarter") or None]
                for k, i in sorted(by_id.items())]
    pin = hashlib.sha256(json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    children = defaultdict(list)
    pending = {}
    direct_blocks = {}
    for k, idea in active.items():
        unmet = [d for d in deps[k] if d not in by_id or by_id[d]["state"] != "implemented"]
        pending[k] = [d for d in unmet if d in active]
        for d in pending[k]:
            children[d].append(k)
        blockers = [{"idea_id": d, "state": by_id[d]["state"] if d in by_id else "missing"}
                    for d in unmet if d not in active or active[d]["state"] == "on_hold"]
        if idea["state"] == "on_hold":
            blockers.append({"idea_id": k, "state": "on_hold"})
        if blockers:
            direct_blocks[k] = blockers
    cycle_groups, cycle_membership, cycle_downstream = cycle_inventory(pending)
    unknown_links = sum(d not in by_id for k in active for d in deps[k])
    blocked = set(direct_blocks)
    queue = deque(sorted(blocked))
    while queue:
        k = queue.popleft()
        for child in children[k]:
            if child not in blocked:
                blocked.add(child)
                queue.append(child)
    eligible = set(active) - blocked
    degree = {k: len(pending[k]) for k in eligible}
    queue = deque(sorted(k for k in eligible if degree[k] == 0))
    wave = {}
    earliest = {}
    while queue:
        k = queue.popleft()
        wave[k] = 1 + max((wave[d] for d in pending[k]), default=0)
        # Maximum declared target among this idea and its unfinished
        # prerequisites, not a calculated earliest completion date.
        labels = [_planning(active[k]).get("target_quarter") or None]
        labels += [earliest[d] for d in pending[k]]
        earliest[k] = max(labels) if all(_quarter(v) is not None for v in labels) else None
        for child in children[k]:
            if child in eligible:
                degree[child] -= 1
                if degree[child] == 0:
                    queue.append(child)
    residual = eligible - wave.keys()
    rows = []
    groups = defaultdict(list)
    for k, idea in sorted(active.items()):
        if k in blocked:
            status = "blocked_by_declared_state" if k in direct_blocks else "depends_on_blocked_idea"
        elif k in residual:
            status = "cycle_or_depends_on_cycle"
        else:
            status = "prerequisites_declared_implemented" if wave[k] == 1 else "waiting_on_earlier_wave"
            groups[wave[k]].append(k)
        rows.append({
            "idea_id": k, "revision": idea["revision"], "state": idea["state"],
            "target_quarter": _planning(idea).get("target_quarter") or None,
            "status": status, "wave": wave.get(k),
            "cycle_group": cycle_membership.get(k),
            "depends_on_cycle": k in cycle_downstream,
            "also_blocked_by_declared_state": k in blocked,
            "unfinished_prerequisite_ids": sorted(d for d in deps[k]
                if d not in by_id or by_id[d]["state"] != "implemented"),
            "direct_blockers": direct_blocks.get(k, []),
            "blocked_predecessor_ids": sorted(d for d in pending[k] if d in blocked),
            "latest_declared_target_in_unfinished_chain": earliest.get(k),
        })
    return {
        "format": "openworkgraph.automation-dependency-waves.v1",
        "portfolio_basis_sha256": pin, "active_idea_count": len(active),
        "layered_idea_count": len(wave), "blocked_idea_count": len(blocked),
        "cycle_or_downstream_idea_count": len(residual),
        "exact_cycle_member_count": len(cycle_membership),
        "exact_cycle_downstream_count": len(cycle_downstream),
        "cycle_groups": [{"group": i+1, "idea_ids": group} for i, group in enumerate(cycle_groups)],
        "unknown_dependency_link_count": unknown_links,
        "wave_count": len(groups), "waves": [
            {"wave": level, "idea_ids": groups[level]} for level in sorted(groups)],
        "ideas": rows, "all_active_ideas_classified": True,
        "precedence_order_complete": not residual and not blocked,
        "implemented_states_independently_verified": False,
        "resources_or_durations_modeled": False,
        "execution_authorized": False,
    }


def review() -> dict[str, Any]:
    """No provider reads, writes, inferred completion, or savings arithmetic."""
    all_ideas = pipeline.pipeline()["ideas"]
    by_id = {idea["idea_id"]: idea for idea in all_ideas}
    active = sorted((i for i in all_ideas if i["state"] not in {"implemented", "rejected"}),
                    key=lambda i: i["idea_id"])
    risks: list[dict[str, Any]] = []
    truncated = False
    with_targets = sum(_quarter(_planning(i).get("target_quarter")) is not None for i in active)
    with_dependencies = sum(bool(_planning(i).get("depends_on")) for i in active)

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
        direct = declared.get("depends_on") or []
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

    waves = dependency_waves(all_ideas)
    return {
        "format": FORMAT,
        "dependency_waves": waves,
        "active_ideas": len(active),
        "active_ideas_with_target_quarter": with_targets,
        "active_ideas_with_dependencies": with_dependencies,
        "risk_candidates": risks,
        "risk_candidates_truncated": truncated,
        "review_complete": not truncated and not waves["cycle_groups"] and not waves["unknown_dependency_link_count"] and not any(
            r["kind"] in {"review_depth_limit", "dependency_cycle", "deleted_or_unknown_dependency"}
            for r in risks),
        "status": "review_candidates" if risks or waves["cycle_groups"] or waves["unknown_dependency_link_count"] else "no_declared_conflicts_detected",
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


__all__ = ["dependency_waves", "review", "FORMAT", "MAX_RISKS", "MAX_PATH"]
