"""Evidence-only same-record actor participation for already linked work items.

This is an observed access/return timeline, not an inferred employee handoff.
It never adds work-item membership, verifies business state, reads text payloads,
or exposes organization/actor/device/session identifiers. A complete literal
capture scope and an already admitted exact source-record claim are mandatory.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Any

_MAX_CHANGES = 24


def actor_observation_index(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[int]]]:
    """Index admitted observations once; never match an unknown tenant."""
    positions: dict[str, int] = {}
    tenant_positions: dict[str, list[int]] = defaultdict(list)
    for position, row in enumerate(rows):
        positions[row["event_id"]] = position
        scope = row["scope"]
        if scope is not None and all(scope):
            tenant_positions[scope[0]].append(position)
    return positions, tenant_positions


def observed_same_record_actor_changes(
    member_rows: list[dict[str, Any]],
    *,
    position_index: dict[str, int],
    tenant_positions: dict[str, list[int]],
    max_changes: int = _MAX_CHANGES,
) -> dict[str, Any]:
    """Describe observed participant changes within each *same* exact record.

    Inputs are already admitted, UTC-ordered linker rows for one work item.
    Distinct source records connected by clipboard do not become actor
    handoffs. A missing capture identity or tied timestamp breaks ordering,
    and no unreferenced observation can become an actor endpoint.
    """
    max_changes = max(1, min(int(max_changes), _MAX_CHANGES))
    by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in member_rows:
        if row["ref"] and not row["reference_tainted"]:
            by_record[row["ref"]].append(row)

    # Labels are local to one item response, not stable employee or record IDs.
    participants: dict[tuple[str, str], str] = {}
    changes: list[dict[str, Any]] = []
    total = returns = tied = incomplete = 0
    for record_index, (_ref, record_rows) in enumerate(by_record.items(), start=1):
        previous: dict[str, Any] | None = None
        seen: set[tuple[str, str]] = set()
        for row in record_rows:
            scope = row["scope"]
            if scope is None or not all(scope):
                incomplete += 1
                previous = None
                seen.clear()
                continue
            actor = (scope[0], scope[1])
            if actor not in participants:
                participants[actor] = f"participant_{len(participants) + 1:02d}"
            if previous is not None:
                prior_scope = previous["scope"]
                if prior_scope[0] != scope[0]:
                    # Should be impossible after source-identity conflict
                    # suppression, but never bridge a tenant boundary.
                    previous = None
                    seen.clear()
                elif prior_scope[1] != scope[1]:
                    if previous["observed_at"] >= row["observed_at"]:
                        # Sorting by event ID is deterministic but does not
                        # establish a temporal actor ordering at a tied time.
                        tied += 1
                        previous = None
                        seen.clear()
                        continue
                    prior_position = position_index.get(previous["event_id"])
                    next_position = position_index.get(row["event_id"])
                    if prior_position is None or next_position is None or next_position <= prior_position:
                        previous = None
                        seen.clear()
                        continue
                    positions = tenant_positions.get(scope[0], [])
                    between = max(0, bisect_left(positions, next_position)
                                  - bisect_right(positions, prior_position))
                    is_return = actor in seen
                    total += 1
                    returns += int(is_return)
                    if len(changes) < max_changes:
                        changes.append({
                            "change_index": total,
                            "record_id": f"record_{record_index:02d}",
                            "from_participant_id": participants[(prior_scope[0], prior_scope[1])],
                            "to_participant_id": participants[actor],
                            "from_event_id": previous["event_id"],
                            "to_event_id": row["event_id"],
                            "from_observed_at": previous["observed_at"],
                            "to_observed_at": row["observed_at"],
                            "return_to_previously_observed_participant": is_return,
                            "intervening_same_tenant_observation_count": between,
                            "continuity_unverified": True,
                            "handoff_verified": False,
                        })
            seen.add(actor)
            previous = row

    return {
        "observed_same_record_participant_count": len(participants),
        "observed_same_record_actor_change_count": total,
        "observed_same_record_actor_return_count": returns,
        "observed_same_record_actor_changes": changes,
        "observed_same_record_actor_changes_truncated": total > len(changes),
        "same_time_cross_actor_order_suppressed": tied,
        "incomplete_actor_scope_observations_suppressed": incomplete,
        "actor_participation_is_observed_not_verified_handoff": True,
    }


__all__ = ["actor_observation_index", "observed_same_record_actor_changes"]
