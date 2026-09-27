from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, tmp_path: Path, timeout: int = 120) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-01-01T00:00:00+00:00",
    })
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_locator_name_slugs_and_trace_provenance_are_redacted(tmp_path):
    _run(r'''
from server import ai_context

value={
    "window_title":"Patient Anna Svensson - Journal",
    "page_path":"/patients/anna-svensson/visits",
    "url":"https://journal.example/patients/anna-svensson/visits?assignee=Anna+Svensson",
}
safe=ai_context.redact_contextually(value)
blob=str(safe)
assert "Anna" not in blob and "Svensson" not in blob, safe
assert "PERSON_" in safe["page_path"], safe
assert "PERSON_" in safe["url"], safe

payload={"data_layer":"privacy_hardened_raw_rich_evidence","rows":[value]}
redacted,level=ai_context.redact_for_ai(payload)
assert level=="redacted"
assert redacted["evidence_origin"]=="canonical_local_event_store"
assert redacted["data_layer"]=="privacy_hardened_contextually_redacted_rich_evidence"
assert redacted["text_representation"]=="contextually_redacted"
assert redacted["stored_text_modified_for_ai"] is True
assert "Anna" not in str(redacted) and "Svensson" not in str(redacted)
''', tmp_path)


def test_enrolled_gateway_policy_unknown_fails_closed(tmp_path):
    _run(r'''
import json, os
from pathlib import Path
from connector.state import SyncState
from server import ai_context

cfg=Path(os.environ["WORKFLOW_OBSERVER_CONFIG"])
cfg.write_text(json.dumps({"ai_context":{"detail":"full"},"gateway":{"enabled":True}}),encoding="utf-8")
state=ai_context.effective_detail()
assert state["detail_level"]=="redacted", state
assert state["lock_source"]=="gateway_policy_unknown", state

sync=SyncState(Path(os.environ["WORKFLOW_OBSERVER_DATA"])/"gateway_sync_state.db")
sync.set_bool("org_force_redacted_ai_context",False)
state=ai_context.effective_detail()
assert state["detail_level"]=="full", state
assert state["lock_source"] is None, state

sync.set_bool("org_force_redacted_ai_context",True)
state=ai_context.effective_detail()
assert state["detail_level"]=="redacted", state
assert state["lock_source"]=="gateway_policy", state
''', tmp_path)


def test_redacted_non_json_error_body_never_passes_through(tmp_path):
    _run(r'''
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import server.enterprise_app, server.evidence_delete_routes, server.v0571_polish, server.evidence_paging
import server.work_profile_routes, server.browser_signal_routes, server.agent_dashboard_control_plane
import server.dashboard_privacy
from server.secure_app import app
from server.local_auth import ensure_api_token

@app.get('/v1/_redaction_plaintext_error_test')
def _plain_error():
    return PlainTextResponse('Could not process Anna Svensson',status_code=500)

H={'Authorization':f'Bearer {ensure_api_token()}','X-OpenWorkGraph-Context':'ai'}
with TestClient(app) as c:
    r=c.get('/v1/_redaction_plaintext_error_test',headers=H)
    assert r.status_code==500, r.text
    assert r.headers['X-OpenWorkGraph-Detail-Level']=='redacted'
    assert 'Anna' not in r.text and 'Svensson' not in r.text, r.text
    assert 'withheld' in r.text.lower(), r.text
''', tmp_path)


def test_ai_visible_person_token_can_retrieve_same_canonical_history(tmp_path):
    _run(r'''
import re
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import server.enterprise_app, server.evidence_delete_routes, server.v0571_polish, server.evidence_paging
import server.work_profile_routes, server.browser_signal_routes, server.agent_dashboard_control_plane
import server.dashboard_privacy
from server.secure_app import app
from server.local_auth import ensure_api_token

H={'Authorization':f'Bearer {ensure_api_token()}'}
AI={**H,'X-OpenWorkGraph-Context':'ai'}
event={
    'event_id':'token-search-1',
    'observed_at':datetime.now(timezone.utc).isoformat(),
    'device_id':'d','session_id':'s','app':'Gmail',
    'window_title':'Re: Contract for Anna Svensson - Gmail',
    'event_type':'focus_span','duration_seconds':3,
    'metadata':{'page':{'pathname':'/people/anna-svensson'}},
}
with TestClient(app) as c:
    assert c.post('/v1/events',headers=H,json={'events':[event]}).status_code==200
    first=c.get('/v1/workflow-trace?scope=all',headers=AI)
    assert first.status_code==200, first.text
    first_json=first.json()
    token=re.search(r'PERSON_[0-9A-F]{6}',str(first_json)).group(0)
    assert 'Anna' not in first.text and 'Svensson' not in first.text

    found=c.get('/v1/workflow-trace',headers=AI,params={'scope':'all','query':token})
    assert found.status_code==200, found.text
    body=found.json()
    assert body['query_mode']=='redacted_token', body
    assert body['total']>=1 and any(row.get('event_id')=='token-search-1' for row in body['rows']), body
    assert token in found.text and 'Anna' not in found.text and 'Svensson' not in found.text

    # The pseudonym is not written into canonical storage; token lookup exists
    # only at the redacted AI boundary.
    raw=c.get('/v1/workflow-trace',headers=H,params={'scope':'all','query':token})
    assert raw.status_code==200
    assert raw.json()['total']==0
''', tmp_path)


def test_compact_repeated_workflow_scan_is_bounded_without_affecting_trace(tmp_path):
    _run(r'''
from mcp_server import compact_hardening as h

class FakeRuntime:
    def __init__(self): self.calls=[]
    def secure_get(self,path,params=None):
        self.calls.append((path,dict(params or {})))
        return {'ok':True}

fake=FakeRuntime()
proxy=h._CompactRuntimeProxy(fake)
tool_token=h._CURRENT_TOOL.set('find_repeated_workflows')
try:
    proxy.secure_get('/v1/tasks',{'limit':25000})
    proxy.secure_get('/v1/summary',{'limit':25000})
    proxy.secure_get('/v1/procedural-memory',{'limit':25000})
    limits={path:params['limit'] for path,params in fake.calls}
    assert limits['/v1/tasks']==5000, limits
    assert limits['/v1/summary']==5000, limits
    assert limits['/v1/procedural-memory']==1000, limits
finally:
    h._CURRENT_TOOL.reset(tool_token)

fake.calls.clear()
proxy.secure_get('/v1/workflow-trace',{'limit':500})
assert fake.calls[-1][1]['limit']==500
''', tmp_path)
