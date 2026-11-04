"""Focused authenticated M365 membership review refusal and parser tests."""
import csv
import io
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from server.m365_outlook_authenticated_membership import _source_keys, inspect, ReceiptError


def sourcecsv(rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["source_namespace", "source_event_id", "case_namespace",
                     "case_id", "activity", "timestamp", "resource", "attributes"])
    for row in rows:
        writer.writerow(row)
    return stream.getvalue().encode()


class AuthenticatedMembershipTests(unittest.TestCase):
    def test_source_keys(self):
        raw = sourcecsv([["m365_scope", "outlookevent:1", "m365_scope",
                          "case", "read", "2026-01-01", "", "{}"]])
        self.assertEqual(_source_keys(raw, "m365_scope"), ({"outlookevent:1"}, 1))

    def test_cross_scope_refused(self):
        raw = sourcecsv([["m365_other", "outlookevent:1", "m365_scope",
                          "case", "read", "2026-01-01", "", "{}"]])
        with self.assertRaises(ReceiptError):
            _source_keys(raw, "m365_scope")

    def test_duplicate_refused(self):
        row = ["m365_scope", "outlookevent:1", "m365_scope",
               "case", "read", "2026-01-01", "", "{}"]
        with self.assertRaises(ReceiptError):
            _source_keys(sourcecsv([row, row]), "m365_scope")

    def test_malformed_header_refused(self):
        with self.assertRaises(ReceiptError):
            _source_keys(b"source_event_id\\nfoo\\n", "m365_scope")

    def test_consent_refused_without_side_effect(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "authenticated-report.json"
            with self.assertRaises(ReceiptError):
                inspect(tenant="tenant", mailbox="mail", folder="inbox",
                        grant_file=Path(temp) / "grant.json",
                        review_file=Path(temp) / "review.json", output=output,
                        log_id="test", confirm_log_id="test", port=8765,
                        session="unused")
            self.assertFalse(output.exists())

    def test_bad_target_confirmation_refused(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ReceiptError):
                inspect(tenant="tenant", mailbox="mail", folder="inbox",
                        grant_file=Path(temp) / "grant.json",
                        review_file=Path(temp) / "review.json",
                        output=Path(temp) / "authenticated-report.json",
                        log_id="test", confirm_log_id="other", port=8765,
                        session="unused", consent_local_read=True,
                        consent_private_export=True)


if __name__ == "__main__":
    unittest.main()
