"""Independent interval-boundary oracle for overlapping wait hotspot labels."""
from collections import defaultdict
import random
import pytest
from server import event_log_waiting as waiting
from test_event_log_waiting import _data_dir, _at, _import, _by


def imported(instances,mode):
    if mode=='lifecycle':
        rows=[('case_'+str(case),activity,_at(0,t),transition) for case,activity,start,end in instances
              for t,transition in [(start,'start'),(end,'complete')]]
        return _import(['case','activity','time','lifecycle:transition'],rows)
    return _import(['case','activity','time','finished'],
        [('case_'+str(case),activity,_at(0,start),_at(0,end)) for case,activity,start,end in instances])


def measured(instances,mode):
    return waiting.waiting_time(imported(instances,mode),mode=mode,**({'end_attribute':'finished'} if mode=='end_attribute' else {}))


def oracle(instances):
    # Scan sorted earlier instances without a lag/window/max-struct analogue.
    groups=defaultdict(list);hotspots=defaultdict(list);busy=0;span=0
    for case,activity,start,end in instances:groups[case].append((start,end,activity))
    for values in groups.values():
        earlier=[]
        for start,end,activity in sorted(values):
            if earlier:
                boundary=max(e for s,e,a in earlier)
                candidates=[(s,a) for s,e,a in earlier if e==boundary]
                _,source=sorted(candidates)[-1]
                hotspots[(source,activity)].append(max(0,start-boundary)*3600)
            earlier.append((start,end,activity))
        merged=[]
        for start,end,_ in sorted(values):
            if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
            else:merged.append([start,end])
        busy+=sum(end-start for start,end in merged)*3600
        span+=(max(e for s,e,a in values)-min(s for s,e,a in values))*3600
    return hotspots,busy,span


@pytest.mark.parametrize('mode',['lifecycle','end_attribute'])
def test_nested_finish_order_assigns_gap_to_actual_latest_completion_boundary(mode):
    values=[(1,'Long',0,10),(1,'Short',1,2),(1,'Next',20,21)]
    result=measured(values,mode)
    gap=next(h for h in result['waiting_hotspots'] if h['to']=='Next')
    assert gap['from']=='Long' and gap['total_waiting_seconds']==10*3600
    assert _by(result)['Next']['waiting_before']['median_seconds']==10*3600
    assert result['flow_efficiency']['busy_seconds']==11*3600
    assert result['flow_efficiency']['elapsed_seconds']==21*3600


@pytest.mark.parametrize('mode',['lifecycle','end_attribute'])
def test_seeded_nested_crossed_tied_zero_intervals_match_independent_oracle(mode):
    rng=random.Random(427);values=[]
    for case in range(40):
        for i in range(5):
            start=rng.randrange(0,24);end=start+rng.randrange(0,12)
            values.append((case,'Activity_'+str(i),start,end))
    rng.shuffle(values);result=measured(values,mode);expected,busy,span=oracle(values)
    # Five activities have at most 25 transition pairs, exactly the existing cap.
    actual={(h['from'],h['to']):h for h in result['waiting_hotspots']}
    assert set(actual)==set(expected)
    for key,times in expected.items():
        assert actual[key]['count']==len(times)
        assert actual[key]['total_waiting_seconds']==sum(times)
        ordered=sorted(times);mid=len(times)//2
        median=ordered[mid] if len(times)%2 else (ordered[mid-1]+ordered[mid])/2
        assert actual[key]['median_waiting_seconds']==median
    assert result['flow_efficiency']['busy_seconds']==busy
    assert result['flow_efficiency']['elapsed_seconds']==span


@pytest.mark.parametrize('mode',['lifecycle','end_attribute'])
def test_tied_latest_end_uses_latest_start_then_activity_and_ignores_input_order(mode):
    values=[(1,'Earlier',0,10),(1,'Zulu',1,10),(1,'Alpha',1,10),(1,'Next',20,21)]
    for order in [values,list(reversed(values)),[values[i] for i in [2,0,3,1]]]:
        result=measured(order,mode)
        gap=next(h for h in result['waiting_hotspots'] if h['to']=='Next')
        assert gap['from']=='Zulu' and gap['total_waiting_seconds']==10*3600


@pytest.mark.parametrize('mode',['lifecycle','end_attribute'])
def test_attribution_query_preserves_canonical_case_events_and_timing_controls(mode):
    from server import event_log_workspace as workspace
    values=[(1,'Long',0,10),(1,'Short',1,2),(1,'Next',20,21),(2,'Independent',0,40)]
    log=imported(values,mode);before=list(workspace.case_events(log))
    result=waiting.waiting_time(log,mode=mode,**({'end_attribute':'finished'} if mode=='end_attribute' else {}))
    assert list(workspace.case_events(log))==before
    assert result['flow_efficiency']['busy_seconds']==51*3600
    assert result['flow_efficiency']['elapsed_seconds']==61*3600
    assert _by(result)['Long']['processing']['median_seconds']==10*3600
    assert all(h['from']!='Independent' and h['to']!='Independent' for h in result['waiting_hotspots'])


def test_real_http_keeps_dashboard_auth_and_reports_actual_boundary(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    for mode,attribute,doc in [
        ('lifecycle','lifecycle:transition',b'case,activity,time,lifecycle:transition\nx,Long,2024-01-01T00:00:00Z,start\nx,Short,2024-01-01T01:00:00Z,start\nx,Short,2024-01-01T02:00:00Z,complete\nx,Long,2024-01-01T10:00:00Z,complete\nx,Next,2024-01-01T20:00:00Z,start\nx,Next,2024-01-01T21:00:00Z,complete\n'),
        ('end_attribute','finished',b'case,activity,time,finished\nx,Long,2024-01-01T00:00:00Z,2024-01-01T10:00:00Z\nx,Short,2024-01-01T01:00:00Z,2024-01-01T02:00:00Z\nx,Next,2024-01-01T20:00:00Z,2024-01-01T21:00:00Z\n')]:
        made=client.post('/v1/event-logs/import?format=csv&name=overlap&case_column=case&activity_column=activity&timestamp_column=time&attribute_column='+attribute,
            content=doc,headers={**headers,'x-owg-local-storage-confirmed':'true'})
        assert made.status_code==200,made.text
        url='/v1/event-logs/'+made.json()['log_id']+'/waiting-time'
        assert client.get(url).status_code==401
        assert client.get(url,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code in (401,403)
        report=client.get(url,headers=headers,params={'mode':mode,**({'end_attribute':attribute} if mode=='end_attribute' else {})})
        assert report.status_code==200 and report.headers['cache-control']=='no-store',report.text
        row=next(h for h in report.json()['waiting_hotspots'] if h['to']=='Next')
        assert row['from']=='Long' and row['total_waiting_seconds']==36000
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
