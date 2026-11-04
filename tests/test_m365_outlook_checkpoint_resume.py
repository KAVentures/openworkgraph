"""Checkpoint-gated Microsoft Graph extraction acceptance tests (no network)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.m365_outlook_checkpoint_resume import extract_from_checkpoint
from server.m365_outlook_checkpoint_commit import FORMAT as CHECKPOINT_FORMAT
from server.m365_outlook_delta import DeltaError, FORMAT as DELTA_FORMAT, _scope


class CheckpointResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.tenant, self.mailbox, self.folder = "tenant", "mailbox", "folder"
        self.scope = _scope(self.tenant, self.mailbox, self.folder)
        self.grant = self.root / "grant.json"
        self.prior = self.root / "prior.json"
        self.csv = self.root / "prior.csv"
        self.checkpoint = self.root / "checkpoint-one.json"
        self.output = self.root / "next.csv"
        self.candidate = self.root / "next.json"
        self.cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                       "messages/delta?%24deltatoken=abc")
        self.write(self.grant, {
            "version": 1, "enabled": True, "consented": True,
            "tenant_scope": self.tenant, "mailbox_scope": self.mailbox,
            "folder_id": self.folder,
        })
        self.csv.write_bytes(b"case_id,activity,timestamp,source_event_id\\n")
        self.csv.chmod(0o600)
        csv_sha = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        self.write(self.prior, {
            "format": DELTA_FORMAT, "scope_sha256": self.scope,
            "csv_name": "prior.csv", "csv_sha256": csv_sha,
            "cursor_url": self.cursor,
            "status": "candidate_requires_confirmed_import",
            "events": 0, "removals": 0, "removed_case_ids": [], "pages": 1,
        })
        self.write(self.checkpoint, {
            "format": CHECKPOINT_FORMAT, "status": "operator_checkpoint_committed",
            "scope_sha256": self.scope, "log_id": "log_0123456789abcdef",
            "candidate_sha256": hashlib.sha256(self.prior.read_bytes()).hexdigest(),
            "csv_sha256": csv_sha, "cursor_url": self.cursor,
            "removal_markers_reviewed_by_operator": True,
            "event_level_proof": False, "provider_requests": 0,
            "owg_write_requests": 0,
        })

    def write(self, path, data):
        path.write_text(json.dumps(data, sort_keys=True) + "\\n")
        path.chmod(0o600)

    def run_resume(self, **overrides):
        params = dict(
            tenant=self.tenant, mailbox=self.mailbox, folder=self.folder,
            grant_file=self.grant, checkpoint_file=self.checkpoint,
            previous_candidate=self.prior, output=self.output,
            candidate=self.candidate, token="opaque",
            consent_read=True, consent_export=True,
            confirm_checkpoint_resume=True,
        )
        params.update(overrides)
        return extract_from_checkpoint(**params)

    def test_validated_checkpoint_calls_production_delta_reader(self):
        with patch("server.m365_outlook_checkpoint_resume.export_delta",
                   return_value={"status": "review_ready", "checkpoint_advanced": False}) as delegate:
            result = self.run_resume()
        self.assertEqual(result["resume_source"], "committed_operator_checkpoint")
        self.assertFalse(result["automatic_owg_append"])
        self.assertEqual(delegate.call_args.kwargs["previous_candidate"], self.prior)
        self.assertTrue(delegate.call_args.kwargs["confirm_previous_import"])
        self.assertEqual(delegate.call_args.kwargs["folder_id"], self.folder)
        self.assertEqual(delegate.call_args.kwargs["token"], "opaque")

    def test_no_read_without_all_consents(self):
        for key in ("consent_read", "consent_export", "confirm_checkpoint_resume"):
            with self.subTest(key=key), patch(
                "server.m365_outlook_checkpoint_resume.export_delta"
            ) as delegate:
                with self.assertRaises(DeltaError):
                    self.run_resume(**{key: False})
                delegate.assert_not_called()

    def test_revoked_grant_refuses(self):
        grant = json.loads(self.grant.read_text())
        grant["enabled"] = False
        self.write(self.grant, grant)
        with patch("server.m365_outlook_checkpoint_resume.export_delta") as delegate:
            with self.assertRaises(DeltaError):
                self.run_resume()
            delegate.assert_not_called()

    def test_tampered_candidate_refuses(self):
        data = json.loads(self.prior.read_text())
        data["events"] = 1
        self.write(self.prior, data)
        with patch("server.m365_outlook_checkpoint_resume.export_delta") as delegate:
            with self.assertRaises(DeltaError):
                self.run_resume()
            delegate.assert_not_called()

    def test_cross_scope_and_wrong_cursor_refuse(self):
        with patch("server.m365_outlook_checkpoint_resume.export_delta") as delegate:
            with self.assertRaises(DeltaError):
                self.run_resume(mailbox="other")
            entry = json.loads(self.checkpoint.read_text())
            entry["cursor_url"] += "changed"
            self.write(self.checkpoint, entry)
            with self.assertRaises(DeltaError):
                self.run_resume()
            delegate.assert_not_called()

    def test_missing_checkpoint_and_path_collision_refuse(self):
        with patch("server.m365_outlook_checkpoint_resume.export_delta") as delegate:
            with self.assertRaises(DeltaError):
                self.run_resume(checkpoint_file=self.root / "missing.json")
            with self.assertRaises(DeltaError):
                self.run_resume(output=self.prior)
            delegate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
