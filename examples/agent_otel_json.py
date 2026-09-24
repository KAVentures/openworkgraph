from __future__ import annotations

"""Minimal OTLP/HTTP JSON example for OpenWorkGraph agent observation.

Start OpenWorkGraph, obtain the dedicated write-only telemetry token with:

    python -m server.agent_auth

Set that value as OWG_AGENT_INGEST_TOKEN, then run this file. Real agent
runtimes should emit equivalent OpenTelemetry GenAI spans rather than copying
this fixture.
"""

import json
import os
import urllib.request
from time import time_ns

TOKEN = os.environ.get("OWG_AGENT_INGEST_TOKEN", "")
URL = os.environ.get(
    "OWG_AGENT_OTEL_URL",
    "http://127.0.0.1:8787/agent-ingest/v1/otel",
)
if not TOKEN:
    raise SystemExit("OWG_AGENT_INGEST_TOKEN is required; run: python -m server.agent_auth")

start = time_ns()
end = start + 250_000_000
payload = {
    "openworkgraph": {"agent_name": "Example OTel Agent"},
    "resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "example-agent"}}]},
        "scopeSpans": [{
            "scope": {"name": "example.agent.instrumentation"},
            "spans": [{
                "traceId": "example-trace",
                "spanId": "example-agent-run",
                "name": "invoke_agent example",
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                    {"key": "gen_ai.agent.name", "value": {"stringValue": "Example OTel Agent"}},
                    {"key": "gen_ai.provider.name", "value": {"stringValue": "example"}},
                ],
                "status": {"code": "STATUS_CODE_OK"},
            }],
        }],
    }],
}

request = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.read().decode("utf-8"))
