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


def score_record_lanes(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return aggregate(cases, [predict_record_lanes(case) for case in cases])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score OWG's exact-record lanes on the reconstruction corpus")
    parser.add_argument("--cases", help="optional exported JSONL corpus")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    args = parser.parse_args(argv)
    cases = load_cases(Path(args.cases)) if args.cases else load_cases()
    if args.split != "all":
        from .prepare import split_cases
        cases = split_cases(cases, args.split)
    print(json.dumps(score_record_lanes(cases), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
