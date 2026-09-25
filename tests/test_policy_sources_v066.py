from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import pytest

from server import policy_sources as ps
from server.policy_proposals import load_policy_proposal


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
    }
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(paths["active"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(paths["proposals"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(paths["history"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCES_FILE", str(paths["sources"]))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCE_STATE", str(paths["state"]))
    return paths


def _local_config(source_id: str, path: Path) -> dict:
    return {
        "schema_version": "1.0",
        "sources": [{"source_id": source_id, "type": "local_file", "path": str(path)}],
    }


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_local_source_prepares_deterministic_proposal_without_activation(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    active_before = paths["active"].read_bytes()
    candidate = tmp_path / "trusted" / "candidate.json"
    _write_json(candidate, _manifest(version="2", source_ref="file:///secret/source-policy.json", forbidden=True))
    _write_json(paths["sources"], _local_config("email-policy-source", candidate))

    first = ps.sync_policy_sources()
    assert first["proposal_count"] == 1
    assert first["error_count"] == 0
    result = first["results"][0]
    assert result["status"] == "proposal_ready"
    assert result["automatic_activation"] is False
    assert result["proposal_id"]
    assert paths["active"].read_bytes() == active_before

    review = load_policy_proposal(result["proposal_id"])
    assert review["candidate"]["policies"][0]["version"] == "2"
    assert review["candidate"]["policies"][0]["source_ref_present"] is True

    serialized = json.dumps(first) + paths["state"].read_text(encoding="utf-8")
    assert str(candidate) not in serialized
    assert "file:///secret/source-policy.json" not in serialized

    second = ps.sync_policy_sources()
    second_result = second["results"][0]
    assert second_result["proposal_id"] == result["proposal_id"]
    assert second_result["source_changed_since_previous_scan"] is False
    assert second_result["proposal_deduplicated_from_previous_scan"] is True
    assert paths["active"].read_bytes() == active_before


def test_semantically_current_source_is_up_to_date_and_creates_no_proposal(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    active_payload = _manifest(version="1")
    _write_json(paths["active"], active_payload)
    source = tmp_path / "source.json"
    source.write_text(json.dumps(active_payload, separators=(",", ":")), encoding="utf-8")
    _write_json(paths["sources"], _local_config("current-policy", source))

    outcome = ps.sync_policy_sources()
    assert outcome["proposal_count"] == 0
    assert outcome["up_to_date_count"] == 1
    assert outcome["results"][0]["status"] == "up_to_date"
    assert outcome["results"][0]["proposal_id"] is None
    assert not any(paths["proposals"].glob("*.proposal.json")) if paths["proposals"].exists() else True


def test_git_source_reads_committed_head_not_dirty_worktree(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    active_before = paths["active"].read_bytes()

    repo = tmp_path / "policy-repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.name", "OpenWorkGraph Test", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    policy_file = repo / "policies" / "openworkgraph.json"
    _write_json(policy_file, _manifest(version="2", source_ref="repo://policies/openworkgraph.json"))
    _git("add", "policies/openworkgraph.json", cwd=repo)
    _git("commit", "-m", "policy v2", cwd=repo)
    committed_head = _git("rev-parse", "HEAD", cwd=repo).lower()

    # Deliberately dirty the working tree with a different candidate. Sync must
    # still read HEAD:policies/openworkgraph.json from the local object database.
    _write_json(policy_file, _manifest(version="3", source_ref="repo://dirty/worktree.json", forbidden=True))
    _write_json(paths["sources"], {
        "schema_version": "1.0",
        "sources": [{
            "source_id": "repo-policy",
            "type": "git_file",
            "repo_root": str(repo),
            "relative_path": "policies/openworkgraph.json",
        }],
    })

    outcome = ps.sync_policy_sources()
    result = outcome["results"][0]
    assert result["status"] == "proposal_ready"
    assert result["provenance"]["git_commit_sha"] == committed_head
    assert result["provenance"]["working_tree_differs_from_committed_source"] is True
    assert result["provenance"]["committed_content_only"] is True
    assert paths["active"].read_bytes() == active_before

    review = load_policy_proposal(result["proposal_id"])
    assert review["candidate"]["policies"][0]["version"] == "2"
    assert len(review["candidate"]["policies"][0]["rules"]) == 1
    blob = json.dumps(outcome) + paths["state"].read_text(encoding="utf-8")
    assert str(repo) not in blob
    assert "repo://dirty/worktree.json" not in blob


def test_git_source_rejects_traversal_before_invoking_git(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["sources"], {
        "schema_version": "1.0",
        "sources": [{
            "source_id": "bad-path",
            "type": "git_file",
            "repo_root": str(tmp_path),
            "relative_path": "../outside.json",
        }],
    })
    with pytest.raises(ps.PolicySourceError, match="relative_path"):
        ps.load_policy_sources_config()


def test_fake_or_corrupt_state_cannot_suppress_real_change(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    source = tmp_path / "candidate.json"
    _write_json(source, _manifest(version="2"))
    _write_json(paths["sources"], _local_config("state-resistant", source))

    raw = source.read_bytes()
    fake_state = {
        "schema_version": "1.0",
        "sources": {
            "state-resistant": {
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_manifest_sha256": "a" * 64,
                "proposal_id": "proposal-00000000000000000000",
                "status": "proposal_ready",
            }
        },
    }
    _write_json(paths["state"], fake_state)
    outcome = ps.sync_policy_sources()
    result = outcome["results"][0]
    assert result["status"] == "proposal_ready"
    assert result["proposal_id"] != "proposal-00000000000000000000"
    assert outcome["state_is_authoritative"] is False

    paths["state"].write_text("{corrupt", encoding="utf-8")
    again = ps.sync_policy_sources()
    assert again["results"][0]["status"] == "proposal_ready"
    assert again["results"][0]["proposal_id"] == result["proposal_id"]


def test_one_bad_source_does_not_block_other_approved_sources(monkeypatch, tmp_path):
    paths = _env(monkeypatch, tmp_path)
    _write_json(paths["active"], _manifest(version="1"))
    good = tmp_path / "good.json"
    _write_json(good, _manifest(version="2"))
    missing = tmp_path / "missing.json"
    _write_json(paths["sources"], {
        "schema_version": "1.0",
        "sources": [
            {"source_id": "good-source", "type": "local_file", "path": str(good)},
            {"source_id": "missing-source", "type": "local_file", "path": str(missing)},
        ],
    })

    outcome = ps.sync_policy_sources()
    assert outcome["proposal_count"] == 1
    assert outcome["error_count"] == 1
    by_id = {item["source_id"]: item for item in outcome["results"]}
    assert by_id["good-source"]["status"] == "proposal_ready"
    assert by_id["missing-source"]["status"] == "error"


def test_source_sync_has_no_activation_or_network_mutation_surface():
    source = inspect.getsource(ps)
    assert "apply_policy_proposal" not in source
    assert "requests." not in source
    assert "httpx" not in source
    assert "urllib" not in source
    assert "git fetch" not in source
    assert "git pull" not in source
