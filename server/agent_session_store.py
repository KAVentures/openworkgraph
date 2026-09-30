from __future__ import annotations

"""Local-first agent session continuity store.

This module deliberately keeps conversational session content out of the canonical
``events`` table. Structural agent evidence continues to use the existing event
pipeline; optional visible user/assistant messages live in separate tables with
separate capture, AI-read, retention and Gateway-sharing permissions.
"""

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import DATA_DIR, connect
from .privacy_pipeline import learn_persistent_identities
from .ai_context import redact_contextually
from .local_auth import _read_or_create_secret

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
  session_ref TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  client_id TEXT NOT NULL,
  workspace_ref TEXT NOT NULL DEFAULT '',
  native_file_ref TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  visible_message_count INTEGER NOT NULL DEFAULT 0,
  message_capture_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_updated ON agent_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_workspace ON agent_sessions(workspace_ref, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_source ON agent_sessions(source, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_session_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_ref TEXT NOT NULL UNIQUE,
  session_ref TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(session_ref) REFERENCES agent_sessions(session_ref) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_session_messages_session ON agent_session_messages(session_ref, ordinal, id);
CREATE INDEX IF NOT EXISTS idx_agent_session_messages_time ON agent_session_messages(observed_at);
"""

_POLICY_PATH = DATA_DIR / "agent_session_policy.json"
_DEFAULT_POLICY: dict[str, Any] = {
    "native_session_observation_enabled": False,
    "capture_visible_messages": False,
    "allow_ai_read_visible_messages": False,
    "message_retention_days": 30,
    "allow_gateway_session_messages": False,
    "sources": {"claude_code": True, "codex": True},
}
_ALLOWED_SOURCES = {"claude_code", "codex"}
_ALLOWED_ROLES = {"user", "assistant"}
_SESSION_REF_RE = re.compile(r"^as:[0-9a-f]{20}$")
_MESSAGE_REF_RE = re.compile(r"^am:[0-9a-f]{24}$")
_MAX_MESSAGE_CHARS = 20_000
_MAX_SESSION_MESSAGES = 50_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _secret() -> bytes:
    return _read_or_create_secret(".agent_session_ref_key").encode("utf-8")


def _opaque(prefix: str, value: str, *, chars: int) -> str:
    digest = hmac.new(_secret(), value.encode("utf-8", errors="ignore"), hashlib.sha256).hexdigest()[:chars]
    return f"{prefix}:{digest}"


def session_ref(source: str, native_session_id: Any, native_file: Any = "") -> str:
    src = str(source or "").strip().lower()
    raw = str(native_session_id or "").strip() or str(native_file or "").strip()
    if src not in _ALLOWED_SOURCES or not raw:
        return ""
    return _opaque("as", f"{src}|{raw}", chars=20)


def native_file_ref(path: Any) -> str:
    raw = str(path or "").strip()
    return _opaque("af", raw, chars=20) if raw else ""


def message_ref(session: str, *, ordinal: int, observed_at: str, role: str, native_fingerprint: str = "") -> str:
    base = f"{session}|{int(ordinal)}|{observed_at}|{role}|{native_fingerprint}"
    return _opaque("am", base, chars=24)


def init_agent_session_store() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
    cleanup_agent_session_messages()


def _normalize_policy(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = json.loads(json.dumps(_DEFAULT_POLICY))
    for key in ("native_session_observation_enabled", "capture_visible_messages", "allow_ai_read_visible_messages", "allow_gateway_session_messages"):
        if key in source:
            result[key] = bool(source[key])
    try:
        days = int(source.get("message_retention_days", result["message_retention_days"]))
    except Exception:
        days = int(result["message_retention_days"])
    result["message_retention_days"] = max(1, min(days, 3650))
    raw_sources = source.get("sources")
    if isinstance(raw_sources, dict):
        for name in _ALLOWED_SOURCES:
            if name in raw_sources:
                result["sources"][name] = bool(raw_sources[name])
    # Capturing visible messages necessarily enables the native session sensor.
    # Structural-only native observation can remain enabled without messages.
    if result["capture_visible_messages"]:
        result["native_session_observation_enabled"] = True
    # Reading/sharing content cannot be on when content capture itself is off.
    if not result["capture_visible_messages"]:
        result["allow_ai_read_visible_messages"] = False
        result["allow_gateway_session_messages"] = False
    return result


def read_agent_session_policy() -> dict[str, Any]:
    try:
        value = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        value = {}
    return _normalize_policy(value)


def write_agent_session_policy(value: dict[str, Any]) -> dict[str, Any]:
    policy = _normalize_policy(value)
    _POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _POLICY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, _POLICY_PATH)
    try:
        os.chmod(_POLICY_PATH, 0o600)
    except Exception:
        pass
    cleanup_agent_session_messages()
    return policy


def _safe_message_text(text: Any) -> str:
    raw = str(text or "")[:_MAX_MESSAGE_CHARS]
    if not raw.strip():
        return ""
    # Learn only high-confidence aliases, then apply OWG's full contextual
    # presentation redaction before persistence. Any failure drops the content.
    try:
        learn_persistent_identities({"message": raw})
        protected = redact_contextually({"message": raw})
        value = str((protected or {}).get("message") or "") if isinstance(protected, dict) else ""
        return value[:_MAX_MESSAGE_CHARS]
    except Exception:
        return ""


def upsert_session(
    *,
    session_ref_value: str,
    source: str,
    client_id: str,
    workspace_ref: str = "",
    native_file_ref_value: str = "",
    started_at: str,
    updated_at: str,
    message_capture_enabled: bool,
) -> None:
    if not _SESSION_REF_RE.fullmatch(str(session_ref_value or "")):
        return
    src = str(source or "").strip().lower()
    if src not in _ALLOWED_SOURCES:
        return
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_sessions(
              session_ref, source, client_id, workspace_ref, native_file_ref,
              started_at, updated_at, first_seen_at, last_seen_at,
              message_capture_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_ref) DO UPDATE SET
              workspace_ref = CASE WHEN excluded.workspace_ref != '' THEN excluded.workspace_ref ELSE agent_sessions.workspace_ref END,
              updated_at = CASE WHEN excluded.updated_at > agent_sessions.updated_at THEN excluded.updated_at ELSE agent_sessions.updated_at END,
              last_seen_at = excluded.last_seen_at,
              message_capture_enabled = excluded.message_capture_enabled
            """,
            (
                session_ref_value, src, str(client_id or "")[:80], str(workspace_ref or "")[:64],
                str(native_file_ref_value or "")[:64], str(started_at or updated_at or now)[:80],
                str(updated_at or started_at or now)[:80], now, now, 1 if message_capture_enabled else 0,
            ),
        )


def insert_visible_messages(session: str, messages: Iterable[dict[str, Any]]) -> int:
    policy = read_agent_session_policy()
    if not policy.get("capture_visible_messages") or not _SESSION_REF_RE.fullmatch(str(session or "")):
        return 0
    inserted = 0
    with connect() as conn:
        current = conn.execute(
            "SELECT COUNT(*) FROM agent_session_messages WHERE session_ref = ?", (session,)
        ).fetchone()
        count = int(current[0] if current else 0)
        for item in messages:
            if count >= _MAX_SESSION_MESSAGES or not isinstance(item, dict):
                break
            role = str(item.get("role") or "").strip().lower()
            if role not in _ALLOWED_ROLES:
                continue
            observed_at = str(item.get("observed_at") or "").strip()[:80]
            if not observed_at:
                continue
            try:
                ordinal = max(0, int(item.get("ordinal") or 0))
            except Exception:
                ordinal = 0
            safe = _safe_message_text(item.get("content"))
            if not safe:
                continue
            fingerprint = str(item.get("native_fingerprint") or "")[:128]
            ref = message_ref(session, ordinal=ordinal, observed_at=observed_at, role=role, native_fingerprint=fingerprint)
            if not _MESSAGE_REF_RE.fullmatch(ref):
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO agent_session_messages(message_ref, session_ref, observed_at, role, content, ordinal)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ref, session, observed_at, role, safe, ordinal),
            )
            if cur.rowcount:
                inserted += 1
                count += 1
        if inserted:
            conn.execute(
                "UPDATE agent_sessions SET visible_message_count = visible_message_count + ?, last_seen_at = ? WHERE session_ref = ?",
                (inserted, _now(), session),
            )
    return inserted


