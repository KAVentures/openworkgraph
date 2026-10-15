"""Collector ingest validates timestamps, duration and bounded authenticated payload."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_local_collector_rejects_malformed_event_and_oversized_body(tmp_path):
    env={**os.environ,
        "WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
        "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
        "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json"),
        "PYTHONPATH":str(ROOT)}
    code="""
import copy,json,math
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server.local_auth import ensure_api_token
from server.db import connect

token=ensure_api_token()
headers={'Authorization':'Bearer '+token}
baseline={'event_id':'valid','observed_at':datetime.now(timezone.utc).isoformat(),
          'device_id':'device','session_id':'session','app':'Editor',
          'event_type':'focus_span','duration_seconds':60,'metadata':{}}
with TestClient(app) as client:
    valid=client.post('/v1/events',headers=headers,json={'events':[baseline]})
    assert valid.status_code==200,valid.text
    cases=[
        {'observed_at':'not-a-date'}, {'observed_at':'2031-01-01T10:00:00+00:00'},
        {'duration_seconds':-1},{'duration_seconds':1e9},
        {'duration_seconds':float('nan')},
        {'metadata':{'sample':float('nan')}},
        {'metadata':{'outer':[]}},
    ]
    nested={}
    for _ in range(24): nested={'deeper':nested}
    cases[-1]['metadata']=nested
    for n,overrides in enumerate(cases):
        event={**baseline,**overrides,'event_id':f'invalid-{n}'}
        response=client.post('/v1/events',
            data=json.dumps({'events':[event]},allow_nan=True),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        assert response.status_code==422,(n,response.status_code,response.text[:400])
    huge=client.post('/v1/events',data=b' '* (5*1024*1024+128),
                     headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    assert huge.status_code==413,huge.status_code
    with connect() as db:
        ids=[r[0] for r in db.execute('SELECT event_id FROM events')]
    assert ids==['valid'],ids
"""
    result=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,
        capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
