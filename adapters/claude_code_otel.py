from __future__ import annotations

"""Build the Claude Code settings env needed for structural OWG telemetry only."""


def env_settings(*, token: str, base_url: str) -> dict[str, str]:
    base = str(base_url or "").rstrip("/")
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"{base}/agent-ingest/v1/claude-otel",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS": f"Authorization=Bearer {token}",
        # Claude leaves these content surfaces off by default. OWG writes the
        # explicit zeroes as defense in depth; the ingest adapter independently
        # allowlists structural attributes and would discard content anyway.
        "OTEL_LOG_USER_PROMPTS": "0",
        "OTEL_LOG_ASSISTANT_RESPONSES": "0",
        "OTEL_LOG_TOOL_DETAILS": "0",
        "OTEL_LOG_TOOL_CONTENT": "0",
        "OTEL_LOG_RAW_API_BODIES": "0",
    }


def settings_fragment(*, token: str, base_url: str) -> dict[str, dict[str, str]]:
    return {"env": env_settings(token=token, base_url=base_url)}


__all__ = ["env_settings", "settings_fragment"]
