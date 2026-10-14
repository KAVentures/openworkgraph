"""Personal chat remains excluded for old desktop configs and browser tabs."""
from __future__ import annotations

from server.capture_exclusions import apply_to_desktop_event
from shared.core.browser_privacy import browser_event_is_excluded, harden_browser_event
from shared.core.personal_chat_privacy import (
    personal_chat_surface, suppress_personal_chat_evidence,
)


def test_private_messaging_defaults_not_hiding_business_collaboration():
    legacy = {"excluded_apps": ["1Password"], "excluded_title_patterns": [],
              "excluded_browser_host_patterns": []}
    for app, title, host in (
        ("Messages", "Mamma: ring när du kan", ""),
        ("Google Chrome", "WhatsApp - Mamma: ring när du kan", "web.whatsapp.com"),
        ("Chrome", "Telegram | Private DM", "web.telegram.org"),
        ("Safari", "Chat with family", "messenger.com"),
        ("Signal", "Confidential text", ""),
    ):
        assert personal_chat_surface(app=app, title=title, hostname=host)
        assert browser_event_is_excluded(
            app=app, title=title, hostname=host, config=legacy
        )
    for app, title, host in (
        ("Slack", "PR #197 · Work team", "app.slack.com"),
        ("Microsoft Teams", "Support ticket meeting", "teams.microsoft.com"),
        ("Google Chrome", "OpenWorkGraph GitHub PR", "github.com"),
    ):
        assert not personal_chat_surface(app=app, title=title, hostname=host)

    hidden = apply_to_desktop_event({
        "source": "desktop", "app": "Messages",
        "window_title": "Mamma: ring när du kan", "event_type": "focus_span",
        "metadata": {"target": {"label": "private message"}, "time": 120},
    }, legacy)
    assert hidden["app"] == "Excluded"
    assert hidden["window_title"] == ""
    assert "target" not in hidden["metadata"]
    assert hidden["metadata"]["time"] == 120


def test_browser_whatsapp_metadata_dropped_before_storage_even_with_old_profile():
    event = {
        "source": "browser_extension",
        "app": "Google Chrome",
        "event_type": "browser_navigation",
        "window_title": "WhatsApp · Mamma: ring när du kan",
        "metadata": {
            "action": "navigate",
            "page": {"hostname": "web.whatsapp.com",
                     "title": "WhatsApp · Mamma: ring när du kan"},
            "target": {"label": "PRIVATE_MOM_MESSAGE"},
            "resource_reference": {"provider": "other", "resource_ref": "abc"},
        },
    }
    out = harden_browser_event(event, {
        "excluded_apps": [], "excluded_title_patterns": [],
        "excluded_browser_host_patterns": [],
        "business_object_references": False,
    })
    assert out["app"] == "Excluded"
    assert out["window_title"] == ""
    assert out["metadata"]["excluded"] is True
    assert "PRIVATE_MOM_MESSAGE" not in str(out)
    assert "Mamma" not in str(out)


def test_legacy_ai_history_scrubbed_even_when_app_was_chrome():
    source = {
        "rows": [
            {"event_id": "private-1", "observed_at": "2026-10-14T08:00:00Z",
             "source": "desktop", "event_type": "focus_span", "app": "Messages",
             "window_title": "Mamma: ring när du kan",
             "target_label": "PERSONAL_MESSAGE_TEXT"},
            {"event_id": "private-2", "observed_at": "2026-10-14T09:00:00Z",
             "source": "browser_extension", "app": "Chrome",
             "window_title": "WhatsApp - Sensitive chat",
             "metadata": {"page": {"hostname": "web.whatsapp.com"},
                          "target": {"label": "PERSONAL_MESSAGE_TEXT"}}},
            {"event_id": "business", "observed_at": "2026-10-14T10:00:00Z",
             "app": "Slack", "window_title": "Support workflow"},
        ],
    }
    safe = suppress_personal_chat_evidence(source)
    assert "Mamma" not in str(safe)
    assert "PERSONAL_MESSAGE_TEXT" not in str(safe)
    assert "Sensitive chat" not in str(safe)
    assert safe["rows"][0]["app"] == "Excluded"
    assert safe["rows"][1]["app"] == "Excluded"
    assert safe["rows"][2]["window_title"] == "Support workflow"
