"""Read one worksheet of an Excel (.xlsx) workbook as CSV, with the standard library only.

Business exports often arrive as Excel files. The workspace imports CSV, so the
dashboard converts a chosen worksheet to CSV here and then uses the ordinary CSV
import (column mapping, consent, quality reports) unchanged. Nothing is stored.

Safety and limits:

* the file must be a real .xlsx (a zip with ``xl/workbook.xml``); macro-enabled
  and binary workbooks are refused;
* the uncompressed size of every part and of the whole workbook is bounded, so
  a small file cannot expand without limit;
* XML parts that declare a DOCTYPE are refused;
* at most ``MAX_ROWS`` rows and ``MAX_COLUMNS`` columns are read.

Cells become text: shared and inline strings as written, booleans as
TRUE/FALSE, numbers without a trailing ``.0``, and cells formatted as dates or
times as ISO 8601 without an offset, because Excel stores no time zone. The CSV
import then asks which offset such times use.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta
from typing import IO, Any
from xml.etree import ElementTree as ET

FORMAT = "openworkgraph.event-log-xlsx-conversion.v1"
MAX_PART_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ROWS = 2_000_000
MAX_COLUMNS = 500
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
_CELL = re.compile(r"^([A-Z]{1,3})(\d+)$")


class XlsxError(ValueError):
    """The file is not a readable .xlsx workbook, or exceeds a limit."""


def _column_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_part(book: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = book.getinfo(name)
    except KeyError:
        raise XlsxError(f"the workbook has no {name}") from None
    if info.file_size > MAX_PART_BYTES:
        raise XlsxError("a part of the workbook is too large")
    data = book.read(name)
    if b"<!DOCTYPE" in data[:4096].upper():
        raise XlsxError("the workbook contains an XML document type declaration, which is not accepted")
    return data


def _date_format(code: str) -> bool:
    cleaned = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code.lower())
    return bool(re.search(r"[dy]|h{1,2}|s{1,2}", cleaned)) and "general" not in cleaned


def _styles(book: zipfile.ZipFile) -> list[bool]:
    """For each cell style index: whether it formats numbers as dates or times."""
    if "xl/styles.xml" not in book.namelist():
        return []
    root = ET.fromstring(_read_part(book, "xl/styles.xml"))
    custom = {int(f.get("numFmtId")): _date_format(f.get("formatCode") or "")
              for f in root.findall("m:numFmts/m:numFmt", NS) if (f.get("numFmtId") or "").isdigit()}
    out = []
    for xf in root.findall("m:cellXfs/m:xf", NS):
        fmt = int(xf.get("numFmtId") or 0)
        out.append(custom.get(fmt, fmt in _BUILTIN_DATE_FORMATS))
    return out


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(_read_part(book, "xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root.findall("m:si", NS)]


def _sheets(book: zipfile.ZipFile) -> tuple[list[tuple[str, str]], bool]:
    workbook = ET.fromstring(_read_part(book, "xl/workbook.xml"))
    rels = ET.fromstring(_read_part(book, "xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    sheets = []
    for sheet in workbook.findall("m:sheets/m:sheet", NS):
        target = targets.get(sheet.get(_REL)) or ""
        target = target.lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
        sheets.append((sheet.get("name") or "", path))
    pr = workbook.find("m:workbookPr", NS)
    date1904 = pr is not None and (pr.get("date1904") or "").lower() in ("1", "true")
    return sheets, date1904


def _number(text: str) -> str:
    value = float(text)
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)


def _date(text: str, date1904: bool) -> str:
    # Excel stores days as a binary float; round to whole milliseconds so 09:30:15 does not become 09:30:14.999.
    millis = round(float(text) * 86_400_000)
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    moment = base + timedelta(milliseconds=millis)
    return moment.isoformat(timespec="milliseconds" if moment.microsecond else "seconds")


def to_csv(stream: IO[bytes], sheet: str | None = None) -> dict[str, Any]:
    """Convert one worksheet (the first by default) to CSV text."""
    try:
        book = zipfile.ZipFile(stream)
    except zipfile.BadZipFile:
        raise XlsxError("this is not an .xlsx workbook (Excel 97-2003 .xls files are not supported)") from None
    with book:
        names = book.namelist()
        if "xl/workbook.xml" not in names:
            raise XlsxError("this is not an .xlsx workbook")
        if "xl/vbaProject.bin" in names:
            raise XlsxError("macro-enabled workbooks are not accepted; save the sheet as .xlsx without macros")
        if sum(i.file_size for i in book.infolist()) > MAX_TOTAL_BYTES:
            raise XlsxError("the workbook is too large when uncompressed")
        sheets, date1904 = _sheets(book)
        if not sheets:
            raise XlsxError("the workbook has no worksheets")
        chosen = sheets[0] if sheet is None else next((s for s in sheets if s[0] == sheet), None)
        if chosen is None:
            raise XlsxError("the workbook has no worksheet with that name")
        styles, strings = _styles(book), _shared_strings(book)
        try:
            info = book.getinfo(chosen[1])
        except KeyError:
            raise XlsxError("the worksheet is missing from the workbook") from None
        if info.file_size > MAX_PART_BYTES:
            raise XlsxError("the worksheet is too large")
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        rows = 0
        date_columns: set[int] = set()
        width = 0
        with book.open(chosen[1]) as part:
            head = part.peek(4096) if hasattr(part, "peek") else b""
            if b"<!DOCTYPE" in head[:4096].upper():
                raise XlsxError("the workbook contains an XML document type declaration, which is not accepted")
            for _event, element in ET.iterparse(part, events=("end",)):
                if element.tag != f"{{{NS['m']}}}row":
                    continue
                values: dict[int, str] = {}
                next_col = 0
                for cell in element.findall("m:c", NS):
                    ref = _CELL.match(cell.get("r") or "")
                    col = _column_index(ref.group(1)) if ref else next_col
                    next_col = col + 1
                    if col >= MAX_COLUMNS:
                        raise XlsxError(f"the worksheet has more than {MAX_COLUMNS} columns")
                    kind = cell.get("t") or "n"
                    v = cell.find("m:v", NS)
                    text = v.text if v is not None and v.text is not None else None
                    if kind == "s" and text is not None:
                        value = strings[int(text)] if int(text) < len(strings) else ""
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
                    elif kind == "b" and text is not None:
                        value = "TRUE" if text == "1" else "FALSE"
                    elif kind in ("str", "e") and text is not None:
                        value = text
                    elif text is not None:
                        style = int(cell.get("s") or 0)
                        if style < len(styles) and styles[style]:
                            value = _date(text, date1904)
                            if rows:
                                date_columns.add(col)
                        else:
                            value = _number(text)
                    else:
                        value = ""
                    values[col] = value
                element.clear()
                if not values or not any(values.values()):
                    continue
                rows += 1
                if rows > MAX_ROWS:
                    raise XlsxError(f"the worksheet has more than {MAX_ROWS:,} rows")
                width = max(width, max(values) + 1)
                writer.writerow([values.get(i, "") for i in range(max(values) + 1)])
        if not rows:
            raise XlsxError("the worksheet is empty")
    text = out.getvalue()
    # Pad short rows so every row has the header's width.
    if width:
        padded = io.StringIO()
        w = csv.writer(padded, lineterminator="\n")
        for row in csv.reader(io.StringIO(text)):
            w.writerow(row + [""] * (width - len(row)))
        text = padded.getvalue()
    header = next(csv.reader(io.StringIO(text)))
    return {"format": FORMAT, "csv": text, "sheet": chosen[0], "sheets": [s[0] for s in sheets],
            "rows": rows - 1, "columns": header,
            "date_columns": [header[i] for i in sorted(date_columns) if i < len(header)],
            "dates_have_no_offset": bool(date_columns), "stored": False}
