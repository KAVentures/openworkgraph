"""Local agent known-case context: same saved-history guard, no new authority."""
from __future__ import annotations

import pytest

from server.business_case_context import observed_business_case_context

REF = "owg:r:" + "a" * 32


def graph(ref, *, since=None, until=None, include_source_status=False):
    assert ref == REF
    assert include_source_status is False
    return {
        "record_observed": True,
        "record_type": {"provider": "stripe", "kind": "invoice"},
        "record_identity_complete_within_retained_window": True,
        "timeline": [
            {"event_id": "ev1", "observed_at": "2026-10-10T10:00:00Z",
             "observed_action_type": "browser_click",
             "source": "browser_extension",
             "window_title": "PRIVATE CUSTOMER", "metadata": {"email": "secret"}},
        ],
        "source_evidence": {"availability": "unknown", "reason": "source_refresh_not_requested"},
    }


def test_readonly_case_context_preserves_unknown_source_and_workflow_mapping():
    context = observed_business_case_context(REF, record_reader=graph)
    assert context["record_observed"] is True
    assert context["record_type"] == {"provider": "stripe", "kind": "invoice"}
    assert context["source_reported_status"]["availability"] == "unknown"
    assert context["workflow_mapping"]["existing_workflow_discovery_remains_available"]
    assert context["authorization"]["agent_execution_authorized"] is False
    assert context["authorization"]["may_issue_refund"] is False
    assert "PRIVATE" not in str(context)
    assert "secret" not in str(context)


def test_independent_processor_receipt_does_not_claim_customer_outcome():
    def read(ref, **kw):
        v = graph(ref, **{**kw, "include_source_status": False})
        v["source_evidence"] = {
            "availability": "source_reported", "source_state_reported": True,
            "source_state": "paid", "verified_at": "2026-10-19T10:00:00Z",
            "verified_operational_outcome": {"kind": "stripe_invoice_paid",
                                            "verified": True, "scope": "stripe_processor_only"},
        }
        return v
    context = observed_business_case_context(REF, record_reader=read)
    assert context["source_reported_status"]["state"] == "paid"
    assert context["source_reported_status"]["verified_operational_outcome"]["verified"] is True
    assert context["source_reported_status"]["end_customer_outcome_verified"] is False
    assert context["authorization"]["agent_execution_authorized"] is False


def test_missing_record_remains_unknown_not_no_work():
    context = observed_business_case_context(
        REF, record_reader=lambda *_a, **_kw: {
            "record_observed": False, "source_evidence": {
                "availability": "unknown",
            },
        },
    )
    assert context["record_observed"] is False
    assert context["record_type"] is None
    assert context["confidence_and_unknowns"]["no_record_is_not_no_work"] is True


def test_local_mcp_tool_uses_exact_reference_and_existing_auth():
    from mcp_server.case_context_tools import register_case_context_tools
    from types import SimpleNamespace

    calls = []
    class FakeMCP:
        def tool(self, **kwargs):
            def wrapper(fn):
                self.fn = fn
                return fn
            return wrapper
    mcp = FakeMCP()
    runtime = SimpleNamespace(
        authorize_tool=lambda name: calls.append(("auth", name)),
        secure_get=lambda path, params: calls.append(("get", path, params)) or {"record_observed": True},
        core=SimpleNamespace(_finish=lambda name, data: calls.append(("finish", name)) or data),
    )
    register_case_context_tools(mcp, runtime)
    with pytest.raises(Exception):
        mcp.fn("native-stripe-invoice-id")
    assert calls == []
    context = mcp.fn(REF, include_source_status=False)
    assert context["record_observed"] is True
    assert calls[0] == ("auth", "get_case_context")
    assert calls[1][1] == "/v1/case-context"
    assert calls[1][2]["resource_ref"] == REF
    assert calls[1][2]["include_source_status"] is False


def test_central_history_guard_bounds_case_context_without_saved_access():
    from mcp_server.history_guard import install_history_guard
    from types import SimpleNamespace

    calls = []
    def get(path, params=None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": "off"}}
        if path == "/v1/capture/status":
            return {"run_started_at": "2026-10-19T08:00:00Z"}
        calls.append((path, params))
        return {}
    runtime = SimpleNamespace(secure_get=get, core=SimpleNamespace(_get=get))
    install_history_guard(runtime)
    runtime.secure_get("/v1/case-context", {
        "resource_ref": REF, "since": "2026-01-01T00:00:00Z",
    })
    assert calls == [("/v1/case-context", {
        "resource_ref": REF, "since": "2026-10-19T08:00:00+00:00",
    })]


def test_secure_app_case_context_requires_auth(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = """
import server.secure_app
from fastapi.testclient import TestClient
from server.secure_app import app
r = TestClient(app).get('/v1/case-context', params={'resource_ref': 'owg:r:' + 'a'*32})
assert r.status_code == 401, r.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    r = subprocess.run([sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]), env=env,
        capture_output=True, text=True, timeout=75 if os.name == "nt" else 35)
    assert r.returncode == 0, r.stderr[-3000:]
