"""Bounded source-record bridge evidence for already linked work items.

A bridge describes the exact *observed* signal used by the deterministic
linker, not a task handoff, business case, source-system relation or outcome.
The public projection omits raw record references, transfer tokens and capture
scope; record labels exist only within one returned work item.
"""
from __future__ import annotations

from typing import Any

_MAX_BRIDGES = 24


def directional_transfer_witness(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Find an ordered, literal, cross-record copy/paste pair in one scoped token.

    Caller supplies validated, UTC-sorted rows for a single scoped transfer.
    A generic action, a loose endpoint, a time gap or a same-record pair is
    never evidence of a directional cross-record transfer.
    """
    pending: dict[str, Any] | None = None
    for row in rows:
        ref = row["ref"]
        if row["action"] not in ("copy", "paste"):
            continue
        # A second, different source copy before the paste makes the token
        # ambiguous: choosing either one would invent the transfer's origin.
        # An explicit loose copy/paste is also not a trustworthy endpoint.
        if not ref or row["reference_tainted"] or row["scope"] is None:
            return None
        if row["action"] == "copy":
            if pending is not None and pending["ref"] != ref:
                return None
            if pending is None:
                pending = row
        elif pending is not None:
            if (pending["ref"] == ref or pending["scope"] != row["scope"]
                    or pending["observed_at"] >= row["observed_at"]):
                return None
            return pending["event_id"], row["event_id"]
    return None


def observed_record_link_evidence(
    member_rows: list[dict[str, Any]],
    membership_links: list[tuple[str, str, str]],
    *,
    max_bridges: int = _MAX_BRIDGES,
) -> dict[str, Any]:
    """Expose only actual union-tree cross-record witnesses, never new joins.

    Same-record links and loose-tab attachments do not become record bridges.
    A legacy generic shared transfer token can support an existing link but
    cannot be presented as a witnessed copy/paste direction.
    """
    max_bridges = max(1, min(int(max_bridges), _MAX_BRIDGES))
    by_id = {row["event_id"]: row for row in member_rows}
    labels: dict[tuple[str, str, str], str] = {}
    for row in member_rows:
        if row["ref"] and not row["reference_tainted"]:
            key = (row["ref"], row["provider"], row["kind"])
            if key not in labels:
                labels[key] = f"record_{len(labels) + 1:02d}"

    bridges: list[dict[str, Any]] = []
    total = directional_count = legacy_count = 0
    for source_id, target_id, basis in membership_links:
        if basis != "clipboard_transfer":
            continue
        source, target = by_id.get(source_id), by_id.get(target_id)
        if source is None or target is None:
            continue
        if (not source["ref"] or not target["ref"]
                or source["reference_tainted"] or target["reference_tainted"]
                or source["scope"] is None or source["scope"] != target["scope"]):
            continue
        source_key = (source["ref"], source["provider"], source["kind"])
        target_key = (target["ref"], target["provider"], target["kind"])
        if source_key == target_key:
            continue

        directional = (source["action"] == "copy" and target["action"] == "paste"
                       and source["observed_at"] < target["observed_at"])
        total += 1
        directional_count += int(directional)
        legacy_count += int(not directional)
        if len(bridges) < max_bridges:
            bridges.append({
                "bridge_index": total,
                "record_ids": sorted((labels[source_key], labels[target_key])),
                "basis": "clipboard_transfer",
                "evidence_class": "ordered_copy_paste" if directional else "legacy_shared_token",
                "direction_observed": directional,
                "witness_event_ids": [source_id, target_id],
                "witness_observed_at": [source["observed_at"], target["observed_at"]],
                "capture_scope_verified": True,
                "copy_record_id": labels[source_key] if directional else None,
                "paste_record_id": labels[target_key] if directional else None,
            })
    return {
        "observed_record_bridge_count": total,
        "observed_directional_bridge_count": directional_count,
        "observed_legacy_token_bridge_count": legacy_count,
        "observed_record_bridges_truncated": total > len(bridges),
        "observed_record_bridges": bridges,
    }


__all__ = ["directional_transfer_witness", "observed_record_link_evidence"]
