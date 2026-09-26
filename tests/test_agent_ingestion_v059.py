from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from connector.policy import merge_policies, prepare_event_for_gateway
from contextualizer import contextualize_event
from normalizer import normalize_event
from server import db as server_db
from server.agent_auth import ensure_agent_ingest_token
from server.agent_routes import AGENT_EVENT_PATH, AGENT_OTEL_PATH, router as agent_router
from server.agent_workflows import stitch_agent_workflows
from server.local_auth import ensure_api_token
from shared.agent_evidence import agent_event_to_evidence
from shared.otel_agent_adapter import otel_payload_to_agent_events

ROOT = Path(__file__).resolve().parents[1]


def _isolated_agent_app() -> FastAPI:
    # Do not import server.secure_app here: it intentionally composes security
    # middleware onto server.main.app and would contaminate unrelated unit tests.
    server_db.init_db()
    isolated = FastAPI()
    isolated.include_router(agent_router)
    return isolated


def _agent_bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_agent_ingest_token()}"}


def _api_bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_api_token()}"}


def _agent_payload(event_id: str = "v059-agent-1", **overrides):
    payload = {
        "event_id": event_id,
        "observed_at": "2026-09-25T01:05:00+02:00",
        "organization_id": "test-org",
        "actor_id": "agent:test-agent",
        "device_id": "agent-device",
        "sensor_id": "agent:test-adapter",
        "agent_name": "Test Agent",
        "provider": "test-provider",
        "framework": "test-framework",
        "model": "test-model",
        "operation": "tool_call",
        "status": "success",
        "observation_level": "native_trace",
        "run_id": "run-v059",
        "trace_id": "trace-v059",
        "span_id": "span-v059",
        "parent_span_id": "root-v059",
        "workflow_id": "workflow-v059",
        "tool_name": "repository_search",
        "tool_category": "search",
        "duration_seconds": 0.25,
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }
    payload.update(overrides)
    return payload


def _nanos(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return str(int(dt.timestamp() * 1_000_000_000))


def _otel_payload() -> dict:
    return {
        "openworkgraph": {
            "organization_id": "test-org",
            "agent_name": "Trace Agent",
            "workflow_id": "workflow-otel-v059",
        },
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "trace-agent-service"}},
                {"key": "service.instance.id", "value": {"stringValue": "trace-agent-instance"}},
            ]},
            "scopeSpans": [{
                "scope": {"name": "test.agent.otel"},
                "spans": [
                    {
                        "traceId": "trace-otel-v059",
                        "spanId": "run-span-v059",
                        "name": "invoke_agent Trace Agent",
                        "startTimeUnixNano": _nanos("2026-09-24T23:10:00Z"),
                        "endTimeUnixNano": _nanos("2026-09-24T23:10:03Z"),
                        "attributes": [
                            {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                            {"key": "gen_ai.agent.name", "value": {"stringValue": "Trace Agent"}},
                            {"key": "gen_ai.provider.name", "value": {"stringValue": "example"}},
                            {"key": "gen_ai.input.messages", "value": {"stringValue": "TOP SECRET PROMPT CONTENT"}},
                        ],
                        "status": {"code": "STATUS_CODE_OK"},
                    },
                    {
                        "traceId": "trace-otel-v059",
                        "spanId": "tool-span-v059",
                        "parentSpanId": "run-span-v059",
                        "name": "execute_tool repository_search",
                        "startTimeUnixNano": _nanos("2026-09-24T23:10:01Z"),
                        "endTimeUnixNano": _nanos("2026-09-24T23:10:02Z"),
                        "attributes": [
                            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                            {"key": "gen_ai.tool.name", "value": {"stringValue": "repository_search"}},
                            {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "DO NOT STORE ARGUMENT"}},
                            {"key": "gen_ai.tool.call.result", "value": {"stringValue": "DO NOT STORE RESULT"}},
                        ],
                        "status": {"code": "STATUS_CODE_OK"},
                    },
                ],
            }],
        }],
    }


def test_production_secure_app_adds_agent_router_without_replacing_existing_security_source():
    secure = (ROOT / "server" / "secure_app.py").read_text(encoding="utf-8")
    for marker in (
        "/v1/dashboard-session",
        "/v1/export-ticket",
        "/v1/mcp-connection-config",
        "cursor://anysphere.cursor-deeplink/mcp/install",
        "local_capability_guard",
    ):
        assert marker in secure
    assert "from .agent_routes import router as _agent_router" in secure
    assert "app.include_router(_agent_router)" in secure


