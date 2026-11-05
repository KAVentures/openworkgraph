"""Strict, bounded numeric projection for previously authorized event-log rows.

This module is not an authorization boundary. The caller MUST obtain rows via
an authenticated, tenant-scoped, retention-valid saved-log read before calling.
No source identifiers, free text, or raw rows are returned to the consumer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import event_log_derived_metrics as metrics

MAX_FIELDS = 16


def summarize_projected(
    definition: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    numeric_fields: set[str],
) -> dict[str, Any]:
    """Summarize an explicit numeric projection; never return source rows.

    Only names in the caller's approved numeric field set can enter the
    evaluator. Unsupported types fail closed rather than being coerced from
    private object/text values. Missing values are represented as null.
    """
    if (
        not isinstance(numeric_fields, set)
        or not numeric_fields
        or len(numeric_fields) > MAX_FIELDS
        or any(not isinstance(f, str) or not f or len(f) > 128 for f in numeric_fields)
    ):
        raise metrics.MetricDefinitionError("invalid numeric projection allowlist")
    metrics.validate(definition, numeric_fields)
    if (
        not isinstance(source_rows, (list, tuple))
        or len(source_rows) > metrics.MAX_BATCH_ROWS
    ):
        raise metrics.MetricDefinitionError("projection row budget exceeded")
    projected: list[dict[str, Any]] = []
    for source in source_rows:
        if not isinstance(source, Mapping):
            raise metrics.MetricDefinitionError("source row must be a mapping")
        row: dict[str, Any] = {}
        for field in numeric_fields:
            value = source.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float, str))
            ):
                raise metrics.MetricDefinitionError("non-numeric projected value")
            row[field] = value
        projected.append(row)
    return metrics.summarize_rows(definition, projected, numeric_fields)
