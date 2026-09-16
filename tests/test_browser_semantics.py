from server import analytics


def test_browser_events_are_counted_and_labeled(monkeypatch):
    sample = [
        {
            "session_id":"s1", "observed_at":"2026-01-01T00:00:00Z", "app":"Google Chrome",
            "window_title":"CRM", "event_type":"browser_click", "duration_seconds":0,
            "metadata":{"action":"click","page":{"hostname":"crm.example.com","pathname":"/accounts"},"target":{"tag":"button","label":"Save"}},
        },
        {
            "session_id":"s1", "observed_at":"2026-01-01T00:00:01Z", "app":"Google Chrome",
            "window_title":"CRM", "event_type":"browser_form_submit", "duration_seconds":0,
            "metadata":{"action":"form_submit","page":{"hostname":"crm.example.com","pathname":"/search"},"target":{"tag":"form","label":"Customer search"}},
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    assert out["browser_semantic_events"] == 2
    assert any("Save" in item["label"] for item in out["recent_browser_actions"])
    assert any(item["action"] == "form_submit" for item in out["recent_browser_actions"])


def test_semantic_activity_merges_browser_and_desktop(monkeypatch):
    sample = [
        {"session_id":"s1","observed_at":"2026-01-01T00:00:00Z","app":"Safari","window_title":"CRM","event_type":"screen_click","metadata":{"action":"click","target":{"role":"AXButton","title":"Accounts"}}},
        {"session_id":"s1","observed_at":"2026-01-01T00:00:01Z","app":"Safari","window_title":"CRM","event_type":"browser_click","metadata":{"action":"click","page":{"hostname":"crm.example.com","pathname":"/accounts"},"target":{"tag":"a","label":"Acme AB"}}},
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.semantic_activity()
    assert len(out) == 2
    assert {x["source"] for x in out} == {"browser_extension", "desktop_accessibility"}


def test_navigation_appears_in_unified_raw_evidence_without_scroll(monkeypatch):
    sample = [
        {
            "session_id":"s1", "observed_at":"2026-01-01T00:00:01Z", "app":"Google Chrome",
            "window_title":"Example", "event_type":"browser_navigation_requested", "duration_seconds":0,
            "metadata":{"action":"navigation_requested","page":{"hostname":"example.com","pathname":"/brief"},"target":{}},
        }
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)
    out = analytics.summary()
    assert len(out["recent_evidence"]) == 1
    row = out["recent_evidence"][0]
    assert row["source"] == "browser"
    assert row["hostname"] == "example.com"
    assert row["action"] == "navigation_requested"
