"""Persist approved cohort expressions per saved local event log.

Definitions contain only numeric expressions, never cohorts or case identifiers.
The dashboard session is enforced by the REST routes; every operation checks
the selected log under the workspace lock. Deleting a log removes its definitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import secrets
from typing import Any

from . import event_log_workspace as workspace
from . import event_log_cohort_metrics as cohort_metrics
from . import event_log_derived_metrics as derived

_ID = re.compile(r"^cm_[0-9a-f]{16}$")
MAX_SAVED = 50


def _validated(body: Any) -> tuple[str, dict]:
    if not isinstance(body, dict) or set(body) != {"name", "definition"}:
        raise workspace.EventLogWorkspaceError("name and definition are required")
    name = body["name"]
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 120) or name != name.strip() or any(ord(c) < 32 or c in "<>" for c in name):
        raise workspace.EventLogWorkspaceError("invalid cohort metric name")
    definition = body["definition"]
    try:
        derived.validate(definition, cohort_metrics.FIELDS)
        json.dumps(definition, allow_nan=False)
    except (derived.MetricDefinitionError, TypeError, ValueError) as exc:
        raise workspace.EventLogWorkspaceError("invalid cohort metric definition: " + str(exc)) from None
    return name, definition


def list_saved(log_id: str) -> dict:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute(
                "SELECT metric_id, created_at, name, definition FROM cohort_metric_definitions "
                "WHERE log_id = ? ORDER BY created_at, metric_id", [log_id]
            ).fetchall()
        finally:
            conn.close()
    return {"definitions": [{"metric_id": mid, "created_at": created.isoformat() + "Z",
                              "name": name, "definition": json.loads(raw)}
                             for mid, created, name, raw in rows], "max_saved": MAX_SAVED}


def save(log_id: str, body: Any) -> dict:
    name, definition = _validated(body)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute(
                "SELECT name FROM cohort_metric_definitions WHERE log_id = ?", [log_id]
            ).fetchall()
            if len(rows) >= MAX_SAVED:
                raise workspace.EventLogWorkspaceError("saved cohort metric limit reached")
            if any(existing == name for (existing,) in rows):
                raise workspace.EventLogWorkspaceError("duplicate saved cohort metric name")
            metric_id = "cm_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute(
                "INSERT INTO cohort_metric_definitions VALUES (?, ?, ?, ?, ?)",
                [log_id, metric_id, created, name, json.dumps(definition, sort_keys=True, allow_nan=False)]
            )
        finally:
            conn.close()
    return {"metric_id": metric_id, "created_at": created.isoformat() + "Z",
            "name": name, "definition": definition}


def delete(log_id: str, metric_id: str) -> dict:
    if not _ID.fullmatch(metric_id):
        raise workspace.EventLogWorkspaceError("unknown saved cohort metric")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            found = conn.execute(
                "SELECT count(*) FROM cohort_metric_definitions WHERE log_id = ? AND metric_id = ?",
                [log_id, metric_id]
            ).fetchone()[0]
            if not found:
                raise workspace.EventLogWorkspaceError("unknown saved cohort metric")
            conn.execute(
                "DELETE FROM cohort_metric_definitions WHERE log_id = ? AND metric_id = ?",
                [log_id, metric_id]
            )
        finally:
            conn.close()
    return {"deleted": True, "metric_id": metric_id}
