from __future__ import annotations

"""Did the agent's work hold up? Pull request state and CI, via the local ``gh`` CLI.

Off by default. When a person turns it on:

* A pull request an agent opens (``gh pr create`` or a create-pull-request
  tool) is added to a local watch list, keyed to the run. Only github.com PRs
  are accepted today; arbitrary hosts never become network targets.
* Every few minutes the local ``gh`` CLI (the person's own GitHub login, read
  only) is asked for the pull request's state and CI result.
* Each run gets a content-free ``delivery_outcome``: how many PRs it opened,
  how many were merged, closed without merge or still open, and the CI result.
* Once a PR is merged or closed with final CI, or after 30 days, the link is
  dropped and only the outcome counts remain.

Turning tracking off stops all GitHub calls and deletes every stored PR link.
Deleting a session or a date range deletes its watches too.
"""

import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .db import DATA_DIR, connect

WATCH_DAYS = 30
CHECK_EVERY_SECONDS = 600
POLL_LOOP_SECONDS = 60
MAX_CHECKS_PER_POLL = 20
GH_TIMEOUT_SECONDS = 20

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_TABLE = """
CREATE TABLE IF NOT EXISTS outcome_watch (
  pr_key TEXT NOT NULL,
  run_key TEXT NOT NULL,
  session_ref TEXT NOT NULL DEFAULT '',
  host TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL DEFAULT '',
  repo TEXT NOT NULL DEFAULT '',
  number INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  checked_at TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'unknown',
  ci TEXT NOT NULL DEFAULT 'none',
  resolved INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pr_key, run_key)
)
"""
_STATUS: dict[str, Any] = {"last_poll_at": None, "last_error": None, "gh_checked_at": None, "gh_logged_in": None}
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _ensure(conn) -> None:
    conn.execute(_TABLE)


# ------------------------------------------------------------------ the switch

def _settings_path() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA") or DATA_DIR) / "outcome_tracking.json"


def enabled() -> bool:
    try:
        return bool(json.loads(_settings_path().read_text(encoding="utf-8")).get("enabled"))
    except Exception:
        return False


def set_enabled(value: bool) -> dict[str, Any]:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": bool(value), "changed_at": _now().isoformat()}) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    removed = 0 if value else forget_links()
    return {"enabled": bool(value), "pr_links_deleted": removed}


# ------------------------------------------------------------------ registering

def _pr_key(host: str, owner: str, repo: str, number: int) -> str:
    from .run_memory import _digest

    return "pr:" + _digest(f"pr|{host}/{owner.lower()}/{repo.lower()}#{number}")


def _valid_ref(ref: Any) -> tuple[str, str, str, int] | None:
    if not isinstance(ref, dict):
        return None
    host, owner, repo = str(ref.get("host") or "").lower(), str(ref.get("owner") or ""), str(ref.get("repo") or "")
    try:
        number = int(ref.get("number"))
    except Exception:
        return None
    # The PR URL comes from untrusted tool output. Do not let a syntactically
    # valid arbitrary hostname widen OWG's network boundary. GHES can be added
    # later through an explicit trusted-host configuration instead of inference.
    if host != "github.com":
        return None
    if not (_NAME_RE.fullmatch(owner) and _NAME_RE.fullmatch(repo) and 0 < number < 10**9):
        return None
    return host, owner, repo, number


def run_material(actor_id: Any, trace: dict[str, Any], session_id: Any) -> str:
    """The same run identity procedural memory and run memory use."""
    native_run = str(trace.get("run_id") or trace.get("trace_id") or session_id or "").strip()
    return f"agent|{str(actor_id or 'agent')}|{native_run}"


