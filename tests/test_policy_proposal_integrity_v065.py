from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import policy_proposals as pp
from server.policy_proposals import PolicyProposalError


def _manifest(version: str = "1") -> dict:
    return {
        "schema_version": "1.0",
        "policies": [
            {
                "policy_id": "email-approval",
                "version": version,
                "status": "active",
                "family_key": "human:email.send",
                "source_type": "manual_sop",
                "source_ref": "file:///private/policy.md",
                "rules": [
                    {
                        "rule_id": "approval-before-submit",
                        "type": "required_predecessor",
                        "required_before": "approval_received:success",
                        "trigger_step": "action:submit",
                    }
                ],
            }
        ],
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _setup(monkeypatch, tmp_path: Path):
    active = tmp_path / "active.json"
    proposals = tmp_path / "proposals"
    history = tmp_path / "history"
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(proposals))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(history))
    _write(active, _manifest("1"))
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest("2"))
    proposal = pp.create_policy_proposal(candidate)
    metadata = proposals / f"{proposal['proposal_id']}.proposal.json"
    return proposal, metadata


def test_base_digest_metadata_tampering_invalidates_proposal(monkeypatch, tmp_path):
    proposal, metadata = _setup(monkeypatch, tmp_path)
    raw = json.loads(metadata.read_text(encoding="utf-8"))
    raw["base_manifest_sha256"] = "0" * 64
    metadata.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PolicyProposalError, match="integrity mismatch"):
        pp.load_policy_proposal(proposal["proposal_id"])


def test_candidate_digest_metadata_tampering_invalidates_proposal(monkeypatch, tmp_path):
    proposal, metadata = _setup(monkeypatch, tmp_path)
    raw = json.loads(metadata.read_text(encoding="utf-8"))
    raw["candidate_manifest_sha256"] = "f" * 64
    metadata.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PolicyProposalError, match="integrity mismatch"):
        pp.load_policy_proposal(proposal["proposal_id"])


def test_stored_metadata_contains_no_policy_source_reference(monkeypatch, tmp_path):
    _proposal, metadata = _setup(monkeypatch, tmp_path)
    text = metadata.read_text(encoding="utf-8")
    assert "source_ref" not in text
    assert "file:///private/policy.md" not in text
    assert "activation" not in text
    assert "diff" not in text
