"""Versioned, nonrecursive arithmetic for case metrics. No expression text is executed."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricOperand(_Strict):
    kind: Literal["metric"]
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class ConstantOperand(_Strict):
    kind: Literal["constant"]
    value: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    unit: Annotated[str, Field(pattern=r"^[A-Za-z%$€£¥/._ -]{1,20}$")] = "ratio"


Operand = Annotated[MetricOperand | ConstantOperand, Field(discriminator="kind")]


class Arithmetic(_Strict):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
    kind: Literal["arithmetic"]
    expression_version: Literal["1"] = "1"
    operation: Literal["add", "subtract", "multiply", "divide"]
    left: Operand
    right: Operand

    @model_validator(mode="after")
    def no_self_reference(self):
        # Even a symbolic library template cannot ever resolve its own name to
        # an earlier definition: analysis names must be unique.
        if any(o.kind == "metric" and o.name == self.name for o in (self.left, self.right)):
            raise ValueError("arithmetic cannot reference its own name")
        return self


def _atomic(unit: str) -> dict[str, int]:
    # Labels are opaque: EUR/hour is one declared unit, never silently parsed or converted.
    return {} if unit == "ratio" else {unit: 1}


def units(metrics) -> dict[str, dict[str, int]]:
    """Validate dependency order and units before opening/reading the dataset."""
    known = {}
    for metric in metrics:
        if metric.kind != "arithmetic":
            unit = "seconds" if metric.kind == "time_between" else "events" if metric.kind == "activity_count" else metric.unit
            known[metric.name] = _atomic(unit)
            continue
        operands = []
        for operand in (metric.left, metric.right):
            if operand.kind == "constant":
                operands.append(_atomic(operand.unit))
            else:
                if operand.name not in known:
                    raise ValueError(f"arithmetic metric {metric.name!r} needs earlier metric {operand.name!r}; forward, self and cyclic references are not allowed")
                operands.append(known[operand.name])
        left, right = operands
        if metric.operation in {"add", "subtract"}:
            if left != right:
                raise ValueError(f"arithmetic metric {metric.name!r} can only add or subtract identical units; no conversion is performed")
            result = dict(left)
        else:
            result = dict(left)
            for label, exponent in right.items():
                result[label] = result.get(label, 0) + exponent * (1 if metric.operation == "multiply" else -1)
            result = {label: exponent for label, exponent in result.items() if exponent}
        known[metric.name] = result
    return known


def unit_text(powers: dict[str, int]) -> str:
    if not powers:
        return "ratio"
    if len(powers) == 1 and next(iter(powers.values())) == 1:
        return next(iter(powers))
    return " * ".join(f"({label})" + (f"^{exponent}" if exponent != 1 else "")
                      for label, exponent in sorted(powers.items()))


def materialize(conn, metric: Arithmetic, columns: dict[str, str], column: str) -> dict:
    """One bounded vector operation per selected case; identifiers come only from the planner."""
    params, inputs = [], []
    for operand in (metric.left, metric.right):
        if operand.kind == "constant":
            inputs.append("?::DOUBLE")
            params.append(operand.value)
        else:
            inputs.append(columns[operand.name])
    conn.execute(f"CREATE OR REPLACE TEMP TABLE qexpr AS SELECT case_id, {inputs[0]} AS a, {inputs[1]} AS b FROM qc", params)
    missing, zeros = conn.execute("""SELECT count(*) FILTER (WHERE a IS NULL OR b IS NULL),
        count(*) FILTER (WHERE a IS NOT NULL AND b = 0) FROM qexpr""").fetchone()
    sql = {"add": "a + b", "subtract": "a - b", "multiply": "a * b", "divide": "a / nullif(b, 0)"}[metric.operation]
    conn.execute(f"UPDATE qc SET {column} = {sql} FROM qexpr WHERE qc.case_id = qexpr.case_id")
    return {"cases_with_missing_operand": missing,
            "cases_with_zero_divisor": zeros if metric.operation == "divide" else 0}
