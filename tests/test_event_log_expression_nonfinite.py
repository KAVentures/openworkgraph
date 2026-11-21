"""Regression: derived numeric case metrics never expose non-finite values."""
import duckdb

from server.event_log_expressions import Arithmetic, materialize


def _metric(operation):
    return Arithmetic.model_validate({
        "name": "derived", "kind": "arithmetic", "operation": operation,
        "left": {"kind": "metric", "name": "left"},
        "right": {"kind": "metric", "name": "right"},
    })


def test_nonfinite_arithmetic_is_null_with_counts():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE qc(case_id VARCHAR, lhs DOUBLE, rhs DOUBLE, derived DOUBLE)")
    conn.executemany("INSERT INTO qc VALUES (?, ?, ?, NULL)", [
        ("overflow", 1e308, 1e308),
        ("normal", 4.0, 2.0),
        ("missing", None, 2.0),
    ])
    result = materialize(conn, _metric("multiply"), {"left": "lhs", "right": "rhs"}, "derived")
    assert result["cases_with_non_finite_value"] == 1
    assert result["cases_with_missing_operand"] == 1
    assert conn.execute("SELECT case_id, derived FROM qc ORDER BY case_id").fetchall() == [
        ("missing", None), ("normal", 8.0), ("overflow", None),
    ]


def test_division_zero_and_overflow_are_separate():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE qc(case_id VARCHAR, lhs DOUBLE, rhs DOUBLE, derived DOUBLE)")
    conn.executemany("INSERT INTO qc VALUES (?, ?, ?, NULL)", [
        ("zero", 1.0, 0.0), ("overflow", 1e308, 1e-308), ("ok", 6.0, 3.0),
    ])
    result = materialize(conn, _metric("divide"), {"left": "lhs", "right": "rhs"}, "derived")
    assert result["cases_with_zero_divisor"] == 1
    assert result["cases_with_non_finite_value"] == 1
    assert conn.execute("SELECT case_id, derived FROM qc ORDER BY case_id").fetchall() == [
        ("ok", 2.0), ("overflow", None), ("zero", None),
    ]
