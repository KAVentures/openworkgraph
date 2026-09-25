from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server import policy_source_drift as drift
from server import policy_sources as ps


def _manifest(*, version: str = "1", source_ref: str = "file:///private/sop.json", forbidden: bool = False) -> dict:
    rules = [
        {
            "rule_id": "approval-before-submit",
            "type": "required_predecessor",
            "required_before": "approval_received:success",
            "trigger_step": "action:submit",
        }
    ]
    if forbidden:
        rules.append({"rule_id": "no-cancel", "type": "forbidden_step", "step": "error:cancelled"})
    return {
        "schema_version": "1.0",
        "policies": [
            {
                "policy_id": "email-approval",
                "version": version,
                "status": "active",
                "family_key": "human:email.send",
                "source_type": "repository_policy",
                "source_ref": source_ref,
                "rules": rules,
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "active": tmp_path / "policy" / "declared.json",
        "proposals": tmp_path / "proposals",
        "history": tmp_path / "history",
        "sources": tmp_path / "sources.json",
        "state": tmp_path / "source-state.json",
        "receipts": tmp_path / "source-receipts",
    }
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(paths["active"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(paths["proposals"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(paths["history"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCES_FILE", str(paths["sources"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCE_STATE", str(paths["state"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCE_RECEIPT_DIR", str(paths["receipts"]))
    return paths


def _local_config(source_id: str, path: Path) -> dict:
    return {
        "schema_version": "1.0",
        "sources": [{"source_id": source_id, "type": "local_file", "path": str(path)}],
    }


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_drift_inspection_is_read_only_and_reports_missing_proposal(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    active_before = paths["active"].read_bytes()
    source = tmp_path / "authoritative" / "policy.json"
    _write_json(source, _manifest(version="2", source_ref="file:///secret/current-sop.json", forbidden=True))
    _write_json(paths["sources"], _local_config("email-policy", source))

    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "drifted_no_proposal"
    assert item["active_policy_stale_relative_to_source"] is True
    assert item["semantic_change_count"] > 0
    assert item["proposal_available"] is False
    assert item["proposal_id"] is None
    assert report["drifted_count"] == 1
    assert report["read_only"] is True
    assert report["automatic_activation"] is False
    assert report["remote_fetch_performed"] is False

    assert paths["active"].read_bytes() == active_before
    assert not paths["proposals"].exists()
    assert not paths["receipts"].exists()
    assert not paths["state"].exists()
    serialized = json.dumps(report)
    assert str(source) not in serialized
    assert "file:///secret/current-sop.json" not in serialized


def test_sync_prepares_proposal_and_drift_report_links_it_without_activation(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    active_before = paths["active"].read_bytes()
    source = tmp_path / "policy.json"
    _write_json(source, _manifest(version="2", forbidden=True))
    _write_json(paths["sources"], _local_config("email-policy", source))

    prepared = ps.sync_policy_sources()
    proposal_id = prepared["results"][0]["proposal_id"]
    assert proposal_id
    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "drifted_proposal_ready"
    assert item["proposal_available"] is True
    assert item["proposal_id"] == proposal_id
    assert item["proposal_candidate_manifest_sha256"] == prepared["results"][0]["candidate_manifest_sha256"]
    assert paths["active"].read_bytes() == active_before


def test_semantically_equal_source_is_in_sync_without_requiring_receipt(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    payload = _manifest(version="1")
    _write_json(paths["active"], payload)
    source = tmp_path / "policy.json"
    source.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    _write_json(paths["sources"], _local_config("email-policy", source))

    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "in_sync"
    assert item["active_policy_stale_relative_to_source"] is False
    assert item["semantic_change_count"] == 0
    assert item["proposal_available"] is False
    assert report["in_sync_count"] == 1
    assert report["active_policy_provenance_known"] is False


def test_active_policy_origin_receipt_is_recovered_and_source_advance_is_separate(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    source = tmp_path / "policy.json"
    _write_json(source, _manifest(version="2"))
    _write_json(paths["sources"], _local_config("email-policy", source))

    first = ps.sync_policy_sources()
    first_result = first["results"][0]
    candidate_path = paths["proposals"] / f"{first_result['proposal_id']}.candidate.json"
    paths["active"].parent.mkdir(parents=True, exist_ok=True)
    paths["active"].write_bytes(candidate_path.read_bytes())

    in_sync = drift.policy_source_drift_status()
    item = in_sync["sources"][0]
    assert item["status"] == "in_sync"
    assert in_sync["active_policy_provenance_known"] is True
    assert item["active_origin_provenance_known_for_source"] is True
    assert item["source_advanced_since_active_origin"] is False
    assert item["active_origin_provenance"][0]["receipt_id"] == first_result["receipt_id"]

    _write_json(source, _manifest(version="3", forbidden=True))
    advanced = drift.policy_source_drift_status()
    advanced_item = advanced["sources"][0]
    assert advanced_item["status"] == "drifted_no_proposal"
    assert advanced_item["source_advanced_since_active_origin"] is True
    assert advanced_item["active_origin_provenance_known_for_source"] is True


def test_git_drift_uses_committed_head_and_reports_commit_advance(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))

    repo = tmp_path / "policy-repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.name", "OpenWorkGraph Test", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    policy_file = repo / "policies" / "openworkgraph.json"
    _write_json(policy_file, _manifest(version="2"))
    _git("add", "policies/openworkgraph.json", cwd=repo)
    _git("commit", "-m", "policy v2", cwd=repo)
    first_commit = _git("rev-parse", "HEAD", cwd=repo).lower()
    _write_json(paths["sources"], {
        "schema_version": "1.0",
        "sources": [{
            "source_id": "repo-policy",
            "type": "git_file",
            "repo_root": str(repo),
            "relative_path": "policies/openworkgraph.json",
        }],
    })

    first = ps.sync_policy_sources()
    first_result = first["results"][0]
    candidate_path = paths["proposals"] / f"{first_result['proposal_id']}.candidate.json"
    paths["active"].write_bytes(candidate_path.read_bytes())

    _write_json(policy_file, _manifest(version="3", forbidden=True))
    _git("add", "policies/openworkgraph.json", cwd=repo)
    _git("commit", "-m", "policy v3", cwd=repo)
    second_commit = _git("rev-parse", "HEAD", cwd=repo).lower()
    assert second_commit != first_commit

    # A dirty worktree edit must not replace the committed authoritative source.
    _write_json(policy_file, _manifest(version="4", source_ref="repo://dirty/not-authoritative.json"))
    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "drifted_no_proposal"
    assert item["current_source_provenance"]["git_commit_sha"] == second_commit
    assert item["current_source_provenance"]["working_tree_differs_from_committed_source"] is True
    assert item["current_source_provenance"]["committed_content_only"] is True
    assert item["active_origin_provenance"][0]["git_commit_sha"] == first_commit
    assert item["source_advanced_since_active_origin"] is True
    assert "repo://dirty/not-authoritative.json" not in json.dumps(report)
    assert str(repo) not in json.dumps(report)


def test_tampered_receipt_degrades_provenance_confidence_without_hiding_semantic_status(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    source = tmp_path / "policy.json"
    _write_json(source, _manifest(version="2"))
    _write_json(paths["sources"], _local_config("email-policy", source))
    prepared = ps.sync_policy_sources()
    result = prepared["results"][0]
    candidate_path = paths["proposals"] / f"{result['proposal_id']}.candidate.json"
    paths["active"].write_bytes(candidate_path.read_bytes())

    receipt_path = paths["receipts"] / f"{result['receipt_id']}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["source_sha256"] = "0" * 64
    _write_json(receipt_path, receipt)

    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "in_sync"
    assert report["receipt_integrity_ok"] is False
    assert report["invalid_receipt_count"] == 1
    assert report["active_policy_provenance_known"] is False
    assert item["active_origin_provenance_known_for_source"] is False


def test_unavailable_source_is_unknown_not_drift(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    missing = tmp_path / "missing.json"
    _write_json(paths["sources"], _local_config("missing-source", missing))

    report = drift.policy_source_drift_status()
    item = report["sources"][0]
    assert item["status"] == "source_unavailable"
    assert item["active_policy_stale_relative_to_source"] is None
    assert item["semantic_change_count"] is None
    assert report["source_unavailable_count"] == 1
    assert report["drifted_count"] == 0


def test_no_source_configuration_is_valid_empty_read_only_state(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    report = drift.policy_source_drift_status()
    assert report["config_present"] is False
    assert report["source_count"] == 0
    assert report["sources"] == []
    assert report["read_only"] is True
    assert report["drifted_count"] == 0
