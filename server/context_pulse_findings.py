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
    """Tolerant candidate workflows observed at least three times with readable steps."""
    try:
        from .procedural_feedback import _human_runs
        from .workflow_candidates import cluster_runs
        runs = _human_runs(raw_rows)
        candidates = cluster_runs(
            runs,
            min_runs=_MIN_WORKFLOW_RUNS,
            structural_key="structural_steps",
            readable_key="semantic_steps",
            limit=100,
        )
    except Exception:
        # A derived convenience view must never break canonical Pulse evidence.
        return []

    by_id = {
        str(run.get("execution_id") or ""): run
        for run in runs
        if str(run.get("execution_id") or "")
    }
    findings: list[dict[str, Any]] = []
    for candidate in candidates:
        selected = [
            by_id[value]
            for value in candidate.get("execution_ids") or []
            if value in by_id
        ]
        count = len(selected)
        if count < _MIN_WORKFLOW_RUNS:
            continue
        durations = [
            float(run.get("duration_seconds") or 0.0)
            for run in selected
            if run.get("duration_seconds")
        ]
        completed = sum(
            1 for run in selected
            if run.get("outcome_status") == "observed_completion"
        )
        evidence: list[str] = []
        for run in selected:
            evidence.extend(
                str(value) for value in (run.get("evidence_refs") or []) if value
            )
        cluster_id = str(candidate.get("candidate_cluster_id") or "")
        findings.append({
            "finding_id": _finding_id("task_candidate", cluster_id),
            "finding_kind": "repeated_workflow",
            "candidate_cluster_id": cluster_id,
            "coarse_family_keys": list(candidate.get("coarse_family_keys") or []),
            "typical_steps": list(candidate.get("core_steps") or [])[:12],
            "observed_variations": list(candidate.get("observed_variations") or [])[:12],
            "exact_variant_count": int(candidate.get("exact_variant_count") or 0),
            "occurrence_count": count,
            "completed_count": completed,
            "median_duration_seconds": round(median(durations), 1) if durations else None,
            "total_duration_seconds": round(sum(durations), 1) if durations else None,
            "active_days": len({
                str(run.get("started_at") or "")[:10]
                for run in selected if run.get("started_at")
            }),
            "first_observed_at": candidate.get("first_observed_at"),
            "last_observed_at": candidate.get("last_observed_at"),
            "evidence_event_ids": list(dict.fromkeys(evidence))[-_MAX_EVIDENCE:],
            "execution_ids": list(candidate.get("execution_ids") or [])[:12],
            "follow_up": {
                "tool": "get_workflow_evidence",
                "execution_ids": ",".join(candidate.get("execution_ids") or []),
            },
            "lookback_days": lookback_days,
            "material_version": f"n{count}",
            "factual_aggregate": True,
            "task_inference_used": True,
            "needs_review": True,
            "candidate_cluster_is_ground_truth": False,
            "canonical_evidence_overrides_cluster": True,
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
    """Linked cross-surface transfers, aggregated by destination without payloads."""
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

    grouped: dict[str, dict[str, Any]] = {}
    for item in patterns:
        if not item.get("cross_surface"):
            continue
        source = str(item.get("source_surface") or "")
        destination = str(item.get("destination_surface") or "")
        if not destination:
            continue
        slot = grouped.setdefault(destination, {
            "count": 0,
            "sources": {},
            "evidence_event_ids": [],
        })
        count = int(item.get("count") or 0)
        slot["count"] += count
        slot["sources"][source or "Unknown"] = (
            int(slot["sources"].get(source or "Unknown") or 0) + count
        )
        slot["evidence_event_ids"].extend(
            str(value) for value in (item.get("example_event_ids") or []) if value
        )

    findings: list[dict[str, Any]] = []
    for destination, slot in grouped.items():
        count = int(slot["count"])
        if count < _MIN_TRANSFERS:
            continue
        sources = [
            {"source_surface": source, "occurrence_count": source_count}
            for source, source_count in sorted(
                slot["sources"].items(),
                key=lambda pair: (-int(pair[1]), str(pair[0])),
            )
        ]
        findings.append({
            "finding_id": _finding_id("manual_transfer_destination", destination),
            "finding_kind": "manual_transfer",
            "source_surface": sources[0]["source_surface"] if len(sources) == 1 else "Multiple sources",
            "destination_surface": destination,
            "source_breakdown": sources,
            "occurrence_count": count,
            "evidence_event_ids": list(dict.fromkeys(slot["evidence_event_ids"]))[:_MAX_EVIDENCE],
            "clipboard_contents_captured": False,
            "grouping_basis": "destination_surface",
            "task_identity_inferred": False,
            "interpretation": (
                "linked copy/cut→paste occurrences aggregated by destination; "
                "source breakdown is preserved and this does not assert one workflow"
            ),
            "lookback_days": lookback_days,
            "material_version": f"n{count}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings

def agent_failure_findings(raw_rows: list[dict[str, Any]], lookback_days: int) -> list[dict[str, Any]]:
    """Separate repeated recovered tool failures from explicit failed runs."""
    try:
        from .agent_execution_traces import agent_execution_traces
        from .procedural_memory import derive_executions
        traces = agent_execution_traces(
            raw_rows, limit=100, max_events_per_execution=200
        ).get("executions") or []
        execution_index = {
            str(item.get("execution_id") or ""): item
            for item in derive_executions(raw_rows)
            if item.get("actor_kind") == "agent"
        }
    except Exception:
        return []

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trace in traces:
        execution_id = str(trace.get("execution_id") or "")
        derived = execution_index.get(execution_id) or {}
        agent = str((trace.get("agent") or {}).get("name") or "agent")
        failing = {
            step.rsplit(":", 1)[0]
            for step in (trace.get("structural_steps") or [])
            if isinstance(step, str)
            and step.endswith((":error", ":failed", ":denied", ":cancelled", ":timeout"))
        }
        if not failing:
            continue

        terminal_failure = str(derived.get("outcome_status") or "") in {
            "error", "denied", "cancelled"
        }
        recovered = bool(derived.get("recovered_failure_observed"))
        classification = (
            "terminal_failure" if terminal_failure
            else "recovered" if recovered
            else "intermediate_unresolved"
        )
        for step in failing:
            slot = grouped.setdefault(
                (classification, agent, step),
                {"executions": [], "times": []},
            )
            slot["executions"].append(execution_id)
            slot["times"].append(str(trace.get("started_at") or ""))

    findings: list[dict[str, Any]] = []
    for (classification, agent, step), value in grouped.items():
        runs = [item for item in dict.fromkeys(value["executions"]) if item]
        if len(runs) < _MIN_FAILING_RUNS:
            continue
        first, last = _bounds(value["times"])
        if classification == "recovered":
            finding_kind = "agent_recovered_failure"
            status_text = "tool failure observed, followed by a later successful tool call"
        elif classification == "terminal_failure":
            finding_kind = "agent_repeated_failure"
            status_text = "explicit failed run terminal status observed"
        else:
            finding_kind = "agent_intermediate_failure"
            status_text = "tool failure observed without terminal run status or observed recovery"
        findings.append({
            "finding_id": _finding_id("agent_failure", classification, agent, step),
            "finding_kind": finding_kind,
            "agent_name": agent,
            "failing_step": step,
            "run_count": len(runs),
            "failing_run_count": len(runs) if classification == "terminal_failure" else 0,
            "recovered_run_count": len(runs) if classification == "recovered" else 0,
            "example_execution_ids": runs[:5],
            "first_observed_at": first,
            "last_observed_at": last,
            "run_outcome_inferred_from_final_tool": False,
            "status_interpretation": status_text,
            "follow_up": {"tool": "get_agent_runs", "execution_id": runs[0]},
            "lookback_days": lookback_days,
            "material_version": f"n{len(runs)}",
            "factual_aggregate": True,
            "task_inference_used": False,
            "advice": False,
        })
    return findings


__all__ = ["repeated_workflow_findings", "manual_transfer_findings", "agent_failure_findings"]
