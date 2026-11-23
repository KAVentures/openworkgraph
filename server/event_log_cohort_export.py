"""Stream original matching-case events in the chosen interchange format."""
from datetime import timezone
from typing import Any, IO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from shared.event_log_formats import EventLogFormatError, write_csv, write_xes

from . import event_log_cohorts as cohorts
from . import event_log_derive as derived
from . import event_log_workspace as workspace


MatchingExportFormat = Literal["csv", "csv-review", "xes", "xes-attributes"]


class ExportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cohort: dict[str, Any]
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    events_from: str | None = Field(default=None, min_length=10, max_length=40)
    events_before: str | None = Field(default=None, min_length=10, max_length=40)


def export_matching_cases(log_id: str, spec: Any, out: IO[str], *,
                          export_format: MatchingExportFormat = "csv") -> None:
    try:
        settings = ExportSpec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("Invalid matching-case export settings") from None
    chosen = cohorts.parse_cohort(settings.cohort)
    start, end = derived._window(settings.events_from, settings.events_before)
    if not any(key != "label" for key in chosen):
        if start is None and end is None:
            raise workspace.EventLogWorkspaceError("Choose a case filter or a time window before exporting matching cases")

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        if conn.execute("SELECT 1 FROM ce LIMIT 1").fetchone() is None:
            raise workspace.EventLogWorkspaceError("No cases match the filter")
        notion = workspace._case_type(conn, log_id, settings.case_type)

        # Filters see the analysis mapping. Export writes the original rows
        # for the matching cases, restricted to the optional event window.
        window_join = ""
        if start is not None or end is not None:
            derived._select_original_events(conn, log_id, start, end)
            window_join = "SEMI JOIN dv_events ON source.event_id = dv_events.event_id"
        is_xes = export_format in ("xes", "xes-attributes")
        include_attributes = export_format == "xes-attributes"
        fields = "source.*" if is_xes else "source.case_id, source.activity, source.ts, source.resource"
        join = ""
        params = [log_id, notion]
        if include_attributes:
            fields += ", o.attributes"
            join = "JOIN objects o ON o.log_id = ? AND o.object_id = source.case_id"
            params.append(log_id)
        cursor = conn.execute(f"""
            SELECT {fields} FROM ({workspace._CASE_EVENTS}) source
            SEMI JOIN ce ON source.case_id = ce.case_id
            {window_join}
            {join}
            ORDER BY source.case_id, source.ts, source.event_id
        """, params)
        if is_xes:
            try:
                write_xes(workspace._group_export_cursor(cursor), out, include_attributes=include_attributes)
            except EventLogFormatError as exc:
                raise workspace.EventLogWorkspaceError(str(exc)) from None
            return {}

        def rows():
            while batch := cursor.fetchmany(10_000):
                for case_id, activity, timestamp, resource in batch:
                    yield {"case_id": case_id, "activity": activity,
                           "timestamp": timestamp.replace(tzinfo=timezone.utc), "resource": resource}

        write_csv(rows(), out, spreadsheet_review=export_format == "csv-review")
        return {}

    workspace._query(log_id, settings.case_type, run)
