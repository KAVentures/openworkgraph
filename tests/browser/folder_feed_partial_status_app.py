"""Browser fixture with an authenticated, explicit scheduler tick."""
import time
from fastapi import HTTPException, Request
from process_logs_app import app
from server import local_scheduler, local_auth
import server.basic_mode_routes  # Install the real Privacy/schedules dashboard.

@app.post('/test/folder-feed/run-due')
async def run_due(request: Request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not local_auth.dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'dashboard session required')
    return local_scheduler.run_due(now=time.time() + 2 * 86400)
