"""Owned loopback dashboard with isolated data and a synthetic session."""
import os
import sys
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
scratch=Path(os.environ['OWG_BROWSER_TEST_DIR'])
os.environ.update(WORKFLOW_OBSERVER_DATA=str(scratch/'data'),
    WORKFLOW_OBSERVER_AUTH_DIR=str(scratch/'auth'),WORKFLOW_OBSERVER_CONFIG=str(scratch/'config.json'))
from server.secure_app import app
from server.local_auth import create_dashboard_session

@app.middleware('http')
async def inspect_owned_synthetic_preview(request, call_next):
    if request.url.path in {'/v1/process-mining/preview','/v1/process-mining/datasets'} and request.method == 'POST':
        body=await request.body()
        # Test-only receipt of the actual incoming bytes. Retain only the header
        # and marker presence, never event/case payloads or credentials.
        (scratch/('last-preview-shape.json' if request.url.path.endswith('/preview') else 'last-retention-shape.json')).write_text(json.dumps({
            'header':body.partition(b'\n')[0].decode('utf-8',errors='replace').rstrip('\r'),
            'unselected_test_markers_seen':any(marker in body for marker in (b'UNSELECTED_SECRET',b'ALICE_SECRET',b'BOB_SECRET',b'SALES_SECRET',b'OPS_SECRET','雪'.encode())),
        }),encoding='utf-8')
    return await call_next(request)

(scratch/'session.txt').write_text(create_dashboard_session(),encoding='utf-8')