def register(pairs: Iterable[tuple[str, Any]]) -> int:
    """Watch pull requests opened by stored agent events (``(event_id, refs)`` pairs)."""
    if not enabled():
        return 0
    from .run_memory import _key, _session_ref, run_key

    key = _key()
    added = 0
    now = _now().isoformat()
    with connect() as conn:
        _ensure(conn)
        for event_id, refs in pairs:
            row = conn.execute(
                "SELECT actor_id, session_id, metadata_json, observed_at FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                continue
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
            rkey = run_key(run_material(row["actor_id"], trace, row["session_id"]), key=key)
            sref = _session_ref(str(row["session_id"] or ""), key=key)
            observed = _parse(row["observed_at"])
            # created_at is the run evidence time, not delayed spool/ingest time.
            # That keeps range deletion and the 30-day watch horizon aligned to
            # the work the person actually asked to forget.
            created_at = observed.isoformat() if observed is not None else now
            for ref in (refs if isinstance(refs, list) else [])[:4]:
                valid = _valid_ref(ref)
                if valid is None:
                    continue
                host, owner, repo, number = valid
                cur = conn.execute(
                    "INSERT OR IGNORE INTO outcome_watch(pr_key, run_key, session_ref, host, owner, repo, number, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_pr_key(host, owner, repo, number), rkey, sref, host, owner, repo, number, created_at),
                )
                added += int(cur.rowcount or 0)
    return added


# ------------------------------------------------------------------ polling

def _ci(rollup: Any) -> str:
    if not isinstance(rollup, list) or not rollup:
        return "none"
    states: list[str] = []
    for check in rollup:
        if not isinstance(check, dict):
            continue
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            states.append("failing")
        elif status in {"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"} or conclusion in {"PENDING", "EXPECTED", ""}:
            states.append("pending")
        else:
            states.append("passing")
    for worst in ("failing", "pending", "passing"):
        if worst in states:
            return worst
    return "none"


def _gh() -> str | None:
    return shutil.which("gh")


def gh_logged_in(*, runner: Callable[..., Any] = subprocess.run, refresh: bool = False) -> bool | None:
    checked = _STATUS.get("gh_checked_at")
    if not refresh and checked and (_now() - checked).total_seconds() < 300:
        return _STATUS.get("gh_logged_in")
    gh = _gh()
    if not gh:
        result = None
    else:
        try:
            result = runner([gh, "auth", "status"], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS).returncode == 0
        except Exception:
            result = False
    _STATUS.update(gh_checked_at=_now(), gh_logged_in=result)
    return result


def poll_once(*, now: datetime | None = None, runner: Callable[..., Any] = subprocess.run) -> dict[str, int]:
    """Check due pull requests once. Never raises."""
    counts = {"checked": 0, "resolved": 0, "errors": 0, "expired": 0}
    if not enabled():
        return counts
    current = now or _now()
    gh = _gh()
    changed_runs: set[str] = set()
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT rowid, * FROM outcome_watch WHERE resolved = 0 ORDER BY checked_at ASC LIMIT 500"
        ).fetchall()
        for row in rows:
            created = _parse(row["created_at"]) or current
            if current - created > timedelta(days=WATCH_DAYS):
                conn.execute("UPDATE outcome_watch SET resolved = 1, host = '', owner = '', repo = '', number = 0 WHERE rowid = ?", (row["rowid"],))
                counts["expired"] += 1
                changed_runs.add(row["run_key"])
                continue
            checked = _parse(row["checked_at"])
            if checked is not None and (current - checked).total_seconds() < CHECK_EVERY_SECONDS:
                continue
            if counts["checked"] + counts["errors"] >= MAX_CHECKS_PER_POLL:
                break
            if not gh:
                _STATUS["last_error"] = "gh_not_found"
                break
            url = f"https://{row['host']}/{row['owner']}/{row['repo']}/pull/{int(row['number'])}"
            try:
                result = runner([gh, "pr", "view", url, "--json", "state,mergedAt,closedAt,statusCheckRollup"],
                                capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
                if result.returncode != 0:
                    raise RuntimeError("gh_failed")
                data = json.loads(result.stdout or "{}")
            except subprocess.TimeoutExpired:
                data, error = None, "timeout"
            except Exception as exc:
                data, error = None, ("gh_failed" if str(exc) == "gh_failed" else "unreadable_response")
            if data is None:
                conn.execute("UPDATE outcome_watch SET checked_at = ?, error = ? WHERE rowid = ?", (current.isoformat(), error, row["rowid"]))
                counts["errors"] += 1
                _STATUS["last_error"] = error
                continue
            state = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}.get(str(data.get("state") or "").upper(), "unknown")
            ci = _ci(data.get("statusCheckRollup"))
            final = state in {"merged", "closed"} and ci != "pending"
            if final:
                conn.execute(
                    "UPDATE outcome_watch SET checked_at = ?, state = ?, ci = ?, error = '', resolved = 1,"
                    " host = '', owner = '', repo = '', number = 0 WHERE rowid = ?",
                    (current.isoformat(), state, ci, row["rowid"]),
                )
                counts["resolved"] += 1
            else:
                conn.execute("UPDATE outcome_watch SET checked_at = ?, state = ?, ci = ?, error = '' WHERE rowid = ?",
                             (current.isoformat(), state, ci, row["rowid"]))
            counts["checked"] += 1
            if (state, ci) != (row["state"], row["ci"]) or final:
                changed_runs.add(row["run_key"])
    _STATUS["last_poll_at"] = current.isoformat()
    if changed_runs:
        _refresh_memory(changed_runs)
    return counts


def _refresh_memory(run_keys: set[str]) -> None:
    """Keep remembered runs' delivery outcome current after their raw history is gone."""
    from .run_memory import _ensure as _ensure_memory

    outcomes = delivery_outcomes_by_key(run_keys)
    try:
        with connect() as conn:
            _ensure_memory(conn)
            for rkey in run_keys:
                row = conn.execute("SELECT record_json FROM run_memory WHERE run_key = ?", (rkey,)).fetchone()
                if row is None or rkey not in outcomes:
                    continue
                record = json.loads(row[0])
                record["delivery_outcome"] = outcomes[rkey]
                conn.execute("UPDATE run_memory SET record_json = ? WHERE run_key = ?", (json.dumps(record, ensure_ascii=False), rkey))
    except Exception:
        pass


