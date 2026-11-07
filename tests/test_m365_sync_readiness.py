"""Focused refusal and successful operator readiness tests."""
import hashlib
import json
from unittest.mock import patch
import pytest
from server.m365_outlook_sync_readiness import readiness
from server.m365_outlook_owg_receipt import ReceiptError


def test_readiness_requires_consent():
    with pytest.raises(ReceiptError):
        readiness(tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
                  grant_file="unused", identity_report="unused", folder_report="unused",
                  ledger_dir="unused", log_id="log")


def test_readiness_matching_private_evidence():
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "upn_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        result = readiness(tenant="t", mailbox="m", folder="folder",
                           expected_address="m@example.com", grant_file="grant",
                           identity_report="identity", folder_report="folder-report",
                           ledger_dir="ledger", log_id="log", consent_local_read=True)
    assert result["status"] == "operator_review_ready"
    assert result["provider_requests"] == 0
    assert result["checkpoint_advanced"] is False


def test_readiness_rejects_untrusted_preflight():
    with (patch("server.m365_outlook_sync_readiness._scope", return_value="a" * 64),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[b'{"format":"wrong"}', b'{}'])):
        with pytest.raises(ReceiptError):
            readiness(tenant="t", mailbox="m", folder="f",
                      expected_address="m@example.com", grant_file="grant",
                      identity_report="identity", folder_report="folder-report",
                      ledger_dir="ledger", log_id="log", consent_local_read=True)


@pytest.mark.parametrize("generation", [1, 3, 0, -1, True, "2"])
def test_readiness_refuses_stale_or_invalid_checkpoint_generation(generation):
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "upn_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        with pytest.raises(ReceiptError, match="generation changed"):
            readiness(tenant="t", mailbox="m", folder="folder",
                      expected_address="m@example.com", grant_file="grant",
                      identity_report="identity", folder_report="folder-report",
                      ledger_dir="ledger", log_id="log", consent_local_read=True,
                      expected_generation=generation)


def test_readiness_accepts_exact_committed_generation():
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "mail_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        result = readiness(tenant="t", mailbox="m", folder="folder",
                           expected_address="m@example.com", grant_file="grant",
                           identity_report="identity", folder_report="folder-report",
                           ledger_dir="ledger", log_id="log", consent_local_read=True,
                           expected_generation=2)
    assert result["committed_generation"] == 2


def _extract_kwargs():
    return dict(expected_generation=2, tenant="t", mailbox="m", folder="f",
                expected_address="m@example.com", grant_file="grant",
                identity_report="identity", folder_report="folder-report",
                ledger_dir="ledger", log_id="log", confirm_log_id="log",
                previous_candidate="previous.json", previous_csv="previous.csv",
                receipt_file="receipt", journal_dir="journal",
                handoff_dir="handoff", successor_label="next",
                output="output", candidate="candidate", token="token",
                consent_local_read=True, consent_delta_read=True,
                consent_local_export=True, confirm_removals_reviewed=True)


@pytest.mark.parametrize("change", [
    {"expected_generation": None}, {"expected_generation": True},
    {"expected_generation": 0}, {"consent_local_read": False},
    {"consent_delta_read": False}, {"consent_local_export": False},
    {"confirm_removals_reviewed": False}, {"confirm_log_id": "other"},
])
def test_extract_after_readiness_refuses_before_any_delegate(change):
    from server.m365_outlook_sync_readiness import extract_after_readiness
    args = {**_extract_kwargs(), **change}
    with (patch("server.m365_outlook_sync_readiness.readiness") as ready,
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint") as extract):
        with pytest.raises(ReceiptError):
            extract_after_readiness(**args)
        ready.assert_not_called()
        extract.assert_not_called()


def test_extract_after_readiness_refuses_mismatched_preflight():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                side_effect=ReceiptError("Preflight evidence mismatch")),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint") as extract):
        with pytest.raises(ReceiptError, match="Preflight evidence mismatch"):
            extract_after_readiness(**_extract_kwargs())
        extract.assert_not_called()


def test_extract_after_readiness_requires_reviewable_delegate_result():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "refused"}) as extract):
        with pytest.raises(ReceiptError, match="not ready for review"):
            extract_after_readiness(**_extract_kwargs())
        assert extract.call_args.kwargs["consent_delta_read"] is True
        assert extract.call_args.kwargs["consent_local_export"] is True


def test_extract_after_readiness_preserves_manual_review_boundary():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}) as ready,
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": 2,
                              "event_snapshots": 3,
                              "removal_markers_for_review": 1,
                              "checkpoint_advanced": False,
                              "owg_append_performed": False}) as extract):
        result = extract_after_readiness(**_extract_kwargs())
    assert ready.call_args.kwargs["expected_generation"] == 2
    assert extract.call_args.kwargs["log_id"] == "log"
    assert result["status"] == "review_required"
    assert result["checkpoint_advanced"] is False
    assert result["owg_append_performed"] is False
    assert result["automatic_retry_allowed"] is False


@pytest.mark.parametrize("generation", [None, 1, 3, True, "2"])
def test_extract_after_readiness_rejects_changed_delegate_generation(generation):
    """A concurrent checkpoint advance must never be labelled approved."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": generation,
                              "event_snapshots": 3,
                              "removal_markers_for_review": 0})):
        with pytest.raises(ReceiptError, match="generation changed"):
            extract_after_readiness(**_extract_kwargs())


@pytest.mark.parametrize("bad", [
    {"event_snapshots": None},
    {"event_snapshots": True},
    {"event_snapshots": -1},
    {"removal_markers_for_review": None},
    {"removal_markers_for_review": -1},
    {"removal_markers_for_review": "1"},
    {"checkpoint_advanced": True},
    {"checkpoint_advanced": None},
    {"owg_append_performed": True},
    {"owg_append_performed": None},
])
def test_extract_after_readiness_refuses_unsafe_review_receipt(bad):
    """A malformed delegate receipt must never be presented as approved."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    result = {"status": "review_required",
              "committed_predecessor_generation": 2,
              "event_snapshots": 3, "removal_markers_for_review": 0,
              "checkpoint_advanced": False, "owg_append_performed": False}
    result.update(bad)
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value=result)):
        with pytest.raises(ReceiptError, match="Unsafe or incomplete"):
            extract_after_readiness(**_extract_kwargs())
