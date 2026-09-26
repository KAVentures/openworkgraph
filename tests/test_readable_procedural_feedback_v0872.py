from __future__ import annotations

import json

import pytest


def _browser_event(at: str, host: str, path: str, label: str) -> dict:
    return {
        "event_id": f"event-{at}-{label}",
        "observed_at": at,
        "session_id": "session-1",
        "event_type": "browser_click",
        "app": "Google Chrome",
        "window_title": "",
        "source": "browser",
        "metadata": {
            "action": "click",
            "page": {"hostname": host, "pathname": path, "title": ""},
            "target": {"label": label, "role": "button"},
        },
    }


def test_semantic_steps_use_safe_surface_and_action_vocabulary():
    from server import procedural_feedback as feedback

    task = {
        "started_at": "2026-09-26T10:00:00+00:00",
        "ended_at": "2026-09-26T10:05:00+00:00",
    }
    events = [
        _browser_event(
            "2026-09-26T10:00:10+00:00",
            "mail.google.com",
            "/mail/u/0/",
            "Open email from Anna Svensson",
        ),
        _browser_event(
            "2026-09-26T10:01:10+00:00",
            "acme.my.salesforce.com",
            "/lightning/r/Account/001ABC/view",
            "Open account Acme AB",
        ),
        _browser_event(
            "2026-09-26T10:02:10+00:00",
            "docs.google.com",
            "/spreadsheets/d/customer-secret/edit",
            "Update status for Anna Svensson",
        ),
        _browser_event(
            "2026-09-26T10:03:10+00:00",
            "mail.google.com",
            "/mail/u/0/",
            "Send to anna@example.com",
        ),
    ]

    steps = feedback._semantic_steps(task, events)
    assert steps == [
        "Gmail · Open email",
        "Salesforce · Open account",
        "Google Sheets · Update status",
        "Gmail · Send",
    ]
    encoded = json.dumps(steps, ensure_ascii=False)
    assert "Anna Svensson" not in encoded
    assert "anna@example.com" not in encoded
    assert "acme.my.salesforce.com" not in encoded
    assert "001ABC" not in encoded
    assert "customer-secret" not in encoded
    assert "surface:" not in encoded


def test_unknown_web_surface_is_pseudonymized_not_echoed():
    from server import procedural_feedback as feedback

    task = {
        "started_at": "2026-09-26T10:00:00+00:00",
        "ended_at": "2026-09-26T10:05:00+00:00",
    }
    events = [
        _browser_event(
            "2026-09-26T10:00:10+00:00",
            "customer-secret.internal.example",
            "/private/123",
            "Send",
        )
    ]
    steps = feedback._semantic_steps(task, events)
    assert len(steps) == 1
    assert steps[0].startswith("Web app ")
    assert steps[0].endswith(" · Send")
    assert "customer-secret" not in steps[0]
    assert "internal.example" not in steps[0]


def test_semantic_projection_cannot_change_structural_family_key(monkeypatch):
    from server import procedural_feedback as feedback

    task = {
        "task_id": "task-1",
        "session_id": "session-1",
        "task_family": "crm.followup",
        "started_at": "2026-09-26T10:00:00+00:00",
        "ended_at": "2026-09-26T10:05:00+00:00",
        "elapsed_seconds": 300,
        "completion_observed": True,
        "anchor_event_ids": [],
    }
    structural = ["surface:gmail", "action:click", "surface:33a7935db79d", "action:click"]
    expected_key, expected_basis = feedback.memory._human_family(task, structural)

    monkeypatch.setattr(
        feedback,
        "candidate_tasks",
        lambda **_kwargs: {"tasks": [task]},
    )
    monkeypatch.setattr(feedback.memory, "_human_steps", lambda _task, _events: list(structural))
    monkeypatch.setattr(
        feedback,
        "_semantic_steps",
        lambda _task, _events: ["Gmail · Open email", "Salesforce · Open account"],
    )

    run = feedback._human_runs([])[0]
    assert run["family_key"] == expected_key
    assert run["family_basis"] == expected_basis
    assert run["structural_steps"] == structural
    assert run["semantic_steps"] == ["Gmail · Open email", "Salesforce · Open account"]


def _three_feedback_runs():
    sequence = [
        "Gmail · Open email",
        "Salesforce · Open account",
        "Google Sheets · Update status",
        "Gmail · Send",
    ]
    structural = ["surface:gmail", "action:click"]
    return [
        {
            "execution_id": f"execution:{index}",
            "family_key": "human:email.compose_send",
            "family_basis": "canonical_task_family",
            "started_at": f"2026-09-26T10:0{index}:00Z",
            "ended_at": f"2026-09-26T10:1{index}:00Z",
            "duration_seconds": 660.0,
            "outcome_status": "observed_completion",
            "outcome_basis": "strong_completion_anchor",
            "positive_example": True,
            "structural_steps": list(structural),
            "semantic_steps": list(sequence),
            "evidence_refs": [],
            "derived": True,
            "authoritative": False,
            "needs_review": True,
        }
        for index in range(3)
    ]


def test_readable_feedback_matches_exact_safe_steps_and_returns_next(monkeypatch):
    from server import procedural_feedback as feedback

    monkeypatch.setattr(feedback, "_human_runs", lambda _raw: _three_feedback_runs())
    result = feedback.readable_feedback(
        [],
        family_key="human:email.compose_send",
        current_steps="Gmail · Open email",
        min_support=2,
    )

    assert result["status"] == "ok"
    assert result["returned"] == 3
    assert result["runs"][0]["semantic_steps"] == [
        "Gmail · Open email",
        "Salesforce · Open account",
        "Google Sheets · Update status",
        "Gmail · Send",
    ]
    assert result["next_steps"]["candidates"][0]["step"] == "Salesforce · Open account"
    assert result["next_steps"]["candidates"][0]["support"] == 3
    assert result["median_completed_duration_seconds"] == 660.0
    assert result["family_keys_changed"] is False


