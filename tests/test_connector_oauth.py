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


def test_jira_profiles_bind_to_the_sign_in_not_the_rotating_token(provider, monkeypatch):
    from server import jira_retained_profiles as profiles
    from server import jira_retained_source as source
    monkeypatch.setattr(profiles, "ensure_api_token", lambda: "local-key")
    started = oauth.begin("atlassian", client_id="atlassian-client")
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    provider.challenge = query["code_challenge"][0]
    conn = oauth.complete(query["state"][0], "GOOD")
    env = {"OWG_JIRA_SOURCE_READ_ENABLED": "true", "OWG_JIRA_RETAINED_INGESTION_ENABLED": "true",
           "OWG_JIRA_HOST": "acme.atlassian.net", "OWG_JIRA_CLOUD_ID": "11111111-2222-3333-4444-555555555555",
           "OWG_JIRA_OAUTH_CONNECTION": conn["connection_id"]}
    first = source.configuration(env)
    assert first[2] == "AT1" and first[2].credential_identity == "oauth_connection:" + conn["connection_id"]
    oauth._access.clear()
    renewed = source.configuration(env)
    assert renewed[2] == "AT-RT2"
    assert profiles._binding(first) == profiles._binding(renewed)
    legacy = ("acme.atlassian.net", env["OWG_JIRA_CLOUD_ID"], "PASTED")
    assert profiles._binding(legacy) != profiles._binding(first)


def test_jira_renewal_failure_fails_closed(provider):
    from server import jira_retained_source as source
    from server.jira_retained_source import JiraIngestionError
    env = {"OWG_JIRA_SOURCE_READ_ENABLED": "true", "OWG_JIRA_RETAINED_INGESTION_ENABLED": "true",
           "OWG_JIRA_HOST": "acme.atlassian.net", "OWG_JIRA_CLOUD_ID": "11111111-2222-3333-4444-555555555555",
           "OWG_JIRA_OAUTH_CONNECTION": "conn_0000000000000000"}
    with pytest.raises(JiraIngestionError, match="reconnect_required"):
        source.configuration(env)


def test_cached_token_is_not_returned_after_another_process_disconnects(provider, tmp_path):
    _, _, conn = _connect(provider)
    connection_id = conn['connection_id']
    assert oauth.access_token(connection_id) == 'AT1'
    script = r'''
import sys
from pathlib import Path
import httpx
from server import connector_oauth as oauth
oauth.auth_dir = lambda: Path(sys.argv[1])
oauth._read_or_create_secret = lambda name: 'local-installation-secret'
oauth._transport = httpx.MockTransport(lambda request: httpx.Response(200))
oauth.disconnect(sys.argv[2])
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), connection_id],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    with pytest.raises(oauth.OAuthError, match='unknown connection'):
        oauth.access_token(connection_id)
    assert connection_id not in oauth._access


def test_disconnect_waits_for_other_process_refresh_and_does_not_restore_connection(provider, tmp_path):
    import time
    _, _, conn = _connect(provider)
    connection_id = conn['connection_id']
    script = r'''
import sys, time
from pathlib import Path
import httpx
from server import connector_oauth as oauth
root, connection, action = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
oauth.auth_dir = lambda: root
oauth._read_or_create_secret = lambda name: 'local-installation-secret'
def handler(request):
    if action == 'refresh':
        (root / 'refresh-started').touch()
        deadline = time.monotonic() + 20
        while not (root / 'release-refresh').exists():
            if time.monotonic() > deadline: raise RuntimeError('refresh release timeout')
            time.sleep(.01)
        return httpx.Response(200, json={'access_token':'A2','refresh_token':'R2','expires_in':3600})
    return httpx.Response(200)
oauth._transport = httpx.MockTransport(handler)
if action == 'refresh': oauth.access_token(connection)
else:
    (root / 'disconnect-started').touch()
    oauth.disconnect(connection)
