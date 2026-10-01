from __future__ import annotations

"""Native Claude Code / Codex session-file sensor.

The sensor reads local session files only when agent observation is enabled. It
projects content-free structural facts into the existing canonical agent evidence
pipeline. Visible user/assistant messages are stored separately only when the
explicit session-continuity opt-in is enabled. Thinking/reasoning, tool outputs,
raw provider records and full tool arguments are never persisted.
"""

import glob
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
from typing import Any

from shared.tool_detail import tool_call_detail, workspace_ref
from shared.time_utils import normalize_timestamp
from .agent_ingest import ingest_agent_payloads
from .agent_session_store import (
    _opaque,
    init_agent_session_store,
    native_file_ref,
    read_agent_session_policy,
    write_agent_session_policy,
    session_ref,
    upsert_session,
    insert_visible_messages,
    cleanup_agent_session_messages,
)
from .connections import is_enabled
from .db import DATA_DIR, connect

_STATE_PATH = DATA_DIR / "agent_session_sensor_state.json"
_POLL_SECONDS = 1.5
_MAX_LINE_BYTES = 2 * 1024 * 1024
_MAX_FILES_PER_SOURCE = 3000
_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_STATS: dict[str, Any] = {
    "running": False,
    "files_seen": 0,
    "lines_seen": 0,
    "structural_events": 0,
    "messages_stored": 0,
    "errors": 0,
    "last_error": None,
    "last_scan_at": None,
}

