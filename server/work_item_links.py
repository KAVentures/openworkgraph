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
import math
import re
from typing import Any

from .record_case_lanes import _EVENT_ID, _RESOURCE, _identity, _stamp, _token

_TRANSFER = re.compile(r"^[A-Za-z0-9:._-]{1,120}$")
_TAB = re.compile(r"^[A-Za-z0-9:._-]{1,160}$")
_MAX_ITEMS = 50
_MAX_EVENT_IDS = 50
_MAX_CANDIDATES = 50
_MAX_DUPLICATE_PAYLOAD_NODES = 10_000
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
    """Read a literal opaque identifier, never a coerced or fallback claim.

    An explicit top-level value takes precedence even when malformed/empty:
    falling back to metadata could silently replace a rejected claim with a
    different identifier. Legacy metadata-only string IDs remain supported.
    """
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    value = event[name] if name in event else metadata.get(name)
    return value if isinstance(value, str) and pattern.fullmatch(value) else ""


def _literal_record_claim(event: dict[str, Any]) -> bool:
    """Reject type-coerced provider and tenant identity before exact-record joins.

    The shared record-lane normalizer is backward-compatible with older input
    shapes. Here, a present explicit record claim must use literal string
    identity fields; otherwise unrelated records could become the same coerced
    boolean or integer identity. Missing optional host/organization remains
    a distinct legacy identity, not a wildcard.
    """
    metadata = event.get("metadata")
    reference = metadata.get("resource_reference") if isinstance(metadata, dict) else None
    if not isinstance(reference, dict):
        return False
    if any(not isinstance(reference.get(name), str)
           for name in ("resource_ref", "provider", "resource_kind")):
        return False
    host = reference.get("host")
    organization = event.get("organization_id")
    return ((host is None or isinstance(host, str))
            and (organization is None or isinstance(organization, str)))


_SCOPE_FIELDS = ("organization_id", "actor_id", "device_id", "session_id")


