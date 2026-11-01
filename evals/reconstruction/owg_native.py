"""Score OWG's own shipped grouping on the reconstruction corpus.

Stage A asks whether an external AI can reconstruct work from raw evidence.
This condition asks the narrower Stage 2 roadmap question: how precise and how
complete is OWG's own exact-record grouping (``record_case_lanes``) on
interleaved cases, before any model is involved?

Lanes deliberately publish no event IDs. For scoring only, each published lane
is projected back to the corpus events carrying that lane's opaque reference.
Events the shipped function suppresses (conflicting identities, duplicates,
malformed timestamps, single-observation references) are therefore excluded
exactly as they are from the lanes themselves.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from server.record_case_lanes import _EVENT_ID, _identity, _stamp, record_case_lanes
from server.work_item_links import link_observations

from .fixtures import load_cases
from .scoring import aggregate


def _ref(event: dict[str, Any]) -> str:
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    ref = meta.get("resource_reference") if isinstance(meta.get("resource_reference"), dict) else {}
    return str(ref.get("resource_ref") or "").lower()


def _visible_lane_identities(
    presented: list[dict[str, Any]],
) -> dict[str, tuple[tuple[str, str, str, str], set[str]] | None]:
    """Admit an ID only with consistent visible identity and UTC instants.

    Rich evidence deliberately omits organization identity. Compare the four
    public resource components, not a guessed tenant or source-only row.
    A duplicate visible ID may retain several valid observed instants, but a
    missing/malformed timestamp or conflicting identity vetoes its authority.
    """
    identities: dict[str, tuple[tuple[str, str, str, str], set[str]] | None] = {}
    for event in presented:
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            continue
        match = _identity(event)
        stamp = _stamp(event.get("observed_at"))
        visible = match[:4] if match and stamp is not None else None
        if event_id not in identities:
            identities[event_id] = (visible, {stamp}) if visible is not None else None
        elif identities[event_id] is not None:
            prior_identity, prior_stamps = identities[event_id]
            if visible is None or prior_identity != visible:
                identities[event_id] = None
            else:
                prior_stamps.add(stamp)
    return identities


def _admitted_visible_lane_members(
    events: list[dict[str, Any]], published: set[str],
    visible_identities: dict[str, tuple[tuple[str, str, str, str], set[str]] | None],
) -> dict[str, list[str]]:
    """Project only source rows actually admitted by the shipped lane engine.

    `record_case_lanes` intentionally exposes no event IDs. Reconstruct the
    evaluator-only ID mapping using its exact identity, UTC timestamp and
    first-valid-(ref, event_id) admission rules, rather than matching every
    raw row by reference. Do not attribute one reused event ID to an arbitrary
    lane: a duplicate across distinct admitted references is unassignable.
    Nonliteral/malformed IDs and source-only rows cannot be scored as visible
    evidence, even if the source lane's aggregate count includes them. A visible
    ID must also carry the same unambiguous provider/kind/host/opaque reference
    as its admitted source row, at the admitted UTC instant; matching an ID
    alone is not provenance.
    """
    admitted: set[tuple[str, str]] = set()
    owners: dict[str, set[str]] = {}
    rows: dict[str, list[tuple[str, str]]] = {ref: [] for ref in published}
    for event in events:
        if not isinstance(event, dict):
            continue
        match = _identity(event)
        stamp = _stamp(event.get("observed_at"))
        raw_id = event.get("event_id")
        if (match is None or stamp is None or not isinstance(raw_id, str)
                or not _EVENT_ID.fullmatch(raw_id)):
            continue
        ref = match[0]
        key = (ref, raw_id)
        if key in admitted:
            continue
        admitted.add(key)
        owners.setdefault(raw_id, set()).add(ref)
        visible = visible_identities.get(raw_id)
        if ref in published and visible is not None and visible[0] == match[:4] and stamp in visible[1]:
            rows[ref].append((stamp, raw_id))
    return {
        ref: [event_id for _, event_id in sorted(values)
              if len(owners[event_id]) == 1]
        for ref, values in rows.items()
    }


def predict_record_lanes(case: dict[str, Any]) -> dict[str, Any]:
    # An explicitly empty source is authoritative (revocation/deletion). Only an
    # absent source key denotes a legacy presented-only evaluation case.
    events = list((case["source_events"] if "source_events" in case
                   else case["presented_evidence"]) or [])
    lanes = record_case_lanes(events, max_lanes=30)["lanes"]
    published = {lane["resource_ref"] for lane in lanes}
    presented = case["presented_evidence"]
    visible_identities = _visible_lane_identities(presented)
    members = _admitted_visible_lane_members(events, published, visible_identities)
    seen = {event_id for ids in members.values() for event_id in ids}
    workflows = [{
        "prediction_id": f"lane_{index}",
        "event_ids": ids,
        "ordered_event_ids": ids,
        "automation_relevant_event_ids": ids,
        "ui_mechanic_event_ids": [],
    } for index, ids in enumerate(
        (members[ref] for ref in sorted(published)), start=1
    ) if ids]
    all_ids = [str(e["event_id"]) for e in presented]
    return {
        "case_id": case["case_id"],
        "workflows": workflows,
        "unassigned_event_ids": [event_id for event_id in all_ids if event_id not in seen],
        # Lanes never claim business-rule knowledge; uncertainty is not scored as known.
        "insufficient_evidence": False,
    }


def _canonical_eval_json(value: Any) -> str | None:
    """Bounded, type-preserving JSON comparison for evaluator evidence claims."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return encoded if len(encoded) <= 65536 else None


