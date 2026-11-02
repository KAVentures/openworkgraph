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


def _fragment_events() -> list[dict]:
    # e03 has a direct weak edge to anchored A; e04 only reaches A through
    # the still-unassigned e03. The Terminal boundary avoids a direct e04
    # anchor edge. No temporal-proximity-only link is eligible.
    events = [_event(1, A), _event(2, A), _event(3), _event(4), _event(5)]
    events[-1]["app"] = "Terminal"
    return events


def test_real_linker_two_hop_fragment_requires_review_without_promoting_intermediary():
    events = _fragment_events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    direct = next(row for row in packet["eligible_candidates"] if row["event_id"] == "e03")
    fragment = next(row for row in packet["eligible_candidates"] if row["event_id"] == "e04")
    assert direct["evidence_hops"] == 1
    assert fragment["evidence_hops"] == 2
    assert packet["eligible_direct_review_total"] >= 1
    assert packet["eligible_fragment_review_total"] >= 1
    [target] = fragment["candidate_items"]
    assert target["witness_event_path"][0:2] == ["e04", "e03"]
    assert len(target["witness_event_path"]) == 3
    assert target["weak_relation_path"] == ["surface_continuity", "surface_continuity"]
    assert target["distinct_intermediary_count"] >= 1
    overlay = apply_review(events, packet, _decision(packet, "e04", target["item_id"], "confirm"))
    item = next(row for row in overlay["items"] if row["item_id"] == target["item_id"])
    assert item["human_confirmed_two_hop_event_ids"] == ["e04"]
    assert item["human_confirmed_direct_event_ids"] == []
    assert overlay["human_confirmed_two_hop_count"] == 1
    assert overlay["human_confirmed_direct_count"] == 0
    assert "e03" in overlay["unassigned_event_ids"]
    assert "e05" in overlay["unassigned_event_ids"]
    assert linked_work_items(events) == before
    for forbidden in ("SECRET", "PRIVATE", "private.example", "/medical-notes"):
        assert forbidden not in json.dumps(packet)
        assert forbidden not in json.dumps(overlay)


def test_two_hop_review_refuses_forged_path_revocation_and_cross_scope():
    events = _fragment_events()
    packet = prepare_review_packet(events)
    fragment = next(row for row in packet["eligible_candidates"] if row["event_id"] == "e04")
    item_id = fragment["candidate_items"][0]["item_id"]
    decision = _decision(packet, "e04", item_id, "confirm")
    forged = copy.deepcopy(packet)
    next(row for row in forged["eligible_candidates"] if row["event_id"] == "e04")[
        "candidate_items"][0]["witness_event_path"][1] = "e99"
    with pytest.raises(ValueError, match="stale|altered"):
        apply_review(events, forged, decision)
    changed = copy.deepcopy(events)
    changed[3]["organization_id"] = "other-tenant"
    with pytest.raises(ValueError, match="stale|altered"):
        apply_review(changed, packet, decision)
    changed_source = copy.deepcopy(events)
    changed_source[3]["source"] = "browser"
    assert all(row["event_id"] != "e04" or row["evidence_hops"] != 2
               for row in prepare_review_packet(changed_source)["eligible_candidates"])
    changed_scope = copy.deepcopy(events)
    changed_scope[3]["organization_id"] = "other-tenant"
    assert all(row["event_id"] != "e04" or row["evidence_hops"] != 2
               for row in prepare_review_packet(changed_scope)["eligible_candidates"])
    with pytest.raises(ValueError):
        apply_review(events, packet, {**decision, "decisions": decision["decisions"] * 2})


def test_two_hop_independent_scoring_separates_human_gain_from_auto_f1():
    events = _fragment_events()
    packet = prepare_review_packet(events)
    fragment = next(row for row in packet["eligible_candidates"] if row["event_id"] == "e04")
    decision = _decision(packet, "e04", fragment["candidate_items"][0]["item_id"], "confirm")
    truth = {
        "workflows": [{"event_ids": ["e01", "e02", "e04"]}],
        "noise_event_ids": ["e03", "e05"],
    }
    report = score_review(events, packet, decision, truth)
    assert report["human_confirmed_two_hop_count"] == 1
    assert report["human_confirmed_direct_count"] == 0
    assert (report["human_assisted_overlay"]["workflow_assignment_f1"]
            > report["automatic_baseline"]["workflow_assignment_f1"])
    assert report["human_assisted_overlay"]["interruption_rejection"] == 1.0
    assert report["automatic_model_improvement_claim"] is False


