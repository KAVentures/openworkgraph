from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import policy_proposals as pp
from server.policy_proposals import PolicyProposalError


def _manifest(*, version: str = "1", source_ref: str = "file:///private/company-sop.md", forbidden: bool = False) -> dict:
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
                "source_type": "manual_sop",
                "source_ref": source_ref,
                "rules": rules,
            }
        ],
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    active = tmp_path / "policy" / "declared.json"
    proposals = tmp_path / "proposals"
    history = tmp_path / "history"
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(proposals))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(history))
    return active, proposals, history


def test_proposal_is_privacy_minimized_and_deterministic(monkeypatch, tmp_path):
    active, _proposals, _history = _env(monkeypatch, tmp_path)
    _write(active, _manifest(version="1", source_ref="file:///secret/current.md"))
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="2", source_ref="file:///secret/future.md", forbidden=True))

    first = pp.create_policy_proposal(candidate)
    second = pp.create_policy_proposal(candidate)
    assert first["proposal_id"] == second["proposal_id"]
    assert first["stale"] is False
    assert first["current_diff"]["change_count"] == 2
    blob = json.dumps(first)
    assert "file:///secret/current.md" not in blob
    assert "file:///secret/future.md" not in blob
    assert first["candidate"]["policies"][0]["source_ref_present"] is True
    assert first["activation"] == {
        "network_write_available": False,
        "mcp_write_available": False,
        "interactive_local_apply_required": True,
    }


def test_candidate_tampering_is_detected(monkeypatch, tmp_path):
    _active, proposals, _history = _env(monkeypatch, tmp_path)
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="1"))
    proposal = pp.create_policy_proposal(candidate)
    candidate_path = proposals / f"{proposal['proposal_id']}.candidate.json"
    candidate_path.write_text(candidate_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(PolicyProposalError, match="digest mismatch"):
        pp.load_policy_proposal(proposal["proposal_id"])


def test_stale_proposal_refuses_activation(monkeypatch, tmp_path):
    active, _proposals, _history = _env(monkeypatch, tmp_path)
    _write(active, _manifest(version="1"))
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="2"))
    proposal = pp.create_policy_proposal(candidate)

    _write(active, _manifest(version="3"))
    review = pp.load_policy_proposal(proposal["proposal_id"])
    assert review["stale"] is True
    monkeypatch.setattr(pp, "_local_tty_available", lambda: True)
    with pytest.raises(PolicyProposalError, match="stale"):
        pp.apply_policy_proposal(proposal["proposal_id"], prompt=lambda _msg: "anything")


def test_noninteractive_or_wrong_confirmation_never_changes_policy(monkeypatch, tmp_path):
    active, _proposals, _history = _env(monkeypatch, tmp_path)
    _write(active, _manifest(version="1"))
    before = active.read_bytes()
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="2"))
    proposal = pp.create_policy_proposal(candidate)

    monkeypatch.setattr(pp, "_local_tty_available", lambda: False)
    with pytest.raises(PolicyProposalError, match="interactive local terminal"):
        pp.apply_policy_proposal(proposal["proposal_id"])
    assert active.read_bytes() == before

    monkeypatch.setattr(pp, "_local_tty_available", lambda: True)
    with pytest.raises(PolicyProposalError, match="confirmation did not match"):
        pp.apply_policy_proposal(proposal["proposal_id"], prompt=lambda _msg: "APPLY something-else")
    assert active.read_bytes() == before


def test_interactive_apply_is_atomic_archived_and_audited_without_source_refs(monkeypatch, tmp_path):
    active, _proposals, history = _env(monkeypatch, tmp_path)
    _write(active, _manifest(version="1", source_ref="file:///secret/current.md"))
    old_raw = active.read_bytes()
    old_sha = pp._sha256(old_raw)
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="2", source_ref="file:///secret/future.md", forbidden=True))
    proposal = pp.create_policy_proposal(candidate)
    expected = pp._confirmation_text(proposal)
    monkeypatch.setattr(pp, "_local_tty_available", lambda: True)

    result = pp.apply_policy_proposal(
        proposal["proposal_id"],
        prompt=lambda _msg: expected,
    )
    assert result["status"] == "applied"
    assert result["previous_manifest_sha256"] == old_sha
    assert pp._sha256(active.read_bytes()) == proposal["candidate_manifest_sha256"]
    assert (history / f"manifest-{old_sha}.json").read_bytes() == old_raw
    assert (history / f"manifest-{proposal['candidate_manifest_sha256']}.json").read_bytes() == active.read_bytes()

    audit_text = (history / "policy_admin_audit.jsonl").read_text(encoding="utf-8")
    assert proposal["proposal_id"] in audit_text
    assert "file:///secret/current.md" not in audit_text
    assert "file:///secret/future.md" not in audit_text


def test_invalid_candidate_and_invalid_current_manifest_fail_closed(monkeypatch, tmp_path):
    active, _proposals, _history = _env(monkeypatch, tmp_path)
    invalid_candidate = tmp_path / "invalid.json"
    _write(invalid_candidate, {"schema_version": "1.0", "policies": [{"bad": True}]})
    with pytest.raises(PolicyProposalError):
        pp.create_policy_proposal(invalid_candidate)

    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("{broken", encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    _write(candidate, _manifest(version="1"))
    with pytest.raises(PolicyProposalError):
        pp.create_policy_proposal(candidate)


def test_no_policy_mutation_route_is_exposed(monkeypatch, tmp_path):
    active, _proposals, _history = _env(monkeypatch, tmp_path)
    _write(active, _manifest(version="1"))
    import server.secure_app as secure_app

    client = TestClient(secure_app.app)
    schema = client.get("/openapi.json").json()
    policy_paths = {
        path: set(methods)
        for path, methods in schema["paths"].items()
        if "policy" in path or "policies" in path
    }
    assert policy_paths
    for methods in policy_paths.values():
        assert not ({"post", "put", "patch", "delete"} & methods)
