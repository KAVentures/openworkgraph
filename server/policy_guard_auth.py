from __future__ import annotations

"""Read-only local credential scoped to structural policy action advisories."""

import hmac
from pathlib import Path

from .local_auth import _read_or_create_secret


def ensure_policy_guard_token(*, directory: Path | None = None) -> str:
    """Return the installation-local token used only for policy guard reads."""
    return _read_or_create_secret(".policy_guard_token", directory=directory)


def policy_guard_bearer_matches(header: str | None) -> bool:
    raw = str(header or "")
    if not raw.lower().startswith("bearer "):
        return False
    supplied = raw[7:].strip()
    target = ensure_policy_guard_token()
    return bool(supplied) and hmac.compare_digest(supplied, target)


def main() -> None:
    # Explicit local setup action for instrumented runtimes. This capability is
    # intentionally narrower than the API/dashboard bearer and separate from the
    # write-only agent-ingest token.
    print(ensure_policy_guard_token())


if __name__ == "__main__":
    main()
