"""Isolated synthetic malformed-report fixture; never part of production."""
from fastapi import Request
from process_logs_app import app
from server import event_log_workspace as workspace
from server.event_log_routes import _require_dashboard

@app.post('/test-corrupt-quality/{log_id}')
def corrupt(request: Request, log_id: str):
    _require_dashboard(request)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            workspace._check_log(conn,log_id)
            conn.execute("UPDATE log_quality SET report='{invalid' WHERE log_id=? AND batch=0",[log_id])
        finally:
            conn.close()
    return {'corrupted':True}

@app.post('/test-malformed-quality/{log_id}')
def malformed(request: Request, log_id: str):
    _require_dashboard(request)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            workspace._check_log(conn,log_id)
            conn.execute('UPDATE log_quality SET report=? WHERE log_id=? AND batch=0',
                ['{"batch":0,"content_sha256":42,"content_sha256_v2":7,"transform":{"mapping":{"case_column":"case","attribute_columns":1}},"rows":{"rejected":3,"rejected_by_reason":42}}',log_id])
        finally:
            conn.close()
    return {'malformed':True}
