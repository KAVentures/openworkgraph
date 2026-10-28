"""Independent interval-intersection controls for a user-declared calendar lens."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from io import BytesIO
import random
import pytest
from server import event_log_waiting as waiting,event_log_workspace as workspace
from server import event_log_calendar_metrics as metrics
from test_event_log_waiting import _data_dir


def calendar(**changes):
    return {'timezone':'UTC','hours':{d:[['09:00','17:00']] for d in ['mon','tue','wed','thu','fri']},**changes}


def imported(rows):
    doc='case,activity,time,finished\n'+'\n'.join(','.join(row) for row in rows)+'\n'
    return workspace.import_log(BytesIO(doc.encode()),name='Owned calendar fixture',source_format='csv',mapping={
        'case_column':'case','activity_column':'activity','timestamp_column':'time','attribute_columns':['finished']})['log_id']


def run(log,spec):return waiting.waiting_time(log,mode='end_attribute',end_attribute='finished',working_calendar=spec)
def by(result):return {r['activity']:r for r in result['activities']}


def test_weekend_gap_keeps_elapsed_and_parallel_schedule_assumption_separate():
    log=imported([('one','A','2024-01-05T15:00:00Z','2024-01-05T16:00:00Z'),
                  ('one','B','2024-01-08T10:00:00Z','2024-01-08T11:00:00Z')])
    before=list(workspace.case_events(log));legacy=run(log,None);report=run(log,calendar())
    assert {k:v for k,v in report.items() if k!='working_calendar'}==legacy
    assert by(report)['B']['waiting_before']['total_seconds']==66*3600
    lens=report['working_calendar'];assert lens['available']
    assert by(lens)['B']['waiting_before']['total_seconds']==2*3600
    assert by(lens)['A']['waiting_before']['median_seconds'] is None
    assert by(lens)['A']['waiting_before']['instances']==0
    assert lens['flow_efficiency']['busy_seconds']==2*3600
    assert lens['flow_efficiency']['elapsed_seconds']==4*3600
    assert lens['flow_efficiency']['median']==.5
    for key in ['actual_working_hours_verified','business_policy_compliance_verified','source_completeness_verified','execution_authorized']:assert lens[key] is False
    assert len(lens['calendar_sha256'])==len(lens['utc_opening_windows_sha256'])==64
    assert list(workspace.case_events(log))==before


def test_holiday_excludes_interval_without_inventing_absent_work_and_changes_clock_pin():
    log=imported([('one','A','2024-01-05T15:00:00Z','2024-01-05T16:00:00Z'),
                  ('one','B','2024-01-08T10:00:00Z','2024-01-08T11:00:00Z')])
    plain=run(log,calendar());closed=run(log,calendar(holidays=['2024-01-08']));lens=closed['working_calendar']
    assert lens['instances_crossing_closed_time']==1
    assert lens['summed_instance_seconds_outside_calendar']==3600
    assert by(lens)['B']['processing']['median_seconds']==0
    assert by(closed)['B']['processing']['median_seconds']==3600
    assert lens['calendar_sha256']!=plain['working_calendar']['calendar_sha256']
    assert lens['utc_opening_windows_sha256']!=plain['working_calendar']['utc_opening_windows_sha256']


def test_closed_only_and_unknown_processing_keep_undefined_efficiency_and_unknowns():
    log=imported([('one','Known','2024-01-06T10:00:00Z','2024-01-06T11:00:00Z'),
                  ('one','Unknown','2024-01-06T12:00:00Z','not-a-time')])
    lens=run(log,calendar())['working_calendar']
    assert lens['flow_efficiency']['median'] is None and lens['flow_efficiency']['cases_with_zero_calendar_span']==1
    assert lens['utc_opening_windows']==0
    assert by(lens)['Unknown']['processing']['median_seconds'] is None
    assert by(lens)['Unknown']['instances_with_processing_time']==0
    assert by(lens)['Known']['processing']['median_seconds']==0


@pytest.mark.parametrize('start,end,expected',[
    ('2024-03-10T05:00:00Z','2024-03-10T08:00:00Z',3*3600),
    ('2024-11-03T04:00:00Z','2024-11-03T09:00:00Z',5*3600)])
def test_dst_opening_boundaries_match_independently_specified_utc_instants(start,end,expected):
    log=imported([('one','Observed',start,end)])
    lens=run(log,calendar(timezone='America/New_York',hours={'sun':[['00:00','04:00']]}))['working_calendar']
    assert by(lens)['Observed']['processing']['median_seconds']==expected
    assert lens['flow_efficiency']['elapsed_seconds']==expected


@pytest.mark.parametrize('spec',[
    {},False,{'timezone':'../UTC','hours':{}},calendar(start='2025-01-01'),calendar(timezone='NOT_A_ZONE'),
    calendar(hours={'mon':[['17:00','09:00']]}),calendar(hours={'mon':[['09:00','12:00'],['11:00','17:00']]}),
    calendar(holidays=['2024-02-30']),calendar(holidays=['x']*401)])
def test_bad_schedules_are_refused_without_partial_lens(spec):
    log=imported([('one','Observed','2024-01-01T09:00:00Z','2024-01-01T10:00:00Z')]);before=list(workspace.case_events(log))
    with pytest.raises(workspace.EventLogWorkspaceError):run(log,spec)
    assert list(workspace.case_events(log))==before


def test_seeded_overlapping_intervals_match_independent_utc_window_intersections():
    rng=random.Random(429);origin=datetime(2024,1,1,tzinfo=timezone.utc);instances=[];rows=[]
    # Independently enumerate UTC openings for two weeks. No shared Calendar or ASOF oracle.
    windows=[(d*86400+9*3600,d*86400+17*3600) for d in range(14) if d%7<5 and d!=3]
    def intersect(a,b):return sum(max(0,min(b,hi)-max(a,lo)) for lo,hi in windows)
    expected={};busy=span=0
    for c in range(30):
        previous=[];clocks=[];case_busy=0
        for i in range(4):
            a=(c%7)*86400+i*5*3600+rng.randrange(0,3600);b=a+rng.randrange(0,12*3600)
            rows.append((str(c),'Step_'+str(i),(origin+timedelta(seconds=a)).isoformat(),(origin+timedelta(seconds=b)).isoformat()))
            instances.append((c,i,a,b))
        sequence=sorted([v for v in instances if v[0]==c],key=lambda v:(v[2],v[3],v[1]))
        for _,i,a,b in sequence:
            last=max((v[3] for v in previous),default=None)
            expected.setdefault('Step_'+str(i),{'proc':[],'wait':[]})['proc'].append(intersect(a,b))
            if last is not None:expected['Step_'+str(i)]['wait'].append(intersect(last,max(last,a)))
            case_busy+=intersect(max(a,last if last is not None else a),max(b,last if last is not None else b))
            previous.append((c,i,a,b))
        busy+=case_busy;span+=intersect(min(v[2] for v in sequence),max(v[3] for v in sequence))
    lens=run(imported(rows),calendar(holidays=['2024-01-04']))['working_calendar'];actual=by(lens)
    for label,e in expected.items():
        assert actual[label]['processing']['mean_seconds']==pytest.approx(sum(e['proc'])/len(e['proc']))
        assert actual[label]['waiting_before']['total_seconds']==sum(e['wait'])
    assert lens['flow_efficiency']['busy_seconds']==busy
    assert lens['flow_efficiency']['elapsed_seconds']==span


def test_pre_epoch_dates_use_portable_clock_arithmetic():
    log=imported([('one','Historical','1900-01-01T09:00:00Z','1900-01-01T10:00:00Z')])
    lens=run(log,calendar())['working_calendar']
    assert by(lens)['Historical']['processing']['median_seconds']==3600


def test_all_day_calendar_keeps_microsecond_intervals_and_legacy_amounts():
    log=imported([('one','A','2024-01-01T09:00:00.000001Z','2024-01-01T09:00:00.000005Z'),
                  ('one','B','2024-01-01T09:00:00.000006Z','2024-01-01T09:00:00.000008Z')])
    report=run(log,calendar(hours={d:[['00:00','24:00']] for d in ['mon','tue','wed','thu','fri','sat','sun']}))
    lens=report['working_calendar']
    for label in ['A','B']:
        assert by(lens)[label]['processing']['median_seconds']==pytest.approx(by(report)[label]['processing']['median_seconds'],abs=1e-12)
        assert by(lens)[label]['waiting_before']['total_seconds']==pytest.approx(by(report)[label]['waiting_before']['total_seconds'],abs=1e-12)


def test_calendar_date_and_instance_budgets_refuse_before_partial_results(monkeypatch):
    log=imported([('one','A','2024-01-01T09:00:00Z','2024-01-10T10:00:00Z')]);before=list(workspace.case_events(log))
    monkeypatch.setattr(metrics,'MAX_DAYS',3)
    with pytest.raises(workspace.EventLogWorkspaceError,match='local dates'):run(log,calendar())
    assert list(workspace.case_events(log))==before
    monkeypatch.setattr(metrics,'MAX_DAYS',3660);monkeypatch.setattr(metrics,'MAX_INSTANCES',0)
    with pytest.raises(workspace.EventLogWorkspaceError,match='instances'):run(log,calendar())
    assert list(workspace.case_events(log))==before


def test_lifecycle_and_case_filter_use_the_existing_instance_projection():
    from test_event_log_waiting import _import
    rows=[('one','A','2024-01-05T15:00:00Z','start'),('one','A','2024-01-05T16:00:00Z','complete'),
          ('one','B','2024-01-08T10:00:00Z','start'),('one','B','2024-01-08T11:00:00Z','complete'),
          ('other','Other','2024-01-09T09:00:00Z','start'),('other','Other','2024-01-09T10:00:00Z','complete')]
    log=_import(['case','activity','time','lifecycle:transition'],rows)
    result=waiting.waiting_time(log,working_calendar=calendar(),cohort={'activity':'B'})
    assert set(by(result['working_calendar']))=={'A','B'}
    assert by(result['working_calendar'])['B']['waiting_before']['total_seconds']==7200


def test_real_http_strict_optional_post_auth_and_unchanged_default(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    doc=b'case,activity,time,finished\nx,A,2024-01-05T15:00:00Z,2024-01-05T16:00:00Z\nx,B,2024-01-08T10:00:00Z,2024-01-08T11:00:00Z\n'
    made=client.post('/v1/event-logs/import?format=csv&name=calendar&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=finished',content=doc,headers={**headers,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/waiting-time'
    payload={'mode':'end_attribute','end_attribute':'finished','working_calendar':{'timezone':'UTC','hours':{d:[['09:00','17:00']] for d in ['mon','tue','wed','thu','fri']}}}
    for h in [{},{'Authorization':'Bearer '+local_auth.ensure_api_token()}]:
        assert client.post(url,headers=h,json=payload).status_code in (401,403)
    response=client.post(url,headers=headers,json=payload)
    assert response.status_code==200 and response.headers['cache-control']=='no-store',response.text
    assert response.json()['working_calendar']['flow_efficiency']['median']==.5
    core=client.get(url,headers=headers,params={'mode':'end_attribute','end_attribute':'finished'}).json()
    bare=client.post(url,headers=headers,json={'mode':'end_attribute','end_attribute':'finished'})
    assert bare.status_code==200 and bare.json()==core
    assert {k:v for k,v in response.json().items() if k!='working_calendar'}==core
    for value in [{**payload,'execution_authorized':True},{**payload,'working_calendar':False},None]:
        assert client.post(url,headers={**headers,'Content-Type':'application/json'},content=__import__('json').dumps(value)).status_code==422
    for data,status in [('x'*65537,413),('{"mode":"auto","mode":"auto"}',422),('{"working_calendar":NaN}',422)]:
        assert client.post(url,headers={**headers,'Content-Type':'application/json'},content=data).status_code==status
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def test_repeated_hour_overlapping_openings_are_unioned_once():
    log=imported([('one','Observed','2024-11-03T05:00:00Z','2024-11-03T07:00:00Z')])
    lens=run(log,calendar(timezone='America/New_York',hours={'sun':[['01:00','01:15'],['01:45','02:00']]}))['working_calendar']
    assert lens['boundary_policy']=='earliest_start_latest_end_union.v1'
    assert lens['utc_opening_windows']==1
    assert by(lens)['Observed']['processing']['median_seconds']==7200
    assert lens['flow_efficiency']['busy_seconds']==7200


def test_skipped_hour_has_no_opening_and_repeated_boundary_policy_is_explicit():
    spring=imported([('one','Observed','2024-03-10T06:00:00Z','2024-03-10T08:00:00Z')])
    lens=run(spring,calendar(timezone='America/New_York',hours={'sun':[['02:10','02:50']]}))['working_calendar']
    assert lens['utc_opening_windows']==0 and lens['flow_efficiency']['median'] is None
    fall=imported([('one','Observed','2024-11-03T05:00:00Z','2024-11-03T07:00:00Z')])
    lens=run(fall,calendar(timezone='America/New_York',hours={'sun':[['01:15','01:45']]}))['working_calendar']
    # Explicit UTC opening 05:15–06:45: earliest opening / latest closing,
    # a continuous 90-minute interval, not pointwise wall-clock filtering.
    assert by(lens)['Observed']['processing']['median_seconds']==5400


def test_only_unmatched_starts_do_not_invent_calendar_durations():
    from test_event_log_waiting import _import
    log=_import(['case','activity','time','lifecycle:transition'],[('one','Pending','2024-01-01T09:00:00Z','start')])
    report=waiting.waiting_time(log,working_calendar=calendar())
    assert report['instances']['unmatched_starts']==1
    assert report['working_calendar']['available'] is False
