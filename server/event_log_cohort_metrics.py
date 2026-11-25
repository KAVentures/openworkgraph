"""Dashboard-only, bounded cohort metric evaluation from a saved local event log.

This is intentionally not an AI token endpoint. The route authenticates the
live dashboard session before calling this module. Workspace._query verifies
the saved log and materializes the selected case notion; cohort restriction is
applied *before* any numeric projection.
"""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_metric_projection as projection
from . import event_log_derived_metrics as derived
from . import event_log_workspace as workspace

# Explicit, non-identifying numeric fields calculated from the selected cases.
FIELDS = {"duration_seconds", "event_count", "resource_count"}
MAX_CASES = derived.MAX_BATCH_ROWS


def summarize(log_id: str, spec: Any, case_type: str | None = None, *,
              include_case_values: bool = False) -> dict[str, Any]:
    """Summarize a metric with an optional dashboard-only case value review."""
    if not isinstance(spec, dict) or set(spec) != {"definition", "cohort"}:
        raise workspace.EventLogWorkspaceError("definition and cohort are required")
    chosen = cohorts.parse_cohort(spec["cohort"])
    definition = spec["definition"]
    try:
        derived.validate(definition, FIELDS)
    except derived.MetricDefinitionError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None

    def run(conn):
        before = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        cohorts.restrict(conn, chosen, log_id)
        count = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        if count > MAX_CASES:
            raise workspace.EventLogWorkspaceError(
                f"cohort has more than {MAX_CASES} cases; narrow the cohort before calculating a metric"
            )
        # Only the explicit case review selects case keys. Both queries use
        # identical scope and numeric fields with no user-supplied SQL.
        keys = ", case_id" if include_case_values else ""
        rows = conn.execute(f"""
            SELECT epoch(max(ts) - min(ts)) AS duration_seconds,
                   count(*) AS event_count,
                   count(DISTINCT resource) FILTER (WHERE resource <> '') AS resource_count
                   {keys}
            FROM ce GROUP BY case_id
        """).fetchall()
        fields = ("duration_seconds", "event_count", "resource_count")
        values = [dict(zip(fields, row)) for row in rows]
        try:
            result, evaluated = projection.evaluate_projected(definition, values, FIELDS)
        except derived.MetricDefinitionError as exc:
            raise workspace.EventLogWorkspaceError(str(exc)) from None
        response = {
            "metric": result,
            "cohort": cohorts.describe(chosen),
            "cases_before_filter": before,
            "metric_fields": sorted(FIELDS),
            "units": {"duration_seconds": "seconds", "event_count": "events/case",
                      "resource_count": "distinct nonempty resources/case"},
            "complete_cohort": True,
            "raw_cases_returned": False,
        }
        if include_case_values:
            pairs = sorted(zip(evaluated, (row[3] for row in rows)),
                           key=lambda pair: (pair[0] is None, pair[0], pair[1]))
            response["case_values"] = [{"case_id": case_id, "value": str(value) if value is not None else None}
                                       for value, case_id in pairs]
        return response
    return workspace._query(log_id, case_type, run)
