"""Add case attributes to a saved log from a separate case table (CSV).

Event exports rarely carry everything known about a case: the customer
segment, region, contract value or channel usually live in a separate table
keyed by the case id. This joins such a table onto the log's case objects, so
cohort filters, root-cause associations and attribute profiles can use the
columns as they already use OCEL object attributes.

Rules, so nothing is guessed:

* the table needs an id column; ids are matched exactly (no trimming of case
  or padding differences) against the objects of one object type;
* an id listed more than once is refused, because it would be ambiguous;
* empty cells add nothing; values are kept as text, trimmed and bounded;
* a column that a case already has is refused unless ``replace`` is set;
* every case keeps at most ``MAX_ATTRIBUTES`` attribute names in all (stored
  and timed), so the log still exports as OCEL;
* rows whose id is not a case of the log are counted and ignored.

Each upload is recorded (columns, counts, SHA-256 of the file) and can be
undone: ``remove`` deletes attributes that an upload added, never attributes
that came with the original import. Event data and content digests are not
changed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-case-attributes.v1"
MAX_ROWS = 2_000_000
MAX_KEY = 80
_DELIMITERS = {",", ";", "\t", "|"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _object_type(conn, log_id: str, object_type: str | None) -> str:
    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT object_type FROM objects WHERE log_id = ? ORDER BY 1", [log_id]).fetchall()]
    if object_type:
        if object_type not in types:
            raise EventLogWorkspaceError("the log has no objects of that type")
        return object_type
    if len(types) != 1:
        raise EventLogWorkspaceError("choose the object type the table describes: " + ", ".join(types)[:300])
    return types[0]


def _sql_text(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def add(log_id: str, path: Path, *, id_column: str, columns: list[str], delimiter: str = ",",
        object_type: str | None = None, replace: bool = False) -> dict[str, Any]:
    """Merge the chosen columns of a CSV case table into the attributes of the matching objects."""
    if delimiter not in _DELIMITERS:
        raise EventLogWorkspaceError("unsupported delimiter")
    if not isinstance(id_column, str) or not id_column:
        raise EventLogWorkspaceError("choose the column that holds the case id")
    columns = list(dict.fromkeys(columns or []))
    if not columns:
        raise EventLogWorkspaceError("choose at least one column to add")
    if len(columns) > workspace.MAX_ATTRIBUTES:
        raise EventLogWorkspaceError(f"add at most {workspace.MAX_ATTRIBUTES} columns")
    if id_column in columns:
        raise EventLogWorkspaceError("the id column cannot also be added as an attribute")
    keys = {c: c.replace("\x00", "").strip()[:MAX_KEY] for c in columns}
    if any(not k for k in keys.values()) or len(set(keys.values())) != len(keys):
        raise EventLogWorkspaceError("column names must be non-empty and distinct after trimming")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = workspace.read_csv_header(handle, delimiter)
    missing = [c for c in [id_column, *columns] if c not in header]
    if missing:
        raise EventLogWorkspaceError("missing columns: " + ", ".join(missing)[:300])
    source_sha = _sha256(path)
    q = lambda name: 'r."' + name.replace('"', '""') + '"'
    entries = ", ".join(
        f"struct_pack(k := {_sql_text(keys[c])}, v := left(trim(replace(coalesce({q(c)}, ''), chr(0), '')), "
        f"{workspace.MAX_ATTRIBUTE_VALUE}))" for c in columns)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            notion = _object_type(conn, log_id, object_type)
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE case_table AS SELECT {q(id_column)} AS id,
                  to_json(map_from_entries(list_filter([{entries}], x -> x.v <> ''))) AS attrs
                FROM read_csv(?, header=true, names=?, delim=?, all_varchar=true, sample_size=-1) AS r""",
                         [str(path), header, delimiter])
            rows, blank_ids = conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE id IS NULL OR id = '') FROM case_table").fetchone()
            if rows > MAX_ROWS:
                raise EventLogWorkspaceError(f"the case table has more than {MAX_ROWS:,} rows")
            conn.execute("DELETE FROM case_table WHERE id IS NULL OR id = ''")
            duplicates = conn.execute("SELECT id FROM case_table GROUP BY id HAVING count(*) > 1 LIMIT 3").fetchall()
            if duplicates:
                raise EventLogWorkspaceError("the case table lists an id more than once, e.g. "
                                             + ", ".join(repr(d[0][:60]) for d in duplicates))
            conn.execute("""CREATE OR REPLACE TEMP TABLE matched AS SELECT c.id, c.attrs, o.attributes AS old
                FROM case_table c JOIN objects o ON o.object_id = c.id AND o.log_id = ? AND o.object_type = ?
                WHERE c.attrs <> '{}'""", [log_id, notion])
            matched = conn.execute("SELECT count(*) FROM matched").fetchone()[0]
            unmatched = conn.execute("""SELECT count(*) FROM case_table c WHERE NOT EXISTS (SELECT 1 FROM objects o
                WHERE o.log_id = ? AND o.object_type = ? AND o.object_id = c.id)""", [log_id, notion]).fetchone()[0]
            cases = conn.execute("SELECT count(*) FROM objects WHERE log_id = ? AND object_type = ?",
                                 [log_id, notion]).fetchone()[0]
            if not matched:
                raise EventLogWorkspaceError("no row of the case table matches a case of this log with a value to add")
            if not replace:
                clash = conn.execute("""SELECT DISTINCT k FROM matched, unnest(json_keys(attrs)) AS t(k)
                    WHERE json_exists(old, '$."' || replace(k, '"', '\\"') || '"') ORDER BY 1 LIMIT 5""").fetchall()
                if clash:
                    raise EventLogWorkspaceError("cases already have " + ", ".join(repr(c[0]) for c in clash)
                                                 + "; choose replace to overwrite those values")
            too_many = conn.execute(f"""SELECT count(*) FROM (
                  SELECT m.id, list_distinct(list_concat(json_keys(json_merge_patch(m.old, m.attrs)),
                    coalesce((SELECT list(DISTINCT v.name) FROM object_attribute_values v
                              WHERE v.log_id = ? AND v.object_id = m.id), []))) AS names FROM matched m)
                WHERE len(names) > {workspace.MAX_ATTRIBUTES}""", [log_id]).fetchone()[0]
            if too_many:
                raise EventLogWorkspaceError(f"{too_many} case(s) would have more than {workspace.MAX_ATTRIBUTES} "
                                             "attribute names; add fewer columns")
            filled = dict(conn.execute("""SELECT k, count(*) FROM matched, unnest(json_keys(attrs)) AS t(k)
                GROUP BY 1""").fetchall())
            conn.execute("BEGIN")
            try:
                conn.execute("""UPDATE objects SET attributes = json_merge_patch(objects.attributes, m.attrs)::VARCHAR
                    FROM matched m WHERE objects.log_id = ? AND objects.object_type = ? AND objects.object_id = m.id""",
                             [log_id, notion])
                record = {"format": FORMAT, "added_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                          "object_type": notion, "id_column": id_column, "columns": [keys[c] for c in columns],
                          "source_sha256": source_sha, "rows": rows, "rows_without_id": blank_ids,
                          "rows_not_in_log": unmatched, "cases_updated": matched,
                          "cases_without_row": cases - matched, "values_added": {keys[c]: filled.get(keys[c], 0)
                                                                                for c in columns},
                          "replaced_existing": bool(replace)}
                upload = "ca_" + hashlib.sha256((log_id + record["added_at"] + source_sha).encode()).hexdigest()[:16]
                record["upload_id"] = upload
                conn.execute("INSERT INTO case_attribute_uploads VALUES (?, ?, now(), ?)",
                             [log_id, upload, json.dumps(record, sort_keys=True)])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return record
        finally:
            conn.close()


