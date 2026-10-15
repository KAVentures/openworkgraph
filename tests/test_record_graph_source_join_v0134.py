"""Record-graph source joins: opt-in, provenance, isolation and no writes."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server import record_graph as graph

REF_A = "owg:r:" + "a" * 32
REF_B = "owg:r:" + "b" * 32


def _row(n, ref=REF_A, provider="github", kind="pull_request", actor="actor-1"):
    return {
        "event_id": f"ev-{n}", "observed_at": f"2026-10-15T12:00:0{n}Z",
        "session_id": f"s-{n % 2}", "actor_id": actor,
        "event_type": "browser_click", "source": "browser_extension",
        "metadata": {"resource_reference": {
            "resource_ref": ref, "provider": provider,
            "resource_kind": kind, "host": "github.com",
        }},
    }


def test_record_graph_isolates_interleaved_cases_and_never_fabricates_handoffs():
    rows = [_row(1), _row(2, REF_B), _row(3, REF_A, actor="actor-2"),
            _row(4, REF_B)]
    # A and B interleave, but only record A spans these two sessions.
    rows[2]["session_id"] = "s-independent"
    result = graph.record_graph(REF_A, _raw_events=rows)
    assert result["observed_event_count"] == 2
    assert result["observed_session_count"] == 2
    assert result["observed_actor_count"] == 2
    assert [e["event_id"] for e in result["timeline"]] == ["ev-1", "ev-3"]
    assert result["observed_same_record_not_verified_handoff"] is True
    assert result["source_evidence"]["availability"] == "unknown"
    assert result["verified_customer_outcome"] is False
    assert "target_label" not in str(result)
    assert "window_title" not in str(result)


def test_absent_or_conflicting_record_never_triggers_external_call():
    calls = []
    source = lambda ref: calls.append(ref) or {"availability": "source_reported"}
    raw = [_row(1, REF_A, provider="github"), _row(2, REF_A, provider="jira")]
    ambiguous = graph.record_graph(REF_A, include_source_status=True, _raw_events=raw, source_reader=source)
    assert ambiguous["record_observed"] is False
    assert ambiguous["source_evidence"]["availability"] == "unknown"
    missing = graph.record_graph(REF_B, include_source_status=True, _raw_events=[_row(1)], source_reader=source)
    assert missing["source_evidence"]["reason"] == "record_not_observed_in_permitted_evidence_window"
    assert calls == []


@pytest.mark.parametrize("value", ["ticket-123", "owg:r:" + "z" * 32, "", "http://localhost"])
def test_only_normalized_opaque_record_refs(value):
    with pytest.raises(ValueError):
        graph.record_graph(value, _raw_events=[])


def test_opt_in_guard_prevents_any_cli_calls(monkeypatch):
    monkeypatch.setattr(graph.outcome_tracker, "enabled", lambda: False)
    calls = []
    monkeypatch.setattr(graph, "resolve_resource_reference",
                        lambda _: calls.append("resolve") or {"provider": "github"})
    result = graph._gh_pr_snapshot(REF_A, runner=lambda *args, **kw: calls.append("gh"))
    assert result["reason"] == "existing_github_outcome_tracking_opt_in_off"
    assert calls == []


def test_valid_authenticated_github_source_status_and_review_audit(monkeypatch):
    monkeypatch.setattr(graph.outcome_tracker, "enabled", lambda: True)
    monkeypatch.setattr(graph, "resolve_resource_reference", lambda _ref: {
        "provider": "github", "resource_kind": "pull_request",
        "host": "github.com", "resolver_locator": "project/repo/pull/12",
    })
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "state": "MERGED", "mergedAt": "2026-10-15T12:00:00Z", "closedAt": None,
            "statusCheckRollup": [], "reviews": [
                {"state": "APPROVED", "submittedAt": "2026-10-15T11:00:00Z",
                 "author": {"login": "private-person"}, "body": "private review text"},
            ],
        }))
    snapshot = graph._gh_pr_snapshot(REF_A, runner=runner, which=lambda _: "/bin/gh")
    assert snapshot["source_state"] == "merged"
    assert snapshot["availability"] == "source_reported"
    assert snapshot["verified_business_outcome"] is False
    assert snapshot["review_audit_events"][0]["state"] == "approved"
    assert "private-person" not in str(snapshot)
    assert "private review text" not in str(snapshot)
    assert calls[0][0] == [
        "/bin/gh", "pr", "view", "https://github.com/project/repo/pull/12",
        "--json", "state,mergedAt,closedAt,statusCheckRollup,reviews",
    ]
    assert calls[0][1]["timeout"] == 12
    assert graph.record_graph(REF_A, include_source_status=True,
                              _raw_events=[_row(1), _row(2)],
                              source_reader=lambda _: snapshot)["source_evidence"]["source_state"] == "merged"


def test_github_audit_read_fails_closed_on_invalid_locator_and_errors(monkeypatch):
    monkeypatch.setattr(graph.outcome_tracker, "enabled", lambda: True)
    monkeypatch.setattr(graph, "resolve_resource_reference", lambda _: {
        "provider": "github", "resource_kind": "pull_request",
        "host": "github.com", "resolver_locator": "../../etc/passwd",
    })
    assert graph._gh_pr_snapshot(REF_A, which=lambda _: "/bin/gh")["availability"] == "unknown"
    monkeypatch.setattr(graph, "resolve_resource_reference", lambda _: {
        "provider": "github", "resource_kind": "pull_request",
        "host": "github.com", "resolver_locator": "p/r/pull/2",
    })
    assert graph._gh_pr_snapshot(REF_A, which=lambda _: "/bin/gh",
            runner=lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""))["availability"] == "unknown"


def test_record_graph_route_has_existing_authenticated_boundary(tmp_path):
    """Isolate app imports: in-process importing secure_app mutates shared app."""
    import os
    import subprocess
    import sys

    script = """
