"""Offline selected SAP change-document (CDHDR/CDPOS) projection; no network or credentials.

Turns an explicitly selected SAP change-document export into an event-log CSV for the
existing OWG import. One change document (CHANGENR) of a selected object becomes one
event. Its activity is the declared status field's new value when the export's CDPOS
rows carry one, otherwise "Created" or "Changed", with the transaction code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 200_000
MAX_OBJECTS = 32
HEADER_COLUMNS = {"OBJECTCLAS", "OBJECTID", "CHANGENR", "UDATE", "UTIME", "TCODE", "CHANGE_IND"}
POSITION_COLUMNS = {"OBJECTCLAS", "OBJECTID", "CHANGENR", "TABNAME", "FNAME", "VALUE_NEW"}
NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
OBJECT_CLASS = re.compile(r"[A-Z0-9_/]{1,15}\Z")
OBJECT_ID = re.compile(r"[A-Za-z0-9_/\-. ]{1,90}\Z")
CHANGE_NUMBER = re.compile(r"\d{1,10}\Z")
TCODE = re.compile(r"[A-Z0-9_/]{0,20}\Z")
FIELD = re.compile(r"[A-Z0-9_/]{1,30}\.[A-Z0-9_/]{1,30}\Z")
OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})\Z")


class ExportError(ValueError):
    """Counts-only error, never an exported business value."""


def _key(namespace: str, kind: str, *parts: str) -> str:
    return "sap:" + hashlib.sha256("\0".join((namespace, kind, *parts)).encode()).hexdigest()


def _offset(value: str) -> timezone:
    match = OFFSET.fullmatch(value or "")
    if not match:
        raise ExportError("Declare the SAP system's UTC offset as +HH:MM or -HH:MM")
    sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3))
    if hours > 14 or minutes not in (0, 15, 30, 45) or (hours == 14 and minutes):
        raise ExportError("Declare the SAP system's UTC offset as +HH:MM or -HH:MM")
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(-delta if sign == "-" else delta)


def _rows(raw: bytes, required: set[str], label: str):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ExportError(f"{label} exceeds the 16 MiB export limit")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers or len(headers) > 64 or len(headers) != len(set(headers)) or not required <= set(headers):
            raise ExportError(f"Expected unique {label} column headers")
        for count, row in enumerate(reader, 1):
            if count > MAX_ROWS:
                raise ExportError(f"{label} exceeds the 200,000-row export limit")
            if None in row or any(value is None for value in row.values()):
                raise ExportError(f"{label} row does not match its headers")
            yield {key: value.strip() for key, value in row.items()}
    except (UnicodeError, csv.Error) as exc:
        raise ExportError(f"Invalid {label} encoding or CSV syntax") from exc


def _label(value: str) -> str:
    if not value or len(value) > 80 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise ExportError("A selected status value is empty, too long or contains controls")
    return value


def project(headers: bytes, *, namespace: str, object_class: str, objects: list[str], utc_offset: str,
            positions: bytes | None = None, status_field: str | None = None) -> tuple[str, dict]:
    """Return canonical CSV and a sample-free report, or refuse the whole export."""
    if (not isinstance(namespace, str) or not NAMESPACE.fullmatch(namespace)
            or not isinstance(object_class, str) or not OBJECT_CLASS.fullmatch(object_class)
            or not isinstance(objects, list) or not 1 <= len(objects) <= MAX_OBJECTS
            or any(not isinstance(o, str) or not OBJECT_ID.fullmatch(o) or o != o.strip() for o in objects)
            or len(set(objects)) != len(objects)):
        raise ExportError("Supply one stable system namespace, one object class and 1–32 distinct literal object IDs")
    if (status_field is None) != (positions is None):
        raise ExportError("A status field needs the matching CDPOS export, and CDPOS needs a status field")
    if status_field is not None and (not isinstance(status_field, str) or not FIELD.fullmatch(status_field)):
        raise ExportError("Name the status field as TABNAME.FNAME")
    zone = _offset(utc_offset)
    selected = set(objects)
    counts = dict(header_rows=0, unselected_rows=0, other_class_rows=0, duplicate_rows=0,
                  position_rows=0, status_positions=0)
    documents: dict[tuple[str, str], tuple[str, datetime, str]] = {}
    for row in _rows(headers, HEADER_COLUMNS, "CDHDR"):
        counts["header_rows"] += 1
        if row["OBJECTCLAS"] != object_class:
            counts["other_class_rows"] += 1
            continue
        if row["OBJECTID"] not in selected:
            counts["unselected_rows"] += 1
            continue
        if not CHANGE_NUMBER.fullmatch(row["CHANGENR"]) or not TCODE.fullmatch(row["TCODE"]):
            raise ExportError("A selected change document has an invalid change number or transaction code")
        if row["CHANGE_IND"] not in ("I", "U"):
            raise ExportError("A selected change document has an unsupported change indicator")
        try:
            # strptime alone would accept short fields such as UTIME "9300".
            if not (re.fullmatch(r"\d{8}", row["UDATE"]) and re.fullmatch(r"\d{6}", row["UTIME"])):
                raise ValueError("not fixed-width")
            local = datetime.strptime(row["UDATE"] + row["UTIME"], "%Y%m%d%H%M%S")
        except ValueError:
            raise ExportError("A selected change document needs UDATE as YYYYMMDD and UTIME as HHMMSS") from None
        key = (row["OBJECTID"], row["CHANGENR"])
        value = (row["CHANGE_IND"], local.replace(tzinfo=zone).astimezone(timezone.utc), row["TCODE"])
        if key in documents:
            if documents[key] != value:
                raise ExportError("A change number has conflicting header values")
            counts["duplicate_rows"] += 1
        documents[key] = value
    if not documents:
        raise ExportError("No selected change documents were found")
    statuses: dict[tuple[str, str], str] = {}
    if positions is not None:
        table, field = status_field.split(".")
        for row in _rows(positions, POSITION_COLUMNS, "CDPOS"):
            counts["position_rows"] += 1
            key = (row["OBJECTID"], row["CHANGENR"])
            if (row["OBJECTCLAS"] != object_class or key not in documents
                    or row["TABNAME"] != table or row["FNAME"] != field):
                continue
            status = _label(row["VALUE_NEW"])
            if statuses.get(key, status) != status:
                raise ExportError("A change document sets the status field to two different values")
            statuses[key] = status
            counts["status_positions"] += 1
    events = []
    for (object_id, change_number), (indicator, moment, tcode) in documents.items():
        if (object_id, change_number) in statuses:
            activity = "Status: " + statuses[(object_id, change_number)]
        else:
            activity = "Created" if indicator == "I" else "Changed"
            if tcode:
                activity += " (" + tcode + ")"
        events.append((_key(namespace, "object", object_class, object_id), activity, moment.isoformat(),
                       _key(namespace, "change", object_class, object_id, change_number)))
    events.sort(key=lambda event: (event[0], event[2], event[3]))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["case_id", "activity", "timestamp", "source_event_id"])
    writer.writerows(events)
    times = [(event[0], event[2]) for event in events]
    report = {"format": "openworkgraph.sap-change-documents-report.v1", **counts,
              "selected_objects": len(objects), "represented_objects": len({e[0] for e in events}),
              "events": len(events), "status_events": len(statuses),
              "tied_timestamp_events": len(times) - len(set(times)),
              "declared_utc_offset": utc_offset, "projection": "change_documents_and_declared_status_only",
              "source_completeness": "unknown", "identity_basis": "caller_declared_namespace_and_literal_ids",
              "users_or_old_values_copied": False, "business_writes_performed": False}
    return output.getvalue(), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cdhdr", type=Path, help="CDHDR export CSV")
    parser.add_argument("--cdpos", type=Path, help="CDPOS export CSV; needs --status-field")
    parser.add_argument("--status-field", help="TABNAME.FNAME whose new value becomes the activity")
    parser.add_argument("--namespace", required=True, help="Stable, caller-declared SAP system namespace")
    parser.add_argument("--object-class", required=True, help="Change-document object class, e.g. VERKBELEG")
    parser.add_argument("--object", action="append", required=True, dest="objects",
                        help="Literal OBJECTID; repeat up to 32")
    parser.add_argument("--utc-offset", required=True, help="The SAP system time zone's offset, e.g. +01:00")
    parser.add_argument("--output", type=Path, required=True, help="New CSV path; existing files are refused")
    args = parser.parse_args()
    try:
        with args.cdhdr.open("rb") as handle:
            headers = handle.read(MAX_BYTES + 1)
        positions = None
        if args.cdpos is not None:
            with args.cdpos.open("rb") as handle:
                positions = handle.read(MAX_BYTES + 1)
        content, report = project(headers, namespace=args.namespace, object_class=args.object_class,
                                  objects=args.objects, utc_offset=args.utc_offset,
                                  positions=positions, status_field=args.status_field)
        with args.output.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ExportError):
        print(json.dumps({"status": "refused",
                          "reason": "Check export columns, selected objects, UTC offset, bounds and a new output path"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
