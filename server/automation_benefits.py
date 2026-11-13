"""Realized value for implemented automation ideas (R16): measured before/after, against the model.

A reviewer records measurement periods for an idea: a *baseline* before it was
implemented and *after* periods once it was implemented. Each period states its
dates, how many cases were handled and the effort, as minutes per case or total
hours, and where the numbers came from. The report normalizes each period to a
30.44-day month, compares baseline with after, and sets the observed change next
to the hours the idea's assessment modeled.

What this is not: an observed difference between two periods is not proof that
the automation caused it. Volume, staffing, seasonality and other changes can
contribute, and the numbers are only as good as the entered measurements. The
report never states savings in money and never adds ideas together.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
import json
import secrets
import sqlite3
import statistics
import threading
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError, model_validator

from . import automation_pipeline as pipeline

MAX_MEASUREMENTS_PER_IDEA = 48
DAYS_PER_MONTH = 30.44
_LOCK = threading.RLock()


class BenefitError(ValueError):
    pass


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Literal["baseline", "after"]
    start: date
    end: date
    cases: Annotated[StrictInt, Field(ge=0, le=100_000_000)]
    minutes_per_case: Annotated[StrictInt | StrictFloat, Field(ge=0, le=100_000)] | None = None
    total_hours: Annotated[StrictInt | StrictFloat, Field(ge=0, le=10_000_000)] | None = None
    source: Literal["entered", "event_log", "time_tracking", "system_report"] = "entered"
    source_reference: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _shape(self):
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if (self.end - self.start).days + 1 > 366:
            raise ValueError("a period is at most 366 days")
        if (self.minutes_per_case is None) == (self.total_hours is None):
            raise ValueError("give either minutes_per_case or total_hours")
        return self


def _connect() -> sqlite3.Connection:
    path = pipeline.DATA_DIR / "automation_pipeline.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS idea_measurements (
        measurement_id TEXT PRIMARY KEY, idea_id TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idea_measurements_by_idea ON idea_measurements(idea_id)")
    return conn


def _implemented_on(idea: dict[str, Any]) -> date | None:
    for entry in reversed(idea.get("history") or []):
        if entry.get("state") == "implemented" and entry.get("action") is None and entry.get("at"):
            return datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00")).date()
    return None


def record(idea_id: str, payload: Any) -> dict[str, Any]:
    try:
        m = Measurement.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise BenefitError(".".join(str(p) for p in first["loc"]) + ": " + first["msg"]) from None
    try:
        idea = pipeline.get(idea_id)
    except pipeline.PipelineError as exc:
        raise BenefitError(str(exc)) from None
    if m.end > datetime.now(timezone.utc).date():
        raise BenefitError("a measured period cannot end in the future")
    implemented = _implemented_on(idea)
    if m.period == "after":
        if idea["state"] != "implemented" or implemented is None:
            raise BenefitError("after measurements need an implemented idea")
        if m.start < implemented:
            raise BenefitError(f"an after period must start on or after the implementation date ({implemented.isoformat()})")
    elif implemented is not None and m.end >= implemented:
        raise BenefitError(f"a baseline period must end before the implementation date ({implemented.isoformat()})")
    with _LOCK, closing(_connect()) as conn, conn:
        if conn.execute("SELECT count(*) FROM idea_measurements WHERE idea_id = ?", [idea_id]).fetchone()[0] >= MAX_MEASUREMENTS_PER_IDEA:
            raise BenefitError(f"an idea holds at most {MAX_MEASUREMENTS_PER_IDEA} measurements")
        mid = "meas_" + secrets.token_hex(8)
        body = m.model_dump(mode="json")
        body["source_reference"], body["note"] = body["source_reference"].strip(), body["note"].strip()
        conn.execute("INSERT INTO idea_measurements VALUES (?, ?, ?, ?)",
                     [mid, idea_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(body)])
    return report(idea_id)


def delete(idea_id: str, measurement_id: str) -> dict[str, Any]:
    with _LOCK, closing(_connect()) as conn, conn:
        gone = conn.execute("DELETE FROM idea_measurements WHERE idea_id = ? AND measurement_id = ?",
                            [idea_id, str(measurement_id)]).rowcount
    if not gone:
        raise BenefitError("unknown measurement")
    return report(idea_id)


def delete_for_idea(idea_id: str) -> None:
    with _LOCK, closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM idea_measurements WHERE idea_id = ?", [idea_id])


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days + 1
    hours = row["total_hours"] if row["total_hours"] is not None else row["cases"] * row["minutes_per_case"] / 60
    per_month = DAYS_PER_MONTH / days
    # Unrounded values feed the summaries; rounding happens once, for display.
    return {**row, "days": days, "_hours": hours, "_hours_per_month": hours * per_month,
            "_cases_per_month": row["cases"] * per_month, "hours": round(hours, 3),
            "cases_per_month": round(row["cases"] * per_month, 2),
            "hours_per_month": round(hours * per_month, 3),
            "minutes_per_case_observed": round(hours * 60 / row["cases"], 3) if row["cases"] else None}


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    hours = [r["_hours_per_month"] for r in rows]
    cases = [r["_cases_per_month"] for r in rows]
    total_cases = sum(r["cases"] for r in rows)
    return {"periods": len(rows), "days": sum(r["days"] for r in rows),
            "_hours_mean": statistics.fmean(hours), "_hours_range": [min(hours), max(hours)],
            "_cases_mean": statistics.fmean(cases),
            "_minutes": sum(r["_hours"] for r in rows) * 60 / total_cases if total_cases else None,
            "hours_per_month_mean": round(statistics.fmean(hours), 3),
            "hours_per_month_range": [round(min(hours), 3), round(max(hours), 3)],
            "cases_per_month_mean": round(statistics.fmean(cases), 2),
            "minutes_per_case": round(sum(r["_hours"] for r in rows) * 60 / total_cases, 3) if total_cases else None}



def _coverage_adjusted(rows: list[dict[str, Any]], modeled: float | None) -> dict[str, Any]:
    """Pool reported case exposure across non-overlapping calendar intervals.

    The legacy per-period mean is unchanged. An adjusted comparison must fail
    closed on shared calendar days or missing case denominators; a claimed
    source type is not independent verification of the inputs.
    """
    cohorts = {p: sorted((r for r in rows if r["period"] == p),
                         key=lambda r: (r["start"], r["end"], r["measurement_id"]))
               for p in ("baseline", "after")}
    coverage, overlap_pairs = {}, []
    for kind, group in cohorts.items():
        coverage[kind] = {
            "periods": len(group),
            "calendar_days_reported": sum(r["days"] for r in group),
            "cases_reported": sum(r["cases"] for r in group),
            "hours_reported": round(sum(r["_hours"] for r in group), 3),
        }
        # There are at most 48 measurements. Compare every overlapping pair,
        # including nested intervals, rather than just the preceding row.
        for i, first in enumerate(group):
            for second in group[i + 1:]:
                if second["start"] > first["end"]:
                    break
                overlap_pairs.append({"period": kind, "first_id": first["measurement_id"],
                                      "second_id": second["measurement_id"]})
    report = {
        "status": "missing_periods", "coverage": coverage, "comparison": None,
        "overlap_pairs": overlap_pairs,
        "period_weighting": "pooled_hours_and_cases_over_nonoverlapping_calendar_days",
        "measurement_sources_independently_verified": False,
        "causal_effect_verified": False,
        "realized_savings_verified": False,
        "interpretation": ("Periods and source labels are user-supplied, not source-verified. "
                           "An observed before/after difference cannot establish causal savings."),
    }
    if not cohorts["baseline"] or not cohorts["after"]:
        return report
    if overlap_pairs:
        return {**report, "status": "overlapping_periods"}
    if any(r["cases"] == 0 for group in cohorts.values() for r in group):
        return {**report, "status": "zero_case_period"}
    b, a = cohorts["baseline"], cohorts["after"]
    b_days, a_days = sum(r["days"] for r in b), sum(r["days"] for r in a)
    b_cases, a_cases = sum(r["cases"] for r in b), sum(r["cases"] for r in a)
    b_hours, a_hours = sum(r["_hours"] for r in b), sum(r["_hours"] for r in a)
    b_minutes, a_minutes = b_hours * 60 / b_cases, a_hours * 60 / a_cases
    b_month, a_month = b_hours * DAYS_PER_MONTH / b_days, a_hours * DAYS_PER_MONTH / a_days
    after_volume = a_cases * DAYS_PER_MONTH / a_days
    at_after_volume = (b_minutes - a_minutes) * after_volume / 60
    comparison = {
        "baseline_hours_per_month": round(b_month, 3),
        "after_hours_per_month": round(a_month, 3),
        "observed_hours_per_month_change": round(b_month - a_month, 3),
        "baseline_cases_per_month": round(b_cases * DAYS_PER_MONTH / b_days, 2),
        "after_cases_per_month": round(after_volume, 2),
        "case_volume_change_per_month": round(after_volume - b_cases * DAYS_PER_MONTH / b_days, 2),
        "baseline_minutes_per_case": round(b_minutes, 3),
        "after_minutes_per_case": round(a_minutes, 3),
        "minutes_per_case_change": round(b_minutes - a_minutes, 3),
        "hours_per_month_from_per_case_change_at_after_volume": round(at_after_volume, 3),
        "modeled_hours_per_month": modeled,
        "observed_vs_modeled_ratio": round(at_after_volume / modeled, 3) if modeled else None,
        "uncertainty_interval_available": False,
    }
    return {**report, "status": "ready", "comparison": comparison}


def report(idea_id: str) -> dict[str, Any]:
    try:
        idea = pipeline.get(idea_id)
    except pipeline.PipelineError as exc:
        raise BenefitError(str(exc)) from None
    with _LOCK, closing(_connect()) as conn:
        stored = conn.execute("SELECT measurement_id, created_at, body FROM idea_measurements WHERE idea_id = ? "
                              "ORDER BY created_at, measurement_id", [idea_id]).fetchall()
    rows = [_normalize({"measurement_id": mid, "recorded_at": at, **json.loads(body)}) for mid, at, body in stored]
    rows.sort(key=lambda r: (r["start"], r["measurement_id"]))
    baseline = _summary([r for r in rows if r["period"] == "baseline"])
    after = _summary([r for r in rows if r["period"] == "after"])
    modeled = (idea.get("score") or {}).get("modeled_hours_per_month")
    comparison = None
    if baseline and after:
        b, a = baseline, after
        # Per-case effort separates a volume change from an effort change.
        per_case = None if b["_minutes"] is None or a["_minutes"] is None else b["_minutes"] - a["_minutes"]
        at_after_volume = None if per_case is None else per_case * a["_cases_mean"] / 60
        comparison = {
            "observed_hours_per_month_change": round(b["_hours_mean"] - a["_hours_mean"], 3),
            "observed_change_range": [round(b["_hours_range"][0] - a["_hours_range"][1], 3),
                                      round(b["_hours_range"][1] - a["_hours_range"][0], 3)],
            "minutes_per_case_change": None if per_case is None else round(per_case, 3),
            "hours_per_month_from_per_case_change_at_after_volume": None if at_after_volume is None else round(at_after_volume, 3),
            "case_volume_change_per_month": round(a["_cases_mean"] - b["_cases_mean"], 2),
            "modeled_hours_per_month": modeled,
            "observed_vs_modeled_ratio": (round(at_after_volume / modeled, 3)
                                          if at_after_volume is not None and modeled else None),
            "single_period_each": b["periods"] == 1 and a["periods"] == 1,
        }
    coverage_adjusted = _coverage_adjusted(rows, modeled)
    for summary in (baseline, after):
        for key in [k for k in (summary or {}) if k.startswith("_")]:
            summary.pop(key)
    for row in rows:
        for key in [k for k in row if k.startswith("_")]:
            row.pop(key)
    implemented = _implemented_on(idea)
    return {"format": "openworkgraph.automation-realized-value.v1", "idea_id": idea_id, "title": idea["title"],
            "state": idea["state"], "implemented_on": implemented.isoformat() if implemented else None,
            "measurements": rows, "baseline": baseline, "after": after, "comparison": comparison,
            "coverage_adjusted": coverage_adjusted,
            "max_measurements": MAX_MEASUREMENTS_PER_IDEA, "month_days": DAYS_PER_MONTH,
            "causal_effect_claimed": False, "savings_claimed": False, "money_estimated": False,
            "interpretation": ("Observed change between the entered baseline and after periods. It is not proof "
                               "that the automation caused it: volume, staffing, seasonality and other changes can "
                               "contribute. Compare per-case effort to separate volume from effort. Modeled hours "
                               "come from the idea's assessment.")}


__all__ = ["BenefitError", "delete", "delete_for_idea", "record", "report"]