def cleanup_agent_session_messages() -> int:
    policy = read_agent_session_policy()
    days = int(policy.get("message_retention_days") or 30)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        cur = conn.execute("DELETE FROM agent_session_messages WHERE observed_at < ?", (cutoff,))
        deleted = int(cur.rowcount or 0)
        conn.execute(
            """UPDATE agent_sessions
               SET visible_message_count = (SELECT COUNT(*) FROM agent_session_messages m WHERE m.session_ref = agent_sessions.session_ref)"""
        )
    return deleted


def delete_all_agent_session_messages() -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM agent_session_messages")
        deleted = int(cur.rowcount or 0)
        conn.execute("UPDATE agent_sessions SET visible_message_count = 0, message_capture_enabled = 0")
    return deleted


def list_sessions(*, source: str = "", workspace_ref: str = "", limit: int = 50) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(str(source).strip().lower())
    if workspace_ref:
        clauses.append("workspace_ref = ?")
        params.append(str(workspace_ref).strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_sessions" + where + " ORDER BY updated_at DESC LIMIT ?", tuple(params)
        ).fetchall()
    return [
        {
            "session_ref": row["session_ref"],
            "source": row["source"],
            "client_id": row["client_id"],
            "workspace_ref": row["workspace_ref"] or None,
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "visible_message_count": int(row["visible_message_count"] or 0),
            "message_capture_enabled": bool(row["message_capture_enabled"]),
        }
        for row in rows
    ]


def session_messages(session: str, *, limit: int = 40) -> list[dict[str, Any]]:
    if not _SESSION_REF_RE.fullmatch(str(session or "")):
        return []
    bounded = max(1, min(int(limit), 200))
    with connect() as conn:
        rows = conn.execute(
            """SELECT message_ref, observed_at, role, content, ordinal
               FROM agent_session_messages WHERE session_ref = ?
               ORDER BY ordinal DESC, id DESC LIMIT ?""",
            (session, bounded),
        ).fetchall()
    items = [dict(row) for row in reversed(rows)]
    return items


def latest_handoff_session(*, source: str = "", exclude_source: str = "", workspace_ref: str = "") -> dict[str, Any] | None:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(str(source).strip().lower())
    if exclude_source:
        clauses.append("source != ?")
        params.append(str(exclude_source).strip().lower())
    if workspace_ref:
        clauses.append("workspace_ref = ?")
        params.append(str(workspace_ref).strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_sessions" + where + " ORDER BY updated_at DESC LIMIT 1", tuple(params)).fetchone()
    if row is None:
        return None
    return {
        "session_ref": row["session_ref"],
        "source": row["source"],
        "client_id": row["client_id"],
        "workspace_ref": row["workspace_ref"] or None,
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "visible_message_count": int(row["visible_message_count"] or 0),
    }


__all__ = [
    "init_agent_session_store", "read_agent_session_policy", "write_agent_session_policy",
    "session_ref", "native_file_ref", "upsert_session", "insert_visible_messages",
    "cleanup_agent_session_messages", "delete_all_agent_session_messages", "list_sessions",
    "session_messages", "latest_handoff_session",
]
