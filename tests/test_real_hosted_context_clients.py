"""Harness regression checks. These are NOT model evaluation results."""
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from evals.hosted_context.instrumentation import _ids_from
from evals.hosted_context.run_clients import classify, command, transport_calls
from evals.hosted_context.score import load_cases

ROOT = Path(__file__).resolve().parents[1]


def test_claude_success_subtype_does_not_turn_auth_failure_into_result():
    assert classify("claude", 0, [{"type": "result", "subtype": "success",
        "is_error": True, "result": "Not logged in · Please run /login"}], "") == "blocked_authentication"
    assert classify("codex", 0, [{"type": "turn.failed"}], "") != "completed"
    assert classify("codex", 0, [{"type": "turn.completed"}], "") == "completed"


def test_prompt_unchanged_and_no_builtin_data_access_in_both_conditions():
    prompt = load_cases()[0]["prompt"]
    for client in ("codex", "claude"):
        enabled = command(client, client, "version", prompt, {"command": "proxy", "args": []})
        disabled = command(client, client, "version", prompt, None)
        assert enabled[-1] == disabled[-1] == prompt
        assert "--resume" not in enabled and "--continue" not in enabled
        if client == "codex":
            assert "features.shell_tool=false" in enabled
            assert "project_doc_max_bytes=0" in enabled
        else:
            assert enabled[enabled.index("--tools") + 1] == ""
            assert "--strict-mcp-config" in enabled


def test_interrupted_transport_request_is_counted_without_inventing_response(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(json.dumps({"phase": "request", "index": 0, "name": "search_work",
                              "arguments": {"query": "synthetic phrase"}}) + "\n")
    result = transport_calls(path)
    assert len(result) == 1
    assert result[0]["event_ids"] == []
    assert result[0]["arguments"]["query"] == "<present>"
    assert result[0]["transport_response_observed"] is False


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    temp = tmp_path_factory.mktemp("real-eval-gateway")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    state = temp / "gateway"
    with (temp / "server.log").open("w") as log:
        p = subprocess.Popen([sys.executable, "-m", "evals.hosted_context.disposable",
            "--state", str(state), "--port", str(port)], cwd=ROOT, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while True:
                if p.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("disposable Gateway failed")
                if (state / "connection.json").exists():
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        pass
                time.sleep(0.05)
            yield state, temp
        finally:
            p.terminate()
            p.wait(timeout=10)


def test_real_oauth_rejects_missing_bearer_and_does_not_merge_empty_accounts(gateway):
    state, _ = gateway
    connection = json.loads((state / "connection.json").read_text())
    with httpx2.Client(trust_env=False) as client:
        response = client.post(connection["url"], headers={
            "Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401
    counts = json.loads((state / "seed.json").read_text())["inserted"]
    assert counts == {"populated": 288, "empty": 0, "local-only": 0}


def test_proxy_preserves_catalog_and_pages_actual_canonical_rows(gateway):
    state, temp = gateway

    async def check():
        connection = json.loads((state / "connection.json").read_text())
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " +
                connection["tokens"]["populated"]}, trust_env=False) as http:
            async with streamable_http_client(connection["url"], http_client=http) as (r, w):
                async with ClientSession(r, w) as direct:
                    init = await direct.initialize()
                    catalog = await direct.list_tools()
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
            "-m", "evals.hosted_context.proxy", "--connection", str(state / "connection.json"),
            "--person", "populated", "--calls", str(temp / "calls.jsonl"),
            "--catalog", str(temp / "catalog.json")])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                assert (await session.initialize()).instructions == init.instructions
                forwarded = await session.list_tools()
                assert forwarded.model_dump() == catalog.model_dump()
                found = set()
                cursor = None
                pages = 0
                while True:
                    result = await session.call_tool("get_workflow_trace", {
                        "limit": 25, **({"cursor": cursor} if cursor else {})})
                    assert not result.is_error
                    found.update(_ids_from(result.model_dump(mode="json")))
                    payload = result.structured_content
                    pages += 1
                    if not payload["has_more"]:
                        break
                    cursor = payload["next_cursor"]
                assert len(found) == 288 and pages == 12
                targets = {event for case in load_cases() for event in case["target_event_ids"]}
                assert targets.issubset(found)
        recorded = transport_calls(temp / "calls.jsonl")
        assert len(recorded) == 12
        assert set.union(*(set(r["event_ids"]) for r in recorded)) == found
        assert all(r["transport_response_observed"] for r in recorded)
        # The recorder contains IDs and structural summaries, no raw payloads.
        assert "window_title" not in (temp / "calls.jsonl").read_text()

    asyncio.run(check())


def test_corrected_fixtures_exercise_advertised_failure_and_retrieval_conditions(gateway):
    state, _ = gateway

    async def check():
        connection = json.loads((state / "connection.json").read_text())
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " +
                connection["tokens"]["populated"]}, trust_env=False) as http:
            async with streamable_http_client(connection["url"], http_client=http) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    miss = await s.call_tool("search_work", {"query": "special request"})
                    assert not miss.is_error and miss.structured_content["returned"] == 0
                    fallback = await s.call_tool("get_workflow_trace", {
                        "since": "2026-10-09T10:04:00Z", "until": "2026-10-09T10:04:01Z"})
                    assert _ids_from(fallback.model_dump()) == {"evt-unmatched-003"}
                    pricing = await s.call_tool("search_work", {"query": "Acme pricing"})
                    assert _ids_from(pricing.model_dump()) == {"evt-price-001"}
                    day = await s.call_tool("get_workflow_trace", {
                        "since": "2026-10-06T00:00:00Z", "until": "2026-10-07T00:00:00Z"})
                    assert day.structured_content["returned"] == 25
                    assert day.structured_content["has_more"] is True
                    tail = await s.call_tool("get_workflow_trace", {
                        "since": "2026-10-06T00:00:00Z", "until": "2026-10-07T00:00:00Z",
                        "cursor": day.structured_content["next_cursor"]})
                    assert tail.structured_content["returned"] == 19
                    assert tail.structured_content["has_more"] is False
                    assert "evt-tuesday-044" in _ids_from(tail.model_dump())
                    agent = await s.call_tool("get_agent_runs", {})
                    assert agent.structured_content["returned"] == 1
                    run = agent.structured_content["executions"][0]
                    assert run["outcome_status"] == "unknown"
                    assert run["outcome_basis"] == "no_terminal_outcome_observed"

    asyncio.run(check())


@pytest.mark.parametrize("person", ["empty", "local-only"])
def test_empty_account_proxy_cannot_return_populated_evidence(gateway, person):
    state, temp = gateway

    async def check():
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
            "-m", "evals.hosted_context.proxy", "--connection", str(state / "connection.json"),
            "--person", person, "--calls", str(temp / f"{person}.jsonl"),
            "--catalog", str(temp / f"{person}.json")])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool("get_current_work_context", {})
                assert not result.is_error
                assert _ids_from(result.model_dump(mode="json")) == set()
                assert result.structured_content["onboarding"]["status"] == "no_synced_evidence"

    asyncio.run(check())
