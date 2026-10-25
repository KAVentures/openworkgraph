"""Saved, reusable case-filter sets for a log (#370 M5, Disco-style saved filters).

A filter set is a named case filter (the same cohort keys every analysis view
accepts, including follower rules) stored next to the log. Applying it in the
dashboard sends the same parameters to every view, so the same cases are
selected everywhere. Sets are re-evaluated against the log as it is now: a set
saved before newer exports were appended can match different cases, and a set
naming activities the log no longer contains is listed with those activities.
Saving or applying a set never changes events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-filter-set.v1"
MAX_SETS = 50


def _name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not 0 < len(name) <= 120:
        raise workspace.EventLogWorkspaceError("a filter set needs a name of 1–120 characters")
    return name


def _activities(cohort: dict[str, Any]) -> set[str]:
    named = {r[k] for r in cohort.get("follows", []) for k in ("from", "to")}
    return named | ({cohort["activity"]} if cohort.get("activity") else set())


def _batches(conn, log_id: str) -> int:
    return conn.execute("SELECT count(*) FROM log_appends WHERE log_id = ?", [log_id]).fetchone()[0]


def list_sets(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT set_id, created_at, definition FROM filter_sets WHERE log_id = ? "
                                "ORDER BY created_at, set_id", [log_id]).fetchall()
            known = {r[0] for r in conn.execute("SELECT DISTINCT activity FROM events WHERE log_id = ?",
                                                [log_id]).fetchall()}
            batches = _batches(conn, log_id)
        finally:
            conn.close()
    sets = []
    for set_id, created, definition in rows:
        saved = json.loads(definition)
        cohort = cohorts.parse_cohort(saved["cohort"])
        sets.append({"set_id": set_id, "created_at": created.isoformat() + "Z", "name": saved["name"],
                     "cohort": saved["cohort"],
                     "activities_not_in_log": sorted(_activities(cohort) - known),
                     "log_appended_since_saved": batches > saved["appended_batches_at_save"]})
    return {"filter_sets": sets, "max_sets": MAX_SETS, "format": FORMAT,
            "interpretation": ("A filter set is re-applied to the log as it is now. Batches appended after it was "
                               "saved can change which cases it selects.")}


def save(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"name", "cohort"}:
        raise workspace.EventLogWorkspaceError("a filter set accepts only name and cohort")
    name = _name(body.get("name"))
    cohort = cohorts.parse_cohort(body.get("cohort"))
    cohort.pop("label", None)
    if not cohort:
        raise workspace.EventLogWorkspaceError("a filter set needs at least one filter")
    described = cohorts.describe(cohort)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            existing = [json.loads(d)["name"] for (d,) in conn.execute(
                "SELECT definition FROM filter_sets WHERE log_id = ?", [log_id]).fetchall()]
            if len(existing) >= MAX_SETS:
                raise workspace.EventLogWorkspaceError(f"a log can hold at most {MAX_SETS} filter sets")
            if name.casefold() in {n.casefold() for n in existing}:
                raise workspace.EventLogWorkspaceError("a filter set with this name already exists")
            set_id = "fs_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            definition = {"format": FORMAT, "name": name, "cohort": described,
                          "appended_batches_at_save": _batches(conn, log_id)}
            conn.execute("INSERT INTO filter_sets VALUES (?, ?, ?, ?)",
                         [log_id, set_id, created, json.dumps(definition, sort_keys=True)])
        finally:
            conn.close()
    return {"set_id": set_id, "created_at": created.isoformat() + "Z", "name": name, "cohort": described}


def delete(log_id: str, set_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if not conn.execute("SELECT count(*) FROM filter_sets WHERE log_id = ? AND set_id = ?",
                                [log_id, str(set_id)]).fetchone()[0]:
                raise workspace.EventLogWorkspaceError("unknown filter set")
            conn.execute("DELETE FROM filter_sets WHERE log_id = ? AND set_id = ?", [log_id, str(set_id)])
        finally:
            conn.close()
    return {"deleted": True, "set_id": set_id}


__all__ = ["delete", "list_sets", "save", "FORMAT", "MAX_SETS"]
