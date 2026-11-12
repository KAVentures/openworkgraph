"""Safe contract checks for the reviewed M365 operator transaction."""
import unittest
import os
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch
from server import m365_outlook_reviewed_transaction as tx
from server.m365_outlook_owg_receipt import ReceiptError, UncertainReceipt

DIGEST = "a" * 64


def inputs(**changes):
    value = dict(tenant="t", mailbox="m", folder="f", log_id="log",
                 confirm_log_id="log", grant_file="grant", candidate="candidate",
                 csv_path="csv", receipt_file="receipt", journal_dir="journal",
                 handoff_dir="handoff", ledger_dir="ledger", successor_label="next",
                 expected_review_csv_sha256=DIGEST, consent_local_read=True,
                 consent_local_append=True, confirm_candidate_reviewed=True,
                 confirm_removals_reviewed=True, confirm_checkpoint_commit=True)
    value.update(changes)
    return value


class ReviewedTransactionTests(unittest.TestCase):
    def test_cli_preflight_needs_no_append_only_arguments(self):
        argv = ["--preflight-only", "--tenant", "t", "--mailbox", "m",
                "--folder", "f", "--log-id", "log", "--confirm-log-id", "log",
                "--grant-file", "grant", "--candidate", "candidate",
                "--ledger-dir", "ledger", "--expected-review-csv-sha256",
                DIGEST, "--consent-local-read"]
        with patch.object(tx, "preflight", return_value={"status": "review_ready_for_explicit_commit"}) as preflight, \
             patch.object(tx, "execute") as execute:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(tx.main(argv), 0)
            self.assertEqual(json.loads(output.getvalue())["status"],
                             "review_ready_for_explicit_commit")
            self.assertEqual(preflight.call_args.kwargs["tenant"], "t")
            execute.assert_not_called()

    def test_cli_preflight_refusal_is_not_mutation_uncertainty(self):
        argv = ["--preflight-only", "--tenant", "t", "--mailbox", "m",
                "--folder", "f", "--log-id", "log", "--confirm-log-id", "log",
                "--grant-file", "grant", "--candidate", "candidate",
                "--ledger-dir", "ledger", "--expected-review-csv-sha256",
                DIGEST, "--consent-local-read"]
        with patch.object(tx, "preflight", side_effect=ReceiptError("revoked")), \
             patch.object(tx, "execute") as execute:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(tx.main(argv), 3)
            self.assertEqual(json.loads(output.getvalue())["status"], "preflight_rejected")
            execute.assert_not_called()

    def test_cli_commit_still_requires_all_mutation_arguments(self):
        argv = ["--tenant", "t", "--mailbox", "m", "--folder", "f",
                "--log-id", "log", "--confirm-log-id", "log",
                "--grant-file", "grant", "--candidate", "candidate",
                "--ledger-dir", "ledger", "--expected-review-csv-sha256", DIGEST]
        with patch.object(tx, "execute") as execute:
            with self.assertRaises(SystemExit) as failure:
                tx.main(argv)
            self.assertEqual(failure.exception.code, 2)
            execute.assert_not_called()

    def test_read_only_preflight_requires_consent_before_any_read(self):
        with patch.object(tx.cycle, "review") as review:
            with self.assertRaisesRegex(ReceiptError, "read consent"):
                tx.preflight(**{k: inputs(consent_local_read=False)[k] for k in (
                    "tenant", "mailbox", "folder", "log_id", "confirm_log_id",
                    "grant_file", "candidate", "ledger_dir", "expected_review_csv_sha256",
                    "consent_local_read")})
            review.assert_not_called()

    def test_read_only_preflight_checks_generation_without_mutation(self):
        values = inputs(expected_previous_candidate_sha256="b" * 64)
        allowed = ("tenant", "mailbox", "folder", "log_id", "confirm_log_id",
                   "grant_file", "candidate", "ledger_dir", "expected_review_csv_sha256",
                   "expected_previous_candidate_sha256", "consent_local_read")
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "log", "generation": 3}), \
             patch.object(tx.cycle, "append") as append, \
             patch.object(tx.cycle, "checkpoint_commit") as commit:
            result = tx.preflight(**{k: values[k] for k in allowed})
            self.assertEqual(result["proposed_generation"], 4)
            self.assertEqual(result["owg_writes"], 0)
            self.assertFalse(result["checkpoint_advanced"])
            append.assert_not_called()
            commit.assert_not_called()

    def test_read_only_preflight_refuses_stale_predecessor(self):
        values = inputs(expected_previous_candidate_sha256="c" * 64)
        allowed = ("tenant", "mailbox", "folder", "log_id", "confirm_log_id",
                   "grant_file", "candidate", "ledger_dir", "expected_review_csv_sha256",
                   "expected_previous_candidate_sha256", "consent_local_read")
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "log", "generation": 3}), \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "Stale"):
                tx.preflight(**{k: values[k] for k in allowed})
            append.assert_not_called()

    def test_missing_approval_refuses_without_read_or_write(self):
        with patch.object(tx.cycle, "review") as review, patch.object(tx.cycle, "append") as append:
            with self.assertRaises(ReceiptError):
                tx.execute(**inputs(confirm_checkpoint_commit=False))
            review.assert_not_called()
            append.assert_not_called()

    def test_review_digest_mismatch_refuses_before_append(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": "b" * 64}), \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "changed"):
                tx.execute(**inputs())
            append.assert_not_called()

    def test_success_calls_real_phases_in_order(self):
        order = []
        def step(name, response):
            def call(**kwargs):
                order.append(name)
                return response
            return call
        with patch.object(tx, "inspect_checkpoint", return_value=None), \
             patch.object(tx.cycle, "review", side_effect=step("review", {"csv_sha256": DIGEST})), \
             patch.object(tx.cycle, "append", side_effect=step("append", {"status": "receipt_persisted"})), \
             patch.object(tx.cycle, "handoff", side_effect=step("handoff", {"status": "next_round_handoff_prepared"})), \
             patch.object(tx.cycle, "checkpoint_commit", side_effect=step("checkpoint", {"status": "checkpoint_committed", "generation": 2})):
            result = tx.execute(**inputs())
        self.assertEqual(order, ["review", "append", "handoff", "checkpoint"])
        self.assertEqual(result["generation"], 2)
        self.assertFalse(result["automatic_retry_allowed"])

    def test_uncertain_append_never_handoffs_or_commits(self):
        with patch.object(tx, "inspect_checkpoint", return_value=None), \
             patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx.cycle, "append", side_effect=UncertainReceipt("unknown")), \
             patch.object(tx.cycle, "handoff") as handoff, \
             patch.object(tx.cycle, "checkpoint_commit") as checkpoint:
            with self.assertRaises(UncertainReceipt):
                tx.execute(**inputs())
            handoff.assert_not_called()
            checkpoint.assert_not_called()

    def test_stale_predecessor_refused_before_append(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "log"}) as inspect, \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "Stale"):
                tx.execute(**inputs(expected_previous_candidate_sha256="c" * 64))
            inspect.assert_called_once()
            append.assert_not_called()

    def test_wrong_destination_log_refused_before_append(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "other"}), \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "different predecessor"):
                tx.execute(**inputs(expected_previous_candidate_sha256="b" * 64))
            append.assert_not_called()

    def test_initial_generation_requires_empty_ledger(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "log"}), \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "Stale"):
                tx.execute(**inputs())
            append.assert_not_called()

    def test_missing_predecessor_refused_before_append(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value=None), \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaisesRegex(ReceiptError, "Missing predecessor"):
                tx.execute(**inputs(expected_previous_candidate_sha256="b" * 64))
            append.assert_not_called()

    def test_matching_predecessor_allows_transaction(self):
        with patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx, "inspect_checkpoint", return_value={
                 "candidate_sha256": "b" * 64, "log_id": "log"}), \
             patch.object(tx.cycle, "append", return_value={"status": "receipt_persisted"}), \
             patch.object(tx.cycle, "handoff", return_value={"status": "next_round_handoff_prepared"}), \
             patch.object(tx.cycle, "checkpoint_commit", return_value={
                 "status": "checkpoint_committed", "generation": 2}) as commit:
            result = tx.execute(**inputs(expected_previous_candidate_sha256="b" * 64))
            self.assertEqual(result["generation"], 2)
            commit.assert_called_once()

    def test_audited_handoff_receipt_precedes_checkpoint_commit(self):
        if os.name != "posix":
            self.skipTest("Private audit writer is POSIX-only")
        order = []
        def phase(name, response):
            def invoke(**kwargs):
                order.append(name)
                return response
            return invoke
        with patch.dict("os.environ", {"OWG_M365_AUDIT_DIR": "/audit"}), \
             patch.object(tx, "preflight_operator_audit"), \
             patch.object(tx, "record_operator_phase", side_effect=lambda name, result, **kw: order.append("audit:" + name)), \
             patch.object(tx, "inspect_checkpoint", return_value=None), \
             patch.object(tx.cycle, "review", side_effect=phase("review", {"csv_sha256": DIGEST})), \
             patch.object(tx.cycle, "append", side_effect=phase("append", {"status": "receipt_persisted"})), \
             patch.object(tx.cycle, "handoff", side_effect=phase("handoff", {"status": "next_round_handoff_prepared"})), \
             patch.object(tx.cycle, "checkpoint_commit", side_effect=phase("checkpoint", {"status": "checkpoint_committed", "generation": 1})):
            tx.execute(**inputs())
        self.assertEqual(order, ["review", "audit:review", "append", "audit:append",
                                 "handoff", "audit:handoff", "checkpoint", "audit:checkpoint-commit"])

    def test_handoff_audit_failure_blocks_checkpoint_commit(self):
        if os.name != "posix":
            self.skipTest("Private audit writer is POSIX-only")
        def audit(name, result, **kwargs):
            if name == "handoff":
                raise tx.AuditWriteError("audit unavailable")
        with patch.dict("os.environ", {"OWG_M365_AUDIT_DIR": "/audit"}), \
             patch.object(tx, "preflight_operator_audit"), \
             patch.object(tx, "record_operator_phase", side_effect=audit), \
             patch.object(tx, "inspect_checkpoint", return_value=None), \
             patch.object(tx.cycle, "review", return_value={"csv_sha256": DIGEST}), \
             patch.object(tx.cycle, "append", return_value={"status": "receipt_persisted"}), \
             patch.object(tx.cycle, "handoff", return_value={"status": "next_round_handoff_prepared"}), \
             patch.object(tx.cycle, "checkpoint_commit") as commit:
            with self.assertRaises(tx.AuditWriteError):
                tx.execute(**inputs())
            commit.assert_not_called()

    def test_audit_preflight_failure_prevents_any_mutation(self):
        with patch.dict("os.environ", {"OWG_M365_AUDIT_DIR": "/audit"}), \
             patch.object(tx, "preflight_operator_audit", side_effect=OSError("unsafe")), \
             patch.object(tx.cycle, "review") as review, \
             patch.object(tx.cycle, "append") as append:
            with self.assertRaises(ReceiptError if os.name != "posix" else OSError):
                tx.execute(**inputs())
            review.assert_not_called()
            append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
