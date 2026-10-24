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

from server.record_case_lanes import record_case_lanes
from server.work_item_links import link_observations

from .fixtures import load_cases
from .scoring import aggregate


def _ref(event: dict[str, Any]) -> str:
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    ref = meta.get("resource_reference") if isinstance(meta.get("resource_reference"), dict) else {}
    return str(ref.get("resource_ref") or "").lower()


def predict_record_lanes(case: dict[str, Any]) -> dict[str, Any]:
    events = list(case.get("source_events") or case["presented_evidence"])
    lanes = record_case_lanes(events, max_lanes=30)["lanes"]
    published = {lane["resource_ref"] for lane in lanes}
    members: dict[str, list[str]] = {ref: [] for ref in sorted(published)}
    seen: set[str] = set()
    for event in sorted(events, key=lambda e: (str(e.get("observed_at")), str(e.get("event_id")))):
        event_id, ref = str(event.get("event_id") or ""), _ref(event)
        if ref in members and event_id and event_id not in seen:
            members[ref].append(event_id)
            seen.add(event_id)
    workflows = [{
        "prediction_id": f"lane_{index}",
        "event_ids": ids,
        "ordered_event_ids": ids,
        "automation_relevant_event_ids": ids,
        "ui_mechanic_event_ids": [],
    } for index, ids in enumerate(members.values(), start=1) if ids]
    all_ids = [str(e["event_id"]) for e in case["presented_evidence"]]
    return {
        "case_id": case["case_id"],
        "workflows": workflows,
        "unassigned_event_ids": [event_id for event_id in all_ids if event_id not in seen],
        # Lanes never claim business-rule knowledge; uncertainty is not scored as known.
        "insufficient_evidence": False,
    }


def predict_linked_items(case: dict[str, Any], *, accept_candidates: bool = False) -> dict[str, Any]:
    """Merged multi-signal items. Accepting the ambiguous candidate links is a
    diagnostic upper bound only: OWG itself never merges them."""
    events = list(case.get("source_events") or case["presented_evidence"])
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
