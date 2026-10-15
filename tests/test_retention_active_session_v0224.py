"""History aging must prune rows within an active session, with no backfill."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def _run(tmp_path, code):
    env={**os.environ,
         "WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json"),
         "PYTHONPATH":str(ROOT)}
    result=subprocess.run([sys.executable,"-c",code],cwd=ROOT,
        env=env,text=True,capture_output=True,timeout=100)
    assert result.returncode == 0, result.stdout+"\n"+result.stderr


def test_retention_ages_out_old_events_in_still_active_session(tmp_path):
    _run(tmp_path, """
import json
from datetime import datetime, timezone, timedelta
from server.db import init_db,insert_events,connect
from shared.history_policy import initialize_policy, update_retention
from server.history_retention import cleanup_expired_history
from collector.outbox import EventOutbox
from connector.state import SyncState
from server.db import DATA_DIR
init_db();initialize_policy(has_existing_evidence=False)
now=datetime.now(timezone.utc)
fresh=now.isoformat(); old=(now-timedelta(days=30)).isoformat()
def e(id):
    return {'event_id':id,'observed_at':fresh,'device_id':'d',
            'session_id':'still-recording','app':'Editor','event_type':'focus_span',
            'duration_seconds':10,'metadata':{}}
assert insert_events([e('e-old'),e('e-new')])==2
with connect() as c:
    c.execute('UPDATE events SET observed_at=? WHERE event_id=?',(old,'e-old'))
    c.execute('UPDATE normalized_events SET observed_at=? WHERE event_id=?',(old,'e-old'))
outbox=EventOutbox(DATA_DIR/'collector_outbox.db')
outbox.enqueue(e('e-old'));outbox.enqueue(e('e-new'))
update_retention(human_mode='days',human_days=7,agent_mode='days',agent_days=7,onboarding_complete=True)
result=cleanup_expired_history()
assert sum(c.get('events_deleted',0) for c in result['cleanups'])==1,result
with connect() as c:
    ids={r[0] for r in c.execute('SELECT event_id FROM events')}
    norm={r[0] for r in c.execute('SELECT event_id FROM normalized_events')}
assert ids==norm=={'e-new'},(ids,norm)
assert [x['event_id'] for x in outbox.pending()]==['e-new']
with connect() as c:
    assert c.execute('SELECT COUNT(*) FROM events WHERE session_id=?',('still-recording',)).fetchone()[0]==1
state=SyncState(DATA_DIR/'gateway_sync_state.db')
assert state.skipped(1) is True
assert state.skipped(2) is False
assert cleanup_expired_history()['cleanups']==[]
""")


def test_retention_forever_no_full_history_scan_and_private_db(tmp_path):
    _run(tmp_path, """
import os,stat
from datetime import datetime,timezone
from server.db import init_db,connect,DB_PATH,insert_events
from server.history_retention import cleanup_expired_history
from shared.history_policy import initialize_policy,update_retention
init_db();initialize_policy(has_existing_evidence=False)
update_retention(human_mode='forever',human_days=None,agent_mode='forever',agent_days=None,onboarding_complete=True)
insert_events([{'event_id':'still-there','observed_at':datetime.now(timezone.utc).isoformat(),
'device_id':'d','session_id':'s','app':'Editor','event_type':'focus_span','duration_seconds':1,'metadata':{}}])
result=cleanup_expired_history()
with connect() as c: assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==1
assert result['cleanups']==[]
if os.name=='posix':
    assert stat.S_IMODE(DB_PATH.stat().st_mode)==0o600
""")


def test_periodic_retention_is_registered():
    source=(ROOT/"server/history_routes.py").read_text()
    assert "_retention_stop.wait(3600)" in source
    assert "cleanup_expired_history(startup=False)" in source
    assert "threading.Thread" in source
    assert "_retention_stop.set()" in source
