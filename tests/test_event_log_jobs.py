"""Real DuckDB cancellation, coherent results, queue budgets and session-scoped HTTP."""
from io import BytesIO
import json
import threading
import time

import pytest

from server import event_log_jobs as module
from server import event_log_query as query
from server import event_log_workspace as workspace
from server.event_log_execution import ExecutionControl, ExecutionStopped

SPEC = {'level': 'cases', 'measures': [{'fn': 'count'}]}


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    manager = module.AnalysisJobs()
    monkeypatch.setattr(module, 'jobs', manager)
    yield manager
    with manager._condition:
        for log in {j['log_id'] for j in manager._jobs.values()}:
            manager.erase_log(log)
    if manager._worker:
        manager._worker.join(timeout=5)
    assert manager._worker is None or not manager._worker.is_alive()


def log():
    return workspace.import_log(BytesIO(b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-02T00:00:00Z\n2,A,2024-01-01T00:00:00Z\n'),
        name='analysis', source_format='csv', mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']


def wait(jobs, log_id, job_id, owner='owner'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = jobs.status(owner, log_id, job_id)
        if status['state'] not in {'queued','running'}:
            return status
        time.sleep(.01)
    pytest.fail('job did not terminate')


def test_exact_same_engine_result_and_immutable_copy(jobs):
    source = log()
    spec = {'level':'cases','dimensions':['last_activity'],'measures':[{'fn':'count'}]}
    expected = query.run(source, spec)
    made = jobs.submit('owner', source, spec)
    spec['measures'][0]['fn'] = 'share'
    assert wait(jobs, source, made['job_id'])['state'] == 'succeeded'
    assert json.loads(jobs.result('owner', source, made['job_id'])) == expected
    status = jobs.status('owner',source,made['job_id'])
    assert not status['source_version_pinned_at_submission'] and not status['durable']
    assert not status['execution_authorized']
    assert 'spec' not in status and 'owner' not in status
    assert jobs.cancel('owner',source,made['job_id'])['state'] == 'succeeded'  # terminal no-op
    jobs.remove('owner',source,made['job_id'])
    with pytest.raises(module.JobError) as exc:jobs.status('owner',source,made['job_id'])
    assert exc.value.status == 404


def test_owner_and_log_isolation(jobs):
    source = log()
    made = jobs.submit('owner',source,SPEC)['job_id']
    wait(jobs,source,made)
    for operation in (jobs.status,jobs.result,jobs.cancel,jobs.remove):
        for owner, log_id in [('other',source),('owner','log_'+'0'*16)]:
            with pytest.raises(module.JobError) as exc:operation(owner,log_id,made)
            assert exc.value.status == 404
    assert json.loads(jobs.result('owner',source,made))['rows'] == [[2]]


def test_cancel_while_waiting_for_workspace_and_no_partial_result(jobs):
    source = log()
    with workspace._LOCK:
        made = jobs.submit('owner',source,SPEC)['job_id']
        until = time.monotonic()+2
        while jobs.status('owner',source,made)['state']=='queued' and time.monotonic()<until:time.sleep(.01)
        assert jobs.cancel('owner',source,made)['state']=='cancelled'
        # Worker exits lock polling without needing the lock's owner to finish.
        time.sleep(.15)
        assert jobs._jobs[made]['result'] is None
        with pytest.raises(module.JobError):jobs.result('owner',source,made)
    assert jobs.status('owner',source,made)['state']=='cancelled'
    assert query.run(source,SPEC)['rows']==[[2]]


def test_deadline_includes_lock_wait_and_queue(jobs,monkeypatch):
    source = log()
    monkeypatch.setattr(module,'DEADLINE_SECONDS',.05)
    with workspace._LOCK:
        first = jobs.submit('owner',source,SPEC)['job_id']
        second = jobs.submit('owner',source,SPEC)['job_id']
        assert wait(jobs,source,first)['state']=='timed_out'
        assert wait(jobs,source,second)['state']=='timed_out'
        with pytest.raises(module.JobError):jobs.result('owner',source,first)
    assert query.run(source,SPEC)['rows']==[[2]]


def test_queue_and_retention_admission_do_not_prune(jobs,monkeypatch):
    source = log()
    monkeypatch.setattr(module,'MAX_PENDING',2)
    with workspace._LOCK:
        ids = [jobs.submit('owner',source,SPEC)['job_id'] for _ in range(2)]
        with pytest.raises(module.JobError) as exc:jobs.submit('other',source,SPEC)
        assert exc.value.status == 429
        assert all(i in jobs._jobs for i in ids)
        for i in ids:jobs.cancel('owner',source,i)
        monkeypatch.setattr(module,'MAX_RETAINED',2)
        with pytest.raises(module.JobError) as exc:jobs.submit('owner',source,SPEC)
        assert exc.value.status == 429
        jobs.remove('owner',source,ids[1])
        jobs.submit('owner',source,SPEC)


def test_expiry_erases_bytes_without_polling(jobs,monkeypatch):
    source = log()
    monkeypatch.setattr(module,'RETENTION_SECONDS',2)
    made = jobs.submit('owner',source,SPEC)['job_id']
    wait(jobs,source,made)
    assert jobs._jobs[made]['result']
    time.sleep(2.3)
    assert made not in jobs._jobs


def test_log_delete_cancels_and_erases_all_sessions(jobs):
    source = log()
    first = jobs.submit('owner',source,SPEC)['job_id']
    wait(jobs,source,first)
    with workspace._LOCK:
        second = jobs.submit('other',source,SPEC)['job_id']
        workspace.delete_log(source)  # RLock: deletion cancels before waiting for lock
    assert not jobs._jobs
    time.sleep(.1)
    assert not jobs._jobs  # late worker must not restore results
    with pytest.raises(module.JobError):jobs.status('other',source,second)


def test_result_budget_and_driver_error_are_safe(jobs,monkeypatch):
    source = log()
    monkeypatch.setattr(module,'MAX_RESULT_BYTES',5)
    made = jobs.submit('owner',source,SPEC)['job_id']
    assert wait(jobs,source,made)['state']=='failed'
    assert 'byte budget' in jobs.status('owner',source,made)['error']
    with pytest.raises(module.JobError):jobs.result('owner',source,made)
    def explode(*args,**kwargs):raise RuntimeError('SECRET source row and SELECT raw_data')
    monkeypatch.setattr(query,'run',explode)
    made = jobs.submit('owner',source,SPEC)['job_id']
    assert wait(jobs,source,made)['error']=='Analysis failed; no partial result was retained'


@pytest.mark.parametrize('spec',[{**SPEC,'sql':'SELECT *'}, {**SPEC,'limit':501}, {**SPEC,'dimensions':['oops']}])
def test_rejects_invalid_before_queue_allocation(jobs,spec):
    with pytest.raises(workspace.EventLogWorkspaceError):jobs.submit('owner',log(),spec)
    assert not jobs._jobs


@pytest.mark.parametrize('reason',['cancelled','timed_out'])
def test_interrupts_real_running_duckdb_and_connection_recovers(tmp_path,monkeypatch,reason):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    source = log()
    entered = threading.Event()
    control = ExecutionControl(time.monotonic()+10)
    errors=[]
    def large(conn):
        entered.set()
        # A genuine engine query, not a mocked slow operation.
        conn.execute('SELECT sum(sin(a.i + b.i)) FROM range(1000000) a(i), range(1000000) b(i)').fetchone()
        return {}
    def run():
        try:workspace._query(source,None,large,control=control)
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=run);worker.start()
    assert entered.wait(3)
    time.sleep(.05)
    control.stop(reason)
    worker.join(3)
    assert not worker.is_alive()
    assert len(errors)==1 and isinstance(errors[0],ExecutionStopped) and errors[0].reason==reason
    assert query.run(source,SPEC)['rows']==[[2]]


def test_cancel_wins_over_late_success(jobs,monkeypatch):
    source=log();entered=threading.Event();release=threading.Event()
    original=query.run
    def held(*args,**kwargs):
        value=original(*args,**kwargs);entered.set();assert release.wait(3);return value
    monkeypatch.setattr(query,'run',held)
    made=jobs.submit('owner',source,SPEC)['job_id']
    try:
        assert entered.wait(3)
        jobs.cancel('owner',source,made)
    finally:release.set()
    time.sleep(.1)
    assert jobs.status('owner',source,made)['state']=='cancelled'
    assert jobs._jobs[made]['result'] is None


def test_log_is_evaluated_when_worker_starts_not_submission(jobs):
    source=log()
    with workspace._LOCK:
        made=jobs.submit('owner',source,SPEC)['job_id']
        workspace.append_log(source,BytesIO(b'case,activity,time\n3,C,2024-01-01T00:00:00Z\n'),source_format='csv',
            mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
    assert wait(jobs,source,made)['state']=='succeeded'
    assert json.loads(jobs.result('owner',source,made))['rows']==[[3]]


def test_real_http_routes_session_boundary_status_cancel_and_erasure(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code = r'''
import json,time
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import event_log_workspace as workspace
from server.event_log_jobs import jobs
with TestClient(app) as client:
    token=local_auth.create_dashboard_session()
    h={'Authorization':'OWG-Session '+token}
    other={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    doc=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n'
    made=client.post('/v1/event-logs/import?format=csv&name=jobs&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc,headers={**h,'X-OWG-Local-Storage-Confirmed':'true'})
    assert made.status_code==200,made.text
    source=made.json()['log_id'];base='/v1/event-logs/'+source+'/analysis-jobs'
    spec={'level':'cases','measures':[{'fn':'count'}]}
    for denied in [bearer,{}, {'Authorization':'OWG-Session invalid'}]:
        assert client.post(base,json=spec,headers=denied).status_code in (401,403)
    assert client.post(base,json={**spec,'sql':'SELECT 1'},headers=h).status_code==422
    assert client.post(base,content=b'x'*65537,headers={**h,'Content-Type':'application/json'}).status_code==413
    assert client.post(base,content=b'{}',headers=h).status_code==415
    r=client.post(base,json=spec,headers=h);assert r.status_code==202,r.text
    assert r.headers['cache-control']=='no-store'
    url=base+'/'+r.json()['job_id']
    for suffix,method in [('',client.get),('/result',client.get),('/cancel',client.post),('/delete',client.post)]:
        assert method(url+suffix,headers=other).status_code==404
        assert method(url+suffix,headers=bearer).status_code in (401,403)
        assert method(url.replace(source,'log_'+'0'*16)+suffix,headers=h).status_code==404
    deadline=time.monotonic()+5
    while client.get(url,headers=h).json()['state'] in ['queued','running']:
        assert time.monotonic()<deadline;time.sleep(.01)
    result=client.get(url+'/result',headers=h)
    assert result.status_code==200 and result.headers['cache-control']=='no-store',result.text
    assert result.json()['rows']==[[1]]
    # Revoking the session prevents access even when the job ID is known.
    with local_auth._LOCK:local_auth._DASHBOARD_SESSIONS.pop(token)
    assert client.get(url+'/result',headers=h).status_code in (401,403)
    with local_auth._LOCK:local_auth._DASHBOARD_SESSIONS[token]=None
    assert client.post(url+'/delete',headers=h).json()=={'deleted':True}
    assert client.get(url+'/result',headers=h).status_code==404
    with workspace._LOCK:
        second=client.post(base,json=spec,headers=h).json()
        url=base+'/'+second['job_id']
        assert client.get(url+'/result',headers=h).status_code==409
        assert client.post(url+'/cancel',headers=h).json()['state']=='cancelled'
    assert client.get(url+'/result',headers=h).status_code==409
    assert client.post('/v1/event-logs/'+source+'/delete',headers=h).status_code==200
    assert client.get(url,headers=h).status_code==404
    assert not jobs._jobs
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    done=subprocess.run([sys.executable,'-c',code],cwd=str(__import__('pathlib').Path(__file__).resolve().parents[1]),
        env=env,text=True,capture_output=True,timeout=60)
    assert done.returncode==0,done.stdout+done.stderr


def test_submission_during_delete_lock_wait_is_erased(jobs,monkeypatch):
    source=log();first_erase=threading.Event();errors=[]
    original=jobs.erase_log
    def observed(log_id):
        original(log_id);first_erase.set()
    monkeypatch.setattr(jobs,'erase_log',observed)
    def delete():
        try:workspace.delete_log(source)
        except Exception as exc:errors.append(exc)
    with workspace._LOCK:
        deleting=threading.Thread(target=delete);deleting.start()
        assert first_erase.wait(3)
        made=jobs.submit('owner',source,SPEC)['job_id']
    deleting.join(5)
    assert not deleting.is_alive() and not errors
    time.sleep(.1)
    assert not jobs._jobs
    with pytest.raises(module.JobError):jobs.result('owner',source,made)


def test_job_deadline_interrupts_native_work_and_next_job_recovers(jobs,monkeypatch):
    source=log();original=query._metrics
    monkeypatch.setattr(module,'DEADLINE_SECONDS',.15)
    def heavy(conn,metrics):
        conn.execute('SELECT sum(sin(a.i+b.i)) FROM range(1000000) a(i),range(1000000) b(i)').fetchone()
        return original(conn,metrics)
    monkeypatch.setattr(query,'_metrics',heavy)
    made=jobs.submit('owner',source,SPEC)['job_id']
    assert wait(jobs,source,made)['state']=='timed_out'
    with pytest.raises(module.JobError):jobs.result('owner',source,made)
    monkeypatch.setattr(query,'_metrics',original)
    monkeypatch.setattr(module,'DEADLINE_SECONDS',5)
    next_job=jobs.submit('owner',source,SPEC)['job_id']
    assert wait(jobs,source,next_job)['state']=='succeeded'
    assert json.loads(jobs.result('owner',source,next_job))['rows']==[[2]]
