from __future__ import annotations

"""Write-only local credential for agent telemetry adapters."""

import hmac
from pathlib import Path

from .local_auth import _read_or_create_secret


def ensure_agent_ingest_token(*, directory: Path | None = None) -> str:
    """Return the stable installation-local token used only for agent ingestion."""
    return _read_or_create_secret(".agent_ingest_token", directory=directory)


def agent_bearer_matches(header: str | None) -> bool:
    raw = str(header or "")
    if not raw.lower().startswith("bearer "):
        return False
    supplied = raw[7:].strip()
    target = ensure_agent_ingest_token()
    return bool(supplied) and hmac.compare_digest(supplied, target)


def ensure_agent_otlp_path_token(*, directory: Path | None = None) -> str:
    """Write-only token carried in the OTLP endpoint *path*.

    VS Code Copilot and Gemini CLI can be pointed at an OTLP endpoint from their
    settings files but cannot send an Authorization header from there. This
    separate secret authorizes only the OTLP agent-ingest routes, so it can sit in
    those settings files (as the bearer token already sits in Claude Code's) and
    be rotated independently. Access logs redact it (see server.enterprise_runner).
    """
    return _read_or_create_secret(".agent_otlp_path_token", directory=directory)


def agent_otlp_path_token_matches(supplied: str | None) -> bool:
    supplied = str(supplied or "")
    return bool(supplied) and hmac.compare_digest(supplied, ensure_agent_otlp_path_token())


def ensure_agent_brief_token(*, directory: Path | None = None) -> str:
    """Token the brief hook uses to fetch a session-start brief, and nothing else.

    Separate from the write-only ingest token (which must never read) and from the
    full API bearer. Briefs are content-free and served only while the person has
    turned them on for that agent.
    """
    return _read_or_create_secret(".agent_brief_token", directory=directory)


def agent_brief_bearer_matches(header: str | None) -> bool:
    raw = str(header or "")
    if not raw.lower().startswith("bearer "):
        return False
    supplied = raw[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, ensure_agent_brief_token())


def main() -> None:
    # Explicit administrator/setup action. Printing is intentional here so a
    # local adapter can be configured without granting the broader API bearer.
    print(ensure_agent_ingest_token())


if __name__ == "__main__":
    main()
