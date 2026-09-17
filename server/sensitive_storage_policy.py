from __future__ import annotations

"""Storage policy for identifiers that should never remain literal at rest.

Names continue to use the downstream presentation layer because they can carry
workflow meaning. High-confidence national/patient/case/account identifiers do
not need their literal value for workflow analysis, so they are pseudonymized
before persistence and legacy rows are migrated once.
"""

import json
from functools import wraps
from typing import Any, Iterable

from sensitive_identifiers import sanitize_event_identifiers

MIGRATION_KEY = "sensitive_identifiers_v40"


def install(db: Any) -> None:
    if getattr(db.insert_events, "_openworkgraph_sensitive_storage_policy", False):
        return

    original_insert_events = db.insert_events
    original_init_db = db.init_db

    def harden_existing_sensitive_identifiers() -> int:
        changed = 0
        with db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM privacy_migrations WHERE migration_key = ?",
                (MIGRATION_KEY,),
            ).fetchone():
                return 0

            existing = conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
            for row in existing:
                raw = db._row_to_event(row)
                safe = sanitize_event_identifiers(raw)
                if safe == raw:
                    continue

                conn.execute(
                    """
                    UPDATE events
                    SET app = ?, window_title = ?, metadata_json = ?
                    WHERE event_id = ?
                    """,
                    (
                        safe.get("app"),
                        safe.get("window_title"),
                        json.dumps(safe.get("metadata") or {}, ensure_ascii=False),
                        safe.get("event_id"),
                    ),
                )
                conn.execute(
                    "DELETE FROM normalized_events WHERE event_id = ?",
                    (safe.get("event_id"),),
                )
                conn.execute(
                    "DELETE FROM context_events WHERE event_id = ?",
                    (safe.get("event_id"),),
                )
                db._insert_event(conn, "normalized_events", db.normalize_event(safe))
                db._insert_context(conn, db.contextualize_event(safe))
                changed += 1

            conn.execute(
                "INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)",
                (MIGRATION_KEY,),
            )
        return changed

    @wraps(original_insert_events)
    def insert_events(events: Iterable[dict[str, Any]]) -> int:
        safe_events = (sanitize_event_identifiers(dict(event)) for event in events)
        return original_insert_events(safe_events)

    @wraps(original_init_db)
    def init_db() -> None:
        original_init_db()
        harden_existing_sensitive_identifiers()

    insert_events._openworkgraph_sensitive_storage_policy = True  # type: ignore[attr-defined]
    init_db._openworkgraph_sensitive_storage_policy = True  # type: ignore[attr-defined]
    db.insert_events = insert_events
    db.init_db = init_db
    db.harden_existing_sensitive_identifiers = harden_existing_sensitive_identifiers
