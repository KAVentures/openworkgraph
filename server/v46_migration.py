from __future__ import annotations

"""v0.46 one-time storage hardening.

Re-runs the current identifier sanitizer over existing raw events so credentials
that older versions missed are removed and legacy OCR/payment-card
misclassifications are semantically corrected where the surrounding cue survives.
"""

import json

from contextualizer import contextualize_event
from normalizer import normalize_event
from sensitive_identifiers import sanitize_event_identifiers
from .db import connect, _insert_event, _insert_context, _row_to_event

MIGRATION_KEY = "sensitive_identifiers_v46"


def harden_existing_sensitive_identifiers_v46() -> int:
    changed = 0
    with connect() as conn:
        if conn.execute(
            "SELECT 1 FROM privacy_migrations WHERE migration_key = ?",
            (MIGRATION_KEY,),
        ).fetchone():
            return 0

        existing = conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
        for row in existing:
            raw = _row_to_event(row)
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
            conn.execute("DELETE FROM normalized_events WHERE event_id = ?", (safe.get("event_id"),))
            conn.execute("DELETE FROM context_events WHERE event_id = ?", (safe.get("event_id"),))
            _insert_event(conn, "normalized_events", normalize_event(safe))
            _insert_context(conn, contextualize_event(safe))
            changed += 1

        conn.execute(
            "INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)",
            (MIGRATION_KEY,),
        )
    return changed
