"""Loopback-only authenticated synthetic Gateway; never uses ambient OWG settings."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from connector.policy import merge_policies, prepare_event_for_gateway
from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings
from mcp.server.transport_security import TransportSecuritySettings
from .fixtures import fixture_events


def seed(db: GatewayDB, settings: GatewaySettings) -> dict:
    """Enroll via one-use grants and ingest through normal authenticated API."""
    admin = {"Authorization": f"Bearer {settings.admin_token}"}
    counts = {}
    with TestClient(create_app(settings=settings, db=db)) as client:
        for person in ("populated", "empty", "local-only"):
            org = f"oauth-sub:eval-{person}"
            response = client.put(f"/v1/admin/policy/{org}", headers=admin,
                                  json={"policy": {"allow_agent_events": True}})
            response.raise_for_status()
            policy = response.json()["policy"]
            grant = client.post("/v1/admin/enrollment-codes", headers={
                "Authorization": f"Bearer {settings.enrollment_token}"},
                json={"organization_id": org, "actor_id": org})
            grant.raise_for_status()
            enrolled = client.post("/v1/devices/enroll", headers={
                "Authorization": f"Bearer {grant.json()['token']}"},
                json={"organization_id": org, "actor_id": org, "device_id": "synthetic"})
            enrolled.raise_for_status()
            # User instruction authorizes synthetic structural agent sharing only.
            effective = merge_policies({"allow_agent_events": True}, policy)
            rows = [prepare_event_for_gateway(e, effective) for e in fixture_events()]
            rows = [e for e in rows if e is not None] if person == "populated" else []
            ingested = client.post("/v1/evidence/batch", headers={
                "Authorization": f"Bearer {enrolled.json()['token']}"}, json={"events": rows})
            ingested.raise_for_status()
            counts[person] = ingested.json()["inserted"]
            client.put(f"/v1/admin/policy/{org}", headers=admin,
                       json={"policy": {"allow_agent_events": False}}).raise_for_status()
    return counts


def serve(state: Path, port: int) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="disposable", use="sig", alg="RS256")
    # PyJWT's JWKS fetch is synchronous. A separate listener/thread prevents it
    # from blocking on the MCP server's own event loop during verification.
    class JWKSHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps({"keys": [jwk]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    key_server = ThreadingHTTPServer(("127.0.0.1", 0), JWKSHandler)
    threading.Thread(target=key_server.serve_forever, daemon=True).start()
    issuer = f"http://127.0.0.1:{key_server.server_port}"
    transport = f"http://127.0.0.1:{port}"
    # The production constructor requires an HTTPS resource identifier. Actual
    # transport is loopback HTTP in this disposable environment only.
    resource = "https://disposable.invalid/mcp"
    values = {
        "OWG_GATEWAY_DATABASE_URL": f"sqlite:///{state / 'gateway.db'}",
        "OWG_GATEWAY_ADMIN_TOKEN": secrets.token_urlsafe(32),
        "OWG_GATEWAY_ENROLLMENT_TOKEN": secrets.token_urlsafe(32),
        "OWG_PLUGIN_OAUTH_ISSUER": issuer,
        "OWG_PLUGIN_RESOURCE_URL": resource,
        "OWG_PLUGIN_OAUTH_JWKS_URL": issuer + "/jwks",
        "OWG_PLUGIN_OAUTH_ALGORITHMS": "RS256",
        "OWG_PLUGIN_TOKEN_AUDIENCE": "eval",
        "OWG_PLUGIN_REQUIRED_SCOPE": "work:read",
        "OWG_PLUGIN_PROFILE_KEY": secrets.token_urlsafe(32),
    }
    os.environ.update(values)
    db = GatewayDB(values["OWG_GATEWAY_DATABASE_URL"])
    settings = GatewaySettings.from_env()
    counts = seed(db, settings)
    from gateway.public_plugin_mcp import create_public_mcp
    server = create_public_mcp(db=db)
    inner = server.streamable_http_app(stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[transport]))

    @asynccontextmanager
    async def lifespan(app):
        async with inner.router.lifespan_context(inner):
            yield

    app = Starlette(routes=[Mount("/", inner)], lifespan=lifespan)
    tokens = {person: jwt.encode({
        "iss": issuer, "aud": "eval", "sub": f"eval-{person}",
        "scope": "work:read", "exp": int(time.time()) + 86400,
        "client_id": "disposable-eval",
    }, key, algorithm="RS256", headers={"kid": "disposable"})
        for person in counts}
    credential = state / "connection.json"
    credential.write_text(json.dumps({"url": transport + "/mcp", "tokens": tokens}))
    credential.chmod(0o600)
    (state / "seed.json").write_text(json.dumps({"inserted": counts,
        "structural_agent_sharing": "explicit synthetic opt-in, disabled after ingestion",
        "gateway_scope": "disposable loopback SQLite only"}, indent=2))
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
    finally:
        key_server.shutdown()
        key_server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    serve(args.state.resolve(), args.port)
