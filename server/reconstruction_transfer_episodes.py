"""Conservative directional episodes for reused scoped clipboard tokens.

A token reused for several unrelated copy/paste operations must not turn all
observed records into one work item. This module recovers *only* independent,
fully witnessed copy -> paste pairs from a previously ambiguous 3+-record
token group. No clipboard content, app labels or wall-clock gap is consulted.
"""
from __future__ import annotations

from typing import Any

_MAX_EPISODES = 64


def disjoint_directional_transfer_episodes(
    ordered_rows: list[dict[str, Any]],
) -> list[tuple[str, str]] | None:
    """Return safe (copy_event_id, paste_event_id) pairs, else no authority.

    Caller must have validated event IDs, literal record identities, source
    claims, UTC timestamps and exact capture scope, and excluded tainted tokens.
    All rows share one exact scoped token and are sorted by (UTC time, event ID).

    An episode is exactly one observed copy followed by one later observed
    paste into a *different* source record. Every observation must belong to
    exactly one episode, and each record can occur in only one episode.
    Consequently, a reused token cannot bridge episodes by transitivity.
    Missing/loose, generic, interleaved, overlapping or excessive groups are
    rejected as a whole rather than selecting a plausible subset.
    """
    count = len(ordered_rows)
    if count < 4 or count % 2 or count > 2 * _MAX_EPISODES:
        return None
    # Unlike legacy two-record tokens, multi-episode recovery needs all four
    # independently observed capture-scope components. A shared *missing*
    # legacy scope cannot establish that a reused token belongs to one actor.
    scope = ordered_rows[0].get("scope")
    if (not isinstance(scope, tuple) or len(scope) != 4
            or any(not isinstance(part, str) or not part for part in scope)
            or any(row.get("scope") != scope for row in ordered_rows)):
        return None
    used_refs: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for index in range(0, count, 2):
        copy, paste = ordered_rows[index], ordered_rows[index + 1]
        if copy["action"] != "copy" or paste["action"] != "paste":
            return None
        source_ref, target_ref = copy["ref"], paste["ref"]
        if (not source_ref or not target_ref or source_ref == target_ref
                or source_ref in used_refs or target_ref in used_refs
                or copy["observed_at"] >= paste["observed_at"]):
            return None
        used_refs.add(source_ref)
        used_refs.add(target_ref)
        pairs.append((copy["event_id"], paste["event_id"]))
    return pairs


__all__ = ["disjoint_directional_transfer_episodes"]
