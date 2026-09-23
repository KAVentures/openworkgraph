from __future__ import annotations

import json
from typing import Any

from contextualizer import contextualize_event
from .db import connect
from .work_profile import (
    SELF_TAG_CATEGORIES,
    add_self_tag,
    list_self_tags,
    compute_work_profile as _compute_work_profile,
)
from .work_profile_signals import enrich_work_profile

MIGRATION_KEY = "work_profile_clipboard_context_links_v1"


def _row_to_event(row: Any) -> dict[str, Any]:
    item = dict(row)
    item.pop("id", None)
    try:
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    except Exception:
        item["metadata"] = {}
    return item


def backfill_clipboard_context_links() -> int:
    changed = 0
    with connect() as conn:
        try:
            already = conn.execute("SELECT 1 FROM privacy_migrations WHERE migration_key = ?", (MIGRATION_KEY,)).fetchone()
        except Exception:
            return 0
        if already:
            return 0
        rows = conn.execute("SELECT * FROM events WHERE event_type LIKE 'clipboard_%' ORDER BY id ASC").fetchall()
        for row in rows:
            context = contextualize_event(_row_to_event(row))
            metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
            conn.execute(
                """UPDATE context_events SET surface=?, action=?, resource_title=?, resource_locator=?, target_label=?, context_text=?, metadata_json=? WHERE event_id=?""",
                (context.get("surface",""), context.get("action",""), context.get("resource_title",""), context.get("resource_locator",""), context.get("target_label",""), context.get("context_text",""), json.dumps(metadata,ensure_ascii=False), context.get("event_id","")),
            )
            changed += 1
        conn.execute("INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)", (MIGRATION_KEY,))
    return changed


def compute_work_profile(*, scope: str = "current", now=None) -> dict[str, Any]:
    backfill_clipboard_context_links()
    profile = _compute_work_profile(scope=scope, now=now)
    return enrich_work_profile(profile, since=profile.get("since"))


__all__ = ["SELF_TAG_CATEGORIES","add_self_tag","list_self_tags","compute_work_profile","backfill_clipboard_context_links"]
