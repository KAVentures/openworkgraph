"""OAuth 2.0 connect, refresh and revoke for business-system connectors (#247).

The user registers OWG as an app in their own tenant (their client ID, and a
client secret only where the provider requires one), then clicks Connect. OWG
runs the authorization-code flow with PKCE (S256) and a single-use ``state``,
receiving the code on this computer's local callback
(``http://127.0.0.1:<port>/v1/connector-oauth/callback``).

Tokens never leave this computer and are never returned by any API or shown to
AI. Refresh tokens are stored AES-256-GCM-encrypted in the auth folder, keyed by
this installation's local secret. Access tokens live in memory only. Rotating
refresh tokens are saved before the new access token is used, so a crash cannot
lose the only valid grant. Disconnect calls the provider's revocation endpoint
when it has one, then forgets everything locally.

Connecting grants exactly the scopes the user approves at the provider. It does
not enable any OWG read, sync or schedule by itself: each connector still asks
for its own explicit consent before reading.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx

from .local_auth import _read_or_create_secret, auth_dir

FORMAT = "openworkgraph.connector-oauth.v1"
_FILE = ".connector_oauth.bin"
_LOCK = threading.RLock()
_PENDING_TTL = 600
_MAX_CONNECTIONS = 20
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_TENANT = re.compile(r"^(?:common|organizations|consumers|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[a-z0-9.-]{3,100})$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._~:@/+=-]{4,256}$")
_SCOPE = re.compile(r"^[A-Za-z0-9._:/+-]{1,200}$")
_ID = re.compile(r"^conn_[0-9a-f]{16}$")


class OAuthError(ValueError):
    """Safe error; never echoes tokens, codes or provider response bodies."""


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize: str
    token: str
    revoke: str | None
    host_kind: str  # "fixed", "instance" (customer host) or "tenant" (directory id)
    default_scopes: tuple[str, ...]
    allowed_suffixes: tuple[str, ...] = ()
    extra_authorize: tuple[tuple[str, str], ...] = ()


PROVIDERS: dict[str, Provider] = {
    "salesforce": Provider(
        "salesforce", "Salesforce", "https://{host}/services/oauth2/authorize",
        "https://{host}/services/oauth2/token", "https://{host}/services/oauth2/revoke",
        "instance", ("api", "refresh_token"),
        allowed_suffixes=(".salesforce.com", ".my.salesforce.com", ".force.com")),
    "servicenow": Provider(
        "servicenow", "ServiceNow", "https://{host}/oauth_auth.do", "https://{host}/oauth_token.do",
        "https://{host}/oauth_revoke_token.do", "instance", ("useraccount",),
        allowed_suffixes=(".service-now.com", ".servicenowservices.com")),
    "microsoft": Provider(
        "microsoft", "Microsoft 365", "https://login.microsoftonline.com/{host}/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/{host}/oauth2/v2.0/token", None, "tenant",
        ("offline_access", "Mail.Read")),
    "atlassian": Provider(
        "atlassian", "Atlassian (Jira)", "https://auth.atlassian.com/authorize",
        "https://auth.atlassian.com/oauth/token", None, "fixed",
        ("read:jira-work", "offline_access"),
        extra_authorize=(("audience", "api.atlassian.com"), ("prompt", "consent"))),
    "zendesk": Provider(
        "zendesk", "Zendesk", "https://{host}/oauth/authorizations/new", "https://{host}/oauth/tokens",
        "https://{host}/api/v2/oauth/tokens/current.json", "instance", ("read",),
        allowed_suffixes=(".zendesk.com",)),
}

_pending: dict[str, dict[str, Any]] = {}
_access: dict[str, tuple[str, float]] = {}
_transport: httpx.BaseTransport | None = None  # tests inject a mock transport


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, transport=_transport, follow_redirects=False)


def _key() -> bytes:
    return hashlib.sha256(("owg-connector-oauth-v1:" + _read_or_create_secret(".connector_oauth_key")).encode()).digest()


def _path() -> Path:
    return auth_dir() / _FILE


def _load() -> dict[str, Any]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        blob = _path().read_bytes()
    except FileNotFoundError:
        return {"version": 1, "connections": {}}
    try:
        data = json.loads(AESGCM(_key()).decrypt(blob[:12], blob[12:], FORMAT.encode()))
    except (InvalidTag, ValueError):
        raise OAuthError("stored connector sign-ins could not be decrypted; reconnect them") from None
    if not isinstance(data, dict) or not isinstance(data.get("connections"), dict):
        return {"version": 1, "connections": {}}
    return data


def _save(data: dict[str, Any]) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    blob = nonce + AESGCM(_key()).encrypt(nonce, json.dumps(data).encode(), FORMAT.encode())
    fd, temp = tempfile.mkstemp(prefix=".owg-oauth-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(blob)
            out.flush()
            os.fsync(out.fileno())
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _host_for(provider: Provider, host: str | None) -> str:
    if provider.host_kind == "fixed":
        return ""
    value = str(host or "").strip().lower()
    if provider.host_kind == "tenant":
        if not _TENANT.fullmatch(value):
            raise OAuthError("enter the Microsoft Entra tenant ID or domain")
        return value
    if not _HOST.fullmatch(value) or not value.endswith(provider.allowed_suffixes):
        raise OAuthError(f"enter your {provider.label} host, ending in {' or '.join(provider.allowed_suffixes)}")
    return value


def _redirect_uri(port: int) -> str:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise OAuthError("invalid local callback port")
    return f"http://127.0.0.1:{port}/v1/connector-oauth/callback"


def _public(conn_id: str, conn: dict[str, Any]) -> dict[str, Any]:
    return {
        "connection_id": conn_id, "provider": conn["provider"],
        "label": PROVIDERS[conn["provider"]].label, "host": conn.get("host") or None,
        "scopes": conn.get("scopes", []), "connected_at": conn.get("connected_at"),
        "last_refreshed_at": conn.get("last_refreshed_at"),
        "has_refresh_token": bool(conn.get("refresh_token")),
        "tokens_exposed": False,
    }


def begin(provider: str, *, client_id: str, host: str | None = None, scopes: list[str] | None = None,
          port: int = 8787, client_secret: str | None = None) -> dict[str, Any]:
    """Start a connect; returns the provider URL the user opens in their browser."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise OAuthError("unknown provider")
    if not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id):
        raise OAuthError("enter the client ID of the app you registered with the provider")
    if client_secret is not None and (not isinstance(client_secret, str) or not 8 <= len(client_secret) <= 512):
        raise OAuthError("invalid client secret")
    chosen = list(scopes) if scopes else list(spec.default_scopes)
    if not 1 <= len(chosen) <= 20 or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in chosen):
        raise OAuthError("invalid scopes")
    resolved = _host_for(spec, host)
    redirect = _redirect_uri(port)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for key in [k for k, v in _pending.items() if v["expires"] <= now]:
            del _pending[key]
        if len(_pending) >= 10:
            raise OAuthError("too many sign-ins in progress; finish or wait for them to expire")
        _pending[state] = {"provider": provider, "client_id": client_id, "client_secret": client_secret,
                           "host": resolved, "scopes": chosen, "verifier": verifier,
                           "redirect_uri": redirect, "expires": now + _PENDING_TTL}
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
              "scope": " ".join(chosen), "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256", **dict(spec.extra_authorize)}
    return {"authorization_url": spec.authorize.format(host=resolved) + "?" + urlencode(params),
            "expires_in_seconds": _PENDING_TTL, "redirect_uri": redirect}