_SOURCE_SPECS = {
    "claude_code": {
        "client_id": "claude_code",
        "framework": "claude-code",
        "agent_name": "Claude Code",
        "provider": "anthropic",
        "pattern": "~/.claude/projects/**/*.jsonl",
    },
    "codex": {
        "client_id": "codex",
        "framework": "codex",
        "agent_name": "Codex",
        "provider": "openai",
        "pattern": "~/.codex/sessions/**/*.jsonl",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_error(stage: str, exc: Exception) -> None:
    """Count a real sensor failure without leaking paths/provider payloads."""
    _STATS["errors"] += 1
    _STATS["last_error"] = f"{str(stage)[:80]}: {type(exc).__name__}"


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, _STATE_PATH)
    try:
        os.chmod(_STATE_PATH, 0o600)
    except Exception:
        pass


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _ts(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return normalize_timestamp(text)
    except Exception:
        return _now()


def _claude_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n\n".join(parts)


def _tool_category(name: str) -> str:
    low = str(name or "").strip().lower()
    if low in {"read", "write", "edit", "multiedit", "apply_patch", "notebookedit", "glob"}:
        return "filesystem" if low != "apply_patch" else "code"
    if low in {"bash", "shell", "terminal", "command"}:
        return "shell"
    if low in {"grep", "search", "web_search", "websearch"}:
        return "search"
    if "browser" in low or low in {"playwright", "computer"}:
        return "browser"
    if "git" in low or low in {"apply_patch"}:
        return "code"
    return "other"


def _paths_from_input(tool_name: str, value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    paths: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        raw = value.get(key)
        if isinstance(raw, str) and raw:
            paths.append(raw)
    if tool_name.lower() in {"glob", "grep"}:
        raw = value.get("path")
        if isinstance(raw, str) and raw:
            paths.append(raw)
    return paths[:8]


def _claude_tool_span(session: str, native_tool_id: Any) -> str:
    raw = str(native_tool_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("at:"):
        return raw
    return _opaque("at", f"claude_code|{session}|span|{raw}", chars=24)


def _structural_payload(
    spec: dict[str, str], *, session: str, observed_at: str, operation: str,
    event_seed: str, status: str = "success", tool_name: str = "",
    tool_category: str = "none", tool_detail: dict[str, Any] | None = None,
    workspace: str = "", model: str = "", span_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": f"native-session:{_fingerprint(event_seed)}",
        "observed_at": observed_at,
        "agent_name": spec["agent_name"],
        "provider": spec["provider"],
        "framework": spec["framework"],
        "session_id": session,
        "run_id": session,
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "tool_name": tool_name,
        "tool_category": tool_category,
        "sensor_id": f"agent:native-session:{spec['client_id']}",
        "device_id": "agent-local",
    }
    if span_id:
        payload["span_id"] = span_id
    if tool_detail:
        payload["tool_detail"] = tool_detail
    if workspace:
        payload["workspace_ref"] = workspace
    if model:
        payload["model"] = str(model)[:200]
    return payload


def _parse_claude(obj: dict[str, Any], spec: dict[str, str], session: str, ordinal: int, workspace: str) -> tuple[list[dict], list[dict]]:
    typ = str(obj.get("type") or "").strip().lower()
    observed = _ts(obj.get("timestamp") or obj.get("ts"))
    native_id = str(obj.get("uuid") or obj.get("messageId") or obj.get("requestId") or ordinal)
    seed = f"claude|{session}|{native_id}|{typ}|{observed}"
    structural: list[dict] = []
    messages: list[dict] = []

    if typ in {"user", "assistant"}:
        role = typ
        raw_content = (obj.get("message") or {}).get("content") if isinstance(obj.get("message"), dict) else obj.get("content")
        visible = _claude_text(raw_content)
        if visible:
            messages.append({"role": role, "content": visible, "observed_at": observed, "ordinal": ordinal, "native_fingerprint": _fingerprint(seed)})
        if typ == "assistant":
            model = (obj.get("message") or {}).get("model") if isinstance(obj.get("message"), dict) else ""
            structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="model_call", event_seed=seed+"|model", workspace=workspace, model=str(model or "")))
        if isinstance(raw_content, list):
            for i, item in enumerate(raw_content):
                if not isinstance(item, dict) or str(item.get("type") or "") != "tool_use":
                    continue
                name = str(item.get("name") or "unknown")[:200]
                tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
                detail: dict[str, Any] = {}
                if name.lower() in {"bash", "shell", "terminal"}:
                    detail = tool_call_detail(command=tool_input.get("command"))
                elif name.lower() == "apply_patch":
                    detail = tool_call_detail(patch=tool_input.get("patch") or tool_input.get("input"))
                else:
                    detail = tool_call_detail(paths=_paths_from_input(name, tool_input), new_file_text=(tool_input.get("content") if name.lower() in {"write", "write_file"} else None))
                native_tool_id = item.get("id") or item.get("tool_use_id")
                structural.append(_structural_payload(
                    spec, session=session, observed_at=observed, operation="tool_call",
                    event_seed=seed+f"|tool|{i}|{name}", tool_name=name,
                    tool_category=_tool_category(name), tool_detail=detail, workspace=workspace,
                    span_id=_claude_tool_span(session, native_tool_id),
                ))
    elif typ == "system":
        subtype = str(obj.get("subtype") or "").lower()
        if subtype == "turn_duration":
            # End-of-turn signal, not necessarily end of the whole session. Keep it
            # as a model-call timing observation rather than claiming run_finished.
            duration_ms = obj.get("durationMs")
            try:
                duration = max(0.0, min(float(duration_ms or 0) / 1000.0, 7 * 86400.0))
            except Exception:
                duration = 0.0
            p = _structural_payload(spec, session=session, observed_at=observed, operation="model_call", event_seed=seed+"|turn", workspace=workspace)
            p["duration_seconds"] = duration
            structural.append(p)
    # Explicitly ignore thinking blocks, tool_result content, last-prompt,
    # summaries, attachment content and raw records.
    return structural, messages


def _codex_message_text(payload: dict[str, Any]) -> str:
    raw = payload.get("message")
    if isinstance(raw, str):
        return raw
    content = payload.get("content")
    if isinstance(content, list):
        return "\n\n".join(str(x.get("text") or "") for x in content if isinstance(x, dict) and isinstance(x.get("text"), str))
    return ""


def _parse_codex(obj: dict[str, Any], spec: dict[str, str], session: str, ordinal: int, workspace: str) -> tuple[list[dict], list[dict]]:
    typ = str(obj.get("type") or "").strip()
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    observed = _ts(obj.get("timestamp"))
    subtype = str(payload.get("type") or "")
    native_id = str(payload.get("id") or payload.get("call_id") or payload.get("turn_id") or ordinal)
    seed = f"codex|{session}|{native_id}|{typ}|{subtype}|{observed}"
    structural: list[dict] = []
    messages: list[dict] = []

    if typ == "session_meta":
        return structural, messages
    if typ == "event_msg":
        if subtype == "task_started":
            structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="run_started", event_seed=seed, status="running", workspace=workspace, model=str(payload.get("model") or "")))
        elif subtype == "task_complete":
            status = "error" if str(payload.get("status") or "").lower() in {"error", "failed", "failure"} else "success"
            structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="run_finished", event_seed=seed, status=status, workspace=workspace))
        elif subtype in {"user_message", "agent_message"}:
            role = "user" if subtype == "user_message" else "assistant"
            text = str(payload.get("message") or payload.get("last_agent_message") or "")
            if text:
                messages.append({"role": role, "content": text, "observed_at": observed, "ordinal": ordinal, "native_fingerprint": _fingerprint(seed)})
            if role == "assistant":
                structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="model_call", event_seed=seed+"|model", workspace=workspace, model=str(payload.get("model") or "")))
        # agent_reasoning/token_count are intentionally ignored here.
        return structural, messages
    if typ == "response_item":
        if subtype == "message":
            role = str(payload.get("role") or "").lower()
            if role in {"user", "assistant"}:
                text = _codex_message_text(payload)
                if text:
                    messages.append({"role": role, "content": text, "observed_at": observed, "ordinal": ordinal, "native_fingerprint": _fingerprint(seed)})
                if role == "assistant":
                    structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="model_call", event_seed=seed+"|model", workspace=workspace))
        elif subtype in {"function_call", "custom_tool_call"}:
            name = str(payload.get("name") or "unknown")[:200]
            args_raw = payload.get("arguments") if subtype == "function_call" else payload.get("input")
            args: Any = args_raw
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {"command": args_raw} if name.lower() in {"shell", "bash", "exec_command"} else {}
            args = args if isinstance(args, dict) else {}
            if name.lower() in {"shell", "bash", "exec_command", "command"}:
                detail = tool_call_detail(command=args.get("command") or args.get("cmd"))
            elif name.lower() in {"apply_patch", "patch"}:
                detail = tool_call_detail(patch=args.get("patch") or args_raw)
            else:
                detail = tool_call_detail(paths=_paths_from_input(name, args), new_file_text=(args.get("content") if name.lower() in {"write", "write_file"} else None))
            structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="tool_call", event_seed=seed+"|tool", tool_name=name, tool_category=_tool_category(name), tool_detail=detail, workspace=workspace))
        elif subtype == "web_search_call":
            structural.append(_structural_payload(spec, session=session, observed_at=observed, operation="tool_call", event_seed=seed+"|search", tool_name="web_search", tool_category="search", workspace=workspace))
        # reasoning and all tool outputs are intentionally ignored.
    return structural, messages


