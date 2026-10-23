"""Independent post-deletion verification and content-free receipts (roadmap #264 Stage 8)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

_CODE = r'''
import hashlib, json
from pathlib import Path
from fastapi.testclient import TestClient

from server.db import connect, init_db, insert_events
from collector.outbox import EventOutbox
from server.local_auth import ensure_api_token
from server import agent_session_store
from server.evidence_deletion_receipt import verify_range

DATA = Path(__import__('os').environ['WORKFLOW_OBSERVER_DATA'])
DATA.mkdir(parents=True, exist_ok=True)
base = {'schema_version': '1.0', 'organization_id': '', 'actor_id': '', 'device_id': 'd1', 'sensor_id': 's1',
        'source': 'desktop', 'session_id': 'run1', 'app': 'Editor', 'window_title': 'PRIVATE TITLE',
        'event_type': 'screen_click', 'duration_seconds': 0, 'screenshot_path': None, 'metadata': {}}
inside = dict(base, event_id='inside', observed_at='2026-09-23T08:05:20+00:00')
crossing = dict(base, event_id='crossing', observed_at='2026-09-23T08:04:50+00:00',
                event_type='focus_span', duration_seconds=30)
outside = dict(base, event_id='outside', observed_at='2026-09-23T08:07:00+00:00')
init_db(); assert insert_events([inside, crossing, outside]) == 3
with (DATA / 'events.jsonl').open('w', encoding='utf-8') as handle:
    for event in (inside, crossing, outside):
        handle.write(json.dumps(event) + '\n')
outbox = EventOutbox(DATA / 'collector_outbox.db')
for event in (inside, crossing, outside):
    outbox.enqueue(event)

# An agent conversation message inside the range: kept by design, but must be reported.
agent_session_store.init_agent_session_store()
ref = agent_session_store.session_ref('codex', 'native-1')
with connect() as conn:
    conn.execute("INSERT INTO agent_sessions(session_ref, source, client_id, started_at, updated_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
                 (ref, 'codex', 'c', '2026-09-23T08:05:00+00:00', '2026-09-23T08:05:00+00:00', 'x', 'x'))
    conn.execute("INSERT INTO agent_session_messages(message_ref, session_ref, observed_at, role, content) VALUES (?,?,?,?,?)",
                 ('m' * 32, ref, '2026-09-23T08:05:40+00:00', 'user', 'SECRET MESSAGE'))

before = verify_range('2026-09-23T08:05:00+00:00', '2026-09-23T08:06:00+00:00')
assert before['verified_clean'] is False
assert before['remaining_in_deleted_stores']['events'] == 2, before
assert before['remaining_in_deleted_stores']['collector_outbox'] == 2, before
assert before['remaining_in_deleted_stores']['jsonl'] == 2, before

from server.enterprise_app import app
import server.evidence_delete_routes
headers = {'Authorization': f'Bearer {ensure_api_token()}'}
with TestClient(app) as client:
    response = client.post('/v1/evidence/delete', headers=headers,
                           json={'since': '2026-09-23T08:05:00+00:00', 'until': '2026-09-23T08:06:00+00:00'})
    assert response.status_code == 200, response.text
    payload = response.json()

# Existing response fields are unchanged.
assert payload['status'] == 'deleted' and payload['deleted_events'] == 2
assert payload['gateway_recall_performed'] is False
verification = payload['verification']
assert verification['verified_clean'] is True, verification
assert set(verification['remaining_in_deleted_stores'].values()) == {0}
assert verification['remaining_in_separately_deleted_stores'] == {
    'agent_session_messages': 1, 'agent_working_detail': 0}
receipt = payload['receipt']
assert receipt['format'] == 'openworkgraph.local-deletion-receipt.v1'
assert receipt['deleted']['deleted_events'] == 2
body = {key: value for key, value in receipt.items() if key != 'sha256'}
assert receipt['sha256'] == hashlib.sha256(
    json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
text = json.dumps(payload)
for secret in ('PRIVATE TITLE', 'SECRET MESSAGE', '"inside"', '"crossing"', 'Editor'):
    assert secret not in text, secret

# A row that bypasses deletion is caught: the verifier is not trivially clean.
with connect() as conn:
    conn.execute("INSERT INTO events(event_id, observed_at, device_id, session_id, event_type) VALUES ('bypass','2026-09-23T08:05:30+00:00','d1','run1','screen_click')")
after = verify_range('2026-09-23T08:05:00+00:00', '2026-09-23T08:06:00+00:00')
assert after['verified_clean'] is False and after['remaining_in_deleted_stores']['events'] == 1
print('ok')
'''


def test_deletion_returns_independent_verification_and_content_free_receipt(tmp_path):
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
    })
    result = subprocess.run([sys.executable, "-c", _CODE], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout.strip().endswith("ok")


def test_verifier_never_creates_missing_stores(tmp_path):
    code = r'''
from pathlib import Path
import os
from server.db import init_db
from server.evidence_deletion_receipt import verify_range
init_db()
data = Path(os.environ['WORKFLOW_OBSERVER_DATA'])
result = verify_range('2026-09-23T08:05:00+00:00', '2026-09-23T08:06:00+00:00')
assert result['verified_clean'] is True
assert not (data / 'collector_outbox.db').exists()
from server import work_text_capture
assert not Path(work_text_capture._DB).exists()
print('ok')
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
