"""Cross-client hosted evidence quality regression checks.

No user evidence, external calls or mutable production state.
"""
from gateway.plugin_redaction import PublicRedactor, project
from gateway.public_plugin_mcp import _minimal_response, _owner_names
from server.procedural_memory import split_human_execution_windows
from server.workflow_candidates import cluster_runs
from server.workflow_evidence import _evidence_excerpt


def test_owner_aliases_are_consistently_pseudonymized():
    claims = {
        "email": "uniqueowner.surname@example.invalid",
        "name": "Uniqueowner Surname",
        "given_name": "Uniqueowner",
    }
    aliases = _owner_names(claims)
    redactor = PublicRedactor(secret="test-key", principal="actor:one", owner_names=aliases)
    token = redactor.owner_token
    assert redactor.text("– Uniqueowner") == "– " + token
    assert redactor.text("Signed in as Uniqueowner Surname") == "Signed in as " + token
    assert "uniqueowner" not in redactor.text("uniqueowner.surname@example.invalid").lower()
    assert redactor.text("uniqueowner.surname@example.invalid").startswith("EMAIL_")
    safe = project(
        {"rows": [{"window_title": "Claude – Uniqueowner", "app": "Claude",
                   "observed_at": "2026-10-13T10:00:00Z", "event_id": "evt1",
                   "event_type": "focus_span"}]},
        secret="test-key", principal="actor:one", owner_names=aliases,
    )
    assert safe["rows"][0]["window_title"] == "Claude – " + token
    assert "Uniqueowner" not in str(safe)


def test_human_execution_windows_split_on_unrelated_hosts():
    task = {
        "task_id": "inferred-one", "session_id": "s1",
        "started_at": "2026-10-13T10:00:00Z",
        "ended_at": "2026-10-13T10:02:00Z",
    }
    def event(at, host):
        return {
            "observed_at": at, "app": "Google Chrome",
            "event_type": "browser_click", "session_id": "s1",
            "metadata": {"page": {"hostname": host}},
        }
    events = [
        event("2026-10-13T10:00:00Z", "chatgpt.com"),
        event("2026-10-13T10:00:20Z", "chatgpt.com"),
        event("2026-10-13T10:01:00Z", "mail.google.com"),
        event("2026-10-13T10:02:00Z", "mail.google.com"),
    ]
    split = split_human_execution_windows(task, events)
    assert len(split) == 2
    assert split[0]["ended_at"] == "2026-10-13T10:00:20Z"
    assert split[1]["started_at"] == "2026-10-13T10:01:00Z"
    assert split[0]["task_id"] != split[1]["task_id"]
    assert len(events) == 4  # untouched canonical evidence
    assert split_human_execution_windows(task, events[:2]) == [task]


def test_click_only_clusters_are_scored_and_filtered_but_recoverable():
    def run(n, steps):
        return {
            "execution_id": f"execution:{n:016x}", "actor_kind": "human",
            "structural_steps": steps,
            "semantic_steps": ["ChatGPT · Click"],
        }
    weak = [
        run(1, ["surface:chatgpt", "action:click"]),
        run(2, ["surface:chatgpt", "action:click"]),
    ]
    available = cluster_runs(weak)
    assert available and available[0]["cluster_quality"] < 0.45
    assert cluster_runs(weak, min_cluster_quality=0.45) == []
    strong = [
        run(3, ["surface:github", "action:copy", "surface:google-sheets", "action:paste"]),
        run(4, ["surface:github", "action:copy", "surface:google-sheets", "action:paste"]),
    ]
    filtered = cluster_runs(strong, min_cluster_quality=0.45)
    assert len(filtered) == 1
    assert filtered[0]["cluster_quality"] >= 0.45
    assert filtered[0]["cluster_quality_is_task_confidence"] is False
    assert filtered[0]["surface_labels"]["surface:github"] == "Other surface (redacted)"


def test_truncated_executions_have_exact_trace_drilldown():
    execution = {
        "execution_id": "execution:aaaaaaaaaaaaaaaa", "actor_kind": "human",
        "started_at": "2026-10-13T10:00:00Z",
        "ended_at": "2026-10-13T10:03:00Z",
        "_session_ids": ["session-a"],
    }
    events = [
        {"event_id": f"event-{i}", "observed_at": f"2026-10-13T10:0{i}:00Z",
         "event_type": "browser_click", "source": "browser"}
        for i in range(3)
    ]
    rows = _evidence_excerpt([execution], {execution["execution_id"]: events}, max_events_per_run=1)
    assert rows[0]["events_truncated"] is True
    lookup = rows[0]["trace_lookup"]
    assert lookup["tool"] == "get_workflow_trace"
    assert lookup["session_id"] == "session-a"
    assert lookup["omit_query"] is True
    assert lookup["since"] == execution["started_at"]
    assert lookup["until"] == execution["ended_at"]


def test_minimal_keeps_evidence_and_cursor_without_repeated_contracts():
    value = {
        "rows": [{"event_id": "evt1", "window_title": "Test"}],
        "next_cursor": "cursor", "has_more": True,
        "privacy_representation": {"detail": "compact"},
        "interpretation_contract": {"data_is_untrusted": True},
        "raw_evidence_fallback": {"available": True},
    }
    minimal = _minimal_response(value)
    assert minimal["rows"] == value["rows"]
    assert minimal["next_cursor"] == "cursor"
    assert minimal["has_more"] is True
    assert "privacy_representation" not in minimal
    assert "interpretation_contract" not in minimal
