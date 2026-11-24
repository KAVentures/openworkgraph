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
import hashlib
import json
import re
import secrets
from typing import Any

from . import event_log_activity_mappings as mappings
from . import event_log_cohorts as cohorts
from . import event_log_hidden_activities as hidden
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-filter-set.v1"
MAX_SETS = 50


def _name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not 0 < len(name) <= 120:
        raise workspace.EventLogWorkspaceError("a filter set needs a name of 1–120 characters")
    return name


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _activities(cohort: dict[str, Any]) -> set[str]:
    named = {r[k] for r in cohort.get("follows", []) for k in ("from", "to")}
    named.update(activity for variant in cohort.get("variants", []) for activity in variant)
    named.update(cohort[key] for key in ("activity", "without_activity", "starts_with", "ends_with")
                 if cohort.get(key))
    return named


def _batches(conn, log_id: str) -> int:
    return conn.execute("SELECT count(*) FROM log_appends WHERE log_id = ?", [log_id]).fetchone()[0]


def _known_labels(conn, log_id: str, labels: set[str], notions: set[str]) -> dict[str, set[str]]:
    mapping = mappings.applied_mapping(conn, log_id)
    relabel = {raw: group["label"] for group in (mapping or {}).get("groups", [])
               for raw in group["activities"]}
    candidates = {raw for raw, visible in relabel.items() if visible in labels}
    candidates.update(label for label in labels if label not in relabel)
    candidates.difference_update(hidden.hidden_names(conn, log_id))
    known: dict[str, set[str]] = {notion: set() for notion in notions}
    if candidates:
        # Collapse can remove repeated rows, but always leaves a first event
        # for a visible label. Presence therefore needs no full trace scan.
        rows = conn.execute("""
            SELECT DISTINCT o.object_type, e.activity
            FROM events e JOIN event_objects eo ON eo.log_id = e.log_id AND eo.event_id = e.event_id
            JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
            WHERE e.log_id = ? AND e.activity IN (SELECT unnest(?::VARCHAR[]))
              AND o.object_type IN (SELECT unnest(?::VARCHAR[]))
        """, [log_id, sorted(candidates), sorted(notions)]).fetchall()
        for notion, raw in rows:
            known[notion].add(relabel.get(raw, raw))
    return known


def list_sets(log_id: str, case_type: str | None = None) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT set_id, created_at, definition FROM filter_sets WHERE log_id = ? "
                                "ORDER BY created_at, set_id", [log_id]).fetchall()
            sets = []
            if rows:
                current = workspace._case_type(conn, log_id, case_type)
                entries = [(set_id, created, json.loads(definition), _fingerprint(definition))
                           for set_id, created, definition in rows]
                labels_by_id = {set_id: _activities(cohorts.parse_cohort(saved["cohort"]))
                                for set_id, _, saved, _ in entries}
                notions = {saved.get("case_type") or current for _, _, saved, _ in entries}
                labels = set().union(*labels_by_id.values())
                known = _known_labels(conn, log_id, labels, notions) if labels else {}
                batches = _batches(conn, log_id)
                for set_id, created, saved, fingerprint in entries:
                    notion = saved.get("case_type") or current
                    sets.append({"set_id": set_id, "created_at": created.isoformat() + "Z", "name": saved["name"],
                                 "definition_sha256": fingerprint,
                                 "cohort": saved["cohort"],
                                 **({"case_type": saved["case_type"]} if "case_type" in saved else {}),
                                 "activities_not_in_log": sorted(labels_by_id[set_id] - known.get(notion, set())),
                                 "log_appended_since_saved": batches > saved["appended_batches_at_save"]})
        finally:
            conn.close()
    return {"filter_sets": sets, "max_sets": MAX_SETS, "format": FORMAT,
            "interpretation": ("A filter set is re-applied to the log as it is now. Batches appended after it was "
                               "saved can change which cases it selects.")}


