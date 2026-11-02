"""Ad-hoc analysis builder over saved event logs (roadmap #326 F3).

A typed, bounded alternative to writing SQL or PQL: pick a level (cases or
events), optional filters, up to two dimensions and up to four measures.
Case-level analyses can also define up to four typed *case metrics* (derived
per-case values such as the time from one activity to another, how often an
activity occurs, a numeric attribute with its declared unit, or bounded typed
arithmetic over earlier metrics and finite constants) and measure
them; named metric definitions can be saved with the log and reused. The
spec is validated against fixed vocabularies and compiled to a parameterized
query; no user text ever becomes SQL. Analyses can be saved with the log and
are deleted with it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_serializer

from . import event_log_cohorts as cohorts
from . import event_log_expressions as expressions
from . import event_log_workspace as workspace

MAX_ROWS = 500
MAX_SAVED = 50
MAX_VARIANT_PATH_BYTES = 1024 * 1024
MAX_VARIANT_TOTAL_BYTES = 64 * 1024 * 1024
Name = Annotated[str, Field(min_length=1, max_length=200)]

# Fixed SQL fragments per vocabulary entry; user values only travel as parameters.
_CASE_DIMENSIONS = {
    "start_week": "strftime(date_trunc('week', started), '%Y-%m-%d')",
    "start_month": "strftime(date_trunc('month', started), '%Y-%m')",
    # Case timestamps are stored in UTC. Prefix weekday with ISO 1..7 so a
    # two-dimensional day/hour breakdown sorts chronologically by dimensions.
    "start_weekday_utc": "strftime(started, '%u %A')",
    "start_hour_utc": "strftime(started, '%H')",
    # Single bucket preserves the second dimension for case attributes or variants.
    "start_weekday_hour_utc": "strftime(started, '%u %A %H')",
    "end_weekday_utc": "strftime(ended, '%u %A')",
    "end_hour_utc": "strftime(ended, '%H')",
    "end_weekday_hour_utc": "strftime(ended, '%u %A %H')",
    # Half-open, disjoint elapsed-time bands; never a local wall-clock estimate.
    "duration_band": """CASE WHEN duration < 3600 THEN '0: under 1 hour'
        WHEN duration < 86400 THEN '1: 1 to under 24 hours'
        WHEN duration < 604800 THEN '2: 1 to under 7 days'
        ELSE '3: 7 days or more' END""",
    "end_week": "strftime(date_trunc('week', ended), '%Y-%m-%d')",
    "first_activity": "first_activity",
    "last_activity": "last_activity",
    "variant": "variant",
    "has_rework": "CASE WHEN rework THEN 'rework' ELSE 'no rework' END",
}
# Explicit IANA-local wall-time dimensions. Stored event instants are UTC-naive;
# interpret them in UTC first, then convert the instant to the chosen zone.
# The zone is always a bound SQL parameter, never interpolated user input.
_LOCAL_CASE_DIMENSIONS = {
    "start_weekday_local": "strftime(timezone(?, timezone('UTC', started)), '%u %A')",
    "start_hour_local": "strftime(timezone(?, timezone('UTC', started)), '%H')",
    "start_weekday_hour_local": "strftime(timezone(?, timezone('UTC', started)), '%u %A %H')",
    "end_weekday_local": "strftime(timezone(?, timezone('UTC', ended)), '%u %A')",
    "end_hour_local": "strftime(timezone(?, timezone('UTC', ended)), '%H')",
    "end_weekday_hour_local": "strftime(timezone(?, timezone('UTC', ended)), '%u %A %H')",
}
_LOCAL_EVENT_DIMENSIONS = {
    # Event timestamps are also stored as UTC-naive instants. Explicit IANA
    # conversion is needed to group actual observed event volume by local shift.
    "weekday_local": "strftime(timezone(?, timezone('UTC', ts)), '%u %A')",
    "hour_local": "strftime(timezone(?, timezone('UTC', ts)), '%H')",
    "weekday_hour_local": "strftime(timezone(?, timezone('UTC', ts)), '%u %A %H')",
}
_EVENT_DIMENSIONS = {
    "activity": "activity",
    "resource": "nullif(resource, '')",
    "week": "strftime(date_trunc('week', ts), '%Y-%m-%d')",
    "month": "strftime(date_trunc('month', ts), '%Y-%m')",
    "weekday": "dayname(ts)",
    "hour": "CAST(hour(ts) AS VARCHAR)",
    "weekday_hour_utc": "strftime(ts, '%u %A %H')",
}
_CASE_VALUES = {"duration_seconds": "duration", "events": "events", "distinct_activities": "distinct_activities"}
_FUNCTIONS = {"median": "quantile_cont({x}, 0.5)", "p90": "quantile_cont({x}, 0.9)", "avg": "avg({x})",
              "min": "min({x})", "max": "max({x})", "sum": "sum({x})"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


MetricName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
AttributeName = Annotated[str, Field(pattern=r'^[^"\\\x00-\x1f]{1,120}$')]
Unit = Annotated[str, Field(pattern=r"^[A-Za-z%$€£¥/._ -]{1,20}$")]


class TimeBetween(_Strict):
    """Seconds from an occurrence of ``from_activity`` to an occurrence of ``to_activity`` in the case."""
    name: MetricName
    kind: Literal["time_between"]
    from_activity: Name
    to_activity: Name
    from_occurrence: Literal["first", "last"] = "first"
    to_occurrence: Literal["first", "last"] = "first"


class ActivityCount(_Strict):
    """How many events of one activity the case has (0 when it has none)."""
    name: MetricName
    kind: Literal["activity_count"]
    activity: Name


class CaseAttributeNumber(_Strict):
    """A numeric case attribute, in the unit the user declares (values are never converted)."""
    name: MetricName
    kind: Literal["case_attribute_number"]
    attribute: AttributeName
    unit: Unit


class EventAttributeSum(_Strict):
    """The sum of a numeric event attribute over the case's events, in the declared unit."""
    name: MetricName
    kind: Literal["event_attribute_sum"]
    attribute: AttributeName
    unit: Unit


