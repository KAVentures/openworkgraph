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
    ADJUDICATION_FORMAT, RESOLUTION_FORMAT, DECISION_FORMAT, adjudicate_review,
    resolve_review_disagreement,
    apply_review, prepare_review_packet, score_review,
    propose_automatic_review, score_automatic_proposals,
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


def test_two_reviewer_consensus_only_promotes_identical_human_choices():
    events = _events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    candidate = packet["eligible_candidates"][0]
    first_item, second_item = [x["item_id"] for x in candidate["candidate_items"]]
    left = _decision(packet, "e03", first_item, "confirm")
    right = _decision(packet, "e03", second_item, "confirm")
    disputed = adjudicate_review(events, packet, left, right)
    assert disputed["format"] == ADJUDICATION_FORMAT
    assert disputed["consensus_decisions"]["decisions"] == []
    assert disputed["unresolved_event_ids"] == ["e03"]
    assert disputed["reviewer_independence_verified"] is False
    overlay = apply_review(events, packet, disputed)
    assert overlay["human_confirmed_count"] == 0
    assert overlay["unresolved_reviewer_disagreement_count"] == 1
    assert overlay["review_decision_provenance"] == "two_reviewer_consensus"
    assert "e03" in overlay["unassigned_event_ids"]

    agreed = adjudicate_review(events, packet, left, copy.deepcopy(left))
    assert agreed["consensus_confirmed_count"] == 1
    assert agreed["unresolved_event_ids"] == []
    confirmed = apply_review(events, packet, agreed)
    assert confirmed["human_confirmed_count"] == 1
    assert confirmed["human_confirmed_direct_count"] == 1
    assert confirmed["canonical_membership_unchanged"] is True
    assert confirmed["authorizes_business_action"] is False
    assert linked_work_items(events) == before
    one_sided = adjudicate_review(
        events, packet, left,
        {"format": DECISION_FORMAT, "evidence_sha256": packet["evidence_sha256"],
         "decisions": []},
    )
    assert one_sided["unresolved_event_ids"] == ["e03"]
    assert apply_review(events, packet, one_sided)["human_confirmed_count"] == 0
    for artifact in (agreed, disputed):
        output = json.dumps(artifact)
        for forbidden in ("PRIVATE", "SECRET", "private.example", "/medical-notes"):
            assert forbidden not in output


def test_adjudication_rejects_tampering_revocation_and_unsupported_choices():
    events = _events()
    packet = prepare_review_packet(events)
    target = packet["eligible_candidates"][0]["candidate_items"][0]["item_id"]
    left = _decision(packet, "e03", target, "confirm")
    agreed = adjudicate_review(events, packet, left, copy.deepcopy(left))
    for mutation in (
        {"consensus_confirmed_count": 2},
        {"unresolved_event_ids": ["e03"]},
        {"consensus_decisions": {**agreed["consensus_decisions"], "decisions": []}},
        {"reviewer_independence_verified": True},
        {"extra_permission": True},
    ):
        forged = {**agreed, **mutation}
        with pytest.raises(ValueError):
            apply_review(events, packet, forged)
    altered = copy.deepcopy(events)
    altered[2]["metadata"]["target"]["label"] = "PRIVATE-REVOKED"
    with pytest.raises(ValueError, match="stale|altered"):
        apply_review(altered, packet, agreed)
    with pytest.raises(ValueError):
        adjudicate_review(events, packet, left, _decision(packet, "e01", target, "confirm"))
    with pytest.raises(ValueError):
        adjudicate_review(events, packet, left, {**left, "decisions": left["decisions"] * 2})


