"""Local synthetic batch-history fixture for browser tests only."""
from datetime import datetime
import json
from fastapi import Request
from process_logs_app import app
from server import event_log_workspace as workspace
from server.event_log_routes import _require_dashboard

@app.post('/test-seed-quality-batches/{log_id}')
def seed(request: Request, log_id: str):
    _require_dashboard(request)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            original = json.loads(conn.execute('SELECT report FROM log_quality WHERE log_id = ?', [log_id]).fetchone()[0])
            now = datetime(2024, 1, 1)
            conn.executemany('INSERT INTO log_appends VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [(log_id, i, now, 'csv', 1, 0, 0, 0) for i in range(1, 205)])
            reports = []
            for i in range(1, 205):
                report = dict(original, batch=i, kind='append')
                report['rows'] = {**original['rows'], 'duplicates_skipped_on_append': 0}
                reports.append((log_id, i, now, json.dumps(report)))
            conn.executemany('INSERT INTO log_quality VALUES (?, ?, ?, ?)', reports)
        finally:
            conn.close()
    return {'batches': 205}
