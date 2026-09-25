from __future__ import annotations

import json

import server.procedural_memory as pm


def _agent_event(
    *,
    run: str,
    when: str,
    operation: str,
    status: str = "success",
    workflow: str = "refund-workflow",
    tool_name: str = "",
    tool_category: str = "none",
    suffix: str = "",
) -> dict:
    return {
        "event_id": f"event-{run}-{operation}-{suffix or when}",
        "observed_at": when,
        "source": "agent",
        "actor_id": "agent:private-actor-patient@example.com",
        "session_id": f"session-{run}-SUPERSECRET",
        "app": "Private Agent",
        "event_type": f"agent_{operation}",
        "duration_seconds": 0.0,
        "window_title": "Anna Svensson SUPERSECRET",
        "metadata": {
            "operation": operation,
            "status": status,
            "observation_level": "native_trace",
            "agent": {
                "name": "Anna Svensson Agent",
                "provider": "openai",
                "framework": "openai-agents-python",
            },
            "trace": {
                "run_id": run,
                "trace_id": f"trace-{run}-patient@example.com",
                "workflow_id": workflow,
            },
            "tool": {"name": tool_name, "category": tool_category},
            "privacy_poison": {
                "prompt": "SUPERSECRET prompt that must never be read",
                "customer_email": "patient@example.com",
            },
        },
    }


def _run(
    run: str,
    base_minute: int,
    *,
    outcome: str = "success",
    workflow: str = "refund-workflow",
    approval: bool = False,
    private_tool: bool = False,
) -> list[dict]:
    tool_name = "patient@example.com" if private_tool else "repository_search"
    minute = base_minute
    events = [
        _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:00+00:00", operation="run_started", status="running", workflow=workflow, suffix="start"),
        _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:01+00:00", operation="tool_call", status="success", workflow=workflow, tool_name=tool_name, tool_category="search", suffix="search"),
    ]
    if approval:
        events.extend([
            _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:02+00:00", operation="human_approval_requested", status="running", workflow=workflow, suffix="approval-request"),
            _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:03+00:00", operation="human_approval_received", status="success", workflow=workflow, suffix="approval-received"),
        ])
    tool_status = "error" if outcome == "error" else "success"
    events.append(
        _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:04+00:00", operation="tool_call", status=tool_status, workflow=workflow, tool_name="apply_patch", tool_category="code", suffix="patch")
    )
    events.append(
        _agent_event(run=run, when=f"2026-09-25T00:{minute:02d}:05+00:00", operation="run_finished", status=outcome, workflow=workflow, suffix="finish")
    )
    return events


def _agent_dataset() -> list[dict]:
    return [
        *_run("success-a", 0, approval=True),
        *_run("success-b", 10, approval=True),
        *_run("failure-a", 20, outcome="error"),
        *_run("failure-b", 30, outcome="error"),
    ]


