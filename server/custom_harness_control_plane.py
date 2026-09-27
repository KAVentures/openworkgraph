from __future__ import annotations

"""Human-reviewed setup material for arbitrary agent harnesses.

Telemetry credentials are write-only. MCP context is a separate connection and
remains subject to the normal per-run AI-access and saved-history controls.
"""

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from server.agent_auth import ensure_agent_ingest_token
from server.main import ROOT
from server.secure_app import app

SCRIPT = ROOT / "dashboard" / "custom_harness_setup.js"
SCRIPT_MARKER = '<script src="/custom-harness-setup.js"></script>'
PYTHON_SDK = ROOT / "sdk" / "python" / "openworkgraph_agent.py"
TYPESCRIPT_SDK = ROOT / "sdk" / "typescript" / "index.mjs"
TYPESCRIPT_TYPES = ROOT / "sdk" / "typescript" / "index.d.ts"
LAUNCHER = ROOT / "mcp_server" / "launcher.py"
VERSION_FILE = ROOT / "VERSION"


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "openworkgraph": {
                "command": sys.executable,
                "args": [str(LAUNCHER), "--client", "custom-harness"],
            }
        }
    }


def setup_payload(request: Request) -> dict[str, Any]:
    base = _base_url(request)
    token = ensure_agent_ingest_token()
    version = _version()
    endpoint = f"{base}/agent-ingest/v1/events"
    python_install = (
        'pip install "openworkgraph-agent @ '
        f'git+https://github.com/KAVentures/openworkgraph.git@v{version}#subdirectory=sdk/python"'
    )
    python_env = "\n".join([
        f'export OWG_AGENT_INGEST_URL="{endpoint}"',
        f'export OWG_AGENT_INGEST_TOKEN="{token}"',
    ])
    python_example = '''from openworkgraph_agent import AgentObserver

owg = AgentObserver("my-agent", framework="my-harness")
try:
    with owg.run(workflow_id="optional-workflow-id") as run:
        with run.model(model="model-id"):
            call_model()  # return value/content is never sent to OWG
        with run.tool("repository_search", category="search"):
            search_repository()
finally:
    owg.shutdown()
'''
    typescript_download = "\n".join([
        f"curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/v{version}/sdk/typescript/index.mjs -o openworkgraph-agent.mjs",
        f"curl -fsSL https://raw.githubusercontent.com/KAVentures/openworkgraph/v{version}/sdk/typescript/index.d.ts -o openworkgraph-agent.d.ts",
    ])
    typescript_env = "\n".join([
        f'export OWG_AGENT_INGEST_URL="{endpoint}"',
        f'export OWG_AGENT_INGEST_TOKEN="{token}"',
    ])
    typescript_example = '''import {AgentObserver} from "./openworkgraph-agent.mjs";

const owg = new AgentObserver("my-agent", {framework: "my-harness"});
try {
  await owg.withRun(async run => {
    await run.model({model: "model-id"}, async () => callModel());
    await run.tool("repository_search", {category: "search"}, async () => searchRepository());
  }, {workflowId: "optional-workflow-id"});
} finally {
  await owg.shutdown();
}
'''
    raw_example = {
        "events": [{
            "observed_at": "2026-01-01T12:00:00Z",
            "agent_name": "my-agent",
            "framework": "my-harness",
            "operation": "tool_call",
            "status": "success",
            "observation_level": "instrumented_tools",
            "run_id": "run-opaque-id",
            "tool_name": "repository_search",
            "tool_category": "search",
            "duration_seconds": 0.42,
        }]
    }
    return {
        "version": version,
        "write": {
            "credential_scope": "agent_ingest_write_only",
            "endpoint": endpoint,
            "authorization": f"Bearer {token}",
            "python": {
                "install": python_install,
                "environment": python_env,
                "example": python_example,
                "bundled_source": str(PYTHON_SDK),
                "dependency_free_runtime": True,
            },
            "typescript": {
                "download": typescript_download,
                "environment": typescript_env,
                "example": typescript_example,
                "bundled_source": str(TYPESCRIPT_SDK),
                "types_source": str(TYPESCRIPT_TYPES),
                "dependency_free_runtime": True,
                "minimum_node": 18,
            },
            "raw_http": {
                "endpoint": endpoint,
                "authorization": f"Bearer {token}",
                "example": json.dumps(raw_example, indent=2),
            },
            "otel": {
                "endpoint": f"{base}/agent-ingest/v1/otel",
                "protocol": "OTLP/HTTP JSON",
            },
        },
        "read": {
            "method": "MCP stdio",
            "config": _mcp_config(),
            "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(LAUNCHER))} --client custom-harness",
            "master_ai_access_required": True,
            "saved_history_lease_required_for_historical_reads": True,
            "note": "Context access is separate from telemetry. Giving a harness the write-only telemetry token never grants it MCP/history read access.",
        },
        "privacy": {
            "prompt_content": False,
            "model_response_content": False,
            "tool_arguments": False,
            "tool_results": False,
            "reasoning": False,
            "exception_text": False,
            "returned_values": False,
        },
    }


def get_setup(request: Request) -> JSONResponse:
    return JSONResponse(setup_payload(request), headers={"Cache-Control": "no-store"})


def script() -> Response:
    return Response(SCRIPT.read_text(encoding="utf-8"), media_type="application/javascript")


async def _inject(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response
    if SCRIPT_MARKER not in text:
        text = text.replace("</body>", SCRIPT_MARKER + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


def _install() -> None:
    app.add_api_route("/v1/custom-harness-setup", get_setup, methods=["GET"])
    app.add_api_route("/custom-harness-setup.js", script, methods=["GET"])
    app.middleware("http")(_inject)


if not getattr(app.state, "owg_custom_harness_control_plane_installed", False):
    _install()
    app.state.owg_custom_harness_control_plane_installed = True


__all__ = ["setup_payload"]
