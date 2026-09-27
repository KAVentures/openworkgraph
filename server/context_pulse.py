from __future__ import annotations

"""Incremental, evidence-backed context updates for AI clients.

Context Pulse answers two factual questions without becoming an advice engine:

1. What canonical evidence arrived since this caller last checked?
2. Which long-horizon factual aggregates are new or materially changed?

The cursor intentionally contains no titles, names, surfaces or other captured text.
It keeps only monotonic database watermarks and hashes/versions of findings.
"""

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
from .context_layers import factual_context_timeline
from .db import connect

_CURSOR_VERSION = 1
_MAX_CURSOR_BYTES = 32_768
_MAX_TRACKED_FINDINGS = 50
_MAX_FINDING_SOURCE_EVENTS = 100_000


def _encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        padded = str(value) + "=" * (-len(str(value)) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) > _MAX_CURSOR_BYTES:
            raise ValueError("Context Pulse cursor is too large")
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid Context Pulse cursor") from exc
    if not isinstance(data, dict) or int(data.get("v") or 0) != _CURSOR_VERSION:
        raise ValueError("Invalid Context Pulse cursor version")
    return data


def _event_dict(row: Any) -> dict[str, Any]:
    value = dict(row)
    raw = value.get("metadata_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            value["metadata"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            value["metadata"] = {}
    return value


def _finding_id(kind: str, *parts: str) -> str:
    material = "\x1f".join([kind, *(str(part).strip().casefold() for part in parts)])
    return f"finding:{kind}:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _parse_ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise ValueError("Invalid Context Pulse snapshot time") from exc


def _source_rows(
    *,
    snapshot_max_id: int,
    snapshot_at: str,
    lookback_days: int,
) -> tuple[list[dict[str, Any]], bool]:
    since = (_parse_datetime(snapshot_at).astimezone(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE id <= ? AND observed_at >= ? "
            "ORDER BY observed_at ASC, id ASC LIMIT ?",
            (snapshot_max_id, since, _MAX_FINDING_SOURCE_EVENTS + 1),
        ).fetchall()
    truncated = len(rows) > _MAX_FINDING_SOURCE_EVENTS
    return [_event_dict(row) for row in rows[:_MAX_FINDING_SOURCE_EVENTS]], truncated


def _surface_findings(timeline: list[dict[str, Any]], lookback_days: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in timeline:
        surface = str(row.get("work_surface") or row.get("container_app") or "").strip()
        if not surface:
            continue
        bucket = grouped.setdefault(surface, {
            "engaged_seconds": 0.0,
            "foreground_seconds": 0.0,
            "span_count": 0,
            "days": set(),
            "first_observed_at": None,
            "last_observed_at": None,
            "evidence_event_ids": [],
        })
        bucket["engaged_seconds"] += float(row.get("engaged_seconds") or 0.0)
        bucket["foreground_seconds"] += float(row.get("foreground_seconds") or 0.0)
        bucket["span_count"] += 1
        started = str(row.get("started_at") or "")
        if started:
            bucket["days"].add(started[:10])
            if bucket["first_observed_at"] is None or started < bucket["first_observed_at"]:
                bucket["first_observed_at"] = started
            if bucket["last_observed_at"] is None or started > bucket["last_observed_at"]:
                bucket["last_observed_at"] = started
        bucket["evidence_event_ids"].extend(str(x) for x in (row.get("evidence_event_ids") or []) if x)

    findings: list[dict[str, Any]] = []
    for surface, value in grouped.items():
        engaged = round(float(value["engaged_seconds"]), 3)
        active_days = len(value["days"])
        # Avoid turning every briefly opened app into a long-horizon finding.
        if active_days < 2 and engaged < 1800:
            continue
        findings.append({
            "finding_id": _finding_id("surface_engagement", surface),
            "finding_kind": "surface_engagement",
            "surface": surface,
            "engaged_seconds": engaged,
            "foreground_seconds": round(float(value["foreground_seconds"]), 3),
            "span_count": int(value["span_count"]),
            "active_days": active_days,
            "first_observed_at": value["first_observed_at"],
            "last_observed_at": value["last_observed_at"],
            "evidence_event_ids": list(dict.fromkeys(value["evidence_event_ids"]))[-12:],
            "lookback_days": lookback_days,
            # Engagement changes are material every 15 engaged minutes, or when a
            # new active day appears. Tiny focus/heartbeat changes do not spam AI.
            "material_version": f"d{active_days}:q{int(engaged // 900)}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings


def _transition_findings(timeline: list[dict[str, Any]], lookback_days: int) -> list[dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in timeline:
        by_session[str(row.get("session_id") or "")].append(row)

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in by_session.values():
        rows.sort(key=lambda row: str(row.get("started_at") or ""))
        for left, right in zip(rows, rows[1:]):
            source = str(left.get("work_surface") or left.get("container_app") or "").strip()
            destination = str(right.get("work_surface") or right.get("container_app") or "").strip()
            if not source or not destination or source == destination:
                continue
            left_end = _parse_ts(left.get("ended_at"))
            right_start = _parse_ts(right.get("started_at"))
            if left_end is None or right_start is None:
                continue
            gap = right_start - left_end
            if gap < -1.0 or gap > 300.0:
                continue
            key = (source, destination)
            value = grouped.setdefault(key, {
                "count": 0,
                "first_observed_at": None,
                "last_observed_at": None,
                "evidence_event_ids": [],
            })
            value["count"] += 1
            observed = str(right.get("started_at") or "")
            if value["first_observed_at"] is None or observed < value["first_observed_at"]:
                value["first_observed_at"] = observed
            if value["last_observed_at"] is None or observed > value["last_observed_at"]:
                value["last_observed_at"] = observed
            value["evidence_event_ids"].extend(str(x) for x in (left.get("evidence_event_ids") or []) if x)
            value["evidence_event_ids"].extend(str(x) for x in (right.get("evidence_event_ids") or []) if x)

    findings: list[dict[str, Any]] = []
    for (source, destination), value in grouped.items():
        count = int(value["count"])
        if count < 3:
            continue
        findings.append({
            "finding_id": _finding_id("surface_transition", source, destination),
            "finding_kind": "repeated_surface_transition",
            "source_surface": source,
            "destination_surface": destination,
            "occurrence_count": count,
            "first_observed_at": value["first_observed_at"],
            "last_observed_at": value["last_observed_at"],
            "evidence_event_ids": list(dict.fromkeys(value["evidence_event_ids"]))[-12:],
            "lookback_days": lookback_days,
            "material_version": f"n{count}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings


def _findings(
    *,
    snapshot_max_id: int,
    snapshot_at: str,
    lookback_days: int,
) -> tuple[list[dict[str, Any]], bool]:
    raw_rows, truncated = _source_rows(
        snapshot_max_id=snapshot_max_id,
        snapshot_at=snapshot_at,
        lookback_days=lookback_days,
    )
    timeline = factual_context_timeline(_raw_events=raw_rows)
    findings = [
        *_surface_findings(timeline, lookback_days),
        *_transition_findings(timeline, lookback_days),
    ]
    findings.sort(
        key=lambda item: (
            0 if item.get("finding_kind") == "repeated_surface_transition" else 1,
            -int(item.get("occurrence_count") or item.get("active_days") or 0),
            str(item.get("finding_id") or ""),
        )
    )
    return findings[:_MAX_TRACKED_FINDINGS], truncated or len(findings) > _MAX_TRACKED_FINDINGS


def context_pulse(
    *,
    cursor: str | None = None,
    recent_limit: int = 100,
    finding_limit: int = 20,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Return one incremental factual update and an opaque bookmark for the next check."""
    state = _decode_cursor(cursor)
    bootstrap = not bool(state)
    page_limit = max(1, min(int(recent_limit), 500))
    output_finding_limit = max(0, min(int(finding_limit), 50))

    # Once a cursor exists it owns the lookback contract. A caller can start a new
    # pulse (no cursor) to choose another lookback; it need not repeat the original
    # value on every subsequent tool call.
    if state:
        lookback_days = max(7, min(int(state.get("lookback_days") or 30), 90))
    else:
        lookback_days = max(7, min(int(lookback_days), 90))

    last_event_id = max(0, int(state.get("last_event_id") or 0)) if state else 0
    pending_snapshot = max(0, int(state.get("pending_snapshot_max_id") or 0)) if state else 0
    pending_snapshot_at = str(state.get("pending_snapshot_at") or "") if state else ""
    previous_versions = state.get("finding_versions") if state else {}
    if not isinstance(previous_versions, dict):
        raise ValueError("Invalid Context Pulse finding state")
    previous_versions = {
        str(key): str(value)
        for key, value in list(previous_versions.items())[:_MAX_TRACKED_FINDINGS]
    }
    baseline_pending = bool(state.get("baseline_pending")) if state else True

    with connect() as conn:
        current_max_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
    snapshot_max_id = pending_snapshot or current_max_id
    snapshot_at = pending_snapshot_at or datetime.now(timezone.utc).isoformat()

    if bootstrap:
        with connect() as conn:
            db_rows = conn.execute(
                "SELECT * FROM events WHERE id <= ? ORDER BY id DESC LIMIT ?",
                (snapshot_max_id, page_limit),
            ).fetchall()
        visible = list(reversed(db_rows))
        has_more = False
        recent_mode = "bootstrap_tail"
    else:
        with connect() as conn:
            db_rows = conn.execute(
                "SELECT * FROM events WHERE id > ? AND id <= ? ORDER BY id ASC LIMIT ?",
                (last_event_id, snapshot_max_id, page_limit + 1),
            ).fetchall()
        has_more = len(db_rows) > page_limit
        visible = db_rows[:page_limit]
        recent_mode = "since_last_pulse"

    recent_rows = [rich_evidence_row(dict(row), include_identity=False) for row in visible]
    findings, findings_truncated = _findings(
        snapshot_max_id=snapshot_max_id,
        snapshot_at=snapshot_at,
        lookback_days=lookback_days,
    )
    current_versions = {
        str(item["finding_id"]): str(item["material_version"])
        for item in findings
    }

    # Keep only still-current delivered state. Crucially, a finding does not enter
    # the cursor until it has actually been returned to the caller; a low
    # finding_limit therefore cannot silently mark unseen findings as delivered.
    next_versions = {
        finding_id: version
        for finding_id, version in previous_versions.items()
        if finding_id in current_versions
    }
    candidates: list[tuple[dict[str, Any], str]] = []
    if output_finding_limit > 0:
        for item in findings:
            finding_id = str(item["finding_id"])
            version = str(item["material_version"])
            previous = previous_versions.get(finding_id)
            if previous == version:
                continue
            status = "baseline" if baseline_pending and previous is None else ("new" if previous is None else "changed")
            candidates.append((item, status))

    selected = candidates[:output_finding_limit] if output_finding_limit > 0 else []
    changed: list[dict[str, Any]] = []
    for item, status in selected:
        out = {key: value for key, value in item.items() if key != "material_version"}
        out["status"] = status
        changed.append(out)
        next_versions[str(item["finding_id"])] = str(item["material_version"])

    findings_has_more = output_finding_limit > 0 and len(candidates) > len(selected)
    next_baseline_pending = bool(baseline_pending and findings_has_more)

    if bootstrap:
        next_last_event_id = snapshot_max_id
    elif has_more and visible:
        next_last_event_id = int(visible[-1]["id"])
    else:
        next_last_event_id = snapshot_max_id

    # Freeze the same snapshot while either the evidence page or finding page has
    # more to deliver. New arrivals wait for the next completed pulse.
    keep_snapshot = bool(has_more or findings_has_more)
    next_cursor = _encode_cursor({
        "v": _CURSOR_VERSION,
        "last_event_id": next_last_event_id,
        "pending_snapshot_max_id": snapshot_max_id if keep_snapshot else 0,
        "pending_snapshot_at": snapshot_at if keep_snapshot else "",
        "lookback_days": lookback_days,
        "baseline_pending": next_baseline_pending,
        "finding_versions": dict(list(next_versions.items())[:_MAX_TRACKED_FINDINGS]),
    })

    return {
        "bootstrap": bootstrap,
        "recent_mode": recent_mode,
        "recent_evidence": recent_rows,
        "recent_returned": len(recent_rows),
        "recent_has_more": has_more,
        "snapshot_max_event_watermark": snapshot_max_id,
        "snapshot_at": snapshot_at,
        "findings": changed,
        "findings_returned": len(changed),
        "findings_has_more": findings_has_more,
        "findings_lookback_days": lookback_days,
        "findings_source_truncated": findings_truncated,
        "next_cursor": next_cursor,
        "cursor_contract": "opaque_bookmark; pass next_cursor unchanged to the next get_context_pulse call",
        "canonical_evidence_tool": "get_workflow_trace",
        "recent_evidence_contract": dict(RAW_RICH_EVIDENCE_CONTRACT),
        "finding_contract": {
            "factual_aggregates_only": True,
            "task_inference_used": False,
            "recommendations_or_advice": False,
            "unchanged_findings_omitted_after_bootstrap": True,
            "material_change": "new support, new active day, or another 15 minutes of engaged time depending on finding kind",
        },
    }


__all__ = ["context_pulse"]
