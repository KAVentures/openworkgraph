from __future__ import annotations

"""Derived human↔agent correlations over canonical evidence.

These links are indexes, never ground truth. Explicit trigger IDs win. Temporal
matching is deliberately conservative and reports its method/confidence.
"""

from datetime import datetime
import hashlib
import re
from typing import Any

from .context_layers import agent_turns
from .db import rows


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _agent_meta(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    return meta, trace


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) >= 3 and token not in {"agent", "assistant", "work", "code"}
    }


def _surface_matches(agent_name: str, surface: str) -> bool:
    a = _tokens(agent_name)
    b = _tokens(surface)
    return bool(a and b and a.intersection(b))


def stitch_agent_workflows(
    raw_events: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 180.0,
) -> list[dict[str, Any]]:
    event_by_id = {str(e.get("event_id") or ""): e for e in raw_events if e.get("event_id")}
    turns = agent_turns(_raw_events=raw_events)
    turn_times = [(_ts(t.get("submitted_at")), t) for t in turns]
    turn_times = [(ts, t) for ts, t in turn_times if ts is not None]

    agent_events = [
        e for e in raw_events
        if str(e.get("source") or "") == "agent" or str(e.get("event_type") or "").startswith("agent_")
    ]
    runs: dict[str, list[dict[str, Any]]] = {}
    for event in agent_events:
        meta, trace = _agent_meta(event)
        run_id = str(trace.get("run_id") or trace.get("trace_id") or event.get("session_id") or "").strip()
        if not run_id:
            continue
        runs.setdefault(run_id, []).append(event)

    output: list[dict[str, Any]] = []
    for run_id, events in runs.items():
        events.sort(key=lambda e: str(e.get("observed_at") or ""))
        starts = [e for e in events if str(e.get("event_type") or "") == "agent_run_started"]
        finishes = [e for e in events if str(e.get("event_type") or "") == "agent_run_finished"]
        first = starts[0] if starts else events[0]
        last = finishes[-1] if finishes else events[-1]
        meta, trace = _agent_meta(first)
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        agent_name = str(agent.get("name") or first.get("app") or "Agent")
        started_ts = _ts(first.get("observed_at"))

        trigger_id = str(trace.get("trigger_event_id") or "").strip()
        trigger_turn: dict[str, Any] | None = None
        method = "unlinked"
        confidence = 0.0

        if trigger_id and trigger_id in event_by_id:
            trigger_event = event_by_id[trigger_id]
            trigger_turn = {
                "evidence_event_id": trigger_id,
                "submitted_at": trigger_event.get("observed_at"),
                "surface": trigger_event.get("app") or "",
            }
            method = "explicit_trigger_event"
            confidence = 1.0
        elif started_ts is not None:
            candidates: list[tuple[float, dict[str, Any], bool]] = []
            for turn_ts, turn in turn_times:
                gap = started_ts - turn_ts
                if gap < -1.0 or gap > max_gap_seconds:
                    continue
                surface_match = _surface_matches(agent_name, str(turn.get("surface") or ""))
                candidates.append((gap, turn, surface_match))
            if candidates:
                surface_candidates = [x for x in candidates if x[2]]
                chosen = min(surface_candidates or candidates, key=lambda x: x[0])
                gap, trigger_turn, matched = chosen
                if matched:
                    method = "temporal_surface_match"
                    confidence = 0.85 if gap <= 30 else 0.7
                elif gap <= 15:
                    method = "temporal_nearest"
                    confidence = 0.5
                else:
                    trigger_turn = None

        finish_meta, finish_trace = _agent_meta(last)
        workflow_id = str(trace.get("workflow_id") or finish_trace.get("workflow_id") or "").strip()
        finish_status = str(finish_meta.get("status") or "unknown")
        observation_level = str(meta.get("observation_level") or "unknown")
        link_id = hashlib.sha256(
            f"{run_id}|{trigger_turn.get('evidence_event_id') if trigger_turn else ''}|{first.get('event_id')}".encode()
        ).hexdigest()[:16]

        output.append({
            "workflow_link_id": link_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "trace_id": trace.get("trace_id") or "",
            "agent_actor_id": first.get("actor_id") or "",
            "agent_name": agent_name,
            "agent_started_at": first.get("observed_at"),
            "agent_finished_at": last.get("observed_at"),
            "agent_finish_status": finish_status,
            "observation_level": observation_level,
            "human_trigger_event_id": trigger_turn.get("evidence_event_id") if trigger_turn else "",
            "human_trigger_at": trigger_turn.get("submitted_at") if trigger_turn else None,
            "human_trigger_surface": trigger_turn.get("surface") if trigger_turn else "",
            "link_method": method,
            "link_confidence": confidence,
            "derived": True,
            "authoritative": False,
        })

    output.sort(key=lambda x: str(x.get("agent_started_at") or ""))
    return output


def agent_workflow_view(*, limit: int = 5000, since: str | None = None) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), 20_000))
    if since:
        raw = rows(
            "SELECT * FROM events WHERE observed_at >= ? ORDER BY observed_at ASC LIMIT ?",
            (since, page_limit),
        )
    else:
        raw = rows("SELECT * FROM events ORDER BY observed_at DESC LIMIT ?", (page_limit,))
        raw.reverse()
    links = stitch_agent_workflows(raw)
    return {
        "items": links,
        "returned": len(links),
        "evidence_rows_considered": len(raw),
        "derived_task_inference_authoritative": False,
    }