def _source_enabled(source: str, spec: dict[str, str], policy: dict[str, Any]) -> bool:
    # Explicit local opt-in is required; upgrading an existing installation does
    # not silently start reading native agent session files. The existing
    # Connect → Observe switch remains an upper-level off switch if the user has
    # explicitly disabled that client.
    if policy.get("native_session_observation_enabled") is not True:
        return False
    if not bool((policy.get("sources") or {}).get(source, True)):
        return False
    try:
        return is_enabled(spec["client_id"], "observe")
    except Exception:
        return False


def _source_root(spec: dict[str, str]) -> Path:
    pattern = os.path.expanduser(spec["pattern"])
    positions = [pos for token in ("*", "?", "[") if (pos := pattern.find(token)) >= 0]
    prefix = pattern[: min(positions)] if positions else pattern
    root = Path(prefix)
    if prefix and not prefix.endswith(("/", "\\")):
        root = root.parent
    return root.resolve(strict=False)


def _safe_source_file(path: Path, spec: dict[str, str]) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(_source_root(spec))
        return resolved.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def _source_files(spec: dict[str, str]) -> list[Path]:
    pattern = os.path.expanduser(spec["pattern"])
    return [path for raw in glob.glob(pattern, recursive=True) if _safe_source_file(path := Path(raw), spec)]


def _session_and_workspace(source: str, obj: dict[str, Any], path: Path) -> tuple[str, str, str]:
    if source == "claude_code":
        native = str(obj.get("sessionId") or obj.get("session_id") or "").strip()
        cwd = str(obj.get("cwd") or "").strip()
        if not native:
            native = path.stem
    else:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        native = str(payload.get("session_id") or payload.get("id") or obj.get("session_id") or "").strip()
        cwd = str(payload.get("cwd") or obj.get("cwd") or "").strip()
        if not native:
            native = path.stem
    ref = session_ref(source, native, path)
    return ref, workspace_ref(cwd) if cwd else "", native


def _native_sensor_id(client_id: str) -> str:
    return f"agent:native-session:{client_id}"


