from __future__ import annotations

"""Playbooks: a repeated workflow, portable and content-free.

Export turns one observed workflow family (runs that share a structural shape)
into a small JSON file that another person, another device or another agent can
use: the typical readable steps, the commands and git operations used, how tests
and pull requests usually ended, how often the person reworked the result, and
typical size. Import accepts such a file through one strict gate, and agents can
read imported playbooks through MCP (``get_playbooks``).

A playbook never carries titles, paths, prompts, tool content, file or workspace
hashes (those are keyed to one device), execution IDs or evidence references.
Its only free text is its name, which must be short, plain and not
instruction-like.
"""

import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect

FORMAT = "openworkgraph.playbook/v1"
MAX_BYTES = 32_000
MAX_STEPS = 24
MIN_RUNS = 2
LOOKBACK_DAYS = 180
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,()\-]{0,79}$")
_FAMILY_RE = re.compile(r"^agent:(?:workflow|structure):[0-9a-f]{16}$")
_HASHED_TOOL_RE = re.compile(r"^tool:[0-9a-f]{12}$")
_APPROVAL_STEP_RE = re.compile(r"^approval_received:(?:success|denied|cancelled|observed)$")
_ERROR_STEP_RE = re.compile(r"^error:(?:error|failed|denied|cancelled|timeout)$")
_TOOL_CATEGORIES = frozenset({
    "filesystem", "shell", "browser", "code", "search", "network", "database",
    "messaging", "issue_tracker", "deployment", "mcp", "other", "none",
})
_FAILURE_STATES = frozenset({"error", "failed", "denied", "cancelled", "timeout"})
_SIMPLE_STEPS = frozenset({"model_call", "handoff", "approval_request"})
_LOCAL_REF_PREFIXES = ("f:", "w:", "event:", "execution:", "run:", "s:")
_SAFE_FRAMEWORKS = frozenset({
    "claude-code", "codex", "cursor", "copilot", "gemini-cli", "openai-agents-python",
    "openclaw", "langgraph", "langchain", "semantic-kernel", "autogen",
})
_TABLE = """
CREATE TABLE IF NOT EXISTS playbooks (
  playbook_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  playbook_json TEXT NOT NULL
)
"""


class PlaybookError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ the gate

def _int(value: Any, maximum: int = 10_000_000) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if 0 <= number <= maximum else None


def _rate(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 4) if 0.0 <= float(value) <= 1.0 else None


def _name(value: Any) -> str:
    from mcp_server.security import _looks_instruction_like

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not _NAME_RE.fullmatch(text) or _looks_instruction_like(text):
        raise PlaybookError("playbook name must be 1-80 plain characters (letters, digits, spaces, . , _ - ( ))")
    return text


