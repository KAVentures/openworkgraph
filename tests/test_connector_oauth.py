"""OAuth connect, refresh and revoke for business-system connectors."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from server import connector_oauth as oauth


class FakeProvider:
    def __init__(self):
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.challenge = ""
        self.refresh_tokens = {"RT1": "RT2", "RT2": "RT3"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = dict(pair.split("=", 1) for pair in request.content.decode().split("&")) if request.content else {}
        self.calls.append((request.method, request.url.path, form))
        if request.url.path.endswith(("/revoke", "/oauth_revoke_token.do")):
            return httpx.Response(200)
        if form.get("grant_type") == "authorization_code":
            verifier = form["code_verifier"]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if digest != self.challenge or form.get("code") != "GOOD":
                return httpx.Response(400, json={"error": "invalid_grant", "secret": "PROVIDER-SECRET"})
            return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600,
                                             "instance_url": "https://acme.my.salesforce.com", "scope": "api refresh_token"})
        if form.get("grant_type") == "refresh_token":
            nxt = self.refresh_tokens.get(form["refresh_token"])
            if nxt is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "AT-" + nxt, "refresh_token": nxt, "expires_in": 3600})
        return httpx.Response(404)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(oauth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_read_or_create_secret", lambda name: "local-installation-secret")
    monkeypatch.setattr(oauth, "_transport", httpx.MockTransport(fake))
    oauth._pending.clear()
    oauth._access.clear()
    return fake


def _connect(fake, provider_name="salesforce", host="acme.my.salesforce.com"):
    started = oauth.begin(provider_name, client_id="3MVG9client", host=host)
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    fake.challenge = query["code_challenge"][0]
    return started, query, oauth.complete(query["state"][0], "GOOD")


def test_authorization_url_uses_pkce_state_and_local_callback(provider):
    started, query, _ = _connect(provider)
    url = urlsplit(started["authorization_url"])
    assert url.scheme == "https" and url.netloc == "acme.my.salesforce.com"
    assert query["code_challenge_method"] == ["S256"] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8787/v1/connector-oauth/callback"]
    assert len(query["state"][0]) >= 40


def test_connect_stores_encrypted_refresh_token_and_never_exposes_tokens(provider, tmp_path):
    _, _, conn = _connect(provider)
    assert conn["has_refresh_token"] is True and conn["tokens_exposed"] is False
    assert conn["scopes"] == ["api", "refresh_token"]
    stored = (tmp_path / ".connector_oauth.bin").read_bytes()
    assert b"RT1" not in stored and b"AT1" not in stored
    assert "RT1" not in str(oauth.status()) and "AT1" not in str(oauth.status())
    if os.name == "posix":
        assert oct((tmp_path / ".connector_oauth.bin").stat().st_mode & 0o777) == "0o600"
    assert oauth.access_token(conn["connection_id"]) == "AT1"


def test_state_is_single_use_and_bad_codes_fail_without_echo(provider):
    started = oauth.begin("salesforce", client_id="3MVG9client", host="acme.my.salesforce.com")
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    provider.challenge = query["code_challenge"][0]
    with pytest.raises(oauth.OAuthError) as error:
        oauth.complete(query["state"][0], "BAD")
    assert "PROVIDER-SECRET" not in str(error.value)
    with pytest.raises(oauth.OAuthError, match="expired or was already used"):
        oauth.complete(query["state"][0], "GOOD")
    with pytest.raises(oauth.OAuthError):
        oauth.complete("forged-state", "GOOD")


def test_refresh_persists_rotated_token(provider, tmp_path):
    _, _, conn = _connect(provider)
    oauth._access.clear()
    assert oauth.access_token(conn["connection_id"]) == "AT-RT2"
    oauth._access.clear()
    assert oauth.access_token(conn["connection_id"]) == "AT-RT3"
    refreshes = [form["refresh_token"] for _, _, form in provider.calls if form.get("grant_type") == "refresh_token"]
    assert refreshes == ["RT1", "RT2"]
    assert oauth._load()["connections"][conn["connection_id"]]["refresh_token"] == "RT3"


def test_disconnect_revokes_and_forgets(provider):
    _, _, conn = _connect(provider)
    result = oauth.disconnect(conn["connection_id"])
    assert result["provider_revocation"] == "revoked" and result["forgotten_locally"] is True
    assert provider.calls[-1][1].endswith("/revoke") and provider.calls[-1][2]["token"] == "RT1"
    assert oauth.status()["connections"] == []
    with pytest.raises(oauth.OAuthError):
        oauth.access_token(conn["connection_id"])


def test_microsoft_has_no_revocation_endpoint_and_is_forgotten_locally(provider):
    started = oauth.begin("microsoft", client_id="00000000-aaaa-bbbb-cccc-000000000000", host="contoso.onmicrosoft.com")
    assert urlsplit(started["authorization_url"]).path == "/contoso.onmicrosoft.com/oauth2/v2.0/authorize"
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    provider.challenge = query["code_challenge"][0]
    conn = oauth.complete(query["state"][0], "GOOD")
    assert oauth.disconnect(conn["connection_id"])["provider_revocation"] == "not_supported_by_provider"


@pytest.mark.parametrize("provider_name,host", [
    ("salesforce", "evil.example.com"), ("salesforce", "acme.my.salesforce.com.evil.com"),
    ("servicenow", "acme.example.com"), ("zendesk", "https://acme.zendesk.com"),
    ("microsoft", "../../evil"), ("nope", "acme.my.salesforce.com"),
])
def test_hosts_and_providers_are_validated(provider, provider_name, host):
    with pytest.raises(oauth.OAuthError):
        oauth.begin(provider_name, client_id="3MVG9client", host=host)


def test_client_ids_scopes_and_ports_are_validated(provider):
    with pytest.raises(oauth.OAuthError):
        oauth.begin("atlassian", client_id="x y")
    with pytest.raises(oauth.OAuthError):
        oauth.begin("atlassian", client_id="client-id", scopes=["read jira"])
    with pytest.raises(oauth.OAuthError):
        oauth.begin("atlassian", client_id="client-id", port=80)
    started = oauth.begin("atlassian", client_id="client-id")
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    assert query["audience"] == ["api.atlassian.com"] and "offline_access" in query["scope"][0]


def test_connector_token_prefers_matching_connection_and_refuses_other_providers(provider):
    _, _, conn = _connect(provider)
    env = {"OWG_SALESFORCE_READ_OAUTH_CONNECTION": conn["connection_id"], "OWG_SALESFORCE_READ_TOKEN": "LEGACY"}
    assert oauth.connector_token("OWG_SALESFORCE_READ_TOKEN", env.get) == "AT1"
    assert oauth.connector_token("OWG_SERVICENOW_TOKEN", {"OWG_SERVICENOW_TOKEN": "LEGACY"}.get) == "LEGACY"
    with pytest.raises(oauth.OAuthError, match="not a connection for this provider"):
        oauth.connector_token("OWG_SERVICENOW_TOKEN", {"OWG_SERVICENOW_OAUTH_CONNECTION": conn["connection_id"]}.get)


def test_callback_is_the_only_public_route_and_needs_matching_state(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    assert client.get("/v1/connector-oauth").status_code == 401
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get("/v1/connector-oauth", headers=bearer).status_code == 403
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    assert client.get("/v1/connector-oauth", headers=owner).json()["connections"] == []
    assert client.post("/v1/connector-oauth/begin", json={"provider": "salesforce", "client_id": "3MVG9c",
                       "host": "evil.example.com"}, headers=owner).status_code == 409
    page = client.get("/v1/connector-oauth/callback?state=forged&code=x")
    assert page.status_code == 400 and "Sign-in failed" in page.text
    assert client.get("/v1/connector-oauth/callback?error=access_denied").status_code == 400
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / "data"), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / "auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / "config.json"))
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_connector_clis_use_the_shared_token_helper():
    root = Path(__file__).resolve().parents[1]
    for name in ("server/salesforce_case_history_live.py", "server/servicenow_incident_sync.py",
                 "scripts/scim_entra_lifecycle_reconcile.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "_connector_token(\"OWG_" in text and "os.environ.get(\"OWG_SERVICENOW_TOKEN\")" not in text
