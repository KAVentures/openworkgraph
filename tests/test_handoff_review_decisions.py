"""Source contract tests for the local handoff decision UX; browser acceptance is separate."""
from pathlib import Path
import re
import unittest

HTML = Path(__file__).resolve().parents[1] / "dashboard" / "handoff_review_workspace.html"

class HandoffDecisionContract(unittest.TestCase):
    def test_review_only_receipt_and_digest(self):
        page = HTML.read_text(encoding="utf-8")
        for expected in (
            "openworkgraph.handoff-review-decision.v1",
            "baseline_packet_sha256:current.digest",
            "crypto.subtle.digest('SHA-256',bytes)",
            "execution_authorized:false,source_read_authorized:false",
            "consent_verified:false,owner_approval_verified:false,server_freshness_verified:false",
            "activeReview=null",
            "current.generation!==reviewGeneration",
        ):
            self.assertIn(expected,page)
    def test_no_persistent_storage_or_network(self):
        page = HTML.read_text(encoding="utf-8")
        self.assertNotRegex(page,r"\\b(?:fetch|XMLHttpRequest|localStorage|sessionStorage)\\s*[.(]")
    def test_dispositions_are_bounded(self):
        page = HTML.read_text(encoding="utf-8")
        for disposition in ("needs_evidence","escalate","not_applicable"):
            self.assertIn(disposition,page)
        self.assertIn("controls.some(x=>!x.value)",page)

if __name__ == "__main__":
    unittest.main()