def test_adjudicated_two_hop_and_scoring_preserve_uncertain_intermediary():
    events = _fragment_events()
    packet = prepare_review_packet(events)
    fragment = next(x for x in packet["eligible_candidates"] if x["evidence_hops"] == 2)
    target = fragment["candidate_items"][0]["item_id"]
    left = _decision(packet, fragment["event_id"], target, "confirm")
    artifact = adjudicate_review(events, packet, left, copy.deepcopy(left))
    overlay = apply_review(events, packet, artifact)
    middle = fragment["candidate_items"][0]["witness_event_path"][1]
    assert fragment["event_id"] not in overlay["unassigned_event_ids"]
    assert middle in overlay["unassigned_event_ids"]
    assert overlay["human_confirmed_two_hop_count"] == 1
    truth = {
        "workflows": [{"event_ids": ["e01", "e02", "e04"]}],
        "noise_event_ids": ["e03", "e05"],
    }
    report = score_review(events, packet, artifact, truth)
    assert report["review_decision_provenance"] == "two_reviewer_consensus"
    assert report["automatic_model_improvement_claim"] is False
    # A reviewer may not confirm the loose fragment while explicitly
    # rejecting its required intermediary-to-item candidate.
    direct = next(x for x in packet["eligible_candidates"] if x["event_id"] == middle)
    conflicting = copy.deepcopy(left)
    conflicting["decisions"].append({
        "event_id": middle, "item_id": direct["candidate_items"][0]["item_id"],
        "action": "reject",
    })
    with pytest.raises(ValueError, match="conflicting human decisions"):
        adjudicate_review(events, packet, conflicting, copy.deepcopy(left))


def test_adjudicate_cli_to_apply_score_and_batch_replays_exact_reviewers(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=root, capture_output=True, text=True, timeout=40,
        )
    manifest_sessions = []
    for index in range(2):
        events, packet, _single, truth = _batch_session(f"adjudication-{index}")
        target = next(
            x["item_id"] for x in packet["eligible_candidates"]
            if x["event_id"] == "e03"
            for x in x["candidate_items"]
        )
        reviewer = _decision(packet, "e03", target, "confirm")
        paths = {}
        for name, document in (
            ("events", events), ("packet", packet), ("reviewer-a", reviewer),
            ("reviewer-b", copy.deepcopy(reviewer)), ("truth", truth),
        ):
            p = tmp_path / f"{index}-{name}.json"
            p.write_text(json.dumps(document))
            paths[name] = str(p)
        artifact = tmp_path / f"{index}-adjudicated.json"
        result = run(
            "adjudicate", "--events", paths["events"], "--packet", paths["packet"],
            "--reviewer-a", paths["reviewer-a"], "--reviewer-b", paths["reviewer-b"],
            "--output", str(artifact),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(artifact.read_text())["consensus_confirmed_count"] == 1
        overlay = tmp_path / f"{index}-overlay.json"
        assert run("apply", "--events", paths["events"], "--packet", paths["packet"],
                   "--decisions", str(artifact), "--output", str(overlay)).returncode == 0
        assert json.loads(overlay.read_text())["review_decision_provenance"] == (
            "two_reviewer_consensus"
        )
        manifest_sessions.append({
            "events": paths["events"], "packet": paths["packet"],
            "decisions": str(artifact), "truth": paths["truth"],
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "format": "openworkgraph.reconstruction-review-manifest.v1",
        "sessions": manifest_sessions,
    }))
    report_path = tmp_path / "batch-adjudicated.json"
    result = run("score-batch", "--manifest", str(manifest),
                 "--output", str(report_path))
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text())
    assert report["two_reviewer_consensus_session_count"] == 2
    assert report["unresolved_reviewer_disagreement_count"] == 0
    assert "PRIVATE" not in report_path.read_text()
    if os.name != "nt":
        assert report_path.stat().st_mode & 0o077 == 0
    # Tampering with the portable adjudication file must fail before output.
    altered = json.loads(Path(manifest_sessions[1]["decisions"]).read_text())
    altered["consensus_confirmed_count"] = 2
    Path(manifest_sessions[1]["decisions"]).write_text(json.dumps(altered))
    refused = tmp_path / "refused-batch.json"
    assert run("score-batch", "--manifest", str(manifest),
               "--output", str(refused)).returncode == 2
    assert not refused.exists()


