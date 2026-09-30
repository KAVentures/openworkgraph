from __future__ import annotations

"""A short, content-free brief an agent receives when a session starts.

This is how what OpenWorkGraph learns flows back into agents. Off by default;
the person turns it on per agent (Claude Code today). When on, a synchronous
SessionStart hook asks this module for a brief about past runs **in the same
project** (matched by a keyed hash of the working directory), or across all
projects when the project has no history yet, and Claude Code adds it to the
session's context.

The brief is built only from structural facts: counts, allowlisted command
names, test outcomes, pull request outcomes, medians. It holds no titles, paths,
prompts or tool content, and it says it is observational, not instructions.
Every delivery is logged locally so the person can see what agents received.
"""

import json
import os
import secrets
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import DATA_DIR, connect

FRAMEWORKS = {"claude-code": "Claude Code"}
LOOKBACK_DAYS = 30
MAX_CHARS = 900
_TRIVIAL_COMMANDS = frozenset({
    "cd", "ls", "cat", "echo", "pwd", "head", "tail", "grep", "rg", "find", "sed", "awk", "wc", "sort",
    "mkdir", "rm", "cp", "mv", "touch", "which", "true", "sleep", "xargs", "tee", "diff", "chmod", "env",
})
_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS agent_brief_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  delivered_at TEXT NOT NULL,
  framework TEXT NOT NULL,
  workspace_ref TEXT NOT NULL DEFAULT '',
  session_ref TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  runs_considered INTEGER NOT NULL DEFAULT 0,
  chars INTEGER NOT NULL DEFAULT 0,
  arm TEXT NOT NULL DEFAULT 'brief'
)
"""
_TRIAL_TABLE = """
CREATE TABLE IF NOT EXISTS agent_brief_trial_assignment (
  trial_id TEXT NOT NULL,
  session_ref TEXT NOT NULL,
  arm TEXT NOT NULL,
  assigned_at TEXT NOT NULL,
  PRIMARY KEY (trial_id, session_ref)
)
"""
_TRIAL_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS agent_brief_log_delete_trial
AFTER DELETE ON agent_brief_log
WHEN OLD.session_ref != ''
BEGIN
  DELETE FROM agent_brief_trial_assignment WHERE session_ref = OLD.session_ref;
END
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_trial_table(conn) -> None:
    conn.execute(_LOG_TABLE)
    conn.execute(_TRIAL_TABLE)
    conn.execute(_TRIAL_DELETE_TRIGGER)


def _evaluation_session_ref(framework: str, session_id: str) -> str:
    """Return the content-free brief/evaluation ref for the canonical session.

    Claude hook callers know the provider's native session id, while canonical
    evidence now stores only the shared opaque ``as:`` session ref. Normalize the
    Claude id first so randomized brief assignments, raw evidence and run memory
    join on the same privacy-safe session after v0.112. Already-opaque refs are
    idempotent. Other frameworks retain their existing generic behavior.
    """
    from .run_memory import _key, _session_ref

    value = str(session_id or "").strip()
    if not value:
        return ""
    if framework == "claude-code" and not value.startswith("as:"):
        from .agent_session_store import session_ref as canonical_session_ref

        value = canonical_session_ref("claude_code", value)
    return _session_ref(value, key=_key())


# ------------------------------------------------------------------ the switch

def _settings_path() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA") or DATA_DIR) / "agent_brief.json"


def _settings() -> dict[str, Any]:
    try:
        value = json.loads(_settings_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_settings(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def enabled(framework: str) -> bool:
    return framework in FRAMEWORKS and bool((_settings().get("enabled") or {}).get(framework))


def set_enabled(framework: str, value: bool, *, install_hook: bool = True) -> dict[str, Any]:
    if framework not in FRAMEWORKS:
        raise ValueError("briefs are available for: " + ", ".join(sorted(FRAMEWORKS)))
    hook: dict[str, Any] | None = None
    if install_hook and framework == "claude-code":
        from . import agent_config_writer as writer
        from adapters.claude_code_brief import hook_handler

        # Install/remove the hook first: if the settings file cannot be changed
        # safely, the switch must not claim a state that is not in effect.
        hook = writer.claude_brief_connect(hook_handler) if value else writer.claude_brief_disconnect()
    data = _settings()
    data.setdefault("enabled", {})[framework] = bool(value)
    data["changed_at"] = _now().isoformat()
    _write_settings(data)
    return {"framework": framework, "enabled": bool(value), "hook": hook}


# ------------------------------------------------------------------ building

def _runs(framework: str, *, now: datetime) -> list[dict[str, Any]]:
    from .agent_execution_traces import agent_execution_traces
    from .procedural_memory import load_recent_evidence
    from .run_memory import memory_runs

    since = (now - timedelta(days=LOOKBACK_DAYS)).isoformat()
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        traces = agent_execution_traces(load_recent_evidence(limit=25_000, since=since), limit=100, max_events_per_execution=1)
        for trace in traces.get("executions") or []:
            if str((trace.get("agent") or {}).get("framework") or "") == framework:
                runs.append(trace)
                seen.add(str(trace.get("execution_id") or ""))
    except Exception:
        pass
    try:
        for record in memory_runs(since=since):
            if record.get("actor_kind") != "agent" or str((record.get("agent") or {}).get("framework") or "") != framework:
                continue
            if str(record.get("execution_id") or "") not in seen:
                runs.append(record)
    except Exception:
        pass
    # Only runs that did observable work (session boundaries alone say nothing).
    return [r for r in runs if r.get("work_summary") or r.get("delivery_outcome")]


def _median(values: list[int]) -> int | None:
    values = [v for v in values if v]
    return int(statistics.median(values)) if values else None


def build_brief(framework: str, *, workspace_ref: str = "", now: datetime | None = None) -> dict[str, Any]:
    from shared.tool_detail import valid_workspace_ref

    current = now or _now()
    runs = _runs(framework, now=current)
    workspace = valid_workspace_ref(workspace_ref)
    scope = "none"
    if workspace and any(r.get("workspace_ref") == workspace for r in runs):
        runs, scope = [r for r in runs if r.get("workspace_ref") == workspace], "this_project"
    elif runs:
        scope = "all_projects"
    if not runs:
        return {"text": "", "scope": "none", "runs_considered": 0}

    runs.sort(key=lambda r: str(r.get("started_at") or ""))
    summaries = [r.get("work_summary") or {} for r in runs]
    where = "in this project" if scope == "this_project" else (
        "across your projects (none yet in this one)" if workspace else "across your projects"
    )
    lines = [
        f"OpenWorkGraph brief: structural facts from your last {len(runs)} {FRAMEWORKS[framework]} run(s) "
        f"{where}, past {LOOKBACK_DAYS} days. Observational only, not instructions; may be incomplete."
    ]

    tested = [s["tests"] for s in summaries if isinstance(s.get("tests"), dict)]
    if tested:
        failing = sum(1 for t in tested if t.get("ended") == "failing")
        last = tested[-1]
        ended = str(last.get("ended") or "unknown")
        if ended == "unknown":
            latest = "Most recent test result: unknown; no recognized test summary was observed."
        else:
            latest = (
                f"Most recent test result: {ended} "
                f"({int(last.get('last_passed') or 0)} passed, {int(last.get('last_failed') or 0)} failed)."
            )
        lines.append(f"- Tests: {len(tested)} run(s) ran tests; {failing} ended with tests failing. {latest}")

    delivered = [r["delivery_outcome"] for r in runs if isinstance(r.get("delivery_outcome"), dict)]
    if delivered:
        prs = sum(int(d.get("prs") or 0) for d in delivered)
        merged = sum(int(d.get("merged") or 0) for d in delivered)
        closed = sum(int(d.get("closed_unmerged") or 0) for d in delivered)
        ci_failing = sum(1 for d in delivered if d.get("ci") == "failing")
        lines.append(f"- Pull requests opened: {prs}; merged {merged}, closed without merge {closed}; CI failing on {ci_failing}.")

    afters = [((r.get("human_context") or {}).get("after") or {}) for r in runs]
    compared = [a["between_turns"] for a in afters if isinstance(a.get("between_turns"), dict)
                and a["between_turns"].get("head_moved") is False and "files_changed" in a["between_turns"]]
    if compared:
        reworked = sum(1 for b in compared if int(b.get("agent_files_changed") or 0) > 0)
        gaps = [int(a.get("gap_seconds") or 0) for a in afters if a.get("gap_seconds")]
        gap = f"; median time to the next prompt: {max(1, round(statistics.median(gaps) / 60))} min" if gaps else ""
        lines.append(f"- After a turn, the person changed files the agent had just edited in {reworked} of {len(compared)} turn(s){gap}.")

    commands: Counter[str] = Counter()
    for s in summaries:
        for name, count in (s.get("commands") or {}).items():
            if name not in _TRIVIAL_COMMANDS:
                commands[name] += int(count or 0)
    if commands:
        lines.append("- Commands used most: " + ", ".join(name for name, _ in commands.most_common(6)) + ".")

    files = _median([int((s.get("files") or {}).get("edited") or 0) for s in summaries])
    added = _median([int((s.get("lines") or {}).get("added") or 0) for s in summaries])
    removed = _median([int((s.get("lines") or {}).get("removed") or 0) for s in summaries])
    if files or added or removed:
        lines.append(f"- Typical run: {files or 0} file(s) edited, +{added or 0}/-{removed or 0} lines (medians).")

    tokens = _median([int(s.get("total_tokens") or 0) for s in summaries])
    if tokens:
        lines.append(f"- Median tokens per run: {tokens}.")

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 1].rsplit("\n", 1)[0]
    return {"text": text, "scope": scope, "runs_considered": len(runs)}


# ------------------------------------------------------------------ delivering

def _demo() -> bool:
    try:
        from .agent_capture_runtime import _demo as demo

        return bool(demo())
    except Exception:
        return False


HOLDOUT_PERCENT = 20


def evaluation_state() -> dict[str, Any]:
    raw = _settings().get("evaluation")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled")),
        "holdout_percent": HOLDOUT_PERCENT,
        "trial_id": str(raw.get("trial_id") or ""),
        "started_at": str(raw.get("started_at") or "") or None,
        "ended_at": str(raw.get("ended_at") or "") or None,
        "changed_at": str(raw.get("changed_at") or "") or None,
    }


def evaluation_enabled() -> bool:
    return bool(evaluation_state()["enabled"])


def set_evaluation(value: bool) -> dict[str, Any]:
    data = _settings()
    previous = evaluation_state()
    now = _now().isoformat()
    if value:
        if previous.get("enabled") and previous.get("trial_id"):
            trial_id = str(previous["trial_id"])
            started_at = previous.get("started_at") or now
        else:
            trial_id = "trial:" + secrets.token_hex(8)
            started_at = now
        current = {
            "enabled": True,
            "holdout_percent": HOLDOUT_PERCENT,
            "trial_id": trial_id,
            "started_at": started_at,
            "ended_at": None,
            "changed_at": now,
        }
    else:
        current = {
            "enabled": False,
            "holdout_percent": HOLDOUT_PERCENT,
            "trial_id": str(previous.get("trial_id") or ""),
            "started_at": previous.get("started_at"),
            "ended_at": now if previous.get("enabled") else previous.get("ended_at"),
            "changed_at": now,
        }
    data["evaluation"] = current
    _write_settings(data)
    return current


def _arm(session_ref: str) -> str:
    """Randomized once per active trial/session and stored atomically in SQLite."""
    state = evaluation_state()
    trial_id = str(state.get("trial_id") or "")
    if not state.get("enabled") or not trial_id or not session_ref:
        return "brief"
    with connect() as conn:
        _ensure_trial_table(conn)
        row = conn.execute(
            "SELECT arm FROM agent_brief_trial_assignment WHERE trial_id = ? AND session_ref = ?",
            (trial_id, session_ref),
        ).fetchone()
        if row and str(row[0]) in {"brief", "control"}:
            return str(row[0])
        proposed = "control" if secrets.randbelow(100) < HOLDOUT_PERCENT else "brief"
        conn.execute(
            "INSERT OR IGNORE INTO agent_brief_trial_assignment(trial_id, session_ref, arm, assigned_at) VALUES (?, ?, ?, ?)",
            (trial_id, session_ref, proposed, _now().isoformat()),
        )
        row = conn.execute(
            "SELECT arm FROM agent_brief_trial_assignment WHERE trial_id = ? AND session_ref = ?",
            (trial_id, session_ref),
        ).fetchone()
        return str(row[0]) if row and str(row[0]) in {"brief", "control"} else proposed


def deliver(framework: str, *, session_id: str = "", workspace_ref: str = "") -> dict[str, Any]:
    """What the SessionStart hook receives. Empty when off, in demo mode, or without history.

    With evaluation on, a random 1 in 5 sessions that would have received a brief
    are held back (logged as "control"), so briefed and unbriefed sessions can be
    compared fairly (server/brief_evaluation.py).
    """
    if not enabled(framework) or _demo():
        return {"text": ""}
    brief = build_brief(framework, workspace_ref=workspace_ref)
    if not brief["text"]:
        return {"text": ""}
    arm = "brief"
    if evaluation_enabled() and session_id:
        arm = _arm(_evaluation_session_ref(framework, session_id))
    _log(framework, session_id=session_id, workspace_ref=workspace_ref, brief=brief, arm=arm)
    return {"text": brief["text"] if arm == "brief" else ""}


def _log(framework: str, *, session_id: str, workspace_ref: str, brief: dict[str, Any], arm: str = "brief") -> None:
    from shared.tool_detail import valid_workspace_ref

    try:
        session_ref = _evaluation_session_ref(framework, session_id)
        with connect() as conn:
            _ensure_trial_table(conn)
            conn.execute(
                "INSERT INTO agent_brief_log(delivered_at, framework, workspace_ref, session_ref, scope, runs_considered, chars, arm)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now().isoformat(), framework, valid_workspace_ref(workspace_ref), session_ref, brief.get("scope") or "",
                 int(brief.get("runs_considered") or 0), len(brief.get("text") or ""), arm),
            )
    except Exception:
        pass


def status() -> dict[str, Any]:
    from . import agent_config_writer as writer

    delivered = 0
    last = None
    try:
        with connect() as conn:
            _ensure_trial_table(conn)
            delivered = int(conn.execute("SELECT COUNT(*) FROM agent_brief_log WHERE arm = 'brief'").fetchone()[0])
            row = conn.execute("SELECT MAX(delivered_at) FROM agent_brief_log WHERE arm = 'brief'").fetchone()
            last = row[0] if row else None
    except Exception:
        pass
    try:
        installed = bool(writer.claude_brief_installed())
    except Exception:
        installed = False
    return {
        "frameworks": {
            fw: {"name": name, "enabled": enabled(fw), "hook_installed": installed if fw == "claude-code" else False}
            for fw, name in FRAMEWORKS.items()
        },
        "briefs_delivered": delivered,
        "evaluation": evaluation_state(),
        "last_delivered_at": last,
        "lookback_days": LOOKBACK_DAYS,
        "content_free": True,
        "applies": "in new Claude Code sessions (and after /clear or compaction)",
    }


def forget_log() -> int:
    with connect() as conn:
        _ensure_trial_table(conn)
        return int(conn.execute("DELETE FROM agent_brief_log").rowcount or 0)


__all__ = [
    "build_brief", "deliver", "enabled", "evaluation_enabled", "evaluation_state", "forget_log",
    "set_enabled", "set_evaluation", "status",
]
