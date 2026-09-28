from __future__ import annotations

"""Is it working? Turn delivery counts and configuration into specific next steps.

``agent_telemetry_diagnostics`` counts what arrived on each channel and
``agent_capture_runtime.configuration_state`` says what is configured. Neither
says what a person should *do*. These checks do, and only from those facts:
no guessing about apps OpenWorkGraph cannot see.

Each check: ``{"id", "client", "status": "warn"|"info", "message", "action"}``.
Only problems are listed; an empty list means nothing needs attention.
"""

from datetime import datetime, timezone
from typing import Any

RECENT_SECONDS = 30 * 60


def _age(value: Any, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    return (now - parsed).total_seconds() if parsed.tzinfo else None


def _check(check_id: str, client: str, status: str, message: str, action: str) -> dict[str, str]:
    return {"id": check_id, "client": client, "status": status, "message": message, "action": action}


def checks(snapshot: dict[str, Any], configuration: dict[str, Any], *, extras: dict[str, Any] | None = None,
           now: datetime | None = None) -> list[dict[str, str]]:
    current = now or datetime.now(timezone.utc)
    channels = snapshot.get("channels") or {}
    extras = extras or {}
    out: list[dict[str, str]] = []

    def channel(name: str) -> dict[str, Any]:
        value = channels.get(name)
        return value if isinstance(value, dict) else {}

    claude = configuration.get("claude_code") if isinstance(configuration.get("claude_code"), dict) else {}
    hooks, otel = channel("claude_code_hooks"), channel("claude_code_otel_logs")
    hooks_recent = (_age(hooks.get("last_received_at"), current) or 1e12) < RECENT_SECONDS
    if claude.get("hook_events"):
        if claude.get("missing_hook_events"):
            out.append(_check(
                "claude_hooks_outdated", "claude_code", "warn",
                "Claude Code hooks are missing " + ", ".join(claude["missing_hook_events"]) + ", so turns are not split correctly.",
                "Restart OpenWorkGraph (it updates its own hooks), then start a new Claude Code session."))
        if not claude.get("telemetry_enabled") or not claude.get("otel_logs_endpoint_is_openworkgraph"):
            out.append(_check(
                "claude_telemetry_off", "claude_code", "warn",
                "Claude Code telemetry is not set up, so model calls and token usage are not observed.",
                "In Connect, switch Observe for Claude Code off and on again."))
        elif hooks_recent and not otel.get("requests"):
            out.append(_check(
                "claude_sessions_predate_telemetry", "claude_code", "warn",
                "Hooks are arriving but no Claude Code telemetry: the open sessions started before telemetry was turned on "
                "(it applies to new sessions only), so their model calls and tokens are missing.",
                "Start a new Claude Code session, or restart the open ones."))
    for name, client in (("claude_code_hooks", "claude_code"), ("claude_code_otel_logs", "claude_code"), ("codex_otel", "codex"),
                         ("cursor_hooks", "cursor"), ("copilot_otel", "vscode"), ("gemini_otel", "gemini_cli")):
        rejected = channel(name).get("rejected") or {}
        if rejected.get("auth"):
            out.append(_check(
                "token_rejected", client, "warn",
                f"{channel(name).get('label') or name} is being sent with a token OpenWorkGraph does not accept "
                "(often after reinstalling or moving the data folder).",
                "In Connect, switch Observe for this app off and on again to write the current token."))
        if rejected.get("protobuf_unsupported"):
            out.append(_check(
                "otlp_protobuf", client, "warn",
                "Telemetry arrives as OTLP protobuf; OpenWorkGraph accepts OTLP/HTTP JSON only.",
                "Set the app's OTLP protocol to http/json (Observe sets this for you)."))
    codex = configuration.get("codex") if isinstance(configuration.get("codex"), dict) else {}
    if codex.get("configured") and not channel("codex_otel").get("requests"):
        out.append(_check(
            "codex_nothing_yet", "codex", "info",
            "No Codex telemetry since OpenWorkGraph started. Codex reads its settings when a session starts.",
            "If you have used Codex since, start a new Codex session."))
    spool = configuration.get("agent_spool") if isinstance(configuration.get("agent_spool"), dict) else {}
    if int(spool.get("pending_files") or 0) and not spool.get("lease_active"):
        out.append(_check(
            "spool_waiting", "claude_code", "info",
            "Agent events are waiting for delivery, but recording is paused or stopped, so they are not being stored.",
            "Resume recording to deliver them; they expire after 24 hours."))
    outcome = extras.get("outcome_tracking") if isinstance(extras.get("outcome_tracking"), dict) else {}
    if outcome.get("enabled") and not outcome.get("gh_found"):
        out.append(_check("gh_missing", "outcomes", "warn", "Outcome tracking is on, but the GitHub CLI (gh) was not found.",
                          "Install gh (https://cli.github.com) or turn outcome tracking off in History."))
    elif outcome.get("enabled") and outcome.get("gh_logged_in") is False:
        out.append(_check("gh_logged_out", "outcomes", "warn", "Outcome tracking is on, but gh is not logged in.",
                          "Run: gh auth login"))
    brief = extras.get("agent_brief") if isinstance(extras.get("agent_brief"), dict) else {}
    claude_brief = (brief.get("frameworks") or {}).get("claude-code") or {}
    if claude_brief.get("enabled") and not claude_brief.get("hook_installed"):
        out.append(_check("brief_hook_missing", "claude_code", "warn",
                          "Session briefs are on, but their Claude Code hook is not in the settings file.",
                          "In History, save the Claude Code brief switch again."))
    return out


__all__ = ["checks"]
