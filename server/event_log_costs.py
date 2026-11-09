"""Process cost from a user-supplied cost model.

The analyst states what one execution of each activity costs and, optionally,
what each hour a case stays open costs (a holding or delay cost). From that the
log yields cost per case, per activity, per variant and per resource, and the
cost of repeated executions: every execution of an activity after its first in
the same case.

All figures are the user's model applied to observed events. The log does not
record what work cost; a missing activity price is reported, never guessed,
and repeated executions are not verified rework.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError, field_validator

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-costs.v1"
MAX_ACTIVITIES = 500
MAX_PRICE = 1e9
_SEP = "\x1f"


def _price(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= MAX_PRICE:
        raise ValueError(f"must be a number from 0 to {MAX_PRICE:.0f}")
    return float(value)


class CostSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    activity_costs: dict[str, StrictFloat | StrictInt] = Field(default_factory=dict)
    default_activity_cost: StrictFloat | StrictInt | None = None
    hourly_open_case_cost: StrictFloat | StrictInt = 0
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    limit: StrictInt = Field(default=20, ge=1, le=200)

    @field_validator("activity_costs")
    @classmethod
    def _activities(cls, value: dict[str, Any]) -> dict[str, float]:
        if len(value) > MAX_ACTIVITIES:
            raise ValueError(f"at most {MAX_ACTIVITIES} activities")
        out = {}
        for name, price in value.items():
            if not name or len(name) > 200:
                raise ValueError("activity names must be 1 to 200 characters")
            out[name] = _price(price)
        return out

    @field_validator("default_activity_cost", "hourly_open_case_cost")
    @classmethod
    def _prices(cls, value: Any) -> float | None:
        return None if value is None else _price(value)


def _parse(spec: Any) -> CostSpec:
    try:
        parsed = CostSpec.model_validate(spec if spec is not None else {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "cost model is invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    if not parsed.activity_costs and parsed.default_activity_cost is None and not parsed.hourly_open_case_cost:
        raise workspace.EventLogWorkspaceError("give at least one activity cost, a default activity cost or an hourly cost")
    return parsed


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * q
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _money(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def costs(log_id: str, spec: Any = None) -> dict[str, Any]:
    model = _parse(spec)
    prices = [[name, price] for name, price in model.activity_costs.items()]
    default = model.default_activity_cost
    hourly = model.hourly_open_case_cost

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(model.cohort), log_id)
        conn.execute("CREATE OR REPLACE TEMP TABLE cost_prices (activity VARCHAR, price DOUBLE)")
        if prices:
            conn.executemany("INSERT INTO cost_prices VALUES (?, ?)", prices)
        conn.execute("""CREATE OR REPLACE TEMP TABLE cost_events AS
            SELECT ce.case_id, ce.activity, coalesce(ce.resource, '') AS resource, ce.ts, ce.event_id,
                   coalesce(p.price, ?) AS price, p.price IS NULL AS unpriced,
                   row_number() OVER (PARTITION BY ce.case_id, ce.activity ORDER BY ce.ts, ce.event_id) > 1 AS repeated
            FROM ce LEFT JOIN cost_prices p ON p.activity = ce.activity""", [default])
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE cost_cases AS
            SELECT case_id, coalesce(sum(price), 0) AS activity_cost,
                   coalesce(sum(price) FILTER (WHERE repeated), 0) AS repeat_cost,
                   epoch(max(ts) - min(ts)) / 3600.0 AS open_hours,
                   string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path
            FROM cost_events GROUP BY case_id""")
        cases = conn.execute("SELECT count(*) FROM cost_cases").fetchone()[0]
        if not cases:
            raise workspace.EventLogWorkspaceError("no cases match the filter")
        per_case = sorted(float(a) + float(h) * hourly for a, h in
                          conn.execute("SELECT activity_cost, open_hours FROM cost_cases").fetchall())
        activity_total, repeat_total, holding_hours = conn.execute(
            "SELECT sum(activity_cost), sum(repeat_cost), sum(open_hours) FROM cost_cases").fetchone()
        activities = conn.execute("""SELECT activity, count(*), count(DISTINCT case_id), any_value(price),
              bool_or(unpriced), sum(price), sum(price) FILTER (WHERE repeated), count(*) FILTER (WHERE repeated)
            FROM cost_events GROUP BY 1 ORDER BY coalesce(sum(price), 0) DESC, 2 DESC, 1""").fetchall()
        unpriced = conn.execute("""SELECT activity, count(*) FROM cost_events WHERE price IS NULL
            GROUP BY 1 ORDER BY 2 DESC, 1""").fetchall()
        variants = conn.execute(f"""SELECT path, count(*), avg(activity_cost + open_hours * ?),
              sum(activity_cost + open_hours * ?), avg(repeat_cost)
            FROM cost_cases GROUP BY 1 ORDER BY 4 DESC, 2 DESC, 1 LIMIT {model.limit}""", [hourly, hourly]).fetchall()
        resources = conn.execute(f"""SELECT resource, count(*), sum(price), sum(price) FILTER (WHERE repeated)
            FROM cost_events WHERE resource <> '' GROUP BY 1 ORDER BY coalesce(sum(price), 0) DESC, 2 DESC, 1
            LIMIT {model.limit}""").fetchall()
        events = conn.execute("SELECT count(*) FROM cost_events").fetchone()[0]
        return {"cases": cases, "events": events, "per_case": per_case,
                "activity_total": float(activity_total or 0), "repeat_total": float(repeat_total or 0),
                "holding_hours": float(holding_hours or 0), "activities": activities, "unpriced": unpriced,
                "variants": variants, "resources": resources}

    data = workspace._query(log_id, model.case_type, run)
    holding_total = data["holding_hours"] * hourly
    total = data["activity_total"] + holding_total
    per_case = data["per_case"]
    unpriced_events = sum(n for _, n in data["unpriced"])
    return {
        "format": FORMAT,
        "currency": model.currency,
        "cohort": cohorts.describe(cohorts.parse_cohort(model.cohort)),
        "cases": data["cases"],
        "events": data["events"],
        "total_cost": _money(total),
        "activity_cost": _money(data["activity_total"]),
        "holding_cost": _money(holding_total),
        "repeated_execution_cost": _money(data["repeat_total"]),
        "repeated_execution_share": round(data["repeat_total"] / total, 6) if total else None,
        "cost_per_case": {"mean": _money(total / data["cases"]), "median": _money(_quantile(per_case, 0.5)),
                          "p90": _money(_quantile(per_case, 0.9)), "max": _money(per_case[-1])},
        "activities": [{"activity": a, "events": n, "cases": c, "unit_cost": unit, "cost": _money(cost),
                        "share_of_activity_cost": (round(cost / data["activity_total"], 6)
                                                   if cost is not None and data["activity_total"] else None),
                        "repeated_executions": reps, "repeated_execution_cost": _money(rep_cost or 0) if cost is not None else None,
                        "price_source": "none" if unit is None else ("default" if defaulted else "model")}
                       for a, n, c, unit, defaulted, cost, rep_cost, reps in data["activities"]],
        "unpriced_activities": [{"activity": a, "events": n} for a, n in data["unpriced"]],
        "unpriced_events": unpriced_events,
        "costliest_variants": [{"path": p.split(_SEP), "cases": n, "cost_per_case": _money(avg),
                                "total_cost": _money(sum_), "repeated_execution_cost_per_case": _money(rep)}
                               for p, n, avg, sum_, rep in data["variants"]],
        "resources": [{"resource": r, "events": n, "cost": _money(cost), "repeated_execution_cost": _money(rep or 0)}
                      for r, n, cost, rep in data["resources"]],
        "model": {"activity_costs": model.activity_costs, "default_activity_cost": default,
                  "hourly_open_case_cost": hourly},
        "interpretation": ("The user's cost model applied to observed events; the log does not record what work "
                           "cost. Holding cost uses each case's first-to-last event time. Repeated executions are "
                           "executions of an activity after its first in the same case and are not verified rework. "
                           "Activities without a price are left out of the totals and listed."),
    }
