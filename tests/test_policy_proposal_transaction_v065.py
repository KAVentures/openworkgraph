from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import policy_proposals as pp
from server.policy_proposals import PolicyProposalError


def _manifest(version: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [
            {
                "policy_id": "email-approval",
                "version": version,
                "status": "active",
                "family_key": "human:email.send",
                "source_type": "manual_sop",
                "source_ref": f"file:///private/policy-{version}.md",
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


def test_corrupt_audit_rolls_back_replaced_active_manifest(monkeypatch, tmp_path):
    active = tmp_path / "active.json"
    proposals = tmp_path / "proposals"
    history = tmp_path / "history"
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(proposals))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(history))
    monkeypatch.setattr(pp, "_local_tty_available", lambda: True)

    _write(active, _manifest("1"))
    previous_raw = active.read_bytes()
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest("2"))
    proposal = pp.create_policy_proposal(candidate)
    expected = pp._confirmation_text(proposal)

    history.mkdir(parents=True, exist_ok=True)
    audit = history / "policy_admin_audit.jsonl"
    audit.write_text("this is not json\n", encoding="utf-8")

    with pytest.raises(PolicyProposalError, match="previous policy restored"):
        pp.apply_policy_proposal(proposal["proposal_id"], prompt=lambda _msg: expected)

    assert active.read_bytes() == previous_raw
    assert audit.read_text(encoding="utf-8") == "this is not json\n"
    assert pp.load_policy_proposal(proposal["proposal_id"])["stale"] is False


def test_failed_first_activation_removes_candidate_from_active_path(monkeypatch, tmp_path):
    active = tmp_path / "active.json"
    proposals = tmp_path / "proposals"
    history = tmp_path / "history"
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(proposals))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(history))
    monkeypatch.setattr(pp, "_local_tty_available", lambda: True)

    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest("1"))
    proposal = pp.create_policy_proposal(candidate)
    expected = pp._confirmation_text(proposal)

    history.mkdir(parents=True, exist_ok=True)
    (history / "policy_admin_audit.jsonl").write_text("broken\n", encoding="utf-8")

    with pytest.raises(PolicyProposalError, match="previous policy restored"):
        pp.apply_policy_proposal(proposal["proposal_id"], prompt=lambda _msg: expected)

    assert not active.exists()
    assert pp.load_policy_proposal(proposal["proposal_id"])["stale"] is False
