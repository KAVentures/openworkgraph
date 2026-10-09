"""Fixture fidelity and privacy invariants for hosted MCP routing benchmark."""
from __future__ import annotations

import json

from evals.hosted_context.fixtures import fixture_events
from evals.hosted_context.score import load_cases


def test_all_ground_truth_ids_exist_and_event_order_is_stable():
    events = fixture_events()
    ids = [event["event_id"] for event in events]
    targets = {ref for case in load_cases() for ref in case["target_event_ids"]}
    assert targets.issubset(set(ids))
    assert len(ids) == len(set(ids))
    assert len(events) >= 250
    assert [event["observed_at"] for event in events] == sorted(event["observed_at"] for event in events)


def test_old_target_and_false_inferences_remain_retrievable():
    events = fixture_events()
    assert "evt-old-001" not in {row["event_id"] for row in events[-200:]}
    rows = {row["event_id"]: row for row in events}
    assert rows["evt-untagged-004"]["metadata"] == {}
    assert rows["evt-unrelated-001"]["metadata"]["resource_reference"]["resource_ref"] != (
        rows["evt-unrelated-002"]["metadata"]["resource_reference"]["resource_ref"]
    )


def test_fixture_contains_no_sensitive_real_world_payload():
    events = fixture_events()
    for event in events:
        text = json.dumps(event, ensure_ascii=False)
        assert "@" not in text
        assert "password" not in text.casefold()
        assert "token" not in text.casefold()
        assert "https://" not in text
