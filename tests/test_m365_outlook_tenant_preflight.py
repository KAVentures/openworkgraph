"""Live tenant preflight parser and verified-round sequencing acceptance."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_tenant_preflight as tenant
from server import m365_outlook_verified_delta_round as round_module

GUID = "11111111-2222-3333-4444-555555555555"


def test_exact_tenant_only():
    assert tenant._parse(json.dumps({"value": [{"id": GUID.upper()}]}).encode(), GUID) == GUID
    for value in (
        {"value": []}, {"value": [{"id": GUID}, {"id": GUID}]},
        {"value": [{"id": "aaaaaaaa-2222-3333-4444-555555555555"}]},
        {"value": [{"id": GUID, "displayName": "unwanted"}]},
        {"value": [{"id": GUID}], "nextLink": "unexpected"},
    ):
        with pytest.raises(tenant.TenantError):
            tenant._parse(json.dumps(value).encode(), GUID)
    with pytest.raises(tenant.TenantError):
        tenant._parse(b'{"value":[],"value":[]}', GUID)
    with pytest.raises(tenant.TenantError):
        tenant._parse(b"x" * (tenant.MAX_BYTES + 1), GUID)


def test_missing_tenant_consent_refuses_before_any_provider_call(tmp_path):
    with patch.object(round_module, "mailbox_preflight") as mailbox:
        with pytest.raises(round_module.VerifiedDeltaError):
            round_module.verified_delta_round(
                tenant="t", mailbox="m", folder="f", expected_address="a@example.org",
                grant_file=tmp_path / "grant.json",
                identity_report=tmp_path / "mailbox-identity-1.json",
                folder_report=tmp_path / "folder-preflight-1.json",
                csv_output=tmp_path / "delta.csv", candidate_output=tmp_path / "delta.json",
                token="abc", consent_graph_read=True,
                consent_private_reports=True, consent_delta_export=True,
                expected_tenant_id=GUID, tenant_report=tmp_path / "tenant-preflight-1.json")
    mailbox.assert_not_called()


def test_tenant_gate_precedes_mailbox_folder_delta(tmp_path):
    calls = []
    def checked(**kwargs):
        calls.append("tenant")
        assert kwargs["expected_tenant_id"] == GUID
        return {"tenant_id_verified": True}
    def mailbox(**kwargs):
        calls.append("mailbox")
        return {"mailbox_identifier_matched": True}
    def folder(**kwargs):
        calls.append("folder")
        return {"folder_id_matched": True}
    def delta(**kwargs):
        calls.append("delta")
        return {"complete_delta_round": True, "event_snapshots": 0,
                "removal_markers_for_review": 0, "provider_get_requests": 1}
    with (patch.object(round_module, "_delta_private_parent"),
          patch.object(round_module, "_grant"),
          patch.object(round_module, "tenant_preflight", side_effect=checked),
          patch.object(round_module, "mailbox_preflight", side_effect=mailbox),
          patch.object(round_module, "folder_preflight", side_effect=folder),
          patch.object(round_module, "export_delta", side_effect=delta)):
        result = round_module.verified_delta_round(
            tenant="t", mailbox="m", folder="f", expected_address="a@example.org",
            grant_file=tmp_path / "grant.json",
            identity_report=tmp_path / "mailbox-identity-1.json",
            folder_report=tmp_path / "folder-preflight-1.json",
            csv_output=tmp_path / "delta.csv", candidate_output=tmp_path / "delta.json",
            token="abc", consent_graph_read=True,
            consent_private_reports=True, consent_delta_export=True,
            expected_tenant_id=GUID, tenant_report=tmp_path / "tenant-preflight-1.json",
            consent_tenant_lookup=True)
    assert calls == ["tenant", "mailbox", "folder", "delta"]
    assert result["tenant_id_verified"] is True


def test_mismatch_never_reads_delta(tmp_path):
    with (patch.object(round_module, "_delta_private_parent"),
          patch.object(round_module, "_grant"),
          patch.object(round_module, "tenant_preflight", side_effect=tenant.TenantError("mismatch")),
          patch.object(round_module, "export_delta") as delta):
        with pytest.raises(tenant.TenantError):
            round_module.verified_delta_round(
                tenant="t", mailbox="m", folder="f", expected_address="a@example.org",
                grant_file=tmp_path / "grant.json",
                identity_report=tmp_path / "mailbox-identity-1.json",
                folder_report=tmp_path / "folder-preflight-1.json",
                csv_output=tmp_path / "delta.csv", candidate_output=tmp_path / "delta.json",
                token="abc", consent_graph_read=True,
                consent_private_reports=True, consent_delta_export=True,
                expected_tenant_id=GUID, tenant_report=tmp_path / "tenant-preflight-1.json",
                consent_tenant_lookup=True)
    delta.assert_not_called()