def save(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"name", "cohort", "case_type"}:
        raise workspace.EventLogWorkspaceError("a filter set accepts name, cohort and case type")
    name = _name(body.get("name"))
    case_type = body.get("case_type")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise workspace.EventLogWorkspaceError("choose a valid case notion")
    cohort = cohorts.parse_cohort(body.get("cohort"))
    cohort.pop("label", None)
    if not cohort:
        raise workspace.EventLogWorkspaceError("a filter set needs at least one filter")
    described = cohorts.describe(cohort)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            if case_type is not None:
                case_type = workspace._case_type(conn, log_id, case_type)
            existing = [json.loads(d)["name"] for (d,) in conn.execute(
                "SELECT definition FROM filter_sets WHERE log_id = ?", [log_id]).fetchall()]
            if len(existing) >= MAX_SETS:
                raise workspace.EventLogWorkspaceError(f"a log can hold at most {MAX_SETS} filter sets")
            if name.casefold() in {n.casefold() for n in existing}:
                raise workspace.EventLogWorkspaceError("a filter set with this name already exists")
            set_id = "fs_" + secrets.token_hex(8)
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            definition = {"format": FORMAT, "name": name, "cohort": described,
                          **({"case_type": case_type} if case_type is not None else {}),
                          "appended_batches_at_save": _batches(conn, log_id)}
            conn.execute("INSERT INTO filter_sets VALUES (?, ?, ?, ?)",
                         [log_id, set_id, created, json.dumps(definition, sort_keys=True)])
        finally:
            conn.close()
    return {"set_id": set_id, "created_at": created.isoformat() + "Z", "name": name, "cohort": described,
            **({"case_type": case_type} if case_type is not None else {})}


def update(log_id: str, set_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"name", "cohort", "case_type", "expected_definition_sha256"}:
        raise workspace.EventLogWorkspaceError("provide name, cohort, case type and expected saved definition")
    expected = body["expected_definition_sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise workspace.EventLogWorkspaceError("invalid saved filter fingerprint")
    name = _name(body["name"])
    requested = body["case_type"]
    if requested is not None and (not isinstance(requested, str) or not 0 < len(requested) <= 200):
        raise workspace.EventLogWorkspaceError("choose a valid case notion")
    cohort = cohorts.parse_cohort(body["cohort"])
    cohort.pop("label", None)
    if not cohort:
        raise workspace.EventLogWorkspaceError("a filter set needs at least one filter")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            row = conn.execute("SELECT definition FROM filter_sets WHERE log_id=? AND set_id=?",
                               [log_id, set_id]).fetchone()
            if row is None:
                raise workspace.EventLogWorkspaceError("unknown filter set")
            if _fingerprint(row[0]) != expected:
                raise workspace.EventLogWorkspaceError("the saved filter changed; reload it before updating")
            if requested is not None:
                requested = workspace._case_type(conn, log_id, requested)
            others = conn.execute("SELECT definition FROM filter_sets WHERE log_id=? AND set_id<>?",
                                  [log_id, set_id]).fetchall()
            if name.casefold() in {json.loads(value)["name"].casefold() for (value,) in others}:
                raise workspace.EventLogWorkspaceError("a filter set with this name already exists")
            definition = {"format": FORMAT, "name": name, "cohort": cohorts.describe(cohort),
                          **({"case_type": requested} if requested is not None else {}),
                          "appended_batches_at_save": _batches(conn, log_id)}
            encoded = json.dumps(definition, sort_keys=True)
            conn.execute("UPDATE filter_sets SET definition=? WHERE log_id=? AND set_id=?",
                         [encoded, log_id, set_id])
        finally:
            conn.close()
    return {"set_id": set_id, "name": name, "cohort": definition["cohort"],
            **({"case_type": requested} if requested is not None else {}),
            "definition_sha256": _fingerprint(encoded)}


def import_set(log_id: str, document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise workspace.EventLogWorkspaceError("choose an OpenWorkGraph filter JSON file")
    return save(log_id, {key: value for key, value in document.items() if key != "format"})


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


__all__ = ["delete", "import_set", "list_sets", "save", "FORMAT", "MAX_SETS"]
