"""A differentially private process map: share how work flows without revealing any single case.

Privacy-preserving process mining (Mannhardt et al. 2019; Elkoumy et al., "Libra" and "SaCoFa";
PM4Py's privacy module) publishes the directly-follows graph with calibrated noise so that the
result barely changes whether or not any one case is in the log. This module does that for a saved
event log:

1. **Bounded contribution.** Each case contributes at most ``k`` directly-follows pairs and the
   matching ``k + 1`` activity occurrences. Longer cases keep a pseudo-random subset of their
   pairs and occurrences, drawn independently for this export.
2. **Laplace noise.** Adding or removing one case changes the pair counts by at most ``k`` in total
   (L1 sensitivity ``k``) and the activity counts by at most ``k + 1``. Half of the privacy budget
   ε goes to each: pair counts get Laplace noise of scale ``2k/ε`` and activity counts
   ``2(k+1)/ε``. Start and end markers are pairs ``(start) → A`` and ``B → (end)``, counted within
   the same ``k``.
3. **Post-processing.** Noise is added to every possible pair over the log's activity labels,
   observed or not, so whether an edge appears does not reveal that a case took it. Noisy counts
   below the release threshold are left out and the rest are rounded. The default threshold keeps
   the expected number of released pairs that no case took at ``FALSE_EDGES`` (0.05), and likewise
   for activities. Post-processing cannot weaken the guarantee.

The released map has activities, edges and approximate counts only. It has no case identifiers,
timestamps, resources, attributes or durations. Activity *names* are released as they are; they
come from the log's label set and are not protected by the noise, so a log whose activity names
themselves identify someone needs renaming first. Every export draws fresh noise, and repeated
exports of the same log add up: two exports at ε each spend 2ε in total. The guarantee is
ε-differential privacy with respect to adding or removing one case.
"""
from __future__ import annotations

import math
import secrets
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_private_map.v1"
START, END = "(start)", "(end)"
MIN_EPSILON, MAX_EPSILON = 0.01, 10.0
MAX_K = 100
DEFAULT_EPSILON = 1.0
DEFAULT_K = 10
MAX_ACTIVITIES = 500
FALSE_EDGES = 0.05


def _laplace(rng: secrets.SystemRandom, scale: float) -> float:
    u = rng.random() - 0.5
    while u == -0.5:
        u = rng.random() - 0.5
    return -scale * math.copysign(1.0, u) * math.log(1 - 2 * abs(u))


def _check(epsilon: Any, k: Any, threshold: Any) -> tuple[float, int, float | None]:
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)) or not MIN_EPSILON <= epsilon <= MAX_EPSILON:
        raise EventLogWorkspaceError(f"epsilon must be a number from {MIN_EPSILON} to {MAX_EPSILON}")
    if type(k) is not int or not 1 <= k <= MAX_K:
        raise EventLogWorkspaceError(f"max_transitions_per_case must be an integer from 1 to {MAX_K}")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                  or not 0 <= threshold <= 1e9):
        raise EventLogWorkspaceError("threshold must be a non-negative count")
    return float(epsilon), k, (float(threshold) if threshold is not None else None)


def private_map(log_id: str, case_type: str | None = None, *, epsilon: float = DEFAULT_EPSILON,
                max_transitions_per_case: int = DEFAULT_K, threshold: float | None = None,
                _rng: Any = None) -> dict[str, Any]:
    epsilon, k, threshold = _check(epsilon, max_transitions_per_case, threshold)
    rng = _rng or secrets.SystemRandom()

    def run(conn):
        paths = conn.execute("""SELECT list(activity ORDER BY ts, event_id) FROM ce GROUP BY case_id""").fetchall()
        labels = [a for (a,) in conn.execute("""SELECT DISTINCT activity FROM ce ORDER BY 1 LIMIT ?""",
                                            [MAX_ACTIVITIES + 1]).fetchall()]
        return {"paths": paths, "labels": labels}

    data = workspace._query(log_id, case_type, run)
    paths, labels = data.pop("paths"), data.pop("labels")
    if len(labels) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"the log has more than {MAX_ACTIVITIES} activities; filter or map them first")
    pairs, occurrences = Counter(), Counter()
    clipped = 0
    for (path,) in paths:
        steps = [START, *path, END]
        transitions = list(zip(steps, steps[1:]))
        if len(transitions) > k:
            clipped += 1
            # A fresh random subset per export, so which pairs a long case keeps follows no fixed pattern.
            transitions = rng.sample(transitions, k)
        pairs.update(transitions)
        kept = list(path)
        if len(kept) > k + 1:
            kept = rng.sample(kept, k + 1)
        occurrences.update(kept)
    pair_scale = 2 * k / epsilon
    activity_scale = 2 * (k + 1) / epsilon
    nodes = [START, *labels, END]
    candidate_pairs = (len(nodes) - 1) ** 2 - 1
    # Default cut-off: an unobserved pair passes with probability exp(-t/b)/2, so with
    # t = b·ln(N / (2·FALSE_EDGES)) the expected number of released pairs nobody took is FALSE_EDGES.
    pair_cut = threshold if threshold is not None else pair_scale * math.log(max(candidate_pairs, 1) / (2 * FALSE_EDGES))
    activity_cut = (threshold if threshold is not None
                    else activity_scale * math.log(max(len(labels), 1) / (2 * FALSE_EDGES)))
    pair_cut, activity_cut = max(pair_cut, 0.0), max(activity_cut, 0.0)
    # Noise goes on every possible pair over the public label set, observed or not, so that whether
    # an edge appears does not reveal that some case took it.
    edges = []
    for a in nodes:
        if a == END:
            continue
        for b in nodes:
            if b == START or (a == START and b == END):
                continue
            noisy = pairs.get((a, b), 0) + _laplace(rng, pair_scale)
            if noisy >= pair_cut:
                edges.append({"from": a, "to": b, "count": max(0, round(noisy))})
    activities = []
    for a in labels:
        noisy = occurrences.get(a, 0) + _laplace(rng, activity_scale)
        if noisy >= activity_cut:
            activities.append({"activity": a, "count": max(0, round(noisy))})
    edges.sort(key=lambda e: (-e["count"], e["from"], e["to"]))
    activities.sort(key=lambda a: (-a["count"], a["activity"]))
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacy": {
            "guarantee": "epsilon-differential privacy for adding or removing one case",
            "epsilon": epsilon, "epsilon_split": {"edges": epsilon / 2, "activities": epsilon / 2},
            "max_transitions_per_case": k,
            "noise": {"mechanism": "Laplace", "edge_scale": round(pair_scale, 4),
                      "activity_scale": round(activity_scale, 4)},
            "release_threshold": {"edges": round(pair_cut, 4), "activities": round(activity_cut, 4),
                                  "expected_released_pairs_nobody_took": FALSE_EDGES if threshold is None else None},
            "activity_names_protected": False,
            "repeated_exports_add_up": True,
        },
        "activities": activities, "edges": edges,
        "start": START, "end": END,
        "note": ("Counts are approximate by design and some rare activities and paths are left out. Activity names "
                 "are released as they are. Each export draws new noise and spends its epsilon again; keep track "
                 "of what you share. No case, time, resource or attribute is included."),
        # Exact figures for the owner choosing k; not differentially private, never part of a shared export.
        "owner_only_not_private": {"cases": len(paths), "cases_clipped": clipped},
    }


def shareable(result: dict[str, Any]) -> dict[str, Any]:
    """The export as released: without the exact (non-private) diagnostics."""
    return {key: value for key, value in result.items() if key != "owner_only_not_private"}


__all__ = ["private_map", "shareable", "FORMAT"]