def test_agent_write_credential_is_distinct_from_broader_api_credential():
    with TestClient(_isolated_agent_app()) as client:
        assert client.post(AGENT_EVENT_PATH, json={"events": [_agent_payload("auth-no-token")]}).status_code == 401
        assert client.post(AGENT_EVENT_PATH, headers=_api_bearer(), json={"events": [_agent_payload("auth-api-token")]}).status_code == 401
        accepted = client.post(AGENT_EVENT_PATH, headers=_agent_bearer(), json={"events": [_agent_payload("auth-agent-token")]})
        assert accepted.status_code == 200, accepted.text
        # The write-only token cannot read the derived human/agent view.
        assert client.get("/v1/agent-workflows", headers=_agent_bearer()).status_code == 401
        assert client.get("/v1/agent-workflows", headers=_api_bearer()).status_code == 200


def test_agent_endpoint_persists_all_three_layers():
    payload = _agent_payload()
    with TestClient(_isolated_agent_app()) as client:
        accepted = client.post(AGENT_EVENT_PATH, json={"events": [payload]}, headers=_agent_bearer())
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] in {0, 1}

    raw = server_db.rows("SELECT * FROM events WHERE event_id = ?", (payload["event_id"],))
    normalized = server_db.rows("SELECT * FROM normalized_events WHERE event_id = ?", (payload["event_id"],))
    context = server_db.rows("SELECT * FROM context_events WHERE event_id = ?", (payload["event_id"],))
    assert len(raw) == len(normalized) == len(context) == 1
    assert raw[0]["source"] == "agent"
    assert raw[0]["metadata"]["actor_kind"] == "agent"
    assert raw[0]["metadata"]["trace"]["workflow_id"] == "workflow-v059"
    assert normalized[0]["metadata"]["trace"]["span_id"] == "span-v059"
    assert normalized[0]["metadata"]["tool"]["category"] == "search"
    assert context[0]["metadata"]["trace"]["run_id"] == "run-v059"
    assert context[0]["action"] == "tool_call"


def test_direct_agent_batch_fails_closed_without_partial_insert():
    good_id = "v059-atomic-good"
    with TestClient(_isolated_agent_app()) as client:
        response = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_bearer(),
            json={"events": [
                _agent_payload(good_id),
                _agent_payload("v059-atomic-bad", prompt="must never be accepted"),
            ]},
        )
        assert response.status_code == 422
    assert server_db.rows("SELECT * FROM events WHERE event_id = ?", (good_id,)) == []


