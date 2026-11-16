"""Local synthetic quarantine-batch fixture for browser tests only."""
from fastapi import Request
from process_logs_app import app
from server import event_log_workspace as workspace
from server.event_log_routes import _require_dashboard

@app.post('/test-seed-quarantine-batches/{log_id}')
def seed(request: Request, log_id: str):
    _require_dashboard(request)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute('''INSERT INTO quarantine SELECT log_id, i, 1, reasons, row
                FROM quarantine, range(1, 205) t(i) WHERE log_id = ? AND batch = 0 AND row_n = 2''', [log_id])
        finally:
            conn.close()
    return {'batches': 205}
