"""Real-linker acceptance for bounded noncanonical interruption/resumption graphs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from server.reconstruction_review_overlay import prepare_review_packet
from server.reconstruction_resumption import reconstruct_resumptions
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n: int, ref: str | None = None) -> dict:
    metadata = {"page": {"pathname": "/SECRET/medical-notes"}, "target": {"label": "PRIVATE"}}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
            "host": "private.example", "resolver_locator": "SECRET",
        }
    return {
        "event_id": f"e{n:03d}",
        "observed_at": f"2026-10-20T10:{n // 60:02d}:{n % 60:02d}+00:00",
        "app": "Excel", "source": "desktop", "event_type": "window_focus",
        "metadata": metadata, "organization_id": "tenant-a", "actor_id": "actor-1",
        "device_id": "device-1", "session_id": "session-1", "window_title": "PRIVATE",
    }


def reconstruct(events: list[dict]) -> dict:
    return reconstruct_resumptions(events, prepare_review_packet(events))


def test_real_linker_a_b_a_yields_one_resumption_without_merging():
    events = [event(1, A), event(2, A), event(3, B), event(4, B),
              event(5, A), event(6, A)]
    original = linked_work_items(events)
    result = reconstruct(events)
    assert result["resumption_count"] == 1
    assert result["episode_count"] == 3
    [edge] = result["resumption_edges"]
    assert edge["from_episode_id"] == "episode_00001"
    assert edge["to_episode_id"] == "episode_00003"
    assert edge["intervening_episode_ids"] == ["episode_00002"]
    assert edge["interruption_classes"] == ["other_production_linked_item"]
    assert edge["other_case_observation_count"] == 2
    assert edge["unknown_observation_count"] == 0
    assert result["episodes"][0]["item_id"] == result["episodes"][2]["item_id"]
    assert result["episodes"][0]["event_ids"] == ["e001", "e002"]
    assert result["episodes"][2]["event_ids"] == ["e005", "e006"]
    assert linked_work_items(events) == original
    assert result["canonical_membership_unchanged"] is True
    assert result["case_identity_verified"] is False
    assert result["authorizes_business_action"] is False


def test_unknown_gap_is_distinct_from_competing_case_and_both_are_preserved():
    unknown = [event(1, A), event(2, A), event(3), event(4, A), event(5, A)]
    unknown[2]["app"] = "Slack"  # unknown surface, not a bracketed same-app record
    result = reconstruct(unknown)
    assert result["resumption_count"] == 1
    [edge] = result["resumption_edges"]
    assert edge["interruption_classes"] == ["unassigned_observations"]
    assert edge["unknown_observation_count"] == 1
    assert result["episodes"][1]["item_id"] is None

    mixed = [event(1, A), event(2, A), event(3, B), event(4, B),
             event(5), event(6, A), event(7, A)]
    mixed[4]["app"] = "Slack"
    result = reconstruct(mixed)
    assert result["resumption_count"] == 1
    [edge] = result["resumption_edges"]
    assert edge["interruption_classes"] == [
        "other_production_linked_item", "unassigned_observations",
    ]
    assert edge["intervening_episode_ids"] == ["episode_00002", "episode_00003"]
    assert edge["other_case_observation_count"] == 2
    assert edge["unknown_observation_count"] == 1


def test_scope_barrier_and_tied_time_refuse_resumption():
    base = [event(1, A), event(2, A), event(3, B), event(4, B),
            event(5, A), event(6, A)]
    foreign = copy.deepcopy(base)
    foreign[2]["organization_id"] = "other-tenant"
    result = reconstruct(foreign)
    assert result["resumption_count"] == 0
    assert result["abstention_counts"].get("untrusted_or_cross_scope_interruption", 0) >= 1

    tied = copy.deepcopy(base)
    tied[3]["observed_at"] = tied[2]["observed_at"]
    result = reconstruct(tied)
    assert result["resumption_count"] == 0
    assert result["abstention_counts"].get("nonmonotonic_or_tied_observations", 0) >= 1


def test_app_change_disclosed_without_guessing_case_identity():
    events = [event(1, A), event(2, A), event(3, B), event(4, B),
              event(5, A), event(6, A)]
    events[2]["app"] = "Browser"
    events[3]["app"] = "Browser"
    result = reconstruct(events)
    assert result["resumption_count"] == 1
    assert result["resumption_edges"][0]["surface_changed_within_span"] is True
    assert result["production_linked_event_count"] == 6
    assert result["experimental_proposed_event_count"] == 0
    assert result["capture_completeness_verified"] is False


def test_interruption_budget_abstains_instead_of_truncating():
    events = [event(1, A), event(2, A)]
    gap = [event(n) for n in range(3, 133)]
    for row in gap:
        row["app"] = "Slack"
    events.extend(gap)
    events.extend([event(133, A), event(134, A)])
    result = reconstruct(events)
    assert result["resumption_count"] == 0
    assert result["abstention_counts"].get("interruption_observation_budget_exceeded", 0) == 1
    assert result["admitted_observation_count"] == len(events)


def test_private_cli_create_only_stale_and_revoked_evidence_refusal(tmp_path: Path):
    events = [event(1, A), event(2, A), event(3, B), event(4, B),
              event(5, A), event(6, A)]
    source, packet_path, output = (tmp_path / name for name in
                                   ("events.json", "packet.json", "resumptions.json"))
    source.write_text(json.dumps(events))
    packet_path.write_text(json.dumps(prepare_review_packet(events)))
    root = Path(__file__).resolve().parents[1]

    def run(target: Path) -> subprocess.CompletedProcess:
        return subprocess.run([
            sys.executable, "-m", "scripts.reconstruction_resumption",
            "--events", str(source), "--packet", str(packet_path),
            "--output", str(target),
        ], cwd=root, capture_output=True, text=True, timeout=40)

    result = run(output)
    assert result.returncode == 0, result.stderr
    document = json.loads(output.read_text())
    assert document["resumption_count"] == 1
    for secret in (A, B, "SECRET", "PRIVATE", "private.example", "/medical-notes"):
        assert secret not in output.read_text()
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0
    assert run(output).returncode == 2
    events[4]["metadata"].pop("resource_reference")
    source.write_text(json.dumps(events))
    refused = tmp_path / "refused.json"
    assert run(refused).returncode == 2
    assert not refused.exists()
    source.write_text(json.dumps([event(1, A), event(2, A)]))
    assert run(refused).returncode == 2
    assert not refused.exists()


def test_multiple_independent_returns_form_one_source_anchored_chain():
    # A -> B -> A -> B -> A: two independently verified resumptions,
    # not a single collapsed work episode or a temporal guess.
    events = [event(n, A if n in (1, 2, 5, 6, 9, 10)
                    else B if n in (3, 4) else "owg:r:" + "c" * 32)
              for n in range(1, 11)]
    result = reconstruct(events)
    assert result["episode_count"] == 5
    assert result["resumption_count"] == 2
    assert result["resumption_chain_count"] == 1
    assert result["chained_resumption_edge_count"] == 2
    assert result["standalone_resumption_edge_count"] == 0
    [chain] = result["resumption_chains"]
    assert chain["episode_ids"] == [
        "episode_00001", "episode_00003", "episode_00005",
    ]
    assert chain["resumption_edge_indexes"] == [0, 1]
    assert chain["resumption_count"] == 2
    assert chain["intervening_observation_count"] == 4
    assert chain["other_case_observation_count"] == 4
    assert chain["unknown_observation_count"] == 0
    assert chain["interruption_classes"] == ["other_production_linked_item"]
    assert chain["all_links_independently_anchored"] is True
    assert chain["business_case_identity_verified"] is False
    assert result["episodes"][0]["event_ids"] == ["e001", "e002"]
    assert result["episodes"][2]["event_ids"] == ["e005", "e006"]
    assert result["episodes"][4]["event_ids"] == ["e009", "e010"]


def test_chain_refuses_to_bridge_unverified_second_interruption():
    events = [event(n, A if n in (1, 2, 5, 6, 9, 10)
                    else B if n in (3, 4) else "owg:r:" + "c" * 32)
              for n in range(1, 11)]
    # Tied timestamps in the second interruption invalidate only its
    # candidate return. The earlier verified edge remains standalone.
    events[7]["observed_at"] = events[6]["observed_at"]
    result = reconstruct(events)
    assert result["resumption_count"] == 1
    assert result["resumption_chain_count"] == 0
    assert result["resumption_chains"] == []
    assert result["chained_resumption_edge_count"] == 0
    assert result["standalone_resumption_edge_count"] == 1
    assert result["abstention_counts"].get(
        "nonmonotonic_or_tied_observations", 0
    ) >= 1


def test_chain_preserves_unknown_and_other_case_interruption_counts():
    events = [event(1, A), event(2, A), event(3, B), event(4, B),
              event(5, A), event(6, A), event(7), event(8, A), event(9, A)]
    events[6]["app"] = "Slack"
    result = reconstruct(events)
    assert result["resumption_count"] == 2
    assert result["resumption_chain_count"] == 1
    [chain] = result["resumption_chains"]
    assert chain["resumption_edge_indexes"] == [0, 1]
    assert chain["interruption_classes"] == [
        "other_production_linked_item", "unassigned_observations",
    ]
    assert chain["other_case_observation_count"] == 2
    assert chain["unknown_observation_count"] == 1
    assert chain["intervening_observation_count"] == 3
    assert chain["canonical_membership_unchanged"] is True