def _has_richer_equivalent(payload: dict[str, Any]) -> bool:
    """Return true when a non-session adapter already reported this structural step.

    Native session files are a zero-config fallback. Hooks/OTel/SDK traces are
    richer and remain canonical when both observe the same step. Prefer exact
    opaque span identity when the provider supplies one; retain the narrow
    time/tool/workspace fallback for older/provider records without an ID.
    """
    operation = str(payload.get("operation") or "")
    framework = str(payload.get("framework") or "")
    tool_name = str(payload.get("tool_name") or "")
    workspace = str(payload.get("workspace_ref") or "")
    observed = str(payload.get("observed_at") or "")
    own_sensor = str(payload.get("sensor_id") or "")
    session = str(payload.get("session_id") or "")
    span_id = str(payload.get("span_id") or "")
    if not operation or not observed:
        return False

    if session and span_id:
        try:
            with connect() as conn:
                exact_rows = conn.execute(
                    """SELECT sensor_id, metadata_json FROM events
                       WHERE source='agent' AND session_id=? AND event_type=?
                       ORDER BY id DESC LIMIT 200""",
                    (session, f"agent_{operation}"),
                ).fetchall()
            for row in exact_rows:
                sensor = str(row["sensor_id"] or "")
                if not sensor or sensor == own_sensor or sensor.startswith("agent:native-session:"):
                    continue
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    continue
                trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
                if str(trace.get("span_id") or "") == span_id:
                    return True
        except Exception:
            # Fall through to conservative time/tool matching.
            pass

    try:
        with connect() as conn:
            rows = conn.execute(
                """SELECT observed_at, sensor_id, metadata_json FROM events
                   WHERE source='agent' AND event_type=?
                     AND ABS((julianday(observed_at)-julianday(?))*86400.0) <= 2.5
                   ORDER BY id DESC LIMIT 40""",
                (f"agent_{operation}", observed),
            ).fetchall()
    except Exception:
        # Duplicate avoidance must never make observation unavailable. Canonical
        # event-id idempotence still protects the native path itself.
        return False
    for row in rows:
        sensor = str(row["sensor_id"] or "")
        if not sensor or sensor == own_sensor or sensor.startswith("agent:native-session:"):
            continue
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            continue
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        if framework and str(agent.get("framework") or "") != framework:
            continue
        existing_workspace = str(meta.get("workspace_ref") or "")
        if workspace and existing_workspace and existing_workspace != workspace:
            continue
        if operation == "tool_call":
            tool = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
            if tool_name and str(tool.get("name") or "") != tool_name:
                continue
        return True
    return False


