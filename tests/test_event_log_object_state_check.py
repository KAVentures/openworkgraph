"""As-of attribute checks against real retained OCEL fixtures."""
from copy import deepcopy
import csv
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_workspace as w
from server import event_log_object_state_check as s


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(w,"DATA_DIR",tmp_path)


def fixture():
    def obj(ident,values):
        return {"id":ident,"type":"order","attributes":[{"name":"status","time":at,"value":v} for at,v in values]}
    def ts(hour):
        return f"2024-01-01T{hour:02d}:00:00Z"
    def event(ident,h,links):
        return {"id":ident,"type":"Pay","time":ts(h),"relationships":[{"objectId":o,"qualifier":q} for o,q in links]}
    return {"objectTypes":[{"name":"order","attributes":[]},{"name":"invoice","attributes":[]}],"eventTypes":[],
            "objects":[obj("a",[("1970-01-01T00:00:00Z","created"),(ts(3),"approved")]),
                       obj("b",[(ts(4),"approved")]),
                       obj("c",[(ts(1),"created"),(ts(1),"approved"),(ts(5),"approved")]),
                       obj("d",[]),{"id":"i","type":"invoice","attributes":[]}],
            "events":[event("e1",2,[("a",""),("a","payer")]),
                      event("e2",3,[("a",""),("b","")]),
                      event("e3",4,[("a",""),("b","")]),
                      event("e4",2,[("c","")]),
                      event("e5",6,[("c","")]),
                      event("e6",2,[("d","")]),
                      event("e7",2,[("i","")]),
                      event("e8",2,[("a",""),("d","")])]}


def load():
    return w.import_log(BytesIO(json.dumps(fixture()).encode()),name="orders",source_format="ocel")["log_id"]


def spec(**kwargs):
    return {"activity":"Pay","object_type":"order","attribute":"status","expected_value":"approved",**kwargs}


def test_asof_values_conflict_resolution_missing_and_quantifiers():
    log=load()
    a=s.analyze(log,spec())
    assert a["selected_events"]==8 and a["selected_distinct_event_object_links"]==10
    assert a["event_counts"]=={"matched":2,"not_matched":2,"unknown":3,"no_linked_object":1}
    assert a["object_link_counts"]=={"matched":4,"not_matched":2,"missing_history":2,
                                      "before_first_value":1,"ambiguous_value":1}
    rows={r["event_id"]:r for r in a["rows"]}
    assert rows["e1"]["linked_objects"]==1
    assert rows["e2"]["object_witnesses"][0]["recorded_value"]=="approved"
    assert rows["e2"]["object_witnesses"][0]["valid_from"]=="2024-01-01T03:00:00Z"
    assert rows["e4"]["object_witnesses"][0]["status"]=="ambiguous_value"
    assert rows["e5"]["status"]=="matched"
    assert rows["e7"]["status"]=="no_linked_object"
    any_=s.analyze(log,spec(quantifier="any"))
    assert any_["event_counts"]=={"matched":3,"not_matched":1,"unknown":3,"no_linked_object":1}
    assert not any_["policy_compliance_verified"]


def test_exact_qualifier_empty_and_ne():
    log=load()
    result=s.analyze(log,spec(event_qualifier="payer",operator="ne"))
    assert result["event_counts"]=={"matched":1,"not_matched":0,"unknown":0,"no_linked_object":7}
    assert result["selected_distinct_event_object_links"]==1
    assert s.analyze(log,spec(event_qualifier=""))["selected_distinct_event_object_links"]==10


def test_filtered_pagination_and_revision_rejection():
    log=load()
    first=s.analyze(log,spec(status="unknown",limit=1))
    assert first["matching_events"]==3 and first["next_offset"]==1
    second=s.analyze(log,spec(status="unknown",limit=1,offset=1,expected_revision=first["revision"]))
    assert first["rows"][0]["event_id"]!=second["rows"][0]["event_id"]
    with pytest.raises(w.EventLogWorkspaceError,match="page one"):
        s.analyze(log,spec(offset=1))
    with pytest.raises(w.EventLogWorkspaceError,match="changed"):
        s.analyze(log,spec(expected_revision=first["revision"]+1))