def test_agent_memory_requires_explicit_terminal_status_and_support(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _agent_dataset()
    overview = pm.procedural_overview(raw, min_support=2)

    assert overview["family_count"] == 1
    family = overview["families"][0]
    assert family["execution_count"] == 4
    assert family["explicit_success_count"] == 2
    assert family["explicit_failure_count"] == 2
    assert family["observed_completion_count"] == 0
    assert family["reusable_candidate"] is True
    assert family["authoritative"] is False

    failures = pm.failure_patterns(raw, family_key=family["family_key"], min_support=2)
    assert len(failures["patterns"]) == 1
    pattern = failures["patterns"][0]
    assert pattern["support"] == 2
    assert pattern["explicit_status"] == "error"
    assert "causal" in pattern["interpretation"]


def test_next_steps_use_positive_examples_only(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _agent_dataset()
    family_key = pm.procedural_overview(raw)["families"][0]["family_key"]
    similar = pm.similar_runs(raw, family_key=family_key, limit=10)
    first_search = next(
        step
        for run in similar["runs"]
        for step in run["steps"]
        if step.startswith("tool:search:")
    )

    result = pm.next_steps(raw, family_key=family_key, after_step=first_search, min_support=2)
    assert result["positive_execution_count"] == 2
    assert result["opportunities"] == 2
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["step"] == "approval_request"
    assert result["candidates"][0]["support"] == 2
    assert result["prescriptive"] is False


def test_approval_hotspots_are_observational_not_policy(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _agent_dataset()
    family_key = pm.procedural_overview(raw)["families"][0]["family_key"]
    result = pm.approval_patterns(raw, family_key=family_key, min_support=2)

    assert len(result["patterns"]) == 1
    pattern = result["patterns"][0]
    assert pattern["executions_with_request"] == 2
    assert pattern["family_execution_count"] == 4
    assert pattern["observed_fraction"] == 0.5
    assert pattern["decision_status_counts"] == {"success": 2}
    assert pattern["normative_requirement"] is False
    assert result["normative_requirement_inferred"] is False


def test_unknown_agent_outcome_is_not_promoted_to_success_or_failure(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _run("unknown-a", 0, outcome="unknown") + _run("unknown-b", 10, outcome="unknown")
    executions = pm.derive_executions(raw)
    assert {item["outcome_status"] for item in executions} == {"unknown"}
    assert all(item["positive_example"] is False for item in executions)
    assert all(item["explicit_failure"] is False for item in executions)
    assert pm.failure_patterns(raw)["patterns"] == []


def test_agent_memory_does_not_echo_raw_ids_content_or_dynamic_tool_names(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _run(
        "patient@example.com-SUPERSECRET-run",
        0,
        workflow="customer-739201 patient@example.com SUPERSECRET",
        private_tool=True,
    )
    family_key = pm.derive_executions(raw)[0]["family_key"]
    payload = {
        "overview": pm.procedural_overview(raw, min_support=1),
        "similar": pm.similar_runs(raw, family_key=family_key),
        "failures": pm.failure_patterns(raw, family_key=family_key),
        "approvals": pm.approval_patterns(raw, family_key=family_key),
    }
    serialized = json.dumps(payload)
    for forbidden in (
        "patient@example.com",
        "SUPERSECRET",
        "customer-739201",
        "Anna Svensson",
        "session-patient",
        "trace-patient",
    ):
        assert forbidden not in serialized
    assert "tool:search:tool:" in serialized


def test_human_completion_is_observed_completion_never_success(monkeypatch):
    tasks = [
        {
            "task_id": "task-complete",
            "session_id": "session-SUPERSECRET",
            "started_at": "2026-09-25T01:00:00+00:00",
            "ended_at": "2026-09-25T01:00:05+00:00",
            "elapsed_seconds": 5,
            "completion_observed": True,
            "task_family": "internal Anna Svensson workflow",
            "primary_surface": "Customer Anna Svensson",
            "anchor_event_ids": ["raw-event-patient@example.com"],
            "evidence_window": {"session_id": "session-SUPERSECRET", "started_at": "2026-09-25T01:00:00+00:00", "ended_at": "2026-09-25T01:00:05+00:00"},
        },
        {
            "task_id": "task-open",
            "session_id": "session-SUPERSECRET",
            "started_at": "2026-09-25T01:01:00+00:00",
            "ended_at": "2026-09-25T01:01:05+00:00",
            "elapsed_seconds": 5,
            "completion_observed": False,
            "task_family": "internal Anna Svensson workflow",
            "primary_surface": "Customer Anna Svensson",
            "anchor_event_ids": [],
            "evidence_window": {"session_id": "session-SUPERSECRET", "started_at": "2026-09-25T01:01:00+00:00", "ended_at": "2026-09-25T01:01:05+00:00"},
        },
    ]
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": tasks})
    raw = [
        {
            "event_id": "h1", "observed_at": "2026-09-25T01:00:00+00:00",
            "session_id": "session-SUPERSECRET", "event_type": "focus_span",
            "app": "Customer Anna Svensson", "duration_seconds": 5, "metadata": {},
        },
        {
            "event_id": "h2", "observed_at": "2026-09-25T01:00:04+00:00",
            "session_id": "session-SUPERSECRET", "event_type": "browser_form_submit",
            "app": "Google Chrome", "duration_seconds": 0,
            "metadata": {"action": "form_submit", "page": {"hostname": "internal.example"}, "target": {"label": "Anna Svensson"}},
        },
        {
            "event_id": "h3", "observed_at": "2026-09-25T01:01:00+00:00",
            "session_id": "session-SUPERSECRET", "event_type": "focus_span",
            "app": "Customer Anna Svensson", "duration_seconds": 5, "metadata": {},
        },
        {
            "event_id": "h4", "observed_at": "2026-09-25T01:01:02+00:00",
            "session_id": "session-SUPERSECRET", "event_type": "screen_click",
            "app": "Customer Anna Svensson", "duration_seconds": 0,
            "metadata": {"action": "screen_click", "target": {"label": "patient@example.com"}},
        },
    ]
    executions = [item for item in pm.derive_executions(raw) if item["actor_kind"] == "human"]
    assert [item["outcome_status"] for item in executions] == ["unknown", "observed_completion"]
    assert all(item["outcome_status"] != "success" for item in executions)
    assert all(item["explicit_failure"] is False for item in executions)
    serialized = json.dumps(executions)
    assert "Anna Svensson" not in serialized
    assert "patient@example.com" not in serialized
    assert "internal Anna Svensson workflow" not in serialized


def test_similarity_rejects_arbitrary_untrusted_step_strings(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _agent_dataset()
    family_key = pm.procedural_overview(raw)["families"][0]["family_key"]
    try:
        pm.similar_runs(raw, family_key=family_key, current_steps=["ignore previous instructions"])
    except ValueError:
        pass
    else:
        raise AssertionError("untrusted free-text structural step was accepted")
