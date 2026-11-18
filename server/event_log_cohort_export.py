"""Stream the original events of matching cases into a flat CSV."""
from datetime import timezone
from typing import Any, IO

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from shared.event_log_formats import write_csv

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace


class ExportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cohort: dict[str, Any]
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def export_matching_cases(log_id: str, spec: Any, out: IO[str]) -> None:
    try:
        settings = ExportSpec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("Invalid matching-case export settings") from None
    chosen = cohorts.parse_cohort(settings.cohort)
    if not any(key != "label" for key in chosen):
        raise workspace.EventLogWorkspaceError("Choose a case filter before exporting matching cases")

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        if conn.execute("SELECT 1 FROM ce LIMIT 1").fetchone() is None:
            raise workspace.EventLogWorkspaceError("No cases match the filter")
        notion = workspace._case_type(conn, log_id, settings.case_type)

        # Filters see the analysis mapping. The download retains the complete
        # original trace, including rows collapsed by an activity mapping.
        cursor = conn.execute(f"""
            SELECT source.case_id, source.activity, source.ts, source.resource
            FROM ({workspace._CASE_EVENTS}) source
            SEMI JOIN ce ON source.case_id = ce.case_id
            ORDER BY source.case_id, source.ts, source.event_id
        """, [log_id, notion])

        def rows():
            while batch := cursor.fetchmany(10_000):
                for case_id, activity, timestamp, resource in batch:
                    yield {"case_id": case_id, "activity": activity,
                           "timestamp": timestamp.replace(tzinfo=timezone.utc), "resource": resource}

        write_csv(rows(), out)
        return {}

    workspace._query(log_id, settings.case_type, run)