'''
    root = Path(__file__).resolve().parents[1]
    args = [sys.executable, '-c', script, str(tmp_path), connection_id]
    refresh = subprocess.Popen(args + ['refresh'], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    disconnect = None
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / 'refresh-started').exists():
            assert refresh.poll() is None and time.monotonic() < deadline, 'refresh did not start'
            time.sleep(.01)
        disconnect = subprocess.Popen(args + ['disconnect'], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while not (tmp_path / 'disconnect-started').exists():
            assert disconnect.poll() is None and time.monotonic() < deadline, 'disconnect did not start'
            time.sleep(.01)
        # Give an unlocked disconnect an opportunity to race the held refresh.
        time.sleep(.3)
        (tmp_path / 'release-refresh').touch()
        _, refresh_err = refresh.communicate(timeout=20)
        _, disconnect_err = disconnect.communicate(timeout=20)
        assert refresh.returncode == 0, refresh_err
        assert disconnect.returncode == 0, disconnect_err
        assert oauth.status()['connections'] == []
        with pytest.raises(oauth.OAuthError, match='unknown connection'):
            oauth.access_token(connection_id)
    finally:
        (tmp_path / 'release-refresh').touch()
        for process in (refresh, disconnect):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_new_sign_in_is_not_lost_while_another_process_refreshes(provider, tmp_path):
    import concurrent.futures
    import time
    _, _, first = _connect(provider)
    script = r'''
import sys, time
from pathlib import Path
import httpx
from server import connector_oauth as oauth
root = Path(sys.argv[1])
oauth.auth_dir = lambda: root
oauth._read_or_create_secret = lambda name: 'local-installation-secret'
def handler(request):
    (root / 'refresh-started').touch()
    deadline = time.monotonic() + 20
    while not (root / 'release-refresh').exists():
        if time.monotonic() > deadline: raise RuntimeError('refresh release timeout')
        time.sleep(.01)
    return httpx.Response(200, json={'access_token':'A2','refresh_token':'R2','expires_in':3600})
