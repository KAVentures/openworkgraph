"""Role discovery: groups of resources that perform the same mix of activities.

Organizational mining (Song and van der Aalst, 2008; PM4Py's role discovery; ProM's organizational
miner; Celonis and Signavio org views) finds roles from the log: resources whose activity profiles
are alike probably share a role, whatever the org chart says. This module:

1. counts, for each resource, how often it performed each activity;
2. starts with one group per resource and repeatedly merges the two groups whose summed activity
   profiles have the highest Pearson correlation, while that correlation is at least the threshold
   (default 0.65, as in PM4Py);
3. describes each group by its activity mix and by the activities only that group performs.

It describes who did what in the log, not what anyone's job is. Resources are pseudonymous by
default ("Resource 01", numbered by event count as in the organization view). No duration, speed,
ranking or performance measure of any person is computed.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_roles.v1"
MAX_RESOURCES = 200
MAX_ACTIVITIES = 200
DEFAULT_THRESHOLD = 0.65
TOP_ACTIVITIES = 8
_NAMES = {"pseudonymous", "shown"}


def _pearson(a: Counter, b: Counter, keys: list[str]) -> float:
    n = len(keys)
    if n < 2:
        return 1.0 if a and b else 0.0
    xa = [a.get(k, 0) for k in keys]
    xb = [b.get(k, 0) for k in keys]
    ma, mb = sum(xa) / n, sum(xb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    va = sum((x - ma) ** 2 for x in xa)
    vb = sum((y - mb) ** 2 for y in xb)
    if va == 0 or vb == 0:
        return 1.0 if va == vb == 0 else 0.0
    return cov / math.sqrt(va * vb)


def roles(log_id: str, case_type: str | None = None, *, threshold: float = DEFAULT_THRESHOLD,
          names: str = "pseudonymous", cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if names not in _NAMES:
        raise EventLogWorkspaceError("names must be pseudonymous or shown")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 < threshold < 1.0:
        raise EventLogWorkspaceError("threshold must be a correlation above 0 and below 1")

    def run(conn):
        ranked = conn.execute("""SELECT resource, count(*) AS n FROM ce WHERE resource <> ''
            GROUP BY 1 ORDER BY n DESC, resource""").fetchall()
        activities = [a for (a,) in conn.execute("""SELECT activity FROM ce WHERE resource <> ''
            GROUP BY 1 ORDER BY count(*) DESC, activity LIMIT ?""", [MAX_ACTIVITIES]).fetchall()]
        cells = conn.execute("""SELECT resource, activity, count(*) FROM ce WHERE resource <> ''
            GROUP BY 1, 2""").fetchall()
        total, with_resource = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE resource <> '') FROM ce").fetchone()
        return {"ranked": ranked, "activities": activities, "cells": cells,
                "coverage": {"events": total, "events_with_resource": with_resource,
                             "resources": len(ranked), "resources_truncated": len(ranked) > MAX_RESOURCES,
                             "activities_truncated": False}}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    ranked = data.pop("ranked")[:MAX_RESOURCES]
    activities = data.pop("activities")
    keep = set(activities)
    label = {name: (name if names == "shown" else f"Resource {i:02d}") for i, (name, _) in enumerate(ranked, start=1)}
    profile: dict[str, Counter] = {name: Counter() for name in label}
    for resource, activity, n in data.pop("cells"):
        if resource in profile and activity in keep:
            profile[resource][activity] += n
    groups: list[tuple[list[str], Counter]] = [([name], profile[name]) for name, _ in ranked if profile[name]]
    merges = []
    while len(groups) > 1:
        best, pair = threshold, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                r = _pearson(groups[i][1], groups[j][1], activities)
                if r >= best + (0 if pair is None else 1e-12):
                    best, pair = r, (i, j)
        if pair is None:
            break
        i, j = pair
        merged = (groups[i][0] + groups[j][0], groups[i][1] + groups[j][1])
        merges.append({"correlation": round(best, 4), "role_size": len(merged[0])})
        groups = [g for k, g in enumerate(groups) if k not in pair] + [merged]
    performers: dict[str, set[int]] = {}
    groups.sort(key=lambda g: (-sum(g[1].values()), min(label[m] for m in g[0])))
    for index, (members, counts) in enumerate(groups):
        for activity in counts:
            performers.setdefault(activity, set()).add(index)
    out = []
    for index, (members, counts) in enumerate(groups):
        events = sum(counts.values())
        order = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        out.append({
            "role": f"Role {index + 1}",
            "members": sorted((label[m] for m in members), key=lambda s: (s.startswith("Resource "), s)),
            "member_count": len(members), "events": events,
            "activity_mix": [{"activity": a, "events": n, "share": round(n / events, 4)} for a, n in order[:TOP_ACTIVITIES]],
            "activities": len(counts),
            "exclusive_activities": sorted(a for a in counts if performers[a] == {index}),
        })
    return {"format": FORMAT, **data, "threshold": float(threshold), "names": names,
            "roles": out, "merges": len(merges),
            "activities_considered": len(activities),
            "method": ("agglomerative grouping of resources by the Pearson correlation of their activity counts; "
                       "groups merge while their summed profiles correlate at or above the threshold"),
            "privacy": {"pseudonymous_by_default": True, "per_person_durations_or_speed_computed": False,
                        "ranking_or_performance_scoring": False},
            "interpretation": ("Roles group resources that performed a similar mix of activities in this log. They "
                               "describe recorded work, not job titles, responsibilities or how well anyone worked.")}


__all__ = ["roles", "FORMAT"]
