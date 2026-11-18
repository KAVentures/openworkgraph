"""Owned browser fixture with more distinct keys than a single quality page."""
import io
import json
from datetime import datetime
from process_logs_app import app
from server import event_log_workspace as workspace

made = workspace.import_log(io.BytesIO(b'case,activity,time\nc1,Start,2026-01-01T00:00:00Z\n'),
    name='Coverage pages', source_format='csv',
    mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
conn = workspace._connect()
try:
    conn.execute('UPDATE events SET attributes=? WHERE log_id=?',
        [json.dumps({f'key{i:02d}': 'private-value' for i in range(32)}), made['log_id']])
    conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)',
        [made['log_id'], 'fixture-extra', 'End', datetime(2026, 1, 2), '',
         json.dumps({f'key{i:02d}': 'private-value' for i in range(32, 40)})])
finally:
    conn.close()
