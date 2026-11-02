"""Read-only, evidence-anchored work-item switch and return timeline.

A switch is an observed sequence of distinct *already linked* items in the
same exact local capture scope, with literal source-record evidence at both
endpoints. It is not a task handoff, a verified business transition or a new
membership edge. Unknown intermediate observations remain explicitly unknown.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

_MAX_SWITCHES = 32


def observed_work_switches(
    rows: list[dict[str, Any]],
    item_of: dict[str, str],
    *,
    max_switches: int = _MAX_SWITCHES,
) -> dict[str, Any]:
    """Project a bounded timeline without returning any capture-scope values.

    `rows` are the production linker's validated, UTC-ordered observations.
    `item_of` contains only items actually returned to the caller; hidden,
    single-observation, conflicted or otherwise unassigned events cannot
    become switch endpoints. A time gap never creates a switch.
    """
    max_switches = max(1, min(int(max_switches), _MAX_SWITCHES))
    last_anchor: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    seen_items: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    unattributed: dict[tuple[str, str, str, str], int] = defaultdict(int)
    switches: list[dict[str, Any]] = []
    total = returns = uncertain = 0

    for row in rows:
        scope = row["scope"]
        if scope is None:
            continue
        event_id = row["event_id"]
        item_id = item_of.get(event_id)
        # An ambiguous tab/clipboard/app candidate or loose observation is
        # not independent evidence that a business record was being worked.
        if not (item_id and row["ref"] and not row["reference_tainted"]):
            if scope in last_anchor:
                unattributed[scope] += 1
            continue

        previous = last_anchor.get(scope)
        if previous is not None and previous[0] != item_id:
            prior_item, prior_id, prior_at = previous
            is_return = item_id in seen_items[scope]
            missing = unattributed[scope]
            total += 1
            returns += int(is_return)
            uncertain += int(missing > 0)
            if len(switches) < max_switches:
                switches.append({
                    "switch_index": total,
                    "from_item_id": prior_item,
                    "to_item_id": item_id,
                    "from_event_id": prior_id,
                    "to_event_id": event_id,
                    "from_observed_at": prior_at,
                    "to_observed_at": row["observed_at"],
                    "return_to_previously_observed_item": is_return,
                    "unattributed_observations_between": missing,
                    "continuity_unverified": missing > 0,
                })

        seen_items[scope].add(item_id)
        last_anchor[scope] = (item_id, event_id, row["observed_at"])
        unattributed[scope] = 0

    return {
        "observed_switch_count": total,
        "observed_return_count": returns,
        "observed_switches_with_unattributed_evidence_count": uncertain,
        "observed_switches_truncated": total > len(switches),
        "observed_switches": switches,
    }


__all__ = ["observed_work_switches"]
