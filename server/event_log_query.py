"""Ad-hoc analysis builder over saved event logs (roadmap #326 F3).

A typed, bounded alternative to writing SQL or PQL: pick a level (cases or
events), optional filters, up to two dimensions and up to four measures. The
spec is validated against fixed vocabularies and compiled to a parameterized
query; no user text ever becomes SQL. Analyses can be saved with the log and
are deleted with it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_ROWS = 500
MAX_SAVED = 50
Name = Annotated[str, Field(min_length=1, max_length=200)]

# Fixed SQL fragments per vocabulary entry; user values only travel as parameters.
_CASE_DIMENSIONS = {
    "start_week": "strftime(date_trunc('week', started), '%Y-%m-%d')",
    "start_month": "strftime(date_trunc('month', started), '%Y-%m')",
    "end_week": "strftime(date_trunc('week', ended), '%Y-%m-%d')",
    "first_activity": "first_activity",
    "last_activity": "last_activity",
    "variant": "variant",
    "has_rework": "CASE WHEN rework THEN 'rework' ELSE 'no rework' END",
}
_EVENT_DIMENSIONS = {
    "activity": "activity",
    "resource": "nullif(resource, '')",
    "week": "strftime(date_trunc('week', ts), '%Y-%m-%d')",
    "month": "strftime(date_trunc('month', ts), '%Y-%m')",
    "weekday": "dayname(ts)",
    "hour": "CAST(hour(ts) AS VARCHAR)",
}
_CASE_VALUES = {"duration_seconds": "duration", "events": "events", "distinct_activities": "distinct_activities"}
_FUNCTIONS = {"median": "quantile_cont({x}, 0.5)", "p90": "quantile_cont({x}, 0.9)", "avg": "avg({x})",
              "min": "min({x})", "max": "max({x})", "sum": "sum({x})"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Measure(_Strict):
    fn: Literal["count", "cases", "share", "median", "p90", "avg", "min", "max", "sum"]
    of: Literal["duration_seconds", "events", "distinct_activities"] | None = None


class CaseFilter(_Strict):
    duration_over_seconds: StrictInt | None = Field(default=None, ge=0)
    duration_under_seconds: StrictInt | None = Field(default=None, ge=1)
    min_events: StrictInt | None = Field(default=None, ge=1)
    ends_with: Name | None = None
    lacks_activity: Name | None = None


class AnalysisSpec(_Strict):
    level: Literal["cases", "events"]
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    measures: list[Measure] = Field(min_length=1, max_length=4)
    cohort: dict[str, Any] = Field(default_factory=dict)
    where: CaseFilter = Field(default_factory=CaseFilter)
    activity_in: list[Name] = Field(default_factory=list, max_length=50)
    order: Literal["first_measure_desc", "first_measure_asc", "dimensions"] = "first_measure_desc"
    limit: StrictInt = Field(default=100, ge=1, le=MAX_ROWS)
    case_type: Name | None = None


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
    allowed = _CASE_DIMENSIONS if parsed.level == "cases" else _EVENT_DIMENSIONS
    for dimension in parsed.dimensions:
        if dimension not in allowed and _attribute_dimension(dimension) is None:
            raise workspace.EventLogWorkspaceError(
                f"unknown {parsed.level} dimension {dimension!r}; use one of {sorted(allowed)} or attribute:<name>")
    if len(set(parsed.dimensions)) != len(parsed.dimensions):
        raise workspace.EventLogWorkspaceError("dimensions must be different")
    for measure in parsed.measures:
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


def _column(measure: Measure) -> tuple[str, str]:
    label = measure.fn if measure.of is None else f"{measure.fn}_{measure.of}"
    if measure.fn == "count":
        return label, "count(*)"
    if measure.fn == "cases":
        return label, "count(DISTINCT case_id)"
    if measure.fn == "share":
        return label, "count(*) / sum(count(*)) OVER ()"
    return label, _FUNCTIONS[measure.fn].format(x=_CASE_VALUES[measure.of])


def run(log_id: str, spec: Any) -> dict[str, Any]:
    parsed = parse(spec)

    def execute(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(parsed.cohort), log_id)
        params: list[Any] = []
        if parsed.level == "cases":
            notion = workspace._case_type(conn, log_id, parsed.case_type)
            conn.execute("""CREATE OR REPLACE TEMP TABLE qc AS SELECT c.*, o.attributes AS case_attributes FROM (
                  SELECT case_id, min(ts) AS started, max(ts) AS ended,
                    date_diff('second', min(ts), max(ts)) AS duration, count(*) AS events,
                    count(DISTINCT activity) AS distinct_activities, count(*) > count(DISTINCT activity) AS rework,
                    arg_min(activity, ts) AS first_activity, arg_max(activity, ts) AS last_activity,
                    string_agg(activity, ' > ' ORDER BY ts, event_id) AS variant
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
            dimension_sql = []
            for dimension in parsed.dimensions:
                attribute = _attribute_dimension(dimension)
                if attribute is None:
                    dimension_sql.append(_CASE_DIMENSIONS[dimension])
                else:
                    dimension_sql.append("""map_extract(json_transform(case_attributes, '"MAP(VARCHAR, VARCHAR)"'), ?)[1]""")
        else:
            source = "ce"
            clauses = []
            if parsed.activity_in:
                clauses.append("list_contains(?, activity)"); params.append(parsed.activity_in)
            dimension_sql = []
            for dimension in parsed.dimensions:
                attribute = _attribute_dimension(dimension)
                dimension_sql.append(_EVENT_DIMENSIONS[dimension] if attribute is None else
                                     """map_extract(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?)[1]""")
        # Attribute names are parameters of the SELECT list, which precedes WHERE.
        dimension_params = [a for a in map(_attribute_dimension, parsed.dimensions) if a is not None]
        measures = [_column(m) for m in parsed.measures]
        select = [f"{sql} AS d{i}" for i, sql in enumerate(dimension_sql)] + [f"{sql} AS m{i}" for i, (_, sql) in enumerate(measures)]
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
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
            "rows": [[(round(v, 6) if isinstance(v, float) else v) for v in row] for row in rows[:parsed.limit]],
            "rows_truncated": len(rows) > parsed.limit,
            "matching": {"rows": total[0], "cases": total[1]},
            "cohort": cohorts.describe(cohorts.parse_cohort(parsed.cohort)),
            "query_basis": "typed_spec_compiled_to_parameterized_sql",
        }
    return workspace._query(log_id, parsed.case_type, execute)


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


__all__ = ["delete_saved", "list_saved", "parse", "run", "save"]
