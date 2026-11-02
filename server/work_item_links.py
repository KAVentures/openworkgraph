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

from bisect import bisect_right
from collections import defaultdict
import math
import re
from typing import Any

from .record_case_lanes import _EVENT_ID, _RESOURCE, _identity, _stamp, _token
from .reconstruction_segments import observed_activity_segments
from .reconstruction_record_path import observed_record_path, record_path_index
from .reconstruction_switches import observed_work_switches
from .reconstruction_surfaces import observed_surface_runs
from .reconstruction_transfer_episodes import disjoint_directional_transfer_episodes
from .reconstruction_link_proof import directional_transfer_witness, observed_record_link_evidence
from .reconstruction_tab_focus import (
    bracketed_multiplexed_tab_focus, single_record_tab_foreign_case_boundaries,
)
from .reconstruction_actor_continuity import actor_observation_index, observed_same_record_actor_changes
from .reconstruction_case_coverage import observed_record_participant_surfaces

_TRANSFER = re.compile(r"^[A-Za-z0-9:._-]{1,120}$")
_TAB = re.compile(r"^[A-Za-z0-9:._-]{1,160}$")
_MAX_ITEMS = 50
_MAX_EVENT_IDS = 50
_MAX_CANDIDATES = 50
_MAX_DUPLICATE_PAYLOAD_NODES = 10_000
_CONFIDENCE = {"same_record": 0.95, "clipboard_transfer": 0.85, "same_tab_single_record": 0.7, "same_tab_bracketed_record": 0.72, "same_tab_interleaved_bracketed_record": 0.68, "same_tab_single_record_interleaved_bracketed": 0.66}
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


def _dual_field_disagreement(event: dict[str, Any], name: str) -> bool:
    """Whether two explicitly supplied signal IDs contradict each other.

    Never compare deep malformed JSON values: only matching literal strings
    are trustworthy agreement. Absent legacy locations are not conflicts.
    """
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if name not in event or name not in metadata:
        return False
    top, nested = event[name], metadata[name]
    return not (isinstance(top, str) and isinstance(nested, str) and top == nested)


def _contradictory_field_tokens(
    event: dict[str, Any], name: str, pattern: re.Pattern[str],
) -> set[str]:
    """Valid tokens vetoed by inconsistent top-level/metadata signal claims."""
    if not _dual_field_disagreement(event, name):
        return set()
    metadata = event["metadata"]
    return {value for value in (event[name], metadata[name])
            if isinstance(value, str) and pattern.fullmatch(value)}


