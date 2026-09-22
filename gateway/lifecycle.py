from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from shared.time_utils import normalize_timestamp
from .db import GatewayDB


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_lifecycle (
  organization_id TEXT PRIMARY KEY,
  retention_days INTEGER,
  updated_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = SQLITE_SCHEMA

MAX_RETENTION_DAYS = 36500


def _now() -> str:
    return normalize_timestamp(datetime.now(timezone.utc).isoformat())


def init_lifecycle_schema(db: GatewayDB) -> None:
    """Idempotently add lifecycle configuration without changing evidence rows."""
    with db.connect() as conn:
        schema = POSTGRES_SCHEMA if db.is_postgres else SQLITE_SCHEMA
        if db.is_postgres:
            for statement in [x.strip() for x in schema.split(";") if x.strip()]:
                conn.execute(statement)
        else:
            conn.executescript(schema)


def normalize_retention_days(value: int | None) -> int | None:
    if value is None:
        return None
    days = int(value)
    if days < 1 or days > MAX_RETENTION_DAYS:
        raise ValueError(f"retention_days must be between 1 and {MAX_RETENTION_DAYS}, or null to disable")
    return days


def set_retention_policy(db: GatewayDB, organization_id: str, retention_days: int | None) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    if not org:
        raise ValueError("organization_id is required")
    days = normalize_retention_days(retention_days)
    now = _now()
    with db.connect() as conn:
        if db.is_postgres:
            db._execute(
                conn,
                """INSERT INTO organization_lifecycle(organization_id, retention_days, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(organization_id) DO UPDATE
                   SET retention_days = EXCLUDED.retention_days, updated_at = EXCLUDED.updated_at""",
                (org, days, now),
            )
        else:
            db._execute(
                conn,
                """INSERT INTO organization_lifecycle(organization_id, retention_days, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(organization_id) DO UPDATE
                   SET retention_days = excluded.retention_days, updated_at = excluded.updated_at""",
                (org, days, now),
            )
    return get_retention_policy(db, org)


def get_retention_policy(
    db: GatewayDB,
    organization_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    with db.connect() as conn:
        cur = db._execute(
            conn,
            "SELECT retention_days, updated_at FROM organization_lifecycle WHERE organization_id = ?",
            (org,),
        )
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else None
    data = db._row(row, columns)
    days = normalize_retention_days(data.get("retention_days")) if data and data.get("retention_days") is not None else None
    cutoff = None
    if days is not None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = normalize_timestamp((current.astimezone(timezone.utc) - timedelta(days=days)).isoformat())
    return {
        "organization_id": org,
        "retention_days": days,
        "enabled": days is not None,
        "cutoff": cutoff,
        "updated_at": str(data.get("updated_at") or "") if data else "",
        "physical_delete_on_ingest": days is not None,
        "audit_log_retained": True,
    }


def retention_cutoff(db: GatewayDB, organization_id: str) -> str | None:
    return get_retention_policy(db, organization_id).get("cutoff")


def effective_since(db: GatewayDB, organization_id: str, requested_since: str | None) -> str | None:
    """Apply the org retention floor without weakening a caller's stricter bound."""
    cutoff = retention_cutoff(db, organization_id)
    requested = normalize_timestamp(requested_since) if requested_since else None
    if cutoff and requested:
        return max(cutoff, requested)
    return cutoff or requested


def _evidence_filter(
    organization_id: str,
    *,
    before: str | None = None,
    after: str | None = None,
    actor_id: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    clauses = ["organization_id = ?"]
    params: list[Any] = [str(organization_id)]
    if before:
        clauses.append("observed_at < ?")
        params.append(normalize_timestamp(before))
    if after:
        clauses.append("observed_at >= ?")
        params.append(normalize_timestamp(after))
    if actor_id:
        clauses.append("actor_id = ?")
        params.append(str(actor_id))
    if device_id:
        clauses.append("device_id = ?")
        params.append(str(device_id))
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id))
    if event_type:
        clauses.append("event_type = ?")
        params.append(str(event_type))
    return " AND ".join(clauses), tuple(params)


def count_evidence(
    db: GatewayDB,
    organization_id: str,
    **filters: Any,
) -> int:
    where, params = _evidence_filter(organization_id, **filters)
    with db.connect() as conn:
        cur = db._execute(conn, f"SELECT COUNT(*) AS count FROM evidence_events WHERE {where}", params)
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else None
    data = db._row(row, columns)
    return int(data.get("count", 0) or 0)


def delete_evidence(
    db: GatewayDB,
    organization_id: str,
    **filters: Any,
) -> int:
    where, params = _evidence_filter(organization_id, **filters)
    with db.connect() as conn:
        cur = db._execute(conn, f"DELETE FROM evidence_events WHERE {where}", params)
        return max(0, int(cur.rowcount or 0))


def apply_retention(
    db: GatewayDB,
    organization_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    policy = get_retention_policy(db, organization_id)
    cutoff = policy.get("cutoff")
    if not cutoff:
        return {
            **policy,
            "candidate_rows": 0,
            "deleted_rows": 0,
            "dry_run": bool(dry_run),
        }
    candidates = count_evidence(db, organization_id, before=cutoff)
    deleted = 0 if dry_run else delete_evidence(db, organization_id, before=cutoff)
    return {
        **policy,
        "candidate_rows": candidates,
        "deleted_rows": deleted,
        "dry_run": bool(dry_run),
    }