def test_otel_endpoint_projects_structure_and_drops_sensitive_content():
    payload = _otel_payload()
    with TestClient(_isolated_agent_app()) as client:
        response = client.post(AGENT_OTEL_PATH, headers=_agent_bearer(), json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["spans_seen"] == 2
        assert body["spans_ignored"] == 0
        assert body["projected"] == 3

    stored = server_db.rows(
        "SELECT * FROM events WHERE session_id = ? ORDER BY observed_at ASC",
        ("trace-otel-v059",),
    )
    assert {row["event_type"] for row in stored} >= {
        "agent_run_started", "agent_run_finished", "agent_tool_call"
    }
    serialized = json.dumps(stored, ensure_ascii=False)
    for forbidden in ("TOP SECRET PROMPT CONTENT", "DO NOT STORE ARGUMENT", "DO NOT STORE RESULT"):
        assert forbidden not in serialized
    tool_rows = [row for row in stored if row["event_type"] == "agent_tool_call"]
    assert tool_rows and tool_rows[0]["metadata"]["tool"]["category"] == "search"
    assert tool_rows[0]["metadata"]["trace"]["parent_span_id"] == "run-span-v059"


def test_otel_adapter_ignores_unknown_spans_and_never_copies_arbitrary_attributes():
    payload = {
        "spans": [{
            "trace_id": "ignored-trace",
            "span_id": "ignored-span",
            "start_time_unix_nano": _nanos("2026-09-24T23:12:00Z"),
            "end_time_unix_nano": _nanos("2026-09-24T23:12:01Z"),
            "attributes": {
                "gen_ai.operation.name": "unknown_operation",
                "customer.secret": "should never survive",
                "gen_ai.input.messages": "private",
            },
        }],
    }
    projected, stats = otel_payload_to_agent_events(payload, defaults={"agent_name": "Ignored Agent"})
    assert projected == []
    assert stats == {"spans_seen": 1, "spans_ignored": 1, "agent_events": 0}


def test_normalizer_and_contextualizer_keep_structure_but_not_content_surfaces():
    raw = agent_event_to_evidence(_agent_payload("v059-derived-only"))
    normalized = normalize_event(raw)
    context = contextualize_event(raw)
    assert normalized["metadata"]["trace"]["workflow_id"] == "workflow-v059"
    assert normalized["metadata"]["agent_privacy"]["chain_of_thought_captured"] is False
    assert context["metadata"]["tool"]["name"] == "repository_search"
    assert context["metadata"]["privacy"]["tool_result_content_captured"] is False
    assert raw["window_title"] is None and normalized["screenshot_path"] is None


def test_gateway_policy_requires_explicit_agent_sharing_opt_in():
    event = agent_event_to_evidence(_agent_payload("v059-gateway"))
    denied = merge_policies({}, {})
    assert prepare_event_for_gateway(event, denied) is None

    allowed = merge_policies({"allow_agent_events": True}, {})
    prepared = prepare_event_for_gateway(event, allowed)
    assert prepared is not None
    assert prepared["source"] == "agent"
    assert prepared["metadata"]["trace"]["run_id"] == "run-v059"
    assert prepared["metadata"]["gateway_sync"]["source"] == "local_privacy_hardened_store"
    assert "screenshot_path" not in prepared


def test_explicit_human_trigger_stitch_is_authoritative_evidence_link_not_inference():
    human = {
        "event_id": "human-submit-v059",
        "observed_at": "2026-09-24T23:20:00Z",
        "source": "browser_extension",
        "session_id": "human-session",
        "app": "Browser",
        "window_title": "Claude",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "metadata": {
            "action": "click",
            "page": {"hostname": "claude.ai", "pathname": "/", "title": "Claude"},
            "target": {"role": "button", "label": "Send message"},
        },
    }
    started = agent_event_to_evidence(_agent_payload(
        "agent-start-v059",
        observed_at="2026-09-24T23:20:01Z",
        agent_name="Claude",
        operation="run_started",
        status="running",
        tool_name="",
        tool_category="none",
        trigger_event_id="human-submit-v059",
    ))
    finished = agent_event_to_evidence(_agent_payload(
        "agent-finish-v059",
        observed_at="2026-09-24T23:20:04Z",
        agent_name="Claude",
        operation="run_finished",
        status="success",
        tool_name="",
        tool_category="none",
        trigger_event_id="human-submit-v059",
    ))
    links = stitch_agent_workflows([human, started, finished])
    assert len(links) == 1
    assert links[0]["human_trigger_event_id"] == "human-submit-v059"
    assert links[0]["link_method"] == "explicit_trigger_event"
    assert links[0]["link_confidence"] == 1.0
    assert links[0]["authoritative"] is False


def test_temporal_stitch_is_conservative_and_far_run_stays_unlinked():
    human = {
        "event_id": "human-claude-temporal",
        "observed_at": "2026-09-24T23:30:00Z",
        "source": "browser_extension",
        "session_id": "human-temporal-session",
        "app": "Google Chrome",
        "window_title": "Claude",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "metadata": {
            "action": "click",
            "page": {"hostname": "claude.ai", "pathname": "/", "title": "Claude"},
            "target": {"role": "button", "label": "Send message"},
        },
    }
    near = agent_event_to_evidence(_agent_payload(
        "near-agent-v059",
        observed_at="2026-09-24T23:30:04Z",
        run_id="run-near-v059",
        trace_id="trace-near-v059",
        agent_name="Claude",
        operation="run_started",
        status="running",
        tool_name="",
        tool_category="none",
    ))
    far = agent_event_to_evidence(_agent_payload(
        "far-agent-v059",
        observed_at="2026-09-24T23:40:00Z",
        run_id="run-far-v059",
        trace_id="trace-far-v059",
        agent_name="Claude",
        operation="run_started",
        status="running",
        tool_name="",
        tool_category="none",
    ))
    links = {item["run_id"]: item for item in stitch_agent_workflows([human, near, far])}
    assert links["run-near-v059"]["link_method"] == "temporal_surface_match"
    assert links["run-near-v059"]["human_trigger_event_id"] == "human-claude-temporal"
    assert links["run-far-v059"]["link_method"] == "unlinked"
    assert links["run-far-v059"]["human_trigger_event_id"] == ""
