"""Owned isolated fixture: production routes plus explicit test-only query/lock controls."""
from process_logs_app import app
import threading
from server import event_log_workspace as workspace
from server import event_log_query as query
from server import event_log_jobs as module

_release = threading.Event()
_held = threading.Event()
_heavy = threading.Event()
_entered = threading.Event()
_metrics = query._metrics


def controlled_metrics(conn, metrics):
    if _heavy.is_set():
        _heavy.clear()
        _entered.set()
        conn.execute('SELECT sum(sin(a.i+b.i)) FROM range(1000000) a(i),range(1000000) b(i)').fetchone()
    return _metrics(conn, metrics)


query._metrics = controlled_metrics


@app.post('/test-analysis/hold')
def hold():
    _held.clear();_release.clear()
    def wait():
        with workspace._LOCK:
            _held.set()
            _release.wait(15)
    threading.Thread(target=wait, daemon=True).start()
    assert _held.wait(3)
    return {'held':True}


@app.post('/test-analysis/release')
def release():
    _release.set()
    return {'released':True}


@app.post('/test-analysis/heavy')
def heavy():
    _entered.clear();_heavy.set()
    return {'armed':True}


@app.post('/test-analysis/short-deadline')
def deadline():
    module.DEADLINE_SECONDS = .25
    return {'deadline':.25}


@app.post('/test-analysis/normal-deadline')
def normal():
    module.DEADLINE_SECONDS = 120
    return {'deadline':120}


@app.get('/test-analysis/state')
def state():
    with module.jobs._condition:
        return {'entered':_entered.is_set(),'jobs':len(module.jobs._jobs)}
