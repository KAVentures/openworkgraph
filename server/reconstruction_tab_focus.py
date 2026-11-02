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



def single_record_tab_foreign_case_boundaries(
    ordered_rows: list[dict[str, Any]],
    by_tab: dict[tuple[tuple[str, str, str, str], str], list[dict[str, Any]]],
    pre_tab_root: dict[str, str],
) -> set[str]:
    """Loose single-record-tab IDs needing independent two-sided focus evidence.

    A tab's sole observed record is not sufficient when another exact record
    from the same complete capture scope in an explicitly different tab
    appears between the loose step and either adjacent same-tab record anchor.
    Only independently linked record components count: a legitimate observed
    clipboard bridge between A and B must not become a false competing case.
    The other record is a negative boundary, never a positive link. No time
    threshold or source text is used. Indexed complexity is O(n log n).
    """
    from bisect import bisect_left, bisect_right
    from collections import defaultdict

    positions = {row["event_id"]: i for i, row in enumerate(ordered_rows)}
    all_record_positions: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    component_positions: dict[
        tuple[tuple[str, str, str, str], str], list[int]
    ] = defaultdict(list)
    tied_components: dict[tuple[tuple[str, str, str, str], str], set[str]] = defaultdict(set)
    for position, row in enumerate(ordered_rows):
        scope = row["scope"]
        if (row["ref"] and row["tab"] and not row["reference_tainted"] and
                _complete_scope(scope)):
            component = pre_tab_root[row["event_id"]]
            all_record_positions[scope].append(position)
            component_positions[(scope, component)].append(position)
            tied_components[(scope, row["observed_at"])].add(component)

    def has_foreign(scope: tuple[str, str, str, str], component: str,
                    left: int, right: int) -> bool:
        all_positions = all_record_positions[scope]
        same_positions = component_positions[(scope, component)]
        all_between = bisect_left(all_positions, right) - bisect_right(all_positions, left)
        same_between = bisect_left(same_positions, right) - bisect_right(same_positions, left)
        return all_between > same_between

    withheld: set[str] = set()
    for (scope, _tab), tab_rows in by_tab.items():
        if not _complete_scope(scope):
            continue
        refs = {row["ref"] for row in tab_rows if row["ref"]}
        if len(refs) != 1:
            continue
        sole_ref = next(iter(refs))
        anchor_rows = [row for row in tab_rows if row["ref"] == sole_ref]
        anchor_component = pre_tab_root[anchor_rows[0]["event_id"]]
        anchors = [positions[row["event_id"]] for row in anchor_rows]
        for row in tab_rows:
            if row["ref"]:
                continue
            position = positions[row["event_id"]]
            index = bisect_left(anchors, position)
            left = anchors[index - 1] if index else None
            right = anchors[index] if index < len(anchors) else None
            # An event-ID sort is not evidence of which case was active when
            # two independently named records share an observation instant.
            # Treat foreign record ties at either anchor or the loose step as
            # an unresolved interruption regardless of sort order.
            tied_foreign = any(
                tied_components.get((scope, ordered_rows[p]["observed_at"]), set())
                - {anchor_component}
                for p in (left, position, right) if p is not None
            )
            if (tied_foreign or
                    (left is not None and
                     has_foreign(scope, anchor_component, left, position)) or
                    (right is not None and
                     has_foreign(scope, anchor_component, position, right))):
                withheld.add(row["event_id"])
    return withheld

__all__ = ["bracketed_multiplexed_tab_focus", "single_record_tab_foreign_case_boundaries"]
