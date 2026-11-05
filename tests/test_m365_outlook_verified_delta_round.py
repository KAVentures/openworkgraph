"""Acceptance tests for consented live preflight-to-delta orchestration."""
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_verified_delta_round as flow


def _args(tmp_path):
    return dict(
        tenant="tenant", mailbox="mailbox", folder="folder",
        expected_address="person@example.org", grant_file=tmp_path / "grant.json",
        identity_report=tmp_path / "mailbox-identity-1.json",
        folder_report=tmp_path / "folder-preflight-1.json",
        csv_output=tmp_path / "round.csv",
        candidate_output=tmp_path / "round.json", token="test-token",
        consent_graph_read=True, consent_private_reports=True,
        consent_delta_export=True,
    )


def test_order_and_consent_propagation(tmp_path):
    args = _args(tmp_path)
    observed = []

    def mailbox(**kw):
        observed.append(("mailbox", kw))
        return {"mailbox_identifier_matched": True}

    def folder(**kw):
        observed.append(("folder", kw))
        return {"folder_id_matched": True}

    def delta(**kw):
        observed.append(("delta", kw))
        return {"complete_delta_round": True, "event_snapshots": 2,
                "removal_markers_for_review": 1, "provider_get_requests": 3}

    with patch.object(flow, "_delta_private_parent"), patch.object(flow, "_grant") as grant, \
         patch.object(flow, "mailbox_preflight", side_effect=mailbox), \
         patch.object(flow, "folder_preflight", side_effect=folder), \
         patch.object(flow, "export_delta", side_effect=delta):
        result = flow.verified_delta_round(**args)
    assert [stage for stage, _ in observed] == ["mailbox", "folder", "delta"]
    assert grant.call_count == 3
    assert observed[2][1]["consent_read"] is True
    assert observed[2][1]["consent_export"] is True
    assert observed[2][1]["confirm_previous_import"] is False
    assert result["checkpoint_advanced"] is False
    assert result["automatic_owg_append"] is False
    assert result["tenant_id_verified"] is False


@pytest.mark.parametrize("missing", [
    "consent_graph_read", "consent_private_reports", "consent_delta_export",
])
def test_missing_consent_never_calls_provider(tmp_path, missing):
    args = _args(tmp_path)
    args[missing] = False
    with patch.object(flow, "mailbox_preflight") as mailbox:
        with pytest.raises(flow.VerifiedDeltaError):
            flow.verified_delta_round(**args)
        mailbox.assert_not_called()


@pytest.mark.parametrize("stage", ["mailbox", "folder"])
def test_failed_preflight_never_exports(tmp_path, stage):
    args = _args(tmp_path)
    with patch.object(flow, "_delta_private_parent"), patch.object(flow, "_grant"), \
         patch.object(flow, "mailbox_preflight", return_value={
             "mailbox_identifier_matched": stage != "mailbox"
         }), patch.object(flow, "folder_preflight", return_value={
             "folder_id_matched": stage != "folder"
         }) as folder, patch.object(flow, "export_delta") as delta:
        with pytest.raises(flow.VerifiedDeltaError):
            flow.verified_delta_round(**args)
        delta.assert_not_called()
        if stage == "mailbox":
            folder.assert_not_called()


def test_revocation_between_stages_prevents_delta(tmp_path):
    args = _args(tmp_path)
    with patch.object(flow, "_delta_private_parent"), \
         patch.object(flow, "_grant", side_effect=[None, None, ValueError("revoked")]), \
         patch.object(flow, "mailbox_preflight", return_value={
             "mailbox_identifier_matched": True
         }), patch.object(flow, "folder_preflight", return_value={
             "folder_id_matched": True
         }), patch.object(flow, "export_delta") as delta:
        with pytest.raises(ValueError):
            flow.verified_delta_round(**args)
        delta.assert_not_called()


def test_existing_artifact_rejected_before_network(tmp_path):
    args = _args(tmp_path)
    Path(args["candidate_output"]).write_text("existing")
    with patch.object(flow, "mailbox_preflight") as mailbox:
        with pytest.raises(flow.VerifiedDeltaError):
            flow.verified_delta_round(**args)
        mailbox.assert_not_called()
