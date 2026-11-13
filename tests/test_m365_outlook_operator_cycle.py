"""Integration-boundary checks for separately authorized operator phases."""
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

    def test_checkpoint_bound_extract_rechecks_after_graph_and_refuses_race(self):
        import json
        from pathlib import Path
        from unittest.mock import call
        prior = {"generation": 2, "candidate_sha256": "a" * 64,
                 "log_id": "log", "cursor_url": "cursor"}
        changed = {**prior, "generation": 3}
        response = {"status": "next_round_review_ready", "event_snapshots": 1,
                    "removal_markers_for_review": 0}
        kwargs = dict(consent_local_read=True, consent_delta_read=True,
                      consent_local_export=True, confirm_removals_reviewed=True,
                      ledger_dir=Path("ledger"), expected_generation=2,
                      tenant="tenant", mailbox="mailbox", folder="folder",
                      grant_file=Path("grant"), previous_candidate=Path("previous"),
                      log_id="log")
        with patch.object(cycle, "inspect_checkpoint",
                          side_effect=[prior, changed]) as inspect, \
             patch.object(cycle, "_private_read",
                          return_value=json.dumps({"csv_sha256": "a" * 64}).encode()), \
             patch.object(cycle, "run_verified_next_round",
                          return_value=response) as graph:
            with self.assertRaisesRegex(ReceiptError, "changed during Graph"):
                cycle.extract(**kwargs)
            self.assertEqual(inspect.call_count, 2)
            graph.assert_called_once()

    def test_checkpoint_bound_extract_accepts_unchanged_generation(self):
        import json
        from pathlib import Path
        prior = {"generation": 2, "candidate_sha256": "a" * 64,
                 "log_id": "log", "cursor_url": "cursor"}
        with patch.object(cycle, "inspect_checkpoint", return_value=prior) as inspect, \
             patch.object(cycle, "_private_read",
                          return_value=json.dumps({"csv_sha256": "a" * 64}).encode()), \
             patch.object(cycle, "run_verified_next_round", return_value={
                 "status": "next_round_review_ready", "event_snapshots": 1,
                 "removal_markers_for_review": 0
             }):
            result = cycle.extract(
                consent_local_read=True, consent_delta_read=True,
                consent_local_export=True, confirm_removals_reviewed=True,
                ledger_dir=Path("ledger"), expected_generation=2,
                tenant="tenant", mailbox="mailbox", folder="folder",
                grant_file=Path("grant"), previous_candidate=Path("previous"),
                log_id="log")
            self.assertEqual(result["status"], "review_required")
            self.assertEqual(inspect.call_count, 2)

    def test_checkpoint_bound_extract_fails_before_graph_when_stale(self):
        from pathlib import Path
        with patch.object(cycle, "inspect_checkpoint", return_value={
            "generation": 3, "candidate_sha256": "a" * 64, "log_id": "log"
        }), patch.object(cycle, "run_verified_next_round") as graph:
            with self.assertRaisesRegex(ReceiptError, "stale"):
                cycle.extract(
                    consent_local_read=True, consent_delta_read=True,
                    consent_local_export=True, confirm_removals_reviewed=True,
                    ledger_dir=Path("ledger"), expected_generation=2,
                    tenant="tenant", mailbox="mailbox", folder="folder",
                    grant_file=Path("grant"), previous_candidate=Path("previous"),
                    log_id="log")
            graph.assert_not_called()

    def test_review_refuses_before_artifact_access_without_consent(self):
        with patch.object(cycle, "review_candidate") as delegate:
            with self.assertRaises(ReceiptError):
                cycle.review(tenant="t", mailbox="m", folder="f",
                             grant_file="grant", candidate="candidate")
            delegate.assert_not_called()

    def test_review_delegates_to_production_validator_without_graph_or_append(self):
        response = {"status": "review_ready", "event_snapshots": 2,
                    "removal_markers_for_review": 1}
        with patch.object(cycle, "review_candidate", return_value=response) as delegate, \
             patch.object(cycle, "run_verified_next_round") as graph, \
             patch.object(cycle, "append_and_record") as writer:
            result = cycle.review(tenant="t", mailbox="m", folder="f",
                                  grant_file="grant", candidate="candidate",
                                  consent_local_read=True)
            self.assertEqual(result["phase"], "review")
            self.assertEqual(result["event_snapshots"], 2)
            self.assertEqual(result["removal_markers_for_review"], 1)
            self.assertEqual(result["provider_requests"], 0)
            self.assertEqual(result["owg_writes"], 0)
            self.assertFalse(result["checkpoint_advanced"])
            self.assertEqual(delegate.call_args.kwargs["tenant"], "t")
            graph.assert_not_called()
            writer.assert_not_called()

    def test_review_cli_uses_only_scope_and_private_artifacts(self):
        import contextlib
        import io
        import json
        argv = ["review", "--tenant", "t", "--mailbox", "m",
                "--folder", "f", "--grant-file", "grant",
                "--candidate", "candidate", "--consent-local-read"]
        with patch.object(cycle, "review_candidate", return_value={
            "status": "review_ready", "event_snapshots": 1,
            "removal_markers_for_review": 0,
        }) as delegate:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cycle.main(argv), 0)
            self.assertEqual(json.loads(output.getvalue())["phase"], "review")
            self.assertEqual(delegate.call_args.kwargs["candidate"],
                             __import__("pathlib").Path("candidate"))
            self.assertNotIn("session", delegate.call_args.kwargs)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cycle.main(argv[:-1]), 2)
            self.assertEqual(delegate.call_count, 1)

    def test_review_unexpected_delegate_status_refuses(self):
        with patch.object(cycle, "review_candidate",
                          return_value={"status": "incomplete"}):
            with self.assertRaises(ReceiptError):
                cycle.review(consent_local_read=True)

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

    def test_recover_requires_separate_consents_before_readback(self):
        with patch.object(cycle, "recovery_triage") as delegate:
            with self.assertRaises(ReceiptError):
                cycle.recover(consent_local_read=True)
            delegate.assert_not_called()

    def test_recover_cli_verified_and_uncertain_have_distinct_exit_codes(self):
        import contextlib
        import io
        import json
        argv = ["recover", "--tenant", "t", "--mailbox", "m", "--folder", "f",
                "--log-id", "log", "--confirm-log-id", "log",
                "--grant-file", "grant", "--candidate", "candidate",
                "--csv-path", "events.csv", "--receipt-file", "receipt",
                "--consent-local-read", "--confirm-removals-reviewed"]
        with patch.dict("os.environ", {"OWG_DASHBOARD_SESSION": "session"}), \
             patch.object(cycle, "recovery_triage", return_value={
                 "status": "receipt_verified", "next_action": "operator_may_separately_prepare_handoff"
             }) as delegate, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cycle.main(argv), 0)
            self.assertEqual(json.loads(out.getvalue())["phase"], "recover")
            self.assertEqual(delegate.call_args.kwargs["session"], "session")
        with patch.object(cycle, "recovery_triage", return_value={
            "status": "manual_reconciliation_required",
            "next_action": "inspect_authenticated_owg_append_history_and_provider_grant"
        }), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cycle.main(argv), 3)
            self.assertFalse(json.loads(out.getvalue())["automatic_retry_allowed"])

    def test_handoff_requires_independent_removal_review_before_readback(self):
        with patch.object(cycle, "prepare_handoff") as delegate:
            with self.assertRaises(ReceiptError):
                cycle.handoff(consent_local_read=True, tenant="t")
            delegate.assert_not_called()

    def test_handoff_uses_production_verified_readback_without_graph_or_append(self):
        response = {"status": "next_round_handoff_prepared", "idempotent_replay": False}
        with patch.object(cycle, "prepare_handoff", return_value=response) as delegate, \
             patch.object(cycle, "run_verified_next_round") as graph, \
             patch.object(cycle, "append_and_record") as writer:
            result = cycle.handoff(consent_local_read=True,
                                   confirm_removals_reviewed=True,
                                   tenant="t", successor_label="round-2")
            self.assertEqual(result["status"], "next_round_handoff_prepared")
            self.assertFalse(result["checkpoint_advanced"])
            self.assertEqual(result["provider_requests"], 0)
            self.assertFalse(result["automatic_retry"])
            self.assertEqual(delegate.call_args.kwargs["successor_label"], "round-2")
            graph.assert_not_called()
            writer.assert_not_called()

    def test_handoff_refuses_incomplete_delegate_status(self):
        with patch.object(cycle, "prepare_handoff", return_value={"status": "unknown"}):
            with self.assertRaises(ReceiptError):
                cycle.handoff(consent_local_read=True,
                              confirm_removals_reviewed=True)

    def test_handoff_cli_requires_explicit_confirmation_and_auth_session(self):
        with patch.object(cycle, "prepare_handoff", return_value={
            "status": "next_round_handoff_prepared", "idempotent_replay": True,
        }) as delegate, patch.dict("os.environ", {"OWG_DASHBOARD_SESSION": "session"}):
            import contextlib
            import io
            argv = ["handoff", "--tenant", "t", "--mailbox", "m",
                    "--folder", "f", "--log-id", "log", "--confirm-log-id", "log",
                    "--grant-file", "grant", "--candidate", "candidate",
                    "--csv-path", "events.csv", "--receipt-file", "receipt",
                    "--journal-dir", "journal", "--handoff-dir", "handoff",
                    "--successor-label", "round-2", "--consent-local-read",
                    "--confirm-removals-reviewed"]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cycle.main(argv), 0)
            self.assertEqual(__import__("json").loads(out.getvalue())["phase"], "handoff")
            self.assertEqual(delegate.call_args.kwargs["session"], "session")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cycle.main(argv[:-1]), 2)
            self.assertEqual(delegate.call_count, 1)

    def test_digest_bound_append_refuses_mismatch_before_write(self):
        digest = "a" * 64
        with patch.object(cycle, "review_candidate", return_value={
            "status": "review_ready", "csv_sha256": "b" * 64
        }) as review, patch.object(cycle, "append_and_record") as writer:
            with self.assertRaisesRegex(ReceiptError, "differs"):
                cycle.append(consent_local_read=True, consent_local_append=True,
                             confirm_candidate_reviewed=True,
                             expected_review_csv_sha256=digest,
                             tenant="t", mailbox="m", folder="f",
                             grant_file="grant", candidate="candidate")
            review.assert_called_once()
            writer.assert_not_called()

    def test_digest_bound_append_rechecks_and_delegates(self):
        digest = "a" * 64
        with patch.object(cycle, "review_candidate", return_value={
            "status": "review_ready", "csv_sha256": digest
        }), patch.object(cycle, "append_and_record", return_value={
            "status": "receipt_persisted", "log_id": "log", "events_after": 2
        }) as writer:
            result = cycle.append(consent_local_read=True, consent_local_append=True,
                                  confirm_candidate_reviewed=True,
                                  expected_review_csv_sha256=digest,
                                  tenant="t", mailbox="m", folder="f",
                                  grant_file="grant", candidate="candidate",
                                  log_id="log")
            self.assertEqual(result["status"], "receipt_persisted")
            self.assertNotIn("expected_review_csv_sha256", writer.call_args.kwargs)

    def test_digest_bound_append_rejects_bad_digest_before_read_or_write(self):
        with patch.object(cycle, "review_candidate") as review, patch.object(
                cycle, "append_and_record") as writer:
            with self.assertRaisesRegex(ReceiptError, "Invalid reviewed"):
                cycle.append(consent_local_read=True, consent_local_append=True,
                             confirm_candidate_reviewed=True,
                             expected_review_csv_sha256="INVALID")
            review.assert_not_called()
            writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
