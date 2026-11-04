"""Focused refusal, arithmetic and lineage tests for derived metrics."""
import unittest

from server.event_log_derived_metrics import MetricDefinitionError, evaluate, validate


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

    def test_mutation_requires_revalidation(self):
        expr = metric({"field": "public"})
        self.assertEqual(evaluate(expr, {"public": 1}, {"public"}), "1")
        expr["expression"] = {"field": "private"}
        with self.assertRaises(MetricDefinitionError):
            evaluate(expr, {"private": 2}, {"public"})


if __name__ == "__main__":
    unittest.main()