def _malformed_optional_identity(value: Any) -> bool:
    """Reject corrupt explicit host/tenant claims, retaining empty legacy IDs.

    The shared normalizer strips host whitespace. An explicitly supplied
    whitespace-only host must not silently become a missing legacy host;
    control-bearing identities must not anchor source-grounded membership.
    Genuinely absent/null/empty optional identities retain prior semantics.
    """
    return (value is not None and (
        not isinstance(value, str) or
        (bool(value) and (
            value.isspace() or
            any(ord(char) < 32 or ord(char) == 127 for char in value)
        ))
    ))


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
    return (not _malformed_optional_identity(host)
            and not _malformed_optional_identity(organization))


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
        if (not isinstance(value, str) or not value or len(value) > 160
                or value.isspace()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            # Explicit whitespace-only or control-bearing scope strings are
            # not trustworthy identity, even when repeated verbatim. Do not
            # normalize them into a different tenant/actor/session identity.
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
            # A single wide JSON object can enqueue far more than the node
            # budget before the next loop iteration. Account for all pending
            # children *before* materializing the comparison worklist.
            if (len(left) != len(right) or
                    checked + len(pending) + len(left) > _MAX_DUPLICATE_PAYLOAD_NODES or
                    any(type(key) is not str for key in left) or
                    left.keys() != right.keys()):
                return False
            pending.extend((left[key], right[key]) for key in left)
        elif type(left) is list:
            if (len(left) != len(right) or
                    checked + len(pending) + len(left) > _MAX_DUPLICATE_PAYLOAD_NODES):
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


def _observations(events: list[dict[str, Any]]) -> tuple[
    list[dict[str, Any]], int,
    set[tuple[tuple[str, str, str, str], str]],
    set[tuple[tuple[str, str, str, str], str]],
]:
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
    # Conflicting duplicate event IDs are discarded, but their exact-scoped
    # ephemeral token claims must still veto weaker joins among surviving
    # observations. Otherwise discarding a competing record makes a tab look
    # single-record or a transfer look like an uncontested A-copy/B-paste.
    # Both conflicting occurrences contribute their *own* valid scoped token;
    # neither is allowed to poison a different scope or opaque token. A row
    # with contradictory top-level/metadata claims also vetoes BOTH named
    # valid tokens: otherwise one silently ignored alternative can make a
    # surviving tab or clipboard group appear uncontested.
    duplicate_tainted_transfers: set[tuple[tuple[str, str, str, str], str]] = set()
    duplicate_tainted_tabs: set[tuple[tuple[str, str, str, str], str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        if _stamp(event.get("observed_at")) is None:
            continue
        scope = _link_scope(event)
        if scope is None:
            continue
        for token in _contradictory_field_tokens(event, "clipboard_transfer_id", _TRANSFER):
            duplicate_tainted_transfers.add((scope, token))
        for token in _contradictory_field_tokens(event, "tab_context_id", _TAB):
            duplicate_tainted_tabs.add((scope, token))
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or event_id not in conflicting_event_ids:
            continue
        transfer = _field(event, "clipboard_transfer_id", _TRANSFER)
        tab = _field(event, "tab_context_id", _TAB)
        if transfer:
            duplicate_tainted_transfers.add((scope, transfer))
        if tab:
            duplicate_tainted_tabs.add((scope, tab))
    def register_record_claim(event: dict[str, Any], event_id: str) -> tuple[str, str, str, str, str] | None:
        # Record identity contradictions must be assessed even for rows that
        # cannot become members (invalid time or conflicting duplicate IDs).
        # Discarding a duplicate must not erase a conflicting provider/tenant
        # claim about the *same* opaque reference.
        match = _identity(event) if _literal_record_claim(event) else None
        if match:
            ref, provider, kind, host, org = match
            if identities.setdefault(ref, (provider, kind, host, org)) != (provider, kind, host, org):
                conflicts.add(ref)
        else:
            # Explicit malformed metadata cannot be treated as an absent legacy
            # object. We cannot inspect a hidden/conflicting resource claim,
            # so its top-level tab/clipboard tokens must not authorize joins.
            # Truly absent metadata remains a supported legacy capture shape.
            if "metadata" in event and not isinstance(event["metadata"], dict):
                invalid_claim_events.add(event_id)
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            raw_claim = metadata.get("resource_reference")
            claimed = raw_claim if isinstance(raw_claim, dict) else {}
            claimed_ref = str(claimed.get("resource_ref") or "").lower()
            # Malformed explicit source claims cannot be reintroduced through
            # tab, transfer or adjacent-surface evidence.
            if "resource_reference" in metadata:
                invalid_claim_events.add(event_id)
            if _RESOURCE.fullmatch(claimed_ref):
                conflicts.add(claimed_ref)
        return match

    # Every conflicting duplicate occurrence still contributes its literal
    # source identity claim. None contributes observation membership, a
    # timestamp, or an ephemeral link endpoint. Independent refs stay separate.
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id in conflicting_event_ids:
            register_record_claim(event, event_id)

    for event_id, event in unique_events.items():
        if event_id in conflicting_event_ids:
            continue
        # An invalid observation time removes membership, not the source
        # identity claim. A contradictory provider/tenant assertion must not
        # disappear merely because its timestamp cannot be ordered.
        match = register_record_claim(event, event_id)
        ref = match[0] if match else ""
        # Invalid timestamps never become linked observations or scoped
        # clipboard/tab vetoes; only their literal source-identity claims can
        # invalidate a conflicting exact-reference group.
        stamp = _stamp(event.get("observed_at"))
        if stamp is None:
            continue
        # A supplied nonstring action is not a trustworthy legacy generic
        # clipboard event. Keep its record/tab evidence, but do not let the
        # shared token authorize a cross-record transfer.
        malformed_action = ("event_type" in event and
                            not isinstance(event["event_type"], str))
        rows.append({
            "event_id": event_id, "observed_at": stamp, "ref": ref,
            "provider": match[1] if match else "", "kind": match[2] if match else "",
            "tab": _field(event, "tab_context_id", _TAB),
            "transfer": _field(event, "clipboard_transfer_id", _TRANSFER),
            "scope": _link_scope(event),
            "reference_tainted": False,
            "ephemeral_claim_tainted": (
                _dual_field_disagreement(event, "tab_context_id") or
                _dual_field_disagreement(event, "clipboard_transfer_id")
            ),
            "action_claim_malformed": malformed_action,
            "app": event["app"].strip().casefold()[:120] if isinstance(event.get("app"), str) else "",
            "action": "unknown" if malformed_action else _token(event.get("event_type")),
            "source": _token(event.get("source")) if isinstance(event.get("source"), str) else "unknown",
        })
    # A reference seen with conflicting identities never links anything,
    # including via weaker signals that would otherwise treat it as loose.
    # Invalid explicit claims are similarly ineligible for ephemeral linking.
    for row in rows:
        if row["ref"] in conflicts or row["event_id"] in invalid_claim_events:
            row["reference_tainted"] = True
            row["ref"] = ""
    rows.sort(key=lambda row: (row["observed_at"], row["event_id"]))
    return rows, len(conflicts), duplicate_tainted_transfers, duplicate_tainted_tabs


def link_observations(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Internal membership (event ids per item) plus typed links and candidates."""
    rows, conflicts, tainted_transfers, tainted_tabs = _observations(events)
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
        if row["scope"] is None:
            continue
        if row["action_claim_malformed"] and row["transfer"]:
            # An invalid explicit action cannot be treated as a generic
            # legacy event that completes an otherwise unproven transfer.
            # Veto only this exact scoped token, not record or tab evidence.
            tainted_transfers.add((row["scope"], row["transfer"]))
        if row["reference_tainted"]:
            # Excluding a contradictory record claim from the token group is
            # insufficient: the remaining observations could appear to form a
            # single-record tab or a clean two-record clipboard transfer.
            # Veto only this exact scoped token, never independent record links.
            if row["transfer"]:
                tainted_transfers.add((row["scope"], row["transfer"]))
            if row["tab"]:
                tainted_tabs.add((row["scope"], row["tab"]))
            continue
        if row["transfer"]:
            by_transfer[(row["scope"], row["transfer"])].append(row)
        if row["tab"]:
            by_tab[(row["scope"], row["tab"])].append(row)
    for group in by_ref.values():
        join(group, "same_record")
    ambiguous_transfer_groups = 0
    recovered_transfer_groups = 0
    recovered_transfer_episodes = 0
    inconsistent_transfer_direction_groups = 0
    for token_key, group in by_transfer.items():
        if token_key in tainted_transfers:
            continue
        if len(group) < 2:
            continue
        # A single scoped token claiming three distinct records is not a
        # reliable copy/paste edge: token reuse or interleaved work is possible.
        # Keep independent same-record evidence but do not merge the fanout.
        referenced_records = {row["ref"] for row in group if row["ref"]}
        if len(referenced_records) > 2:
            # A capture token may be reused for separate operations. Recover
            # strictly witnessed, non-overlapping directional episodes only;
            # never union the entire multi-record token group. Any partial,
            # interleaved, loose or repeated-record evidence remains ambiguous.
            episodes = disjoint_directional_transfer_episodes(group)
            if episodes is None:
                ambiguous_transfer_groups += 1
                continue
            for source_event, target_event in episodes:
                join([source_event, target_event], "clipboard_transfer")
            recovered_transfer_groups += 1
            recovered_transfer_episodes += len(episodes)
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
            # Join the actual witnessed endpoints, not every observation
            # sharing this token. Same-record edges already attach other
            # observations to the appropriate record.
            pair = directional_transfer_witness(group)
            if pair is None:
                inconsistent_transfer_direction_groups += 1
                continue
            join(list(pair), "clipboard_transfer")
        else:
            if len(referenced_records) == 2:
                # For legacy generic-only tokens, establish the two-record
                # witness edge before attaching loose observations. Otherwise
                # a loose-first capture would produce only loose-to-record
                # union edges and conceal the actual weak A/B association.
                referenced = [row["event_id"] for row in group if row["ref"]]
                loose = [row["event_id"] for row in group if not row["ref"]]
                join(referenced + loose, "clipboard_transfer")
            else:
                join([row["event_id"] for row in group], "clipboard_transfer")

    # A tab containing only record A cannot safely absorb a loose event
    # already attached (via clipboard) to record B. Nor may two single-record
    # tabs attach A and B to the *same* loose clipboard component. Evaluate
    # these competing claims against the fixed post-transfer components BEFORE
    # any tab union, so tab iteration order cannot choose a winning record.
    pre_tab_root = {row["event_id"]: union.find(row["event_id"]) for row in rows}
    component_refs: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["ref"]:
            refs = component_refs[pre_tab_root[row["event_id"]]]
            if len(refs) < 2:
                refs.add(row["ref"])
    tab_targets: dict[str, set[str]] = defaultdict(set)
    for token_key, tab_rows in by_tab.items():
        if token_key in tainted_tabs:
            continue
        refs = {row["ref"] for row in tab_rows if row["ref"]}
        if len(refs) != 1:
            continue
        sole_ref = next(iter(refs))
        for row in tab_rows:
            if not row["ref"]:
                targets = tab_targets[pre_tab_root[row["event_id"]]]
                if len(targets) < 2:
                    targets.add(sole_ref)

    # A multiplexed tab can still contain a strictly observed A -> loose -> A
    # focus span. Compute all proposed targets against frozen post-transfer
    # components *before* tab joins, so a competing single-record tab or
    # second focus claim cannot win merely through iteration order.
    multiplexed_tabs = {
        key for key, tab_rows in by_tab.items()
        if key not in tainted_tabs and len({r["ref"] for r in tab_rows if r["ref"]}) > 1
    }
    focus_proposals = bracketed_multiplexed_tab_focus(rows, multiplexed_tabs)
    # A tab with only one observed record can still be reused for unrelated
    # work. If an independently observed same-scope foreign record interrupts
    # its one-sided tab evidence, require a bounded two-sided A -> loose -> A
    # witness. Never let the foreign case itself become a membership endpoint.
    single_record_boundaries = single_record_tab_foreign_case_boundaries(
        rows, {key: tab_rows for key, tab_rows in by_tab.items()
               if key not in tainted_tabs}, pre_tab_root,
    )
    single_record_tabs = {
        key for key, tab_rows in by_tab.items()
        if key not in tainted_tabs and
        len({r["ref"] for r in tab_rows if r["ref"]}) == 1
    }
    single_focus_proposals = [
        (anchor, loose, ref, "same_tab_single_record_interleaved_bracketed")
        for anchor, loose, ref, basis in
        bracketed_multiplexed_tab_focus(rows, single_record_tabs)
        if basis == "same_tab_interleaved_bracketed_record"
        and loose in single_record_boundaries
    ]
    focus_proposals.extend(single_focus_proposals)
    for _anchor, loose_id, focus_ref, _basis in focus_proposals:
        targets = tab_targets[pre_tab_root[loose_id]]
        if len(targets) < 2:
            targets.add(focus_ref)

    candidates: list[dict[str, Any]] = []
    tab_component_conflicts = 0
    for token_key, tab_rows in by_tab.items():
        if token_key in tainted_tabs:
            continue
        refs = {row["ref"] for row in tab_rows if row["ref"]}
        loose = [row for row in tab_rows if not row["ref"]]
        if len(refs) == 1:
            anchor = next(row["event_id"] for row in tab_rows if row["ref"])
            for row in loose:
                if row["event_id"] in single_record_boundaries:
                    # Requires a separate, strict two-sided focus witness.
                    continue
                if union.find(anchor) == union.find(row["event_id"]):
                    # This observation is already in the anchor component;
                    # no new tab attachment is being considered or withheld.
                    continue
                original_root = pre_tab_root[row["event_id"]]
                # A loose clipboard component already containing another
                # record, or targeted by two different single-record tabs,
                # is ambiguous. Do not let transitive union manufacture an
                # A/B case from two individually plausible weak signals.
                if (component_refs.get(original_root, set()) - refs or
                        len(tab_targets.get(original_root, set())) > 1):
                    tab_component_conflicts += 1
                    continue
                join([anchor, row["event_id"]], "same_tab_single_record")
        elif len(refs) > 1:
            # Candidate-only, never a merge. Both lists inherit the global
            # (UTC timestamp, event_id) order. Index referenced timestamps
            # once rather than scanning the entire tab for every loose row:
            # O(n log n) rather than O(n^2) on long interleaved tabs.
            # bisect_right preserves the old <= behavior for exact ties,
            # including references ordered after a loose event by event_id.
            referenced = [row for row in tab_rows if row["ref"]]
            reference_times = [row["observed_at"] for row in referenced]
            for row in loose:
                position = bisect_right(reference_times, row["observed_at"])
                if position:
                    candidates.append({"event_id": row["event_id"],
                                       "toward_event_id": referenced[position - 1]["event_id"],
                                       "relation": "same_tab_multiplexed"})
        elif len(loose) >= 2:
            for previous, row in zip(loose, loose[1:]):
                candidates.append({"event_id": row["event_id"], "toward_event_id": previous["event_id"],
                                   "relation": "same_tab_unreferenced"})
    bracketed_tab_focus_links = 0
    interleaved_tab_focus_links = 0
    single_record_interleaved_focus_links = 0
    bracketed_tab_focus_conflicts = 0
    for anchor_id, loose_id, focus_ref, focus_basis in focus_proposals:
        loose_root = pre_tab_root[loose_id]
        anchor_root = pre_tab_root[anchor_id]
        if (component_refs.get(loose_root, set()) - {focus_ref}
                or component_refs.get(anchor_root, set()) - {focus_ref}
                or len(tab_targets.get(loose_root, set())) > 1):
            bracketed_tab_focus_conflicts += 1
            continue
        if union.find(anchor_id) != union.find(loose_id):
            join([anchor_id, loose_id], focus_basis)
            if focus_basis == "same_tab_single_record_interleaved_bracketed":
                single_record_interleaved_focus_links += 1
            else:
                bracketed_tab_focus_links += 1
                if focus_basis == "same_tab_interleaved_bracketed_record":
                    interleaved_tab_focus_links += 1

    for index, row in enumerate(rows):
        if (row["reference_tainted"] or row["ephemeral_claim_tainted"] or
                row["ref"] or row["tab"] or row["transfer"] or not row["app"]):
            continue
        for neighbour in (rows[index - 1] if index else None, rows[index + 1] if index + 1 < len(rows) else None):
            if (row["scope"] is not None and neighbour is not None
                    and not neighbour["reference_tainted"]
                    and not neighbour["ephemeral_claim_tainted"]
                    and neighbour["scope"] == row["scope"] and neighbour["app"] == row["app"]):
                candidates.append({"event_id": row["event_id"], "toward_event_id": neighbour["event_id"],
                                   "relation": "surface_continuity"})
    members: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        members[union.find(row["event_id"])].append(row["event_id"])
    return {"rows": rows, "members": members, "root": union.find, "links": links,
            "candidates": candidates, "conflicting_references": conflicts,
            "ambiguous_transfer_groups": ambiguous_transfer_groups,
            "recovered_transfer_groups": recovered_transfer_groups,
            "recovered_transfer_episodes": recovered_transfer_episodes,
            "inconsistent_transfer_direction_groups": inconsistent_transfer_direction_groups,
            "tab_component_conflicts": tab_component_conflicts,
            "bracketed_tab_focus_links": bracketed_tab_focus_links,
            "interleaved_tab_focus_links": interleaved_tab_focus_links,
            "single_record_interleaved_focus_links": single_record_interleaved_focus_links,
            "single_record_interleaving_abstentions": (
                len(single_record_boundaries) - single_record_interleaved_focus_links
            ),
            "bracketed_tab_focus_conflicts": bracketed_tab_focus_conflicts}


def linked_work_items(events: list[dict[str, Any]], *, max_items: int = 20) -> dict[str, Any]:
    """Privacy-minimized projection of :func:`link_observations`."""
    max_items = max(1, min(int(max_items), _MAX_ITEMS))
    linked = link_observations(events)
    rows = {row["event_id"]: row for row in linked["rows"]}
    bases: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    link_edges: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for left, right, basis in linked["links"]:
        root = linked["root"](left)
        bases[root][basis] += 1
        link_edges[root].append((left, right, basis))
    items: list[dict[str, Any]] = []
    item_of: dict[str, str] = {}
    record_positions, record_scope_positions = record_path_index(linked["rows"])
    actor_positions, actor_tenant_positions = actor_observation_index(linked["rows"])
    groups = sorted((ids for ids in linked["members"].values() if len(ids) >= 2),
                    key=lambda ids: (-len(ids), rows[ids[0]]["observed_at"], ids[0]))
    for number, ids in enumerate(groups[:max_items], start=1):
        item_id = f"linked_item_{number:02d}"
        for event_id in ids:
            item_of[event_id] = item_id
        refs = sorted({rows[i]["ref"] for i in ids if rows[i]["ref"]})
        basis = dict(sorted(bases[linked["root"](ids[0])].items()))
        segments = observed_activity_segments(linked["rows"], ids)
        surfaces = observed_surface_runs([rows[i] for i in ids])
        record_path = observed_record_path(
            [rows[i] for i in ids], position_index=record_positions,
            scope_positions=record_scope_positions,
        )
        bridge_evidence = observed_record_link_evidence(
            [rows[i] for i in ids], link_edges[linked["root"](ids[0])],
        )
        actor_participation = observed_same_record_actor_changes(
            [rows[i] for i in ids], position_index=actor_positions,
            tenant_positions=actor_tenant_positions,
        )
        coverage = observed_record_participant_surfaces([rows[i] for i in ids])
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
            **segments,
            **surfaces,
            **record_path,
            **bridge_evidence,
            **actor_participation,
            **coverage,
            "link_basis": basis,
            "cross_record_join": len(refs) > 1,
            "confidence": min(_CONFIDENCE[name] for name in basis) if basis else _CONFIDENCE["same_record"],
            "association_status": "linked",
            "task_identity_inferred": False,
            "business_outcome_verified": False,
        })
    switches = observed_work_switches(linked["rows"], item_of)
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
        **switches,
        "candidate_links": candidates[:_MAX_CANDIDATES],
        "total_candidate_links": len(candidates),
        "unlinked_observation_count": unlinked,
        "conflicting_reference_groups_suppressed": linked["conflicting_references"],
        "ambiguous_clipboard_transfer_groups": linked["ambiguous_transfer_groups"],
        "recovered_reused_clipboard_transfer_groups": linked["recovered_transfer_groups"],
        "recovered_disjoint_clipboard_transfer_episodes": linked["recovered_transfer_episodes"],
        "inconsistent_clipboard_transfer_direction_groups": linked["inconsistent_transfer_direction_groups"],
        "ambiguous_tab_component_attachments_suppressed": linked["tab_component_conflicts"],
        "observed_bracketed_tab_focus_links": linked["bracketed_tab_focus_links"],
        "observed_interleaved_tab_focus_links": linked["interleaved_tab_focus_links"],
        "observed_single_record_interleaved_focus_links": linked["single_record_interleaved_focus_links"],
        "ambiguous_single_record_tab_interleaving_abstentions": linked["single_record_interleaving_abstentions"],
        "ambiguous_bracketed_tab_focus_attachments_suppressed": linked["bracketed_tab_focus_conflicts"],
        "link_contract": {
            "merged_bases": sorted(_CONFIDENCE),
            "candidate_only_bases": sorted(_CANDIDATE_CONFIDENCE),
            "time_alone_never_links": True,
            "activity_segments_are_observed_episodes_not_tasks": True,
            "interruption_requires_same_scope_foreign_record": True,
            "clock_gaps_alone_do_not_split_activity_segments": True,
            "observed_surface_runs_are_not_task_memberships": True,
            "observed_record_paths_are_not_verified_business_transitions": True,
            "record_bridges_are_observed_link_witnesses_not_handoffs": True,
            "same_record_actor_changes_are_observed_not_verified_handoffs": True,
            "record_participant_surface_coverage_is_presence_not_productivity": True,
            "record_participant_surface_coverage_requires_complete_scope": True,
            "record_participant_surface_coverage_does_not_add_membership": True,
            "cross_actor_participation_requires_complete_explicit_capture_scope": True,
            "cross_actor_participation_does_not_add_membership": True,
            "directional_bridges_require_ordered_copy_paste": True,
            "legacy_shared_token_bridges_have_no_observed_direction": True,
            "observed_record_changes_require_same_exact_capture_scope": True,
            "unknown_record_runs_do_not_create_transitions": True,
            "interleaved_nonmember_evidence_breaks_record_path_continuity": True,
            "unknown_surfaces_are_not_other_app": True,
            "surface_transitions_require_verified_same_capture_scope": True,
            "observed_switches_require_same_scope_literal_record_endpoints": True,
            "observed_switches_are_not_task_handoffs": True,
            "unattributed_intervening_evidence_is_not_direct_adjacency": True,
            "ephemeral_signals_scope": "exact_organization_actor_device_session",
            "missing_scope_is_wildcard": False,
            "multiplexed_clipboard_transfer_is_unmerged": True,
            "reused_clipboard_token_only_links_disjoint_explicit_episodes": True,
            "explicit_clipboard_direction_conflicts_are_unmerged": True,
            "competing_tab_component_records_are_unmerged": True,
            "multiplexed_tab_focus_requires_global_two_sided_same_record_evidence": False,
            "multiplexed_tab_focus_requires_two_sided_same_tab_record_evidence": True,
            "interleaved_tab_focus_requires_explicit_distinct_tab": True,
            "interleaved_tab_focus_has_bounded_foreign_and_loose_events": True,
            "single_record_tab_foreign_case_requires_two_sided_focus": True,
            "multiplexed_tab_focus_requires_complete_capture_scope": True,
            "multiplexed_tab_focus_never_uses_elapsed_time_as_link_evidence": True,
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
