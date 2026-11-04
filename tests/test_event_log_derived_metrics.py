"""Focused refusal, arithmetic and lineage tests for derived metrics."""
import unittest

from server.event_log_derived_metrics import MetricDefinitionError, evaluate, summarize_rows, validate


def metric(expr):
    return {"version": 1, "expression": expr}


class DerivedMetricTests(unittest.TestCase):
    def test_arithmetic_and_digest(self):
        expr = metric({"divide": [{"field": "cost"}, {"field": "units"}]})
        self.assertEqual(evaluate(expr, {"cost": "12.50", "units": 5}, {"cost", "units"}), "2.50")
        self.assertEqual(validate(expr, {"cost", "units"}), validate(expr, {"units", "cost"}))
        self.assertEqual(len(validate(expr, {"cost", "units"})), 64)

    def test_null_and_zero_division(self):
        expr = metric({"divide": [{"field": "cost"}, {"field": "units"}]})
        self.assertIsNone(evaluate(expr, {"cost": 5, "units": 0}, {"cost", "units"}))
        self.assertIsNone(evaluate(expr, {"cost": 5}, {"cost", "units"}))
        fallback = metric({"coalesce": [{"field": "missing"}, {"literal": "3.25"}]})
        self.assertEqual(evaluate(fallback, {}, {"missing"}), "3.25")

    def test_refuses_unapproved_fields_and_code(self):
        for expr in [
            {"field": "secret"},
            {"eval": "__import__('os').system('true')"},
            {"add": [{"literal": 1}]},
            {"literal": True},
            {"literal": "NaN"},
        ]:
            with self.subTest(expr=expr), self.assertRaises(MetricDefinitionError):
                evaluate(metric(expr), {}, {"approved"})

    def test_budget_and_version(self):
        expr = {"literal": 1}
        for _ in range(14):
            expr = {"add": [expr, {"literal": 1}]}
        with self.assertRaises(MetricDefinitionError):
            validate(metric(expr), set())
        with self.assertRaises(MetricDefinitionError):
            validate({"version": 2, "expression": {"literal": 1}}, set())

    def test_bounded_authorized_cohort_summary(self):
        expr = metric({"divide": [{"field": "cost"}, {"field": "units"}]})
        summary = summarize_rows(expr, [
            {"cost": 10, "units": 2},
            {"cost": 12, "units": 3},
            {"cost": 9, "units": 0},
        ], {"cost", "units"})
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["non_null"], 2)
        self.assertEqual(summary["null"], 1)
        self.assertEqual(summary["sum"], "9")
        self.assertEqual(summary["mean"], "4.5")
        self.assertEqual(summary["min"], "4")
        self.assertEqual(summary["max"], "5")
        self.assertEqual(len(summary["definition_sha256"]), 64)
        self.assertNotIn("cost", summary)

    def test_cohort_refusals_and_empty(self):
        expr = metric({"field": "approved"})
        empty = summarize_rows(expr, [], {"approved"})
        self.assertIsNone(empty["mean"])
        self.assertEqual(empty["null"], 0)
        for rows in [[{}] * 1001, [{"approved": 1}, "bad"], "not-a-list"]:
            with self.subTest(rows_type=type(rows).__name__), self.assertRaises(MetricDefinitionError):
                summarize_rows(expr, rows, {"approved"})
        with self.assertRaises(MetricDefinitionError):
            summarize_rows(expr, [{"secret": 3}], {"secret"}) if False else summarize_rows(
                metric({"field": "secret"}), [{"secret": 3}], {"approved"}
            )

    def test_extreme_exponents_and_aggregate_overflow_refused(self):
        for literal in ("1e999999", "1e-999999", "1e19"):
            with self.subTest(literal=literal), self.assertRaises(MetricDefinitionError):
                evaluate(metric({"literal": literal}), {}, set())
        huge_product = metric({"multiply": [
            {"literal": "1e18"}, {"literal": "1e18"}
        ]})
        self.assertIsNone(evaluate(huge_product, {}, set()))
        with self.assertRaises(MetricDefinitionError):
            summarize_rows(metric({"field": "x"}), [{"x": "1e18"}] * 11, {"x"})

    def test_mutation_requires_revalidation(self):
        expr = metric({"field": "public"})
        self.assertEqual(evaluate(expr, {"public": 1}, {"public"}), "1")
        expr["expression"] = {"field": "private"}
        with self.assertRaises(MetricDefinitionError):
            evaluate(expr, {"private": 2}, {"public"})


if __name__ == "__main__":
    unittest.main()
