"""Local dashboard seeded with owned synthetic retained agent evidence."""
from datetime import datetime,timezone
import json
import sys

from process_logs_app import app,scratch,ROOT
sys.path.insert(0,str(ROOT/'tests'))
import server.workflow_evidence_dashboard  # install the production explorer injection
from shared.lifespan import extend_lifespan
from server import db
from test_procedural_memory_v062 import _run


def seed():
    raw=_run('synthetic-a',0)+_run('synthetic-b',10)+_run('synthetic-c',20)
    today=datetime.now(timezone.utc).date().isoformat()
    for event in raw:event['observed_at']=today+event['observed_at'][10:]
    db.insert_events(raw)
    with db.connect() as conn:
        snapshot=[list(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
    (scratch/'original-events.json').write_text(json.dumps(snapshot),encoding='utf-8')


extend_lifespan(app,startup=seed)