def _linked_projection(event: dict[str, Any]) -> str | None:
    """Visible, link-authorizing fields of one observation, not hidden identity.

    Rich evidence omits actor/device/organization, so those stay authoritative
    only in the original source row and the production linker's scope checks.
    Metadata comparisons intentionally retain literal malformed claims: an
    unseen or changed tab/transfer/reference cannot acquire link authority.
    """
    stamp = _stamp(event.get("observed_at"))
    metadata = event.get("metadata")
    if stamp is None or not isinstance(metadata, dict):
        return None
    # Rich evidence projects metadata tokens into top-level convenience keys.
    # Those are not independent claims. Reject source-only top-level tokens or
    # contradictions instead of mistaking a convenience copy for a conflict.
    for field in ("tab_context_id", "clipboard_transfer_id"):
        if field in event and (field not in metadata or event[field] != metadata[field]):
            return None
    return _canonical_eval_json((
        stamp, event.get("app"), event.get("event_type"),
        event.get("source"), event.get("session_id"),
        metadata.get("resource_reference"),
        metadata.get("tab_context_id"),
        metadata.get("clipboard_transfer_id"),
        metadata.get("clipboard_source_observed"),
        metadata.get("action"), metadata.get("semantic_action"),
    ))


def _visible_linked_source_events(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only unambiguous source observations actually presented for scoring.

    Filtering happens BEFORE linking: a source-only event must neither appear
    as a member nor bridge two otherwise separate visible components. Reused
    IDs with conflicting source payloads or visible link evidence fail closed.
    Identical duplicates are idempotent. This is evaluator-only; the production
    linker continues to receive the caller's authorized source observations.
    """
    visible: dict[str, list[str | None]] = {}
    for event in case["presented_evidence"]:
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id")
        if isinstance(event_id, str) and _EVENT_ID.fullmatch(event_id):
            visible.setdefault(event_id, []).append(_linked_projection(event))

    # Do not resurrect deleted source observations from a stale visible snapshot.
    # Legacy cases without a source_events key still use presented evidence.
    source = list((case["source_events"] if "source_events" in case
                   else case["presented_evidence"]) or [])
    by_id: dict[str, list[dict[str, Any]]] = {}
    for event in source:
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id")
        if isinstance(event_id, str) and _EVENT_ID.fullmatch(event_id):
            by_id.setdefault(event_id, []).append(event)

    admitted: list[dict[str, Any]] = []
    for event_id, rows in by_id.items():
        projection = _linked_projection(rows[0])
        if (projection is None or not visible.get(event_id)
                or any(item != projection for item in visible[event_id])):
            continue
        if len(rows) > 1:
            first_claim = _canonical_eval_json(rows[0])
            if (first_claim is None or any(
                _canonical_eval_json(row) != first_claim for row in rows[1:]
            )):
                continue
        admitted.append(rows[0])
    return admitted


def predict_linked_items(case: dict[str, Any], *, accept_candidates: bool = False) -> dict[str, Any]:
    """Merged multi-signal items. Accepting the ambiguous candidate links is a
    diagnostic upper bound only: OWG itself never merges them."""
    events = _visible_linked_source_events(case)
    linked = link_observations(events)
    members = list(linked["members"].values())
    if accept_candidates:
        parent = {event_id: index for index, ids in enumerate(members) for event_id in ids}
        merged: dict[int, set[int]] = {index: {index} for index in range(len(members))}
        for candidate in linked["candidates"]:
            a, b = parent[candidate["event_id"]], parent[candidate["toward_event_id"]]
            if merged[a] is not merged[b]:
                union = merged[a] | merged[b]
                for index in union:
                    merged[index] = union
        position = {row["event_id"]: index for index, row in enumerate(linked["rows"])}
        groups = {id(group): group for group in merged.values()}.values()
        members = [sorted((e for index in group for e in members[index]), key=position.__getitem__)
                   for group in groups]
    members = [ids for ids in members if len(ids) >= 2]
    assigned = {event_id for ids in members for event_id in ids}
    all_ids = [str(e["event_id"]) for e in case["presented_evidence"]]
    return {
        "case_id": case["case_id"],
        "workflows": [{
            "prediction_id": f"item_{index}",
            "event_ids": ids,
            "ordered_event_ids": ids,
            "automation_relevant_event_ids": ids,
            "ui_mechanic_event_ids": [],
        } for index, ids in enumerate(members, start=1)],
        "unassigned_event_ids": [event_id for event_id in all_ids if event_id not in assigned],
        "insufficient_evidence": False,
    }


def score_linked_items(cases: list[dict[str, Any]], *, accept_candidates: bool = False) -> dict[str, Any]:
    return aggregate(cases, [predict_linked_items(case, accept_candidates=accept_candidates) for case in cases])


def score_record_lanes(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return aggregate(cases, [predict_record_lanes(case) for case in cases])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score OWG's exact-record lanes on the reconstruction corpus")
    parser.add_argument("--cases", help="optional exported JSONL corpus")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--grouping", choices=("record_lanes", "linked_items", "linked_items_accepting_candidates"),
                        default="record_lanes")
    args = parser.parse_args(argv)
    cases = load_cases(Path(args.cases)) if args.cases else load_cases()
    if args.split != "all":
        from .prepare import split_cases
        cases = split_cases(cases, args.split)
    if args.grouping == "record_lanes":
        report = score_record_lanes(cases)
    else:
        report = score_linked_items(cases, accept_candidates=args.grouping != "linked_items")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
