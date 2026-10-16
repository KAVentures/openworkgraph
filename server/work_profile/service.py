from __future__ import annotations

import json
from typing import Any

from shared.core.contextualizer import contextualize_event
from server.db import connect
from . import (
    SELF_TAG_CATEGORIES,
    add_self_tag,
    list_self_tags,
    compute_work_profile as _compute_work_profile,
    _context_rows,
    _focus_rows,
    _scope_since,
)
from .accuracy import compute_today_work_profile, local_day_since
from .signals import enrich_work_profile

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


def compute_work_profile(*, scope: str = "current", now=None, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    backfill_clipboard_context_links()
    if scope == "range":
        from server.export_window import selection, timestamp, observed_timestamp
        window = selection(scope, since, until)
        since, until = window["since"], window["until"]
        context = _context_rows(since, until=until)
        focus = _focus_rows(since, until=until)
        end = timestamp(until).timestamp()
        for row in focus:
            duration = row["foreground_seconds"]
            kept = min(duration, max(0, end - observed_timestamp(row["observed_at"]).timestamp()))
            if duration and kept < duration:
                for key in ("engaged_seconds", "keypress_count", "click_count", "scroll_count"):
                    row[key] *= kept / duration
                row["duration_seconds"] = row["foreground_seconds"] = kept
        tags = [t for t in list_self_tags(since=since) if observed_timestamp(t["started_at"]) < timestamp(until)]
        for tag in tags:
            tag["started_at"] = max(observed_timestamp(tag["started_at"]), timestamp(since)).isoformat()
            tag["ended_at"] = min(observed_timestamp(tag["ended_at"]), timestamp(until)).isoformat()
        profile = _compute_work_profile(scope="all", _preloaded_context=context,
                                       _preloaded_focus=focus, _preloaded_tags=tags)
        profile.update(scope="range", since=since, until=until)
        return enrich_work_profile(profile, since=since, rows=context)
    since = local_day_since(now=now) if scope == "today" else _scope_since(scope, now=now)
    # Read and decode canonical context once, then feed the same material to
    # both core profile metrics and optional browser-signal enrichments.
    # No derived results are cached across calls or deletion/consent changes.
    context = _context_rows(since)
    profile = (
        compute_today_work_profile(now=now, _preloaded_context=context)
        if scope == "today" else
        _compute_work_profile(scope=scope, now=now, _preloaded_context=context)
    )
    return enrich_work_profile(profile, since=profile.get("since"), rows=context)


__all__ = ["SELF_TAG_CATEGORIES","add_self_tag","list_self_tags","compute_work_profile","backfill_clipboard_context_links"]
