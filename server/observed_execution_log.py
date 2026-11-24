"""Turn selected inferred executions into a reviewable local process-mining log.

A snapshot uses real retained event timestamps, not invented timings for
projected steps. It is a new independent dataset; the original history and
business-case identity are unchanged. No captured text or clipboard values
enter the import.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
from io import BytesIO, StringIO
import json
import re
from typing import Any

from . import workflow_evidence as evidence
from .procedural_memory import _surface_key, _coarse_human_action

FORMAT = "openworkgraph.observed-execution-log.v1"
MAX_RUNS = 12
MAX_EVENTS = 10_000
SOURCE_LIMIT = 25_000
_ID = re.compile(r"execution:[a-f0-9]{16}")
_AGENT_ACTIVITIES = {
    "run_started": "Run started", "run_finished": "Run finished",
    "tool_call": "Tool call", "model_call": "Model call",
    "handoff": "Handoff", "error": "Error",
    "human_approval_requested": "Approval requested",
    "human_approval_received": "Approval response observed",
}
_HUMAN_ACTIVITIES = {
    "edit": "Control focused", "change": "Control changed", "copy": "Copy",
    "paste": "Paste", "submit": "Submit", "click": "Click",
    "right_click": "Right click",
}
_MAPPING = {
    "case_column": "execution", "activity_column": "activity",
    "timestamp_column": "observed_at", "resource_column": "",
    "attribute_columns": ["evidence_ref", "actor_kind", "membership_basis"],
}


class SnapshotError(ValueError):
    pass


def _iso(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise SnapshotError("A timezone-qualified timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise SnapshotError("A timezone-qualified timestamp is required") from None


def _spec(spec: Any) -> tuple[list[str], str | None, str | None]:
    if not isinstance(spec, dict) or set(spec) - {"execution_ids", "since", "until"}:
        raise SnapshotError("Send exact execution IDs and optional evidence window")
    ids = spec.get("execution_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RUNS
            or not all(isinstance(i, str) and _ID.fullmatch(i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise SnapshotError("Select 1–12 distinct opaque execution IDs")
    since = _iso(spec["since"]) if spec.get("since") is not None else None
    until = _iso(spec["until"]) if spec.get("until") is not None else None
    if since and until and since >= until:
        raise SnapshotError("The end of the evidence window must follow the start")
    return ids, since, until


def _activity(event: dict[str, Any], actor: str) -> str | None:
    kind = str(event.get("event_type") or "")
    is_agent = event.get("source") == "agent" or kind.startswith("agent_")
    if actor == "agent":
        if not is_agent:
            return None
        meta = event.get("metadata")
        if not isinstance(meta, dict):
            return None
        return _AGENT_ACTIVITIES.get(str(meta.get("operation") or "").lower())
    if actor != "human" or is_agent:
        return None
    if kind in {"clipboard_copy", "clipboard_cut", "clipboard_paste"}:
        action = {"clipboard_copy": "Copy", "clipboard_cut": "Cut", "clipboard_paste": "Paste"}[kind]
    else:
        action = _HUMAN_ACTIVITIES.get(_coarse_human_action(event))
    if not action:
        return None
    # Known surfaces are fixed tokens; unknown names stay opaque. Never use
    # titles, control labels, URLs, source record IDs or user identities.
    surface = _surface_key(evidence._surface_for_event(event))
    return surface + " / " + action


def project(selected: list[dict[str, Any]], per_run_events: dict[str, list[dict[str, Any]]],
            scope: dict[str, Any]) -> tuple[list[list[str]], dict[str, Any]]:
    rows: list[list[str]] = []
    seen: set[str] = set()
    cases = []
    for run in selected:
        identifier = run.get("execution_id")
        actor = run.get("actor_kind")
        if actor not in {"human", "agent"}:
            raise SnapshotError("Unknown execution actor type")
        if actor == "human" and not any(run.get("_session_ids") or []):
            raise SnapshotError("A human execution has no retained capture session")
        if actor == "agent":
            parts = str(run.get("_run_material") or "").split("|", 2)
            if len(parts) != 3 or parts[0] != "agent" or not all(parts[1:]):
                raise SnapshotError("An agent execution has no retained native run identity")
        membership = "inferred_time_session" if actor == "human" else "native_agent_run"
        case_rows = []
        for event in per_run_events.get(identifier, []):
            activity = _activity(event, actor)
            if not activity:
                continue
            raw_id = event.get("event_id")
            if not isinstance(raw_id, str) or not raw_id:
                raise SnapshotError("A selected activity lacks a canonical evidence reference")
            ref = evidence._event_ref(raw_id)
            if ref in seen:
                raise SnapshotError("Selected runs share retained activity evidence; select non-overlapping runs")
            seen.add(ref)
            at = _iso(event.get("observed_at"))
            case_rows.append([identifier, activity, at, ref, actor, membership])
            if len(seen) > MAX_EVENTS:
                raise SnapshotError("Selected activities exceed the 10,000-event snapshot limit")
        if not case_rows:
            raise SnapshotError("A selected execution has no retained structural activities")
        # Stable storage order is not observed order within timestamp ties.
        case_rows.sort(key=lambda row: (row[2], row[3]))
        ties = Counter(row[2] for row in case_rows)
        cases.append({
            "execution_id": identifier, "actor_kind": actor,
            "events": len(case_rows),
            "timestamp_tie_groups": sum(value > 1 for value in ties.values()),
            "membership_basis": membership,
        })
        rows.extend(case_rows)
    fingerprint = hashlib.sha256(json.dumps(
        {"format": FORMAT, "scope": scope, "rows": rows},
        ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    activities = Counter(row[1] for row in rows)
    return rows, {
        "format": FORMAT, "snapshot_sha256": fingerprint,
        "scope": scope, "case_count": len(cases), "event_count": len(rows),
        "cases": cases, "activity_count": len(activities),
        "top_activities": [{"activity": key, "events": count}
                           for key, count in sorted(activities.items(), key=lambda x: (-x[1], x[0]))[:20]],
        "timestamp_tie_groups": sum(case["timestamp_tie_groups"] for case in cases),
        "inferred_execution_membership": True,
        "business_case_identity_verified": False,
        "business_outcomes_verified": False,
        "sensor_observations_not_deduplicated_actions": True,
        "capture_complete": False,
        "source_limit_reached": False,
    }


def prepare(spec: Any) -> tuple[list[list[str]], dict[str, Any]]:
    ids, since, until = _spec(spec)
    raw = evidence._load(limit=SOURCE_LIMIT, since=since, until=until)
    if len(raw) >= SOURCE_LIMIT:
        raise SnapshotError("Source limit reached; narrow the evidence window before creating a snapshot")
    selected = evidence._select_executions(
        evidence.derive_executions(raw), family_key="", execution_ids=ids, max_runs=len(ids)
    )
    scoped = {run["execution_id"]: evidence._events_for_execution(raw, run) for run in selected}
    return project(selected, scoped, {"execution_ids": ids, "since": since, "until": until})


def preview(spec: Any) -> dict[str, Any]:
    return prepare(spec)[1]


def create(spec: Any, *, expected_sha256: str, name: str) -> dict[str, Any]:
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise SnapshotError("Review the exact snapshot preview before importing")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 90:
        raise SnapshotError("Enter a snapshot name of 1–90 characters")
    rows, report = prepare(spec)
    if report["snapshot_sha256"] != expected_sha256:
        raise SnapshotError("Evidence changed since preview; review the new snapshot before importing")
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["execution", "activity", "observed_at", "evidence_ref", "actor_kind", "membership_basis"])
    writer.writerows(rows)

    from . import event_log_workspace as workspace
    from . import event_log_description as descriptions

    def remember(conn, log_id):
        conn.execute(descriptions._TABLE)
        text = (
            "Observed execution snapshot: one case per selected inferred execution, "
            "one event per retained structural sensor observation. Not verified business cases, "
            "outcomes or distinct human actions. Repeated sensor observations and timestamp ties "
            "may affect process metrics. Captured text and clipboard values are excluded. "
            "Independent saved copy; delete this event log separately. "
            f"Snapshot SHA-256: {report['snapshot_sha256']}."
        )
        conn.execute("INSERT INTO log_descriptions VALUES (?, ?, ?)",
                     [log_id, text, datetime.now(timezone.utc).replace(tzinfo=None)])

    stored = workspace.import_log(BytesIO(buffer.getvalue().encode("utf-8")),
                                  name="Observed executions - " + name.strip(), source_format="csv",
                                  mapping=_MAPPING, _transaction_hook=remember)
    return {**report, "log_id": stored["log_id"], "name": stored["name"],
            "stored_independent_snapshot": True}


__all__ = ["preview", "create", "project", "SnapshotError"]
