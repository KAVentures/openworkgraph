from __future__ import annotations

"""Dashboard control plane for opt-in native agent observation.

This module does not change the agent-ingest contract. It exposes authenticated,
local setup material that the human owner can review/copy, plus explicit one-click
Connect/Disconnect routes for agents whose configuration lives in a well-known
per-user file (Claude Code, Codex). Those routes only run when the owner clicks
them; see server.agent_config_writer for the safety rules. The dashboard still
treats an integration as active only when structural telemetry is observed.
"""

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from adapters.claude_code_hook import settings_fragment
from adapters.codex_config import config_snippet
from server import agent_config_writer as writer
from server.agent_auth import ensure_agent_ingest_token
from server.main import ROOT
from server.secure_app import app


DASHBOARD_SCRIPT = ROOT / "dashboard" / "agent_control_plane.js"
CONNECTIONS_SCRIPT = ROOT / "dashboard" / "connections.js"
_SCRIPT_MARKER = '<script src="/agent-control-plane.js"></script>'
_CONNECTIONS_MARKER = '<script src="/connections.js"></script>'


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
                "command": f"cd {shlex.quote(str(ROOT))} && {shlex.quote(sys.executable)} -m adapters.claude_code_hook --print-settings",
                "one_click": True,
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
                "instructions": "Click Connect to add these hooks to ~/.claude/settings.json (other settings are preserved and a backup is written), or merge the hooks object manually into ~/.claude/settings.json or an intentional project-scoped .claude/settings.json.",
                "failure_mode": "fail_open_async",
            },
            "codex": {
                "label": "Codex",
                "method": "otel_http_json_trace_export",
                "command": f"cd {shlex.quote(str(ROOT))} && {shlex.quote(sys.executable)} -m adapters.codex_config --with-token",
                "one_click": True,
                "config": config_snippet(token=token, base_url=base_url),
                "endpoint": f"{base_url}/agent-ingest/v1/codex-otel",
                "instructions": "Click Connect to add a managed [otel] block to ~/.codex/config.toml (refused if you already have your own [otel] settings), or merge these keys manually into the existing [otel] section. Do not create a second [otel] table.",
                "logs_enabled_by_owg": False,
            },
            "openai_agents": {
                "label": "OpenAI Agents SDK",
                "method": "additional_tracing_processor",
                "python": "from adapters.openai_agents import install_openai_agents_processor\n\ninstall_openai_agents_processor()",
                "instructions": "Register OpenWorkGraph as an additional tracing processor in the agent application. Existing SDK tracing remains in place.",
                "one_click": False,
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


_ONE_CLICK = {
    "claude_code": {
        "status": writer.claude_status,
        "connect": lambda request: writer.claude_connect(settings_fragment),
        "disconnect": writer.claude_disconnect,
    },
    "codex": {
        "status": writer.codex_status,
        "connect": lambda request: writer.codex_connect(
            config_snippet(token=ensure_agent_ingest_token(), base_url=_base_url(request))
        ),
        "disconnect": writer.codex_disconnect,
    },
}


def get_agent_config_status() -> JSONResponse:
    status = {kind: ops["status"]() for kind, ops in _ONE_CLICK.items()}
    return JSONResponse({"integrations": status}, headers={"Cache-Control": "no-store"})


async def change_agent_config(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid request"}, status_code=400)
    kind = str((payload or {}).get("agent") or "")
    action = str((payload or {}).get("action") or "")
    ops = _ONE_CLICK.get(kind)
    if ops is None or action not in {"connect", "disconnect"}:
        return JSONResponse({"detail": "unsupported agent or action"}, status_code=400)
    try:
        result = ops["connect"](request) if action == "connect" else ops["disconnect"]()
    except writer.ConfigConflict as exc:
        return JSONResponse({"detail": str(exc), "manual_setup_required": True}, status_code=409)
    except OSError:
        return JSONResponse(
            {"detail": "Could not write the agent configuration file.", "manual_setup_required": True},
            status_code=500,
        )
    return JSONResponse({"agent": kind, **result}, headers={"Cache-Control": "no-store"})


def get_connections() -> JSONResponse:
    from server.connections import list_connections
    return JSONResponse(list_connections(), headers={"Cache-Control": "no-store"})


async def change_connection(request: Request) -> JSONResponse:
    from server import connections
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid request"}, status_code=400)
    payload = payload if isinstance(payload, dict) else {}
    kind = str(payload.get("kind") or "both")
    kinds = connections.KINDS if kind == "both" else (kind,)
    try:
        result = connections.change(str(payload.get("client") or ""), str(payload.get("action") or ""), kinds)
    except KeyError:
        return JSONResponse({"detail": "unknown client"}, status_code=400)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except writer.ConfigConflict as exc:
        return JSONResponse({"detail": str(exc), "manual_setup_required": True}, status_code=409)
    except OSError:
        return JSONResponse(
            {"detail": "Could not write the client configuration file.", "manual_setup_required": True},
            status_code=500,
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def connections_script() -> Response:
    return Response(CONNECTIONS_SCRIPT.read_text(encoding="utf-8"), media_type="application/javascript")


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
    if _CONNECTIONS_MARKER not in text:
        text = text.replace("</body>", _CONNECTIONS_MARKER + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


def _install() -> None:
    app.add_api_route("/v1/agent-setup", get_agent_setup, methods=["GET"])
    app.add_api_route("/v1/agent-config", get_agent_config_status, methods=["GET"])
    app.add_api_route("/v1/agent-config", change_agent_config, methods=["POST"])
    app.add_api_route("/agent-control-plane.js", agent_control_plane_script, methods=["GET"])
    app.add_api_route("/v1/connections", get_connections, methods=["GET"])
    app.add_api_route("/v1/connections", change_connection, methods=["POST"])
    app.add_api_route("/connections.js", connections_script, methods=["GET"])
    app.middleware("http")(_inject_agent_control_plane)


if not getattr(app.state, "owg_agent_dashboard_control_plane_installed", False):
    _install()
    app.state.owg_agent_dashboard_control_plane_installed = True


__all__ = ["agent_setup_payload"]