def _token_request(spec: Provider, host: str, form: dict[str, str]) -> dict[str, Any]:
    url = spec.token.format(host=host)
    try:
        with _client() as client:
            response = client.post(url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        raise OAuthError(f"{spec.label} could not be reached") from None
    if response.status_code != 200:
        raise OAuthError(f"{spec.label} refused the request (HTTP {response.status_code})")
    try:
        body = response.json()
    except ValueError:
        raise OAuthError(f"{spec.label} returned an unreadable token response") from None
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str) or not body["access_token"]:
        raise OAuthError(f"{spec.label} returned no access token")
    return body


def _expiry(body: dict[str, Any]) -> float:
    lifetime = body.get("expires_in")
    seconds = int(lifetime) if isinstance(lifetime, (int, str)) and str(lifetime).isdigit() else 3600
    return time.time() + max(60, min(seconds, 86_400)) - 60


def complete(state: str, code: str) -> dict[str, Any]:
    """Finish a connect from the local callback; returns the stored connection (no tokens)."""
    with _LOCK:
        pending = _pending.pop(str(state or ""), None)
    if pending is None or pending["expires"] <= time.time():
        raise OAuthError("this sign-in link expired or was already used; start Connect again")
    if not isinstance(code, str) or not 1 <= len(code) <= 4096:
        raise OAuthError("the provider returned no authorization code")
    spec = PROVIDERS[pending["provider"]]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"],
            "client_id": pending["client_id"], "code_verifier": pending["verifier"]}
    if pending["client_secret"]:
        form["client_secret"] = pending["client_secret"]
    body = _token_request(spec, pending["host"], form)
    conn_id = "conn_" + secrets.token_hex(8)
    granted = body.get("scope")
    conn = {
        "provider": spec.name, "host": pending["host"], "client_id": pending["client_id"],
        "client_secret": pending["client_secret"],
        "scopes": granted.split() if isinstance(granted, str) and granted else pending["scopes"],
        "refresh_token": body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else None,
        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "last_refreshed_at": None,
    }
    instance = body.get("instance_url")
    if spec.name == "salesforce" and isinstance(instance, str):
        parsed = urlsplit(instance)
        if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(spec.allowed_suffixes):
            conn["api_host"] = parsed.hostname
    with _LOCK:
        data = _load()
        if len(data["connections"]) >= _MAX_CONNECTIONS:
            raise OAuthError("too many connector sign-ins; disconnect one first")
        data["connections"][conn_id] = conn
        _save(data)
        _access[conn_id] = (body["access_token"], _expiry(body))
    return _public(conn_id, conn)


