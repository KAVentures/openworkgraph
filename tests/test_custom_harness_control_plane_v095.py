from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_child(code: str, tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-27T20:00:00+00:00",
    })
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=45,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_custom_harness_setup_has_separate_write_and_mcp_permissions(tmp_path):
    code = r'''
from starlette.requests import Request
from server.agent_auth import ensure_agent_ingest_token
import server.custom_harness_control_plane as control

scope={
    'type':'http','http_version':'1.1','method':'GET','scheme':'http',
    'path':'/v1/custom-harness-setup','raw_path':b'/v1/custom-harness-setup','query_string':b'',
    'headers':[], 'client':('127.0.0.1',12345), 'server':('127.0.0.1',8787),
}
payload=control.setup_payload(Request(scope))
token=ensure_agent_ingest_token()
write=payload['write']
read=payload['read']
assert write['credential_scope']=='agent_ingest_write_only'
assert write['raw_http']['authorization']==f'Bearer {token}'
assert write['raw_http']['endpoint'].endswith('/agent-ingest/v1/events')
assert write['otel']['endpoint'].endswith('/agent-ingest/v1/otel')
assert 'openworkgraph-agent' in write['python']['install']
assert 'AgentObserver' in write['python']['example']
assert 'openworkgraph-agent.mjs' in write['typescript']['download']
assert 'AgentObserver' in write['typescript']['example']
assert read['method']=='MCP stdio'
assert read['master_ai_access_required'] is True
assert read['saved_history_lease_required_for_historical_reads'] is True
entry=read['config']['mcpServers']['openworkgraph']
assert '--client' in entry['args'] and 'custom-harness' in entry['args']
assert token not in str(read)
assert all(value is False for value in payload['privacy'].values())
'''
    _run_child(code, tmp_path)


def test_custom_harness_setup_route_is_human_auth_protected_and_injected(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
import server.agent_dashboard_control_plane
import server.custom_harness_control_plane
from server.secure_app import app

api_token=ensure_api_token()
with TestClient(app) as client:
    denied=client.get('/v1/custom-harness-setup')
    assert denied.status_code==401, denied.text
    allowed=client.get('/v1/custom-harness-setup',headers={'Authorization':f'Bearer {api_token}'})
    assert allowed.status_code==200, allowed.text
    assert allowed.headers.get('cache-control')=='no-store'
    root=client.get('/')
    assert root.status_code==200
    assert '<script src="/custom-harness-setup.js"></script>' in root.text
'''
    _run_child(code, tmp_path)


def test_enterprise_runner_registers_custom_harness_control_plane():
    source=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    assert 'import server.custom_harness_control_plane' in source
    assert source.index('import server.agent_dashboard_control_plane') < source.index('import server.custom_harness_control_plane')
    assert source.index('import server.custom_harness_control_plane') < source.index('import server.dashboard_privacy')
