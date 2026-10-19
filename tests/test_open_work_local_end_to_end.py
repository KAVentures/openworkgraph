"""Issue #247: integrated local route→source candidate→OWG observed record→case.

Synthetic provider payloads, real local FastAPI authentication and SQLite
history. No real Stripe access, credential write, source modification or agent
run. This is a compatibility and privacy regression, NOT business uplift proof.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_open_work_local_dashboard_evidence_flow_end_to_end(tmp_path):
    script = r'''
import json
from fastapi.testclient import TestClient
from server import db, local_reference_lookup, open_work_preview, open_work_case_context
from server.local_auth import create_dashboard_session, ensure_api_token
import server.secure_app
from server.secure_app import app
from server import local_source_routes as routes

ACCOUNT = "acct_1234567890ABCDEF"
INVOICE = "in_1234567890ABCDEF"
REF = "owg:r:" + "b" * 32
SECRET = b"only-for-test-local-owg-key-1234567890"

db.init_db()
with db.connect() as conn:
    conn.execute(
        "INSERT INTO events(event_id, observed_at, session_id, actor_id, "
        "event_type, source, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("observed-exact-invoice", "2026-10-19T08:00:00Z", "session",
         "actor", "browser_click", "browser_extension",
         json.dumps({"resource_reference": {
             "resource_ref": REF, "provider": "stripe", "resource_kind": "invoice",
             "host": "dashboard.stripe.com"
         }})),
    )
local_reference_lookup.remember_resource_reference(
    {"resource_ref": REF, "provider": "stripe", "resource_kind": "invoice",
     "host": "dashboard.stripe.com"}, "invoice:" + INVOICE,
)

calls = []
def account(url, key):
    calls.append(("account", url))
    return {"object": "account", "id": ACCOUNT}
def inventory(url, key):
    calls.append(("invoices", url))
    return {"object": "list", "has_more": False, "data": [{
        "object": "invoice", "id": INVOICE, "status": "open", "paid": False,
        "due_date": None, "customer_email": "PRIVATE_CUSTOMER@example.com",
        "amount_due": 999999,
    }]}
def scan(**kw):
    return open_work_preview.stripe_open_work_preview(
        environment={
            "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
            "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
            "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_synthetic1234567890",
        },
        reference_key=SECRET, account_reader=account,
        list_reader=inventory, **kw,
    )
routes.stripe_open_work_preview = scan
routes.stripe_open_work_case_context = lambda work_item_ref, **kw: (
    open_work_case_context.stripe_open_work_case_context(
        work_item_ref, inventory_scan=scan, **kw
    )
)
client = TestClient(app)
headers = {"Authorization": "OWG-Session " + create_dashboard_session()}

blocked = client.get("/v1/open-work/stripe-preview",
    params={"confirm_scan": "false", "include_local_observation": "true"},
    headers=headers)
assert blocked.status_code == 403
assert calls == []

for verb in ("get", "post"):
    forbidden = getattr(client, verb)("/v1/local-open-work/mcp-tools",
        headers={"Authorization": "Bearer " + ensure_api_token()},
        **({"json": {"enabled": True}} if verb == "post" else {}))
    assert forbidden.status_code == 403, forbidden.text

preview = client.get("/v1/open-work/stripe-preview",
    params={"confirm_scan": "true", "include_local_observation": "true"},
    headers=headers)
assert preview.status_code == 200, preview.text
data = preview.json()
assert len(data["candidates"]) == 1
assert data["candidates"][0]["historically_observed_in_owg"] == "observed"
assert data["absence_of_observation_proves_no_work"] is False
ref = data["candidates"][0]["work_item_ref"]
assert ref.startswith("owg:w:")
assert "PRIVATE_CUSTOMER" not in preview.text
assert INVOICE not in preview.text

case = client.get("/v1/open-work/stripe-preview/case",
    params={"work_item_ref": ref, "confirm_scan": "true"}, headers=headers)
assert case.status_code == 200, case.text
record = case.json()
assert record["availability"] == "source_reported"
assert record["related_owg_evidence"][0]["resource_ref"] == REF
assert record["case_graph"]["related_observation_count"] == 1
assert record["agent_execution_authorized"] is False
assert record["customer_contact_authorized"] is False
assert record["case_graph"]["authorization"]["agent_may_execute"] is False
assert INVOICE not in case.text
assert "PRIVATE_CUSTOMER" not in case.text
assert "999999" not in case.text

off = client.get("/v1/local-open-work/mcp-tools", headers=headers)
assert off.status_code == 200 and off.json()["enabled"] is False
on = client.post("/v1/local-open-work/mcp-tools", headers=headers,
    json={"enabled": True})
assert on.status_code == 200 and on.json()["enabled"] is True
off_again = client.post("/v1/local-open-work/mcp-tools", headers=headers,
    json={"enabled": False})
assert off_again.status_code == 200 and off_again.json()["enabled"] is False
assert len(calls) == 4
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    })
    env.pop("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", None)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True,
        timeout=90 if os.name == "nt" else 60,
    )
    assert proc.returncode == 0, (
        "Open Work integrated local dashboard test failed:\n"
        + proc.stdout[-1800:] + "\n" + proc.stderr[-2800:]
    )
