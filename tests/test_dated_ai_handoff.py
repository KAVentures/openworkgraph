"""Cross-layer regressions: selected evidence, privacy, and scoped download capabilities."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(code, tmp_path):
    env = dict(os.environ, WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
               WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
               WORKFLOW_OBSERVER_RUN_STARTED_AT='2026-10-17T00:00:00+00:00')
    p = subprocess.run([sys.executable, '-c', SETUP+'\n'+code], cwd=ROOT,
                       env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout+'\n'+p.stderr


SETUP = r'''
import io, json, zipfile
from fastapi.testclient import TestClient
import server.enterprise_app, server.history_routes, server.v0571_polish
import server.evidence_paging, server.work_profile_routes, server.basic_mode_routes
from server.secure_app import app
from server.db import insert_events, connect
from server.local_auth import ensure_api_token, create_dashboard_session
from server.context_exporter import build_export_payload, csv_zip_bytes, xlsx_bytes
H={'Authorization':'Bearer '+ensure_api_token()}
D={'Authorization':'OWG-Session '+create_dashboard_session()}
A='2026-10-16T09:00:00+00:00'
B='2026-10-16T10:00:00+00:00'
def event(eid, at, title, duration=30, **extra):
    return dict(event_id=eid, observed_at=at,device_id='synthetic',session_id='s',
                app='Microsoft Excel',window_title=title,event_type='focus_span',
                duration_seconds=duration,metadata={'activity':{'engaged_seconds':duration,'click_count':4}},**extra)
'''


def test_dated_package_uses_same_window_for_all_layers_and_formats(tmp_path):
    run(r'''
with TestClient(app) as c:
    insert_events([
      event('before','2026-10-16T08:59:59Z','OUTSIDE_BEFORE_SENTINEL'),
      event('inside','2026-10-16T10:00:00+01:00','Invoice Acme Q4',7200),
      event('inside2','2026-10-16T09:30:00Z','Invoice Acme Q4'),
      event('after',B,'OUTSIDE_AFTER_SENTINEL'),
    ])
    current=c.get('/v1/export/json?scope=current&include_raw=true',headers=H).json()
    assert current['raw_local_evidence']==[]
    args=dict(scope='range',since=A,until=B,include_raw=True,redact_names=True)
    r=c.get('/v1/export/json',params=args,headers=H)
    assert r.status_code==200,r.text
    data=r.json()
    assert {e['event_id'] for e in data['raw_local_evidence']}=={'inside','inside2'}
    assert 'OUTSIDE_BEFORE_SENTINEL' not in r.text and 'OUTSIDE_AFTER_SENTINEL' not in r.text
    assert data['raw_local_evidence'][0]['duration_seconds']==7200  # canonical record unchanged
    assert data['overview']['operational']['total_active_seconds']==3630
    assert data['work_profile']['fragmentation']['foreground_seconds']==3630
    assert data['capture_manifest']['window']['until']==B
    assert data['capture_manifest']['window']['canonical_rows']==2
    assert data['capture_manifest']['window']['analysis_spans_clipped_to_end']
    assert 'no repeated' not in data['starter_prompt_markdown'].lower() # no repetition gate
    assert 'even when repeated-work candidates are empty' in data['starter_prompt_markdown']
    for fmt in ('csvzip','xlsx'):
        r=c.get('/v1/export/'+fmt,params=args,headers=H)
        assert r.status_code==200,r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            content=b'\n'.join(z.read(n) for n in z.namelist())
            assert b'OUTSIDE_AFTER_SENTINEL' not in content and b'OUTSIDE_BEFORE_SENTINEL' not in content
            if fmt=='csvzip':
                assert b'event_id' in z.read('raw_local_evidence.csv')
                assert 'START_HERE.md' in z.namelist()
                manifest=json.loads(z.read('CAPTURE_MANIFEST.json'))
                assert manifest['window']['canonical_rows']==2
    with connect() as conn:
        assert conn.execute("SELECT duration_seconds FROM events WHERE event_id='inside'").fetchone()[0]==7200
''', tmp_path)


def test_handoff_chunks_and_legacy_private_export_protection(tmp_path):
    run(r'''
with TestClient(app) as c:
    insert_events([event('ordinary', A, 'Invoice template'),
                   event('legacy-private', '2026-10-16T09:30:00Z', 'Placeholder'),
                   event('health', '2026-10-16T09:45:00Z', 'Capture gap')])
    with connect() as conn:
        for table in ('events','normalized_events'):
            conn.execute('UPDATE '+table+' SET app=?, window_title=?, metadata_json=? WHERE event_id=?',
                         ('Google Chrome','SECRET_LEGACY - Google Chrome (Inkognito)',
                          json.dumps({'target':{'label':'SECRET_TARGET'},'page':{'hostname':'secret.invalid'}}),
                          'legacy-private'))
        conn.execute("UPDATE events SET event_type='capture_gap' WHERE event_id='health'")
    args={'scope':'range','since':A,'until':B}
    preview=c.get('/v1/analysis-preview',headers=D,params=args)
    assert preview.status_code==200,preview.text
    data=preview.json()
    assert data['work_evidence']['health_event_rows']==1
    assert data['analysis_plan']['page_size']==200
    chunks=data['analysis_plan']['date_chunks']
    assert len(chunks)==1 and chunks[0]['event_rows']==3
    from datetime import datetime
    assert datetime.fromisoformat(chunks[0]['since'])==datetime.fromisoformat(A)
    assert datetime.fromisoformat(chunks[0]['until'])==datetime.fromisoformat(B)
    assert not data['analysis_plan']['complete_history_claimed']
    assert 'SECRET_' not in preview.text
    r=c.get('/v1/export/json',headers=H,params={**args,'include_raw':True,'redact_names':False})
    assert r.status_code==200,r.text
    assert 'SECRET_LEGACY' not in r.text and 'SECRET_TARGET' not in r.text and 'secret.invalid' not in r.text
    rows=r.json()['raw_local_evidence']
    hidden=next(row for row in rows if row['event_id']=='legacy-private')
    assert hidden['app']=='Excluded'
    assert next(row for row in rows if row['event_id']=='ordinary')['window_title']=='Invoice template'
    with connect() as conn:
        assert 'SECRET_LEGACY' in conn.execute("SELECT window_title FROM events WHERE event_id='legacy-private'").fetchone()[0]
''', tmp_path)


def test_download_ticket_cannot_widen_dates_and_remains_one_use(tmp_path):
    run(r'''
with TestClient(app) as c:
    insert_events([event('inside',A,'Invoice Acme')])
    args=dict(format='json',scope='range',since=A,until=B,include_raw=True,redact_names=True)
    for replacement in ('scope=all','until=2026-10-17T00%3A00%3A00%2B00%3A00','redact_names=false','since=猫'):
        r=c.post('/v1/export-ticket',headers=D,json=args)
        assert r.status_code==200,r.text
        url=r.json()['url']
        from urllib.parse import urlsplit,parse_qs,urlencode
        query=parse_qs(urlsplit(url).query)
        key,val=replacement.split('=',1)
        from urllib.parse import unquote
        query[key]=[unquote(val)]
        bad='/v1/export/json?'+urlencode({k:v[0] for k,v in query.items()})
        assert c.get(bad).status_code==401
        assert c.get(url).status_code==401
    good=c.post('/v1/export-ticket',headers=D,json=args).json()['url']
    assert c.get(good).status_code==200
    assert c.get(good).status_code==401
    for payload in [dict(args,until=A),dict(args,since='bad'),dict(args,since='2026-10-16T09:00:00')]:
        assert c.post('/v1/export-ticket',headers=D,json=payload).status_code==400
''', tmp_path)


def test_preview_does_not_grant_history_or_depend_on_repeated_work(tmp_path):
    run(r'''
from shared.history_policy import active_ai_history_access
with TestClient(app) as c:
    insert_events([event('inside',A,'natalie bergstrom cardiology - Google Search')])
    prior=active_ai_history_access()
    args=dict(scope='range',since=A,until=B)
    assert c.get('/v1/analysis-preview',params=args).status_code==401
    assert c.get('/v1/analysis-preview',headers=H,params=args).status_code==403
    r=c.get('/v1/analysis-preview',headers=D,params=args)
    assert r.status_code==200,r.text
    d=r.json()
    assert d['canonical_rows']==1 and d['retained_rows']==1
    assert 'natalie' not in r.text and 'bergstrom' not in r.text
    assert 'cardiology' in r.text and 'PERSON_' in r.text
    assert d['work_text']['included_in_activity_package'] is False
    assert active_ai_history_access()==prior
    empty=c.get('/v1/analysis-preview',headers=D,params={'scope':'current'}).json()
    assert empty['canonical_rows']==0 and empty['retained_rows']==1
    assert 'without a search filter' in d['request_text']
''', tmp_path)


def test_activity_paging_respects_dates_offsets_and_cursor_binding(tmp_path):
    run(r'''
with TestClient(app) as c:
    insert_events([event('before','2026-10-16T08:59:59Z','OUTSIDE_BEFORE_SENTINEL'),
                   event('one','2026-10-16T10:00:00+01:00','Invoice Acme'),
                   event('two','2026-10-16T09:30:00Z','Receipt Acme'),
                   event('after',B,'OUTSIDE_AFTER_SENTINEL')])
    args=dict(scope='range',since=A,until=B,limit=1)
    r=c.get('/v1/evidence',headers=H,params=args)
    assert r.status_code==200,r.text
    first=r.json();assert first['total']==2,first
    assert first['items'][0]['event_id']=='two'
    cursor=first['next_cursor'];assert cursor
    next=c.get('/v1/evidence',headers=H,params=dict(args,cursor=cursor)).json()
    assert next['items'][0]['event_id']=='one'
    assert c.get('/v1/evidence',headers=H,params=dict(args,cursor=cursor,until='2026-10-17T00:00:00Z')).status_code==400
    # Preserve the existing recorder-state boundary for legacy current-run calls,
    # even when it differs from the launcher's process environment.
    from unittest.mock import patch
    with patch('server.enterprise_app._current_run_start', return_value=A):
        current=c.get('/v1/evidence',headers=H,params={'scope':'current'}).json()
    assert current['since']==A and current['total']==3, current
    assert {x['event_id'] for x in current['items']}=={'one','two','after'}
''', tmp_path)


def test_lowercase_search_names_are_hidden_without_losing_context(tmp_path):
    run(r'''
# Do not depend on HTTP import-order to enable capture-time redaction.
from shared.core.browser_title_privacy import protect_text
from server.ai_context import redact_contextually, contextual_text_redactor
for title in ('natalie bergstrom cardiology - Google Search',
              'zubeyde okonkwo cardiology - Google Search',
              'zübeyde okonkwo cardiology - Google Search',
              'anna svensson acme - Sök på Google - Google Chrome'):
    for transform in (protect_text, lambda x:redact_contextually({'window_title':x})['window_title']):
        safe=transform(title)
        assert 'PERSON_' in safe,(title,safe)
        assert 'natalie' not in safe and 'bergstrom' not in safe and 'anna' not in safe and 'svensson' not in safe
        assert 'zubeyde' not in safe and 'zübeyde' not in safe and 'okonkwo' not in safe
        assert 'cardiology' in safe or 'acme' in safe
        assert transform(safe)==safe
for title in ('Q4 pipeline - Atlas Logistics - Google Sheets',
              'Fakturamall Sollefteå v42 Acme AB - Google Sheets',
              'mark as read - Google Search','Stockholm weather - Google Search','De Beers','Van rental',
              'john deere price - Google Search','John Deere price - Google Search'):
    assert protect_text(title)==title,(title,protect_text(title))
r=contextual_text_redactor({'never_redact':['anna svensson'],'always_redact':[]})
assert r('anna svensson cardiology - Google Search')=='anna svensson cardiology - Google Search'
r=contextual_text_redactor({'never_redact':[], 'always_redact':['John Deere']})
assert 'PERSON_' in r('John Deere price - Google Search')
''', tmp_path)


def test_upgrade_reprotects_history_after_the_previous_title_migration(tmp_path):
    run(r'''
from server import db
db.init_db()
insert_events([event('legacy', A, 'Invoice Acme Q4')])
with connect() as c:
    # Reproduce a retained title missed by the already-completed older pass.
    c.execute("UPDATE events SET window_title=? WHERE event_id='legacy'",
              ('natalie bergstrom cardiology - Google Search',))
    c.execute("INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)",
              ('titles_sensitive_details_tokenized_v0108',))
assert not db.title_protection_complete()
assert db.protect_existing_titles(batch_size=1) == 1
assert db.title_protection_complete()
with connect() as c:
    for table, column in [('events','window_title'), ('normalized_events','window_title'),
                          ('context_events','resource_title')]:
        title=c.execute(f"SELECT {column} FROM {table} WHERE event_id='legacy'").fetchone()[0]
        assert 'natalie' not in title and 'bergstrom' not in title, (table,title)
        if table != 'normalized_events':  # normalized titles intentionally use coarse app labels
            assert 'PERSON_' in title and 'cardiology' in title, (table,title)
assert db.protect_existing_titles(batch_size=1) == 0
''', tmp_path)


def test_new_given_name_protection_applies_to_already_migrated_history(tmp_path):
    run(r'''
from server import db
db.init_db()
insert_events([event('retained', A, 'Invoice Acme Q4')])
with connect() as c:
    c.execute("UPDATE events SET window_title=? WHERE event_id='retained'",
              ('zubeyde okonkwo cardiology - Google Search',))
    c.execute("INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)",
              ('titles_sensitive_details_tokenized_v0108_search_names_v2',))
assert not db.title_protection_complete()
assert db.protect_existing_titles(batch_size=1) == 1
assert db.title_protection_complete()
with connect() as c:
    title=c.execute("SELECT window_title FROM events WHERE event_id='retained'").fetchone()[0]
assert 'zubeyde' not in title and 'okonkwo' not in title
assert 'PERSON_' in title and 'cardiology' in title
assert db.protect_existing_titles() == 0
''', tmp_path)


def test_upgrade_responds_to_health_before_heavy_title_migration(tmp_path):
    run(r'''
import time
from threading import Event
from unittest.mock import patch
from server import db
started=Event()
finished=Event()
# Simulate an existing installation with a pending historical re-protection.
db.init_db()
insert_events([event('prior_history', A, 'Invoice for Acme')])
with connect() as c:
    c.execute("UPDATE events SET window_title=? WHERE event_id='prior_history'",
              ('natalie bergstrom cardiology - Google Search',))
assert db.title_protection_complete() is False
def expensive_history_scan(*,batch_size,pause_seconds):
    # Models a 300k-event migration without spending CI minutes writing fixtures.
    assert batch_size <= 200
    assert pause_seconds >= 0.01
    started.set()
    time.sleep(1.0)
    finished.set()
with patch.object(db,'protect_existing_titles',expensive_history_scan):
    began=time.monotonic()
    with TestClient(app) as c:
        # ASGI lifespan finishes before migration. The launcher sees /health.
        assert time.monotonic()-began < 2.5
        response=c.get('/health')
        assert response.status_code == 200,response.text
        assert not started.is_set(), 'title migration launched before health became available'
        assert db.title_protection_complete() is False
        assert started.wait(5), 'deferred migration never started'
        check=time.monotonic()
        assert c.get('/health').status_code == 200
        assert time.monotonic()-check < 0.8, 'slow migration blocked health'
        assert finished.wait(5)
        assert db.title_protection_complete() is False
''', tmp_path)


def test_title_migration_checkpoint_yields_and_preserves_privacy_marker(tmp_path):
    run(r'''
from server import db
from unittest.mock import patch
db.init_db()
insert_events([
  event('old1', A, 'Invoice for Acme'),
  event('old2', A, 'Invoice for Acme'),
  event('old3', A, 'Invoice for Acme'),
])
with connect() as c:
    for eid in ('old1','old2','old3'):
        c.execute('UPDATE events SET window_title=? WHERE event_id=?',
                  ('natalie bergstrom cardiology - Google Search', eid))
assert db.title_protection_complete() is False
pauses=[]
with patch.object(db.time,'sleep',lambda seconds: pauses.append(seconds)):
    assert db.protect_existing_titles(batch_size=1,pause_seconds=0.02) == 3
assert len(pauses)==3 and all(0 < value <= 0.2 for value in pauses), pauses
assert db.title_protection_complete() is True
with connect() as c:
    assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 3
    for table,column in [('events','window_title'),('normalized_events','window_title'),('context_events','resource_title')]:
        result=[row[0] for row in c.execute(f'SELECT {column} FROM {table}')]
        assert len(result)==3,(table,result)
        assert not any('natalie' in value or 'bergstrom' in value for value in result)
''', tmp_path)



def test_empty_install_title_marker_does_not_delay_full_context(tmp_path):
    run(r'''
from server import db
assert db.title_protection_complete() is False
with TestClient(app) as c:
    assert c.get('/health').status_code == 200
    assert db.title_protection_complete() is True
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
''', tmp_path)
