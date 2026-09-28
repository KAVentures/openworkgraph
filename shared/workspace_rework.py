from __future__ import annotations

"""What changed in the project between an agent's turns (hook side, content-free).

When an agent turn ends (Claude Code ``Stop``), the hook takes a snapshot of the
project's working tree: for each file git reports as changed, a keyed hash of
its path and a keyed hash of its content. When the next turn starts
(``UserPromptSubmit``, or a new session in the same project), it takes another
and compares. Files that differ were changed by something other than this
agent's turn: usually the person reviewing and fixing its work.

What is recorded on the next turn's start: counts only (files changed, how many
of them this agent had edited in the last 24 hours, whether HEAD moved, the gap).
No paths, names or contents leave this computer; the snapshot itself stays in a
small local state file, keyed by the project's hash, for at most 7 days.

Safety: runs only while OpenWorkGraph holds a recording lease (so never while
paused or stopped), with a short timeout, with git's fsmonitor disabled so a
repository cannot make it run configured commands, and without taking git's
optional index lock (so it never blocks the agent's own git).
"""

import hashlib
import hmac
import json
import os
import posixpath
import subprocess
import time
from pathlib import Path
from typing import Any

MAX_FILES = 200
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 50_000_000
GIT_TIMEOUT_SECONDS = 2.0
STATE_TTL_SECONDS = 7 * 24 * 3600
AGENT_REF_TTL_SECONDS = 24 * 3600
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}


def enabled() -> bool:
    from .tool_detail import enabled as detail_enabled

    return detail_enabled() and os.getenv("OWG_AGENT_REWORK", "1").strip().lower() not in {"0", "false", "off", "no"}


def _git(cwd: str, *args: str) -> bytes | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", cwd, *args],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, env=env,
        )
    except Exception:
        return None
    return result.stdout if result.returncode == 0 else None


def _digest(key: bytes, material: bytes) -> str:
    return hmac.new(key, material, hashlib.sha256).hexdigest()[:16]


def snapshot(cwd: str, *, key: bytes) -> dict[str, Any] | None:
    """Keyed path/content hashes of the files git reports as changed, or None."""
    from .tool_detail import file_ref

    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return None
    root = top.decode("utf-8", "replace").strip()
    head = (_git(cwd, "rev-parse", "HEAD") or b"").strip()
    status = _git(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    if status is None:
        return None
    entries = status.split(b"\0")
    files: dict[str, str] = {}
    total = 0
    truncated = False
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:].decode("utf-8", "replace")
        if code[:1] in (b"R", b"C"):
            index += 1  # the next field is the original path of a rename/copy
        if len(files) >= MAX_FILES:
            truncated = True
            break
        path = posixpath.normpath(posixpath.join(root.replace("\\", "/"), rel))
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                digest = "large:" + str(size)
            else:
                with open(path, "rb") as handle:
                    content = handle.read()
                total += len(content)
                digest = _digest(key, content)
        except FileNotFoundError:
            digest = "deleted"
        except (IsADirectoryError, PermissionError, OSError):
            continue
        files[file_ref(os.path.realpath(path), key=key)] = digest
    return {"head": _digest(key, head) if head else "", "files": files, "truncated": truncated}


def compare(before: dict[str, Any], after: dict[str, Any], *, agent_refs: set[str]) -> dict[str, Any]:
    if before.get("head") != after.get("head"):
        # A commit, pull or checkout moved HEAD: which differences are the
        # person's is no longer knowable from the working tree, so say only that.
        return {"head_moved": True}
    old, new = before.get("files") or {}, after.get("files") or {}
    changed = {ref for ref in set(old) | set(new) if old.get(ref) != new.get(ref)}
    return {
        "head_moved": False,
        "files_changed": len(changed),
        "agent_files_changed": len(changed & agent_refs),
        "incomplete": bool(before.get("truncated") or after.get("truncated")),
    }


# ------------------------------------------------------------------ state

def _state_dir() -> Path:
    from server.local_auth import auth_dir

    path = auth_dir() / "agent_workspace_state"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass
    return path


def _state_path(workspace: str) -> Path:
    return _state_dir() / (workspace.replace(":", "_") + ".json")


class _Locked:
    """Serialize concurrent async hooks for one project (best effort off POSIX)."""

    def __init__(self, path: Path):
        self.lock_path = path.with_suffix(".lock")
        self.handle = None

    def __enter__(self):
        self.handle = open(self.lock_path, "a+")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            self.handle.close()
        except Exception:
            pass


def _load(path: Path, now: float) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(state, dict) or now - float(state.get("updated_at") or 0) > STATE_TTL_SECONDS:
        return {}
    return state


def _save(path: Path, state: dict[str, Any], now: float) -> None:
    state["updated_at"] = now
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)


def prune(now: float | None = None) -> int:
    current = time.time() if now is None else now
    removed = 0
    try:
        for path in _state_dir().glob("*.json"):
            if current - path.stat().st_mtime > STATE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                path.with_suffix(".lock").unlink(missing_ok=True)
                removed += 1
    except Exception:
        pass
    return removed


# ------------------------------------------------------------------ the hook step

def process_claude_hook(payload: dict[str, Any], events: list[dict[str, Any]], *, now: float | None = None,
                        lease_ok=None) -> None:
    """Update project state for this hook and attach ``between_turns`` to a new turn's start."""
    if not enabled() or not isinstance(payload, dict):
        return
    cwd = str(payload.get("cwd") or "")
    hook = str(payload.get("hook_event_name") or "")
    if not cwd or hook not in {"PostToolUse", "Stop", "UserPromptSubmit", "SessionStart"}:
        return
    if lease_ok is None:
        from server.agent_spool import valid_lease

        lease_ok = valid_lease() is not None
    if not lease_ok:
        return  # never while OpenWorkGraph is paused, stopped or not running
    from .tool_detail import _file_ref_key, file_ref, workspace_ref

    key = _file_ref_key()
    current = time.time() if now is None else now
    if hook == "SessionStart":
        prune(current)  # drop project state untouched for 7 days
    path = _state_path(workspace_ref(cwd, key=key))
    with _Locked(path):
        state = _load(path, current)
        agent_refs = {ref: at for ref, at in (state.get("agent_refs") or {}).items() if current - float(at) < AGENT_REF_TTL_SECONDS}
        if hook == "PostToolUse" and str(payload.get("tool_name") or "") in EDIT_TOOLS:
            tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
            target = tool_input.get("file_path") or tool_input.get("notebook_path")
            if target:
                # realpath on both sides: git reports resolved paths (/private/tmp, not /tmp).
                agent_refs[file_ref(os.path.realpath(str(target)), key=key)] = current
        elif hook == "Stop":
            snap = snapshot(cwd, key=key)
            if snap is not None:
                state["stop_snapshot"], state["stop_at"] = snap, current
        elif hook == "UserPromptSubmit" or (hook == "SessionStart" and str(payload.get("source") or "startup") == "startup"):
            before = state.pop("stop_snapshot", None)
            if before:
                after = snapshot(cwd, key=key)
                if after is not None:
                    between = compare(before, after, agent_refs=set(agent_refs))
                    between["gap_seconds"] = round(max(0.0, current - float(state.get("stop_at") or current)), 1)
                    for event in events:
                        if event.get("operation") == "run_started":
                            event["between_turns"] = between
                            break
        state["agent_refs"] = agent_refs
        _save(path, state, current)


__all__ = ["compare", "enabled", "process_claude_hook", "prune", "snapshot"]
