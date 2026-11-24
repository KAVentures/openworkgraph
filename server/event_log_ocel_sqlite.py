"""Export a saved log as an OCEL 2.0 SQLite database.

OCEL 2.0 defines three interchange formats: JSON, XML and a relational SQLite schema. The workspace
already writes JSON and XML; this writes the SQLite form, which tools such as pm4py and OCEL-aware
databases read directly. The content is exactly the OCEL 2.0 JSON export of the log (the same
events, objects, qualified links, object-to-object relationships and timed object attributes),
laid out in the standard tables:

* ``event`` / ``object`` (``ocel_id``, ``ocel_type``), ``event_object`` and ``object_object``
  (with ``ocel_qualifier``);
* ``event_map_type`` / ``object_map_type`` naming one table per type, ``event_<Type>`` (``ocel_id``,
  ``ocel_time``, one column per event attribute) and ``object_<Type>`` (``ocel_id``, ``ocel_time``,
  ``ocel_changed_field``, one column per object attribute). An object's first values are dated
  1970-01-01 with no changed field, later values carry the name of the attribute that changed.

Attribute values are text, as in the workspace. The file is built in a temporary folder of the data
directory and streamed to the owner; nothing is kept.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-ocel-sqlite.v1"
_EPOCH = "1970-01-01T00:00:00Z"


def _table_names(types: list[str]) -> dict[str, str]:
    """A unique, SQL-safe table suffix per type (OCEL's ocel_type_map)."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for t in types:
        base = re.sub(r"[^A-Za-z0-9]+", "", t.title()) or "Type"
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base}{n}", n + 1
        used.add(name.lower())
        out[t] = name
    return out


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def write(log_id: str, path: Path) -> dict[str, Any]:
    buffer = io.StringIO()
    workspace.export_log(log_id, "ocel", buffer)
    doc = json.loads(buffer.getvalue())
    events, objects = doc["events"], doc["objects"]
    event_types = _table_names(sorted({e["type"] for e in events}))
    object_types = _table_names(sorted({o["type"] for o in objects}))
    # Attribute names are SQL column identifiers in OCEL SQLite. SQLite treats column names
    # case-insensitively; a supplied name must not shadow mandatory OCEL columns or another
    # supplied attribute. JSON/XML export remains available for such names.
    for kind, rows, reserved in (
        ("event", events, {"ocel_id", "ocel_time"}),
        ("object", objects, {"ocel_id", "ocel_time", "ocel_changed_field"}),
    ):
        by_type: dict[str, dict[str, str]] = {}
        for entry in rows:
            known = by_type.setdefault(entry["type"], {})
            for attribute in entry["attributes"]:
                name = attribute["name"]
                key = name.casefold()
                if key in reserved or (key in known and known[key] != name):
                    raise workspace.EventLogWorkspaceError(
                        f"OCEL SQLite attribute column {name[:80]!r} conflicts with a required "
                        f"column or another attribute of the same {kind} type; use OCEL JSON or XML instead")
                known[key] = name
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE event (ocel_id TEXT PRIMARY KEY, ocel_type TEXT);
            CREATE TABLE object (ocel_id TEXT PRIMARY KEY, ocel_type TEXT);
            CREATE TABLE event_object (ocel_event_id TEXT, ocel_object_id TEXT, ocel_qualifier TEXT,
                PRIMARY KEY (ocel_event_id, ocel_object_id, ocel_qualifier));
            CREATE TABLE object_object (ocel_source_id TEXT, ocel_target_id TEXT, ocel_qualifier TEXT,
                PRIMARY KEY (ocel_source_id, ocel_target_id, ocel_qualifier));
            CREATE TABLE event_map_type (ocel_type TEXT PRIMARY KEY, ocel_type_map TEXT);
            CREATE TABLE object_map_type (ocel_type TEXT PRIMARY KEY, ocel_type_map TEXT);""")
        conn.executemany("INSERT INTO event_map_type VALUES (?, ?)", event_types.items())
        conn.executemany("INSERT INTO object_map_type VALUES (?, ?)", object_types.items())
        for kind, suffix in event_types.items():
            rows = [e for e in events if e["type"] == kind]
            names = sorted({a["name"] for e in rows for a in e["attributes"]})
            table = _q("event_" + suffix)
            conn.execute(f"CREATE TABLE {table} (ocel_id TEXT PRIMARY KEY, ocel_time TIMESTAMP"
                         + "".join(f", {_q(n)} TEXT" for n in names) + ")")
            conn.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * (len(names) + 2))})",
                             [[e["id"], e["time"], *[{a["name"]: a["value"] for a in e["attributes"]}.get(n) for n in names]]
                              for e in rows])
        for kind, suffix in object_types.items():
            rows = [o for o in objects if o["type"] == kind]
            names = sorted({a["name"] for o in rows for a in o["attributes"]})
            table = _q("object_" + suffix)
            conn.execute(f"CREATE TABLE {table} (ocel_id TEXT, ocel_time TIMESTAMP, ocel_changed_field TEXT"
                         + "".join(f", {_q(n)} TEXT" for n in names) + ")")
            records = []
            for o in rows:
                first = {a["name"]: a["value"] for a in o["attributes"] if a["time"] == _EPOCH}
                records.append([o["id"], _EPOCH, None, *[first.get(n) for n in names]])
                for a in o["attributes"]:
                    if a["time"] != _EPOCH:
                        records.append([o["id"], a["time"], a["name"], *[a["value"] if n == a["name"] else None for n in names]])
            conn.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * (len(names) + 3))})", records)
        conn.executemany("INSERT INTO event VALUES (?, ?)", [(e["id"], e["type"]) for e in events])
        conn.executemany("INSERT INTO object VALUES (?, ?)", [(o["id"], o["type"]) for o in objects])
        conn.executemany("INSERT OR IGNORE INTO event_object VALUES (?, ?, ?)",
                         [(e["id"], r["objectId"], r["qualifier"]) for e in events for r in e["relationships"]])
        conn.executemany("INSERT OR IGNORE INTO object_object VALUES (?, ?, ?)",
                         [(o["id"], r["objectId"], r["qualifier"]) for o in objects for r in o["relationships"]])
        conn.commit()
    finally:
        conn.close()
    return {"format": FORMAT, "events": len(events), "objects": len(objects),
            "event_types": len(event_types), "object_types": len(object_types)}


__all__ = ["write", "FORMAT"]