def test_third_reviewer_resolves_contested_case_without_changing_canonical_membership():
    events = _events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    first, second = [x["item_id"] for x in packet["eligible_candidates"][0]["candidate_items"]]
    adjudication = adjudicate_review(
        events, packet, _decision(packet, "e03", first, "confirm"),
        _decision(packet, "e03", second, "confirm"),
    )
    assert adjudication["unresolved_event_ids"] == ["e03"]
    resolved = resolve_review_disagreement(
        events, packet, adjudication, _decision(packet, "e03", second, "confirm"),
    )
    assert resolved["format"] == RESOLUTION_FORMAT
    assert resolved["third_reviewer_resolved_count"] == 1
    assert resolved["remaining_unresolved_event_ids"] == []
    overlay = apply_review(events, packet, resolved)
    assert overlay["human_confirmed_count"] == 1
    assert overlay["review_decision_provenance"] == "third_reviewer_resolution"
    assert overlay["unresolved_reviewer_disagreement_count"] == 0
    assert "e03" in next(x for x in overlay["items"] if x["item_id"] == second)["event_ids"]
    assert overlay["canonical_membership_unchanged"] is True
    assert overlay["authorizes_business_action"] is False
    assert linked_work_items(events) == before
    scored = score_review(events, packet, resolved, _batch_truth())
    assert scored["human_assisted_overlay"]["workflow_assignment_f1"] == 1.0
    assert scored["automatic_baseline"]["workflow_assignment_f1"] < 1.0
    assert scored["automatic_model_improvement_claim"] is False
    for forbidden in ("SECRET", "PRIVATE", "private.example", "/medical-notes"):
        assert forbidden not in json.dumps(resolved)


def test_third_reviewer_cannot_change_consensus_or_resolve_outside_dispute():
    events = _events()
    packet = prepare_review_packet(events)
    first, second = [x["item_id"] for x in packet["eligible_candidates"][0]["candidate_items"]]
    left = _decision(packet, "e03", first, "confirm")
    right = _decision(packet, "e03", second, "confirm")
    disputed = adjudicate_review(events, packet, left, right)
    third = _decision(packet, "e03", second, "reject")
    resolved = resolve_review_disagreement(events, packet, disputed, third)
    assert apply_review(events, packet, resolved)["human_confirmed_count"] == 0
    for mutation in (
        {"third_reviewer_resolved_count": 2},
        {"remaining_unresolved_event_ids": ["e03"]},
        {"resolved_decisions": left},
        {"reviewer_independence_verified": True},
        {"extra_authorization": True},
    ):
        with pytest.raises(ValueError):
            apply_review(events, packet, {**resolved, **mutation})
    changed = copy.deepcopy(events)
    changed[2]["metadata"]["target"]["label"] = "revoked"
    with pytest.raises(ValueError):
        apply_review(changed, packet, resolved)
    for bad in (
        {"format": DECISION_FORMAT, "evidence_sha256": packet["evidence_sha256"], "decisions": []},
        _decision(packet, "e01", first, "confirm"),
        {**third, "decisions": third["decisions"] * 2},
    ):
        with pytest.raises(ValueError):
            resolve_review_disagreement(events, packet, disputed, bad)
    agreed = adjudicate_review(events, packet, left, copy.deepcopy(left))
    with pytest.raises(ValueError, match="only unresolved"):
        resolve_review_disagreement(events, packet, agreed, third)
    with pytest.raises(ValueError):
        resolve_review_disagreement(events, packet, {**disputed, "unresolved_event_ids": []}, third)


def test_third_reviewer_resolution_rejects_cross_review_two_hop_contradictions():
    events = _fragment_events()
    packet = prepare_review_packet(events)
    fragment = next(row for row in packet["eligible_candidates"] if row["evidence_hops"] == 2)
    direct = next(row for row in packet["eligible_candidates"] if row["evidence_hops"] == 1)
    target = fragment["candidate_items"][0]["item_id"]
    fragment_confirm = _decision(packet, fragment["event_id"], target, "confirm")
    first = copy.deepcopy(fragment_confirm)
    first["decisions"].append({
        "event_id": direct["event_id"], "item_id": target, "action": "confirm",
    })
    # Both agree on the fragment, only the first reviews the intermediary.
    disputed = adjudicate_review(events, packet, first, fragment_confirm)
    assert disputed["unresolved_event_ids"] == [direct["event_id"]]
    third_reject = _decision(packet, direct["event_id"], target, "reject")
    with pytest.raises(ValueError, match="conflicting human decisions"):
        resolve_review_disagreement(events, packet, disputed, third_reject)
    third_confirm = _decision(packet, direct["event_id"], target, "confirm")
    resolution = resolve_review_disagreement(events, packet, disputed, third_confirm)
    overlay = apply_review(events, packet, resolution)
    assert overlay["human_confirmed_count"] == 2
    assert overlay["human_confirmed_two_hop_count"] == 1
    assert overlay["human_confirmed_direct_count"] == 1


