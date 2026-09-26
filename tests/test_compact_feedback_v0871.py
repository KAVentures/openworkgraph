from __future__ import annotations

import inspect


def _fixture_payloads():
    overview = {
        "families": [
            {
                "family_key": "human:email.compose_send",
                "actor_kind": "human",
                "family_basis": "canonical_task_family",
                "execution_count": 3,
                "positive_example_count": 3,
                "explicit_failure_count": 0,
                "confidence": "low",
            },
            {
                "family_key": "agent:workflow:abc123",
                "actor_kind": "agent",
                "family_basis": "explicit_workflow_id",
                "execution_count": 2,
                "positive_example_count": 2,
                "explicit_failure_count": 0,
                "confidence": "low",
            },
        ]
    }
    tasks = {
        "tasks": [
            {
                "suggested_label": "Compose and send email",
                "task_family": "email.compose_send",
                "started_at": f"2026-09-26T10:0{i}:00Z",
                "ended_at": f"2026-09-26T10:0{i}:30Z",
            }
            for i in range(3)
        ],
        "patterns": [
            {
                "suggested_label": "Compose and send email",
                "task_family": "email.compose_send",
                "observed_count": 3,
            }
        ],
    }
    summary = {
        "repeated_task_patterns": [
            {
                "suggested_label": "Compose and send email",
                "task_family": "email.compose_send",
                "observed_count": 3,
            }
        ]
    }
    return overview, tasks, summary


def _install_feedback_fakes(monkeypatch):
    from mcp_server import compact

    overview, tasks, summary = _fixture_payloads()
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def fake_get(path: str, params: dict | None = None):
        params = dict(params or {})
        calls.append((path, params))
        if path == "/v1/tasks":
            return tasks
        if path == "/v1/summary":
            return summary
        if path == "/v1/procedural-memory":
            return overview
        if path == "/v1/procedural-memory/similar-runs":
            key = params.get("family_key")
            return {
                "family_key": key,
                "runs": [{"execution_id": f"execution:{i}"} for i in range(3)],
                "returned": 3,
            }
        if path == "/v1/procedural-memory/failure-patterns":
            return {"family_key": params.get("family_key"), "patterns": []}
        if path == "/v1/procedural-memory/approval-patterns":
            return {"family_key": params.get("family_key"), "patterns": []}
        if path == "/v1/procedural-memory/next-steps":
            return {"family_key": params.get("family_key"), "candidates": []}
        if path == "/v1/procedural-memory/context-pack":
            return {"family_key": params.get("family_key"), "similar_runs": []}
        if path == "/v1/workflow-trace":
            return {"rows": [], "returned": 0, "has_more": False}
        if path == "/v1/semantic-activity":
            return {"events": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    return compact, calls


def test_repeated_workflow_key_feeds_feedback_loop(monkeypatch):
    compact, _calls = _install_feedback_fakes(monkeypatch)

    repeated = compact.find_repeated_workflows(task_family="email.compose_send")
    assert repeated["patterns"][0]["task_family"] == "email.compose_send"
    assert repeated["patterns"][0]["family_key"] == "human:email.compose_send"
    assert repeated["examples"][0]["family_key"] == "human:email.compose_send"

    feedback = compact.how_did_similar_runs_go(
        family_key=repeated["patterns"][0]["family_key"]
    )
    assert feedback["status"] == "ok"
    assert feedback["family_key"] == "human:email.compose_send"
    assert feedback["resolution_method"] == "exact_family_key"
    assert feedback["similar_prior_runs"]["returned"] == 3


def test_feedback_accepts_bare_canonical_human_family_without_guessing_agent(monkeypatch):
    compact, _calls = _install_feedback_fakes(monkeypatch)

    result = compact.how_did_similar_runs_go(family_key="email.compose_send")
    assert result["status"] == "ok"
    assert result["family_key"] == "human:email.compose_send"
    assert result["resolution_method"] == "canonical_human_task_family"

    unknown = compact.how_did_similar_runs_go(family_key="missing.workflow")
    assert unknown["status"] == "unknown_family_key"
    assert unknown["resolved_family_keys"] == []
    assert {x["family_key"] for x in unknown["available_families"]} == {
        "human:email.compose_send",
        "agent:workflow:abc123",
    }


def test_feedback_without_family_key_never_validation_errors(monkeypatch):
    compact, _calls = _install_feedback_fakes(monkeypatch)

    result = compact.how_did_similar_runs_go()
    assert result["status"] == "ok"
    assert result["family_key"] == "human:email.compose_send"
    assert result["resolution_method"] == "current_task_exact_match"


def test_feedback_without_family_key_returns_selection_when_current_family_is_ambiguous(monkeypatch):
    compact, _calls = _install_feedback_fakes(monkeypatch)
    original_get = compact.secure_runtime.secure_get

    def ambiguous_get(path: str, params: dict | None = None):
        if path == "/v1/tasks" and (params or {}).get("scope") == "current":
            return {
                "tasks": [
                    {"task_family": "email.compose_send"},
                    {"task_family": "email.reply"},
                ]
            }
        if path == "/v1/procedural-memory":
            return {
                "families": [
                    {"family_key": "human:email.compose_send", "actor_kind": "human", "execution_count": 3},
                    {"family_key": "human:email.reply", "actor_kind": "human", "execution_count": 2},
                ]
            }
        return original_get(path, params)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", ambiguous_get)
    result = compact.how_did_similar_runs_go()
    assert result["status"] == "family_selection_required"
    assert result["resolved_family_keys"] == []
    assert len(result["available_families"]) == 2


def test_compact_context_defaults_are_smaller_but_explicit_limits_still_work(monkeypatch):
    compact, calls = _install_feedback_fakes(monkeypatch)

    compact.get_current_work_context()
    trace_call = next(params for path, params in calls if path == "/v1/workflow-trace")
    semantic_call = next(params for path, params in calls if path == "/v1/semantic-activity")
    assert trace_call["limit"] == 25
    assert semantic_call["limit"] == 20

    calls.clear()
    compact.get_workflow_trace()
    assert calls[-1][1]["limit"] == 25

    calls.clear()
    compact.get_workflow_trace(limit=100)
    assert calls[-1][1]["limit"] == 100


def test_feedback_and_task_context_tool_descriptions_explain_inputs():
    from mcp_server import compact

    feedback_doc = inspect.getdoc(compact.how_did_similar_runs_go) or ""
    context_doc = inspect.getdoc(compact.get_task_context) or ""
    assert "find_repeated_workflows" in feedback_doc
    assert "family_key" in feedback_doc
    assert "family_key" in context_doc
    assert "task_family" in context_doc
    assert "structural step" in context_doc
