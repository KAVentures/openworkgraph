"""Fail-closed next-round preflight contract, without Graph or OWG writes."""
from unittest.mock import patch

import pytest

from server import m365_next_round_review as operator
from server.m365_outlook_owg_receipt import ReceiptError


@pytest.mark.parametrize("proposal", [
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 2,
     "checkpoint_predecessor_generation": 0, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": True,
     "checkpoint_predecessor_generation": 1, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 2,
     "checkpoint_predecessor_generation": 1, "reviewed_event_snapshots": 3,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 2,
     "checkpoint_predecessor_generation": 1, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": True},
    {"status": "unverified", "proposed_generation": 2,
     "checkpoint_predecessor_generation": 1, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    None,
])
def test_preflight_mismatch_cannot_be_presented_as_review_ready(proposal, tmp_path, monkeypatch):
    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    digest = "a" * 64
    predecessor = {"generation": 1, "candidate_sha256": digest, "log_id": "log"}
    with (
        patch.object(operator, "inspect_checkpoint", return_value=predecessor),
        patch.object(operator.cycle, "extract", return_value={
            "event_snapshots": 2, "removal_markers_for_review": 0}),
        patch.object(operator.cycle, "review", return_value={
            "csv_sha256": "b" * 64, "event_snapshots": 2,
            "removal_markers_for_review": 0}),
        patch.object(operator.transaction, "preflight", return_value=proposal),
        patch.object(operator.transaction, "execute") as append,
        pytest.raises(ReceiptError),
    ):
        operator.prepare(
            tenant="tenant", mailbox="mailbox", folder="folder",
            log_id="log", confirm_log_id="log", grant_file=tmp_path / "grant",
            previous_candidate=tmp_path / "old.json",
            previous_csv=tmp_path / "old.csv",
            receipt_file=tmp_path / "receipt", journal_dir=tmp_path / "journal",
            handoff_dir=tmp_path / "handoff", ledger_dir=tmp_path / "ledger",
            successor_label="next", output=tmp_path / "new.csv",
            candidate=tmp_path / "new.json", expected_generation=1,
            expected_previous_candidate_sha256=digest, token="token",
            consent_local_read=True, consent_delta_read=True,
            consent_local_export=True, confirm_previous_removals_reviewed=True,
        )
    append.assert_not_called()


@pytest.mark.parametrize("source,bad_value", [
    ("extracted", {"event_snapshots": True, "removal_markers_for_review": 0}),
    ("extracted", {"event_snapshots": -1, "removal_markers_for_review": 0}),
    ("checked", {"csv_sha256": "b" * 64, "event_snapshots": 2,
                 "removal_markers_for_review": False}),
    ("checked", {"csv_sha256": "not-a-sha", "event_snapshots": 2,
                 "removal_markers_for_review": 0}),
])
def test_malformed_adapter_evidence_fails_before_transaction_preflight(
        source, bad_value, tmp_path, monkeypatch):
    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    digest = "a" * 64
    predecessor = {"generation": 1, "candidate_sha256": digest, "log_id": "log"}
    extracted = {"event_snapshots": 2, "removal_markers_for_review": 0}
    checked = {"csv_sha256": "b" * 64, **extracted}
    if source == "extracted":
        extracted = bad_value
    else:
        checked = bad_value
    with (
        patch.object(operator, "inspect_checkpoint", return_value=predecessor),
        patch.object(operator.cycle, "extract", return_value=extracted),
        patch.object(operator.cycle, "review", return_value=checked),
        patch.object(operator.transaction, "preflight") as preflight,
        patch.object(operator.transaction, "execute") as append,
        pytest.raises(ReceiptError),
    ):
        operator.prepare(
            tenant="tenant", mailbox="mailbox", folder="folder",
            log_id="log", confirm_log_id="log", grant_file=tmp_path / "grant",
            previous_candidate=tmp_path / "old.json",
            previous_csv=tmp_path / "old.csv",
            receipt_file=tmp_path / "receipt", journal_dir=tmp_path / "journal",
            handoff_dir=tmp_path / "handoff", ledger_dir=tmp_path / "ledger",
            successor_label="next", output=tmp_path / "new.csv",
            candidate=tmp_path / "new.json", expected_generation=1,
            expected_previous_candidate_sha256=digest, token="token",
            consent_local_read=True, consent_delta_read=True,
            consent_local_export=True, confirm_previous_removals_reviewed=True,
        )
    preflight.assert_not_called()
    append.assert_not_called()


@pytest.mark.parametrize("source_counts,review_counts", [
    ((3, 0), (2, 0)),
    ((2, 1), (2, 0)),
    ((2, 0), (2, 1)),
])
def test_extraction_review_count_drift_refused_before_transaction_preflight(
        source_counts, review_counts, tmp_path, monkeypatch):
    """No transaction proposal may be produced from disagreeing provider evidence."""
    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    digest = "a" * 64
    predecessor = {"generation": 1, "candidate_sha256": digest, "log_id": "log"}
    extracted = dict(zip(("event_snapshots", "removal_markers_for_review"), source_counts))
    checked = {"csv_sha256": "b" * 64, **dict(zip(
        ("event_snapshots", "removal_markers_for_review"), review_counts))}
    with (
        patch.object(operator, "inspect_checkpoint", return_value=predecessor),
        patch.object(operator.cycle, "extract", return_value=extracted),
        patch.object(operator.cycle, "review", return_value=checked),
        patch.object(operator.transaction, "preflight") as preflight,
        patch.object(operator.transaction, "execute") as append,
        pytest.raises(ReceiptError, match="count mismatch"),
    ):
        operator.prepare(
            tenant="tenant", mailbox="mailbox", folder="folder",
            log_id="log", confirm_log_id="log", grant_file=tmp_path / "grant",
            previous_candidate=tmp_path / "old.json",
            previous_csv=tmp_path / "old.csv",
            receipt_file=tmp_path / "receipt", journal_dir=tmp_path / "journal",
            handoff_dir=tmp_path / "handoff", ledger_dir=tmp_path / "ledger",
            successor_label="next", output=tmp_path / "new.csv",
            candidate=tmp_path / "new.json", expected_generation=1,
            expected_previous_candidate_sha256=digest, token="token",
            consent_local_read=True, consent_delta_read=True,
            consent_local_export=True, confirm_previous_removals_reviewed=True,
        )
    preflight.assert_not_called()
    append.assert_not_called()