def test_resolve_cli_apply_score_and_batch_replay_private_artifact(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.reconstruction_review", *args],
            cwd=root, capture_output=True, text=True, timeout=40,
        )
    sessions = []
    for index in range(2):
        events, packet, _decisions, truth = _batch_session(f"resolution-{index}")
        target = _decisions["decisions"][0]["item_id"]
        other = next(x["item_id"] for x in packet["eligible_candidates"][0]["candidate_items"]
                     if x["item_id"] != target)
        a = _decision(packet, "e03", target, "confirm")
        b = _decision(packet, "e03", other, "confirm")
        paths = {}
        for name, document in (
            ("events", events), ("packet", packet), ("a", a), ("b", b),
            ("third", copy.deepcopy(a)), ("truth", truth),
        ):
            p = tmp_path / f"{index}-{name}.json"
            p.write_text(json.dumps(document))
            paths[name] = str(p)
        adjudicated = tmp_path / f"{index}-adjudication.json"
        assert run("adjudicate", "--events", paths["events"], "--packet", paths["packet"],
                   "--reviewer-a", paths["a"], "--reviewer-b", paths["b"],
                   "--output", str(adjudicated)).returncode == 0
        resolved = tmp_path / f"{index}-resolution.json"
        result = run("resolve", "--events", paths["events"], "--packet", paths["packet"],
                     "--adjudication", str(adjudicated), "--third-reviewer", paths["third"],
                     "--output", str(resolved))
        assert result.returncode == 0, result.stderr
        overlay_path = tmp_path / f"{index}-overlay.json"
        result = run("apply", "--events", paths["events"], "--packet", paths["packet"],
                     "--decisions", str(resolved), "--output", str(overlay_path))
        assert result.returncode == 0, result.stderr
        assert json.loads(overlay_path.read_text())["human_confirmed_count"] == 1
        sessions.append({
            "events": paths["events"], "packet": paths["packet"],
            "decisions": str(resolved), "truth": paths["truth"],
        })
        if os.name != "nt":
            assert resolved.stat().st_mode & 0o077 == 0
        assert "PRIVATE" not in resolved.read_text()
    manifest = tmp_path / "resolution-manifest.json"
    manifest.write_text(json.dumps({
        "format": "openworkgraph.reconstruction-review-manifest.v1",
        "sessions": sessions,
    }))
    report = tmp_path / "resolution-batch.json"
    result = run("score-batch", "--manifest", str(manifest), "--output", str(report))
    assert result.returncode == 0, result.stderr
    batch = json.loads(report.read_text())
    assert batch["third_reviewer_resolution_session_count"] == 2
    assert batch["unresolved_reviewer_disagreement_count"] == 0
    assert batch["session_macro_human_assisted_overlay"]["workflow_assignment_f1"] == 1.0
    assert "PRIVATE" not in report.read_text()
    # Tampered resolution cannot be used as a shortcut around revalidation.
    corrupt = json.loads(Path(sessions[1]["decisions"]).read_text())
    corrupt["third_reviewer_resolved_count"] = 99
    Path(sessions[1]["decisions"]).write_text(json.dumps(corrupt))
    refused = tmp_path / "refused-resolution-batch.json"
    assert run("score-batch", "--manifest", str(manifest),
               "--output", str(refused)).returncode == 2
    assert not refused.exists()


