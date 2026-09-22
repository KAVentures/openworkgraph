from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import Principal, token_hash
from .policy import DEFAULT_ORGANIZATION_POLICY, normalize_policy


SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS evidence_events (
  event_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  schema_version TEXT NOT NULL DEFAULT '1.0',
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  sensor_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'desktop',
  session_id TEXT NOT NULL DEFAULT '',
  app TEXT,
  window_title TEXT,
  event_type TEXT NOT NULL,
  duration_seconds REAL NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  ingested_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_gateway_org_time ON evidence_events(organization_id, observed_at, event_id);
CREATE INDEX IF NOT EXISTS idx_gateway_org_actor_time ON evidence_events(organization_id, actor_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_gateway_org_session_time ON evidence_events(organization_id, session_id, observed_at);
CREATE TABLE IF NOT EXISTS access_tokens (
  token_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  token_type TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  scopes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_tokens_org ON access_tokens(organization_id, token_type);
CREATE TABLE IF NOT EXISTS organization_policies (
  organization_id TEXT PRIMARY KEY,
  policy_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  action TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_gateway_audit_org_time ON audit_log(organization_id, observed_at);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_events (
  event_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  schema_version TEXT NOT NULL DEFAULT '1.0',
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  sensor_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'desktop',
  session_id TEXT NOT NULL DEFAULT '',
  app TEXT,
  window_title TEXT,
  event_type TEXT NOT NULL,
  duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  ingested_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_gateway_org_time ON evidence_events(organization_id, observed_at, event_id);
CREATE INDEX IF NOT EXISTS idx_gateway_org_actor_time ON evidence_events(organization_id, actor_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_gateway_org_session_time ON evidence_events(organization_id, session_id, observed_at);
CREATE TABLE IF NOT EXISTS access_tokens (
  token_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  token_type TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  scopes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_tokens_org ON access_tokens(organization_id, token_type);
CREATE TABLE IF NOT EXISTS organization_policies (
  organization_id TEXT PRIMARY KEY,
  policy_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  observed_at TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  action TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_gateway_audit_org_time ON audit_log(organization_id, observed_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GatewayDB:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.sqlite_path: Path | None = None
        self.is_postgres = database_url.startswith(("postgres://", "postgresql://"))
        if not self.is_postgres:
            value = database_url.removeprefix("sqlite:///")
            self.sqlite_path = Path(value)
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError("PostgreSQL Gateway support requires `pip install '.[gateway]'`") from exc
            conn = psycopg.connect(self.database_url)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        else:
            assert self.sqlite_path is not None
            conn = sqlite3.connect(self.sqlite_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            if self.is_postgres:
                for statement in [x.strip() for x in POSTGRES_SCHEMA.split(";") if x.strip()]:
                    conn.execute(statement)
            else:
                conn.executescript(SQLITE_SCHEMA)

    def _execute(self, conn, sql: str, params: tuple[Any, ...] = ()):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return conn.execute(sql, params)

    @staticmethod
    def _row(row: Any, columns: list[str] | None = None) -> dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if hasattr(row, "keys"):
            return dict(row)
        if columns:
            return dict(zip(columns, row))
        return {}

    def put_token(
        self,
        *,
        token_id: str,
        token: str,
        token_type: str,
        organization_id: str,
        actor_id: str = "",
        device_id: str = "",
        scopes: set[str],
        replace_device: bool = False,
    ) -> None:
        with self.connect() as conn:
            if replace_device and token_type == "device":
                self._execute(
                    conn,
                    "UPDATE access_tokens SET revoked_at = ? WHERE organization_id = ? AND device_id = ? AND token_type = 'device' AND revoked_at IS NULL",
                    (_now(), organization_id, device_id),
                )
            self._execute(
                conn,
                """INSERT INTO access_tokens(token_id, token_hash, token_type, organization_id, actor_id, device_id, scopes_json, created_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (token_id, token_hash(token), token_type, organization_id, actor_id, device_id, json.dumps(sorted(scopes)), _now()),
            )

    def authenticate(self, token: str) -> Principal | None:
        if not token:
            return None
        with self.connect() as conn:
            cur = self._execute(
                conn,
                "SELECT token_id, token_type, organization_id, actor_id, device_id, scopes_json FROM access_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash(token),),
            )
            row = cur.fetchone()
            columns = [d[0] for d in cur.description] if cur.description else None
        data = self._row(row, columns)
        if not data:
            return None
        try:
            scopes = frozenset(json.loads(data.get("scopes_json") or "[]"))
        except Exception:
            scopes = frozenset()
        return Principal(
            token_id=str(data["token_id"]),
            token_type=str(data["token_type"]),
            organization_id=str(data["organization_id"]),
            actor_id=str(data.get("actor_id") or ""),
            device_id=str(data.get("device_id") or ""),
            scopes=scopes,
        )

    def revoke_token(self, token_id: str) -> bool:
        with self.connect() as conn:
            cur = self._execute(
                conn,
                "UPDATE access_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
                (_now(), token_id),
            )
            return bool(cur.rowcount)

    def insert_events(self, principal: Principal, events: list[dict[str, Any]]) -> tuple[int, list[str]]:
        inserted = 0
        acknowledged: list[str] = []
        now = _now()
        with self.connect() as conn:
            for event in events:
                event_id = str(event.get("event_id") or "")
                if not event_id:
                    continue
                acknowledged.append(event_id)
                params = (
                    event_id,
                    str(event.get("observed_at") or now),
                    str(event.get("schema_version") or "1.0"),
                    principal.organization_id,
                    principal.actor_id,
                    principal.device_id,
                    str(event.get("sensor_id") or ""),
                    str(event.get("source") or "desktop"),
                    str(event.get("session_id") or ""),
                    event.get("app"),
                    event.get("window_title"),
                    str(event.get("event_type") or "unknown"),
                    float(event.get("duration_seconds") or 0),
                    json.dumps(event.get("metadata") or {}, ensure_ascii=False),
                    now,
                )
                sql = (
                    """INSERT INTO evidence_events(event_id, observed_at, schema_version, organization_id, actor_id, device_id, sensor_id, source, session_id, app, window_title, event_type, duration_seconds, metadata_json, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(organization_id, event_id) DO NOTHING"""
                    if self.is_postgres
                    else """INSERT OR IGNORE INTO evidence_events(event_id, observed_at, schema_version, organization_id, actor_id, device_id, sensor_id, source, session_id, app, window_title, event_type, duration_seconds, metadata_json, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
                )
                cur = self._execute(conn, sql, params)
                inserted += max(0, int(cur.rowcount or 0))
        return inserted, acknowledged

    def trace_rows(
        self,
        *,
        organization_id: str,
        since: str | None = None,
        until: str | None = None,
        after_at: str | None = None,
        after_event_id: str | None = None,
        query: str | None = None,
        actor_id: str | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
        event_type: str | None = None,
        limit: int = 101,
    ) -> list[dict[str, Any]]:
        clauses = ["organization_id = ?"]
        params: list[Any] = [organization_id]
        if since:
            clauses.append("observed_at >= ?")
            params.append(since)
        if until:
            clauses.append("observed_at <= ?")
            params.append(until)
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if query:
            like = f"%{query}%"
            clauses.append("(COALESCE(app,'') LIKE ? OR COALESCE(window_title,'') LIKE ? OR event_type LIKE ? OR metadata_json LIKE ?)")
            params.extend([like, like, like, like])
        if after_at:
            clauses.append("(observed_at > ? OR (observed_at = ? AND event_id > ?))")
            params.extend([after_at, after_at, after_event_id or ""])
        sql = f"SELECT * FROM evidence_events WHERE {' AND '.join(clauses)} ORDER BY observed_at ASC, event_id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 5001)))
        with self.connect() as conn:
            cur = self._execute(conn, sql, tuple(params))
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else None
        return [self._row(row, columns) for row in rows]

    def recent_rows(
        self,
        *,
        organization_id: str,
        actor_id: str | None = None,
        device_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["organization_id = ?"]
        params: list[Any] = [organization_id]
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        sql = f"SELECT * FROM evidence_events WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC, event_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as conn:
            cur = self._execute(conn, sql, tuple(params))
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else None
        values = [self._row(row, columns) for row in rows]
        values.reverse()
        return values

    def set_policy(self, organization_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_policy(policy)
        payload = json.dumps(normalized, ensure_ascii=False)
        with self.connect() as conn:
            if self.is_postgres:
                self._execute(
                    conn,
                    """INSERT INTO organization_policies(organization_id, policy_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(organization_id) DO UPDATE SET policy_json = EXCLUDED.policy_json, updated_at = EXCLUDED.updated_at""",
                    (organization_id, payload, _now()),
                )
            else:
                self._execute(
                    conn,
                    """INSERT INTO organization_policies(organization_id, policy_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(organization_id) DO UPDATE SET policy_json = excluded.policy_json, updated_at = excluded.updated_at""",
                    (organization_id, payload, _now()),
                )
        return normalized

    def get_policy(self, organization_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            cur = self._execute(conn, "SELECT policy_json FROM organization_policies WHERE organization_id = ?", (organization_id,))
            row = cur.fetchone()
            columns = [d[0] for d in cur.description] if cur.description else None
        data = self._row(row, columns)
        if not data:
            return dict(DEFAULT_ORGANIZATION_POLICY)
        try:
            return normalize_policy(json.loads(data.get("policy_json") or "{}"))
        except Exception:
            return dict(DEFAULT_ORGANIZATION_POLICY)

    def audit(
        self,
        *,
        organization_id: str,
        principal_id: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            self._execute(
                conn,
                "INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json) VALUES (?, ?, ?, ?, ?)",
                (_now(), organization_id, principal_id, action, json.dumps(details or {}, ensure_ascii=False)),
            )

    def audit_rows(self, organization_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cur = self._execute(
                conn,
                "SELECT observed_at, principal_id, action, details_json FROM audit_log WHERE organization_id = ? ORDER BY id DESC LIMIT ?",
                (organization_id, max(1, min(int(limit), 1000))),
            )
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else None
        result: list[dict[str, Any]] = []
        for row in rows:
            data = self._row(row, columns)
            try:
                data["details"] = json.loads(data.pop("details_json") or "{}")
            except Exception:
                data["details"] = {}
            result.append(data)
        return result
