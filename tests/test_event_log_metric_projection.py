"""Safety and aggregation acceptance for bounded R9 numeric projection."""
import pytest

from server import event_log_derived_metrics as metrics
from server.event_log_metric_projection import summarize_projected


DEFINITION = {"version": 1, "expression": {"field": "duration"}}


def test_projects_only_approved_numeric_values_and_never_returns_private_rows():
    rows = [
        {"duration": 2, "customer_email": "secret@example.com", "case_id": "private-a"},
        {"duration": "4", "customer_email": "another@example.com", "case_id": "private-b"},
        {"customer_email": "third@example.com"},
    ]
    result = summarize_projected(DEFINITION, rows, {"duration"})
    assert result["rows"] == 3
    assert result["non_null"] == 2
    assert result["null"] == 1
    assert result["sum"] == "6"
    assert result["mean"] == "3"
    assert "secret" not in repr(result)
    assert "private-" not in repr(result)


@pytest.mark.parametrize("fields", [set(), {"duration", "bad" * 50}, {"duration", 3}])
def test_invalid_allowlists_fail_closed(fields):
    with pytest.raises(metrics.MetricDefinitionError):
        summarize_projected(DEFINITION, [], fields)


def test_unapproved_expression_fails_closed():
    definition = {"version": 1, "expression": {"field": "private_value"}}
    with pytest.raises(metrics.MetricDefinitionError):
        summarize_projected(definition, [{"private_value": 10}], {"duration"})


@pytest.mark.parametrize("value", [True, [], {}, object()])
def test_unsupported_values_fail_closed(value):
    with pytest.raises(metrics.MetricDefinitionError):
        summarize_projected(DEFINITION, [{"duration": value}], {"duration"})


def test_oversize_and_nonmapping_rows_fail_closed():
    with pytest.raises(metrics.MetricDefinitionError):
        summarize_projected(DEFINITION, [{}] * (metrics.MAX_BATCH_ROWS + 1), {"duration"})
    with pytest.raises(metrics.MetricDefinitionError):
        summarize_projected(DEFINITION, [42], {"duration"})


def test_divide_by_zero_produces_null_not_exception():
    definition = {
        "version": 1,
        "expression": {
            "divide": [{"field": "duration"}, {"field": "denominator"}]
        },
    }
    result = summarize_projected(
        definition,
        [{"duration": 8, "denominator": 0}, {"duration": 6, "denominator": 2}],
        {"duration", "denominator"},
    )
    assert result["null"] == 1
    assert result["sum"] == "3"
