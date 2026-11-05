"""Static safety and accessibility contract for the standalone local handoff review UI."""
from pathlib import Path
import unittest

HTML = (Path(__file__).resolve().parents[1] / "dashboard" / "handoff_review_workspace.html").read_text()

class HandoffReviewWorkspaceTests(unittest.TestCase):
    def test_local_only_and_non_executing(self):
        self.assertNotIn("fetch(", HTML)
        self.assertNotIn("XMLHttpRequest", HTML)
        self.assertNotIn("localStorage", HTML)
        self.assertNotIn("sessionStorage", HTML)
        self.assertNotIn("eval(", HTML)
        self.assertIn("execution_authorized!==false", HTML)
        self.assertIn("source_read_authorized!==false", HTML)
        self.assertIn("human_review_required!==true", HTML)
        self.assertIn("MAX_BYTES=256*1024", HTML)

    def test_safe_dom_and_keyboard_controls(self):
        self.assertNotIn("innerHTML", HTML)
        self.assertNotIn("document.write", HTML)
        self.assertIn("textContent=", HTML)
        self.assertIn('role="status"', HTML)
        self.assertIn('aria-live="polite"', HTML)
        self.assertIn('type="button"', HTML)
        self.assertIn('for="baseline"', HTML)
        self.assertIn('for="candidate"', HTML)

    def test_cross_project_and_evidence_drift_refused_or_visible(self):
        self.assertIn('a.idea.idea_id!==b.idea.idea_id', HTML)
        self.assertIn('a.project.project_id!==b.project.project_id', HTML)
        self.assertIn("['Dataset origins'", HTML)
        self.assertIn("['Reported specifications'", HTML)
        self.assertIn("['Source-check exports'", HTML)
        self.assertIn('dataset_origins_truncated', HTML)
        self.assertIn('reported_specifications_truncated', HTML)

    def test_superseded_async_reads_cannot_mix_results(self):
        self.assertIn('let reviewGeneration=0', HTML)
        self.assertIn("addEventListener('change'", HTML)
        self.assertIn('const generation=++reviewGeneration', HTML)
        self.assertIn('if(generation!==reviewGeneration)return', HTML)
        self.assertIn('if(generation===reviewGeneration)', HTML)

    def test_review_and_comparison_are_functional(self):
        self.assertIn("JSON.parse(await file.text())", HTML)
        self.assertIn("function validate(p)", HTML)
        self.assertIn("function compare(parent,a,b)", HTML)
        self.assertIn("details.replaceChildren()", HTML)
        self.assertIn("results.hidden=false", HTML)
        self.assertIn("Review refused:", HTML)

if __name__ == "__main__":
    unittest.main()
