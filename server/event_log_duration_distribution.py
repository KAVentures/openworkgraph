"""Exact bands of observed case duration for a selected local event log.

A duration is the elapsed time between the first and last retained events of a
case, not a measure of work time or proof that a business case is complete.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

MAX_CUTOFFS = 10
# A maximum of 100 Julian years keeps the API bounded without assuming that
# old, delayed, future, or unfinished cases are erroneous.
MAX_CUTOFF_SECONDS = 3_155_760_000


def validate_cutoffs(value: Any) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CUTOFFS:
        raise workspace.EventLogWorkspaceError("choose between 1 and 10 duration thresholds")
    if any(type(x) is not int or not 1 <= x <= MAX_CUTOFF_SECONDS for x in value):
        raise workspace.EventLogWorkspaceError("duration thresholds must be whole seconds between 1 and 3155760000")
    if any(left >= right for left, right in zip(value, value[1:])):
        raise workspace.EventLogWorkspaceError("duration thresholds must be strictly increasing")
    return value[:]


def distribution(log_id: str, cutoffs_seconds: Any, case_type: str | None = None,
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    cutoffs = validate_cutoffs(cutoffs_seconds)

    def run(conn):
        # DuckDB timestamps in this workspace are UTC-normalized microseconds.
        # Integer microseconds give exact boundary handling, including durations
        # crossing a second boundary and one-event (zero-duration) cases.
        conn.execute("""CREATE TEMP TABLE duration_distribution_cases AS
            SELECT case_id, count(*) AS events,
                date_diff('microsecond', min(ts), max(ts)) AS duration_us
            FROM ce GROUP BY case_id""")
        cases, single_events, minimum, maximum, quantiles = conn.execute("""SELECT
            count(*), count(*) FILTER (WHERE events = 1), min(duration_us), max(duration_us),
            quantile_cont(duration_us / 1000000.0, [0.5, 0.9, 0.95])
            FROM duration_distribution_cases""").fetchone()
        clauses = " ".join(f"WHEN duration_us < ? THEN {index}" for index in range(len(cutoffs)))
        rows = conn.execute(f"""SELECT CASE {clauses} ELSE {len(cutoffs)} END AS bucket,
            count(*) FROM duration_distribution_cases GROUP BY bucket ORDER BY bucket""",
            [x * 1_000_000 for x in cutoffs]).fetchall()
        counts = dict(rows)
        bands = []
        for index in range(len(cutoffs) + 1):
            count = counts.get(index, 0)
            bands.append({"lower_seconds": 0 if index == 0 else cutoffs[index - 1],
                          "upper_seconds": cutoffs[index] if index < len(cutoffs) else None,
                          "cases": count, "share": count / cases if cases else None})
        return {
            "cases": cases,
            "single_event_cases": single_events,
            "cutoffs_seconds": cutoffs,
            "interval": "lower inclusive, upper exclusive",
            "bands": bands,
            "observed_duration_seconds": {
                "minimum": minimum / 1_000_000 if minimum is not None else None,
                "median": quantiles[0] if quantiles is not None else None,
                "p90": quantiles[1] if quantiles is not None else None,
                "p95": quantiles[2] if quantiles is not None else None,
                "maximum": maximum / 1_000_000 if maximum is not None else None,
            },
            "interpretation": ("Elapsed time between the first and last retained events for each selected "
                "case; not employee work time, completion, an SLA breach, or a verified business outcome. "
                "One-event cases have zero observed elapsed time. Imported histories may be incomplete."),
        }

    return workspace._query_filtered(log_id, case_type, cohort, run)