def test_review_interleaving_context_competing_anchors_are_not_assignments():
    events = _events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    candidate = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e03")
    context = candidate["interleaving_context"]
    assert context["status"] == "competing_record_anchors"
    assert context["preceding_linked_item_id"] != context["following_linked_item_id"]
    assert set((context["preceding_linked_item_id"], context["following_linked_item_id"])) == {
        item["item_id"] for item in before["items"]
    }
    assert context["case_identity_verified"] is False
    assert context["auto_assigned"] is False
    assert packet["observed_interleaving_context_counts"]["competing_record_anchors"] == 1
    # Context changes neither canonical membership nor the human approval gate.
    empty = {"format": DECISION_FORMAT, "evidence_sha256": packet["evidence_sha256"],
             "decisions": []}
    assert apply_review(events, packet, empty)["human_confirmed_count"] == 0
    assert linked_work_items(events) == before
    assert "SECRET" not in json.dumps(packet)
    assert "private.example" not in json.dumps(packet)


def test_review_interleaving_context_matching_anchors_and_scope_barriers():
    matching = [_event(1, A), _event(2, A), _event(3), _event(4, A), _event(5, A)]
    # An unpaired copy action is deliberately not auto-joined by tabless
    # two-sided focus recovery, but remains eligible for weak surface review.
    matching[2]["event_type"] = "copy"
    packet = prepare_review_packet(matching)
    context = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e03")[
        "interleaving_context"
    ]
    assert context["status"] == "matching_linked_item_anchors"
    assert context["preceding_linked_item_id"] == context["following_linked_item_id"]
    # A foreign-tenant observation cannot be used as a before-anchor or
    # crossed to reach an older matching record in the same tenant.
    scoped = [_event(1, A), _event(2, B, tenant="tenant-b"), _event(3),
              _event(4, A), _event(5, A)]
    scoped[2]["event_type"] = "copy"
    packet = prepare_review_packet(scoped)
    context = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e03")[
        "interleaving_context"
    ]
    assert context["status"] == "one_sided_record_anchor"
    assert context["preceding_linked_item_id"] is None
    assert context["following_linked_item_id"] is not None
    # A different application cannot supply a preceding anchor or let a
    # reviewer skip over the app boundary to a convenient earlier case.
    app_switch = copy.deepcopy(_events())
    app_switch[1]["app"] = "OtherApp"
    app_switch[2]["event_type"] = "copy"
    packet = prepare_review_packet(app_switch)
    context = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e03")[
        "interleaving_context"
    ]
    assert context["preceding_linked_item_id"] is None
    assert context["status"] == "one_sided_record_anchor"
    # Tied timestamps do not establish observed before/after order.
    tied = copy.deepcopy(matching)
    tied[1]["observed_at"] = tied[2]["observed_at"]
    packet = prepare_review_packet(tied)
    context = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e03")[
        "interleaving_context"
    ]
    assert context["preceding_linked_item_id"] is None
    assert context["status"] == "one_sided_record_anchor"


def test_review_interleaving_context_has_bounded_observation_distance():
    # e02 (B) is ten observations before e12; a nearby A anchor after
    # e12 must not turn a distant B observation into local negative evidence.
    events = [_event(1, A), _event(2, B)]
    events.extend(_event(number) for number in range(3, 13))
    events.extend([_event(13, A), _event(14, B)])
    packet = prepare_review_packet(events)
    candidate = next(x for x in packet["eligible_candidates"] if x["event_id"] == "e12")
    context = candidate["interleaving_context"]
    assert context["status"] == "one_sided_record_anchor"
    assert context["preceding_linked_item_id"] is None
    assert context["following_linked_item_id"] is not None
    assert context["maximum_intervening_observations_per_side"] == 8
    # Changing the exact evidence requires a fresh packet, including context.
    altered = copy.deepcopy(events)
    altered[3]["metadata"]["target"]["label"] = "CHANGED PRIVATE"
    with pytest.raises(ValueError, match="stale|altered"):
        apply_review(altered, packet, {"format": DECISION_FORMAT,
                     "evidence_sha256": packet["evidence_sha256"], "decisions": []})


