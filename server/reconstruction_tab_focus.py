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
    *,
    cross_app_components: dict[str, str] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Return (anchor ID, loose ID, exact ref, evidence basis) proposals.

    Caller must veto competing union components before admitting any edge.
    A -> loose... -> A on a multiplexed tab is sufficient when timestamps
    are strictly ordered and all intervening observations are either safe
    loose steps on that tab or explicitly distinct same-scope browser tabs.
    With cross_app_components, independently linked exact records in a named
    different application are also permitted negative interruptions, including
    tabless desktop observations. They never gain membership themselves.
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
        cross_app_count = 0
        unreferenced_app_count = 0
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
            # Only the single-record-tab caller enables cross-app interruptions.
            # An independently linked B in Terminal can interrupt Browser A,
            # but cannot become positive evidence for A or join A by proximity.
            if (cross_app_components is not None
                    and bool(row["ref"])
                    and row["app"] not in ("", "unknown", "other")
                    and row["app"] != left["app"]
                    and row["source"] not in ("", "unknown")
                    and row["scope"] == left["scope"]
                    and _complete_scope(row["scope"])
                    and not row["reference_tainted"]
                    and not row["ephemeral_claim_tainted"]
                    and not row["action_claim_malformed"]
                    and not row["transfer"]
                    and row["action"] not in ("copy", "paste")
                    and cross_app_components.get(row["event_id"])
                    != cross_app_components.get(left["event_id"])):
                foreign_count += 1
                cross_app_count += 1
                if foreign_count > _MAX_FOREIGN_TAB_SPAN:
                    break
                continue
            # A visible switch to another *named* app is an observed
            # interruption even when its business record is unknown. It is
            # never a positive case witness. Require the same complete scope,
            # trusted capture and a subsequent exact A anchor before using
            # either side's loose browser observations.
            if (cross_app_components is not None
                    and not row["ref"] and not row["tab"]
                    and row["app"] not in ("", "unknown", "other", "other app", "unknown app")
                    and row["app"] != left["app"]
                    and row["source"] not in ("", "unknown")
                    and row["scope"] == left["scope"]
                    and _complete_scope(row["scope"])
                    and not row["reference_tainted"]
                    and not row["ephemeral_claim_tainted"]
                    and not row["action_claim_malformed"]
                    and not row["transfer"]
                    and row["action"] not in ("copy", "paste")):
                foreign_count += 1
                unreferenced_app_count += 1
                if foreign_count > _MAX_FOREIGN_TAB_SPAN:
                    break
                continue
            if loose_ids and _same_anchor(row, left):
                # A concurrent-tab focus is weaker than a globally consecutive
                # span, and an explicit transfer at either anchor makes the
                # interleaved attribution ambiguous.
                if foreign_count and (left["transfer"] or row["transfer"]):
                    break
                basis = ("same_tab_unreferenced_app_interleaved_bracketed_record"
                         if unreferenced_app_count else
                         "same_tab_cross_app_interleaved_bracketed_record" if cross_app_count
                         else "same_tab_interleaved_bracketed_record" if foreign_count
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

    A tab's sole observed record is not sufficient when another independent
    exact record from the same complete capture scope appears in a different
    explicitly named tab OR a different named application (including a tabless
    desktop source) between the loose step and an adjacent same-tab anchor.
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
    all_app_positions: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    same_app_positions: dict[tuple[tuple[str, str, str, str], str], list[int]] = defaultdict(list)
    component_app_positions: dict[tuple[tuple[str, str, str, str], str], list[int]] = defaultdict(list)
    component_same_app_positions: dict[
        tuple[tuple[str, str, str, str], str, str], list[int]
    ] = defaultdict(list)
    # Unreferenced, named cross-app activity is negative focus evidence,
    # never a claim about which business record was active. Keep a separate
    # indexed stream so a single-record tab cannot absorb post-switch clicks.
    unreferenced_app_positions: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    same_unreferenced_app_positions: dict[
        tuple[tuple[str, str, str, str], str], list[int]
    ] = defaultdict(list)
    tied_unreferenced_apps: dict[
        tuple[tuple[str, str, str, str], str], set[str]
    ] = defaultdict(set)
    tied_components: dict[
        tuple[tuple[str, str, str, str], str], set[tuple[str, str, str]]
    ] = defaultdict(set)
    for position, row in enumerate(ordered_rows):
        scope = row["scope"]
        if (not row["ref"] and not row["tab"] and not row["transfer"]
                and _complete_scope(scope)
                and row["app"] not in ("", "unknown", "other", "other app", "unknown app")
                and row["source"] not in ("", "unknown")
                and not row["reference_tainted"]
                and not row["ephemeral_claim_tainted"]
                and not row["action_claim_malformed"]
                and row["action"] not in ("copy", "paste")):
            unreferenced_app_positions[scope].append(position)
            same_unreferenced_app_positions[(scope, row["app"])].append(position)
            tied_unreferenced_apps[(scope, row["observed_at"])].add(row["app"])
        if not (row["ref"] and not row["reference_tainted"]
                and _complete_scope(scope)):
            continue
        component = pre_tab_root[row["event_id"]]
        app = row["app"]
        if row["tab"]:
            all_record_positions[scope].append(position)
            component_positions[(scope, component)].append(position)
        if app not in ("", "unknown", "other"):
            all_app_positions[scope].append(position)
            same_app_positions[(scope, app)].append(position)
            component_app_positions[(scope, component)].append(position)
            component_same_app_positions[(scope, component, app)].append(position)
        if row["tab"] or app not in ("", "unknown", "other"):
            tied_components[(scope, row["observed_at"])].add(
                (component, row["tab"], app)
            )

    def between(positions: list[int], left: int, right: int) -> int:
        return bisect_left(positions, right) - bisect_right(positions, left)

    def has_foreign(scope: tuple[str, str, str, str], component: str,
                    app: str, left: int, right: int) -> bool:
        # Existing explicit other-tab boundary (a single-record tab cannot
        # contain an independently named second record itself).
        if (between(all_record_positions[scope], left, right)
                > between(component_positions[(scope, component)], left, right)):
            return True
        # Cross-app exact records can be tabless. Subtract the same component
        # before treating a different application as an independent case.
        all_other_app = (between(all_app_positions[scope], left, right)
                         - between(same_app_positions[(scope, app)], left, right))
        own_other_app = (between(component_app_positions[(scope, component)], left, right)
                         - between(component_same_app_positions[(scope, component, app)],
                                   left, right))
        if all_other_app > own_other_app:
            return True
        # Unknown-record activity in another named app is still an observed
        # focus interruption. It does not establish an independent B case.
        return (between(unreferenced_app_positions[scope], left, right)
                > between(same_unreferenced_app_positions[(scope, app)], left, right))

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
        anchor_app = anchor_rows[0]["app"]
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
                other_app != anchor_app
                for p in (left, position, right) if p is not None
                for other_app in tied_unreferenced_apps.get(
                    (scope, ordered_rows[p]["observed_at"]), set()
                )
            ) or any(
                component != anchor_component
                and ((tab and tab != row["tab"])
                     or (app not in ("", "unknown", "other") and app != anchor_app))
                for p in (left, position, right) if p is not None
                for component, tab, app in tied_components.get(
                    (scope, ordered_rows[p]["observed_at"]), set()
                )
            )
            if (tied_foreign or
                    (left is not None and
                     has_foreign(scope, anchor_component, anchor_app, left, position)) or
                    (right is not None and
                     has_foreign(scope, anchor_component, anchor_app, position, right))):
                withheld.add(row["event_id"])
    return withheld

__all__ = ["bracketed_multiplexed_tab_focus", "single_record_tab_foreign_case_boundaries"]