class EventAttributeStatistic(_Strict):
    """Finite numeric event-attribute statistic per selected full case."""
    name: MetricName
    kind: Literal["event_attribute_statistic"]
    attribute: AttributeName
    unit: Unit
    statistic: Literal["average", "minimum", "maximum", "median", "p90"]
    # Optional exact activity selector. Only matching event rows contribute;
    # unmatched cases remain unknown rather than becoming zero.
    activity: Name | None = None
    # First from-activity (inclusive) to first strictly later to-activity
    # (exclusive), with deterministic (timestamp, event ID) ordering.
    from_activity: Name | None = None
    to_activity: Name | None = None
    missing_policy: Literal["available", "complete"] = "available"


Metric = Annotated[TimeBetween | ActivityCount | CaseAttributeNumber | EventAttributeSum | EventAttributeStatistic | expressions.Arithmetic, Field(discriminator="kind")]
METRIC_FORMAT = "openworkgraph.event-log-case-metric.v1"


class Measure(_Strict):
    fn: Literal["count", "cases", "share", "median", "p90", "avg", "min", "max", "sum"]
    of: Annotated[str, Field(max_length=60)] | None = None


class CaseFilter(_Strict):
    duration_over_seconds: StrictInt | None = Field(default=None, ge=0)
    duration_under_seconds: StrictInt | None = Field(default=None, ge=1)
    min_events: StrictInt | None = Field(default=None, ge=1)
    ends_with: Name | None = None
    lacks_activity: Name | None = None


WeekdayNumber = Annotated[StrictInt, Field(ge=1, le=7)]
HourNumber = Annotated[StrictInt, Field(ge=0, le=23)]


class TimeOfWeekCell(_Strict):
    """One ISO weekday and 24-hour clock cell, not a cross-product."""
    weekday: WeekdayNumber
    hour: HourNumber


class TimeOfWeekFilter(_Strict):
    """Select observations in explicitly chosen ISO weekdays and clock hours."""
    anchor: Literal["event", "case_start", "case_end"]
    clock: Literal["utc", "local"] = "utc"
    weekdays: list[WeekdayNumber] = Field(default_factory=lambda: list(range(1, 8)), min_length=1, max_length=7)
    hours: list[HourNumber] = Field(default_factory=lambda: list(range(24)), min_length=1, max_length=24)
    # Explicit disjoint cells for overnight schedules: unlike weekdays × hours,
    # Sunday 22:00 and Monday 01:00 select only those two clock hours.
    slots: list[TimeOfWeekCell] | None = Field(default=None, min_length=1, max_length=168)

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if self.slots is not None:
            # Saved analyses and returned specs must be parseable again.
            # The implicit Cartesian defaults are not explicit user input.
            data.pop("weekdays", None)
            data.pop("hours", None)
        return data


class AnalysisSpec(_Strict):
    level: Literal["cases", "events"]
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    measures: list[Measure] = Field(min_length=1, max_length=4)
    cohort: dict[str, Any] = Field(default_factory=dict)
    where: CaseFilter = Field(default_factory=CaseFilter)
    activity_in: list[Name] = Field(default_factory=list, max_length=50)
    metrics: list[Metric] = Field(default_factory=list, max_length=4)
    order: Literal["first_measure_desc", "first_measure_asc", "dimensions"] = "first_measure_desc"
    limit: StrictInt = Field(default=100, ge=1, le=MAX_ROWS)
    case_type: Name | None = None
    time_zone: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_+./-]+$")] | None = None
    time_of_week: TimeOfWeekFilter | None = None


