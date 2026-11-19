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

from shared.event_log_formats import RESOURCE_PRESENCE_WHITESPACE

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_roles.v1"
MAX_RESOURCES = 200
MAX_ACTIVITIES = 200
DEFAULT_THRESHOLD = 0.65
TOP_ACTIVITIES = 8
_NAMES = {"pseudonymous", "shown"}


def _moments(counts: Counter, dimensions: int) -> tuple[int, int]:
    total = sum(counts.values())
    variance = dimensions * sum(value * value for value in counts.values()) - total * total
    return total, variance


def _pearson(a: Counter, b: Counter, dimensions: int,
             moments_a: tuple[int, int], moments_b: tuple[int, int]) -> float:
    if dimensions < 2:
        return 1.0 if a and b else 0.0
    total_a, variance_a = moments_a
    total_b, variance_b = moments_b
    if not variance_a or not variance_b:
        return 1.0 if variance_a == variance_b == 0 else 0.0
    # Missing activity counts are zero, so only common activities contribute
    # to the dot product. Integer moments avoid subtracting rounded means.
    if len(a) > len(b):
        a, b = b, a
    dot = sum(count * b.get(activity, 0) for activity, count in a.items())
    return (dimensions * dot - total_a * total_b) / math.sqrt(variance_a * variance_b)


def roles(log_id: str, case_type: str | None = None, *, threshold: float = DEFAULT_THRESHOLD,
          names: str = "pseudonymous", cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if names not in _NAMES:
        raise EventLogWorkspaceError("names must be pseudonymous or shown")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 < threshold < 1.0:
        raise EventLogWorkspaceError("threshold must be a correlation above 0 and below 1")

    def run(conn):
        conn.execute("""CREATE TEMP TABLE role_events AS
            SELECT resource, activity FROM ce WHERE trim(resource, ?) <> ''""",
                     [RESOURCE_PRESENCE_WHITESPACE])
        total = conn.execute("SELECT count(*) FROM ce").fetchone()[0]
        with_resource, resource_count, activity_count = conn.execute("""SELECT count(*),
            count(DISTINCT resource), count(DISTINCT activity) FROM role_events""").fetchone()
        conn.execute("""CREATE TEMP TABLE role_resources AS SELECT resource, count(*) AS n
            FROM role_events GROUP BY resource ORDER BY n DESC, resource LIMIT ?""", [MAX_RESOURCES])
        conn.execute("""CREATE TEMP TABLE role_activities AS SELECT activity, count(*) AS n,
            count(DISTINCT resource) AS performers FROM role_events
            GROUP BY activity ORDER BY n DESC, activity LIMIT ?""", [MAX_ACTIVITIES])
        ranked = conn.execute("SELECT resource FROM role_resources ORDER BY n DESC, resource").fetchall()
        activities = conn.execute("SELECT activity, performers FROM role_activities ORDER BY n DESC, activity").fetchall()
        cells = conn.execute("""SELECT e.resource, e.activity, count(*)
            FROM role_events e SEMI JOIN role_resources r USING (resource)
            SEMI JOIN role_activities a USING (activity) GROUP BY 1, 2""").fetchall()
        return {"ranked": ranked, "activities": activities, "cells": cells,
                "coverage": {"events": total, "events_with_resource": with_resource,
                             "resources": resource_count, "activities": activity_count,
                             "resources_truncated": resource_count > MAX_RESOURCES,
                             "activities_truncated": activity_count > MAX_ACTIVITIES}}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    ranked = data.pop("ranked")
    activities = dict(data.pop("activities"))
    dimensions = len(activities)
    label = {name: (name if names == "shown" else f"Resource {i:02d}") for i, (name,) in enumerate(ranked, start=1)}
    profile: dict[str, Counter] = {name: Counter() for name in label}
    included_performers: Counter = Counter()
    represented_events = 0
    for resource, activity, count in data.pop("cells"):
        profile[resource][activity] = count
        included_performers[activity] += 1
        represented_events += count
    groups = {i: ([name], profile[name]) for i, (name,) in enumerate(ranked) if profile[name]}
    data["coverage"].update(resources_represented=len(groups), activities_represented=len(included_performers),
                            events_represented=represented_events)
    # A merge changes only the correlations involving its new group. Group
    # IDs follow the original ordering so equal-correlation ties stay stable.
    scores = {}
    moments = {group: _moments(counts, dimensions) for group, (_, counts) in groups.items()}
    next_group = len(ranked)
    merges = 0
    while len(groups) > 1:
        best, pair = threshold, None
        group_ids = list(groups)
        for pos, left in enumerate(group_ids):
            for right in group_ids[pos + 1:]:
                key = (left, right)
                if key not in scores:
                    scores[key] = _pearson(groups[left][1], groups[right][1], dimensions,
                                           moments[left], moments[right])
                score = scores[key]
                if score >= best + (0 if pair is None else 1e-12):
                    best, pair = score, key
        if pair is None:
            break
        left, right = pair
        members_a, counts_a = groups.pop(left)
        members_b, counts_b = groups.pop(right)
        merged_counts = counts_a + counts_b
        groups[next_group] = (members_a + members_b, merged_counts)
        moments[next_group] = _moments(merged_counts, dimensions)
        next_group += 1
        merges += 1
    groups = list(groups.values())
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
            "exclusive_activities": sorted(a for a in counts if performers[a] == {index}
                                           and included_performers[a] == activities[a]),
        })
    return {"format": FORMAT, **data, "threshold": float(threshold), "names": names,
            "roles": out, "merges": merges,
            "activities_considered": len(activities),
            "method": ("agglomerative grouping of resources by the Pearson correlation of their activity counts; "
                       "groups merge while their summed profiles correlate at or above the threshold"),
            "privacy": {"pseudonymous_by_default": True, "per_person_durations_or_speed_computed": False,
                        "ranking_or_performance_scoring": False},
            "interpretation": ("Roles group resources that performed a similar mix of activities in this log. They "
                               "describe recorded work, not job titles, responsibilities or how well anyone worked.")}


__all__ = ["roles", "FORMAT"]
