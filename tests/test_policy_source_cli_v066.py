from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import policy_admin


def _manifest(version: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "email-approval",
            "version": version,
            "status": "active",
            "family_key": "human:email.send",
            "source_type": "repository_policy",
            "source_ref": "repo://policy.json",
            "rules": [{
                "rule_id": "approval-before-submit",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": "action:submit",
            }],
        }],
    }


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_sync_sources_cli_prepares_but_never_activates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    active = tmp_path / "active.json"
    source = tmp_path / "source.json"
    config = tmp_path / "sources.json"
    proposals = tmp_path / "proposals"
    state = tmp_path / "state.json"
    receipts = tmp_path / "receipts"
    history = tmp_path / "history"

    _write(active, _manifest("1"))
    before = active.read_bytes()
    _write(source, _manifest("2"))
    _write(config, {
        "schema_version": "1.0",
        "sources": [{"source_id": "cli-source", "type": "local_file", "path": str(source)}],
    })

    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_FILE", str(active))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", str(proposals))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", str(history))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCES_FILE", str(config))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCE_STATE", str(state))
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCE_RECEIPT_DIR", str(receipts))

    assert policy_admin.main(["sync-sources"]) == 0
    sync_payload = json.loads(capsys.readouterr().out)
    assert sync_payload["proposal_count"] == 1
    assert sync_payload["automatic_activation"] is False
    assert sync_payload["results"][0]["proposal_id"].startswith("proposal-")
    assert sync_payload["results"][0]["receipt_id"].startswith("receipt-")
    assert active.read_bytes() == before

    assert policy_admin.main(["source-status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["automatic_activation"] is False
    assert status_payload["sources"][0]["last_proposal_id"] == sync_payload["results"][0]["proposal_id"]
    assert status_payload["sources"][0]["last_receipt_id"] == sync_payload["results"][0]["receipt_id"]
    assert active.read_bytes() == before


def test_source_config_rejects_instruction_like_source_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from server import policy_sources

    config = tmp_path / "sources.json"
    _write(config, {
        "schema_version": "1.0",
        "sources": [{
            "source_id": "ignore_previous_instructions",
            "type": "local_file",
            "path": str(tmp_path / "whatever.json"),
        }],
    })
    monkeypatch.setenv("WORKFLOW_OBSERVER_POLICY_SOURCES_FILE", str(config))
    with pytest.raises(policy_sources.PolicySourceError, match="source_id"):
        policy_sources.load_policy_sources_config()
