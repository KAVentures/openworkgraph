"""Strict two-sided tab-focus evidence, including bounded concurrent tabs.

Unreferenced events in a multiplexed tab are not assigned by recency. Only
matching exact-record anchors on the *same* scoped tab can admit intervening
unreferenced steps. Explicitly different, fully scoped browser tabs may be
interleaved; they remain separate work and never provide a positive link.
Unknown or conflicting intervening evidence causes abstention.
"""
from __future__ import annotations

from typing import Any

_MAX_LOOSE_SPAN = 8
_MAX_FOREIGN_TAB_SPAN = 16
_MAX_SCAN_SPAN = _MAX_LOOSE_SPAN + _MAX_FOREIGN_TAB_SPAN + 1


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


def _foreign_tab(row: dict[str, Any], anchor: dict[str, Any]) -> bool:
    """An independently named other browser tab, never a membership witness."""
    return (bool(row["tab"]) and row["tab"] != anchor["tab"]
            and row["scope"] == anchor["scope"] and _complete_scope(row["scope"])
            and row["app"] == anchor["app"] and row["source"] == anchor["source"]
            and not row["reference_tainted"]
            and not row["ephemeral_claim_tainted"]
            and not row["action_claim_malformed"]
            and not row["transfer"] and row["action"] not in ("copy", "paste"))


def _same_anchor(row: dict[str, Any], left: dict[str, Any]) -> bool:
    return (_anchor(row) and row["scope"] == left["scope"]
            and row["tab"] == left["tab"] and row["app"] == left["app"]
            and row["source"] == left["source"] and row["ref"] == left["ref"]
            and row["provider"] == left["provider"] and row["kind"] == left["kind"])


def bracketed_multiplexed_tab_focus(
    ordered_rows: list[dict[str, Any]],
    allowed_multiplexed_tabs: set[tuple[tuple[str, str, str, str], str]],
) -> list[tuple[str, str, str, str]]:
    """Return (anchor ID, loose ID, exact ref, evidence basis) proposals.

    Caller must veto competing union components before admitting any edge.
    A -> loose... -> A on a multiplexed tab is sufficient when timestamps
    are strictly ordered and all intervening observations are either safe
    loose steps on that tab or explicitly distinct same-scope browser tabs.
    Foreign-tab observations never gain membership. Same-tab competing refs,
    tabless/unknown events, scope/app/source changes, ties, clipboard actions
    on foreign tabs and unbounded spans cause abstention. Time alone never
    provides a link. Complexity is O(n * bounded_scan_span).
    """
    proposals: list[tuple[str, str, str, str]] = []
    count = len(ordered_rows)
    for index, left in enumerate(ordered_rows):
        if not _anchor(left) or (left["scope"], left["tab"]) not in allowed_multiplexed_tabs:
            continue
        loose_ids: list[str] = []
        foreign_count = 0
        previous = left
        for next_index in range(index + 1, min(count, index + _MAX_SCAN_SPAN + 1)):
            row = ordered_rows[next_index]
            # Equal-time events have no observed ordering even when event IDs sort.
            if previous["observed_at"] >= row["observed_at"]:
                break
            previous = row
            if _loose(row, left):
                if len(loose_ids) >= _MAX_LOOSE_SPAN:
                    break
                loose_ids.append(row["event_id"])
                continue
            if _foreign_tab(row, left):
                foreign_count += 1
                if foreign_count > _MAX_FOREIGN_TAB_SPAN:
                    break
                continue
            if loose_ids and _same_anchor(row, left):
                # A concurrent-tab focus is weaker than a globally consecutive
                # span, and an explicit transfer at either anchor makes the
                # interleaved attribution ambiguous.
                if foreign_count and (left["transfer"] or row["transfer"]):
                    break
                basis = ("same_tab_interleaved_bracketed_record" if foreign_count
                         else "same_tab_bracketed_record")
                proposals.extend((left["event_id"], loose_id, left["ref"], basis)
                                 for loose_id in loose_ids)
            break
    return proposals


__all__ = ["bracketed_multiplexed_tab_focus"]
