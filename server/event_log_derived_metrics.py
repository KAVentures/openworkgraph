"""Bounded, typed, deterministic derived metrics for local event-log analysis.

Definitions contain only field, literal, add, subtract, multiply, divide and
coalesce nodes. No SQL, eval, dynamic attribute access or external I/O.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

VERSION = 1
MAX_NODES = 64
MAX_DEPTH = 12
MAX_FIELDS = 32
MAX_NUMBER_DIGITS = 24
MAX_DECIMAL_MAGNITUDE = 18
OPERATORS = {"add", "subtract", "multiply", "divide", "coalesce"}


class MetricDefinitionError(ValueError):
    """Unsupported or unsafe metric definition."""


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise MetricDefinitionError("numeric value required")
    if isinstance(value, float) and not math.isfinite(value):
        raise MetricDefinitionError("non-finite number")
    raw = str(value)
    if len(raw) > MAX_NUMBER_DIGITS:
        raise MetricDefinitionError("numeric literal too long")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise MetricDefinitionError("invalid number") from exc
    if not result.is_finite():
        raise MetricDefinitionError("non-finite number")
    if result and abs(result.adjusted()) > MAX_DECIMAL_MAGNITUDE:
        raise MetricDefinitionError("numeric magnitude exceeds safe bound")
    return result


def _validate(node: Any, fields: frozenset[str], budget: list[int], depth: int) -> None:
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        raise MetricDefinitionError("expression budget exceeded")
    if not isinstance(node, dict) or len(node) != 1:
        raise MetricDefinitionError("each expression must have exactly one key")
    op, arg = next(iter(node.items()))
    if op == "field":
        if not isinstance(arg, str) or arg not in fields:
            raise MetricDefinitionError("field not explicitly allowed")
    elif op == "literal":
        _number(arg)
    elif op in OPERATORS:
        if not isinstance(arg, list) or len(arg) != 2:
            raise MetricDefinitionError("binary operator requires two arguments")
        for child in arg:
            _validate(child, fields, budget, depth + 1)
    else:
        raise MetricDefinitionError("unsupported operator")


def validate(definition: Mapping[str, Any], allowed_fields: set[str]) -> str:
    """Validate definition and return a stable SHA256 fingerprint."""
    if not isinstance(definition, dict) or set(definition) != {"version", "expression"}:
        raise MetricDefinitionError("version and expression required")
    if definition["version"] != VERSION or isinstance(definition["version"], bool):
        raise MetricDefinitionError("unsupported version")
    if not isinstance(allowed_fields, set) or len(allowed_fields) > MAX_FIELDS or any(
        not isinstance(field, str) or not field or len(field) > 128 for field in allowed_fields
    ):
        raise MetricDefinitionError("invalid field allowlist")
    _validate(definition["expression"], frozenset(allowed_fields), [MAX_NODES], 0)
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluate(node: dict[str, Any], row: Mapping[str, Any]) -> Decimal | None:
    op, arg = next(iter(node.items()))
    if op == "literal":
        return _number(arg)
    if op == "field":
        return _number(row.get(arg))
    left = _evaluate(arg[0], row)
    if op == "coalesce":
        return left if left is not None else _evaluate(arg[1], row)
    right = _evaluate(arg[1], row)
    if left is None or right is None:
        return None
    try:
        if op == "add":
            value = left + right
        elif op == "subtract":
            value = left - right
        elif op == "multiply":
            value = left * right
        else:
            if right == 0:
                return None
            value = left / right
    except (InvalidOperation, OverflowError, ZeroDivisionError):
        return None
    return value if value.is_finite() and (not value or abs(value.adjusted()) <= MAX_DECIMAL_MAGNITUDE) else None


def evaluate(
    definition: Mapping[str, Any],
    row: Mapping[str, Any],
    allowed_fields: set[str],
) -> str | None:
    """Return an exact decimal string or None for null/zero-denominator.

    Validate per call so a mutated definition cannot bypass the allowlist.
    Callers must enforce their own authorization, tenant and row scopes.
    """
    validate(definition, allowed_fields)
    if not isinstance(row, Mapping):
        raise MetricDefinitionError("row must be a mapping")
    value = _evaluate(definition["expression"], row)
    return str(value) if value is not None else None


MAX_BATCH_ROWS = 1000


def summarize_rows(
    definition: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    allowed_fields: set[str],
) -> dict[str, Any]:
    """Summarize a bounded authorized numeric cohort without returning raw rows.

    Caller supplies only consented, tenant-scoped, retention-valid rows. The
    definition fingerprint is lineage metadata, not an access-control token.
    """
    digest = validate(definition, allowed_fields)
    if not isinstance(rows, list) or len(rows) > MAX_BATCH_ROWS:
        raise MetricDefinitionError("row budget exceeded")
    total = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    non_null = 0
    for row in rows:
        if not isinstance(row, Mapping):
            raise MetricDefinitionError("row must be a mapping")
        value = _evaluate(definition["expression"], row)
        if value is None:
            continue
        non_null += 1
        total += value
        if total and abs(total.adjusted()) > MAX_DECIMAL_MAGNITUDE:
            raise MetricDefinitionError("aggregate magnitude exceeds safe bound")
        minimum = value if minimum is None else min(minimum, value)
        maximum = value if maximum is None else max(maximum, value)
    return {
        "definition_sha256": digest,
        "rows": len(rows),
        "non_null": non_null,
        "null": len(rows) - non_null,
        "sum": str(total) if non_null else None,
        "mean": str(total / non_null) if non_null else None,
        "min": str(minimum) if minimum is not None else None,
        "max": str(maximum) if maximum is not None else None,
    }