def _structural_step(value: Any) -> str | None:
    """Accept only values OpenWorkGraph itself can generate as structural steps.

    A fixed character set is not a security boundary: strings such as
    ``ignore_previous_instructions`` are syntactically simple but still arbitrary
    agent-visible text. Portable playbooks therefore accept only the canonical
    structural grammar, and never device-local opaque references.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    low = text.lower()
    if not text or len(text) > 120 or low.startswith(_LOCAL_REF_PREFIXES):
        return None
    if text in _SIMPLE_STEPS or _APPROVAL_STEP_RE.fullmatch(text) or _ERROR_STEP_RE.fullmatch(text):
        return text
    if not text.startswith("tool:"):
        return None
    body = text[5:]
    category, sep, tail = body.partition(":")
    if not sep or category not in _TOOL_CATEGORIES or not tail:
        return None
    status = ""
    label = tail
    for candidate in _FAILURE_STATES:
        suffix = ":" + candidate
        if tail.endswith(suffix):
            status = candidate
            label = tail[:-len(suffix)]
            break
    if not label or label.lower().startswith(_LOCAL_REF_PREFIXES):
        return None
    if _HASHED_TOOL_RE.fullmatch(label):
        canonical = label
    else:
        from server.agent_tool_labels import readable_tool_name

        canonical = readable_tool_name(label)
    if canonical != label:
        return None
    return f"tool:{category}:{label}" + (f":{status}" if status else "")


def sanitize(raw: Any) -> dict[str, Any]:
    """The single gate for every playbook, exported or imported. Unknown keys are dropped."""
    from shared.tool_detail import COMMANDS, GIT_OPS, GH_OPS

    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise PlaybookError(f"not an OpenWorkGraph playbook (format must be {FORMAT})")
    family = str(raw.get("family_key") or "").lower()
    if not _FAMILY_RE.fullmatch(family):
        raise PlaybookError("invalid family_key")
    out: dict[str, Any] = {"format": FORMAT, "name": _name(raw.get("name")), "family_key": family}
    out["agent_frameworks"] = sorted({str(f).lower() for f in raw.get("agent_frameworks") or [] if str(f).lower() in _SAFE_FRAMEWORKS})[:8]
    runs = _int(raw.get("runs_observed"))
    if runs is None or runs < 1:
        raise PlaybookError("runs_observed must be a positive number")
    out["runs_observed"] = runs
    steps = [_structural_step(s) for s in raw.get("typical_steps") or []]
    out["typical_steps"] = [s for s in steps if s][:MAX_STEPS]
    out["commands"] = [str(c) for c in raw.get("commands") or [] if str(c) in COMMANDS][:12]
    out["git"] = [str(g) for g in raw.get("git") or [] if str(g) in GIT_OPS][:12]
    out["gh"] = [str(g) for g in raw.get("gh") or [] if str(g) in GH_OPS][:12]
    tests = raw.get("tests") if isinstance(raw.get("tests"), dict) else {}
    out["tests"] = {k: v for k, v in {"runs_with_tests": _int(tests.get("runs_with_tests")),
                                      "ended_failing_rate": _rate(tests.get("ended_failing_rate"))}.items() if v is not None}
    delivery = raw.get("pull_requests") if isinstance(raw.get("pull_requests"), dict) else {}
    out["pull_requests"] = {k: v for k, v in {"opened": _int(delivery.get("opened")), "merged": _int(delivery.get("merged")),
                                              "closed_unmerged": _int(delivery.get("closed_unmerged"))}.items() if v is not None}
    human = raw.get("human_after") if isinstance(raw.get("human_after"), dict) else {}
    out["human_after"] = {k: v for k, v in {"turns_compared": _int(human.get("turns_compared")),
                                            "rework_rate": _rate(human.get("rework_rate")),
                                            "median_minutes_to_next_prompt": _int(human.get("median_minutes_to_next_prompt"), 24 * 60)}.items() if v is not None}
    typical = raw.get("typical_run") if isinstance(raw.get("typical_run"), dict) else {}
    out["typical_run"] = {k: v for k, v in {key: _int(typical.get(key)) for key in ("files_edited", "lines_added", "lines_removed", "tokens")}.items() if v is not None}
    exported = str(raw.get("exported_at") or "")[:40]
    out["exported_at"] = exported if re.fullmatch(r"[0-9T:.+\-Z]{10,40}", exported) else ""
    out["content_free"] = True
    if len(json.dumps(out)) > MAX_BYTES:
        raise PlaybookError("playbook is too large")
    return out


# ------------------------------------------------------------------ building from local runs

def _local_runs(*, now: datetime | None = None) -> list[dict[str, Any]]:
    from .agent_execution_traces import agent_execution_traces
    from .procedural_memory import load_recent_evidence
    from .run_memory import memory_runs

    since = ((now or _now()) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for trace in agent_execution_traces(load_recent_evidence(limit=100_000, since=since), limit=100,
                                            max_events_per_execution=1).get("executions") or []:
            runs.append(trace)
            seen.add(str(trace.get("execution_id")))
    except Exception:
        pass
    for record in memory_runs(since=since):
        if record.get("actor_kind") == "agent" and str(record.get("execution_id")) not in seen:
            runs.append(record)
    for run in runs:
        run["_family"] = str(run.get("observed_family_key") or run.get("family_key") or "")
    return [r for r in runs if r["_family"] and not r.get("parent_execution_id")]


def local_families() -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in _local_runs():
        groups.setdefault(run["_family"], []).append(run)
    out = []
    for family, runs in groups.items():
        if len(runs) < MIN_RUNS:
            continue
        out.append({
            "family_key": family,
            "runs_observed": len(runs),
            "agent_frameworks": sorted({str((r.get("agent") or {}).get("framework") or "") for r in runs} - {""}),
            "last_seen": max(str(r.get("started_at") or "") for r in runs)[:10],
            "typical_steps": _typical_steps(runs)[:8],
        })
    out.sort(key=lambda f: (-f["runs_observed"], f["family_key"]))
    return out[:50]


def _typical_steps(runs: list[dict[str, Any]]) -> list[str]:
    sequences = Counter(tuple(r.get("structural_steps") or [])[:MAX_STEPS] for r in runs if r.get("structural_steps"))
    return list(sequences.most_common(1)[0][0]) if sequences else []


def build(family_key: str, *, name: str) -> dict[str, Any]:
    runs = [r for r in _local_runs() if r["_family"] == family_key]
    if len(runs) < MIN_RUNS:
        raise PlaybookError(f"a playbook needs at least {MIN_RUNS} observed runs of this workflow")
    summaries = [r.get("work_summary") or {} for r in runs]
    commands: Counter[str] = Counter()
    git: Counter[str] = Counter()
    gh: Counter[str] = Counter()
    for s in summaries:
        commands.update({k: int(v or 0) for k, v in (s.get("commands") or {}).items()})
        git.update({k: int(v or 0) for k, v in (s.get("git") or {}).items()})
        gh.update({k: int(v or 0) for k, v in (s.get("gh") or {}).items()})
    tested = [s["tests"] for s in summaries if isinstance(s.get("tests"), dict)]
    delivered = [r["delivery_outcome"] for r in runs if isinstance(r.get("delivery_outcome"), dict)]
    afters = [((r.get("human_context") or {}).get("after") or {}) for r in runs]
    compared = [a["between_turns"] for a in afters if isinstance(a.get("between_turns"), dict)
                and a["between_turns"].get("head_moved") is False and "files_changed" in a["between_turns"]]
    gaps = [float(a["gap_seconds"]) for a in afters if a.get("gap_seconds")]

    def median(values: list[int]) -> int | None:
        values = [v for v in values if v]
        return int(statistics.median(values)) if values else None

    raw = {
        "format": FORMAT,
        "name": name,
        "family_key": family_key,
        "agent_frameworks": sorted({str((r.get("agent") or {}).get("framework") or "") for r in runs} - {""}),
        "runs_observed": len(runs),
        "typical_steps": _typical_steps(runs),
        "commands": [c for c, _ in commands.most_common(12)],
        "git": [g for g, _ in git.most_common(12)],
        "gh": [g for g, _ in gh.most_common(12)],
        "tests": {"runs_with_tests": len(tested),
                  "ended_failing_rate": (sum(1 for t in tested if t.get("ended") == "failing") / len(tested)) if tested else None},
        "pull_requests": {"opened": sum(int(d.get("prs") or 0) for d in delivered),
                          "merged": sum(int(d.get("merged") or 0) for d in delivered),
                          "closed_unmerged": sum(int(d.get("closed_unmerged") or 0) for d in delivered)} if delivered else {},
        "human_after": {"turns_compared": len(compared),
                        "rework_rate": (sum(1 for b in compared if int(b.get("agent_files_changed") or 0) > 0) / len(compared)) if compared else None,
                        "median_minutes_to_next_prompt": int(round(statistics.median(gaps) / 60)) if gaps else None},
        "typical_run": {
            "files_edited": median([int((s.get("files") or {}).get("edited") or 0) for s in summaries]),
            "lines_added": median([int((s.get("lines") or {}).get("added") or 0) for s in summaries]),
            "lines_removed": median([int((s.get("lines") or {}).get("removed") or 0) for s in summaries]),
            "tokens": median([int(s.get("total_tokens") or 0) for s in summaries]),
        },
        "exported_at": _now().isoformat(timespec="seconds"),
    }
    return sanitize(raw)


# ------------------------------------------------------------------ imported playbooks

def import_playbook(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (str, bytes)):
        if len(raw) > MAX_BYTES:
            raise PlaybookError("playbook is too large")
        try:
            raw = json.loads(raw)
        except Exception as exc:
            raise PlaybookError("playbook is not valid JSON") from exc
    elif len(json.dumps(raw, default=str)) > MAX_BYTES:
        raise PlaybookError("playbook is too large")
    clean = sanitize(raw)
    playbook_id = "playbook:" + hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    with connect() as conn:
        conn.execute(_TABLE)
        conn.execute("INSERT OR REPLACE INTO playbooks(playbook_id, name, imported_at, playbook_json) VALUES (?, ?, ?, ?)",
                     (playbook_id, clean["name"], _now().isoformat(), json.dumps(clean)))
    return {"playbook_id": playbook_id, **clean}


def imported(*, family_key: str = "") -> list[dict[str, Any]]:
    with connect() as conn:
        conn.execute(_TABLE)
        rows = conn.execute("SELECT playbook_id, imported_at, playbook_json FROM playbooks ORDER BY imported_at DESC LIMIT 200").fetchall()
    out = []
    for playbook_id, imported_at, raw in rows:
        try:
            item = sanitize(json.loads(raw))  # re-checked on every read
        except Exception:
            continue
        if family_key and item["family_key"] != family_key:
            continue
        out.append({"playbook_id": playbook_id, "imported_at": imported_at, **item})
    return out


def delete(playbook_id: str) -> int:
    with connect() as conn:
        conn.execute(_TABLE)
        return int(conn.execute("DELETE FROM playbooks WHERE playbook_id = ?", (str(playbook_id),)).rowcount or 0)


__all__ = ["FORMAT", "PlaybookError", "build", "delete", "import_playbook", "imported", "local_families", "sanitize"]
