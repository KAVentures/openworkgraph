from collections import Counter

from collector.privacy import should_exclude, title_for_mode


def test_privacy_exclusions():
    assert should_exclude("1Password", "Vault", ["1Password"], [])
    assert should_exclude("Chrome", "Private customer password", [], ["password"])
    assert not should_exclude("Excel", "Quarterly plan", ["1Password"], ["password"])


def test_title_modes():
    assert title_for_mode("Secret", "none") == ""
    assert title_for_mode("Secret", "full") == "Secret"
    assert title_for_mode("Secret", "hash") != "Secret"


def test_browser_titles_become_distinct_workflow_steps(monkeypatch):
    from server import analytics
    sample = [
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00Z","app":"Google Chrome","window_title":"Gmail - Inbox","duration_seconds":10},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:10Z","app":"Google Chrome","window_title":"Salesforce","duration_seconds":12},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:22Z","app":"Google Chrome","window_title":"Google Sheets","duration_seconds":15},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:37Z","app":"Google Chrome","window_title":"Gmail - Inbox","duration_seconds":8},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:45Z","app":"Google Chrome","window_title":"Salesforce","duration_seconds":9},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:54Z","app":"Google Chrome","window_title":"Google Sheets","duration_seconds":11},
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    assert out["transitions"][0]["from"].startswith("Google Chrome · ")
    assert any("Salesforce" in x["to"] for x in out["transitions"])
    assert any(len(x["sequence"]) == 3 and x["count"] >= 2 for x in out["frequent_sequences"])


def test_screen_interactions_are_counted_separately(monkeypatch):
    from server import analytics
    sample = [
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00Z","app":"Safari","window_title":"CRM","event_type":"focus_span","duration_seconds":10,"metadata":{}},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:03Z","app":"Safari","window_title":"CRM","event_type":"screen_click","duration_seconds":0,"metadata":{"action":"click","target":{"role":"AXButton","title":"Save"}}},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:04Z","app":"Safari","window_title":"CRM","event_type":"screen_scroll","duration_seconds":0,"metadata":{"action":"scroll"}},
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    assert out["focus_events"] == 1
    assert out["screen_interactions"] == 2
    assert out["recent_interactions"][0]["action"] == "scroll"
    assert any("Save" in x["label"] for x in out["recent_interactions"])


def test_effort_is_split_by_browser_work_surface(monkeypatch):
    from server import analytics
    sample = [
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00Z","app":"Google Chrome","window_title":"Inbox - Gmail","event_type":"focus_span","duration_seconds":30,"metadata":{"activity":{"engaged_seconds":25,"idle_seconds":5,"active_input_seconds":10,"keypress_count":20,"click_count":2,"scroll_count":1}}},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:30Z","app":"Google Chrome","window_title":"Project plan - Google Docs","event_type":"focus_span","duration_seconds":45,"metadata":{"activity":{"engaged_seconds":40,"idle_seconds":5,"active_input_seconds":20,"keypress_count":120,"click_count":3,"scroll_count":2}}},
        {"session_id":"s1","observed_at":"2026-01-01T00:01:15Z","app":"Google Chrome","window_title":"Lovable","event_type":"focus_span","duration_seconds":20,"metadata":{"activity":{"engaged_seconds":18,"idle_seconds":2,"active_input_seconds":8,"keypress_count":50,"click_count":4,"scroll_count":0}}},
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    surfaces = {x["surface"]: x for x in out["surfaces"]}
    assert surfaces["Gmail"]["keypress_count"] == 20
    assert surfaces["Google Docs"]["keypress_count"] == 120
    assert surfaces["Lovable"]["keypress_count"] == 50
    assert all(x["container_app"] == "Google Chrome" for x in surfaces.values())
    assert out["apps"][0]["app"] == "Google Chrome"
    assert out["apps"][0]["keypress_count"] == 190


def test_effort_uses_browser_hostname_when_available(monkeypatch):
    from server import analytics
    sample = [
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00Z","app":"Google Chrome","window_title":"Untitled spreadsheet","event_type":"focus_span","duration_seconds":20,"metadata":{"activity":{"engaged_seconds":19,"keypress_count":40}}},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00.200Z","app":"Google Chrome","window_title":"Untitled spreadsheet","event_type":"browser_tab_activated","duration_seconds":0,"metadata":{"action":"tab_activated","page":{"hostname":"docs.google.com","pathname":"/spreadsheets/d/abc","title":"Untitled spreadsheet"},"target":{}}},
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    assert out["surfaces"][0]["surface"] == "Google Sheets"
    assert out["surfaces"][0]["keypress_count"] == 40
