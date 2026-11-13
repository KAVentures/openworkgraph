"""Contract tests for the checkpoint-bound operator next-round entrypoint."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from server import m365_next_round_review as flow
from server.m365_outlook_owg_receipt import ReceiptError

SHA = "a" * 64


def inputs(**kw):
    values = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  log_id="log", confirm_log_id="log", grant_file="grant",
                  previous_candidate="previous.json", previous_csv="previous.csv",
                  receipt_file="receipt", journal_dir="journal",
                  handoff_dir="handoff", ledger_dir="ledger", successor_label="next",
                  output="new.csv", candidate="new.json", expected_generation=2,
                  expected_previous_candidate_sha256=SHA, token="token",
                  consent_local_read=True, consent_delta_read=True,
                  consent_local_export=True, confirm_previous_removals_reviewed=True,
                  require_event_membership=True)
    values.update(kw)
    return values


class NextRoundTests(unittest.TestCase):
    def test_missing_approval_blocks_graph_and_local_reads(self):
        with patch.object(flow, "inspect_checkpoint") as checkpoint, \
             patch.object(flow.cycle, "extract") as extract:
            with self.assertRaises(ReceiptError):
                flow.prepare(**inputs(consent_delta_read=False))
            checkpoint.assert_not_called()
            extract.assert_not_called()

    def test_predecessor_digest_mismatch_refuses_before_graph(self):
        with patch.object(flow, "inspect_checkpoint", return_value={
                "generation": 2, "candidate_sha256": "b" * 64, "log_id": "log"}), \
             patch.object(flow.cycle, "extract") as extract:
            with self.assertRaisesRegex(ReceiptError, "Stale"):
                flow.prepare(**inputs())
            extract.assert_not_called()

    def test_success_is_review_only_and_calls_production_adapters(self):
        order = []
        def extracted(**kwargs):
            order.append("graph")
            self.assertTrue(kwargs["require_event_membership"])
            self.assertEqual(kwargs["expected_generation"], 2)
            return {"event_snapshots": 3, "removal_markers_for_review": 1}
        def reviewed(**kwargs):
            order.append("review")
            return {"csv_sha256": "c" * 64, "event_snapshots": 3,
                    "removal_markers_for_review": 1}
        def preflight(**kwargs):
            order.append("preflight")
            self.assertEqual(kwargs["expected_previous_candidate_sha256"], SHA)
            self.assertEqual(kwargs["expected_review_csv_sha256"], "c" * 64)
            return {"proposed_generation": 3}
        with patch.object(flow, "inspect_checkpoint", return_value={
                "generation": 2, "candidate_sha256": SHA, "log_id": "log"}), \
             patch.object(flow.cycle, "extract", side_effect=extracted), \
             patch.object(flow.cycle, "review", side_effect=reviewed), \
             patch.object(flow.transaction, "preflight", side_effect=preflight), \
             patch.object(flow.transaction, "execute") as commit:
            result = flow.prepare(**inputs())
        self.assertEqual(order, ["graph", "review", "preflight"])
        self.assertEqual(result["status"], "operator_review_required")
        self.assertEqual(result["proposed_generation"], 3)
        self.assertFalse(result["checkpoint_advanced"])
        commit.assert_not_called()

    def test_concurrent_checkpoint_advance_refuses_no_commit(self):
        with patch.object(flow, "inspect_checkpoint", return_value={
                "generation": 2, "candidate_sha256": SHA, "log_id": "log"}), \
             patch.object(flow.cycle, "extract", return_value={
                 "event_snapshots": 1, "removal_markers_for_review": 0}), \
             patch.object(flow.cycle, "review", return_value={
                 "csv_sha256": "c" * 64, "event_snapshots": 1,
                 "removal_markers_for_review": 0}), \
             patch.object(flow.transaction, "preflight",
                          return_value={"proposed_generation": 5}), \
             patch.object(flow.transaction, "execute") as commit:
            with self.assertRaisesRegex(ReceiptError, "Concurrent"):
                flow.prepare(**inputs())
            commit.assert_not_called()

    def test_count_disagreement_refuses_no_commit(self):
        with patch.object(flow, "inspect_checkpoint", return_value={
                "generation": 2, "candidate_sha256": SHA, "log_id": "log"}), \
             patch.object(flow.cycle, "extract", return_value={
                 "event_snapshots": 2, "removal_markers_for_review": 0}), \
             patch.object(flow.cycle, "review", return_value={
                 "csv_sha256": "c" * 64, "event_snapshots": 1,
                 "removal_markers_for_review": 0}), \
             patch.object(flow.transaction, "preflight",
                          return_value={"proposed_generation": 3}), \
             patch.object(flow.transaction, "execute") as commit:
            with self.assertRaisesRegex(ReceiptError, "count mismatch"):
                flow.prepare(**inputs())
            commit.assert_not_called()

    def test_cli_refusal_is_non_retryable(self):
        with patch.object(flow, "prepare", side_effect=ReceiptError("private")):
            args = ["--tenant", "t", "--mailbox", "m", "--folder", "f",
                    "--log-id", "l", "--confirm-log-id", "l",
                    "--successor-label", "next",
                    "--expected-previous-candidate-sha256", SHA,
                    "--expected-generation", "1"]
            for name in ("grant-file", "previous-candidate", "previous-csv",
                         "receipt-file", "journal-dir", "handoff-dir",
                         "ledger-dir", "output", "candidate"):
                args.extend(["--" + name, name])
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(flow.main(args), 3)
            result = json.loads(out.getvalue())
            self.assertFalse(result["automatic_retry_allowed"])
            self.assertNotIn("private", out.getvalue())


if __name__ == "__main__":
    unittest.main()
