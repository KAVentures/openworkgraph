"""Export blind synthetic cases through the shipped authenticated hosted MCP.

This checks presentation/retrieval fidelity. It is not a model efficacy trial
or a test of a real OS sensor; browser runtime tests cover that earlier boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from copy import deepcopy

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from connector.policy import prepare_event_for_gateway, merge_policies
from gateway.auth import Principal
from gateway.db import GatewayDB
from gateway.public_plugin_mcp import create_app
from .fixtures import generate_cases
from .prepare import SYSTEM_INSTRUCTION, split_cases


@contextmanager
def replay_client(directory: Path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="replay", use="sig", alg="RS256")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"keys": [jwk]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    issuer = f"http://127.0.0.1:{listener.server_port}"
    values = {
        "OWG_GATEWAY_DATABASE_URL": "sqlite:///" + str(directory / "replay.db"),
        "OWG_PLUGIN_OAUTH_ISSUER": issuer,
        "OWG_PLUGIN_OAUTH_JWKS_URL": issuer + "/jwks",
        "OWG_PLUGIN_OAUTH_ALGORITHMS": "RS256",
        "OWG_PLUGIN_TOKEN_AUDIENCE": "replay",
        "OWG_PLUGIN_REQUIRED_SCOPE": "work:read",
        "OWG_PLUGIN_RESOURCE_URL": "https://replay.invalid/mcp",
        "OWG_PLUGIN_PROFILE_KEY": "disposable-synthetic-replay-only",
        "OWG_CLOUD_WORK_TEXT_ENABLED": "0",
    }
    old = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    db = GatewayDB(values["OWG_GATEWAY_DATABASE_URL"])
    try:
        with TestClient(create_app(), base_url="https://replay.invalid") as client:
            def call(case_id, name, arguments):
                token = jwt.encode({"iss": issuer, "aud": "replay", "sub": case_id,
                    "scope": "work:read", "exp": int(time.time()) + 600}, key,
                    algorithm="RS256", headers={"kid": "replay"})
                response = client.post("/mcp", headers={"Authorization": "Bearer " + token,
                    "Accept": "application/json, text/event-stream"}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
                response.raise_for_status()
                result = response.json()["result"]
                if result.get("isError"):
                    raise RuntimeError("MCP replay failed: " + str(result))
                return result["structuredContent"]
            yield db, call
    finally:
        listener.shutdown()
        listener.server_close()
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def replay_case(db, call, case, *, detail="rich", page_size=25):
    org = "oauth-sub:" + case["case_id"]
    actor = Principal("device", "synthetic", org, org, "synthetic", frozenset())
    # Relative chronology is retained; move the synthetic day into the current
    # visibility window so replay isn't silently empty after retention changes.
    source = deepcopy(case["source_events"])
    base = datetime.fromisoformat(source[0]["observed_at"].replace("Z", "+00:00"))
    offset = datetime.now(timezone.utc) - timedelta(days=1) - base
    policy = merge_policies({"allow_agent_events": True}, {"allow_agent_events": True})
    for row in source:
        row["observed_at"] = (datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")) + offset).isoformat()
    prepared = [prepare_event_for_gateway(row, policy) for row in source]
    prepared = [row for row in prepared if row is not None]
    inserted, _ = db.insert_events(actor, prepared)
    rows, cursor = [], None
    while True:
        page = call(case["case_id"], "get_workflow_trace", {"limit": page_size,
            "detail": detail, **({"cursor": cursor} if cursor else {})})
        rows.extend(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return {"case_id": case["case_id"], "system_instruction": SYSTEM_INSTRUCTION,
            "presented_evidence": rows, "replay_contract": {
                "source_rows": len(source), "prepared_rows": len(prepared), "inserted_rows": inserted,
                "returned_rows": len(rows), "detail": detail,
                "path": "connector_policy_gateway_store_oauth_hosted_mcp",
                "synthetic": True, "os_sensor_exercised": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detail", choices=("compact", "rich"), default="rich")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="owg-replay-") as temporary:
        with replay_client(Path(temporary)) as (db, call), args.output.open("w") as output:
            for case in split_cases(generate_cases(), args.split):
                output.write(json.dumps(replay_case(db, call, case, detail=args.detail)) + "\n")
    print("Wrote blind authenticated MCP replay to", args.output)


if __name__ == "__main__":
    main()
