"""Actual local source flow with canonical synthetic proof and an owned CLI fake."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from process_logs_app import app, scratch
import server.workflow_evidence_dashboard
from server import db, record_graph
from shared.lifespan import extend_lifespan

REF='owg:r:'+'a'*32
original_snapshot=record_graph._gh_pr_snapshot
record_graph.outcome_tracker.enabled=lambda:True
record_graph.resolve_resource_reference=lambda ref: {'provider':'github','resource_kind':'pull_request',
    'host':'github.com','resolver_locator':'owned/reviews/pull/7'} if ref==REF else None
calls=[]


def runner(command, **kwargs):
    assert command[:3]==['/owned/gh','pr','view']
    calls.append(True);(scratch/'source-calls.json').write_text(json.dumps(len(calls)),encoding='utf-8')
    return SimpleNamespace(returncode=0,stdout=json.dumps({'state':'MERGED','mergedAt':'2026-10-20T10:10:00Z',
        'closedAt':None,'reviews':[
            {'id':'PRR_SYNTHETIC_1','state':'CHANGES_REQUESTED','submittedAt':'2026-10-20T10:00:00Z','body':'PRIVATE_SOURCE_TEXT'},
            {'id':'PRR_SYNTHETIC_2','state':'APPROVED','submittedAt':'2026-10-20T10:05:00Z','author':{'login':'PRIVATE_ACTOR'}}]}))


record_graph._gh_pr_snapshot=lambda ref:original_snapshot(ref,runner=runner,which=lambda _:'/owned/gh')


def seed():
    now=datetime.now(timezone.utc).date().isoformat()
    db.insert_events([{'event_id':'github-case-'+str(i),'observed_at':now+f'T10:0{i}:00Z','device_id':'synthetic',
        'session_id':'owned-source-study','app':'Google Chrome','window_title':'Owned workflow','event_type':'browser_click',
        'source':'browser_extension','duration_seconds':1,'metadata':{'resource_reference':{
            'provider':'github','resource_kind':'pull_request','host':'github.com','resource_ref':REF}}} for i in range(3)])
    with db.connect() as conn:
        (scratch/'original-events.json').write_text(json.dumps([list(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]),encoding='utf-8')
    (scratch/'source-calls.json').write_text('0',encoding='utf-8')


extend_lifespan(app,startup=seed)
