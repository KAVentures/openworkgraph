from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from server.db import init_db, insert_events


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _activity(duration: int, keys: int, clicks: int = 1, scrolls: int = 0) -> dict:
    engaged = max(1, duration - 4)
    return {
        "foreground_seconds": duration,
        "engaged_seconds": engaged,
        "idle_seconds": duration - engaged,
        "active_input_seconds": min(engaged, max(2, keys / 3)),
        "keypress_count": keys,
        "click_count": clicks,
        "scroll_count": scrolls,
        "input_events": keys + clicks + scrolls,
    }


def _focus(session: str, at: datetime, duration: int, *, title: str, activity: dict) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": _iso(at),
        "schema_version": "1.0",
        "device_id": "demo-laptop",
        "sensor_id": "demo:desktop",
        "source": "desktop",
        "session_id": session,
        "app": "Google Chrome",
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": duration,
        "metadata": {
            "source": "desktop",
            "excluded": False,
            "activity": activity,
            "privacy": {"key_identities": False, "typed_values": False},
        },
    }


def _browser(session: str, at: datetime, *, host: str, path: str, title: str, label: str, action: str = "click") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": _iso(at),
        "schema_version": "1.0",
        "device_id": "demo-laptop",
        "sensor_id": "demo:browser",
        "source": "browser_extension",
        "session_id": session,
        "app": "Google Chrome",
        "window_title": title,
        "event_type": f"browser_{action}",
        "duration_seconds": 0,
        "metadata": {
            "source": "browser_extension",
            "action": action,
            "page": {"origin": f"https://{host}", "hostname": host, "pathname": path, "title": title},
            "target": {"tag": "button", "role": "button", "label": label},
            "privacy": {"typed_values": False, "clipboard_contents": False, "url_query": False, "url_fragment": False},
            "demo": True,
        },
    }


def build_demo_events(base: datetime) -> list[dict]:
    session = "demo-session"
    events: list[dict] = []
    for i in range(3):
        t = base + timedelta(minutes=i * 22)
        events.extend([
            _focus(session, t, 150, title="Inbox - Gmail", activity=_activity(150, 24, 3, 1)),
            _browser(session, t + timedelta(seconds=8), host="mail.google.com", path="/mail/u/0/inbox", title="Inbox - Gmail", label="Open customer email"),
            _focus(session, t + timedelta(seconds=150), 170, title="Customer account - Salesforce", activity=_activity(170, 38, 4, 2)),
            _browser(session, t + timedelta(seconds=165), host="example.my.salesforce.com", path="/lightning/r/Account/demo", title="Customer account - Salesforce", label="Open account"),
            _focus(session, t + timedelta(seconds=320), 190, title="Customer tracker - Google Sheets", activity=_activity(190, 72, 5, 2)),
            _browser(session, t + timedelta(seconds=340), host="docs.google.com", path="/spreadsheets/d/demo/edit", title="Customer tracker - Google Sheets", label="Update status"),
            _focus(session, t + timedelta(seconds=510), 145, title="Reply - Gmail", activity=_activity(145, 86, 4, 1)),
            _browser(session, t + timedelta(seconds=625), host="mail.google.com", path="/mail/u/0/inbox", title="Reply - Gmail", label="Send"),
        ])
    return events


def main() -> None:
    init_db()
    run_started = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    try:
        base = datetime.fromisoformat(str(run_started).replace("Z", "+00:00")) + timedelta(minutes=5)
    except Exception:
        base = datetime.now(timezone.utc) - timedelta(minutes=90)
    events = build_demo_events(base)
    inserted = insert_events(events)
    print({"inserted": inserted, "focus_spans": 12, "semantic_events": 12, "workflow_executions": 3})


if __name__ == "__main__":
    main()
