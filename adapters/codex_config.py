from __future__ import annotations

"""Print a Codex OTLP trace-exporter snippet for OpenWorkGraph.

This helper never edits ~/.codex/config.toml. With --with-token it intentionally
prints the least-privilege write token so an administrator can paste a complete
local-only configuration.
"""

import argparse
import json

from adapters._agent_client import _base_url
from server.agent_auth import ensure_agent_ingest_token


def _toml_string(value: str) -> str:
    # JSON string escaping is compatible with TOML basic-string escaping for the
    # characters that can occur in our generated URL/token values.
    return json.dumps(value)


def config_snippet(*, token: str, base_url: str | None = None) -> str:
    endpoint = (base_url or _base_url()).rstrip("/") + "/agent-ingest/v1/codex-otel"
    authorization = f"Bearer {token}"
    return "\n".join([
        "[otel]",
        "log_user_prompt = false",
        "log_agent_responses = false",
        "log_guardian_assessments = false",
        (
            "trace_exporter = { otlp-http = { endpoint = "
            + _toml_string(endpoint)
            + ", headers = { Authorization = "
            + _toml_string(authorization)
            + " }, protocol = \"json\" } }"
        ),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Codex → OpenWorkGraph OTLP config")
    parser.add_argument(
        "--with-token",
        action="store_true",
        help="include the local write-only agent token in the printed snippet",
    )
    args = parser.parse_args(argv)
    token = ensure_agent_ingest_token() if args.with_token else "PASTE_WRITE_ONLY_AGENT_TOKEN_HERE"
    print(config_snippet(token=token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
