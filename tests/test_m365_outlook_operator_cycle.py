"""Integration-boundary checks for the operator's two distinct sync phases."""
import unittest
from unittest.mock import patch

from server import m365_outlook_operator_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError, UncertainReceipt


class OperatorCycleTests(unittest.TestCase):
    def test_extract_refuses_before_provider_without_all_consents(self):
        with patch.object(cycle, "run_verified_next_round") as delegate:
            with self.assertRaises(ReceiptError):
                cycle.extract(consent_local_read=True, consent_delta_read=True,
                              consent_local_export=True)
            delegate.assert_not_called()

    def test_extract_invokes_real_verified_reader_only(self):
        response = {"status": "next_round_review_ready", "event_snapshots": 3,
                    "removal_markers_for_review": 1}
        with patch.object(cycle, "run_verified_next_round", return_value=response) as reader, \
             patch.object(cycle, "append_and_record") as writer:
            result = cycle.extract(consent_local_read=True, consent_delta_read=True,
                                   consent_local_export=True,
                                   confirm_removals_reviewed=True, tenant="t")
            self.assertEqual(result["status"], "review_required")
            self.assertEqual(result["removal_markers_for_review"], 1)
            self.assertFalse(result["owg_append_performed"])
            reader.assert_called_once()
            self.assertEqual(reader.call_args.kwargs["tenant"], "t")
            writer.assert_not_called()

    def test_append_refuses_without_independent_review(self):
        with patch.object(cycle, "append_and_record") as writer:
            with self.assertRaises(ReceiptError):
                cycle.append(consent_local_read=True, consent_local_append=True)
            writer.assert_not_called()

    def test_append_uses_durable_receipt_not_provider(self):
        response = {"status": "receipt_persisted", "log_id": "log1",
                    "events_after": 9}
        with patch.object(cycle, "append_and_record", return_value=response) as writer, \
             patch.object(cycle, "run_verified_next_round") as reader:
            result = cycle.append(consent_local_read=True, consent_local_append=True,
                                  confirm_candidate_reviewed=True, log_id="log1")
            self.assertEqual(result["status"], "receipt_persisted")
            self.assertFalse(result["automatic_retry"])
            self.assertFalse(result["checkpoint_advanced"])
            writer.assert_called_once()
            reader.assert_not_called()

    def test_append_uncertain_result_does_not_claim_success(self):
        with patch.object(cycle, "append_and_record", return_value={"status": "uncertain"}):
            with self.assertRaises(UncertainReceipt):
                cycle.append(consent_local_read=True, consent_local_append=True,
                             confirm_candidate_reviewed=True)

    def test_extract_unexpected_status_fails_closed(self):
        with patch.object(cycle, "run_verified_next_round",
                          return_value={"status": "incomplete"}):
            with self.assertRaises(ReceiptError):
                cycle.extract(consent_local_read=True, consent_delta_read=True,
                              consent_local_export=True,
                              confirm_removals_reviewed=True)


if __name__ == "__main__":
    unittest.main()