def _filter_structural_fallback(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [payload for payload in payloads if not _has_richer_equivalent(payload)]


def _prime_source_to_eof(source: str, spec: dict[str, str], state: dict[str, Any]) -> None:
    for path in _source_files(spec):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = native_file_ref(path)
        previous = state.get(key) if isinstance(state.get(key), dict) else {}
        same_file = (
            previous.get("file_dev") is not None
            and previous.get("file_ino") is not None
            and int(previous.get("file_dev") or 0) == int(getattr(stat, "st_dev", 0) or 0)
            and int(previous.get("file_ino") or 0) == int(getattr(stat, "st_ino", 0) or 0)
        )
        entry = {
            "offset": int(stat.st_size),
            "line_no": int(previous.get("line_no") or 0) if same_file else 0,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "file_dev": int(getattr(stat, "st_dev", 0) or 0),
            "file_ino": int(getattr(stat, "st_ino", 0) or 0),
        }
        if same_file and previous.get("session_ref"):
            entry["session_ref"] = previous["session_ref"]
        if same_file and previous.get("workspace_ref"):
            entry["workspace_ref"] = previous["workspace_ref"]
        state[key] = entry
    state[f"__bootstrap__:{source}"] = True


def _bootstrap_existing_files(source: str, spec: dict[str, str], state: dict[str, Any]) -> bool:
    """Prime existing native session files to EOF on first observation."""
    marker = f"__bootstrap__:{source}"
    if state.get(marker) is True:
        return False
    _prime_source_to_eof(source, spec, state)
    return True


def _process_file(source: str, spec: dict[str, str], path: Path, state: dict[str, Any], policy: dict[str, Any]) -> None:
    file_key = native_file_ref(path)
    is_new_file = file_key not in state
    entry = state.get(file_key) if isinstance(state.get(file_key), dict) else {}
    try:
        stat = path.stat()
    except OSError:
        return
    offset = int(entry.get("offset") or 0)
    line_no = int(entry.get("line_no") or 0)
    current_dev = int(getattr(stat, "st_dev", 0) or 0)
    current_ino = int(getattr(stat, "st_ino", 0) or 0)
    known_identity = entry.get("file_dev") is not None and entry.get("file_ino") is not None
    identity_changed = bool(known_identity and (
        int(entry.get("file_dev") or 0) != current_dev
        or int(entry.get("file_ino") or 0) != current_ino
    ))
    if identity_changed or offset < 0 or offset > stat.st_size:
        # Provider replacement/rotation/truncation: restart from the beginning
        # and reset the ordinal/session carry-forward.
        offset = 0
        line_no = 0
    structural: list[dict] = []
    messages_by_session: dict[str, list[dict[str, Any]]] = {}
    session_meta: dict[str, dict[str, Any]] = {}

    # Codex establishes the session/workspace once in session_meta and later
    # records often omit both. Carry those opaque facts forward only while the
    # physical file identity is unchanged.
    file_session_ref = "" if identity_changed else str(entry.get("session_ref") or "")
    file_workspace_ref = "" if identity_changed else str(entry.get("workspace_ref") or "")

    with path.open("rb") as fh:
        fh.seek(offset)
        while True:
            start = fh.tell()
            raw = fh.readline(_MAX_LINE_BYTES + 1)
            if not raw:
                break
            # A writer may have flushed only part of its next JSONL record.
            # For an ordinary bounded partial record, do not advance: retry it
            # on the next scan. If the bounded read itself already exceeded our
            # maximum, however, this is an oversized physical record rather
            # than a normal partial write. Consume only that physical line in
            # bounded chunks, never parse/persist its contents, advance the
            # cursor, and continue so one hostile/accidental giant record cannot
            # pin observation of the rest of the session file forever.
            if not raw.endswith(b"\n"):
                if len(raw) > _MAX_LINE_BYTES:
                    while raw and not raw.endswith(b"\n"):
                        raw = fh.readline(_MAX_LINE_BYTES + 1)
                    line_no += 1
                    continue
                fh.seek(start)
                break
            if len(raw) > _MAX_LINE_BYTES:
                # Skip oversized complete provider records without trying to
                # parse or persist their contents.
                line_no += 1
                continue
            line_no += 1
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            derived_sref, derived_wref, _native = _session_and_workspace(source, obj, path)
            if source == "codex":
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if str(obj.get("type") or "") == "session_meta":
                    file_session_ref = derived_sref or file_session_ref
                    file_workspace_ref = derived_wref or file_workspace_ref
                sref = file_session_ref or derived_sref
                wref = file_workspace_ref or derived_wref
            else:
                sref, wref = derived_sref, derived_wref
            if not sref:
                continue
            if wref:
                file_workspace_ref = wref

            observed = _ts(obj.get("timestamp") or obj.get("ts"))
            meta = session_meta.setdefault(sref, {"started_at": observed, "updated_at": observed, "workspace_ref": wref})
            if observed < str(meta.get("started_at") or observed):
                meta["started_at"] = observed
            if observed > str(meta.get("updated_at") or observed):
                meta["updated_at"] = observed
            if wref:
                meta["workspace_ref"] = wref
            if source == "claude_code":
                evs, msgs = _parse_claude(obj, spec, sref, line_no, str(meta.get("workspace_ref") or ""))
            else:
                evs, msgs = _parse_codex(obj, spec, sref, line_no, str(meta.get("workspace_ref") or ""))
            structural.extend(evs)
            if msgs:
                messages_by_session.setdefault(sref, []).extend(msgs)
        new_offset = fh.tell()

    # Claude Code session files do not consistently expose a dedicated session
    # start record. For a genuinely new file (never one primed during upgrade)
    # synthesize only the start boundary. We intentionally do not invent a
    # run_finished boundary when the provider has not supplied one.
    if source == "claude_code" and is_new_file and session_meta:
        for sref, meta in session_meta.items():
            structural.insert(0, _structural_payload(
                spec, session=sref, observed_at=str(meta.get("started_at") or _now()),
                operation="run_started", event_seed=f"claude|{sref}|synthetic-run-start",
                status="running", workspace=str(meta.get("workspace_ref") or ""),
            ))
            break

    capture_messages = bool(policy.get("capture_visible_messages"))
    for sref, meta in session_meta.items():
        upsert_session(
            session_ref_value=sref,
            source=source,
            client_id=spec["client_id"],
            workspace_ref=str(meta.get("workspace_ref") or ""),
            native_file_ref_value=file_key,
            started_at=str(meta.get("started_at") or _now()),
            updated_at=str(meta.get("updated_at") or _now()),
            message_capture_enabled=capture_messages,
        )
        if capture_messages:
            _STATS["messages_stored"] += insert_visible_messages(sref, messages_by_session.get(sref, []))

    if structural:
        structural = _filter_structural_fallback(structural)
    if structural:
        result = ingest_agent_payloads(structural)
        _STATS["structural_events"] += int(result.get("inserted") or 0)
    _STATS["lines_seen"] += max(0, line_no - int(entry.get("line_no") or 0))
    state[file_key] = {
        "offset": new_offset, "line_no": line_no, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "file_dev": current_dev, "file_ino": current_ino,
        "session_ref": file_session_ref, "workspace_ref": file_workspace_ref,
    }


def _scan_once_unlocked() -> dict[str, Any]:
    if os.getenv("WORKFLOW_OBSERVER_MODE", "observe") == "demo":
        return status()
    policy = read_agent_session_policy()
    state = _load_state()
    _STATS["last_error"] = None
    try:
        # A first scan can race application startup. Initialize the local session
        # tables before cleanup so bootstrap itself is not reported as an error.
        init_agent_session_store()
        cleanup_agent_session_messages()
    except Exception as exc:
        _record_error("session_store_cleanup", exc)
    for source, spec in _SOURCE_SPECS.items():
        active_marker = f"__effective_enabled__:{source}"
        enabled = _source_enabled(source, spec, policy)
        was_enabled = state.get(active_marker)
        if not enabled:
            state[active_marker] = False
            continue
        if was_enabled is False:
            # Anything written while this source/effective Observe setting was
            # off is outside the capture boundary. Skip it permanently.
            _prime_source_to_eof(source, spec, state)
            state[active_marker] = True
            continue
        if _bootstrap_existing_files(source, spec, state):
            state[active_marker] = True
            continue
        state[active_marker] = True
        files = _source_files(spec)
        files.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0)
        for path in files[-_MAX_FILES_PER_SOURCE:]:
            try:
                _process_file(source, spec, path, state, policy)
                _STATS["files_seen"] += 1
            except Exception as exc:
                _record_error(f"{source}_file", exc)
    _STATS["last_scan_at"] = _now()
    _save_state(state)
    return status()


