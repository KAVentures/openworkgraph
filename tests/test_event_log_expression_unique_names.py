"""R9: metric identifiers must be unambiguous across chained expressions."""
from types import SimpleNamespace

import pytest

from server.event_log_expressions import Arithmetic, units


def _base(name, unit="EUR"):
    return SimpleNamespace(name=name, kind="attribute", unit=unit)


def _derived(name, left, right):
    return Arithmetic.model_validate({
        "name": name, "kind": "arithmetic", "operation": "add",
        "left": {"kind": "metric", "name": left},
        "right": {"kind": "metric", "name": right},
    })


@pytest.mark.parametrize("definitions", [
    [_base("amount"), _base("amount", "USD")],
    [_base("amount"), _base("other"), _derived("total", "amount", "other"), _base("total")],
    [_base("amount"), _base("other"), _derived("total", "amount", "other"), _derived("total", "amount", "other")],
])
def test_duplicate_metric_names_are_rejected_before_query(definitions):
    with pytest.raises(ValueError, match="duplicate metric name"):
        units(definitions)


def test_distinct_chained_metrics_keep_unit_provenance():
    definitions = [_base("amount"), _base("other"),
                   _derived("total", "amount", "other"),
                   _derived("grand_total", "total", "amount")]
    result = units(definitions)
    assert list(result) == ["amount", "other", "total", "grand_total"]
    assert result["grand_total"] == {"EUR": 1}
