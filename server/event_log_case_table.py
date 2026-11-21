"""Export one row per case for building or reviewing a case attribute table."""
from __future__ import annotations

import csv
import json
from typing import Any, IO

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shared.spreadsheet_review import review_cell

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace


class ExportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    cohort: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list, max_length=workspace.MAX_ATTRIBUTES)
    include_values: bool = False
    spreadsheet_review: bool = False

    @field_validator("columns")
    @classmethod
    def column_names(cls, names: list[str]) -> list[str]:
        folded = {name.lower() for name in names}
        if (len(folded) != len(names) or "case_id" in folded
                or any(not 1 <= len(name) <= 80 or name != name.strip()
                       or any(char in name for char in "\x00\r\n") for name in names)):
            raise ValueError("Choose distinct attribute names up to 80 characters, separate from case_id")
        return names


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def export(log_id: str, spec: Any, out: IO[str]) -> None:
    try:
        settings = ExportSpec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError(
            "Choose distinct case table columns up to 80 characters and a supported case notion"
        ) from None
    cohort = cohorts.parse_cohort(settings.cohort)
    filtered = any(key != "label" for key in cohort)

    def write(conn, notion):
        projected = "attributes" if settings.include_values and settings.columns else "NULL"
        scope = "AND object_id IN (SELECT case_id FROM ce)" if filtered else ""
        cursor = conn.execute(f"""SELECT object_id, {projected} FROM objects
            WHERE log_id = ? AND object_type = ? {scope} ORDER BY object_id""", [log_id, notion])
        writer = csv.writer(out)
        fields = ["case_id", *settings.columns]
        if settings.spreadsheet_review:
            writer.writerow([review_cell(name + "_review") for name in fields])
        else:
            writer.writerow(fields)
        while batch := cursor.fetchmany(10_000):
            for case_id, attributes in batch:
                data = json.loads(attributes) if attributes else {}
                row = [case_id, *(_cell(data.get(key)) for key in settings.columns)]
                writer.writerow([review_cell(value) for value in row] if settings.spreadsheet_review else row)
        return {}

    if filtered:
        def run(conn):
            cohorts.restrict(conn, cohort, log_id)
            return write(conn, workspace._case_type(conn, log_id, settings.case_type))

        workspace._query(log_id, settings.case_type, run)
        return
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            write(conn, workspace._case_type(conn, log_id, settings.case_type))
        finally:
            conn.close()