def test_two_hop_cli_prepare_apply_and_score_with_actual_linker(tmp_path: Path):
    events = _fragment_events()
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(events))
    packet_path = tmp_path / "packet.json"
    root = Path(__file__).resolve().parents[1]
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
    assert run("prepare", "--events", str(events_path),
               "--output", str(packet_path)).returncode == 0
    packet = json.loads(packet_path.read_text())
    fragment = next(row for row in packet["eligible_candidates"] if row["event_id"] == "e04")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps(_decision(
        packet, "e04", fragment["candidate_items"][0]["item_id"], "confirm")))
    overlay_path = tmp_path / "overlay.json"
    result = run("apply", "--events", str(events_path), "--packet", str(packet_path),
                 "--decisions", str(decisions_path), "--output", str(overlay_path))
    assert result.returncode == 0, result.stderr
    assert json.loads(overlay_path.read_text())["human_confirmed_two_hop_count"] == 1
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps({
        "workflows": [{"event_ids": ["e01", "e02", "e04"]}],
        "noise_event_ids": ["e03", "e05"],
    }))
    score_path = tmp_path / "score.json"
    result = run("score", "--events", str(events_path), "--packet", str(packet_path),
                 "--decisions", str(decisions_path), "--truth", str(truth_path),
                 "--output", str(score_path))
    assert result.returncode == 0, result.stderr
    assert json.loads(score_path.read_text())["human_confirmed_two_hop_count"] == 1
    assert "PRIVATE" not in overlay_path.read_text()
    assert "PRIVATE" not in score_path.read_text()


def test_mixed_direct_and_fragment_decisions_reject_contradictory_intermediary():
    events = _fragment_events()
    packet = prepare_review_packet(events)
    candidates = {row["event_id"]: row for row in packet["eligible_candidates"]}
    direct_item = candidates["e03"]["candidate_items"][0]["item_id"]
    fragment_item = candidates["e04"]["candidate_items"][0]["item_id"]
    assert direct_item == fragment_item
    decisions = {
        "format": DECISION_FORMAT,
        "evidence_sha256": packet["evidence_sha256"],
        "decisions": [
            {"event_id": "e03", "item_id": direct_item, "action": "confirm"},
            {"event_id": "e04", "item_id": fragment_item, "action": "confirm"},
        ],
    }
    overlay = apply_review(events, packet, decisions)
    assert overlay["human_confirmed_count"] == 2
    assert overlay["human_confirmed_direct_count"] == 1
    assert overlay["human_confirmed_two_hop_count"] == 1
    assert overlay["human_rejected_count"] == 0
    assert "e03" not in overlay["unassigned_event_ids"]
    assert "e04" not in overlay["unassigned_event_ids"]
    assert overlay["human_decisions_are_not_source_evidence"] is True
    # A reviewer cannot reject the very intermediary-to-item link that the
    # confirmed two-hop loose event depends on, in either decision order.
    contradictory = copy.deepcopy(decisions)
    contradictory["decisions"][0]["action"] = "reject"
    for rows in (contradictory["decisions"], list(reversed(contradictory["decisions"]))):
        with pytest.raises(ValueError, match="conflicting human decisions"):
            apply_review(events, packet, {**contradictory, "decisions": rows})



def _batch_truth() -> dict:
    return {
        "workflows": [
            {"event_ids": ["e01", "e04"]},
            {"event_ids": ["e02", "e03", "e05"]},
        ],
        "noise_event_ids": [],
    }


def _batch_session(suffix: str) -> tuple[list[dict], dict, dict, dict]:
    events = copy.deepcopy(_events())
    for event in events:
        event["session_id"] = f"session-{suffix}"
    packet = prepare_review_packet(events)
    target = next(
        item["item_id"] for item in linked_work_items(events)["items"]
        if "e02" in item["event_ids"]
    )
    decisions = _decision(packet, "e03", target, "confirm")
    return events, packet, decisions, _batch_truth()


