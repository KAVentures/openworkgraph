"""Stream original matching-case events in the chosen interchange format."""
from datetime import timezone
from typing import Any, IO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from shared.event_log_formats import EventLogFormatError, write_csv, write_csv_attributes, write_jsonl, write_ocel_json, write_ocel_xml, write_xes

from . import event_log_cohorts as cohorts
from . import event_log_derive as derived
from . import event_log_source_identity as source_identity
from . import event_log_workspace as workspace


MatchingExportFormat = Literal["csv", "csv-attributes", "csv-review", "sourcecsv", "sourcecsv-review", "jsonl", "xes", "xes-attributes", "ocel", "ocelxml"]


class ExportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cohort: dict[str, Any]
    include_unfiltered: bool = False
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    events_from: str | None = Field(default=None, min_length=10, max_length=40)
    events_before: str | None = Field(default=None, min_length=10, max_length=40)
    trim_from_activity: str | None = Field(default=None, min_length=1, max_length=500)
    trim_to_activity: str | None = Field(default=None, min_length=1, max_length=500)
    trim_mode: Literal["longest", "first"] = "longest"


def export_matching_cases(log_id: str, spec: Any, out: IO[str], *,
                          export_format: MatchingExportFormat = "csv") -> None:
    try:
        settings = ExportSpec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("Invalid matching-case export settings") from None
    chosen = cohorts.parse_cohort(settings.cohort)
    start, end = derived._window(settings.events_from, settings.events_before)
    trim = derived.activity_trim(settings)
    if (settings.case_id is None and not settings.include_unfiltered
            and not any(key != "label" for key in chosen) and start is None and end is None and trim is None):
        raise workspace.EventLogWorkspaceError(
            "Choose a case filter or a time window, or an activity trim, before exporting matching cases")

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        if settings.case_id is not None:
            conn.execute("DELETE FROM ce WHERE case_id <> ?", [settings.case_id])
        if conn.execute("SELECT 1 FROM ce LIMIT 1").fetchone() is None:
            raise workspace.EventLogWorkspaceError("No cases match the filter")
        is_ocel = export_format in ("ocel", "ocelxml")
        is_source_csv = export_format in ("sourcecsv", "sourcecsv-review")
        has_window = start is not None or end is not None or trim is not None
        if is_ocel or is_source_csv or has_window:
            derived._select_original_events(conn, log_id, start, end, trim)
        if is_ocel:
            objects, events = workspace._ocel_export_projection(conn, log_id, matching=True)
            writer = write_ocel_json if export_format == "ocel" else write_ocel_xml
            writer(objects, events, out)
            return {}
        notion = workspace._case_type(conn, log_id, settings.case_type)
        if is_source_csv:
            try:
                source_identity.export_csv(conn, log_id, notion, out, matching=True,
                                           spreadsheet_review=export_format == "sourcecsv-review")
            except EventLogFormatError as exc:
                raise workspace.EventLogWorkspaceError(str(exc)) from None
            return {}

        # Filters see the analysis mapping. Export writes the original rows
        # for the matching cases, restricted to the optional event window.
        window_join = "SEMI JOIN dv_events ON source.event_id = dv_events.event_id" if has_window else ""
        if trim is not None:
            # A trimmed case keeps only its own segment, also when its events are shared with other cases.
            window_join = ("SEMI JOIN dv_case_events ON source.case_id = dv_case_events.case_id"
                           " AND source.event_id = dv_case_events.event_id")
        is_xes = export_format in ("xes", "xes-attributes")
        include_attributes = export_format == "xes-attributes"
        grouped = is_xes or export_format == "csv-attributes"
        fields = "source.*" if grouped else "source.case_id, source.activity, source.ts, source.resource"
        join = ""
        params = [log_id, notion]
        if include_attributes:
            fields += ", o.attributes"
            join = "JOIN objects o ON o.log_id = ? AND o.object_id = source.case_id"
            params.append(log_id)
        # Column names first: a second query on this connection would end the export cursor.
        names: list[str] = []
        if export_format == "csv-attributes":
            names = workspace._event_attribute_names(conn, f"""(SELECT source.attributes FROM ({workspace._CASE_EVENTS}) source
                SEMI JOIN ce ON source.case_id = ce.case_id {window_join})""", [log_id, notion])
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
        if export_format == "csv-attributes":
            try:
                write_csv_attributes((event for _, events, _ in workspace._group_export_cursor(cursor) for event in events),
                                     names, out)
            except EventLogFormatError as exc:
                raise workspace.EventLogWorkspaceError(str(exc)) from None
            return {}

        def rows():
            while batch := cursor.fetchmany(10_000):
                for case_id, activity, timestamp, resource in batch:
                    yield {"case_id": case_id, "activity": activity,
                           "timestamp": timestamp.replace(tzinfo=timezone.utc), "resource": resource}

        if export_format == "jsonl":
            write_jsonl(rows(), out)
        else:
            write_csv(rows(), out, spreadsheet_review=export_format == "csv-review")
        return {}

    workspace._query(log_id, settings.case_type, run)