def test_all_event_witnesses_page_with_exact_revision_and_filter():
    objects=[]
    for n in range(120):
        values=([{'name':'status','time':'1970-01-01T00:00:00Z','value':'approved'}] if n<60 else
                [{'name':'status','time':'2024-01-01T01:00:00Z','value':v} for v in ('one','two')] if n<90 else [])
        objects.append({'id':f'o{n:03}','type':'order','attributes':values})
    doc={'objectTypes':[],'eventTypes':[],'objects':objects,
         'events':[{'id':'big','type':'Pay','time':'2024-01-01T02:00:00Z',
                    'relationships':[{'objectId':o['id'],'qualifier':''} for o in objects]}]}
    log=w.import_log(BytesIO(json.dumps(doc).encode()),name='many',source_format='ocel')['log_id']
    report=s.analyze(log,spec())
    assert report['rows'][0]['object_witnesses_truncated'] and report['rows'][0]['status']=='unknown'
    request=spec(event_id='big',status='unknown',expected_revision=report['revision'],witness_limit=50)
    first=s.witnesses(log,request)
    second=s.witnesses(log,{**request,'after':first['next_after']})
    third=s.witnesses(log,{**request,'after':second['next_after']})
    rows=first['witnesses']+second['witnesses']+third['witnesses']
    assert first['total_objects']==120 and third['next_after'] is None
    assert [r['object_id'] for r in rows]==[f'o{n:03}' for n in range(120)]
    assert rows[0]['status']=='matched' and rows[60]['status']=='ambiguous_value' and rows[-1]['status']=='missing_history'
    with pytest.raises(w.EventLogWorkspaceError,match='not in'):
        s.witnesses(log,{**request,'status':'matched'})
    with pytest.raises(w.EventLogWorkspaceError,match='not in'):
        s.witnesses(log,{**request,'event_id':'missing'})
    with pytest.raises(w.EventLogWorkspaceError,match='changed'):
        s.witnesses(log,{**request,'expected_revision':report['revision']+1})
    with pytest.raises(w.EventLogWorkspaceError,match='invalid'):
        s.witnesses(log,spec(event_id='big'))
    with pytest.raises(w.EventLogWorkspaceError,match='invalid'):
        s.witnesses(log,{**request,'witness_limit':101})
    newer={'objectTypes':[],'eventTypes':[], 'objects':[{'id':'o000','type':'order'}],
           'events':[{'id':'new-event','type':'Pay','time':'2024-01-02T00:00:00Z',
                      'relationships':[{'objectId':'o000','qualifier':''}]}]}
    w.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
    with pytest.raises(w.EventLogWorkspaceError,match='changed'):
        s.witnesses(log,{**request,'after':first['next_after']})
    other=load();base=s.analyze(other,spec())
    empty=s.witnesses(other,spec(event_id='e7',expected_revision=base['revision'],status='no_linked_object'))
    assert empty['total_objects']==0 and empty['witnesses']==[] and empty['next_after'] is None


def test_state_check_reads_one_snapshot_during_append(monkeypatch):
    log=load()
    original=s.relationships._revision
    appended=[]
    def interleave(conn, log_id):
        revision=original(conn,log_id)
        if not appended:
            appended.append(True)
            newer={'objectTypes':[],'eventTypes':[], 'objects':[{'id':'a','type':'order'}],
                   'events':[{'id':'new-event','type':'Pay','time':'2024-01-02T00:00:00Z',
                              'relationships':[{'objectId':'a','qualifier':''}]}]}
            w.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
        return revision
    monkeypatch.setattr(s.relationships,'_revision',interleave)
    reviewed=s.analyze(log,spec())
    assert reviewed['selected_events']==8 and reviewed['revision']==0
    assert s.analyze(log,spec())['selected_events']==9


