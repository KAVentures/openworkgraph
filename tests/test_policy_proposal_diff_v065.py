from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import policy_proposals as pp
from server.policy_proposals import PolicyProposalError


def _manifest(*, source_ref: str, forbidden: bool = False) -> dict:
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
                "version": "1",
                "status": "active",
                "family_key": "human:email.send",
                "source_type": "manual_sop",
                "source_ref": source_ref,
                "rules": rules,
            }
        ],
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _env(monkeypatch, tmp_path: Path):
    active = tmp_path / "active.json"
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(tmp_path / "proposals"))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(tmp_path / "history"))
    return active


def test_modified_diff_exposes_privacy_safe_rules_and_provenance_hash(monkeypatch, tmp_path):
    active = _env(monkeypatch, tmp_path)
    _write(active, _manifest(source_ref="file:///private/current.md"))
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(source_ref="file:///private/new.md", forbidden=True))

    proposal = pp.create_policy_proposal(candidate)
    diff = proposal["current_diff"]
    assert diff["change_count"] == 1
    assert not diff["added"] and not diff["removed"]
    modified = diff["modified"][0]
    assert modified["before"]["source_ref_hash"] != modified["after"]["source_ref_hash"]
    assert len(modified["before"]["rules"]) == 1
    assert len(modified["after"]["rules"]) == 2
    blob = json.dumps(diff)
    assert "file:///private/current.md" not in blob
    assert "file:///private/new.md" not in blob


def test_semantic_noop_candidate_is_rejected(monkeypatch, tmp_path):
    active = _env(monkeypatch, tmp_path)
    payload = _manifest(source_ref="file:///private/current.md")
    _write(active, payload)
    candidate = tmp_path / "candidate.json"
    _write(candidate, payload)

    with pytest.raises(PolicyProposalError, match="no policy changes"):
        pp.create_policy_proposal(candidate)
    assert not list((tmp_path / "proposals").glob("*.candidate.json"))
    assert not list((tmp_path / "proposals").glob("*.proposal.json"))
