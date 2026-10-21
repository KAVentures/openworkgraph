"""Local dashboard preview for a user-selected evidence handoff. No AI invocation."""
from __future__ import annotations

from fastapi import HTTPException, Request
from datetime import datetime, timedelta, timezone
from .export_window import selection, sql_bounds
from .exporter import _analysis_request
from .db import connect


def analysis_preview(request: Request, scope: str = "current", since: str | None = None,
                     until: str | None = None) -> dict:
    # A preparation screen is not another way for an AI to read ungranted history.
    from .secure_app import _dashboard_authenticated
    if not _dashboard_authenticated(request):
        raise HTTPException(status_code=403, detail="Local dashboard session required")
    try:
        window = selection(scope, since, until)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    clause, params = sql_bounds(window["since"], window["until"])
    where = " WHERE " + clause if clause else ""
    with connect() as conn:
        grouped = conn.execute("SELECT event_type, source, COUNT(*) AS count FROM events" + where
                               + " GROUP BY event_type, source", params).fetchall()
        available = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        samples = conn.execute("SELECT event_id, observed_at, app, window_title FROM events" + where
                               + (" AND " if where else " WHERE ") + "event_type='focus_span'"
                               + " ORDER BY julianday(observed_at) DESC LIMIT 6", params).fetchall()
        bounds = conn.execute("SELECT observed_at FROM events" + where
                              + " ORDER BY julianday(observed_at), id LIMIT 1", params).fetchone()
        last = conn.execute("SELECT observed_at FROM events" + where
                            + " ORDER BY julianday(observed_at) DESC, id DESC LIMIT 1", params).fetchone()
        last_work = conn.execute("SELECT observed_at FROM events" + where
                                + (" AND " if where else " WHERE ")
                                + "event_type NOT IN ('capture_gap','capture_health') AND app != 'Excluded'"
                                + " ORDER BY julianday(observed_at) DESC, id DESC LIMIT 1", params).fetchone()
        days = conn.execute("SELECT date(observed_at) AS day, COUNT(*) AS count FROM events" + where
                            + " GROUP BY date(observed_at) HAVING day IS NOT NULL ORDER BY day LIMIT 32", params).fetchall()
    from .ai_context import redact_contextually
    from shared.history_policy import active_ai_history_access
    from .work_text_capture import get_policy
    from .ai_access import ai_access_enabled
    counts: dict[str, int] = {}
    sources: dict[str, int] = {}
    for row in grouped:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + row["count"]
        sources[row["source"]] = sources.get(row["source"], 0) + row["count"]
    chunks = []
    for row in days[:31]:
        first = datetime.fromisoformat(row['day']).replace(tzinfo=timezone.utc)
        end = first + timedelta(days=1)
        if window['since']:
            first = max(first, datetime.fromisoformat(window['since'].replace('Z', '+00:00')))
        if window['until']:
            end = min(end, datetime.fromisoformat(window['until'].replace('Z', '+00:00')))
        chunks.append({'since': first.isoformat(), 'until': end.isoformat(), 'event_rows': row['count']})
    health_rows = counts.get('capture_gap', 0) + counts.get('capture_health', 0)
    return {
        "window": window,
        "first_observed_at": bounds[0] if bounds else None,
        "last_observed_at": last[0] if last else None,
        "canonical_rows": sum(counts.values()), "event_types": counts,
        "sources": sources, "retained_rows": available,
        "work_evidence": {"ordinary_event_rows": sum(counts.values()) - health_rows,
                          "health_event_rows": health_rows,
                          "last_work_observed_at": last_work[0] if last_work else None,
                          "recorder_liveness_verified": False},
        "analysis_plan": {"page_size": 200, "date_chunks": chunks,
                          "more_dates_not_listed": len(days) > 31,
                          "complete_history_claimed": False,
                          "chunks_respect_selected_window": True},
        "redacted_examples": redact_contextually([dict(x) for x in samples]),
        "local_ai_enabled": ai_access_enabled(),
        "local_ai_history_access": active_ai_history_access(),
        "work_text": {"included_in_activity_package": False,
                      "local_capture_enabled": bool(get_policy().get("capture_enabled"))},
        "request_text": _analysis_request(window) + "\n\nUse the connected OWG chronology tools, without a search filter, "
                        "for this period and follow their pagination. Respect the currently granted history boundary. "
                        "If these dates are unavailable, explain the missing access rather than widening it. "
                        "An attached evidence package can also be used without MCP.",
        "destination": "Nothing sent. Choose a file download or copy a request for your AI.",
        "hosted_history_availability": "Not established by this local preview; hosted apps only see evidence already shared with them.",
        "limitations": ["Gaps are unobserved intervals, not lost work.",
                        "Observed dates and event counts do not establish complete coverage.",
                        "No repeated-work candidates are required to analyze the chronology."],
    }
