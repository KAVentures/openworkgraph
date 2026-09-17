from __future__ import annotations

import importlib


def _fresh_presentation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)

    monkeypatch.setattr(
        presentation,
        "_owner_identity",
        lambda: ({"koyar afrasyab": "OWNER", "koyar": "OWNER"}, set(), set()),
    )
    return presentation


def test_linkedin_subject_redacts_two_hiring_people(monkeypatch, tmp_path):
    presentation = _fresh_presentation(monkeypatch, tmp_path)
    raw = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": (
            "click: oläst, LinkedIn , Julia Brännström and Joel Berbres are hiring , "
            "14 maj , See… (AXCheckBox)"
        ),
    }
    safe = presentation.redact_for_display(raw)

    assert "LinkedIn" in safe["label"]
    assert "Julia Brännström" not in safe["label"]
    assert "Joel Berbres" not in safe["label"]
    assert safe["label"].count("PERSON_") >= 2
    assert "are hiring" in safe["label"]
    assert "14 maj" in safe["label"]
    assert "AXCheckBox" in safe["label"]


def test_linkedin_subject_redacts_two_first_names_without_linking_identity(monkeypatch, tmp_path):
    presentation = _fresh_presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: unread, LinkedIn, Julia and Joel are hiring, 20:24, … (AXCheckBox)",
    })

    assert "Julia" not in safe["label"]
    assert "Joel" not in safe["label"]
    assert "PERSON and PERSON are hiring" in safe["label"]


def test_live_chrome_row_uses_latest_browser_surface_when_page_title_differs(monkeypatch):
    from server import analytics
    analytics.clear_summary_cache()
    sample = [
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.000Z",
            "app": "Google Chrome",
            "window_title": "ChatGPT",
            "event_type": "browser_tab_activated",
            "duration_seconds": 0,
            "metadata": {
                "action": "tab_activated",
                "page": {
                    "hostname": "chatgpt.com",
                    "pathname": "/c/example",
                    "title": "ChatGPT",
                },
                "target": {},
            },
        },
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:05:00.000Z",
            "app": "Google Chrome",
            "window_title": "Enterprise Task Mining Software - Google Chrome – OWNER (kinvectum.com)",
            "event_type": "screen_click",
            "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"role": "AXGroup"}},
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary(since="2026-09-17T18:59:00+00:00")
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert desktop["work_surface"] == "ChatGPT"
    assert desktop["browser_hostname"] == "chatgpt.com"
    assert desktop["browser_context_join"] == "active_run_latest_browser"
    assert desktop["browser_context_age_seconds"] == 300.0


def test_live_context_does_not_override_known_different_site_title(monkeypatch):
    from server import analytics
    analytics.clear_summary_cache()
    sample = [
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:00:00.000Z",
            "app": "Google Chrome",
            "window_title": "ChatGPT",
            "event_type": "browser_tab_activated",
            "duration_seconds": 0,
            "metadata": {
                "action": "tab_activated",
                "page": {"hostname": "chatgpt.com", "pathname": "/", "title": "ChatGPT"},
                "target": {},
            },
        },
        {
            "session_id": "s1",
            "observed_at": "2026-09-17T19:05:00.000Z",
            "app": "Google Chrome",
            "window_title": "Supabase | companion-health - Google Chrome",
            "event_type": "screen_click",
            "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"role": "AXGroup"}},
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: sample)

    out = analytics.summary(since="2026-09-17T18:59:00+00:00")
    desktop = next(item for item in out["recent_evidence"] if item["source"] == "desktop")

    assert desktop["app"] == "Google Chrome"
    assert "work_surface" not in desktop
