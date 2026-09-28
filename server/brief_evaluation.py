from __future__ import annotations

"""Do session briefs help? A randomized comparison, computed locally.

With evaluation on, ``agent_brief.deliver`` holds the brief back from a random
1 in 5 sessions that would have received one ("control"), sticky per session
*and per trial*. This module compares only sessions assigned inside the selected
trial; ordinary briefs delivered before evaluation are never treated as a
randomized arm.

Outcomes are observed after assignment:

* ``test_fail_rate``: share of test-running turns whose tests ended failing (lower is better)
* ``rework_rate``: share of turns after which the person changed files the agent had just edited (lower is better)
* ``pr_merge_rate``: share of resolved pull requests that merged (higher is better; open/pending PRs are excluded)
* ``tokens_per_turn``: median tokens per turn (lower is better)
* ``turns``: turns in the session (reported, no better/worse direction)

The session is the unit. Each arm's mean is compared with a bootstrap 95%
interval for the difference (briefed minus control; 2,000 resamples, fixed seed).
Nothing is claimed until both arms have at least ``MIN_SESSIONS`` sessions with
the metric. This is exploratory: several metrics, no multiple-comparison
correction, and "no clear difference" is the honest default.
"""

import json
import random
import statistics
from collections import defaultdict
from typing import Any

from .db import connect

MIN_SESSIONS = 10
RESAMPLES = 2000
METRICS = {
    "test_fail_rate": "lower",
    "rework_rate": "lower",
    "pr_merge_rate": "higher",
    "tokens_per_turn": "lower",
    "turns": None,
}


def _sessions() -> dict[str, dict[str, Any]]:
    from .agent_brief import _ensure_trial_table, evaluation_state

    state = evaluation_state()
    trial_id = str(state.get("trial_id") or "")
    if not trial_id:
        return {}
    with connect() as conn:
        _ensure_trial_table(conn)
        rows = conn.execute(
            "SELECT session_ref, arm, assigned_at FROM agent_brief_trial_assignment WHERE trial_id = ? ORDER BY assigned_at",
            (trial_id,),
        ).fetchall()
    sessions: dict[str, dict[str, Any]] = {}
    for session_ref, arm, assigned_at in rows:
        if session_ref and arm in {"brief", "control"}:
            sessions[str(session_ref)] = {"arm": str(arm), "delivered_at": str(assigned_at), "trial_id": trial_id}
    return sessions


def _runs_by_session(session_refs: set[str], since: str) -> dict[str, list[dict[str, Any]]]:
    """Turn-level runs (raw history and run memory) per session ref."""
    from .agent_execution_traces import agent_execution_traces
    from .procedural_memory import load_recent_evidence
    from .run_memory import _ensure, _key, _session_ref

    key = _key()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    raw = load_recent_evidence(limit=100_000, since=since)
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in raw:
        if str(event.get("source") or "") == "agent" or str(event.get("event_type") or "").startswith("agent_"):
            ref = _session_ref(str(event.get("session_id") or ""), key=key)
            if ref in session_refs:
                by_session[ref].append(event)
    # Per session: traces return at most 100 runs per call, and one session's
    # turns fit; across all sessions they would not.
    for ref, events in by_session.items():
        for trace in agent_execution_traces(events, limit=100, max_events_per_execution=1).get("executions") or []:
            out[ref].append(trace)
            seen.add(str(trace.get("execution_id")))
    with connect() as conn:
        _ensure(conn)
        for refs_json, record_json in conn.execute("SELECT session_refs, record_json FROM run_memory WHERE ended_at >= ?", (since,)).fetchall():
            try:
                refs, record = set(json.loads(refs_json or "[]")), json.loads(record_json)
            except Exception:
                continue
            if record.get("actor_kind") != "agent" or str(record.get("execution_id")) in seen:
                continue
            for ref in refs & session_refs:
                out[ref].append(record)
    return out


