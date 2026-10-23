"""Read and write business event logs as CSV, XES (IEEE 1849) and OCEL 2.0 JSON.

Every format maps to one canonical, object-centric shape:

* events:        (event_id, activity, timestamp UTC, resource, attributes)
* objects:       (object_id, object_type, attributes)
* event_objects: (event_id, object_id, qualifier)

A flat log (CSV or XES) is the special case of a single object type named
``case``. Readers stream where the format allows, refuse XML DTDs and entities,
and bound sizes so a hostile file cannot exhaust memory. Values are data only:
nothing read here is executed, rendered as HTML or treated as an instruction.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import IO, Any, Iterable, Iterator
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

MAX_ID = 200
MAX_ACTIVITY = 200
MAX_ATTRIBUTES = 32
MAX_ATTRIBUTE_VALUE = 500
MAX_OCEL_BYTES = 256 * 1024 * 1024
CASE_TYPE = "case"
_XES_NS = "{http://www.xes-standard.org/}"
_FORBIDDEN_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class EventLogFormatError(ValueError):
    """A user-facing reason the log cannot be read; never echoes file content."""


@dataclass
class EventRecord:
    event_id: str
    activity: str
    timestamp: datetime
    resource: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    objects: list[tuple[str, str]] = field(default_factory=list)  # (object_id, qualifier)


@dataclass
class ObjectRecord:
    object_id: str
    object_type: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class ReadStats:
    events: int = 0
    rejected_events: int = 0
    objects: int = 0


def _text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").strip()
    return text[:limit]


def parse_timestamp(value: Any) -> datetime | None:
    raw = _text(value, 64)
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None  # an unzoned time cannot be ordered safely across sources
    return stamp.astimezone(timezone.utc)


def _attributes(pairs: Iterable[tuple[Any, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in pairs:
        name = _text(key, 80)
        if not name or name in out:
            continue
        out[name] = _text(value, MAX_ATTRIBUTE_VALUE)
        if len(out) >= MAX_ATTRIBUTES:
            break
    return out


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- CSV

def read_csv(stream: IO[str], *, case_column: str, activity_column: str, timestamp_column: str,
             resource_column: str = "", attribute_columns: Iterable[str] = (),
             delimiter: str = ",", stats: ReadStats | None = None) -> Iterator[EventRecord | ObjectRecord]:
    """Stream a flat CSV log; each new case id yields one ``case`` object first."""
    if delimiter not in {",", ";", "\t", "|"}:
        raise EventLogFormatError("unsupported delimiter")
    stats = stats if stats is not None else ReadStats()
    reader = csv.DictReader(stream, delimiter=delimiter)
    header = set(reader.fieldnames or [])
    wanted = [case_column, activity_column, timestamp_column] + ([resource_column] if resource_column else [])
    missing = [name for name in wanted if name not in header]
    if missing:
        raise EventLogFormatError("missing required columns: " + ", ".join(sorted(missing))[:300])
    extra = [name for name in attribute_columns if name in header][:MAX_ATTRIBUTES]
    seen: set[str] = set()
    for index, row in enumerate(reader, 1):
        case_id = _text(row.get(case_column), MAX_ID)
        activity = _text(row.get(activity_column), MAX_ACTIVITY)
        stamp = parse_timestamp(row.get(timestamp_column))
        if not case_id or not activity or stamp is None:
            stats.rejected_events += 1
            continue
        if case_id not in seen:
            seen.add(case_id)
            stats.objects += 1
            yield ObjectRecord(case_id, CASE_TYPE)
        stats.events += 1
        yield EventRecord(f"e{index}", activity, stamp,
                          _text(row.get(resource_column), MAX_ID) if resource_column else "",
                          _attributes((name, row.get(name)) for name in extra), [(case_id, CASE_TYPE)])


def write_csv(events: Iterable[dict[str, Any]], out: IO[str]) -> None:
    """Flat CSV for one case notion: case_id, activity, timestamp, resource."""
    writer = csv.writer(out)
    writer.writerow(["case_id", "activity", "timestamp", "resource"])
    for event in events:
        writer.writerow([event["case_id"], event["activity"], _iso(event["timestamp"]), event.get("resource") or ""])


# ---------------------------------------------------------------- XES

def _refuse_dtd(stream: IO[bytes]) -> None:
    position = stream.tell()
    tail = b""
    while chunk := stream.read(1 << 20):
        if _FORBIDDEN_XML.search(tail + chunk):
            raise EventLogFormatError("XML with a DOCTYPE or entity declaration is not accepted")
        tail = chunk[-16:]
    stream.seek(position)


def _xes_key_values(element: ET.Element) -> Iterator[tuple[str, str, str]]:
    for child in element:
        tag = child.tag.replace(_XES_NS, "")
        if tag in {"string", "date", "int", "float", "boolean", "id"}:
            yield tag, child.get("key") or "", child.get("value") or ""


def read_xes(stream: IO[bytes], *, stats: ReadStats | None = None) -> Iterator[EventRecord | ObjectRecord]:
    """Stream an XES log: traces become ``case`` objects, events keep standard extensions."""
    stats = stats if stats is not None else ReadStats()
    _refuse_dtd(stream)
    trace_index = 0
    event_index = 0
    root: ET.Element | None = None
    try:
        for kind, element in ET.iterparse(stream, events=("start", "end")):
            if root is None:
                root = element
            tag = element.tag.replace(_XES_NS, "")
            if kind != "end" or tag != "trace":
                continue
            trace_index += 1
            values = list(_xes_key_values(element))
            case_id = next((_text(v, MAX_ID) for _, k, v in values if k == "concept:name"), "") or f"trace{trace_index}"
            yield ObjectRecord(case_id, CASE_TYPE,
                               _attributes((k, v) for _, k, v in values if k != "concept:name"))
            stats.objects += 1
            for event in element:
                if event.tag.replace(_XES_NS, "") != "event":
                    continue
                event_index += 1
                fields = list(_xes_key_values(event))
                lookup = {k: v for _, k, v in fields}
                activity = _text(lookup.get("concept:name"), MAX_ACTIVITY)
                stamp = parse_timestamp(lookup.get("time:timestamp"))
                if not activity or stamp is None:
                    stats.rejected_events += 1
                    continue
                stats.events += 1
                yield EventRecord(_text(lookup.get("identity:id"), MAX_ID) or f"e{event_index}", activity, stamp,
                                  _text(lookup.get("org:resource"), MAX_ID),
                                  _attributes((k, v) for _, k, v in fields if k not in {
                                      "concept:name", "time:timestamp", "org:resource", "identity:id"}),
                                  [(case_id, CASE_TYPE)])
            element.clear()
            root.clear()  # keep memory bounded by one trace, however long the log
    except ET.ParseError:
        raise EventLogFormatError("XES XML could not be parsed") from None


def write_xes(cases: Iterable[tuple[str, list[dict[str, Any]]]], out: IO[str]) -> None:
    """XES 1849 with concept, time and org extensions; one trace per case."""
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<log xes.version="1849-2016" xmlns="http://www.xes-standard.org/">\n')
    out.write('  <extension name="Concept" prefix="concept" uri="http://www.xes-standard.org/concept.xesext"/>\n')
    out.write('  <extension name="Time" prefix="time" uri="http://www.xes-standard.org/time.xesext"/>\n')
    out.write('  <extension name="Organizational" prefix="org" uri="http://www.xes-standard.org/org.xesext"/>\n')
    for case_id, events in cases:
        out.write(f'  <trace>\n    <string key="concept:name" value={quoteattr(case_id)}/>\n')
        for event in events:
            out.write("    <event>\n")
            out.write(f'      <string key="concept:name" value={quoteattr(event["activity"])}/>\n')
            out.write(f'      <date key="time:timestamp" value="{escape(_iso(event["timestamp"]))}"/>\n')
            if event.get("resource"):
                out.write(f'      <string key="org:resource" value={quoteattr(event["resource"])}/>\n')
            out.write("    </event>\n")
        out.write("  </trace>\n")
    out.write("</log>\n")


# ---------------------------------------------------------------- OCEL 2.0 JSON

def _ocel_attributes(items: Any) -> dict[str, str]:
    if not isinstance(items, list):
        return {}
    return _attributes((item.get("name"), item.get("value")) for item in items if isinstance(item, dict))


def read_ocel_json(stream: IO[bytes], *, stats: ReadStats | None = None) -> Iterator[EventRecord | ObjectRecord]:
    """Read OCEL 2.0 JSON: typed objects, typed events and qualified event-to-object links."""
    stats = stats if stats is not None else ReadStats()
    raw = stream.read(MAX_OCEL_BYTES + 1)
    if len(raw) > MAX_OCEL_BYTES:
        raise EventLogFormatError("OCEL file is larger than the supported limit")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EventLogFormatError("OCEL JSON could not be parsed") from None
    if not isinstance(document, dict) or not isinstance(document.get("objects"), list) \
            or not isinstance(document.get("events"), list):
        raise EventLogFormatError("OCEL 2.0 JSON requires objects and events lists")
    types: dict[str, str] = {}
    for item in document["objects"]:
        if not isinstance(item, dict):
            continue
        object_id, object_type = _text(item.get("id"), MAX_ID), _text(item.get("type"), MAX_ID)
        if not object_id or not object_type or object_id in types:
            continue
        types[object_id] = object_type
        stats.objects += 1
        yield ObjectRecord(object_id, object_type, _ocel_attributes(item.get("attributes")))
    for item in document["events"]:
        if not isinstance(item, dict):
            stats.rejected_events += 1
            continue
        event_id, activity = _text(item.get("id"), MAX_ID), _text(item.get("type"), MAX_ACTIVITY)
        stamp = parse_timestamp(item.get("time"))
        if not event_id or not activity or stamp is None:
            stats.rejected_events += 1
            continue
        links = []
        for link in item.get("relationships") or []:
            if isinstance(link, dict) and _text(link.get("objectId"), MAX_ID) in types:
                links.append((_text(link.get("objectId"), MAX_ID), _text(link.get("qualifier"), 80)))
        stats.events += 1
        yield EventRecord(event_id, activity, stamp, "", _ocel_attributes(item.get("attributes")), links)


def write_ocel_json(objects: Iterable[dict[str, Any]], events: Iterable[dict[str, Any]], out: IO[str]) -> None:
    """OCEL 2.0 JSON with declared object/event types and qualified relationships."""
    objects, events = list(objects), list(events)
    document = {
        "objectTypes": [{"name": name, "attributes": []} for name in sorted({o["object_type"] for o in objects})],
        "eventTypes": [{"name": name, "attributes": []} for name in sorted({e["activity"] for e in events})],
        "objects": [{"id": o["object_id"], "type": o["object_type"],
                     "attributes": [{"name": k, "time": "1970-01-01T00:00:00Z", "value": v}
                                    for k, v in sorted((o.get("attributes") or {}).items())],
                     "relationships": []} for o in objects],
        "events": [{"id": e["event_id"], "type": e["activity"], "time": _iso(e["timestamp"]),
                    "attributes": [{"name": k, "value": v} for k, v in sorted((e.get("attributes") or {}).items())],
                    "relationships": [{"objectId": oid, "qualifier": q} for oid, q in e.get("objects") or []]}
                   for e in events],
    }
    json.dump(document, out, ensure_ascii=False)


def detect_format(head: bytes) -> str:
    """Best-effort detection from the first bytes; callers may also choose explicitly."""
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:2048]
    if text.startswith(b"<"):
        return "xes"
    if text.startswith(b"{"):
        return "ocel"
    return "csv"


__all__ = [
    "CASE_TYPE", "EventLogFormatError", "EventRecord", "ObjectRecord", "ReadStats",
    "detect_format", "parse_timestamp", "read_csv", "read_ocel_json", "read_xes",
    "write_csv", "write_ocel_json", "write_xes",
]