def status() -> dict[str, Any]:
    with _LOCK:
        data = _load()
    return {"format": FORMAT, "providers": [{"provider": p.name, "label": p.label, "host_kind": p.host_kind,
                                             "default_scopes": list(p.default_scopes),
                                             "supports_revocation": bool(p.revoke)} for p in PROVIDERS.values()],
            "connections": [_public(k, v) for k, v in data["connections"].items()],
            "tokens_exposed": False, "enables_reads_by_itself": False}


class _FileLock:
    """Serialize refresh across OWG processes so a rotated refresh token is never lost."""

    def __enter__(self):
        path = auth_dir() / ".connector_oauth.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(path, "a+b")
        try:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_EX)
        except ImportError:  # Windows: msvcrt byte lock
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *exc):
        try:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()
        return False


def access_token(connection_id: str) -> str:
    """Bearer token for a connector call, refreshing (and saving rotation) when needed."""
    if not _ID.fullmatch(str(connection_id or "")):
        raise OAuthError("unknown connection")
    with _LOCK, _FileLock():
        cached = _access.get(connection_id)
        if cached and cached[1] > time.time():
            return cached[0]
        data = _load()
        conn = data["connections"].get(connection_id)
        if conn is None:
            raise OAuthError("unknown connection")
        if not conn.get("refresh_token"):
            raise OAuthError("this sign-in has expired and cannot be refreshed; connect again")
        spec = PROVIDERS[conn["provider"]]
        form = {"grant_type": "refresh_token", "refresh_token": conn["refresh_token"], "client_id": conn["client_id"]}
        if conn.get("client_secret"):
            form["client_secret"] = conn["client_secret"]
        body = _token_request(spec, conn["host"], form)
        if isinstance(body.get("refresh_token"), str) and body["refresh_token"]:
            conn["refresh_token"] = body["refresh_token"]  # rotation: persist before first use
        conn["last_refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save(data)
        _access[connection_id] = (body["access_token"], _expiry(body))
        return body["access_token"]


def api_host(connection_id: str) -> str | None:
    with _LOCK:
        conn = _load()["connections"].get(str(connection_id))
    return (conn.get("api_host") or conn.get("host")) if conn else None


def disconnect(connection_id: str) -> dict[str, Any]:
    """Revoke at the provider when supported, then forget the sign-in locally."""
    with _LOCK:
        data = _load()
        conn = data["connections"].pop(str(connection_id), None)
        if conn is None:
            raise OAuthError("unknown connection")
        _save(data)
        _access.pop(str(connection_id), None)
    spec = PROVIDERS[conn["provider"]]
    revoked = "not_supported_by_provider"
    token = conn.get("refresh_token")
    if spec.revoke and token:
        try:
            with _client() as client:
                if spec.name == "zendesk":
                    response = client.delete(spec.revoke.format(host=conn["host"]),
                                             headers={"Authorization": "Bearer " + access_token_unsafe_from(conn, spec)})
                else:
                    form = {"token": token, "client_id": conn["client_id"]}
                    if conn.get("client_secret"):
                        form["client_secret"] = conn["client_secret"]
                    response = client.post(spec.revoke.format(host=conn["host"]), data=form)
            revoked = "revoked" if response.status_code in (200, 204) else f"provider_refused_http_{response.status_code}"
        except (httpx.HTTPError, OAuthError):
            revoked = "provider_unreachable"
    return {"connection_id": connection_id, "forgotten_locally": True, "provider_revocation": revoked,
            "note": "If revocation did not succeed, also remove the app's access in the provider's admin settings."}


def access_token_unsafe_from(conn: dict[str, Any], spec: Provider) -> str:
    """Exchange the stored refresh token once, only to call a token-authenticated revoke."""
    form = {"grant_type": "refresh_token", "refresh_token": conn["refresh_token"], "client_id": conn["client_id"]}
    if conn.get("client_secret"):
        form["client_secret"] = conn["client_secret"]
    return _token_request(spec, conn["host"], form)["access_token"]


_ENV_PROVIDER = {"OWG_SALESFORCE_READ_TOKEN": "salesforce", "OWG_SERVICENOW_TOKEN": "servicenow",
                 "OWG_ENTRA_READ_TOKEN": "microsoft", "OWG_M365_READ_TOKEN": "microsoft",
                 "OWG_JIRA_TOKEN": "atlassian", "OWG_ZENDESK_TOKEN": "zendesk"}


class OAuthBearer(str):
    """An access token that remembers which stored sign-in issued it.

    Profiles bind to ``credential_identity`` (the sign-in), not to the token, so a
    routine renewal of the same sign-in does not look like a different credential.
    """
    credential_identity: str


def bearer_for(connection_id: str, provider: str) -> OAuthBearer:
    with _LOCK:
        conn = _load()["connections"].get(str(connection_id))
    if conn is None or conn["provider"] != provider:
        raise OAuthError("the selected sign-in is not a connection for this provider")
    bearer = OAuthBearer(access_token(connection_id))
    bearer.credential_identity = "oauth_connection:" + connection_id
    return bearer


def connector_token(env_name: str, getenv: Callable[[str], str | None] = os.environ.get) -> str | None:
    """Bearer token for a connector CLI.

    ``OWG_<NAME>_OAUTH_CONNECTION`` (e.g. ``OWG_SERVICENOW_OAUTH_CONNECTION``)
    selects a stored sign-in; otherwise the existing token variable is used.
    A sign-in for a different provider is refused rather than sent anywhere.
    """
    connection = getenv(env_name.removesuffix("_TOKEN") + "_OAUTH_CONNECTION")
    if not connection:
        return getenv(env_name)
    expected = _ENV_PROVIDER.get(env_name)
    with _LOCK:
        conn = _load()["connections"].get(connection)
    if conn is None or (expected and conn["provider"] != expected):
        raise OAuthError("the selected sign-in is not a connection for this provider")
    return access_token(connection)


__all__ = ["OAuthError", "PROVIDERS", "access_token", "api_host", "begin", "complete", "connector_token",
           "disconnect", "status"]
