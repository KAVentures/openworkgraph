from __future__ import annotations

"""Additional evidence-backed Context Pulse findings.

Repeated workflow findings are explicitly derived from task inference and are
therefore marked ``needs_review``. Manual transfers and repeated agent failures
are structural aggregates. None of these findings is advice.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from statistics import median
from typing import Any

from .db import connect

_MIN_WORKFLOW_RUNS = 3
_MIN_TRANSFERS = 3
_MIN_FAILING_RUNS = 2
_MAX_EVIDENCE = 12


def _finding_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join((kind, *parts)).encode("utf-8")).hexdigest()[:16]
    return f"finding:{kind}:{digest}"


def _bounds(values: list[str]) -> tuple[str | None, str | None]:
    clean = sorted(value for value in values if value)
    return (clean[0], clean[-1]) if clean else (None, None)


def repeated_workflow_findings(raw_rows: list[dict[str, Any]], lookback_days: int) -> list[dict[str, Any]]:
    """Human task families observed at least three times with readable steps."""
    try:
        from .procedural_feedback import _human_runs
        runs = _human_runs(raw_rows)
    except Exception:
        # A derived convenience view must never break canonical Pulse evidence.
        return []

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        family = str(run.get("family_key") or "")
        if family:
            by_family[family].append(run)

    findings: list[dict[str, Any]] = []
    for family, family_runs in by_family.items():
        if len(family_runs) < _MIN_WORKFLOW_RUNS:
            continue
        durations = [
            float(run.get("duration_seconds") or 0.0)
            for run in family_runs
            if run.get("duration_seconds")
        ]
        completed = sum(1 for run in family_runs if run.get("outcome_status") == "observed_completion")
        first, last = _bounds([str(run.get("started_at") or "") for run in family_runs])
        steps = next((list(run.get("semantic_steps") or []) for run in family_runs if run.get("semantic_steps")), [])
        evidence: list[str] = []
        for run in family_runs:
            evidence.extend(str(value) for value in (run.get("evidence_refs") or []) if value)
        count = len(family_runs)
        findings.append({
            "finding_id": _finding_id("task_family", family),
            "finding_kind": "repeated_workflow",
            "family_key": family,
            "typical_steps": steps[:12],
            "occurrence_count": count,
            "completed_count": completed,
            "median_duration_seconds": round(median(durations), 1) if durations else None,
            "total_duration_seconds": round(sum(durations), 1) if durations else None,
            "active_days": len({str(run.get("started_at") or "")[:10] for run in family_runs if run.get("started_at")}),
            "first_observed_at": first,
            "last_observed_at": last,
            "evidence_event_ids": list(dict.fromkeys(evidence))[-_MAX_EVIDENCE:],
            "follow_up": {"tool": "how_did_similar_runs_go", "family_key": family},
            "lookback_days": lookback_days,
            "material_version": f"n{count}",
            "factual_aggregate": True,
            "task_inference_used": True,
            "needs_review": True,
            "advice": False,
        })
    return findings


def _frozen_context_rows(*, snapshot_context_max_id: int, snapshot_at: str, lookback_days: int) -> list[dict[str, Any]]:
    """Read the context-events table at the Pulse's own frozen context watermark."""
    try:
        end = datetime.fromisoformat(str(snapshot_at).replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    except Exception:
        return []
    since = (end - timedelta(days=lookback_days)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, event_id, observed_at, session_id, source, surface, action,
                   resource_title, resource_locator, target_label, metadata_json
            FROM context_events
            WHERE id <= ? AND observed_at >= ? AND observed_at <= ?
            ORDER BY observed_at ASC, id ASC
            """,
            (snapshot_context_max_id, since, end.isoformat()),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        result.append(item)
    return result


def manual_transfer_findings(
    *,
    snapshot_context_max_id: int,
    snapshot_at: str,
    lookback_days: int,
) -> list[dict[str, Any]]:
    """Linked copy/cut -> paste across surfaces, counted without clipboard contents."""
    try:
        from .work_profile import _canonical_surface, _transfer_patterns
        rows = _frozen_context_rows(
            snapshot_context_max_id=snapshot_context_max_id,
            snapshot_at=snapshot_at,
            lookback_days=lookback_days,
        )
        for row in rows:
            row["surface"] = _canonical_surface(row.get("surface") or "Unknown")
        patterns = _transfer_patterns(rows)
    except Exception:
        return []

    findings: list[dict[str, Any]] = []
    for item in patterns:
        count = int(item.get("count") or 0)
        if not item.get("cross_surface") or count < _MIN_TRANSFERS:
            continue
        source = str(item.get("source_surface") or "")
        destination = str(item.get("destination_surface") or "")
        findings.append({
            "finding_id": _finding_id("manual_transfer", source, destination),
            "finding_kind": "manual_transfer",
            "source_surface": source,
            "destination_surface": destination,
            "occurrence_count": count,
            "evidence_event_ids": [str(value) for value in (item.get("example_event_ids") or []) if value][:_MAX_EVIDENCE],
            "clipboard_contents_captured": False,
            "lookback_days": lookback_days,
            "material_version": f"n{count}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings


def agent_failure_findings(raw_rows: list[dict[str, Any]], lookback_days: int) -> list[dict[str, Any]]:
    """The same readable structural step failing in at least two agent runs."""
    try:
        from .agent_execution_traces import agent_execution_traces
        traces = agent_execution_traces(raw_rows, limit=100, max_events_per_execution=200).get("executions") or []
    except Exception:
        return []

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for trace in traces:
        agent = str((trace.get("agent") or {}).get("name") or "agent")
        failing = {
            step.rsplit(":", 1)[0]
            for step in (trace.get("structural_steps") or [])
            if isinstance(step, str)
            and step.endswith((":error", ":failed", ":denied", ":cancelled", ":timeout"))
        }
        for step in failing:
            slot = grouped.setdefault((agent, step), {"executions": [], "times": []})
            slot["executions"].append(str(trace.get("execution_id") or ""))
            slot["times"].append(str(trace.get("started_at") or ""))

    findings: list[dict[str, Any]] = []
    for (agent, step), value in grouped.items():
        runs = [value for value in dict.fromkeys(value["executions"]) if value]
        if len(runs) < _MIN_FAILING_RUNS:
            continue
        first, last = _bounds(value["times"])
        findings.append({
            "finding_id": _finding_id("agent_failure", agent, step),
            "finding_kind": "agent_repeated_failure",
            "agent_name": agent,
            "failing_step": step,
            "failing_run_count": len(runs),
            "example_execution_ids": runs[:5],
            "first_observed_at": first,
            "last_observed_at": last,
            "follow_up": {"tool": "get_agent_runs", "execution_id": runs[0]},
            "lookback_days": lookback_days,
            "material_version": f"n{len(runs)}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings


__all__ = ["repeated_workflow_findings", "manual_transfer_findings", "agent_failure_findings"]
