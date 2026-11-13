"""Saved metric lineage identities must be reproducible and content-bound."""
import unittest

from server.event_log_query import _metric_lineage


class SavedMetricLineageTests(unittest.TestCase):
    def test_canonical_definition_identity(self):
        first = {"format": "case-metric-v1", "metric": {"name": "cost", "kind": "activity_count"}}
        reordered = {"metric": {"kind": "activity_count", "name": "cost"}, "format": "case-metric-v1"}
        self.assertEqual(_metric_lineage(first), _metric_lineage(reordered))
        self.assertEqual(len(_metric_lineage(first)["definition_sha256"]), 64)

    def test_changed_definition_invalidates_lineage(self):
        before = {"format": "case-metric-v1", "metric": {"name": "cost", "kind": "activity_count"}}
        after = {"format": "case-metric-v1", "metric": {"name": "cost", "kind": "time_between"}}
        self.assertNotEqual(_metric_lineage(before), _metric_lineage(after))

    def test_unicode_is_deterministic(self):
        definition = {"format": "case-metric-v1", "metric": {"name": "cost", "unit": "€"}}
        self.assertEqual(_metric_lineage(definition), _metric_lineage(definition))


if __name__ == "__main__":
    unittest.main()