class SavedAnalysis(_Strict):
    name: str = Field(min_length=1, max_length=120)
    spec: AnalysisSpec


def _attribute_dimension(name: str) -> str | None:
    if name.startswith("attribute:") and 0 < len(name) - 10 <= 200:
        return name[10:]
    return None


def parse(spec: Any) -> AnalysisSpec:
    try:
        parsed = AnalysisSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "analysis is invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    allowed = ({**_CASE_DIMENSIONS, **_LOCAL_CASE_DIMENSIONS}
               if parsed.level == "cases" else {**_EVENT_DIMENSIONS, **_LOCAL_EVENT_DIMENSIONS})
    window = parsed.time_of_week
    if window is not None:
        if (parsed.level == "events") != (window.anchor == "event"):
            raise workspace.EventLogWorkspaceError("time_of_week anchor must be event for events or case_start/case_end for cases")
        if len(set(window.weekdays)) != len(window.weekdays) or len(set(window.hours)) != len(window.hours):
            raise workspace.EventLogWorkspaceError("time_of_week weekdays and hours cannot contain duplicates")
        if window.slots is not None:
            if {"weekdays", "hours"} & window.model_fields_set:
                raise workspace.EventLogWorkspaceError(
                    "time_of_week slots cannot be combined with weekdays or hours")
            if len({(cell.weekday, cell.hour) for cell in window.slots}) != len(window.slots):
                raise workspace.EventLogWorkspaceError("time_of_week slots cannot contain duplicates")
    local_time_requested = (any(d in _LOCAL_CASE_DIMENSIONS or d in _LOCAL_EVENT_DIMENSIONS
                                for d in parsed.dimensions)
                            or (window is not None and window.clock == "local"))
    if local_time_requested:
        if parsed.time_zone is None:
            raise workspace.EventLogWorkspaceError("local time dimensions require an explicit IANA time_zone")
        try:
            ZoneInfo(parsed.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise workspace.EventLogWorkspaceError("time_zone must be a known IANA time zone") from None
    elif parsed.time_zone is not None:
        raise workspace.EventLogWorkspaceError("time_zone requires a local time dimension or local time_of_week filter")
    for dimension in parsed.dimensions:
        if dimension not in allowed and _attribute_dimension(dimension) is None:
            raise workspace.EventLogWorkspaceError(
                f"unknown {parsed.level} dimension {dimension!r}; use one of {sorted(allowed)} or attribute:<name>")
    if len(set(parsed.dimensions)) != len(parsed.dimensions):
        raise workspace.EventLogWorkspaceError("dimensions must be different")
    names = [m.name for m in parsed.metrics]
    if len(set(names)) != len(names):
        raise workspace.EventLogWorkspaceError("case metric names must be different")
    if parsed.metrics and parsed.level != "cases":
        raise workspace.EventLogWorkspaceError("case metrics apply to case-level analyses")
    for metric in parsed.metrics:
        if metric.kind == "event_attribute_statistic" and ((metric.from_activity is None) != (metric.to_activity is None)):
            raise workspace.EventLogWorkspaceError(
                "stage-bounded event attribute statistics require both from_activity and to_activity")
    try:
        expressions.units(parsed.metrics)
    except ValueError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None
    for measure in parsed.measures:
        if measure.of is not None and measure.of not in _CASE_VALUES and not (
                measure.of.startswith("metric:") and measure.of[7:] in names):
            raise workspace.EventLogWorkspaceError(
                f"unknown value {measure.of!r}; use {sorted(_CASE_VALUES)} or metric:<name> of a defined case metric")
        if measure.fn in {"count", "cases", "share"}:
            if measure.of is not None:
                raise workspace.EventLogWorkspaceError(f"{measure.fn} does not take a value")
        elif parsed.level == "events":
            raise workspace.EventLogWorkspaceError("event-level analyses support count, cases and share")
        elif measure.of is None:
            raise workspace.EventLogWorkspaceError(f"{measure.fn} needs a value to measure")
    if parsed.level == "events" and parsed.where != CaseFilter():
        raise workspace.EventLogWorkspaceError("case filters apply to case-level analyses; use cohort instead")
    return parsed


def _time_of_week_clause(spec: AnalysisSpec) -> tuple[str | None, list[Any]]:
    """A bounded clock-window predicate with fixed identifiers and bound values.

    ISO weekdays 1..7 and 24-hour clock bins are evaluated on observed UTC
    instants, optionally converted to an explicitly selected IANA wall clock.
    A repeated DST hour intentionally combines both source instants.
    """
    window = spec.time_of_week
    if window is None:
        return None, []
    column = {"event": "ts", "case_start": "started", "case_end": "ended"}[window.anchor]
    if window.slots is not None:
        # A single fixed strftime expression and a bound list preserve the
        # requested exact weekday-hour pairs. ISO weekday %u is 1..7; %H
        # is 00..23. Both repeated DST-fold instants share the same cell.
        cells = [f"{cell.weekday}{cell.hour:02d}" for cell in window.slots]
        if window.clock == "local":
            return (f"list_contains(?, strftime(timezone(?, timezone('UTC', {column})), '%u%H'))",
                    [cells, spec.time_zone])
        return f"list_contains(?, strftime({column}, '%u%H'))", [cells]
    if window.clock == "local":
        instant = f"timezone(?, timezone('UTC', {column}))"
        params: list[Any] = [window.weekdays, spec.time_zone, window.hours, spec.time_zone]
    else:
        instant = column
        params = [window.weekdays, window.hours]
    return (f"(list_contains(?, CAST(strftime({instant}, '%u') AS INTEGER)) "
            f"AND list_contains(?, CAST(strftime({instant}, '%H') AS INTEGER)))"), params


def _column(measure: Measure, metric_columns: dict[str, str] | None = None) -> tuple[str, str]:
    label = measure.fn if measure.of is None else f"{measure.fn}_{measure.of}"
    if measure.of is not None and measure.of.startswith("metric:"):
        return label, _FUNCTIONS[measure.fn].format(x=(metric_columns or {})[measure.of[7:]])
    if measure.fn == "count":
        return label, "count(*)"
    if measure.fn == "cases":
        return label, "count(DISTINCT case_id)"
    if measure.fn == "share":
        return label, "count(*) / sum(count(*)) OVER ()"
    return label, _FUNCTIONS[measure.fn].format(x=_CASE_VALUES[measure.of])


def _path(attribute: str) -> str:
    return '$."' + attribute + '"'


def _metrics(conn, metrics: list[Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Add one DOUBLE column per case metric to ``qc`` and report how many cases have a value."""
    columns, coverage = {}, []
    metric_units = expressions.units(metrics)
    for i, metric in enumerate(metrics):
        column = f"mv{i}"
        conn.execute(f"ALTER TABLE qc ADD COLUMN {column} DOUBLE")
        extra: dict[str, Any] = {}
        if metric.kind == "arithmetic":
            extra = expressions.materialize(conn, metric, columns, column)
            extra["unit_powers"] = metric_units[metric.name]
            unit = expressions.unit_text(metric_units[metric.name])
        elif metric.kind == "time_between":
            pick = {"first": "min", "last": "max"}
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE qm AS SELECT case_id,
                  {pick[metric.from_occurrence]}(ts) FILTER (WHERE activity = ?) AS a,
                  {pick[metric.to_occurrence]}(ts) FILTER (WHERE activity = ?) AS b FROM ce GROUP BY case_id""",
                         [metric.from_activity, metric.to_activity])
            extra = dict(zip(("cases_without_from_activity", "cases_without_to_activity", "cases_where_to_precedes_from"),
                             conn.execute("""SELECT count(*) FILTER (WHERE a IS NULL), count(*) FILTER (WHERE b IS NULL),
                                 count(*) FILTER (WHERE b < a) FROM qm WHERE case_id IN (SELECT case_id FROM qc)""").fetchone()))
            conn.execute(f"UPDATE qc SET {column} = epoch(qm.b - qm.a) FROM qm WHERE qc.case_id = qm.case_id AND qm.b >= qm.a")
            unit = "seconds"
        elif metric.kind == "activity_count":
            conn.execute(f"""UPDATE qc SET {column} = coalesce(s.n, 0) FROM (SELECT q.case_id, n FROM qc q LEFT JOIN
                (SELECT case_id, count(*) AS n FROM ce WHERE activity = ? GROUP BY case_id) c USING (case_id)) s
                WHERE qc.case_id = s.case_id""", [metric.activity])
            unit = "events"
        elif metric.kind == "case_attribute_number":
            conn.execute(f"UPDATE qc SET {column} = try_cast(json_extract_string(case_attributes, ?) AS DOUBLE)",
                         [_path(metric.attribute)])
            unit = metric.unit
        elif metric.kind == "event_attribute_statistic":
            # The input is flattened onto the selected case notion. An OCEL
            # event linked to two objects is counted once in *each* case, not
            # twice in one case. Restrict to selected cases before aggregating.
            aggregate = {"average": "avg(v)", "minimum": "min(v)", "maximum": "max(v)",
                         "median": "quantile_cont(v, 0.5)", "p90": "quantile_cont(v, 0.9)"}[metric.statistic]
            stage_selected = metric.from_activity is not None
            if stage_selected:
                # Use the same stable event order as the case trace. A prior
                # end does not terminate the stage; only a later end does.
                conn.execute("""CREATE OR REPLACE TEMP TABLE qordered AS
                    SELECT case_id, activity, attributes,
                        row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS ordinal
                    FROM ce WHERE case_id IN (SELECT case_id FROM qc)""")
                conn.execute("""CREATE OR REPLACE TEMP TABLE qstage AS
                    WITH starts AS (
                        SELECT case_id, min(ordinal) AS start_pos FROM qordered
                        WHERE activity = ? GROUP BY case_id)
                    SELECT s.case_id, s.start_pos, min(e.ordinal) AS end_pos
                    FROM starts s LEFT JOIN qordered e
                      ON e.case_id = s.case_id AND e.ordinal > s.start_pos AND e.activity = ?
                    GROUP BY s.case_id, s.start_pos""",
                    [metric.from_activity, metric.to_activity])
                missing_start = int(conn.execute("""SELECT count(*) FROM qc q WHERE NOT EXISTS
                    (SELECT 1 FROM qstage s WHERE s.case_id = q.case_id)""").fetchone()[0])
                missing_end = int(conn.execute(
                    "SELECT count(*) FROM qstage WHERE end_pos IS NULL").fetchone()[0])
                extra["cases_without_stage_start"] = missing_start
                extra["cases_without_later_stage_end"] = missing_end
                extra["cases_without_complete_stage"] = missing_start + missing_end
                source = """FROM qordered e JOIN qstage s ON e.case_id = s.case_id
                    WHERE s.end_pos IS NOT NULL AND e.ordinal >= s.start_pos
                      AND e.ordinal < s.end_pos"""
                if metric.activity is not None:
                    source += " AND e.activity = ?"
            else:
                source = "FROM ce WHERE case_id IN (SELECT case_id FROM qc)"
                if metric.activity is not None:
                    source += " AND activity = ?"
            bound = [_path(metric.attribute)] + ([metric.activity] if metric.activity is not None else [])
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE qstat AS
                SELECT case_id, count(*) AS event_rows,
                    count(*) FILTER (WHERE v IS NOT NULL AND isfinite(v)) AS finite_rows,
                    count(*) FILTER (WHERE v IS NULL OR NOT isfinite(v)) AS excluded_rows,
                    {aggregate} FILTER (WHERE v IS NOT NULL AND isfinite(v)) AS metric_value
                FROM (SELECT case_id, try_cast(json_extract_string(attributes, ?) AS DOUBLE) AS v
                      {source}) typed GROUP BY case_id""", bound)
            stats = conn.execute("""SELECT coalesce(sum(event_rows), 0),
                    coalesce(sum(finite_rows), 0), coalesce(sum(excluded_rows), 0),
                    count(*) FILTER (WHERE finite_rows > 0 AND excluded_rows > 0),
                    count(*) FILTER (WHERE finite_rows = 0)
                FROM qstat""").fetchone()
            extra = dict(zip(("flattened_case_event_rows_considered",
                              "event_rows_with_finite_attribute",
                              "event_rows_with_missing_invalid_or_nonfinite_attribute",
                              "cases_with_partial_attribute_coverage",
                              "cases_with_no_finite_attribute"), (int(v or 0) for v in stats)))
            # No matching activity is not a zero-valued metric or a complete
            # observation; it is a missing case, even with complete policy.
            absent = int(conn.execute("""SELECT count(*) FROM qc q
                WHERE NOT EXISTS (SELECT 1 FROM qstat s WHERE s.case_id = q.case_id)""").fetchone()[0])
            if metric.activity is not None and stage_selected:
                no_activity = int(conn.execute("""SELECT count(*) FROM qstage s
                    WHERE s.end_pos IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM qstat q WHERE q.case_id = s.case_id)""").fetchone()[0])
            else:
                no_activity = absent if metric.activity is not None else 0
            extra["cases_without_matching_activity"] = no_activity
            extra["cases_with_no_finite_attribute"] += absent
            extra["cases_excluded_by_complete_policy"] = (
                extra["cases_with_partial_attribute_coverage"] if metric.missing_policy == "complete" else 0)
            extra["coverage_scope"] = ("selected_full_cases_stage_bounded_" if stage_selected else
                                       "selected_full_cases_") + ("matching_activity_" if metric.activity is not None else
                                       "") + "flattened_case_event_rows"
            conn.execute(f"""UPDATE qc SET {column} =
                CASE WHEN qstat.finite_rows = 0 THEN NULL
                     WHEN ? = 'complete' AND qstat.excluded_rows > 0 THEN NULL
                     ELSE qstat.metric_value END
                FROM qstat WHERE qc.case_id = qstat.case_id""", [metric.missing_policy])
            unit = metric.unit
        else:
            # A non-finite event value makes the case's sum non-finite, so it is withheld below, not silently skipped.
            conn.execute(f"""UPDATE qc SET {column} = s.v FROM (SELECT case_id,
                  sum(try_cast(json_extract_string(attributes, ?) AS DOUBLE)) AS v FROM ce GROUP BY case_id) s
                WHERE qc.case_id = s.case_id""", [_path(metric.attribute)])
            unit = metric.unit
        # NaN, ±Infinity (and sums that overflow) are not values: they become missing and are counted.
        non_finite = conn.execute(f"SELECT count(*) FROM qc WHERE {column} IS NOT NULL AND NOT isfinite({column})").fetchone()[0]
        conn.execute(f"UPDATE qc SET {column} = NULL WHERE NOT isfinite({column})")
        if non_finite:
            extra["cases_with_non_finite_value"] = non_finite
        with_value, without = conn.execute(
            f"SELECT count({column}), count(*) - count({column}) FROM qc").fetchone()
        columns[metric.name] = column
        coverage.append({"name": metric.name, "definition": metric.model_dump(), "unit": unit,
                         "cases_with_value": with_value, "cases_without_value": without, **extra})
    return columns, coverage


def _materialize_variants(conn, where_sql: str, params: list[Any]) -> None:
    """Build complete literal paths only for selected cases, after a byte preflight."""
    selected = 'SELECT case_id FROM qc' + where_sql
    maximum, total = conn.execute(f"""SELECT max(path_bytes), sum(path_bytes) FROM (
        SELECT case_id, sum(octet_length(encode(to_json(activity))) + 1) + 1 AS path_bytes
        FROM ce WHERE case_id IN ({selected}) GROUP BY case_id)""", params).fetchone()
    if (maximum or 0) > MAX_VARIANT_PATH_BYTES or (total or 0) > MAX_VARIANT_TOTAL_BYTES:
        raise workspace.EventLogWorkspaceError(
            'Complete variant paths exceed the analysis byte limits (1 MiB per case, 64 MiB total); '
            'select narrower cases or another dimension. No partial paths were grouped.')
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE qv AS
        SELECT case_id, CAST(to_json(list(activity ORDER BY ts, event_id)) AS VARCHAR) AS variant
        FROM ce WHERE case_id IN ({selected}) GROUP BY case_id""", params)
    conn.execute('UPDATE qc SET variant = qv.variant FROM qv WHERE qc.case_id = qv.case_id')


