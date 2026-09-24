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


def main() -> None:
    # Explicit administrator/setup action. Printing is intentional here so a
    # local adapter can be configured without granting the broader API bearer.
    print(ensure_agent_ingest_token())


if __name__ == "__main__":
    main()