def _link_scope(event: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Exact local identity for ephemeral tab/transfer/app signals.

    Legacy absent fields have their own empty identity; they never match a
    known organization/actor/device/session. Explicit null/empty and other
    malformed fields cannot anchor
    ephemeral joins. This does not authorize cross-actor record linking.
    """
    values: list[str] = []
    for name in _SCOPE_FIELDS:
        # Only an absent legacy field denotes an unknown scope component.
        # An explicit null/empty value is a malformed identity claim, not a
        # wildcard or a synonym for absence: it cannot anchor weak joins.
        if name not in event:
            values.append("")
            continue
        value = event[name]
        if not isinstance(value, str) or not value or len(value) > 160:
            return None
        values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _same_observation_payload(first: Any, second: Any) -> bool:
    """Compare duplicate event claims without Python's bool/int coercion.

    Dict/list nesting is checked iteratively with a fixed work bound. Only
    literal JSON-shaped values are admissible as an *identical* payload:
    `True == 1` and `1 == 1.0` must not choose a first-row-wins record,
    tenant or weak-link claim. Unsupported/cyclic/overlarge payloads fail
    closed rather than granting duplicate evidence authority.
    """
    pending: list[tuple[Any, Any]] = [(first, second)]
    checked = 0
    while pending:
        checked += 1
        if checked > _MAX_DUPLICATE_PAYLOAD_NODES:
            return False
        left, right = pending.pop()
        if type(left) is not type(right):
            return False
        if type(left) is dict:
            if (len(left) != len(right) or
                    any(type(key) is not str for key in left) or
                    left.keys() != right.keys()):
                return False
            pending.extend((left[key], right[key]) for key in left)
        elif type(left) is list:
            if len(left) != len(right):
                return False
            pending.extend(zip(left, right))
        elif type(left) is float:
            if not math.isfinite(left) or not math.isfinite(right) or left != right:
                return False
        elif type(left) in (str, int, bool, type(None)):
            if left != right:
                return False
        else:
            return False
    return True


def _observations(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Validated, de-duplicated, chronologically ordered observations."""
    identities: dict[str, tuple[str, str, str, str]] = {}
    conflicts: set[str] = set()
    invalid_claim_events: set[str] = set()
    rows: list[dict[str, Any]] = []
    # First-row-wins dedupe is unsafe for reused event IDs: a later row may
    # contradict the first row's record, scope or timestamp. Suppress every
    # occurrence of a conflicted ID before building any reference groups.
    # Exact duplicate payloads remain idempotent (including nested metadata).
    unique_events: dict[str, dict[str, Any]] = {}
    conflicting_event_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        raw_event_id = event.get("event_id")
        event_id = raw_event_id if isinstance(raw_event_id, str) else ""
        if not _EVENT_ID.fullmatch(event_id):
            continue
        previous = unique_events.get(event_id)
        if previous is not None:
            if not _same_observation_payload(event, previous):
                conflicting_event_ids.add(event_id)
        else:
            unique_events[event_id] = event
    for event_id, event in unique_events.items():
        if event_id in conflicting_event_ids:
            continue
        stamp = _stamp(event.get("observed_at"))
        if stamp is None:
            continue
        match = _identity(event) if _literal_record_claim(event) else None
        ref = ""
        if match:
            ref, provider, kind, host, org = match
            if identities.setdefault(ref, (provider, kind, host, org)) != (provider, kind, host, org):
                conflicts.add(ref)
        else:
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            raw_claim = metadata.get("resource_reference")
            claimed = raw_claim if isinstance(raw_claim, dict) else {}
            claimed_ref = str(claimed.get("resource_ref") or "").lower()
            # A malformed explicit reference (including non-object values) is
            # not an unreferenced observation. It cannot regain authority via
            # a tab, transfer, or adjacent surface candidate.
            if "resource_reference" in metadata:
                invalid_claim_events.add(event_id)
            if _RESOURCE.fullmatch(claimed_ref):
                conflicts.add(claimed_ref)
        rows.append({
            "event_id": event_id, "observed_at": stamp, "ref": ref,
            "provider": match[1] if match else "", "kind": match[2] if match else "",
            "tab": _field(event, "tab_context_id", _TAB),
            "transfer": _field(event, "clipboard_transfer_id", _TRANSFER),
            "scope": _link_scope(event),
            "reference_tainted": False,
            "app": event["app"].strip().casefold()[:120] if isinstance(event.get("app"), str) else "",
            "action": _token(event.get("event_type")),
            "source": _token(event.get("source")),
        })
    # A reference seen with conflicting identities never links anything,
    # including via weaker signals that would otherwise treat it as loose.
    # Invalid explicit claims are similarly ineligible for ephemeral linking.
    for row in rows:
        if row["ref"] in conflicts or row["event_id"] in invalid_claim_events:
            row["reference_tainted"] = True
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
    by_transfer: dict[tuple[tuple[str, str, str, str], str], list[dict[str, Any]]] = defaultdict(list)
    by_tab: dict[tuple[tuple[str, str, str, str], str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["ref"]:
            by_ref[row["ref"]].append(row["event_id"])
        # Opaque IDs are not globally unique: two tenants, people, devices or
        # capture sessions can legitimately reuse the same tab/transfer token.
        # Do not use missing/invalid identity as a wildcard.
        if row["scope"] is not None and not row["reference_tainted"]:
            if row["transfer"]:
                by_transfer[(row["scope"], row["transfer"])].append(row)
            if row["tab"]:
                by_tab[(row["scope"], row["tab"])].append(row)
    for group in by_ref.values():
        join(group, "same_record")
    ambiguous_transfer_groups = 0
    inconsistent_transfer_direction_groups = 0
    for group in by_transfer.values():
        if len(group) < 2:
            continue
        # A single scoped token claiming three distinct records is not a
        # reliable copy/paste edge: token reuse or interleaved work is possible.
        # Keep independent same-record evidence but do not merge the fanout.
        referenced_records = {row["ref"] for row in group if row["ref"]}
        if len(referenced_records) > 2:
            ambiguous_transfer_groups += 1
            continue
        # Any explicit copy/paste observation makes direction relevant.
        # A generic click in the same token group cannot erase contradictory
        # copy-only, paste-only, or reversed directional evidence. Groups with
        # *no* explicit direction retain the legacy generic capture contract.
        # For two referenced records, the ordered copy -> paste must span
        # both records directly; a loose observation is not a substitute.
        # Timestamps only veto an observed token's inconsistent direction;
        # they never establish a link without the shared transfer token.
        if any(row["action"] in ("copy", "paste") for row in group):
            earlier_copies: dict[str, str] = {}
            directional_pair = False
            for row in group:  # rows are already ordered by observed UTC time
                if row["action"] == "copy":
                    earlier_copies.setdefault(row["ref"], row["observed_at"])
                elif row["action"] == "paste" and any(
                    at < row["observed_at"] and
                    (
                        # With two distinct referenced records, an unreferenced
                        # copy/paste cannot attest transfer to the *other*
                        # record. Require the ordered pair to span both records.
                        (bool(copy_ref) and bool(row["ref"]) and copy_ref != row["ref"])
                        if len(referenced_records) == 2 else
                        (copy_ref != row["ref"] or not copy_ref or not row["ref"])
                    )
                    for copy_ref, at in earlier_copies.items()
                ):
                    directional_pair = True
                    break
            if not directional_pair:
                inconsistent_transfer_direction_groups += 1
                continue
        # A direct A-copy -> B-paste proves a relationship between those
        # referenced records, not membership for every loose event reusing
        # the token. In explicit two-record groups, only the referenced
        # observations receive the clipboard edge. Independent tab evidence
        # can still attach a loose event later on its own merits.
        # Preserve the legacy all-generic and one-record transfer contracts.
        if len(referenced_records) == 2 and any(
            row["action"] in ("copy", "paste") for row in group
        ):
            join([row["event_id"] for row in group if row["ref"]], "clipboard_transfer")
        else:
            join([row["event_id"] for row in group], "clipboard_transfer")

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
        if row["reference_tainted"] or row["ref"] or row["tab"] or row["transfer"] or not row["app"]:
            continue
        for neighbour in (rows[index - 1] if index else None, rows[index + 1] if index + 1 < len(rows) else None):
            if (row["scope"] is not None and neighbour is not None
                    and not neighbour["reference_tainted"]
                    and neighbour["scope"] == row["scope"] and neighbour["app"] == row["app"]):
                candidates.append({"event_id": row["event_id"], "toward_event_id": neighbour["event_id"],
                                   "relation": "surface_continuity"})
    members: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        members[union.find(row["event_id"])].append(row["event_id"])
    return {"rows": rows, "members": members, "root": union.find, "links": links,
            "candidates": candidates, "conflicting_references": conflicts,
            "ambiguous_transfer_groups": ambiguous_transfer_groups,
            "inconsistent_transfer_direction_groups": inconsistent_transfer_direction_groups}


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
        "ambiguous_clipboard_transfer_groups": linked["ambiguous_transfer_groups"],
        "inconsistent_clipboard_transfer_direction_groups": linked["inconsistent_transfer_direction_groups"],
        "link_contract": {
            "merged_bases": sorted(_CONFIDENCE),
            "candidate_only_bases": sorted(_CANDIDATE_CONFIDENCE),
            "time_alone_never_links": True,
            "ephemeral_signals_scope": "exact_organization_actor_device_session",
            "missing_scope_is_wildcard": False,
            "multiplexed_clipboard_transfer_is_unmerged": True,
            "explicit_clipboard_direction_conflicts_are_unmerged": True,
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