def test_complete_review_csv_exports_all_events_and_objects(monkeypatch):
    objects=[]
    for n in range(120):
        values=[] if n==2 else [{'name':'=status','time':'1970-01-01T00:00:00Z','value': '=value' if n%2==0 else ''}]
        objects.append({'id':f'+o{n:03}','type':'order','attributes':values})
    objects.append({'id':'item','type':'item'})
    events=[{'id':f'=e{n:03}','type':'=Pay','time':'2024-01-01T00:00:00Z',
             'relationships':[{'objectId':f'+o{n:03}','qualifier':''},{'objectId':f'+o{n:03}','qualifier':'@other'}]}
            for n in range(120)]
    events.append({'id':'=no-object','type':'=Pay','time':'2024-01-01T00:00:00Z',
                   'relationships':[{'objectId':'item','qualifier':''}]})
    log=w.import_log(BytesIO(json.dumps({'objectTypes':[],'eventTypes':[],'objects':objects,'events':events}).encode()),
                     name='many',source_format='ocel')['log_id']
    settings={'activity':'=Pay','object_type':'order','attribute':'=status','expected_value':'=value','event_qualifier':''}
    report=s.analyze(log,settings);revision=report['revision']
    for kind in ('events','objects'):
        output=StringIO(newline='')
        meta=s.export_worklist(log,{**settings,'expected_revision':revision,'kind':kind},output)
        rows=list(csv.DictReader(StringIO(output.getvalue())))
        check=rows.pop(0)
        assert check['record_type']=='check' and check['log_revision']==str(revision)
        assert None not in check and check['recorded_value_present']=='false'
        assert meta['rows']==len(rows)==121
        assert rows[0]['event_id_review']=="'=e000" and rows[0]['activity_review']=="'=Pay"
        assert rows[0]['attribute_review']=="'=status" and rows[0]['expected_value_review']=="'=value"
        assert rows[0]['qualifier_present']=='true' and rows[0]['qualifier_review']==''
        if kind=='objects':
            assert rows[0]['object_id_review']=="'+o000" and rows[0]['recorded_value_review']=="'=value"
            assert rows[1]['recorded_value_review']=='' and rows[1]['recorded_value_present']=='true'
            assert rows[2]['object_result']=='missing_history' and rows[2]['recorded_value_present']=='false'
            assert rows[-1]['record_type']=='event_without_object' and rows[-1]['event_result']=='no_linked_object'
    filtered=StringIO(newline='')
    assert s.export_worklist(log,{**settings,'status':'unknown','expected_revision':revision,'kind':'objects'},filtered)['rows']==1
    assert list(csv.DictReader(StringIO(filtered.getvalue())))[1]['object_result']=='missing_history'
    empty=StringIO(newline='')
    empty_settings={**settings,'expected_value':'absent','status':'matched','expected_revision':revision,'kind':'events'}
    assert s.export_worklist(log,empty_settings,empty)['rows']==0
    empty_rows=list(csv.DictReader(StringIO(empty.getvalue())))
    assert len(empty_rows)==1 and empty_rows[0]['record_type']=='check' and empty_rows[0]['result_filter']=='matched'
    monkeypatch.setattr(s,'MAX_WORKLIST_BYTES',1)
    too_large=StringIO()
    with pytest.raises(w.EventLogWorkspaceError,match='file limit'):
        s.export_worklist(log,{**settings,'expected_revision':revision,'kind':'events'},too_large)
    assert too_large.getvalue()==''
    monkeypatch.setattr(s,'MAX_WORKLIST_BYTES',512*1024*1024)
    for bad in ({'kind':'events'},{'kind':'other','expected_revision':revision},
                {'kind':'events','expected_revision':revision,'offset':1}):
        out=StringIO()
        with pytest.raises(w.EventLogWorkspaceError):s.export_worklist(log,{**settings,**bad},out)
        assert out.getvalue()==''
    monkeypatch.setattr(s,'MAX_EVENTS',1)
    out=StringIO()
    with pytest.raises(w.EventLogWorkspaceError,match='event bound'):
        s.export_worklist(log,{**settings,'expected_revision':revision,'kind':'events'},out)
    assert out.getvalue()==''
    monkeypatch.setattr(s,'MAX_EVENTS',200_000)
    newer={'objectTypes':[],'eventTypes':[],'objects':[{'id':'+o000','type':'order'}],
           'events':[{'id':'new-event','type':'=Pay','time':'2024-01-02T00:00:00Z',
                      'relationships':[{'objectId':'+o000','qualifier':''}]}]}
    w.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
    out=StringIO()
    with pytest.raises(w.EventLogWorkspaceError,match='changed'):
        s.export_worklist(log,{**settings,'expected_revision':revision,'kind':'events'},out)
    assert out.getvalue()==''


@pytest.mark.parametrize("bad",[{"limit":True},{"quantifier":"none"},{"operator":"gt"},{"extra":1},
                              {"activity":""},{"offset":1}])
def test_invalid_requests(bad):
    with pytest.raises(w.EventLogWorkspaceError):
        s.analyze(load(),spec(**bad))


def test_work_bounds(monkeypatch):
    log=load()
    monkeypatch.setattr(s,"MAX_EVENTS",1)
    with pytest.raises(w.EventLogWorkspaceError,match="event bound"):
        s.analyze(log,spec())
    monkeypatch.setattr(s,"MAX_EVENTS",100)
    monkeypatch.setattr(s,"MAX_LINKS",1)
    with pytest.raises(w.EventLogWorkspaceError,match="links"):
        s.analyze(log,spec())


def test_http_auth_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_check import load,spec
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    log=load();url="/v1/event-logs/"+log+"/object-state-check"
    assert c.post(url,json=spec(),headers=bearer).status_code in (401,403)
    r=c.post(url,json=spec(),headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["event_counts"]["matched"]==2
    witness_url=url+'/witnesses'
    payload=spec(event_id='e4',expected_revision=r.json()['revision'])
    assert c.post(witness_url,json=payload,headers=bearer).status_code in (401,403)
    witness=c.post(witness_url,json=payload,headers=h)
    assert witness.status_code==200 and witness.headers['cache-control']=='no-store',witness.text
    assert witness.json()['witnesses'][0]['status']=='ambiguous_value'
    assert c.post(witness_url,json=spec(event_id='e4'),headers=h).status_code==422
    export_url=url+'/export';export_spec=spec(expected_revision=r.json()['revision'],kind='events')
    assert c.post(export_url,json=export_spec,headers=bearer).status_code in (401,403)
    exported=c.post(export_url,json=export_spec,headers=h)
    assert exported.status_code==200 and exported.headers['cache-control']=='no-store',exported.text
    assert exported.headers['x-owg-rows']=='8' and exported.headers['content-type'].startswith('text/csv')
    assert 'event_id_review' in exported.text
    assert c.post(export_url,json=spec(kind='events'),headers=h).status_code==422
    from server import event_log_workspace as w
    from server import event_log_object_state_check as state
    state.MAX_WORKLIST_BYTES=1
    assert c.post(export_url,json=export_spec,headers=h).status_code==422
    assert not list(w.DATA_DIR.glob('*.review.csv'))
    assert c.post(url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
