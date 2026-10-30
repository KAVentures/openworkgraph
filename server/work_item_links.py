"""Multi-signal work-item linking with explicit uncertainty (roadmap #326 A1).

Extends exact-record lanes: observations are merged into one linked work item
only by evidence that names the same thing, and every weaker signal is returned
as a separate, unmerged candidate link for the reader to judge.

Merged (each link is listed with its basis):
- ``same_record``: the same valid opaque resource reference;
- ``clipboard_transfer``: the same observed clipboard transfer id (copy -> paste),
  which may join two records;
- ``same_tab_single_record``: an unreferenced observation in a browser tab whose
  only observed record is R joins R.

Never merged, only reported as ``ambiguous`` candidate links:
- ``same_tab_multiplexed``: an unreferenced observation in a tab that showed
  several records;
- ``same_tab_unreferenced``: observations sharing a tab with no observed record
  (tabs are routinely reused for unrelated work);
- ``surface_continuity``: an unreferenced, tab-less observation directly next to
  an observation in the same app.

Tab, clipboard-transfer and app-continuity signals are scoped to the exact
organization, actor, device and session. Missing scope values are not wildcards;
invalid scope values cannot support these joins. Exact-record references retain
their separate source-identity conflict checks and may span actors/sessions.
Time alone never links anything. Linked items are not tasks, business cases or
outcomes. No titles, UI labels, URLs or clipboard contents are read or returned.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from .record_case_lanes import _EVENT_ID, _RESOURCE, _identity, _stamp, _token

_TRANSFER = re.compile(r"^[A-Za-z0-9:._-]{1,120}$")
_TAB = re.compile(r"^[A-Za-z0-9:._-]{1,160}$")
_MAX_ITEMS = 50
_MAX_EVENT_IDS = 50
_MAX_CANDIDATES = 50
_CONFIDENCE = {"same_record": 0.95, "clipboard_transfer": 0.85, "same_tab_single_record": 0.7}
_CANDIDATE_CONFIDENCE = {"same_tab_multiplexed": 0.4, "same_tab_unreferenced": 0.35, "surface_continuity": 0.3}


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _field(event: dict[str, Any], name: str, pattern: re.Pattern[str]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    value = str(event.get(name) or metadata.get(name) or "")
    return value if pattern.fullmatch(value) else ""


_SCOPE_FIELDS = ("organization_id", "actor_id", "device_id", "session_id")


def _link_scope(event: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Exact local identity for ephemeral tab/transfer/app signals.

    Legacy absent fields have their own empty identity; they never match a
    known organization/actor/device/session. Malformed fields cannot anchor
    ephemeral joins. This does not authorize cross-actor record linking.
    """
    values: list[str] = []
    for name in _SCOPE_FIELDS:
        value = event.get(name)
        if value is None:
            values.append("")
        elif isinstance(value, str) and len(value) <= 160:
            values.append(value)
        else:
            return None
    return tuple(values)  # type: ignore[return-value]