def _session_metrics(runs: list[dict[str, Any]], delivered_at: str) -> dict[str, float | None]:
    turns = sorted(
        (r for r in runs if not r.get("parent_execution_id") and r.get("work_summary")
         and str(r.get("started_at") or "") >= str(delivered_at or "")),
        key=lambda r: str(r.get("started_at") or ""),
    )
    tested = [r["work_summary"]["tests"] for r in turns if isinstance(r["work_summary"].get("tests"), dict)]
    compared = [((r.get("human_context") or {}).get("after") or {}).get("between_turns") for r in turns]
    compared = [b for b in compared if isinstance(b, dict) and b.get("head_moved") is False and "files_changed" in b]
    prs = [r["delivery_outcome"] for r in turns if isinstance(r.get("delivery_outcome"), dict)]
    merged = sum(int(d.get("merged") or 0) for d in prs)
    closed = sum(int(d.get("closed_unmerged") or 0) for d in prs)
    resolved = merged + closed
    tokens = [int(r["work_summary"].get("total_tokens") or 0) for r in turns if r["work_summary"].get("total_tokens")]
    return {
        "turns": float(len(turns)) if turns else None,
        "test_fail_rate": (sum(1 for t in tested if t.get("ended") == "failing") / len(tested)) if tested else None,
        "rework_rate": (sum(1 for b in compared if int(b.get("agent_files_changed") or 0) > 0) / len(compared)) if compared else None,
        "pr_merge_rate": (merged / resolved) if resolved else None,
        "tokens_per_turn": float(statistics.median(tokens)) if tokens else None,
    }


def _bootstrap_difference(treated: list[float], control: list[float], *, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    diffs = []
    for _ in range(RESAMPLES):
        a = [treated[rng.randrange(len(treated))] for _ in treated]
        b = [control[rng.randrange(len(control))] for _ in control]
        diffs.append(statistics.fmean(a) - statistics.fmean(b))
    diffs.sort()
    return diffs[int(0.025 * RESAMPLES)], diffs[int(0.975 * RESAMPLES) - 1]


def report() -> dict[str, Any]:
    from .agent_brief import HOLDOUT_PERCENT, evaluation_state

    state = evaluation_state()
    sessions = _sessions()
    arms = {"brief": 0, "control": 0}
    for s in sessions.values():
        arms[s["arm"]] = arms.get(s["arm"], 0) + 1
    result: dict[str, Any] = {
        "evaluation_enabled": bool(state.get("enabled")),
        "trial_id": state.get("trial_id") or None,
        "trial_started_at": state.get("started_at"),
        "trial_ended_at": state.get("ended_at"),
        "holdout_percent": HOLDOUT_PERCENT,
        "sessions": arms,
        "min_sessions_per_arm": MIN_SESSIONS,
        "metrics": {},
        "method": "sessions explicitly assigned within one randomized trial to brief or held-back control; difference in per-session means, "
                  "briefed minus control, with a bootstrap 95% interval; exploratory (several metrics, no multiple-comparison correction). "
                  "PR merge rate uses resolved PRs only; open/pending PRs are right-censored and excluded.",
    }
    if not sessions:
        result["status"] = "no_sessions_yet"
        return result
    since = min(str(s["delivered_at"]) for s in sessions.values())
    runs = _runs_by_session(set(sessions), since)
    values: dict[str, dict[str, list[float]]] = {m: {"brief": [], "control": []} for m in METRICS}
    for ref, s in sessions.items():
        metrics = _session_metrics(runs.get(ref, []), s["delivered_at"])
        for name, value in metrics.items():
            if value is not None and s["arm"] in ("brief", "control"):
                values[name][s["arm"]].append(float(value))
    enough_any = False
    for name, direction in METRICS.items():
        treated, control = values[name]["brief"], values[name]["control"]
        entry: dict[str, Any] = {
            "better_when": direction,
            "sessions": {"brief": len(treated), "control": len(control)},
            "mean": {"brief": round(statistics.fmean(treated), 4) if treated else None,
                     "control": round(statistics.fmean(control), 4) if control else None},
        }
        if len(treated) >= MIN_SESSIONS and len(control) >= MIN_SESSIONS:
            enough_any = True
            low, high = _bootstrap_difference(treated, control)
            entry["difference"] = round(statistics.fmean(treated) - statistics.fmean(control), 4)
            entry["ci95"] = [round(low, 4), round(high, 4)]
            if direction is None or low <= 0 <= high:
                entry["verdict"] = "no_clear_difference"
            else:
                helps = (high < 0) if direction == "lower" else (low > 0)
                entry["verdict"] = "briefed_better" if helps else "briefed_worse"
        else:
            entry["verdict"] = "not_enough_data"
        result["metrics"][name] = entry
    result["status"] = "ok" if enough_any else "not_enough_data"
    return result


__all__ = ["report"]
