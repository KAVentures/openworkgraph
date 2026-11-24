"""Write a saved log as an Excel (.xlsx) workbook, with the standard library only.

Analysts often want a log in Excel to sort, filter and share it. The workbook has one worksheet,
"Events", holding exactly the rows and columns of the CSV (exact interchange) export: case ID,
activity, timestamp and resource, one row per event under the chosen case notion. Every cell is an inline text cell, so
nothing is reinterpreted (no formulas, no dates guessed from text, IDs with leading zeros kept);
the header row is frozen and has an auto-filter. A worksheet holds at most 1,048,576 rows, so a
larger log is refused before anything is written. The workspace's own .xlsx reader reads the file
back to the same CSV.
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
# Characters XML 1.0 cannot carry; they are dropped from cells and counted.
_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Events" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


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
    rows = list(csv.reader(io.StringIO(text.getvalue())))
    if len(rows) > MAX_ROWS:
        raise EventLogWorkspaceError(f"the log has more than {MAX_ROWS - 1:,} events, more than one Excel worksheet holds")
    width = max((len(r) for r in rows), default=0)
    dropped = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", _CONTENT_TYPES)
        book.writestr("_rels/.rels", _RELS)
        book.writestr("xl/workbook.xml", _WORKBOOK)
        book.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        with book.open("xl/worksheets/sheet1.xml", "w") as sheet:
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
    return {"format": FORMAT, "rows": max(len(rows) - 1, 0), "columns": width, "control_characters_dropped": dropped}


__all__ = ["write", "FORMAT", "MAX_ROWS"]