def test_batch_aggregates_actual_linker_scores_without_leaking_source_ids():
    from server.reconstruction_review_overlay import aggregate_review_scores, BATCH_FORMAT

    first = _batch_session("one")
    second = _batch_session("two")
    scores = [score_review(*session) for session in (first, second)]
    result = aggregate_review_scores(scores)
    assert result["format"] == BATCH_FORMAT
    assert result["session_count"] == 2
    assert result["annotated_observation_count"] == 10
    assert result["human_confirmed_count"] == 2
    assert result["session_macro_automatic_baseline"]["workflow_assignment_f1"] == (
        scores[0]["automatic_baseline"]["workflow_assignment_f1"]
    )
    assert result["session_macro_human_assisted_overlay"]["workflow_assignment_f1"] == 1.0
    assert result["session_macro_assisted_minus_automatic"]["workflow_assignment_f1"] > 0
    assert result["local_numeric_thresholds"]["observed_local_automatic_meets_both"] is False
    assert result["preregistered_family_holdout_verified"] is False
    assert result["automatic_improvement_claim"] is False
    output = json.dumps(result)
    for forbidden in ("SECRET", "PRIVATE", "private.example", "/medical-notes",
                      first[1]["evidence_sha256"], "e03", "session-one"):
        assert forbidden not in output
    assert [row["session_index"] for row in result["sessions"]] == [1, 2]
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_review_scores([scores[0], scores[0]])
    for corrupted in (
        {**scores[1], "automatic_baseline": {
            **scores[1]["automatic_baseline"], "workflow_assignment_f1": float("nan"),
        }},
        {**scores[1], "human_assisted_overlay": {
            **scores[1]["human_assisted_overlay"], "cross_workflow_contamination": 2,
        }},
        {**scores[1], "annotated_event_count": True},
    ):
        with pytest.raises(ValueError):
            aggregate_review_scores([scores[0], corrupted])


def test_batch_cli_replays_complete_private_sessions_and_refuses_stale_manifest(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=root, capture_output=True, text=True, timeout=40,
        )
    sessions = []
    for index in range(2):
        events, packet, decisions, truth = _batch_session(str(index))
        paths = {}
        for key, document in (
            ("events", events), ("packet", packet),
            ("decisions", decisions), ("truth", truth),
        ):
            path = tmp_path / f"{index}-{key}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            paths[key] = str(path)
        sessions.append(paths)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "format": "openworkgraph.reconstruction-review-manifest.v1",
        "sessions": sessions,
    }))
    output = tmp_path / "batch-score.json"
    completed = run("score-batch", "--manifest", str(manifest), "--output", str(output))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["session_count"] == 2
    assert report["human_confirmed_count"] == 2
    assert "PRIVATE" not in output.read_text()
    assert str(tmp_path) not in output.read_text()
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0
    # A changed or revoked source invalidates its packet and the entire
    # batch refuses before creating a partial output.
    original = json.loads(Path(sessions[1]["events"]).read_text())
    Path(sessions[1]["events"]).write_text(json.dumps(original[:-1]))
    refused_output = tmp_path / "refused.json"
    refused = run("score-batch", "--manifest", str(manifest),
                  "--output", str(refused_output))
    assert refused.returncode == 2
    assert not refused_output.exists()
    Path(sessions[1]["events"]).write_text(json.dumps(original))
    # Duplicate evidence is not two independent sessions.
    duplicate = copy.deepcopy(sessions)
    duplicate[1] = copy.deepcopy(duplicate[0])
    manifest.write_text(json.dumps({
        "format": "openworkgraph.reconstruction-review-manifest.v1",
        "sessions": duplicate,
    }))
    assert run("score-batch", "--manifest", str(manifest),
               "--output", str(refused_output)).returncode == 2
    assert not refused_output.exists()
    # Unknown keys, unsupported formats and over-budget input fail closed.
    for invalid in (
        {"format": "unknown", "sessions": sessions},
        {"format": "openworkgraph.reconstruction-review-manifest.v1",
         "sessions": [sessions[0]]},
        {"format": "openworkgraph.reconstruction-review-manifest.v1",
         "sessions": sessions, "upload": True},
    ):
        manifest.write_text(json.dumps(invalid))
        assert run("score-batch", "--manifest", str(manifest),
                   "--output", str(refused_output)).returncode == 2
        assert not refused_output.exists()
