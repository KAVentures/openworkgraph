"""End-to-end local reviewed reconstruction: no automatic or canonical merges."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server.reconstruction_review_overlay import (
    DECISION_FORMAT, apply_review, prepare_review_packet, score_review,
)
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def _event(number: int, ref: str | None = None, *, tenant: str = "tenant-a") -> dict:
    metadata = {"page": {"pathname": "/SECRET/medical-notes"}, "target": {"label": "PRIVATE"}}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
            "host": "private.example", "resolver_locator": "SECRET",
        }
    return {
        "event_id": f"e{number:02d}",
        "observed_at": f"2026-10-20T10:{number:02d}:00+00:00",
        "app": "Excel", "source": "desktop",
        "event_type": "window_focus", "metadata": metadata,
        "organization_id": tenant, "actor_id": "actor-1",
        "device_id": "device-1", "session_id": "session-1",
        "window_title": "PRIVATE",
    }


def _events() -> list[dict]:
    # Interleaved A/B anchors. The middle event has weak evidence to BOTH
    # visible anchored items, but no automatic membership authority.
    return [
        _event(1, A), _event(2, B), _event(3),
        _event(4, A), _event(5, B),
    ]


def _decision(packet: dict, event_id: str, item_id: str, action: str) -> dict:
    return {
        "format": DECISION_FORMAT,
        "evidence_sha256": packet["evidence_sha256"],
        "decisions": [{"event_id": event_id, "item_id": item_id, "action": action}],
    }


def test_real_linker_candidates_require_explicit_review_and_preserve_original():
    events = _events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    [candidate] = packet["eligible_candidates"]
    assert candidate["event_id"] == "e03"
    assert len(candidate["candidate_items"]) == 2
    assert packet["eligible_review_total"] == 1
    target = candidate["candidate_items"][0]["item_id"]
    overlay = apply_review(events, packet, _decision(packet, "e03", target, "confirm"))
    reviewed = next(item for item in overlay["items"] if item["item_id"] == target)
    assert "e03" in reviewed["event_ids"]
    assert reviewed["human_confirmed_event_ids"] == ["e03"]
    assert overlay["human_confirmed_count"] == 1
    assert overlay["canonical_membership_unchanged"] is True
    assert overlay["authorizes_business_action"] is False
    assert linked_work_items(events) == before
    for forbidden in ("SECRET", "PRIVATE", "private.example", "/medical-notes"):
        assert forbidden not in json.dumps(packet)
        assert forbidden not in json.dumps(overlay)


def test_rejection_does_not_attach_and_empty_decisions_are_safe():
    events = _events()
    packet = prepare_review_packet(events)
    item_id = packet["eligible_candidates"][0]["candidate_items"][0]["item_id"]
    rejected = apply_review(events, packet, _decision(packet, "e03", item_id, "reject"))
    assert rejected["human_rejected_count"] == 1
    assert rejected["human_confirmed_count"] == 0
    assert "e03" in rejected["unassigned_event_ids"]
    assert all("e03" not in item["event_ids"] for item in rejected["items"])
    empty = apply_review(events, packet, {
        "format": DECISION_FORMAT, "evidence_sha256": packet["evidence_sha256"], "decisions": [],
    })
    assert empty["eligible_unreviewed_count"] == 1


def test_stale_revoked_forged_and_duplicate_decisions_fail_closed():
    events = _events()
    packet = prepare_review_packet(events)
    item_id = packet["eligible_candidates"][0]["candidate_items"][0]["item_id"]
    decision = _decision(packet, "e03", item_id, "confirm")
    changed = copy.deepcopy(events)
    changed[2]["metadata"]["target"]["label"] = "PRIVATE-CHANGED"
    for altered in (changed, events[:-1], [row for row in events if row["event_id"] != "e03"]):
        with pytest.raises(ValueError, match="stale|altered"):
            apply_review(altered, packet, decision)
    forged = copy.deepcopy(packet)
    forged["eligible_candidates"][0]["candidate_items"].append({
        "item_id": "linked_item_99", "weak_relations": ["surface_continuity"],
    })
    with pytest.raises(ValueError, match="stale|altered"):
        apply_review(events, forged, decision)
    for invalid in (
        _decision(packet, "e03", "linked_item_99", "confirm"),
        _decision(packet, "e01", item_id, "confirm"),
        {**decision, "decisions": decision["decisions"] * 2},
        {**decision, "evidence_sha256": "0" * 64},
        {**decision, "decisions": [{**decision["decisions"][0], "authorization": True}]},
    ):
        with pytest.raises(ValueError):
            apply_review(events, packet, invalid)


def test_scope_isolation_prevents_cross_tenant_review_candidate():
    events = [_event(1, A), _event(2, B), _event(3, tenant="tenant-b"),
              _event(4, A), _event(5, B)]
    packet = prepare_review_packet(events)
    assert all(row["event_id"] != "e03" for row in packet["eligible_candidates"])
    assert packet["eligible_review_total"] == 0


def test_complete_independent_labels_score_auto_and_human_separately():
    events = _events()
    packet = prepare_review_packet(events)
    items = {item["item_id"]: item["event_ids"] for item in linked_work_items(events)["items"]}
    # e03 belongs to B in the independent annotation, despite the A candidate.
    b_item = next(item_id for item_id, ids in items.items() if "e02" in ids)
    decision = _decision(packet, "e03", b_item, "confirm")
    truth = {"workflows": [
        {"event_ids": ["e01", "e04"]},
        {"event_ids": ["e02", "e03", "e05"]},
    ], "noise_event_ids": []}
    report = score_review(events, packet, decision, truth)
    assert report["automatic_baseline"]["workflow_assignment_f1"] < 1.0
    assert report["human_assisted_overlay"]["workflow_assignment_f1"] == 1.0
    assert report["human_assisted_overlay"]["cross_workflow_contamination"] == 0.0
    assert report["automatic_model_improvement_claim"] is False
    assert report["preregistered_family_holdout"] is False
    for incomplete in (
        {**truth, "noise_event_ids": ["e01"]},
        {**truth, "workflows": [{"event_ids": ["e01", "e04"]}]},
        {**truth, "noise_event_ids": ["unknown"]},
    ):
        with pytest.raises(ValueError, match="partition"):
            score_review(events, packet, decision, incomplete)


def test_real_cli_private_prepare_apply_score_and_stale_refusal(tmp_path: Path):
    events = _events()
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(events), encoding="utf-8")
    packet_path = tmp_path / "packet.json"
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
    prepared = run("prepare", "--events", str(events_path), "--output", str(packet_path))
    assert prepared.returncode == 0, prepared.stderr
    packet = json.loads(packet_path.read_text())
    assert "PRIVATE" not in packet_path.read_text()
    if os.name != "nt":
        assert packet_path.stat().st_mode & 0o077 == 0
    target = packet["eligible_candidates"][0]["candidate_items"][0]["item_id"]
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps(_decision(packet, "e03", target, "confirm")))
    output_path = tmp_path / "overlay.json"
    applied = run("apply", "--events", str(events_path), "--packet", str(packet_path),
                  "--decisions", str(decisions_path), "--output", str(output_path))
    assert applied.returncode == 0, applied.stderr
    assert json.loads(output_path.read_text())["human_confirmed_count"] == 1
    # No overwrite, no partial output after evidence revocation.
    assert run("apply", "--events", str(events_path), "--packet", str(packet_path),
               "--decisions", str(decisions_path), "--output", str(output_path)).returncode == 2
    events_path.write_text(json.dumps(events[:-1]))
    refused_path = tmp_path / "refused.json"
    refused = run("apply", "--events", str(events_path), "--packet", str(packet_path),
                  "--decisions", str(decisions_path), "--output", str(refused_path))
    assert refused.returncode == 2
    assert not refused_path.exists()


def test_cli_scoring_and_duplicate_decision_keys_are_refused(tmp_path: Path):
    events = _events()
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(events))
    packet_path = tmp_path / "packet.json"
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
    assert run("prepare", "--events", str(events_path),
               "--output", str(packet_path)).returncode == 0
    packet = json.loads(packet_path.read_text())
    b_item = next(item["item_id"] for item in linked_work_items(events)["items"]
                  if "e02" in item["event_ids"])
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps(_decision(packet, "e03", b_item, "confirm")))
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps({
        "workflows": [{"event_ids": ["e01", "e04"]},
                      {"event_ids": ["e02", "e03", "e05"]}],
        "noise_event_ids": [],
    }))
    score_path = tmp_path / "score.json"
    scored = run("score", "--events", str(events_path),
                 "--packet", str(packet_path), "--decisions", str(decisions_path),
                 "--truth", str(truth_path), "--output", str(score_path))
    assert scored.returncode == 0, scored.stderr
    result = json.loads(score_path.read_text())
    assert result["automatic_baseline"]["workflow_assignment_f1"] < 1
    assert result["human_assisted_overlay"]["workflow_assignment_f1"] == 1
    assert "PRIVATE" not in score_path.read_text()
    decisions_path.write_text(
        '{"format":"openworkgraph.reconstruction-review-decisions.v1",'
        '"evidence_sha256":"x","decisions":[],"decisions":[]}'
    )
    invalid_output = tmp_path / "invalid.json"
    refused = run("apply", "--events", str(events_path),
                  "--packet", str(packet_path), "--decisions", str(decisions_path),
                  "--output", str(invalid_output))
    assert refused.returncode == 2
    assert not invalid_output.exists()
