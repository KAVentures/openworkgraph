from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.time_utils import normalize_timestamp
from .db import GatewayDB
from .settings import GatewaySettings


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_lifecycle (
  organization_id TEXT PRIMARY KEY,
  retention_days INTEGER,
  updated_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = SQLITE_SCHEMA

MAX_RETENTION_DAYS = 36500
_INITIALIZED_POSTGRES_DATABASES: set[str] = set()
_SCHEMA_LOCK = threading.Lock()


def _now() -> str:
    return normalize_timestamp(datetime.now(timezone.utc).isoformat())


def init_lifecycle_schema(db: GatewayDB) -> None:
    """Idempotently add lifecycle configuration without changing evidence rows.

    SQLite development databases remain fully idempotent on every call. PostgreSQL
    initialization is cached by database URL after the first successful DDL pass so
    normal workflow-trace reads do not repeatedly execute CREATE TABLE statements.
    """
    if db.is_postgres:
        key = db.database_url
        if key in _INITIALIZED_POSTGRES_DATABASES:
            return
        with _SCHEMA_LOCK:
            if key in _INITIALIZED_POSTGRES_DATABASES:
                return
            with db.connect() as conn:
                for statement in [x.strip() for x in POSTGRES_SCHEMA.split(";") if x.strip()]:
                    conn.execute(statement)
            _INITIALIZED_POSTGRES_DATABASES.add(key)
        return

    with db.connect() as conn:
        conn.executescript(SQLITE_SCHEMA)


def normalize_retention_days(value: int | None) -> int | None:
    if value is None:
        return None
    days = int(value)
    if days < 1 or days > MAX_RETENTION_DAYS:
        raise ValueError(f"retention_days must be between 1 and {MAX_RETENTION_DAYS}, or null to disable")
    return days


def set_retention_policy(db: GatewayDB, organization_id: str, retention_days: int | None) -> dict[str, Any]:
    init_lifecycle_schema(db)
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
    init_lifecycle_schema(db)
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
        "physical_cleanup": "explicit_or_scheduled",
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


def count_evidence(db: GatewayDB, organization_id: str, **filters: Any) -> int:
    init_lifecycle_schema(db)
    where, params = _evidence_filter(organization_id, **filters)
    with db.connect() as conn:
        cur = db._execute(conn, f"SELECT COUNT(*) AS count FROM evidence_events WHERE {where}", params)
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else None
    data = db._row(row, columns)
    return int(data.get("count", 0) or 0)


def delete_evidence(db: GatewayDB, organization_id: str, **filters: Any) -> int:
    init_lifecycle_schema(db)
    where, params = _evidence_filter(organization_id, **filters)
    with db.connect() as conn:
        cur = db._execute(conn, f"DELETE FROM evidence_events WHERE {where}", params)
        return max(0, int(cur.rowcount or 0))


def apply_retention(db: GatewayDB, organization_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    policy = get_retention_policy(db, organization_id)
    cutoff = policy.get("cutoff")
    if not cutoff:
        return {**policy, "candidate_rows": 0, "deleted_rows": 0, "dry_run": bool(dry_run)}
    candidates = count_evidence(db, organization_id, before=cutoff)
    deleted = 0 if dry_run else delete_evidence(db, organization_id, before=cutoff)
    return {
        **policy,
        "candidate_rows": candidates,
        "deleted_rows": deleted,
        "dry_run": bool(dry_run),
    }


def _database() -> GatewayDB:
    settings = GatewaySettings.from_env()
    db = GatewayDB(settings.database_url)
    db.init()
    init_lifecycle_schema(db)
    return db


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenWorkGraph Gateway evidence lifecycle administration")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show retention state for one organization")
    status.add_argument("--organization", required=True)

    set_retention = sub.add_parser("set-retention", help="Set or disable organization retention")
    set_retention.add_argument("--organization", required=True)
    group = set_retention.add_mutually_exclusive_group(required=True)
    group.add_argument("--days", type=int)
    group.add_argument("--disable", action="store_true")

    apply_cmd = sub.add_parser("apply-retention", help="Preview or physically delete evidence older than the retention cutoff")
    apply_cmd.add_argument("--organization", required=True)
    apply_cmd.add_argument("--execute", action="store_true")
    apply_cmd.add_argument("--confirm", default="")

    purge = sub.add_parser("purge", help="Preview or delete organization evidence matching explicit selectors")
    purge.add_argument("--organization", required=True)
    purge.add_argument("--before")
    purge.add_argument("--after")
    purge.add_argument("--actor")
    purge.add_argument("--device")
    purge.add_argument("--session")
    purge.add_argument("--event-type")
    purge.add_argument("--all-evidence", action="store_true")
    purge.add_argument("--execute", action="store_true")
    purge.add_argument("--confirm", default="")

    args = parser.parse_args()
    db = _database()
    org = str(args.organization or "").strip()
    if not org:
        parser.error("--organization is required")

    if args.command == "status":
        _print(get_retention_policy(db, org))
        return

    if args.command == "set-retention":
        days = None if args.disable else args.days
        previous = get_retention_policy(db, org)
        result = set_retention_policy(db, org, days)
        db.audit(
            organization_id=org,
            principal_id="gateway-lifecycle-cli",
            action="lifecycle.retention.updated",
            details={"previous_retention_days": previous.get("retention_days"), "retention_days": result.get("retention_days")},
        )
        _print(result)
        return

    if args.command == "apply-retention":
        if args.execute and args.confirm != f"APPLY {org}":
            parser.error(f"destructive apply requires --confirm 'APPLY {org}'")
        result = apply_retention(db, org, dry_run=not args.execute)
        db.audit(
            organization_id=org,
            principal_id="gateway-lifecycle-cli",
            action="lifecycle.retention.applied" if args.execute else "lifecycle.retention.preview",
            details={
                "retention_days": result.get("retention_days"),
                "candidate_rows": result.get("candidate_rows", 0),
                "deleted_rows": result.get("deleted_rows", 0),
            },
        )
        _print(result)
        return

    filters = {
        "before": args.before,
        "after": args.after,
        "actor_id": args.actor,
        "device_id": args.device,
        "session_id": args.session,
        "event_type": args.event_type,
    }
    restrictive = any(value for value in filters.values())
    if args.all_evidence and restrictive:
        parser.error("--all-evidence cannot be combined with other purge selectors")
    if not args.all_evidence and not restrictive:
        parser.error("purge requires at least one selector or --all-evidence")
    if args.execute and args.confirm != f"DELETE {org}":
        parser.error(f"destructive purge requires --confirm 'DELETE {org}'")
    selected = {} if args.all_evidence else filters
    candidates = count_evidence(db, org, **selected)
    deleted = delete_evidence(db, org, **selected) if args.execute else 0
    selector_types = [key for key, value in filters.items() if value]
    db.audit(
        organization_id=org,
        principal_id="gateway-lifecycle-cli",
        action="evidence.purged" if args.execute else "evidence.purge.preview",
        details={
            "all_evidence": bool(args.all_evidence),
            "selector_types": selector_types,
            "candidate_rows": candidates,
            "deleted_rows": deleted,
        },
    )
    _print({
        "organization_id": org,
        "dry_run": not args.execute,
        "all_evidence": bool(args.all_evidence),
        "candidate_rows": candidates,
        "deleted_rows": deleted,
        "audit_log_deleted": False,
    })


if __name__ == "__main__":
    main()
