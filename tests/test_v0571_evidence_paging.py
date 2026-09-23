from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_child(code: str, tmp_path: Path, timeout: int = 60) -> None:
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
            "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
            "WORKFLOW_OBSERVER_MODE": "observe",
            "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_keyset_paging_search_filters_and_live_insert_stability(tmp_path):
    code = r'''
from datetime import datetime,timedelta,timezone
from server import db
from server.evidence_query import query_evidence

db.init_db()
base=datetime(2026,9,23,8,0,tzinfo=timezone.utc)
surfaces=['Gmail','Salesforce','Google Sheets']
rows=[]
for i in range(50000):
    # The newest 200 rows deliberately share a timestamp. Page boundaries must
    # therefore use the DB id tie-breaker, not timestamp alone.
    observed=(datetime(2026,9,24,0,0,tzinfo=timezone.utc) if i>=49800 else base+timedelta(seconds=i)).isoformat()
    surface=surfaces[i%len(surfaces)]
    action='NeedleUniqueAction' if i==20000 else ('Send' if i%17==0 else 'Open')
    locator=f'{surface.lower().replace(" ","-")}.example/work/{i}'
    text=f'{surface} | {action} | {locator}'
    rows.append((f'evt-{i}',observed,'run1','browser_extension',surface,action,f'Title {i}',locator,action,text,'{"event_type":"browser_action"}'))
with db.connect() as conn:
    conn.executemany('''INSERT INTO context_events(
        event_id,observed_at,session_id,source,surface,action,resource_title,
        resource_locator,target_label,context_text,metadata_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', rows)
    conn.execute('''INSERT INTO context_events(
        event_id,observed_at,session_id,source,surface,action,resource_title,
        resource_locator,target_label,context_text,metadata_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(
        'old-event','2026-09-22T08:00:00+00:00','old','desktop','Gmail','Open','Old','mail.google.com/old','Open','Gmail | Open | Old','{}'
    ))

first=query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100)
assert first['count']==100 and first['total']==50000
assert first['position_start']==1 and first['position_end']==100
assert first['has_more'] and first['next_cursor']
ids1=[x['event_id'] for x in first['items']]
assert len(ids1)==len(set(ids1))==100

second=query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,cursor=first['next_cursor'])
ids2=[x['event_id'] for x in second['items']]
assert len(ids2)==100 and not (set(ids1)&set(ids2))
assert second['position_start']==101 and second['position_end']==200

# Search is server-side, not limited to the first loaded page.
needle=query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,q='NeedleUniqueAction')
assert needle['total']==1 and [x['event_id'] for x in needle['items']]==['evt-20000']

salesforce=query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,surface='Salesforce')
assert salesforce['total']>100
assert all(x['surface']=='Salesforce' for x in salesforce['items'])
assert {x['surface'] for x in first['surfaces']}=={'Gmail','Salesforce','Google Sheets'}

try:
    query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,cursor='not-a-cursor')
    raise AssertionError('malformed cursor accepted')
except ValueError:
    pass
try:
    query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,cursor=first['next_cursor'],surface='Gmail')
    raise AssertionError('cursor was reusable under different filters')
except ValueError:
    pass

# A new event arriving above page one does not disturb the continuation encoded
# by an existing page-one cursor.
with db.connect() as conn:
    conn.execute('''INSERT INTO context_events(
        event_id,observed_at,session_id,source,surface,action,resource_title,
        resource_locator,target_label,context_text,metadata_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(
        'new-latest','2026-09-25T08:00:00+00:00','run1','desktop','Gmail','Send','New','mail.google.com/new','Send','Gmail | Send | New','{}'
    ))
second_after=query_evidence(scope='current',since='2026-09-23T08:00:00+00:00',limit=100,cursor=first['next_cursor'])
assert [x['event_id'] for x in second_after['items']]==ids2
assert second_after['total']==50001

all_scope=query_evidence(scope='all',since=None,limit=10,q='Old')
assert any(x['event_id']=='old-event' for x in all_scope['items'])
'''
    _run_child(code, tmp_path, timeout=75)


def test_paged_evidence_route_requires_auth_and_defaults_to_100(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.db import init_db
from server.local_auth import ensure_api_token
import server.enterprise_app
import server.evidence_delete_routes
import server.v0571_polish
import server.evidence_paging
from server.secure_app import app
init_db()
with TestClient(app) as client:
    unauth=client.get('/v1/evidence')
    assert unauth.status_code==401, unauth.text
    headers={'Authorization':f'Bearer {ensure_api_token()}'}
    response=client.get('/v1/evidence',headers=headers)
    assert response.status_code==200, response.text
    payload=response.json()
    assert payload['limit']==100 and payload['source_layer']=='privacy_hardened_context_events'
    bad=client.get('/v1/evidence?cursor=bad',headers=headers)
    assert bad.status_code==400, bad.text
'''
    _run_child(code, tmp_path)


def test_enterprise_runner_registers_paging_after_correctness_layer():
    runner=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    assert runner.index('import server.v0571_polish') < runner.index('import server.evidence_paging')
