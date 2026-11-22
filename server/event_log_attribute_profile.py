"""Profile of the event attributes in a saved log.

For each attribute key this reports how many events and cases carry it, whether
it is numeric or categorical, whether its value stays the same within a case
(a case-level attribute such as region or customer segment) or changes (an
event-level attribute such as amount at a step), and its most frequent values
with the median duration of cases that have each value. Numeric attributes get
quantiles.

The profile helps pick filters, root-cause features and cohorts. Value-level
durations are descriptive: cases with a value can differ from the rest in many
other ways.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-attribute-profile.v1"
MAX_ATTRIBUTES = 100
_NUMERIC_TYPES = ("BIGINT", "UBIGINT", "DOUBLE", "INTEGER")
_VALUES = """SELECT ce.case_id, j.key AS attribute, j.type AS kind,
                    CASE WHEN j.type = 'VARCHAR' THEN j.value->>'$' ELSE j.value::VARCHAR END AS value
             FROM ce, json_each(ce.attributes) j WHERE j.type <> 'NULL'"""


class ProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_values: StrictInt = Field(default=10, ge=1, le=50)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


class CaseProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: str = Field(min_length=1, max_length=200)
    value: str | None = Field(default=None, min_length=1, max_length=4096)
    match: Literal["present", "missing", "varying"] = "present"
    cohort: dict[str, Any] = Field(default_factory=dict)
    after: str | None = Field(default=None, min_length=1, max_length=200)
    limit: StrictInt = Field(default=30, ge=1, le=100)


def cases(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    try:
        settings = CaseProfileSpec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid attribute case selection") from None
    if settings.value is not None and settings.match != "present":
        raise workspace.EventLogWorkspaceError("a literal value requires present attribute cases")
    cohort = cohorts.parse_cohort(settings.cohort)

    def run(conn):
        cohorts.restrict(conn, cohort, log_id)
        params = [settings.attribute]
        predicate = "value <> ''"
        if settings.value is not None:
            predicate = "value = ?"
            params.append(settings.value)
        distinct = "HAVING count(DISTINCT value) > 1" if settings.match == "varying" else ""
        matching = f"""SELECT case_id, count(*) AS matching_events FROM ({_VALUES})
                      WHERE attribute = ? AND {predicate} GROUP BY case_id {distinct}"""
        if settings.match == "missing":
            matching = f"""SELECT DISTINCT ce.case_id, 0 AS matching_events FROM ce
                           WHERE ce.case_id NOT IN (SELECT case_id FROM ({matching}))"""
        conn.execute("CREATE TEMP TABLE ap_case_matches AS " + matching, params)
        total = conn.execute("SELECT count(*) FROM ap_case_matches").fetchone()[0]
        where, args = (" WHERE case_id > ?", [settings.after]) if settings.after is not None else ("", [])
        rows = conn.execute(f"""WITH page AS (
                SELECT case_id, matching_events FROM ap_case_matches {where} ORDER BY case_id LIMIT ?)
            SELECT ce.case_id, count(*), epoch(max(ts) - min(ts)), count(*) - count(DISTINCT ts), matching_events
            FROM ce JOIN page USING (case_id) GROUP BY ce.case_id, matching_events ORDER BY ce.case_id""",
            [*args, settings.limit + 1]).fetchall()
        return {"selected_cases": total, "cases": [
            {"case_id": key, "events": events, "elapsed_seconds": elapsed, "timestamp_ties": ties,
             "matching_events": matches} for key, events, elapsed, ties, matches in rows[:settings.limit]],
            "next_after": rows[settings.limit - 1][0] if len(rows) > settings.limit else None}
    return workspace._query(log_id, case_type, run)


def _parse(spec: Any) -> ProfileSpec:
    try:
        parsed = ProfileSpec.model_validate(spec if spec is not None else {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "attribute profile settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def profile(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)
    numeric = ", ".join(f"'{t}'" for t in _NUMERIC_TYPES)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        events, cases = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM ce").fetchone()
        if not cases:
            raise workspace.EventLogWorkspaceError("no cases match the filter")
        conn.execute("""CREATE OR REPLACE TEMP TABLE ap_durations AS
            SELECT case_id, epoch(max(ts) - min(ts)) AS duration FROM ce GROUP BY case_id""")
        # One row per (event, attribute). JSON nulls and empty strings (blank CSV cells) count as missing;
        # text that parses as a number counts as numeric, as CSV attributes are always text.
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE ap_values AS
            SELECT case_id, attribute, kind, value,
                   CASE WHEN kind IN ({numeric}, 'VARCHAR') AND isfinite(try_cast(value AS DOUBLE))
                        THEN try_cast(value AS DOUBLE) END AS number
            FROM ({_VALUES})
            WHERE value <> ''""")
        rows = conn.execute(f"""SELECT attribute, count(*) AS events, count(DISTINCT case_id) AS cases,
              count(DISTINCT value) AS distinct_values, count(number) AS numeric_events,
              list(DISTINCT kind ORDER BY kind) AS kinds
            FROM ap_values GROUP BY 1 ORDER BY cases DESC, events DESC, 1 LIMIT {MAX_ATTRIBUTES + 1}""").fetchall()
        conn.execute("CREATE TEMP TABLE ap_keys AS SELECT unnest(?::VARCHAR[]) AS attribute",
                     [[row[0] for row in rows[:MAX_ATTRIBUTES]]])
        conn.execute("CREATE TEMP VIEW ap_visible AS SELECT ap_values.* FROM ap_values JOIN ap_keys USING (attribute)")
        varying = dict(conn.execute("""SELECT attribute, count(*) FROM (
              SELECT attribute, case_id FROM ap_visible GROUP BY 1, 2 HAVING count(DISTINCT value) > 1)
            GROUP BY 1""").fetchall())
        top = conn.execute("""WITH v AS (SELECT attribute, value, count(*) AS events FROM ap_visible GROUP BY 1, 2),
            c AS (SELECT vc.attribute, vc.value, count(*) AS cases, quantile_cont(d.duration, 0.5) AS median
                  FROM (SELECT DISTINCT attribute, value, case_id FROM ap_visible) vc
                  JOIN ap_durations d USING (case_id) GROUP BY 1, 2),
            ranked AS (SELECT v.attribute, v.value, v.events, c.cases, c.median,
                              row_number() OVER (PARTITION BY v.attribute ORDER BY c.cases DESC, v.events DESC, v.value) AS r
                       FROM v JOIN c USING (attribute, value))
            SELECT attribute, value, events, cases, median FROM ranked WHERE r <= ? ORDER BY attribute, r""",
                           [settings.top_values]).fetchall()
        quantiles = {a: (lo, q1, med, q3, hi, mean) for a, lo, q1, med, q3, hi, mean in conn.execute("""
            SELECT attribute, min(number), quantile_cont(number, 0.25), quantile_cont(number, 0.5),
                   quantile_cont(number, 0.75), max(number), avg(number)
            FROM ap_visible WHERE number IS NOT NULL GROUP BY 1""").fetchall()}
        overall = conn.execute("SELECT quantile_cont(duration, 0.5) FROM ap_durations").fetchone()[0]
        return {"events": events, "cases": cases, "rows": rows, "varying": varying, "top": top,
                "quantiles": quantiles, "median_duration": overall}

    data = workspace._query(log_id, settings.case_type, run)
    tops: dict[str, list[dict[str, Any]]] = {}
    for attribute, value, n_events, n_cases, median in data["top"]:
        tops.setdefault(attribute, []).append({"value": value, "events": n_events, "cases": n_cases,
                                               "case_share": round(n_cases / data["cases"], 6),
                                               "median_case_duration_seconds": median})
    attributes = []
    for attribute, n_events, n_cases, distinct, numeric_events, kinds in data["rows"][:MAX_ATTRIBUTES]:
        is_numeric = numeric_events == n_events
        entry = {"attribute": attribute, "events": n_events, "cases": n_cases,
                 "event_coverage": round(n_events / data["events"], 6),
                 "case_coverage": round(n_cases / data["cases"], 6),
                 "distinct_values": distinct, "value_types": list(kinds),
                 "kind": "numeric" if is_numeric else "categorical",
                 "cases_where_value_changes": data["varying"].get(attribute, 0),
                 "level": "event" if data["varying"].get(attribute) else "case",
                 "top_values": tops.get(attribute, []),
                 "unique_per_event": distinct == n_events and n_events > 1}
        if is_numeric and attribute in data["quantiles"]:
            lo, q1, med, q3, hi, mean = data["quantiles"][attribute]
            entry["numeric"] = {"min": lo, "p25": q1, "median": med, "p75": q3, "max": hi, "mean": mean}
        attributes.append(entry)
    return {
        "format": FORMAT,
        "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
        "events": data["events"],
        "cases": data["cases"],
        "median_case_duration_seconds": data["median_duration"],
        "attributes": attributes,
        "attributes_truncated": len(data["rows"]) > MAX_ATTRIBUTES,
        "interpretation": ("Descriptive profile of recorded event attributes. A case-level attribute keeps one value "
                           "in every case that has it; an event-level attribute changes within at least one case. "
                           "Median durations per value describe cases with that value and are not evidence that the "
                           "value causes the duration."),
    }
