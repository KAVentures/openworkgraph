"""First-cycle phase and safety acceptance against production composition."""
from unittest.mock import patch

import pytest

from server import m365_outlook_first_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError, UncertainReceipt


def test_extract_requires_all_separate_consents():
    for missing in ("consent_graph_read", "consent_private_reports", "consent_delta_export"):
        consents = dict(consent_graph_read=True, consent_private_reports=True,
                        consent_delta_export=True)
        consents[missing] = False
        with patch.object(cycle, "verified_delta_round") as reader:
            with pytest.raises(ReceiptError):
                cycle.extract_first(**consents)
            reader.assert_not_called()


def test_extract_calls_existing_real_reader_and_requires_review():
    expected = dict(status="verified_export_review_ready", complete_delta_round=True,
                    checkpoint_advanced=False, automatic_owg_append=False,
                    event_snapshots=3, removal_markers_for_review=1,
                    tenant_id_verified=True)
    with patch.object(cycle, "verified_delta_round", return_value=expected) as reader:
        result = cycle.extract_first(
            consent_graph_read=True, consent_private_reports=True,
            consent_delta_export=True, tenant="t", mailbox="m", folder="f")
    assert result["status"] == "review_required"
    assert result["tenant_id_verified"] is True
    assert result["owg_append_performed"] is False
    reader.assert_called_once()
    assert reader.call_args.kwargs["consent_graph_read"] is True


@pytest.mark.parametrize("change", [
    {"complete_delta_round": False}, {"checkpoint_advanced": True},
    {"automatic_owg_append": True}, {"status": "refused"},
])
def test_extract_rejects_unsafe_reader_result(change):
    response = dict(status="verified_export_review_ready", complete_delta_round=True,
                    checkpoint_advanced=False, automatic_owg_append=False,
                    event_snapshots=0, removal_markers_for_review=0)
    response.update(change)
    with patch.object(cycle, "verified_delta_round", return_value=response):
        with pytest.raises(ReceiptError):
            cycle.extract_first(consent_graph_read=True, consent_private_reports=True,
                                consent_delta_export=True)


def test_first_cycle_refuses_previous_cursor():
    with patch.object(cycle, "verified_delta_round") as reader:
        with pytest.raises(ReceiptError):
            cycle.extract_first(consent_graph_read=True, consent_private_reports=True,
                                consent_delta_export=True, previous_candidate="old")
        reader.assert_not_called()


def test_append_requires_review_and_separate_consents():
    for missing in ("consent_local_read", "consent_local_append",
                    "confirm_candidate_reviewed"):
        flags = dict(consent_local_read=True, consent_local_append=True,
                     confirm_candidate_reviewed=True)
        flags[missing] = False
        with patch.object(cycle, "append_and_record") as writer:
            with pytest.raises(ReceiptError):
                cycle.append_reviewed(**flags)
            writer.assert_not_called()


def test_append_uses_durable_production_writer_and_never_commits_checkpoint():
    receipt = dict(status="receipt_persisted", checkpoint_advanced=False,
                   automatic_retry=False, log_id="saved-log", events_after=8)
    with patch.object(cycle, "append_and_record", return_value=receipt) as writer:
        result = cycle.append_reviewed(
            consent_local_read=True, consent_local_append=True,
            confirm_candidate_reviewed=True, log_id="saved-log")
    assert result["status"] == "receipt_persisted"
    assert result["checkpoint_advanced"] is False
    assert result["automatic_retry"] is False
    writer.assert_called_once()


@pytest.mark.parametrize("change", [
    {"status": "not_saved"}, {"checkpoint_advanced": True},
    {"automatic_retry": True},
])
def test_append_uncertain_results_must_not_be_retried(change):
    receipt = dict(status="receipt_persisted", checkpoint_advanced=False,
                   automatic_retry=False, log_id="saved-log", events_after=8)
    receipt.update(change)
    with patch.object(cycle, "append_and_record", return_value=receipt):
        with pytest.raises(UncertainReceipt):
            cycle.append_reviewed(consent_local_read=True,
                                  consent_local_append=True,
                                  confirm_candidate_reviewed=True)


def test_cli_refuses_without_provider_consent(capsys):
    code = cycle.main(["extract", "--tenant", "t", "--mailbox", "m",
                       "--folder", "f", "--grant-file", "/tmp/grant",
                       "--expected-address", "a@example.org",
                       "--identity-report", "/tmp/id", "--folder-report", "/tmp/folder",
                       "--csv-output", "/tmp/events.csv",
                       "--candidate-output", "/tmp/candidate.json"])
    assert code == 2
    assert '"status": "refused"' in capsys.readouterr().out
