from __future__ import annotations

"""Dashboard control plane for opt-in native agent observation.

This module does not change the agent-ingest contract and does not edit third-party
configuration files. It exposes authenticated, local setup material that the human
owner can review/copy, then the dashboard treats an integration as active only when
structural telemetry is actually observed.
"""

import json
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from adapters.claude_code_hook import settings_fragment
from adapters.codex_config import config_snippet
from server.agent_auth import ensure_agent_ingest_token
from server.main import ROOT
from server.secure_app import app


DASHBOARD_SCRIPT = ROOT / "dashboard" / "agent_control_plane.js"
_SCRIPT_MARKER = '<script src="/agent-control-plane.js"></script>'


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def agent_setup_payload(request: Request) -> dict[str, Any]:
    """Return reviewable setup material for the local dashboard owner.

    The only credential included is the dedicated write-only agent-ingest token.
    It cannot read workflow history, summaries, exports, agent reports, or MCP
    context. The route itself remains under the normal authenticated /v1 boundary.
    """
    base_url = _base_url(request)
    token = ensure_agent_ingest_token()
    otel_endpoint = f"{base_url}/agent-ingest/v1/otel"
    event_endpoint = f"{base_url}/agent-ingest/v1/events"

    posix_otel = "\n".join(
        [
            f'export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="{otel_endpoint}"',
            'export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"',
            f'export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer {token}"',
        ]
    )
    powershell_otel = "\n".join(
        [
            f'$env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="{otel_endpoint}"',
            '$env:OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"',
            f'$env:OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer {token}"',
        ]
    )

    return {
        "observer_api": base_url,
        "credential_scope": "agent_ingest_write_only",
        "connection_status_basis": "telemetry_observed_not_configuration_presence",
        "privacy": {
            "prompts_collected": False,
            "model_responses_collected": False,
            "chain_of_thought_collected": False,
            "tool_arguments_collected": False,
            "tool_results_collected": False,
            "transcript_paths_collected": False,
            "arbitrary_otel_attributes_collected": False,
        },
        "integrations": {
            "claude_code": {
                "label": "Claude Code",
                "method": "native_lifecycle_hooks",
                "command": "python -m adapters.claude_code_hook --print-settings",
                "settings": settings_fragment(),
                "events": [
                    "SessionStart",
                    "SessionEnd",
                    "PostToolUse",
                    "PostToolUseFailure",
                    "PermissionRequest",
                    "PermissionDenied",
                    "SubagentStart",
                    "SubagentStop",
                    "StopFailure",
                ],
                "instructions": "Merge the generated hooks object into ~/.claude/settings.json or an intentional project-scoped .claude/settings.json. OpenWorkGraph does not overwrite Claude settings.",
                "failure_mode": "fail_open_async",
            },
            "codex": {
                "label": "Codex",
                "method": "otel_http_json_trace_export",
                "command": "python -m adapters.codex_config --with-token",
                "config": config_snippet(token=token, base_url=base_url),
                "endpoint": f"{base_url}/agent-ingest/v1/codex-otel",
                "instructions": "Merge these keys into the existing [otel] section in ~/.codex/config.toml. Do not create a second [otel] table.",
                "logs_enabled_by_owg": False,
            },
            "openai_agents": {
                "label": "OpenAI Agents SDK",
                "method": "additional_tracing_processor",
                "python": "from adapters.openai_agents import install_openai_agents_processor\n\ninstall_openai_agents_processor()",
                "instructions": "Register OpenWorkGraph as an additional tracing processor in the agent application. Existing SDK tracing remains in place.",
            },
            "otel": {
                "label": "Generic OpenTelemetry",
                "method": "otel_http_json_trace_export",
                "endpoint": otel_endpoint,
                "posix": posix_otel,
                "powershell": powershell_otel,
                "instructions": "Use the signal-specific traces endpoint exactly as shown. OpenWorkGraph currently accepts OTLP/HTTP JSON here, not protobuf or gRPC.",
            },
            "custom": {
                "label": "Custom structural agent",
                "method": "openworkgraph_agent_events",
                "endpoint": event_endpoint,
                "authorization": f"Bearer {token}",
                "instructions": "POST the canonical privacy-safe structural event envelope to this write-only endpoint. Do not send prompts, response text, tool arguments/results, or reasoning content.",
            },
        },
        "read_access_granted_to_agent": False,
        "configuration_files_modified": False,
    }


def get_agent_setup(request: Request) -> JSONResponse:
    return JSONResponse(agent_setup_payload(request), headers={"Cache-Control": "no-store"})


def agent_control_plane_script() -> Response:
    return Response(DASHBOARD_SCRIPT.read_text(encoding="utf-8"), media_type="application/javascript")


async def _inject_agent_control_plane(request: Request, call_next):
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
    if _SCRIPT_MARKER not in text:
        text = text.replace("</body>", _SCRIPT_MARKER + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


def _install() -> None:
    app.add_api_route("/v1/agent-setup", get_agent_setup, methods=["GET"])
    app.add_api_route("/agent-control-plane.js", agent_control_plane_script, methods=["GET"])
    app.middleware("http")(_inject_agent_control_plane)


if not getattr(app.state, "owg_agent_dashboard_control_plane_installed", False):
    _install()
    app.state.owg_agent_dashboard_control_plane_installed = True


__all__ = ["agent_setup_payload"]