def test_readable_feedback_unknown_step_is_explicit_and_unsafe_text_is_rejected(monkeypatch):
    from server import procedural_feedback as feedback

    monkeypatch.setattr(feedback, "_human_runs", lambda _raw: _three_feedback_runs())
    unknown = feedback.readable_feedback(
        [],
        family_key="human:email.compose_send",
        current_steps="Gmail · Archive thread",
    )
    assert unknown["status"] == "unrecognized_step"
    assert "Gmail · Archive thread" in unknown["unrecognized_steps"]
    assert "Gmail · Open email" in unknown["valid_semantic_steps"]
    assert unknown["runs"] == []

    with pytest.raises(ValueError):
        feedback.readable_feedback(
            [],
            family_key="human:email.compose_send",
            current_steps="Gmail · email anna@example.com",
        )


def test_compact_readable_progress_uses_safe_feedback_and_does_not_send_it_to_legacy_routes(monkeypatch):
    from mcp_server import compact

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def fake_get(path: str, params: dict | None = None):
        params = dict(params or {})
        calls.append((path, params))
        if path == "/v1/procedural-memory":
            return {"families": [{"family_key": "human:email.compose_send", "actor_kind": "human", "execution_count": 3}]}
        if path == "/v1/procedural-memory/readable-feedback":
            return {
                "status": "ok",
                "step_vocabulary": "privacy_safe_semantic",
                "identity_vocabulary": "legacy_structural_steps_unchanged",
                "valid_semantic_steps": [
                    "Gmail · Open email",
                    "Salesforce · Open account",
                    "Google Sheets · Update status",
                    "Gmail · Send",
                ],
                "runs": [{"execution_id": f"execution:{i}", "semantic_steps": ["Gmail · Open email", "Salesforce · Open account"]} for i in range(3)],
                "returned": 3,
                "median_completed_duration_seconds": 660.0,
                "next_steps": {"candidates": [{"step": "Salesforce · Open account", "support": 3}]},
            }
        if path.endswith("/similar-runs"):
            return {"runs": [], "returned": 0}
        if path.endswith("/failure-patterns"):
            return {"patterns": []}
        if path.endswith("/approval-patterns"):
            return {"patterns": []}
        if path.endswith("/next-steps"):
            return {"candidates": []}
        if path.endswith("/context-pack"):
            return {"family_key": params.get("family_key"), "similar_runs": []}
        raise AssertionError(path)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.how_did_similar_runs_go(
        family_key="human:email.compose_send",
        current_steps="Gmail · Open email",
    )
    assert result["status"] == "ok"
    assert result["similar_prior_runs"]["returned"] == 3
    assert result["frequently_observed_next_steps"]["candidates"][0]["step"] == "Salesforce · Open account"
    assert result["readable_progress_applied"] is True

    legacy_calls = {
        path: params
        for path, params in calls
        if path in {
            "/v1/procedural-memory/similar-runs",
            "/v1/procedural-memory/next-steps",
            "/v1/procedural-memory/context-pack",
        }
    }
    assert legacy_calls["/v1/procedural-memory/similar-runs"]["current_steps"] == ""
    assert legacy_calls["/v1/procedural-memory/next-steps"]["prefix"] == ""
    assert legacy_calls["/v1/procedural-memory/context-pack"]["current_steps"] == ""


def test_compact_readable_unknown_step_returns_valid_names_not_tool_error(monkeypatch):
    from mcp_server import compact

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def fake_get(path: str, params: dict | None = None):
        if path == "/v1/procedural-memory":
            return {"families": [{"family_key": "human:email.compose_send", "actor_kind": "human", "execution_count": 3}]}
        if path == "/v1/procedural-memory/readable-feedback":
            return {
                "status": "unrecognized_step",
                "unrecognized_steps": ["Gmail · Archive thread"],
                "valid_semantic_steps": ["Gmail · Open email", "Gmail · Send"],
                "instruction": "Use an exact valid semantic step.",
            }
        raise AssertionError(path)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.how_did_similar_runs_go(
        family_key="human:email.compose_send",
        current_steps="Gmail · Archive thread",
    )
    assert result["status"] == "unrecognized_step"
    assert result["valid_semantic_steps"] == ["Gmail · Open email", "Gmail · Send"]


def test_compact_current_context_default_is_small_on_representative_payload(monkeypatch):
    from mcp_server import compact

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    rows = [
        {
            "observed_at": f"2026-09-26T10:00:0{i}Z",
            "app": "Google Chrome",
            "window_title": "A very long customer document title that the overview should omit",
            "event_type": "browser_click",
            "duration_seconds": 1.2,
            "source": "browser",
            "action": "click",
            "target_label": "Open email",
            "target_role": "button",
            "metadata": {"large": "x" * 4000},
        }
        for i in range(6)
    ]

    def fake_get(path: str, params: dict | None = None):
        if path == "/v1/workflow-trace":
            return {
                "rows": rows,
                "returned": 6,
                "total": 25,
                "has_more": True,
                "next_cursor": "cursor",
                "scope": "current",
                "data_layer": "privacy_hardened_raw_rich_evidence",
            }
        if path == "/v1/tasks":
            return {"tasks": [], "patterns": []}
        if path == "/v1/semantic-activity":
            return {"events": []}
        raise AssertionError(path)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.get_current_work_context()
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 8192
    assert result["trace"]["rows_are_compact_overview"] is True
    assert "metadata" not in result["trace"]["rows"][0]
