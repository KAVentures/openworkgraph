"""Owner-saved object rule definitions for an imported log."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Any

from pydantic import Field, StrictInt, ValidationError

from . import event_log_object_constraints as constraints
from . import event_log_workspace as workspace

MAX_SETS = 30
TEMPLATE_FORMAT = "openworkgraph.object-rule-set.v1"
MAX_TEMPLATE_BYTES = 128 * 1024


class _NewSet(constraints.RuleSet):
    name: str = Field(min_length=1, max_length=120)


class _UpdateSet(_NewSet):
    expected_revision: StrictInt = Field(ge=1)


def _parse(model, spec: Any):
    try:
        settings = model.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise workspace.EventLogWorkspaceError(
            f"saved object rule set: {location} {first['msg']}") from None
    name = settings.name.strip()
    if not name or any(ord(char) < 32 for char in name):
        raise workspace.EventLogWorkspaceError("name the rule set in 1 to 120 printable characters")
    rules = constraints.parse({"rules": settings.model_dump(exclude_none=True)["rules"]})
    definition = json.dumps(rules.model_dump(exclude_none=True), ensure_ascii=False)
    return settings, name, definition


def _with_log(log_id: str, operation):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            return operation(conn)
        finally:
            conn.close()


def _read(conn, log_id: str, set_id: str) -> dict[str, Any]:
    row = conn.execute("""SELECT name, revision, created_at, updated_at, definition FROM object_rule_sets
        WHERE log_id = ? AND set_id = ?""", [log_id, set_id]).fetchone()
    if row is None:
        raise workspace.EventLogWorkspaceError("no saved object rule set with this id in this log")
    name, revision, created, updated, definition = row
    return {"set_id": set_id, "name": name, "revision": revision, "created_at": created.isoformat() + "Z",
            "updated_at": updated.isoformat() + "Z", **json.loads(definition)}


def listing(log_id: str) -> dict[str, Any]:
    def run(conn):
        rows = conn.execute("""SELECT set_id, name, revision, updated_at, json_array_length(definition, '$.rules')
            FROM object_rule_sets WHERE log_id = ? ORDER BY name, set_id""", [log_id]).fetchall()
        return {"sets": [{"set_id": key, "name": name, "revision": revision, "updated_at": stamp.isoformat() + "Z",
                          "rule_count": count} for key, name, revision, stamp, count in rows]}
    return _with_log(log_id, run)


def get(log_id: str, set_id: str) -> dict[str, Any]:
    return _with_log(log_id, lambda conn: _read(conn, log_id, set_id))


def preview_template(log_id: str, document: Any) -> dict[str, Any]:
    if (not isinstance(document, dict) or set(document) != {"format", "definition"}
            or document["format"] != TEMPLATE_FORMAT):
        raise workspace.EventLogWorkspaceError("choose an OWG object rule set file")
    settings, name, encoded = _parse(_NewSet, document["definition"])
    try:
        name.encode("utf-8")
        encoded.encode("utf-8")
    except UnicodeEncodeError:
        raise workspace.EventLogWorkspaceError("rule definitions must use valid Unicode") from None
    rules = json.loads(encoded)["rules"]

    def run(conn):
        types = {rule[key] for rule in rules for key in ("object_type", "related_type") if key in rule}
        activities = {rule[key] for rule in rules
                      for key in ("activity", "via_activity", "required_activity") if key in rule}
        qualifiers = {rule["qualifier"] for rule in rules if "qualifier" in rule}
        present_types = {row[0] for row in conn.execute("""SELECT DISTINCT object_type FROM objects
            WHERE log_id = ? AND list_contains(?, object_type)""", [log_id, sorted(types)]).fetchall()}
        present_activities = {row[0] for row in conn.execute("""SELECT DISTINCT activity FROM events
            WHERE log_id = ? AND list_contains(?, activity)""", [log_id, sorted(activities)]).fetchall()}
        present_qualifiers = {row[0] for row in conn.execute("""SELECT DISTINCT qualifier FROM object_relationships
            WHERE log_id = ? AND list_contains(?, qualifier)""", [log_id, sorted(qualifiers)]).fetchall()}
        checks = []
        for rule, parsed in zip(rules, settings.rules):
            checks.append({
                "description": constraints.describe(parsed),
                "missing_object_types": sorted({rule[key] for key in ("object_type", "related_type")
                                                if key in rule and rule[key] not in present_types}),
                "missing_activities": sorted({rule[key] for key in ("activity", "via_activity", "required_activity")
                                              if key in rule and rule[key] not in present_activities}),
                "missing_qualifiers": [rule["qualifier"]] if "qualifier" in rule
                and rule["qualifier"] not in present_qualifiers else [],
            })
        return {"format": TEMPLATE_FORMAT, "definition": {"name": name, "rules": rules}, "label_checks": checks}
    return _with_log(log_id, run)


def save(log_id: str, spec: Any) -> dict[str, Any]:
    _, name, definition = _parse(_NewSet, spec)

    def run(conn):
        total = conn.execute("SELECT count(*) FROM object_rule_sets WHERE log_id = ?", [log_id]).fetchone()[0]
        if total >= MAX_SETS:
            raise workspace.EventLogWorkspaceError(f"a log keeps at most {MAX_SETS} object rule sets")
        if conn.execute("SELECT 1 FROM object_rule_sets WHERE log_id = ? AND name = ?", [log_id, name]).fetchone():
            raise workspace.EventLogWorkspaceError("an object rule set with this name already exists")
        key = "objrules_" + secrets.token_hex(8)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("INSERT INTO object_rule_sets VALUES (?, ?, ?, 1, ?, ?, ?)",
                     [log_id, key, name, now, now, definition])
        return _read(conn, log_id, key)
    return _with_log(log_id, run)


def update(log_id: str, set_id: str, spec: Any) -> dict[str, Any]:
    settings, name, definition = _parse(_UpdateSet, spec)

    def run(conn):
        current = _read(conn, log_id, set_id)
        if current["revision"] != settings.expected_revision:
            raise workspace.EventLogWorkspaceError("the saved rule set has changed; load it again before updating")
        if conn.execute("SELECT 1 FROM object_rule_sets WHERE log_id = ? AND name = ? AND set_id <> ?",
                        [log_id, name, set_id]).fetchone():
            raise workspace.EventLogWorkspaceError("an object rule set with this name already exists")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("""UPDATE object_rule_sets SET name = ?, revision = revision + 1, updated_at = ?, definition = ?
            WHERE log_id = ? AND set_id = ?""", [name, now, definition, log_id, set_id])
        return _read(conn, log_id, set_id)
    return _with_log(log_id, run)


def delete(log_id: str, set_id: str, expected_revision: int) -> dict[str, Any]:
    def run(conn):
        current = _read(conn, log_id, set_id)
        if current["revision"] != expected_revision:
            raise workspace.EventLogWorkspaceError("the saved rule set has changed; load it again before deleting")
        conn.execute("DELETE FROM object_rule_sets WHERE log_id = ? AND set_id = ?", [log_id, set_id])
        return {"deleted": set_id}
    return _with_log(log_id, run)
