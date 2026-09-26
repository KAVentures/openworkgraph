from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_child(code: str, tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
            "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
            "WORKFLOW_OBSERVER_MODE": "observe",
            "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-26T20:00:00+00:00",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_agent_setup_payload_is_write_only_and_privacy_minimized(tmp_path):
    code = r'''
from starlette.requests import Request
from server.agent_auth import ensure_agent_ingest_token
import server.agent_dashboard_control_plane as control

scope={
    'type':'http','http_version':'1.1','method':'GET','scheme':'http',
    'path':'/v1/agent-setup','raw_path':b'/v1/agent-setup','query_string':b'',
    'headers':[], 'client':('127.0.0.1',12345), 'server':('127.0.0.1',8787),
}
payload=control.agent_setup_payload(Request(scope))
token=ensure_agent_ingest_token()
assert payload['credential_scope']=='agent_ingest_write_only'
assert payload['connection_status_basis']=='telemetry_observed_not_configuration_presence'
assert payload['read_access_granted_to_agent'] is False
assert payload['configuration_files_modified'] is False
assert all(value is False for value in payload['privacy'].values())

claude=payload['integrations']['claude_code']
blob=__import__('json').dumps(claude['settings'])
assert 'SessionStart' in blob and 'PostToolUse' in blob and 'SubagentStart' in blob
assert 'UserPromptSubmit' not in blob and 'PreToolUse' not in blob
assert '"async": true' in blob

codex=payload['integrations']['codex']['config']
assert '/agent-ingest/v1/codex-otel' in codex
assert f'Bearer {token}' in codex
assert 'log_user_prompt = false' in codex
assert 'log_agent_responses = false' in codex
assert 'log_guardian_assessments = false' in codex
assert 'log_exporter' not in codex

otel=payload['integrations']['otel']
assert '/agent-ingest/v1/otel' in otel['endpoint']
assert 'OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"' in otel['posix']
assert f'Bearer {token}' in otel['posix']
assert '/v1/traces' not in otel['endpoint']

custom=payload['integrations']['custom']
assert custom['endpoint'].endswith('/agent-ingest/v1/events')
assert custom['authorization']==f'Bearer {token}'
assert payload['integrations']['openai_agents']['python'].endswith('install_openai_agents_processor()')
'''
    _run_child(code, tmp_path)


def test_agent_setup_route_requires_human_read_auth_and_root_injects_control_plane(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
import server.agent_dashboard_control_plane
from server.secure_app import app

api_token=ensure_api_token()
with TestClient(app) as client:
    denied=client.get('/v1/agent-setup')
    assert denied.status_code==401, denied.text
    allowed=client.get('/v1/agent-setup',headers={'Authorization':f'Bearer {api_token}'})
    assert allowed.status_code==200, allowed.text
    assert allowed.headers.get('cache-control')=='no-store'
    assert allowed.json()['read_access_granted_to_agent'] is False
    root=client.get('/')
    assert root.status_code==200
    assert '<script src="/agent-control-plane.js"></script>' in root.text
'''
    _run_child(code, tmp_path)


def test_enterprise_runner_registers_control_plane_before_privacy_loader():
    source=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    control=source.index('import server.agent_dashboard_control_plane')
    privacy=source.index('import server.dashboard_privacy')
    assert control < privacy
