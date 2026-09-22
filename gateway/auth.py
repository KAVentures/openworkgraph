from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Iterable

ALLOWED_INTEGRATION_SCOPES = {
    "evidence:read",
    "context:read",
    "transfers:read",
}
DEVICE_SCOPES = {"evidence:write", "policy:read"}


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def env_token_matches(supplied: str, expected: str) -> bool:
    return bool(supplied and expected) and hmac.compare_digest(str(supplied), str(expected))


def issue_token(prefix: str = "owg") -> str:
    return f"{prefix}_{secrets.token_urlsafe(36)}"


@dataclass(frozen=True)
class Principal:
    token_id: str
    token_type: str
    organization_id: str
    actor_id: str
    device_id: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise PermissionError(f"missing required scope: {scope}")


def normalize_scopes(values: Iterable[str]) -> set[str]:
    return {str(x).strip() for x in values if str(x).strip()}