def scan_once() -> dict[str, Any]:
    with _LOCK:
        return _scan_once_unlocked()


def update_policy_with_boundaries(value: dict[str, Any]) -> dict[str, Any]:
    """Persist policy atomically with native-capture cursor boundaries."""
    with _LOCK:
        previous = read_agent_session_policy()
        policy = write_agent_session_policy(value)
        state = _load_state()
        for source, spec in _SOURCE_SPECS.items():
            marker = f"__effective_enabled__:{source}"
            prev_configured = bool(previous.get("native_session_observation_enabled")) and bool((previous.get("sources") or {}).get(source, True))
            now_configured = bool(policy.get("native_session_observation_enabled")) and bool((policy.get("sources") or {}).get(source, True))
            if not now_configured:
                state[marker] = False
            elif not prev_configured and now_configured:
                _prime_source_to_eof(source, spec, state)
                state[marker] = True
        _save_state(state)
        return policy


def _run() -> None:
    _STATS["running"] = True
    try:
        while not _STOP.is_set():
            try:
                scan_once()
            except Exception as exc:
                _record_error("scan_loop", exc)
            _STOP.wait(_POLL_SECONDS)
    finally:
        _STATS["running"] = False


def start() -> None:
    global _THREAD
    if os.getenv("WORKFLOW_OBSERVER_MODE", "observe") == "demo":
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_run, name="openworkgraph-agent-session-sensor", daemon=True)
        _THREAD.start()


def stop() -> None:
    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.5)
    _THREAD = None


def status() -> dict[str, Any]:
    result = dict(_STATS)
    policy = read_agent_session_policy()
    result["enabled"] = bool(policy.get("native_session_observation_enabled"))
    result["capture_visible_messages"] = bool(policy.get("capture_visible_messages"))
    return result


__all__ = ["start", "stop", "status", "scan_once"]
