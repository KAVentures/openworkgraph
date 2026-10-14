"""Regressions for bounded title capture, lexical cost and browser lifecycle semantics."""
from __future__ import annotations

import time

import pytest

from shared.core.browser_title_privacy import (
    MAX_CAPTURE_TITLE_CHARS, cap_event_title_surfaces, cap_title_text, protect_text,
)
from server.capture_exclusions import DEFAULTS, apply_to_desktop_event
from server.contextual_redaction import Detector
from server.browser_agent_projection import _agent_payload
from server.main import BrowserEvent
from mcp.server.mcpserver.exceptions import ToolError
from mcp_server.compact import _trace_params


def test_normal_work_titles_and_opted_in_work_text_are_not_capped():
    title = "PR #197 · KAVentures/openworkgraph - Chrome"
    assert cap_title_text(title) == title
    assert protect_text(title) == title
    event = {"window_title": title, "metadata": {
        "page": {"title": title}, "target": {"label": "Merge PR #197"},
        "work_text": "some text" * 3000,
    }}
    processed = cap_event_title_surfaces(event)
    assert processed["window_title"] == title
    assert processed["metadata"]["target"]["label"] == "Merge PR #197"
    assert processed["metadata"]["work_text"] == event["metadata"]["work_text"]


def test_oversized_title_is_bounded_early_without_retaining_partial_secrets():
    # Very long opaque unbroken strings must not be retained as partial
    # credentials that evade redaction after the truncation boundary.
    secret = "X" * 200_000
    raw = "Customer invoice | " + secret + " | Gmail"
    start = time.perf_counter()
    capped = cap_title_text(raw)
    elapsed = time.perf_counter() - start
    assert len(capped) < MAX_CAPTURE_TITLE_CHARS + 60
    assert capped.startswith("Customer invoice")
    assert capped.endswith("Gmail")
    assert "[truncated " in capped
    assert "X" * 20 not in capped
    assert elapsed < 3.0
    assert protect_text(raw) == protect_text(capped)


def test_large_title_and_target_are_capped_before_ingest_but_normal_metadata_preserved():
    long_title = "Work progress | " + ("paragraph " * 30_000) + " | Google Docs"
    source = {"source": "desktop", "window_title": long_title, "metadata": {
        "target": {"label": long_title, "role": "button"},
        "page": {"title": long_title, "hostname": "docs.google.com"},
        "timing": {"seconds": 15},
    }}
    capped = cap_event_title_surfaces(source)
    assert capped["metadata"]["page"]["hostname"] == "docs.google.com"
    assert capped["metadata"]["target"]["role"] == "button"
    assert capped["metadata"]["timing"] == {"seconds": 15}
    for text in (capped["window_title"], capped["metadata"]["page"]["title"],
                 capped["metadata"]["target"]["label"]):
        assert len(text) < 2100 and "[truncated " in text


def test_name_redactor_handles_long_title_without_quadratic_rescans():
    detector = Detector(token=lambda kind, word: kind + "_MASKED")
    text = "Quarterly invoice reconciliation | " + ("neutral workflow step " * 10_000)
    start = time.perf_counter()
    output = detector.redact(text)
    duration = time.perf_counter() - start
    assert output == text
    assert duration < 8.0


def test_personal_chat_apps_default_to_excluded_without_excluding_work_chat():
    for app in ("Messages", "iMessage", "WhatsApp", "Signal", "Telegram"):
        assert app in DEFAULTS["excluded_apps"]
        event = {"source": "desktop", "app": app,
                 "window_title": "Mamma: ring när du kan",
                 "event_type": "focus_span", "metadata": {"label": "private"}}
        out = apply_to_desktop_event(event, DEFAULTS)
        assert out["app"] == "Excluded"
        assert out["window_title"] == ""
    assert "Slack" not in DEFAULTS["excluded_apps"]
    assert "Microsoft Teams" not in DEFAULTS["excluded_apps"]


def test_browser_finish_does_not_assert_success():
    event = BrowserEvent(
        observed_at="2026-10-14T08:00:00Z",
        action="agent_run_finished",
        metadata={"agent_provider": "chatgpt", "agent_run_id": "web-abcdef1234567890"},
    )
    projected = _agent_payload(event)
    assert projected is not None
    assert projected["operation"] == "run_finished"
    assert projected["status"] == "unknown"
    assert "message_body" not in projected


def test_invalid_history_scope_fails_explicitly():
    for invalid in ("history", "last-session", "yesterday", "typo"):
        with pytest.raises(ToolError, match="scope must be current or all"):
            _trace_params(scope=invalid)
    assert _trace_params(scope="all", limit=10)["scope"] == "all"
