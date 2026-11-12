"""Checkpoint-bound Microsoft 365 extraction must fail before Graph reads."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from server import m365_outlook_operator_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError


class CheckpointBoundExtractionTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(
            tenant="tenant", mailbox="mailbox", folder="folder",
            grant_file=Path("/private/grant.json"),
            previous_candidate=Path("/private/candidate.json"),
            log_id="log", ledger_dir=Path("/private/ledger"),
            expected_generation=2, consent_local_read=True,
            consent_delta_read=True, consent_local_export=True,
            confirm_removals_reviewed=True,
        )
        self.entry = dict(generation=2, candidate_sha256="a" * 64, log_id="log")

    def test_stale_generation_never_contacts_graph(self):
        with patch.object(cycle, "inspect_checkpoint", return_value=self.entry), \
             patch.object(cycle, "run_verified_next_round") as graph:
            with self.assertRaises(ReceiptError):
                cycle.extract(**{**self.args, "expected_generation": 1})
            graph.assert_not_called()

    def test_mismatched_candidate_never_contacts_graph(self):
        with patch.object(cycle, "inspect_checkpoint", return_value=self.entry), \
             patch.object(cycle, "_private_read",
                          return_value=json.dumps({"csv_sha256": "b" * 64}).encode()), \
             patch.object(cycle, "run_verified_next_round") as graph:
            with self.assertRaises(ReceiptError):
                cycle.extract(**self.args)
            graph.assert_not_called()

    def test_mismatched_log_never_contacts_graph(self):
        with patch.object(cycle, "inspect_checkpoint", return_value=self.entry), \
             patch.object(cycle, "_private_read",
                          return_value=json.dumps({"csv_sha256": "a" * 64}).encode()), \
             patch.object(cycle, "run_verified_next_round") as graph:
            with self.assertRaises(ReceiptError):
                cycle.extract(**{**self.args, "log_id": "other"})
            graph.assert_not_called()

    def test_partial_checkpoint_flags_refused(self):
        for key in ("ledger_dir", "expected_generation"):
            args = {**self.args, key: None}
            with self.subTest(key=key), patch.object(cycle, "run_verified_next_round") as graph:
                with self.assertRaises(ReceiptError):
                    cycle.extract(**args)
                graph.assert_not_called()

    def test_verified_predecessor_allows_bounded_delegate(self):
        with patch.object(cycle, "inspect_checkpoint", return_value=self.entry), \
             patch.object(cycle, "_private_read",
                          return_value=json.dumps({"csv_sha256": "a" * 64}).encode()), \
             patch.object(cycle, "run_verified_next_round", return_value={
                 "status": "next_round_review_ready",
                 "event_snapshots": 3, "removal_markers_for_review": 1}) as graph:
            result = cycle.extract(**self.args)
            self.assertEqual(result["status"], "review_required")
            self.assertFalse(result["checkpoint_advanced"])
            graph.assert_called_once()
            self.assertNotIn("ledger_dir", graph.call_args.kwargs)
            self.assertNotIn("expected_generation", graph.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
