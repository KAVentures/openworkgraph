"""Isolated synthetic history fixture for the quarantine browser journey."""
from datetime import datetime, timedelta
import json
from fastapi import Request
from process_logs_app import app
from server import event_log_workspace as workspace
from server.event_log_routes import _require_dashboard

@app.post('/test-seed-decisions/{log_id}')
def seed(request: Request, log_id: str):
    _require_dashboard(request)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.executemany('INSERT INTO quarantine_decisions VALUES (?, ?, ?)',
                [(log_id, datetime(2024, 1, 1) + timedelta(seconds=i),
                  json.dumps({'kind': 'discard', 'rows_discarded': i})) for i in range(205)])
        finally:
            conn.close()
    return {'seeded': 205}
