"""Write a saved log as an Excel (.xlsx) workbook, with the standard library only.

Analysts often want a log in Excel to sort, filter and share it. The workbook has one worksheet,
"Events", holding exactly the rows and columns of the CSV (exact interchange) export: case ID,
activity, timestamp and resource, one row per event under the chosen case notion. Every cell is an inline text cell, so
nothing is reinterpreted (no formulas, no dates guessed from text, IDs with leading zeros kept);
the header row is frozen and has an auto-filter. A worksheet holds at most 1,048,576 rows, so a
larger log continues on further worksheets ("Events 2", "Events 3", ...), each with the header row,
up to MAX_SHEETS; anything larger is refused before writing. The workspace's own .xlsx reader reads
each worksheet back to the same CSV rows (import the first, append the others). ``write_matching`` writes the matching-case export's CSV rows (a case filter,
one case or a time window) the same way.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-xlsx-export.v1"
MAX_ROWS = 1_048_576
MAX_SHEETS = 16
# Characters XML 1.0 cannot carry; they are dropped from cells and counted.
_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

def _content_types(sheets: int) -> str:
    parts = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, sheets + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            f'spreadsheetml.sheet.main+xml"/>{parts}</Types>')


_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _sheet_name(index: int) -> str:
    return "Events" if index == 1 else f"Events {index}"


def _workbook(sheets: int) -> str:
    names = "".join(f'<sheet name="{_sheet_name(i)}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, sheets + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{names}</sheets>'
            '</workbook>')


def _workbook_rels(sheets: int) -> str:
    rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, sheets + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def write(log_id: str, path: Path, case_type: str | None = None) -> dict[str, Any]:
    text = io.StringIO()
    workspace.export_log(log_id, "csv", text, case_type)
    return _write_csv_text(text.getvalue(), path, "the log has")


def write_matching(log_id: str, spec: Any, path: Path) -> dict[str, Any]:
    """The matching-case export's CSV rows (filter, case, time window) as a workbook."""
    from .event_log_cohort_export import export_matching_cases
    text = io.StringIO()
    export_matching_cases(log_id, spec, text, export_format="csv")
    return _write_csv_text(text.getvalue(), path, "the matching cases have")


def _write_csv_text(text: str, path: Path, subject: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text)))
    header, data = (rows[0], rows[1:]) if rows else ([], [])
    per_sheet = MAX_ROWS - 1
    sheets = max(1, -(-len(data) // per_sheet))
    if sheets > MAX_SHEETS:
        raise EventLogWorkspaceError(f"{subject} more than {MAX_SHEETS * per_sheet:,} events, more than "
                                     f"{MAX_SHEETS} Excel worksheets hold")
    width = max((len(r) for r in rows), default=0)
    dropped = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", _content_types(sheets))
        book.writestr("_rels/.rels", _RELS)
        book.writestr("xl/workbook.xml", _workbook(sheets))
        book.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(sheets))
        for index in range(1, sheets + 1):
            chunk = ([header] if rows else []) + data[(index - 1) * per_sheet:index * per_sheet]
            dropped += _write_sheet(book, index, chunk, width)
    return {"format": FORMAT, "rows": len(data), "columns": width, "control_characters_dropped": dropped,
            "worksheets": sheets}


def _write_sheet(book: zipfile.ZipFile, index: int, rows: list[list[str]], width: int) -> int:
    dropped = 0
    with book.open(f"xl/worksheets/sheet{index}.xml", "w") as sheet:
        last = f"{_column(max(width, 1) - 1)}{max(len(rows), 1)}"
        sheet.write(('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                     f'<dimension ref="A1:{last}"/>'
                     '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/>'
                     '</sheetView></sheetViews><sheetData>').encode())
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                clean, n = _INVALID.subn("", value)
                dropped += n
                if clean:
                    cells.append(f'<c r="{_column(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(clean)}</t></is></c>')
            sheet.write(f'<row r="{r}">{"".join(cells)}</row>'.encode())
        sheet.write(f'</sheetData><autoFilter ref="A1:{last}"/></worksheet>'.encode())
    return dropped


__all__ = ["write", "write_matching", "FORMAT", "MAX_ROWS", "MAX_SHEETS"]
