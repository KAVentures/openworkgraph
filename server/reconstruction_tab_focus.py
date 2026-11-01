"""Strict, observed two-sided tab-focus evidence in a multiplexed browser tab.

An unreferenced event is not automatically part of the most recent record.
Only a bounded *globally consecutive* A -> loose... -> A observation span
with the same tab, application, source and complete capture scope can provide
an additional membership witness. Callers must still veto conflicting union
components before admitting a returned edge.
"""
from __future__ import annotations

from typing import Any

_MAX_LOOSE_SPAN = 8


def _complete_scope(scope: Any) -> bool:
    return (isinstance(scope, tuple) and len(scope) == 4
            and all(isinstance(value, str) and bool(value) for value in scope))


def _anchor(row: dict[str, Any]) -> bool:
    return (bool(row["ref"]) and bool(row["tab"]) and row["app"] not in ("", "unknown", "other")
            and row["source"] != "unknown" and _complete_scope(row["scope"])
            and not row["reference_tainted"]
            and not row["ephemeral_claim_tainted"]
            and not row["action_claim_malformed"])


def _loose(row: dict[str, Any], anchor: dict[str, Any]) -> bool:
    return (not row["ref"] and not row["transfer"]
            and row["tab"] == anchor["tab"] and row["app"] == anchor["app"]
            and row["source"] == anchor["source"] and row["scope"] == anchor["scope"]
            and not row["reference_tainted"]
            and not row["ephemeral_claim_tainted"]
            and not row["action_claim_malformed"])


def bracketed_multiplexed_tab_focus(
    ordered_rows: list[dict[str, Any]],
    allowed_multiplexed_tabs: set[tuple[tuple[str, str, str, str], str]],
) -> list[tuple[str, str, str]]:
    """Return (anchor event ID, loose event ID, exact ref) *proposals*.

    This never merges records itself. A tab must actually contain multiple
    referenced records in the same exact capture scope. Both bracketing
    anchors must show the same exact record and the intervening observations
    must be globally adjacent, unreferenced and untainted. Timestamp ties,
    unrelated app activity, a competing record, incomplete scope and
    unbounded spans all abstain. No elapsed-time threshold creates evidence.
    """
    proposals: list[tuple[str, str, str]] = []
    count = len(ordered_rows)
    for index, left in enumerate(ordered_rows):
        if not _anchor(left) or (left["scope"], left["tab"]) not in allowed_multiplexed_tabs:
            continue
        next_index = index + 1
        while (next_index < count and next_index - index <= _MAX_LOOSE_SPAN
               and _loose(ordered_rows[next_index], left)
               and ordered_rows[next_index - 1]["observed_at"]
                   < ordered_rows[next_index]["observed_at"]):
            next_index += 1
        if next_index == index + 1 or next_index >= count:
            continue
        right = ordered_rows[next_index]
        if (not _anchor(right) or right["scope"] != left["scope"]
                or right["tab"] != left["tab"]
                or right["app"] != left["app"]
                or right["source"] != left["source"]
                or right["ref"] != left["ref"]
                or right["provider"] != left["provider"]
                or right["kind"] != left["kind"]
                or ordered_rows[next_index - 1]["observed_at"] >= right["observed_at"]):
            continue
        proposals.extend((left["event_id"], ordered_rows[at]["event_id"], left["ref"])
                         for at in range(index + 1, next_index))
    return proposals


__all__ = ["bracketed_multiplexed_tab_focus"]