def test_prepare_cli_exposes_interleaving_context_without_private_work_text(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "events.json"
    output = tmp_path / "context-packet.json"
    source.write_text(json.dumps(_events()))
    result = subprocess.run(
        [sys.executable, "-m", "scripts.reconstruction_review", "prepare",
         "--events", str(source), "--output", str(output)],
        cwd=root, capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stderr
    packet = json.loads(output.read_text())
    assert packet["eligible_candidates"][0]["interleaving_context"]["status"] == (
        "competing_record_anchors"
    )
    assert packet["observed_interleaving_context_counts"]["competing_record_anchors"] == 1
    for forbidden in ("SECRET", "PRIVATE", "private.example", "/medical-notes"):
        assert forbidden not in output.read_text()
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0


def _proposal_events() -> list[dict]:
    # Production linker leaves the unreferenced copy action unassigned,
    # while exact source-record A is observed on both sides.
    events = [_event(1, A), _event(2, A), _event(3),
              _event(4, A), _event(5, A)]
    events[2]["event_type"] = "copy"
    return events


def test_conservative_auto_proposal_uses_actual_linker_without_changing_membership():
    events = _proposal_events()
    original = linked_work_items(events)
    packet = prepare_review_packet(events)
    assert len(packet["eligible_candidates"]) == 1
    assert packet["eligible_candidates"][0]["evidence_hops"] == 1
    proposal = propose_automatic_review(events, packet)
    assert proposal["proposal_count"] == 1
    [entry] = proposal["proposed_assignments"]
    assert entry["event_id"] == "e03"
    assert entry["item_id"] == packet["eligible_candidates"][0]["candidate_items"][0]["item_id"]
    assert proposal["canonical_membership_unchanged"] is True
    assert proposal["case_identity_verified"] is False
    assert proposal["authorizes_business_action"] is False
    assert linked_work_items(events) == original
    for private in ("SECRET", "PRIVATE", "private.example", A, "/medical-notes"):
        assert private not in json.dumps(proposal)


def test_conservative_auto_proposal_abstains_on_competing_or_incomplete_evidence():
    competing = _events()
    assert propose_automatic_review(
        competing, prepare_review_packet(competing)
    )["proposal_count"] == 0

    for altered in ("foreign_scope", "other_app", "tied_time", "different_record"):
        events = _proposal_events()
        if altered == "foreign_scope":
            events[1]["organization_id"] = "tenant-b"
        elif altered == "other_app":
            events[1]["app"] = "OtherApp"
        elif altered == "tied_time":
            events[1]["observed_at"] = events[2]["observed_at"]
        else:
            events[1]["metadata"]["resource_reference"]["resource_ref"] = B
        packet = prepare_review_packet(events)
        proposal = propose_automatic_review(events, packet)
        assert proposal["proposal_count"] == 0, altered
        assert proposal["canonical_membership_unchanged"] is True


def test_automatic_proposal_score_is_separate_from_production_and_human_review():
    events = _proposal_events()
    packet = prepare_review_packet(events)
    truth = {"workflows": [{"event_ids": ["e01", "e02", "e03", "e04", "e05"]}],
             "noise_event_ids": []}
    result = score_automatic_proposals(events, packet, truth)
    assert result["proposal_count"] == 1
    assert result["automatic_baseline"]["workflow_assignment_f1"] < 1.0
    assert result["experimental_automatic_proposal_overlay"]["workflow_assignment_f1"] == 1.0
    assert result["experimental_automatic_proposal_overlay"]["cross_workflow_contamination"] == 0.0
    assert result["production_linker_changed"] is False
    assert result["preregistered_family_holdout"] is False
    with pytest.raises(ValueError, match="partition"):
        score_automatic_proposals(events, packet, {"workflows": [
            {"event_ids": ["e01", "e02"]}], "noise_event_ids": []})
    changed = copy.deepcopy(events)
    changed[2]["metadata"]["target"]["label"] = "REVOKED"
    with pytest.raises(ValueError, match="stale|altered"):
        score_automatic_proposals(changed, packet, truth)
    with pytest.raises(ValueError, match="stale|altered"):
        propose_automatic_review(events[:-1], packet)


def test_auto_proposal_cli_private_end_to_end(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    events = _proposal_events()
    event_file = tmp_path / "events.json"
    packet_file = tmp_path / "packet.json"
    proposal_file = tmp_path / "proposals.json"
    truth_file = tmp_path / "truth.json"
    score_file = tmp_path / "proposal-score.json"
    event_file.write_text(json.dumps(events))
    truth_file.write_text(json.dumps({
        "workflows": [{"event_ids": ["e01", "e02", "e03", "e04", "e05"]}],
        "noise_event_ids": [],
    }))

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "scripts.reconstruction_review",
                               *args], cwd=root, capture_output=True, text=True, timeout=40)

    assert run("prepare", "--events", str(event_file),
               "--output", str(packet_file)).returncode == 0
    proposed = run("propose", "--events", str(event_file),
                   "--packet", str(packet_file), "--output", str(proposal_file))
    assert proposed.returncode == 0, proposed.stderr
    assert json.loads(proposal_file.read_text())["proposal_count"] == 1
    scored = run("score-proposals", "--events", str(event_file),
                 "--packet", str(packet_file), "--truth", str(truth_file),
                 "--output", str(score_file))
    assert scored.returncode == 0, scored.stderr
    report = json.loads(score_file.read_text())
    assert report["experimental_automatic_proposal_overlay"]["workflow_assignment_f1"] == 1.0
    for path in (proposal_file, score_file):
        for private in ("SECRET", "PRIVATE", "private.example", A):
            assert private not in path.read_text()
        if os.name != "nt":
            assert path.stat().st_mode & 0o077 == 0
    # Output files are create-only; no silent replacement of earlier results.
    assert run("propose", "--events", str(event_file), "--packet",
               str(packet_file), "--output", str(proposal_file)).returncode == 2
    event_file.write_text(json.dumps(events[:-1]))
    assert run("propose", "--events", str(event_file), "--packet",
               str(packet_file), "--output", str(tmp_path / "stale.json")).returncode == 2


def _seeded_two_hop_proposal_events() -> list[dict]:
    # Three consecutive clipboard actions have no authoritative record claim.
    # e03/e05 have direct weak links to A; e04 reaches A only via e03/e05.
    # The production app-focus linker correctly abstains from copy actions.
    events = [_event(1, A), _event(2, A), _event(3), _event(4),
              _event(5), _event(6, A), _event(7, A)]
    for index in (2, 3, 4):
        events[index]["event_type"] = "copy"
    return events


def test_seeded_two_hop_proposal_recovers_bounded_copy_fragment_with_real_linker():
    events = _seeded_two_hop_proposal_events()
    before = linked_work_items(events)
    packet = prepare_review_packet(events)
    direct = {row["event_id"] for row in packet["eligible_candidates"]
              if row["evidence_hops"] == 1}
    fragments = {row["event_id"] for row in packet["eligible_candidates"]
                 if row["evidence_hops"] == 2}
    assert {"e03", "e05"} <= direct
    assert "e04" in fragments
    proposed = propose_automatic_review(events, packet)
    assert proposed["direct_proposal_count"] == 2
    assert proposed["two_hop_proposal_count"] == 1
    assert proposed["proposal_count"] == 3
    assignments = {row["event_id"]: row for row in proposed["proposed_assignments"]}
    assert set(assignments) == {"e03", "e04", "e05"}
    assert len({row["item_id"] for row in assignments.values()}) == 1
    assert assignments["e04"]["basis"] == (
        "same_literal_record_brackets_and_anchored_direct_two_hop_seed"
    )
    assert proposed["canonical_membership_unchanged"] is True
    assert proposed["authorizes_business_action"] is False
    assert linked_work_items(events) == before
    for private in ("SECRET", "PRIVATE", "private.example", A, "/medical-notes"):
        assert private not in json.dumps(proposed)


@pytest.mark.parametrize("alteration", [
    "different_record", "foreign_scope", "other_app", "other_source",
    "tied_timestamp", "missing_second_anchor",
])
def test_two_hop_auto_proposals_abstain_on_interruption_and_capture_barriers(alteration):
    events = _seeded_two_hop_proposal_events()
    if alteration == "different_record":
        events[5]["metadata"]["resource_reference"]["resource_ref"] = B
    elif alteration == "foreign_scope":
        events[2]["organization_id"] = "tenant-b"
    elif alteration == "other_app":
        events[2]["app"] = "Terminal"
    elif alteration == "other_source":
        events[2]["source"] = "browser"
    elif alteration == "tied_timestamp":
        events[2]["observed_at"] = events[3]["observed_at"]
    else:
        events[5]["metadata"].pop("resource_reference")
        events[6]["metadata"].pop("resource_reference")
    packet = prepare_review_packet(events)
    result = propose_automatic_review(events, packet)
    assert result["two_hop_proposal_count"] == 0, alteration
    assert result["canonical_membership_unchanged"] is True



def test_seeded_two_hop_proposal_abstains_on_competing_visible_record_items():
    events = _seeded_two_hop_proposal_events()
    # Both A and B are real, visible, distinct linked items. The middle
    # action has weak two-hop paths to each and cannot be ranked into A.
    events[5]["metadata"]["resource_reference"]["resource_ref"] = B
    events[6]["metadata"]["resource_reference"]["resource_ref"] = B
    packet = prepare_review_packet(events)
    fragment = next(row for row in packet["eligible_candidates"]
                    if row["event_id"] == "e04")
    assert fragment["evidence_hops"] == 2
    assert len(fragment["candidate_items"]) == 2
    proposed = propose_automatic_review(events, packet)
    assert proposed["two_hop_proposal_count"] == 0
    assert proposed["abstention_counts"]["competing_or_incomplete_two_hop_candidates"] >= 1
    assert proposed["canonical_membership_unchanged"] is True


def test_two_hop_proposal_independent_accuracy_and_stale_evidence_refusal():
    events = _seeded_two_hop_proposal_events()
    packet = prepare_review_packet(events)
    truth = {
        "workflows": [{"event_ids": [f"e{n:02d}" for n in range(1, 8)]}],
        "noise_event_ids": [],
    }
    report = score_automatic_proposals(events, packet, truth)
    assert report["direct_proposal_count"] == 2
    assert report["two_hop_proposal_count"] == 1
    assert report["experimental_automatic_proposal_overlay"]["workflow_assignment_f1"] == 1.0
    assert (report["automatic_baseline"]["workflow_assignment_f1"]
            < report["experimental_automatic_proposal_overlay"]["workflow_assignment_f1"])
    assert report["production_linker_changed"] is False
    assert report["preregistered_family_holdout"] is False
    altered = copy.deepcopy(events)
    altered[3]["organization_id"] = "tenant-revoked"
    with pytest.raises(ValueError, match="stale|altered"):
        propose_automatic_review(altered, packet)
    forged = copy.deepcopy(packet)
    fragment = next(row for row in forged["eligible_candidates"]
                    if row["event_id"] == "e04")
    fragment["candidate_items"][0]["witness_event_path"][1] = "e99"
    with pytest.raises(ValueError, match="stale|altered"):
        propose_automatic_review(events, forged)


def test_seeded_two_hop_cli_propose_and_score_private_outputs(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    events = _seeded_two_hop_proposal_events()
    events_path = tmp_path / "events.json"
    packet_path = tmp_path / "packet.json"
    proposal_path = tmp_path / "proposals.json"
    score_path = tmp_path / "score.json"
    truth_path = tmp_path / "truth.json"
    events_path.write_text(json.dumps(events))
    truth_path.write_text(json.dumps({
        "workflows": [{"event_ids": [f"e{n:02d}" for n in range(1, 8)]}],
        "noise_event_ids": [],
    }))
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "scripts.reconstruction_review",
                               *args], cwd=root, capture_output=True, text=True, timeout=40)
    prepared = run("prepare", "--events", str(events_path), "--output", str(packet_path))
    assert prepared.returncode == 0, prepared.stderr
    proposed = run("propose", "--events", str(events_path), "--packet", str(packet_path),
                   "--output", str(proposal_path))
    assert proposed.returncode == 0, proposed.stderr
    assert json.loads(proposal_path.read_text())["two_hop_proposal_count"] == 1
    scored = run("score-proposals", "--events", str(events_path), "--packet", str(packet_path),
                 "--truth", str(truth_path), "--output", str(score_path))
    assert scored.returncode == 0, scored.stderr
    assert json.loads(score_path.read_text())["two_hop_proposal_count"] == 1
    for output in (proposal_path, score_path):
        for private in ("SECRET", "PRIVATE", "private.example", A):
            assert private not in output.read_text()
        if os.name != "nt":
            assert output.stat().st_mode & 0o077 == 0
