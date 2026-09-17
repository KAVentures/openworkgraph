from __future__ import annotations


def _analytics():
    from server import analytics
    from server.browser_context_policy import install

    # Some earlier tests reload server.analytics for isolation, which removes the
    # package-level wrapper. Production startup always installs this policy, so
    # install it explicitly here to make these regression tests order-independent.
    install(analytics)
    return analytics


def _chatgpt_browser(at: str, title: str = "Enterprise Task Mining Software") -> dict:
    return {
        "session_id": "s1",
        "observed_at": at,
        "app": "Google Chrome",
        "window_title": title,
        "event_type": "browser_tab_activated",
        "duration_seconds": 0,
        "metadata": {
            "action": "tab_activated",
            "page": {
                "hostname": "chatgpt.com",
                "pathname": "/c/example",
                "title": title,
            },
            "target": {},
        },
    }


def _desktop(at: str, title: str) -> dict:
    return {
        "session_id": "s1",
        "observed_at": at,
        "app": "Google Chrome",
        "window_title": title,
        "event_type": "screen_click",
        "duration_seconds": 0,
        "metadata": {"action": "click", "target": {"role": "AXGroup"}},
    }


def test_chatgpt_context_persists_during_long_current_run_dwell(monkeypatch):
    analytics = _analytics()
    sample = [
        _chatgpt_browser("2026-09-17T19:00:00.000Z"),
        _desktop(
            "2026-09-17T19:05:00.000Z",
            "Enterprise Task Mining Software - Google Chrome – OWNER (kinvectum.com)",
        ),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary(since="2026-09-17T18:59:00+00:00")
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "ChatGPT"
    assert desktop["work_surface"] == "ChatGPT"
    assert desktop["browser_hostname"] == "chatgpt.com"
    assert desktop["container_app"] == "Google Chrome"
    assert desktop["browser_context_join"] == "active_run_title_match"
    assert desktop["browser_context_age_seconds"] == 300.0


def test_long_lived_context_does_not_relabel_different_chrome_tab(monkeypatch):
    analytics = _analytics()
    sample = [
        _chatgpt_browser("2026-09-17T19:00:00.000Z"),
        _desktop("2026-09-17T19:05:00.000Z", "Supabase | companion-health - Google Chrome"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary(since="2026-09-17T18:59:00+00:00")
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert "work_surface" not in desktop


def test_active_browser_context_expires_after_thirty_minutes(monkeypatch):
    analytics = _analytics()
    sample = [
        _chatgpt_browser("2026-09-17T19:00:00.000Z"),
        _desktop(
            "2026-09-17T19:31:00.000Z",
            "Enterprise Task Mining Software - Google Chrome – OWNER (kinvectum.com)",
        ),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary(since="2026-09-17T18:59:00+00:00")
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert "work_surface" not in desktop


def test_all_history_summary_keeps_legacy_short_window(monkeypatch):
    analytics = _analytics()
    sample = [
        _chatgpt_browser("2026-09-17T19:00:00.000Z"),
        _desktop(
            "2026-09-17T19:05:00.000Z",
            "Enterprise Task Mining Software - Google Chrome – OWNER (kinvectum.com)",
        ),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    # No `since` means this is not a current-run view. Without explicit session
    # identity on the summary rows, do not carry browser identity for minutes.
    out = analytics.summary()
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert "work_surface" not in desktop
