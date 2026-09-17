from __future__ import annotations

import copy


def test_desktop_chrome_evidence_uses_nearby_browser_hostname(monkeypatch):
    from server import analytics

    sample = [
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.000Z",
            "app": "Google Chrome",
            "window_title": "Enterprise Task Mining Software - Google Chrome – Koyar (kinvectum.com)",
            "event_type": "screen_click",
            "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"role": "AXGroup"}},
        },
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.080Z",
            "app": "Google Chrome",
            "window_title": "Enterprise Task Mining Software",
            "event_type": "browser_click",
            "duration_seconds": 0,
            "metadata": {
                "action": "click",
                "page": {
                    "hostname": "chatgpt.com",
                    "pathname": "/c/example",
                    "title": "Enterprise Task Mining Software",
                },
                "target": {"role": "button", "label": "Send"},
            },
        },
    ]
    before = copy.deepcopy(sample)
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary()
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert sample == before
    assert desktop["app"] == "ChatGPT"
    assert desktop["work_surface"] == "ChatGPT"
    assert desktop["container_app"] == "Google Chrome"
    assert desktop["browser_hostname"] == "chatgpt.com"
    assert desktop["browser_pathname"] == "/c/example"
    assert desktop["page_title"] == "Enterprise Task Mining Software"
    # Dashboard compatibility: hostname is blank so it displays app/work surface
    # as the primary line, and the smaller line carries page + host + container.
    assert desktop["hostname"] == ""
    assert "Enterprise Task Mining Software" in desktop["window_title"]
    assert "chatgpt.com" in desktop["window_title"]
    assert "Google Chrome" in desktop["window_title"]


def test_non_browser_desktop_evidence_is_not_relabelled(monkeypatch):
    from server import analytics

    sample = [
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.000Z",
            "app": "Microsoft Word",
            "window_title": "Quarterly report",
            "event_type": "screen_click",
            "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"role": "button", "title": "Save"}},
        },
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.050Z",
            "app": "Google Chrome",
            "window_title": "Enterprise Task Mining Software",
            "event_type": "browser_click",
            "duration_seconds": 0,
            "metadata": {
                "action": "click",
                "page": {"hostname": "chatgpt.com", "pathname": "/", "title": "Enterprise Task Mining Software"},
                "target": {},
            },
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Microsoft Word"
    assert desktop["window_title"] == "Quarterly report"
    assert "work_surface" not in desktop


def test_stale_browser_context_does_not_relabel_late_desktop_event(monkeypatch):
    from server import analytics

    sample = [
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.000Z",
            "app": "Google Chrome",
            "window_title": "Chat",
            "event_type": "browser_tab_activated",
            "duration_seconds": 0,
            "metadata": {
                "action": "tab_activated",
                "page": {"hostname": "chatgpt.com", "pathname": "/", "title": "Chat"},
                "target": {},
            },
        },
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:20.000Z",
            "app": "Google Chrome",
            "window_title": "Some other page - Google Chrome",
            "event_type": "screen_click",
            "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"role": "AXGroup"}},
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert desktop["hostname"] == ""
    assert "work_surface" not in desktop
