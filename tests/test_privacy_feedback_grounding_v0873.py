from __future__ import annotations

import re
from pathlib import Path

from browser_title_privacy import minimize_browser_title_at_rest
from semantic_actions import safe_semantic_action_label
from server import procedural_feedback as feedback
from mcp_server.compact_hardening import _CompactRuntimeProxy, _readable_step_input


ROOT = Path(__file__).resolve().parents[1]


def test_bilingual_fixed_action_vocabulary_discards_modifiers():
    cases = {
        "Open customer email": "Open email",
        "Open email from Anna Svensson": "Open email",
        "Open customer ticket": "Open ticket",
        "Open invoice 12345": "Open invoice",
        "Open customer conversation": "Open conversation",
        "Skicka meddelande": "Send",
        "Öppna kundens mejl": "Open email",
        "Spara ändringar": "Save",
        "Svara Anna Svensson": "Reply",
        "Arkivera konversation": "Archive",
        "Radera meddelande": "Delete",
        "Skapa nytt ärende": "Create ticket",
        "Uppdatera status": "Update status",
    }
    for source, expected in cases.items():
        result = safe_semantic_action_label({"label": source})
        assert result == expected
        assert "Anna" not in result
        assert "Svensson" not in result


def test_titles_keep_context_and_tokenize_sensitive_details_before_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    salesforce = minimize_browser_title_at_rest({
        "event_type": "browser_click",
        "app": "Google Chrome",
        "window_title": "Anna Svensson - Account 4739 - Salesforce",
        "metadata": {
            "page": {
                "hostname": "acme.my.salesforce.com",
                "pathname": "/lightning/r/Account/4739/view",
                "title": "Anna Svensson - Account 4739 - Salesforce",
            }
        },
    })
    assert re.fullmatch(r"PERSON_[0-9A-F]{6} - Account 4739 - Salesforce", salesforce["window_title"])
    assert salesforce["metadata"]["page"]["title"] == salesforce["window_title"]
    assert salesforce["metadata"]["page"]["surface"] == "Salesforce"
    assert "Anna" not in str(salesforce)

    focus = minimize_browser_title_at_rest({
        "event_type": "focus_span",
        "app": "Google Chrome",
        "window_title": "Inbox - Anna Svensson - Gmail",
        "metadata": {},
    })
    assert re.fullmatch(r"Inbox - PERSON_[0-9A-F]{6} - Gmail", focus["window_title"])

    # Desktop apps follow the same rule; titles without personal data are untouched.
    desktop = {"event_type": "focus_span", "app": "Visual Studio Code", "window_title": "project.py"}
    assert minimize_browser_title_at_rest(desktop) == desktop


def test_readable_feedback_accepts_hyphen_alias(monkeypatch):
    runs = [{
        "execution_id": "execution:1",
        "family_key": "human:email.compose_send",
        "family_basis": "canonical_task_family",
        "started_at": "2026-09-26T10:00:00Z",
        "ended_at": "2026-09-26T10:10:00Z",
        "duration_seconds": 600.0,
        "outcome_status": "observed_completion",
        "outcome_basis": "strong_completion_anchor",
        "positive_example": True,
        "semantic_steps": [
            "Gmail · Open email",
            "Salesforce · Open account",
            "Google Sheets · Update status",
            "Gmail · Send",
        ],
        "evidence_refs": ["event:abc"],
        "evidence_window": {
            "started_at": "2026-09-26T10:00:00Z",
            "ended_at": "2026-09-26T10:10:00Z",
        },
        "trace_lookup": {
            "tool": "get_workflow_trace",
            "since": "2026-09-26T10:00:00Z",
            "until": "2026-09-26T10:10:00Z",
            "scope": "all",
        },
        "derived": True,
        "authoritative": False,
        "needs_review": True,
    }] * 3
    monkeypatch.setattr(feedback, "_human_runs", lambda _events: runs)

    result = feedback.readable_feedback(
        [],
        family_key="human:email.compose_send",
        current_steps="Gmail - Open email",
        min_support=2,
    )
    assert result["status"] == "ok"
    assert result["current_semantic_steps"] == ["Gmail · Open email"]
    assert result["next_steps"]["candidates"][0]["step"] == "Salesforce · Open account"
    assert result["runs"][0]["trace_lookup"]["tool"] == "get_workflow_trace"


def test_human_run_adds_fallback_evidence_refs_and_trace_lookup(monkeypatch):
    task = {
        "task_id": "task-1",
        "session_id": "session-1",
        "started_at": "2026-09-26T10:00:00Z",
        "ended_at": "2026-09-26T10:05:00Z",
        "elapsed_seconds": 300,
        "completion_observed": True,
        "anchor_event_ids": [],
    }
    events = [{
        "event_id": "ev-1",
        "session_id": "session-1",
        "observed_at": "2026-09-26T10:01:00Z",
        "event_type": "browser_click",
        "app": "Google Chrome",
        "metadata": {"page": {"hostname": "mail.google.com", "title": "Gmail"}},
    }]
    monkeypatch.setattr(feedback, "candidate_tasks", lambda **_kwargs: {"tasks": [task]})
    monkeypatch.setattr(feedback.memory, "_human_steps", lambda _task, _events: ["surface:gmail", "action:click"])
    monkeypatch.setattr(feedback.memory, "_human_family", lambda _task, _steps: ("human:email.compose_send", "canonical_task_family"))

    run = feedback._human_runs(events)[0]
    assert run["evidence_refs"]
    assert run["evidence_window"]["started_at"] == task["started_at"]
    assert run["trace_lookup"] == {
        "tool": "get_workflow_trace",
        "since": task["started_at"],
        "until": task["ended_at"],
        "scope": "all",
    }
    assert run["structural_steps"] == ["surface:gmail", "action:click"]


def test_compact_hardening_routes_readable_text_and_omits_structural_pack():
    assert _readable_step_input("Gmail - Open email") is True
    assert _readable_step_input("Gmail · Open email") is True
    assert _readable_step_input("Nonsense step") is True
    assert _readable_step_input("surface:gmail,action:click") is False

    class Runtime:
        def secure_get(self, path, params):
            return {"path": path, "params": params}

    proxy = _CompactRuntimeProxy(Runtime())
    compact_pack = proxy.secure_get(
        "/v1/procedural-memory/context-pack",
        {"family_key": "human:email.compose_send"},
    )
    assert compact_pack["structural_context_omitted"] is True
    assert "surface:" not in str(compact_pack)
    assert proxy.secure_get("/v1/other", {"x": 1}) == {"path": "/v1/other", "params": {"x": 1}}


def test_new_compact_entrypoints_apply_hardening():
    for path in ("mcp_server/compact_stdio.py", "mcp_server/compact_http_app.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "apply_compact_hardening" in source