def _observations(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Validated, de-duplicated, chronologically ordered observations."""
    identities: dict[str, tuple[str, str, str, str]] = {}
    conflicts: set[str] = set()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "")
        stamp = _stamp(event.get("observed_at"))
        if not _EVENT_ID.fullmatch(event_id) or event_id in seen or stamp is None:
            continue
        seen.add(event_id)
        match = _identity(event)
        ref = ""
        if match:
            ref, provider, kind, host, org = match
            if identities.setdefault(ref, (provider, kind, host, org)) != (provider, kind, host, org):
                conflicts.add(ref)
        else:
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            claimed = metadata.get("resource_reference") if isinstance(metadata.get("resource_reference"), dict) else {}
            claimed_ref = str(claimed.get("resource_ref") or "").lower()
            if _RESOURCE.fullmatch(claimed_ref):
                conflicts.add(claimed_ref)
        rows.append({
            "event_id": event_id, "observed_at": stamp, "ref": ref,
            "provider": match[1] if match else "", "kind": match[2] if match else "",
            "tab": _field(event, "tab_context_id", _TAB),
            "transfer": _field(event, "clipboard_transfer_id", _TRANSFER),
            "scope": _link_scope(event),
            "app": str(event.get("app") or "").strip().casefold()[:120],
            "action": _token(event.get("event_type")),
            "source": _token(event.get("source")),
        })
    # A reference seen with conflicting identities never links anything.
    for row in rows:
        if row["ref"] in conflicts:
            row["ref"] = ""
    rows.sort(key=lambda row: (row["observed_at"], row["event_id"]))
    return rows, len(conflicts)


def link_observations(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Internal membership (event ids per item) plus typed links and candidates."""
    rows, conflicts = _observations(events)
    union = _Union()
    for row in rows:
        union.find(row["event_id"])
    links: list[tuple[str, str, str]] = []

    def join(group: list[str], basis: str) -> None:
        for other in group[1:]:
            if union.find(other) != union.find(group[0]):
                links.append((group[0], other, basis))
            union.union(group[0], other)

    by_ref: dict[str, list[str]] = defaultdict(list)
    by_transfer: dict[tuple[tuple[str, str, str, str], str], list[str]] = defaultdict(list)
    by_tab: dict[tuple[tuple[str, str, str, str], str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["ref"]:
            by_ref[row["ref"]].append(row["event_id"])
        # Opaque IDs are not globally unique: two tenants, people, devices or
        # capture sessions can legitimately reuse the same tab/transfer token.
        # Do not use missing/invalid identity as a wildcard.
        if row["scope"] is not None:
            if row["transfer"]:
                by_transfer[(row["scope"], row["transfer"])].append(row["event_id"])
            if row["tab"]:
                by_tab[(row["scope"], row["tab"])].append(row)
    for group in by_ref.values():
        join(group, "same_record")
    for group in by_transfer.values():
        if len(group) >= 2:
            join(group, "clipboard_transfer")

    candidates: list[dict[str, Any]] = []
    for tab_rows in by_tab.values():
        refs = {row["ref"] for row in tab_rows if row["ref"]}
        loose = [row for row in tab_rows if not row["ref"]]
        if len(refs) == 1:
            anchor = next(row["event_id"] for row in tab_rows if row["ref"])
            for row in loose:
                join([anchor, row["event_id"]], "same_tab_single_record")
        elif len(refs) > 1:
            for row in loose:
                earlier = [other for other in tab_rows if other["ref"] and other["observed_at"] <= row["observed_at"]]
                if earlier:
                    candidates.append({"event_id": row["event_id"], "toward_event_id": earlier[-1]["event_id"],
                                       "relation": "same_tab_multiplexed"})
        elif len(loose) >= 2:
            for previous, row in zip(loose, loose[1:]):
                candidates.append({"event_id": row["event_id"], "toward_event_id": previous["event_id"],
                                   "relation": "same_tab_unreferenced"})
    for index, row in enumerate(rows):
        if row["ref"] or row["tab"] or row["transfer"] or not row["app"]:
            continue
        for neighbour in (rows[index - 1] if index else None, rows[index + 1] if index + 1 < len(rows) else None):
            if (row["scope"] is not None and neighbour is not None
                    and neighbour["scope"] == row["scope"] and neighbour["app"] == row["app"]):
                candidates.append({"event_id": row["event_id"], "toward_event_id": neighbour["event_id"],
                                   "relation": "surface_continuity"})
    members: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        members[union.find(row["event_id"])].append(row["event_id"])
    return {"rows": rows, "members": members, "root": union.find, "links": links,
            "candidates": candidates, "conflicting_references": conflicts}


def linked_work_items(events: list[dict[str, Any]], *, max_items: int = 20) -> dict[str, Any]:
    """Privacy-minimized projection of :func:`link_observations`."""
    max_items = max(1, min(int(max_items), _MAX_ITEMS))
    linked = link_observations(events)
    rows = {row["event_id"]: row for row in linked["rows"]}
    bases: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for left, _right, basis in linked["links"]:
        bases[linked["root"](left)][basis] += 1
    items: list[dict[str, Any]] = []
    item_of: dict[str, str] = {}
    groups = sorted((ids for ids in linked["members"].values() if len(ids) >= 2),
                    key=lambda ids: (-len(ids), rows[ids[0]]["observed_at"], ids[0]))
    for number, ids in enumerate(groups[:max_items], start=1):
        item_id = f"linked_item_{number:02d}"
        for event_id in ids:
            item_of[event_id] = item_id
        refs = sorted({rows[i]["ref"] for i in ids if rows[i]["ref"]})
        basis = dict(sorted(bases[linked["root"](ids[0])].items()))
        items.append({
            "item_id": item_id,
            "event_ids": ids[:_MAX_EVENT_IDS],
            "event_ids_truncated": len(ids) > _MAX_EVENT_IDS,
            "observed_event_count": len(ids),
            "resource_refs": refs[:12],
            "record_kinds": sorted({f"{rows[i]['provider']}:{rows[i]['kind']}" for i in ids if rows[i]["ref"]})[:12],
            "first_observed_at": rows[ids[0]]["observed_at"],
            "last_observed_at": rows[ids[-1]]["observed_at"],
            "observed_steps": [{"observed_at": rows[i]["observed_at"], "action": rows[i]["action"],
                                "source": rows[i]["source"]} for i in ids[:24]],
            "link_basis": basis,
            "cross_record_join": len(refs) > 1,
            "confidence": min(_CONFIDENCE[name] for name in basis) if basis else _CONFIDENCE["same_record"],
            "association_status": "linked",
            "task_identity_inferred": False,
            "business_outcome_verified": False,
        })
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in linked["candidates"]:
        source, target = candidate["event_id"], candidate["toward_event_id"]
        if item_of.get(source) and item_of.get(source) == item_of.get(target):
            continue
        key = (source, target, candidate["relation"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            **candidate,
            "event_item_id": item_of.get(source),
            "toward_item_id": item_of.get(target),
            "confidence": _CANDIDATE_CONFIDENCE[candidate["relation"]],
            "association_status": "ambiguous",
            "merged": False,
        })
    unlinked = sum(1 for ids in linked["members"].values() if len(ids) == 1)
    return {
        "format": "openworkgraph.linked-work-items.v1",
        "items": items,
        "total_items": len(groups),
        "candidate_links": candidates[:_MAX_CANDIDATES],
        "total_candidate_links": len(candidates),
        "unlinked_observation_count": unlinked,
        "conflicting_reference_groups_suppressed": linked["conflicting_references"],
        "link_contract": {
            "merged_bases": sorted(_CONFIDENCE),
            "candidate_only_bases": sorted(_CANDIDATE_CONFIDENCE),
            "time_alone_never_links": True,
            "ephemeral_signals_scope": "exact_organization_actor_device_session",
            "missing_scope_is_wildcard": False,
            "labels_titles_urls_and_clipboard_contents_read": False,
            "linked_items_are_tasks": False,
            "ask_when_ambiguous": True,
        },
        "no_item_means_no_linking_evidence_not_no_work": True,
        "derived": True,
        "authoritative": False,
        "read_only": True,
    }


__all__ = ["link_observations", "linked_work_items"]
