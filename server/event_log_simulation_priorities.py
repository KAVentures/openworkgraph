"""Priority classes for what-if simulation (#370 R14).

A scenario can state that a share of cases is more urgent: ``"priorities": [{"name": "urgent", "share": 0.2,
"rank": 0}, {"name": "standard", "share": 0.8, "rank": 1}]``. Each simulated case belongs to one class, drawn
from its own random number so that adding classes never changes another case's route or arrival. Every queue
then serves the lowest rank first, and first come, first served within a rank. Priority is non-preemptive: work
already started is never interrupted. The measured baseline keeps one first-come, first-served queue.

The result reports cycle time and waiting per class. With equal service times, priorities only reorder the
queue: the overall mean wait stays the same, while urgent cases wait less and the others more (Cobham's formulas
for a single server give the exact class waits).
"""
from __future__ import annotations

import math
import random
from typing import Any

from .event_log_workspace import EventLogWorkspaceError

MAX_CLASSES = 5
ASSUMPTIONS = [
    "In the scenario, each case belongs to a priority class drawn with the stated shares. Every queue serves the "
    "lowest rank first and first come, first served within a rank; work in progress is never interrupted. The "
    "measured baseline serves everyone first come, first served.",
]


def parse(spec: Any) -> list[dict[str, Any]] | None:
    if spec is None:
        return None
    if not isinstance(spec, list) or not 2 <= len(spec) <= MAX_CLASSES:
        raise EventLogWorkspaceError(f"priorities list 2 to {MAX_CLASSES} classes")
    classes, names = [], set()
    for item in spec:
        if not isinstance(item, dict) or not {"name", "share", "rank"} <= set(item) <= {"name", "share", "rank", "completion_target_minutes"}:
            raise EventLogWorkspaceError("each priority class has a name, a share and a rank")
        name, share, rank = item["name"], item["share"], item["rank"]
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 40 or name in names:
            raise EventLogWorkspaceError("priority class names are distinct and 1 to 40 characters")
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
            raise EventLogWorkspaceError("each priority class share is above 0 and at most 1")
        if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank <= 9:
            raise EventLogWorkspaceError("priority ranks are whole numbers from 0 (served first) to 9")
        target = item.get("completion_target_minutes")
        if "completion_target_minutes" in item:
            if (isinstance(target, bool) or not isinstance(target, (int, float))
                    or not 0 < target <= 525600 or not math.isfinite(target)):
                raise EventLogWorkspaceError(
                    "completion_target_minutes must be a finite number above 0 and at most 525600")
        names.add(name)
        classes.append({"name": name, "share": float(share), "rank": rank,
                        **({"completion_target_minutes": float(target)}
                           if "completion_target_minutes" in item else {})})
    if abs(sum(c["share"] for c in classes) - 1) > 1e-6:
        raise EventLogWorkspaceError("priority class shares must add up to 1")
    if len({c["rank"] for c in classes}) < 2:
        raise EventLogWorkspaceError("priority classes need at least two different ranks")
    return classes


def draw(classes: list[dict[str, Any]], seed: int, case: int) -> int:
    """The index of the case's class, from a random number that belongs to this case alone."""
    value, acc = random.Random(seed * 7_919 + case * 31 + 17).random(), 0.0
    for index, item in enumerate(classes):
        acc += item["share"]
        if value < acc:
            return index
    return len(classes) - 1


def summary(classes: list[dict[str, Any]], members: dict[int, list[float]], waits: dict[int, list[float]],
            percentile) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(classes):
        cycle, wait = sorted(members.get(index, [])), waits.get(index, [])
        rows.append({**item, "cases_measured": len(cycle),
                     "cycle_time_seconds": {"p50": percentile(cycle, 0.5), "p90": percentile(cycle, 0.9),
                                            "mean": sum(cycle) / len(cycle) if cycle else None},
                     "mean_wait_seconds_per_step": round(sum(wait) / len(wait), 3) if wait else 0})
    return rows


def completion_targets(
        classes: list[dict[str, Any]], assigned: dict[int, int], started: dict[int, float],
        finished: dict[int, float], abandoned: dict[int, str], warm: int,
) -> list[dict[str, Any]]:
    """Conservative target attainment accounting for *all* post-warm-up arrivals.

    A finished case is known on-time or late. Abandonment is a known miss; an
    unfinished case is unknown, not quietly excluded from the denominator.
    The lower/upper rates form an uncertainty interval, not a confidence interval
    or a certified service level. Only classes with an explicit target appear.
    """
    rows = []
    for index, item in enumerate(classes):
        if "completion_target_minutes" not in item:
            continue
        target = item["completion_target_minutes"]
        limit_seconds = target * 60
        total = on_time = late = abandoned_count = unresolved = 0
        for case in range(warm, len(started)):
            if assigned.get(case) != index:
                continue
            total += 1
            if case in finished:
                if finished[case] - started[case] <= limit_seconds:
                    on_time += 1
                else:
                    late += 1
            elif case in abandoned:
                abandoned_count += 1
            else:
                unresolved += 1
        rows.append({
            "name": item["name"], "rank": item["rank"],
            "completion_target_minutes": target,
            "post_warmup_arrivals": total,
            "completed_within_target": on_time,
            "completed_after_target": late,
            "abandoned": abandoned_count,
            "unresolved": unresolved,
            "known_target_misses": late + abandoned_count,
            "attainment_lower_bound": round(on_time / total, 6) if total else None,
            "attainment_upper_bound": round((on_time + unresolved) / total, 6) if total else None,
            "target_is_user_entered": True,
            "rates_are_modelled_not_observed": True,
        })
    return rows


__all__ = ["ASSUMPTIONS", "MAX_CLASSES", "completion_targets", "draw", "parse", "summary"]
