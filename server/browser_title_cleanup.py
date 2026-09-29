from __future__ import annotations

"""Explicit local re-run of the current title rule over all stored evidence.

Since v0.108 new evidence is protected automatically before persistence, and
evidence stored earlier is protected once on upgrade (server.db.
protect_existing_titles). This command re-applies the rule on demand.
"""

import json

from browser_title_privacy import minimize_browser_title_at_rest
from contextualizer import contextualize_event
from normalizer import normalize_event

from . import db


def sanitize_existing_browser_titles() -> int:
    changed = 0
    with db.connect() as conn:
        existing = conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
        for row in existing:
            raw = db._row_to_event(row)
            safe = minimize_browser_title_at_rest(raw)
            if safe == raw:
                continue
            conn.execute(
                """
                UPDATE events
                SET window_title = ?, metadata_json = ?
                WHERE event_id = ?
                """,
                (
                    safe.get("window_title"),
                    json.dumps(safe.get("metadata") or {}, ensure_ascii=False),
                    safe.get("event_id"),
                ),
            )
            conn.execute("DELETE FROM normalized_events WHERE event_id = ?", (safe.get("event_id"),))
            conn.execute("DELETE FROM context_events WHERE event_id = ?", (safe.get("event_id"),))
            db._insert_event(conn, "normalized_events", normalize_event(safe))
            db._insert_context(conn, contextualize_event(safe))
            changed += 1
    return changed


def main() -> None:
    changed = sanitize_existing_browser_titles()
    print(f"Sanitized {changed} historical browser-title event(s).")


if __name__ == "__main__":
    main()