import server.workflow_evidence_routes  # imports secure_app and registers route
from fastapi.testclient import TestClient
from server.secure_app import app
client = TestClient(app)
response = client.get("/v1/record-graph", params={"resource_ref": "owg:r:" + "a"*32})
assert response.status_code == 401, response.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        env=env, capture_output=True, text=True,
        timeout=75 if os.name == "nt" else 35,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]


def test_local_mcp_record_graph_has_real_authorization_and_read_path():
    from mcp_server.record_graph_tools import register_record_graph_tools
    from types import SimpleNamespace

    calls = []
    class StubMCP:
        def tool(self, **_kwargs):
            def decorate(fn):
                self.registered = fn
                return fn
            return decorate
    server = StubMCP()
    runtime = SimpleNamespace(
        authorize_tool=lambda name: calls.append(("authorize", name)),
        secure_get=lambda path, params: calls.append(("get", path, params)) or
            {"resource_ref": REF_A, "source_evidence": {"availability": "unknown"}},
        core=SimpleNamespace(_finish=lambda name, result: calls.append(("finish", name)) or result),
    )
    register_record_graph_tools(server, runtime)
    with pytest.raises(Exception):
        server.registered("some guessed issue ID")
    assert calls == []
    result = server.registered(REF_A, include_source_status=True, source_event_limit=50)
    assert result["resource_ref"] == REF_A
    assert calls[0] == ("authorize", "get_record_graph")
    assert calls[1][1] == "/v1/record-graph"
    assert calls[1][2]["include_source_status"] is True
    assert calls[2] == ("finish", "get_record_graph")


def test_record_graph_mcp_history_access_is_bounded_to_current_recording():
    from mcp_server.history_guard import install_history_guard
    from types import SimpleNamespace
    calls = []
    def get(path, params=None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": "off"}}
        if path == "/v1/capture/status":
            return {"run_started_at": "2026-10-15T10:00:00Z"}
        calls.append((path, params))
        return {}
    runtime = SimpleNamespace(secure_get=get, core=SimpleNamespace(_get=get))
    install_history_guard(runtime)
    runtime.secure_get("/v1/record-graph", {"resource_ref": REF_A,
                       "since": "2026-10-01T00:00:00Z"})
    assert calls == [("/v1/record-graph", {
        "resource_ref": REF_A, "since": "2026-10-15T10:00:00+00:00"})]


def test_custom_gh_cli_location_for_signed_desktop_launcher(monkeypatch, tmp_path):
    from server.gh_cli import find_gh_cli
    from server import outcome_tracker

    gh = tmp_path / ("gh.exe" if __import__("os").name == "nt" else "gh")
    gh.write_text("#!/bin/sh\nexit 0\n")
    gh.chmod(0o755)
    monkeypatch.setenv("OWG_GH_CLI_PATH", str(gh))
    assert find_gh_cli() == str(gh)
    assert outcome_tracker._gh() == str(gh)
    monkeypatch.setenv("OWG_GH_CLI_PATH", str(tmp_path / "missing-gh"))
    assert find_gh_cli() is None