def _finite(value: Any) -> Any:
    """Round floats; an aggregate that overflowed to ±Infinity or NaN is reported as unavailable (None)."""
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _unit(measure: Measure, coverage: list[dict[str, Any]], level: str) -> str | None:
    if measure.fn == "share":
        return "share"
    if measure.of is None:
        return "events" if measure.fn == "count" and level == "events" else "cases"
    if measure.of.startswith("metric:"):
        return next(c["unit"] for c in coverage if c["name"] == measure.of[7:])
    return {"duration_seconds": "seconds", "events": "events", "distinct_activities": "activities"}[measure.of]


def run(log_id: str, spec: Any, *, control=None) -> dict[str, Any]:
    parsed = parse(spec)
    try:
        import duckdb
    except ImportError:
        raise workspace.EventLogWorkspaceError('event-log workspace requires the duckdb package; reinstall OpenWorkGraph') from None

    def execute(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(parsed.cohort), log_id)
        params: list[Any] = []
        if parsed.level == "cases":
            notion = workspace._case_type(conn, log_id, parsed.case_type)
            conn.execute("""CREATE OR REPLACE TEMP TABLE qc AS SELECT c.*, o.attributes AS case_attributes FROM (
                  SELECT case_id, min(ts) AS started, max(ts) AS ended,
                    epoch(max(ts) - min(ts)) AS duration, count(*) AS events,
                    count(DISTINCT activity) AS distinct_activities, count(*) > count(DISTINCT activity) AS rework,
                    arg_min(activity, struct_pack(at := ts, event_order := event_id)) AS first_activity,
                    arg_max(activity, struct_pack(at := ts, event_order := event_id)) AS last_activity,
                    NULL::VARCHAR AS variant
                  FROM ce GROUP BY case_id) c
                LEFT JOIN (SELECT object_id, any_value(attributes) AS attributes FROM objects
                           WHERE log_id = ? AND object_type = ? GROUP BY object_id) o ON o.object_id = c.case_id""",
                         [log_id, notion])
            source, clauses = "qc", []
            where = parsed.where
            if where.duration_over_seconds is not None:
                clauses.append("duration > ?"); params.append(where.duration_over_seconds)
            if where.duration_under_seconds is not None:
                clauses.append("duration < ?"); params.append(where.duration_under_seconds)
            if where.min_events is not None:
                clauses.append("events >= ?"); params.append(where.min_events)
            if where.ends_with is not None:
                clauses.append("last_activity = ?"); params.append(where.ends_with)
            if where.lacks_activity is not None:
                clauses.append("case_id NOT IN (SELECT case_id FROM ce WHERE activity = ?)")
                params.append(where.lacks_activity)
            if parsed.activity_in:
                clauses.append("case_id IN (SELECT case_id FROM ce WHERE list_contains(?, activity))")
                params.append(parsed.activity_in)
            clock_clause, clock_params = _time_of_week_clause(parsed)
            if clock_clause:
                clauses.append(clock_clause)
                params.extend(clock_params)
            if parsed.metrics and clauses:
                # Keep only the selected cases first, so metric coverage describes the analyzed population.
                conn.execute(f"DELETE FROM qc WHERE NOT coalesce(({' AND '.join(clauses)}), false)", params)
            metric_columns, metric_coverage = _metrics(conn, parsed.metrics)
            dimension_sql = []
            dimension_params = []
            for dimension in parsed.dimensions:
                attribute = _attribute_dimension(dimension)
                if attribute is not None:
                    dimension_sql.append("""map_extract(json_transform(case_attributes, '"MAP(VARCHAR, VARCHAR)"'), ?)[1]""")
                    dimension_params.append(attribute)
                elif dimension in _LOCAL_CASE_DIMENSIONS:
                    dimension_sql.append(_LOCAL_CASE_DIMENSIONS[dimension])
                    dimension_params.append(parsed.time_zone)
                else:
                    dimension_sql.append(_CASE_DIMENSIONS[dimension])
        else:
            source = "ce"
            clauses = []
            metric_columns, metric_coverage = {}, []
            if parsed.activity_in:
                clauses.append("list_contains(?, activity)"); params.append(parsed.activity_in)
            clock_clause, clock_params = _time_of_week_clause(parsed)
            if clock_clause:
                clauses.append(clock_clause)
                params.extend(clock_params)
            dimension_sql = []
            dimension_params = []
            for dimension in parsed.dimensions:
                attribute = _attribute_dimension(dimension)
                if attribute is not None:
                    dimension_sql.append("""map_extract(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?)[1]""")
                    dimension_params.append(attribute)
                elif dimension in _LOCAL_EVENT_DIMENSIONS:
                    dimension_sql.append(_LOCAL_EVENT_DIMENSIONS[dimension])
                    dimension_params.append(parsed.time_zone)
                else:
                    dimension_sql.append(_EVENT_DIMENSIONS[dimension])
        # SELECT dimension parameters precede WHERE parameters in SQL order.
        measures = [_column(m, metric_columns) for m in parsed.measures]
        select = [f"{sql} AS d{i}" for i, sql in enumerate(dimension_sql)] + [f"{sql} AS m{i}" for i, (_, sql) in enumerate(measures)]
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        if parsed.level == 'cases' and 'variant' in parsed.dimensions:
            _materialize_variants(conn, where_sql, params)
        group = " GROUP BY " + ", ".join(f"d{i}" for i in range(len(dimension_sql))) if dimension_sql else ""
        order = {"first_measure_desc": "m0 DESC NULLS LAST", "first_measure_asc": "m0 ASC NULLS LAST",
                 "dimensions": ", ".join(f"d{i} NULLS LAST" for i in range(len(dimension_sql))) or "m0 DESC"}[parsed.order]
        tiebreak = "".join(f", d{i} NULLS LAST" for i in range(len(dimension_sql)))
        sql = f"SELECT {', '.join(select)} FROM {source}{where_sql}{group} ORDER BY {order}{tiebreak} LIMIT ?"
        rows = conn.execute(sql, dimension_params + params + [parsed.limit + 1]).fetchall()
        total = conn.execute(f"SELECT count(*), count(DISTINCT case_id) FROM {source}{where_sql}", params).fetchone()
        columns = list(parsed.dimensions) + [label for label, _ in measures]
        return {
            "spec": parsed.model_dump(),
            "columns": columns,
            "rows": [[_finite(v) for v in row] for row in rows[:parsed.limit]],
            "non_finite_results_withheld": sum(isinstance(v, float) and not math.isfinite(v)
                                               for row in rows[:parsed.limit] for v in row),
            "rows_truncated": len(rows) > parsed.limit,
            "matching": {"rows": total[0], "cases": total[1]},
            "cohort": cohorts.describe(cohorts.parse_cohort(parsed.cohort)),
            "query_basis": "typed_spec_compiled_to_parameterized_sql",
            "variant_encoding": 'ordered_activity_json_array' if 'variant' in parsed.dimensions else None,
            "metrics": metric_coverage,
            "units": {label: _unit(m, metric_coverage, parsed.level) for (label, _), m in zip(measures, parsed.measures)},
        }
    try:
        return workspace._query(log_id, parsed.case_type, execute, **({"control": control} if control is not None else {}))
    except duckdb.OutOfMemoryException:
        raise workspace.EventLogWorkspaceError(
            'Analysis exceeds available memory; select narrower cases or another dimension. '
            'No partial result was returned.') from None


