from __future__ import annotations

"""Interactive company sign-in (OpenID Connect authorization code + PKCE).

Used for three things, all optional:

* administrators signing in to /admin (instead of password + authenticator);
* employees opening /me;
* employees confirming a personal invitation before their computer joins.

Configuration (all via environment):

* ``OWG_GATEWAY_SSO_ISSUER`` (defaults to ``OWG_GATEWAY_OIDC_ISSUER``)
* ``OWG_GATEWAY_SSO_CLIENT_ID`` and, for confidential clients,
  ``OWG_GATEWAY_SSO_CLIENT_SECRET``
* ``OWG_GATEWAY_PUBLIC_URL``: the https address people use to reach the
  Gateway; the redirect URI is ``<public url>/sso/callback``
* ``OWG_GATEWAY_SSO_ALLOWED_DOMAINS``: optional comma-separated email domains

The ID token is verified against the provider's published keys (issuer,
audience, expiry, nonce). An explicit ``email_verified: false`` is rejected.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

from shared.time_utils import normalize_timestamp
from .auth import token_hash
from .db import GatewayDB

STATE_MINUTES = 10
PURPOSES = ("admin", "me", "join")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_sso_states (
  state_hash TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  nonce TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
)
"""


class SSOError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SSOSettings:
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    public_url: str = ""
    allowed_domains: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id and self.public_url)

    @property
    def redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/sso/callback"

    @classmethod
    def from_env(cls) -> "SSOSettings":
        issuer = str(os.getenv("OWG_GATEWAY_SSO_ISSUER") or os.getenv("OWG_GATEWAY_OIDC_ISSUER") or "").strip().rstrip("/")
        public_url = str(os.getenv("OWG_GATEWAY_PUBLIC_URL") or "").strip().rstrip("/")
        domains = tuple(
            d.strip().lower().lstrip("@")
            for d in str(os.getenv("OWG_GATEWAY_SSO_ALLOWED_DOMAINS") or "").split(",") if d.strip()
        )
        value = cls(
            issuer=issuer,
            client_id=str(os.getenv("OWG_GATEWAY_SSO_CLIENT_ID") or "").strip(),
            client_secret=str(os.getenv("OWG_GATEWAY_SSO_CLIENT_SECRET") or "").strip(),
            public_url=public_url,
            allowed_domains=domains,
        )
        if value.client_id and public_url:
            parsed = urlparse(public_url)
            local = (parsed.hostname or "") in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not local:
                raise ValueError("OWG_GATEWAY_PUBLIC_URL must use https for SSO")
        return value


def init_sso_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            conn.execute(statement)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: datetime) -> str:
    return normalize_timestamp(value.isoformat())


@dataclass
class SSOClient:
    settings: SSOSettings
    db: GatewayDB
    http_factory: Callable[[], Any] = field(default=lambda: __import__("httpx").Client(timeout=10))
    _discovery: dict[str, Any] | None = None
    _discovery_at: float = 0.0
    _jwks_client: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- provider metadata ----------------------------------------------------------------

    def discovery(self) -> dict[str, Any]:
        with self._lock:
            if self._discovery and time.time() - self._discovery_at < 3600:
                return self._discovery
        url = self.settings.issuer + "/.well-known/openid-configuration"
        with self.http_factory() as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        if str(data.get("issuer") or "").rstrip("/") != self.settings.issuer:
            raise SSOError("the SSO provider's issuer does not match OWG_GATEWAY_SSO_ISSUER", status_code=502)
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not data.get(key):
                raise SSOError(f"the SSO provider does not publish {key}", status_code=502)
        with self._lock:
            self._discovery, self._discovery_at, self._jwks_client = data, time.time(), None
        return data

    def _signing_key(self, id_token: str) -> Any:
        import jwt

        header = jwt.get_unverified_header(id_token)
        with self.http_factory() as client:
            response = client.get(self.discovery()["jwks_uri"])
            response.raise_for_status()
            keys = response.json().get("keys") or []
        kid = header.get("kid")
        for key in keys:
            if not kid or key.get("kid") == kid:
                return jwt.PyJWK(key).key
        raise SSOError("the SSO provider's signing key was not found", status_code=502)

    # -- flow -------------------------------------------------------------------------------

    def start(self, purpose: str, context: dict[str, Any] | None = None) -> str:
        if not self.settings.enabled:
            raise SSOError("company sign-in is not configured on this Gateway", status_code=404)
        if purpose not in PURPOSES:
            raise SSOError("unknown sign-in purpose")
        meta = self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        now = _now()
        with self.db.connect() as conn:
            self.db._execute(
                conn,
                "INSERT INTO gateway_sso_states(state_hash, purpose, nonce, code_verifier, context_json, created_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (token_hash(state), purpose, nonce, verifier, json.dumps(context or {}), _ts(now), _ts(now + timedelta(minutes=STATE_MINUTES))),
            )
        query = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": self.settings.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return str(meta["authorization_endpoint"]) + ("&" if "?" in str(meta["authorization_endpoint"]) else "?") + urlencode(query)

    def finish(self, *, code: str, state: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Return (purpose, context, identity) for a completed sign-in."""
        import jwt

        if not code or not state:
            raise SSOError("the sign-in response is incomplete; start again")
        now = _now()
        with self.db.connect() as conn:
            cur = self.db._execute(conn, "SELECT * FROM gateway_sso_states WHERE state_hash = ?", (token_hash(state),))
            row = cur.fetchone()
            columns = [d[0] for d in cur.description] if cur.description else None
            data = self.db._row(row, columns)
            if not data or data.get("used_at") or data["expires_at"] < _ts(now):
                raise SSOError("this sign-in attempt expired or was already used; start again", status_code=400)
            self.db._execute(conn, "UPDATE gateway_sso_states SET used_at = ? WHERE state_hash = ?", (_ts(now), data["state_hash"]))
        meta = self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.client_id,
            "code_verifier": data["code_verifier"],
        }
        if self.settings.client_secret:
            form["client_secret"] = self.settings.client_secret
        with self.http_factory() as client:
            response = client.post(str(meta["token_endpoint"]), data=form, headers={"Accept": "application/json"})
        if response.status_code >= 400:
            raise SSOError("the SSO provider rejected the sign-in; start again", status_code=502)
        id_token = str((response.json() or {}).get("id_token") or "")
        if not id_token:
            raise SSOError("the SSO provider did not return an ID token", status_code=502)
        try:
            claims = jwt.decode(
                id_token,
                self._signing_key(id_token),
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"],
                audience=self.settings.client_id,
                issuer=str(meta["issuer"]),
                leeway=30,
                options={"require": ["exp", "iat", "sub"]},
            )
        except SSOError:
            raise
        except Exception as exc:
            raise SSOError("the sign-in token could not be verified", status_code=401) from exc
        if claims.get("nonce") != data["nonce"]:
            raise SSOError("the sign-in token does not belong to this attempt", status_code=401)
        if claims.get("email_verified") is False:
            raise SSOError("your company account's email is not verified", status_code=403)
        email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
        if "@" not in email:
            raise SSOError("your company account did not provide an email address", status_code=403)
        domain = email.rsplit("@", 1)[1]
        if self.settings.allowed_domains and domain not in self.settings.allowed_domains:
            raise SSOError(f"accounts from {domain} cannot sign in to this Gateway", status_code=403)
        identity = {
            "subject": str(claims["sub"]),
            "email": email,
            "name": str(claims.get("name") or "")[:120],
        }
        return data["purpose"], json.loads(data.get("context_json") or "{}"), identity