# ------------------------------------------------------------------ reading

def delivery_outcomes_by_key(run_keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    keys = sorted({str(k) for k in run_keys if k})
    if not keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with connect() as conn:
            _ensure(conn)
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                rows = conn.execute(
                    f"SELECT run_key, state, ci FROM outcome_watch WHERE run_key IN ({','.join('?' for _ in chunk)})", tuple(chunk)
                ).fetchall()
                for rkey, state, ci in rows:
                    item = out.setdefault(rkey, {"prs": 0, "merged": 0, "closed_unmerged": 0, "open": 0, "unknown": 0, "ci": "none"})
                    item["prs"] += 1
                    item[{"merged": "merged", "closed": "closed_unmerged", "open": "open"}.get(state, "unknown")] += 1
                    rank = {"none": 0, "passing": 1, "pending": 2, "failing": 3}
                    if rank.get(ci, 0) > rank.get(item["ci"], 0):
                        item["ci"] = ci
    except Exception:
        return {}
    return out


def delivery_outcomes(materials: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Outcomes keyed by run material (``agent|actor|native_run``)."""
    from .run_memory import _key, run_key

    materials = [m for m in materials if m]
    if not materials:
        return {}
    try:
        with connect() as conn:
            exists = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'outcome_watch'").fetchone()
            if not exists or not conn.execute("SELECT 1 FROM outcome_watch LIMIT 1").fetchone():
                return {}
        key = _key()
    except Exception:
        return {}
    by_key = {run_key(m, key=key): m for m in materials}
    return {by_key[k]: v for k, v in delivery_outcomes_by_key(by_key).items()}


# ------------------------------------------------------------------ forgetting

def forget_links() -> int:
    """Delete every stored PR link (unresolved watches). Resolved outcome counts stay."""
    with connect() as conn:
        _ensure(conn)
        return int(conn.execute("DELETE FROM outcome_watch WHERE resolved = 0").rowcount or 0)


def forget_sessions(session_refs: set[str]) -> int:
    if not session_refs:
        return 0
    refs = sorted(session_refs)
    with connect() as conn:
        _ensure(conn)
        return int(conn.execute(f"DELETE FROM outcome_watch WHERE session_ref IN ({','.join('?' for _ in refs)})", tuple(refs)).rowcount or 0)


def forget_run_keys(run_keys: Iterable[str]) -> int:
    keys = sorted({k for k in run_keys if k})
    if not keys:
        return 0
    with connect() as conn:
        _ensure(conn)
        return int(conn.execute(f"DELETE FROM outcome_watch WHERE run_key IN ({','.join('?' for _ in keys)})", tuple(keys)).rowcount or 0)


def forget_range(since: datetime, until: datetime) -> int:
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT rowid, created_at FROM outcome_watch").fetchall()
        doomed = [r[0] for r in rows if (lambda t: t is not None and since <= t < until)(_parse(r[1]))]
        for rowid in doomed:
            conn.execute("DELETE FROM outcome_watch WHERE rowid = ?", (rowid,))
    return len(doomed)


# ------------------------------------------------------------------ status and loop

def status(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    with connect() as conn:
        _ensure(conn)
        watching = int(conn.execute("SELECT COUNT(*) FROM outcome_watch WHERE resolved = 0").fetchone()[0])
        resolved = int(conn.execute("SELECT COUNT(*) FROM outcome_watch WHERE resolved = 1").fetchone()[0])
    on = enabled()
    return {
        "enabled": on,
        "gh_found": bool(_gh()),
        "gh_logged_in": gh_logged_in(runner=runner) if on else None,
        "watching": watching,
        "resolved": resolved,
        "last_poll_at": _STATUS.get("last_poll_at"),
        "last_error": _STATUS.get("last_error"),
        "check_every_seconds": CHECK_EVERY_SECONDS,
        "stored": "github.com owner, repository and PR number agents opened, until resolved or 30 days",
        "contacts": "github.com, through your local gh CLI login, read-only, only while this is on",
    }


def _loop() -> None:
    while not _STOP.is_set():
        try:
            poll_once()
        except Exception:
            pass
        _STOP.wait(POLL_LOOP_SECONDS)


def start() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="owg-outcome-tracker", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()


__all__ = [
    "delivery_outcomes", "delivery_outcomes_by_key", "enabled", "forget_links", "forget_range", "forget_run_keys",
    "forget_sessions", "poll_once", "register", "run_material", "set_enabled", "start", "status", "stop",
]