def list_saved(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT analysis_id, created_at, definition FROM saved_analyses WHERE log_id = ? "
                                "ORDER BY created_at, analysis_id", [log_id]).fetchall()
        finally:
            conn.close()
    return {"analyses": [{"analysis_id": a, "created_at": c.isoformat() + "Z",
                          **SavedAnalysis.model_validate_json(d).model_dump()} for a, c, d in rows],
            "max_saved": MAX_SAVED}


def save(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise workspace.EventLogWorkspaceError("saved analysis must be an object with name and spec")
    try:
        saved = SavedAnalysis(name=body.get("name", ""), spec=parse(body.get("spec")))
    except ValidationError:
        raise workspace.EventLogWorkspaceError("saved analysis needs a name of 1–120 characters") from None
    if set(body) - {"name", "spec"}:
        raise workspace.EventLogWorkspaceError("saved analysis accepts only name and spec")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if conn.execute("SELECT count(*) FROM saved_analyses WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_SAVED:
                raise workspace.EventLogWorkspaceError(f"a log can hold at most {MAX_SAVED} saved analyses")
            analysis_id = "ana_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute("INSERT INTO saved_analyses VALUES (?, ?, ?, ?)",
                         [log_id, analysis_id, created, saved.model_dump_json()])
        finally:
            conn.close()
    return {"analysis_id": analysis_id, "created_at": created.isoformat() + "Z", **saved.model_dump()}


def delete_saved(log_id: str, analysis_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if not conn.execute("SELECT count(*) FROM saved_analyses WHERE log_id = ? AND analysis_id = ?",
                                [log_id, str(analysis_id)]).fetchone()[0]:
                raise workspace.EventLogWorkspaceError("unknown saved analysis")
            conn.execute("DELETE FROM saved_analyses WHERE log_id = ? AND analysis_id = ?", [log_id, str(analysis_id)])
        finally:
            conn.close()
    return {"deleted": True, "analysis_id": analysis_id}


class SavedMetric(_Strict):
    metric: Metric


def list_metrics(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT metric_id, created_at, definition FROM metric_definitions WHERE log_id = ? "
                                "ORDER BY created_at, metric_id", [log_id]).fetchall()
        finally:
            conn.close()
    return {"metrics": [{"metric_id": m, "created_at": c.isoformat() + "Z", **json.loads(d)} for m, c, d in rows],
            "max_saved": MAX_SAVED, "format": METRIC_FORMAT}


def save_metric(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"metric"}:
        raise workspace.EventLogWorkspaceError("a saved case metric is {\"metric\": {...}}")
    try:
        metric = SavedMetric.model_validate(body).metric
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "case metric is invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    definition = {"format": METRIC_FORMAT, "metric": metric.model_dump()}
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            existing = [json.loads(d)["metric"]["name"] for (d,) in conn.execute(
                "SELECT definition FROM metric_definitions WHERE log_id = ?", [log_id]).fetchall()]
            if len(existing) >= MAX_SAVED:
                raise workspace.EventLogWorkspaceError(f"a log can hold at most {MAX_SAVED} saved case metrics")
            if metric.name in existing:
                raise workspace.EventLogWorkspaceError("a saved case metric with this name already exists")
            metric_id = "met_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute("INSERT INTO metric_definitions VALUES (?, ?, ?, ?)",
                         [log_id, metric_id, created, json.dumps(definition, sort_keys=True)])
        finally:
            conn.close()
    return {"metric_id": metric_id, "created_at": created.isoformat() + "Z", **definition}


def delete_metric(log_id: str, metric_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if not conn.execute("SELECT count(*) FROM metric_definitions WHERE log_id = ? AND metric_id = ?",
                                [log_id, str(metric_id)]).fetchone()[0]:
                raise workspace.EventLogWorkspaceError("unknown saved case metric")
            conn.execute("DELETE FROM metric_definitions WHERE log_id = ? AND metric_id = ?", [log_id, str(metric_id)])
        finally:
            conn.close()
    return {"deleted": True, "metric_id": metric_id}


__all__ = ["delete_metric", "delete_saved", "list_metrics", "list_saved", "parse", "run", "save", "save_metric"]