def uploads(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("""SELECT record FROM case_attribute_uploads WHERE log_id = ?
                ORDER BY created_at, upload_id""", [log_id]).fetchall()
        finally:
            conn.close()
    return {"format": FORMAT, "uploads": [json.loads(r[0]) for r in rows]}


def remove(log_id: str, spec: Any) -> dict[str, Any]:
    """Remove attributes an upload added (by name) from every case of that upload's object type."""
    if not isinstance(spec, dict) or set(spec) - {"upload_id", "columns"} or not isinstance(spec.get("upload_id"), str):
        raise EventLogWorkspaceError("give the upload_id and, optionally, the columns to remove")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            row = conn.execute("SELECT record FROM case_attribute_uploads WHERE log_id = ? AND upload_id = ?",
                               [log_id, spec["upload_id"]]).fetchone()
            if not row:
                raise EventLogWorkspaceError("unknown case attribute upload")
            record = json.loads(row[0])
            columns = spec.get("columns") or record["columns"]
            if not isinstance(columns, list) or set(columns) - set(record["columns"]):
                raise EventLogWorkspaceError("only columns that this upload added can be removed")
            conn.execute("BEGIN")
            try:
                patch = json.dumps({c: None for c in columns})
                changed = conn.execute("""UPDATE objects SET attributes = json_merge_patch(attributes, ?)::VARCHAR
                    WHERE log_id = ? AND object_type = ? AND list_has_any(json_keys(attributes), ?)""",
                                       [patch, log_id, record["object_type"], columns]).fetchone()[0]
                left = [c for c in record["columns"] if c not in columns]
                if left:
                    record["columns"] = left
                    conn.execute("UPDATE case_attribute_uploads SET record = ? WHERE log_id = ? AND upload_id = ?",
                                 [json.dumps(record, sort_keys=True), log_id, spec["upload_id"]])
                else:
                    conn.execute("DELETE FROM case_attribute_uploads WHERE log_id = ? AND upload_id = ?",
                                 [log_id, spec["upload_id"]])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    return {"format": FORMAT, "removed_columns": columns, "cases_changed": changed}
