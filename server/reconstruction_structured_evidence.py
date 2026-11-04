"""Consent-gated structured category evidence for production review-only assignment.

Reads only explicitly approved opaque category IDs from local observation metadata.
Does not inspect work text, titles, URLs, clipboard contents or provider services.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .reconstruction_tag_global_assignment import globally_tagged_proposals
from .reconstruction_tag_scoring import _TAG
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

FIELD = "semantic_category_ids"


def structured_evidence_proposals(
    events: list[dict[str, Any]], *, consent: bool = False,
    minimum_shared: int = 2,
) -> dict[str, Any]:
    """Extract bounded literal category IDs and globally match review candidates.

    Conflicting duplicate event-ID claims are excluded entirely, rather than
    allowing first-observation-wins tag assignment. The production optimizer
    independently enforces candidate safety, scope and evidence digest.
    """
    if consent is not True:
        raise PermissionError("explicit local structured-evidence consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    tags: dict[str, list[str]] = {}
    rejected: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("events must be objects")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise ValueError("literal event IDs required")
        metadata = event.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            rejected.add(event_id)
            continue
        values = (metadata or {}).get(FIELD, [])
        if (not isinstance(values, list) or len(values) > 16
                or any(not isinstance(v, str) or not _TAG.fullmatch(v) for v in values)):
            rejected.add(event_id)
            continue
        if event_id in tags:
            # Even matching duplicates cannot add independent semantic evidence.
            rejected.add(event_id)
        else:
            tags[event_id] = sorted(set(values))
    for event_id in rejected:
        tags.pop(event_id, None)
    result = globally_tagged_proposals(
        events, tags, consent=True, minimum_shared=minimum_shared,
    )
    result["scoring_method"] = "explicit_local_structured_categories_global_v1"
    result["structured_category_events"] = len(tags)
    result["rejected_category_event_ids"] = len(rejected)
    result["category_source"] = "metadata.semantic_category_ids"
    result["automatic_merge"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local structured category reconstruction review")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--consent-local-structured-evidence", action="store_true")
    parser.add_argument("--minimum-shared", type=int, default=2)
    args = parser.parse_args()
    if not args.consent_local_structured_evidence:
        parser.error("explicit local structured evidence consent required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local events JSON")
    result = structured_evidence_proposals(
        json.loads(args.events.read_text(encoding="utf-8")),
        consent=True, minimum_shared=args.minimum_shared,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
