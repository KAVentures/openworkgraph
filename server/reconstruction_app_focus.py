"""Conservative two-sided focus recovery for tabless desktop applications.

This module proposes membership edges only when a complete capture scope has
two independently observed, strictly ordered exact-record anchors on the same
known application and source. It does not use time proximity as positive
evidence, inspect screen text, or create cross-record relationships.
"""
from __future__ import annotations

from typing import Any

_MAX_LOOSE_SPAN = 8
_MAX_FOREIGN_APP_SPAN = 16
_MAX_SCAN_SPAN = _MAX_LOOSE_SPAN + _MAX_FOREIGN_APP_SPAN + 1
_UNKNOWN = ("", "unknown", "other", "other app", "unknown app")


def _complete_scope(scope: Any) -> bool:
    return (isinstance(scope, tuple) and len(scope) == 4
            and all(isinstance(value, str) and bool(value) for value in scope))


def _trusted(row: dict[str, Any]) -> bool:
    return (not row["reference_tainted"]
            and not row["ephemeral_claim_tainted"]
            and not row["action_claim_malformed"]
            and not row["transfer"]
            and row["action"] not in ("copy", "paste"))


def _anchor(row: dict[str, Any]) -> bool:
    return (bool(row["ref"]) and not row["tab"] and _trusted(row)
            and row["app"] not in _UNKNOWN
            and row["source"] not in _UNKNOWN
            and _complete_scope(row["scope"]))


def _loose(row: dict[str, Any], left: dict[str, Any]) -> bool:
    return (not row["ref"] and not row["tab"] and _trusted(row)
            and row["scope"] == left["scope"]
            and row["app"] == left["app"]
            and row["source"] == left["source"])


def _same_anchor(row: dict[str, Any], left: dict[str, Any]) -> bool:
    return (_anchor(row) and row["scope"] == left["scope"]
            and row["app"] == left["app"] and row["source"] == left["source"]
            and row["ref"] == left["ref"]
            and row["provider"] == left["provider"]
            and row["kind"] == left["kind"])


def bracketed_tabless_app_focus(
    ordered_rows: list[dict[str, Any]],
    pre_tab_root: dict[str, str],
) -> list[tuple[str, str, str, str]]:
    """Return (anchor event, loose event, exact ref, evidence basis) proposals.

    A -> loose tabless desktop actions -> A is a positive two-anchor witness.
    B in a different named app may appear between those anchors only as an
    independently evidenced *negative* interruption: it never joins A.
    Same-app B, unknown or malformed observations, ties, changed scope,
    clipboard actions and unbounded spans veto the entire bracket.
    The caller must additionally reject competing post-transfer components.
    The scan is O(n * 25), independent of long idle gaps.
    """
    proposals: list[tuple[str, str, str, str]] = []
    count = len(ordered_rows)
    for index, left in enumerate(ordered_rows):
        if not _anchor(left):
            continue
        loose_ids: list[str] = []
        foreign_count = 0
        previous = left
        for next_index in range(index + 1, min(count, index + _MAX_SCAN_SPAN + 1)):
            row = ordered_rows[next_index]
            # Unrelated fully identified capture scopes must neither supply
            # positive evidence nor veto a valid same-scope bracket.
            if (_complete_scope(row["scope"]) and
                    row["scope"] != left["scope"]):
                continue
            # The event-ID tie breaker is not an observed order.
            if previous["observed_at"] >= row["observed_at"]:
                break
            previous = row
            if _loose(row, left):
                if len(loose_ids) >= _MAX_LOOSE_SPAN:
                    break
                loose_ids.append(row["event_id"])
                continue
            if (_trusted(row) and bool(row["ref"])
                    and row["scope"] == left["scope"]
                    and _complete_scope(row["scope"])
                    and row["app"] not in _UNKNOWN
                    and row["app"] != left["app"]
                    and row["source"] not in _UNKNOWN
                    and pre_tab_root.get(row["event_id"])
                    != pre_tab_root.get(left["event_id"])):
                foreign_count += 1
                if foreign_count > _MAX_FOREIGN_APP_SPAN:
                    break
                continue
            if loose_ids and _same_anchor(row, left):
                basis = ("same_app_cross_app_bracketed_record" if foreign_count
                         else "same_app_bracketed_record")
                proposals.extend(
                    (left["event_id"], loose_id, left["ref"], basis)
                    for loose_id in loose_ids
                )
            break
    return proposals


__all__ = ["bracketed_tabless_app_focus"]
