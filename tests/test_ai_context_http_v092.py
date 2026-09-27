from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-01-01T00:00:00+00:00",
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


_SETUP = r'''
import json, time
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import server.enterprise_app, server.evidence_delete_routes, server.v0571_polish, server.evidence_paging
import server.work_profile_routes, server.browser_signal_routes, server.agent_dashboard_control_plane
import server.dashboard_privacy
from server.secure_app import app
from server.local_auth import ensure_api_token
H={'Authorization':f'Bearer {ensure_api_token()}'}
AI={**H,'X-OpenWorkGraph-Context':'ai'}
now=datetime.now(timezone.utc)
people=['Anna Svensson','Erik Lindqvist','Sara Ek','Johan Berg','Maria Johansson','Priya Patel']
templates=['Re: Contract for {p} - Gmail','Faktura {n} från {p} - Outlook','Chat with {p} | Microsoft Teams',
           '{p} - Contact - Salesforce','Q3 pipeline tracker - Google Sheets','Budget 2027 - Google Sheets']
def events(n):
    out=[]
    for i in range(n):
        t=templates[i%len(templates)].format(p=people[i%len(people)], n=4000+i)
        out.append({"event_id":f"perf-{i}","observed_at":(now-timedelta(seconds=n-i)).isoformat(),"device_id":"d",
                    "session_id":"s","app":"Microsoft Outlook","window_title":t,"event_type":"focus_span",
                    "duration_seconds":3.0,"metadata":{"target":{"role":"button","label":"Open email from "+people[i%len(people)]}}})
    return out
'''


def test_default_workflow_trace_is_not_noticeably_slower_with_ai_redaction(tmp_path):
    out = _run(_SETUP + r'''
with TestClient(app) as c:
    evs=events(600)
    for i in range(0,len(evs),200):
        assert c.post('/v1/events',headers=H,json={"events":evs[i:i+200]}).status_code==200
    url='/v1/workflow-trace?scope=all'
    for h in (H,AI): c.get(url,headers=h)  # warm-up
    def timed(h):
        best=9e9
        for _ in range(3):
            t=time.perf_counter(); r=c.get(url,headers=h); best=min(best,time.perf_counter()-t)
            assert r.status_code==200
        return best, r
    base,_=timed(H)
    ai,r=timed(AI)
    assert r.headers['X-OpenWorkGraph-Detail-Level']=='redacted'
    body=json.dumps(r.json())
    assert 'Anna' not in body and 'Svensson' not in body and 'PERSON_' in body
    print(f"workflow-trace baseline={base*1000:.1f}ms ai_redacted={ai*1000:.1f}ms rows={len(r.json().get('rows',[]))}")
    # Generous bound: the AI pass may add work, but not change the order of magnitude.
    assert ai < base*3 + 0.5, (base, ai)
''', tmp_path)
    print(out.strip())


def test_export_can_redact_names_with_the_same_redactor(tmp_path):
    _run(_SETUP + r'''
with TestClient(app) as c:
    # A title the established display redactor leaves alone (no mail cue).
    title={"event_id":"exp-1","observed_at":now.isoformat(),"device_id":"d","session_id":"s",
           "app":"Microsoft Teams","window_title":"Microsoft Teams - Chat with Sara Ek",
           "event_type":"focus_span","duration_seconds":3.0,"metadata":{}}
    assert c.post('/v1/events',headers=H,json={"events":[title]}).status_code==200
    for redact in (False, True):
        ticket=c.post('/v1/export-ticket',headers=H,json={"format":"json","scope":"all","include_raw":True,"redact_names":redact}).json()
        assert ('redact_names=true' in ticket['url'])==redact
        body=c.get(ticket['url']).text
        if redact:
            assert 'Sara Ek' not in body and 'Chat with PERSON_' in body
        else:
            assert 'Chat with Sara Ek' in body  # default export behaviour unchanged
    # A ticket issued for a redacted export cannot be replayed as unredacted.
    ticket=c.post('/v1/export-ticket',headers=H,json={"format":"json","scope":"all","include_raw":True,"redact_names":True}).json()
    tampered=ticket['url'].replace('redact_names=true','redact_names=false')
    assert c.get(tampered).status_code==401
''', tmp_path)


def test_ai_request_to_non_json_route_fails_closed(tmp_path):
    _run(_SETUP + r'''
with TestClient(app) as c:
    c.post('/v1/events',headers=H,json={"events":events(6)})
    r=c.get('/v1/export/json?scope=all&include_raw=true',headers=AI)
    # Export is JSON here; the whole body goes through the AI redactor.
    assert r.status_code==200 and 'Anna Svensson' not in r.text
    r=c.get('/v1/export/xlsx?scope=all&include_raw=true',headers=AI)
    assert r.status_code==406
''', tmp_path)