oauth._transport = httpx.MockTransport(handler)
oauth.access_token(sys.argv[2])
'''
    root = Path(__file__).resolve().parents[1]
    refresh = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), first['connection_id']],
                               cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / 'refresh-started').exists():
            assert refresh.poll() is None and time.monotonic() < deadline, 'refresh did not start'
            time.sleep(.01)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_connect, provider)
            # Confirm the second authorization-code exchange has completed before
            # the first refresh can save its snapshot of the connection store.
            while sum(form.get('grant_type') == 'authorization_code' for _, _, form in provider.calls) < 2:
                assert not future.done() and time.monotonic() < deadline, 'second exchange did not start'
                time.sleep(.01)
            time.sleep(.2)
            (tmp_path / 'release-refresh').touch()
            _, error = refresh.communicate(timeout=20)
            assert refresh.returncode == 0, error
            _, _, second = future.result(timeout=20)
        ids = {row['connection_id'] for row in oauth.status()['connections']}
        assert ids == {first['connection_id'], second['connection_id']}
    finally:
        (tmp_path / 'release-refresh').touch()
        if refresh.poll() is None:
            refresh.kill()
            refresh.communicate(timeout=5)


def test_uncertain_refresh_is_not_retried_or_served_from_cache(provider):
    _, _, conn = _connect(provider, 'atlassian', None)
    cid = conn['connection_id']
    oauth._access.clear()
    calls = []
    def lost_response(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout('synthetic timeout after send')
    oauth._transport = httpx.MockTransport(lost_response)
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert len(calls) == 1
    assert oauth.status()['connections'][0]['reauthorization_required'] is True
    # A stale access token from another worker must not bypass the intent.
    oauth._access[cid] = ('cached-test-token', float('inf'))
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert len(calls) == 1 and cid not in oauth._access
    assert 'cached-test-token' not in str(oauth.status())


def test_refresh_result_save_failure_quarantines_old_token(provider, monkeypatch):
    _, _, conn = _connect(provider, 'atlassian', None)
    cid = conn['connection_id']
    oauth._access.clear()
    save = oauth._save
    def fail_result(data):
        if data['connections'][cid].get('last_refreshed_at'):
            raise OSError('synthetic save failure')
        save(data)
    monkeypatch.setattr(oauth, '_save', fail_result)
    with pytest.raises(oauth.OAuthError, match='could not be saved'):
        oauth.access_token(cid)
    assert oauth._load()['connections'][cid]['refresh_state'] == 'in_flight'
    attempts = sum(form.get('grant_type') == 'refresh_token' for _, _, form in provider.calls)
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert sum(form.get('grant_type') == 'refresh_token' for _, _, form in provider.calls) == attempts


def test_uncertain_zendesk_disconnect_does_not_exchange_the_old_refresh_token(provider):
    _, _, conn = _connect(provider, 'zendesk', 'acme.zendesk.com')
    cid = conn['connection_id']
    with oauth._LOCK, oauth._FileLock():
        data = oauth._load()
        data['connections'][cid]['refresh_state'] = 'in_flight'
        oauth._save(data)
    before = len(provider.calls)
    result = oauth.disconnect(cid)
    assert result['provider_revocation'] == 'manual_revocation_required_refresh_uncertain'
    assert len(provider.calls) == before
    assert oauth.status()['connections'] == []


@pytest.mark.parametrize('replacement', [None, '', 7])
def test_malformed_refresh_replacement_is_quarantined(provider, replacement):
    _, _, conn = _connect(provider, 'atlassian', None)
    cid = conn['connection_id']
    oauth._access.clear()
    calls = []
    def malformed(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'access_token':'next', 'refresh_token':replacement, 'expires_in':3600})
    oauth._transport = httpx.MockTransport(malformed)
    with pytest.raises(oauth.OAuthError, match='invalid replacement'):
        oauth.access_token(cid)
    assert oauth.status()['connections'][0]['reauthorization_required']
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert len(calls) == 1


def test_ambiguous_token_response_does_not_clear_refresh_intent(provider):
    _, _, conn = _connect(provider, 'atlassian', None)
    cid = conn['connection_id']
    oauth._access.clear()
    calls = []
    def duplicate(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=b'{"access_token":"next","refresh_token":"a","refresh_token":"b"}')
    oauth._transport = httpx.MockTransport(duplicate)
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert len(calls) == 1 and oauth.status()['connections'][0]['reauthorization_required']


def test_refresh_without_replacement_can_keep_nonrotating_provider_token(provider):
    _, _, conn = _connect(provider, 'servicenow', 'acme.service-now.com')
    cid = conn['connection_id']
    oauth._access.clear()
    oauth._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'access_token':'next','expires_in':3600}))
    assert oauth.access_token(cid) == 'next'
    saved = oauth._load()['connections'][cid]
    assert saved['refresh_state'] == 'ready' and saved['refresh_token'] == 'RT1'
    assert not oauth.status()['connections'][0]['reauthorization_required']


def test_process_exit_during_refresh_blocks_retry_even_with_another_cached_token(provider, tmp_path):
    _, _, conn = _connect(provider, 'atlassian', None)
    cid = conn['connection_id']
    script = r'''
import os, sys
from pathlib import Path
import httpx
from server import connector_oauth as oauth
oauth.auth_dir = lambda: Path(sys.argv[1])
oauth._read_or_create_secret = lambda name: 'local-installation-secret'
def terminate_after_request(request):
    os._exit(0)
oauth._transport = httpx.MockTransport(terminate_after_request)
oauth.access_token(sys.argv[2])
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), cid],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # The parent still has the token issued before the child exited.
    assert cid in oauth._access
    with pytest.raises(oauth.OAuthError, match='reconnect'):
        oauth.access_token(cid)
    assert oauth.status()['connections'][0]['reauthorization_required']
