"""Connected two-generation operator acceptance with bounded provider/OWG seams.

Exercises the production prepare and transaction orchestration, not replacement
copies of their state machines. External Graph and OWG writes are simulated.
Real Microsoft tenant validation remains a separate release gate.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_next_round_review as next_round
from server import m365_outlook_reviewed_transaction as transaction
from server.m365_outlook_owg_receipt import UncertainReceipt

A = "a" * 64
B = "b" * 64
C = "c" * 64


def _scope():
    return dict(tenant="tenant", mailbox="mailbox", folder="inbox",
                grant_file=Path("/private/grant"), log_id="log",
                confirm_log_id="log", ledger_dir=Path("/private/ledger"))


def _review(digest):
    return dict(status="review_ready", csv_sha256=digest,
                event_snapshots=2, removal_markers_for_review=1, pages=1)


def test_two_generations_require_independent_review_and_explicit_commit():
    state = dict(generation=1, candidate_sha256=A, log_id="log")
    extraction_calls = []
    writes = []
    scope = _scope()
    for predecessor_digest, successor_digest, generation in ((A, B, 1), (B, C, 2)):
        assert state["generation"] == generation
        assert state["candidate_sha256"] == predecessor_digest

        def extract(**kwargs):
            extraction_calls.append(kwargs["expected_generation"])
            return dict(event_snapshots=2, removal_markers_for_review=1)

        def preflight(**kwargs):
            assert kwargs["expected_previous_candidate_sha256"] == predecessor_digest
            return dict(status="review_ready_for_explicit_commit",
                        proposed_generation=generation + 1,
                        checkpoint_predecessor_generation=generation,
                        reviewed_event_snapshots=2, removal_markers_for_review=1,
                        reviewed_csv_sha256=successor_digest,
                        manual_review_and_separate_commit_required=True,
                        provider_requests=0, owg_writes=0,
                        checkpoint_advanced=False, automatic_retry_allowed=False)

        with (patch.object(next_round, "inspect_checkpoint", side_effect=lambda **_: dict(state)),
              patch.object(next_round.cycle, "extract", side_effect=extract),
              patch.object(next_round.cycle, "review", return_value=_review(successor_digest)),
              patch.object(next_round.transaction, "preflight", side_effect=preflight)):
            prepared = next_round.prepare(
                **scope, previous_candidate=Path("/private/old.json"),
                previous_csv=Path("/private/old.csv"),
                receipt_file=Path("/private/receipt"),
                journal_dir=Path("/private/journal"),
                handoff_dir=Path("/private/handoff"),
                successor_label="next", output=Path("/private/next.csv"),
                candidate=Path("/private/next.json"),
                expected_generation=generation,
                expected_previous_candidate_sha256=predecessor_digest,
                token="fake-contract-token", consent_local_read=True,
                consent_delta_read=True, consent_local_export=True,
                confirm_previous_removals_reviewed=True,
                require_event_membership=True)
        assert prepared["status"] == "operator_review_required"
        assert prepared["checkpoint_advanced"] is False
        assert writes == [entry for previous in range(1, generation) for entry in
                          (("append", previous), ("handoff", previous), ("checkpoint", previous))]

        def append(**kwargs):
            writes.append(("append", generation))
            return dict(status="receipt_persisted")

        def handoff(**kwargs):
            writes.append(("handoff", generation))
            return dict(status="next_round_handoff_prepared")

        def checkpoint(**kwargs):
            writes.append(("checkpoint", generation))
            state.update(generation=generation + 1, candidate_sha256=successor_digest)
            return dict(status="checkpoint_committed", generation=generation + 1,
                        idempotent_replay=False)

        with (patch.object(transaction, "inspect_checkpoint", side_effect=lambda **_: dict(state)),
              patch.object(transaction.cycle, "review", return_value=_review(successor_digest)),
              patch.object(transaction.cycle, "append", side_effect=append),
              patch.object(transaction.cycle, "handoff", side_effect=handoff),
              patch.object(transaction.cycle, "checkpoint_commit", side_effect=checkpoint)):
            committed = transaction.execute(
                **scope, candidate=Path("/private/next.json"),
                csv_path=Path("/private/next.csv"),
                receipt_file=Path("/private/receipt"),
                journal_dir=Path("/private/journal"),
                handoff_dir=Path("/private/handoff"), successor_label="next",
                expected_review_csv_sha256=successor_digest,
                expected_previous_candidate_sha256=predecessor_digest,
                consent_local_read=True, consent_local_append=True,
                confirm_candidate_reviewed=True,
                confirm_removals_reviewed=True,
                confirm_checkpoint_commit=True)
        assert committed["generation"] == generation + 1
        assert committed["automatic_retry_allowed"] is False
    assert extraction_calls == [1, 2]
    assert writes == [("append", 1), ("handoff", 1), ("checkpoint", 1),
                      ("append", 2), ("handoff", 2), ("checkpoint", 2)]


def test_uncertain_handoff_never_advances_checkpoint_or_retries_append():
    state = dict(generation=1, candidate_sha256=A, log_id="log")
    with (patch.object(transaction, "inspect_checkpoint", return_value=state),
          patch.object(transaction.cycle, "review", return_value=_review(B)),
          patch.object(transaction.cycle, "append", return_value=dict(status="receipt_persisted")) as append,
          patch.object(transaction.cycle, "handoff", side_effect=UncertainReceipt("uncertain")),
          patch.object(transaction.cycle, "checkpoint_commit") as checkpoint):
        with pytest.raises(UncertainReceipt):
            transaction.execute(
                **_scope(), candidate=Path("/private/next.json"),
                csv_path=Path("/private/next.csv"),
                receipt_file=Path("/private/receipt"),
                journal_dir=Path("/private/journal"),
                handoff_dir=Path("/private/handoff"), successor_label="next",
                expected_review_csv_sha256=B,
                expected_previous_candidate_sha256=A,
                consent_local_read=True, consent_local_append=True,
                confirm_candidate_reviewed=True,
                confirm_removals_reviewed=True,
                confirm_checkpoint_commit=True)
    append.assert_called_once()
    checkpoint.assert_not_called()


@pytest.mark.parametrize("failure_phase", ["append", "handoff", "checkpoint"])
def test_uncertain_mutation_never_retries_or_reports_success(failure_phase):
    """Each uncertain boundary fails closed; only an operator may reconcile."""
    state = dict(generation=1, candidate_sha256=A, log_id="log")
    calls = []

    def append(**kwargs):
        calls.append("append")
        if failure_phase == "append":
            raise UncertainReceipt("append response lost")
        return dict(status="receipt_persisted")

    def handoff(**kwargs):
        calls.append("handoff")
        if failure_phase == "handoff":
            raise UncertainReceipt("handoff response lost")
        return dict(status="next_round_handoff_prepared")

    def checkpoint(**kwargs):
        calls.append("checkpoint")
        if failure_phase == "checkpoint":
            # Simulate a durable write with a lost response: the operator
            # must not retry or falsely claim success.
            state["generation"] = 2
            raise UncertainReceipt("checkpoint response lost")
        return dict(status="checkpoint_committed", generation=2)

    with (patch.object(transaction, "inspect_checkpoint", side_effect=lambda **_: dict(state)),
          patch.object(transaction.cycle, "review", return_value=_review(B)),
          patch.object(transaction.cycle, "append", side_effect=append),
          patch.object(transaction.cycle, "handoff", side_effect=handoff),
          patch.object(transaction.cycle, "checkpoint_commit", side_effect=checkpoint)):
        with pytest.raises(UncertainReceipt):
            transaction.execute(
                **_scope(), candidate=Path("/private/next.json"),
                csv_path=Path("/private/next.csv"),
                receipt_file=Path("/private/receipt"),
                journal_dir=Path("/private/journal"),
                handoff_dir=Path("/private/handoff"), successor_label="next",
                expected_review_csv_sha256=B,
                expected_previous_candidate_sha256=A,
                consent_local_read=True, consent_local_append=True,
                confirm_candidate_reviewed=True,
                confirm_removals_reviewed=True,
                confirm_checkpoint_commit=True)
    assert calls == {
        "append": ["append"],
        "handoff": ["append", "handoff"],
        "checkpoint": ["append", "handoff", "checkpoint"],
    }[failure_phase]


@pytest.mark.parametrize("field", ["provider_requests", "owg_writes"])
def test_next_round_rejects_boolean_preflight_side_effect_counts(field):
    """A boolean is not a trustworthy integer count of provider/OWG effects."""
    state = dict(generation=1, candidate_sha256=A, log_id="log")
    proposal = dict(status="review_ready_for_explicit_commit",
                    proposed_generation=2, checkpoint_predecessor_generation=1,
                    reviewed_event_snapshots=2, removal_markers_for_review=1,
                    reviewed_csv_sha256=B,
                    manual_review_and_separate_commit_required=True,
                    provider_requests=0, owg_writes=0,
                    checkpoint_advanced=False, automatic_retry_allowed=False)
    proposal[field] = False  # False == 0 in Python, but not a valid audit count.
    with (patch.object(next_round, "inspect_checkpoint", return_value=state),
          patch.object(next_round.cycle, "extract",
                       return_value=dict(event_snapshots=2, removal_markers_for_review=1)),
          patch.object(next_round.cycle, "review", return_value=_review(B)),
          patch.object(next_round.transaction, "preflight", return_value=proposal)):
        with pytest.raises(transaction.ReceiptError, match="Invalid or concurrent"):
            next_round.prepare(
                **_scope(), previous_candidate=Path("/private/old.json"),
                previous_csv=Path("/private/old.csv"),
                receipt_file=Path("/private/receipt"),
                journal_dir=Path("/private/journal"),
                handoff_dir=Path("/private/handoff"),
                successor_label="next", output=Path("/private/next.csv"),
                candidate=Path("/private/next.json"),
                expected_generation=1,
                expected_previous_candidate_sha256=A,
                token="fake-contract-token", consent_local_read=True,
                consent_delta_read=True, consent_local_export=True,
                confirm_previous_removals_reviewed=True,
                require_event_membership=True)
