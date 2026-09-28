from __future__ import annotations

"""Company sign-in against an in-process OpenID Connect provider (RSA-signed tokens)."""

import json
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from gateway.human_access import HumanAccessSettings
from gateway_identity_helpers import add_admin, add_employee, bootstrap_owner, enroll, make_app, personal_invite

ISSUER = "https://login.acme.test"
CLIENT_ID = "owg-gateway"


class FakeIdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid="k1", use="sig", alg="RS256")
        self.jwks = {"keys": [jwk]}
        self.next_claims: dict = {}
        self.token_requests: list[dict] = []

    def id_token(self, claims: dict) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, **claims}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == ISSUER + "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "jwks_uri": ISSUER + "/jwks",
            })
        if url == ISSUER + "/jwks":
            return httpx.Response(200, json=self.jwks)
        if url == ISSUER + "/token":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if form.get("code") != ["good-code"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.id_token(self.next_claims), "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture()
def sso(tmp_path, monkeypatch):
    monkeypatch.setenv("OWG_GATEWAY_SSO_ISSUER", ISSUER)
    monkeypatch.setenv("OWG_GATEWAY_SSO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OWG_GATEWAY_SSO_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("OWG_GATEWAY_PUBLIC_URL", "https://gw.acme.test")
    monkeypatch.setenv("OWG_GATEWAY_SSO_ALLOWED_DOMAINS", "acme.se")
    idp = FakeIdP()
    app, db = make_app(tmp_path)
    app.state.sso_client.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(idp.handler))
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        yield client, db, owner, idp


def _sign_in(client, idp, purpose, claims, **body):
    start = client.post("/v1/sso/start", json={"purpose": purpose, **body})
    assert start.status_code == 200, start.text
    query = parse_qs(urlparse(start.json()["redirect_url"]).query)
    assert query["redirect_uri"] == ["https://gw.acme.test/sso/callback"]
    assert query["code_challenge_method"] == ["S256"] and query["client_id"] == [CLIENT_ID]
    idp.next_claims = {"nonce": query["nonce"][0], **claims}
    callback = client.get("/sso/callback", params={"code": "good-code", "state": query["state"][0]}, follow_redirects=False)
    assert callback.status_code == 303
    return callback.headers["location"], query["state"][0]


def _fragment(location: str) -> dict:
    return {k: unquote(v) for k, v in (p.split("=", 1) for p in location.split("#", 1)[1].split("&"))}


def test_admin_signs_in_with_sso_and_pkce_verifier_is_sent(sso):
    client, _db, owner, idp = sso
    add_admin(client, owner, "it@acme.se", "admin")
    location, _state = _sign_in(client, idp, "admin", {"sub": "u-it", "email": "IT@acme.se", "email_verified": True})
    assert location.startswith("/admin#session=owg_admin_session_")
    headers = {"Authorization": "Bearer " + _fragment(location)["session"]}
    me = client.get("/v1/admin/auth/me", headers=headers).json()
    assert me["email"] == "it@acme.se" and me["auth_method"] == "sso"
    form = idp.token_requests[-1]
    assert form["code_verifier"] and form["client_secret"] == ["s3cret"] and form["grant_type"] == ["authorization_code"]
    assert client.get("/v1/admin/auth/state").json()["sso_enabled"] is True


def test_unknown_admin_email_is_rejected(sso):
    client, _db, _owner, idp = sso
    location, _ = _sign_in(client, idp, "admin", {"sub": "u-x", "email": "stranger@acme.se"})
    assert location.startswith("/admin#error=") and "no administrator account" in _fragment(location)["error"]


@pytest.mark.parametrize("claims,reason", [
    ({"sub": "u1", "email": "it@acme.se", "email_verified": False}, "not verified"),
    ({"sub": "u1", "email": "it@other.org"}, "cannot sign in"),
    ({"sub": "u1"}, "did not provide an email"),
])
def test_unverified_foreign_or_missing_email_is_rejected(sso, claims, reason):
    client, _db, owner, idp = sso
    add_admin(client, owner, "it@acme.se", "admin")
    location, _ = _sign_in(client, idp, "admin", claims)
    assert location.startswith("/?error=") and reason in unquote(location)


def test_state_is_single_use_and_nonce_must_match(sso):
    client, _db, owner, idp = sso
    add_admin(client, owner, "it@acme.se", "admin")
    location, state = _sign_in(client, idp, "admin", {"sub": "u-it", "email": "it@acme.se"})
    assert "session=" in location
    replay = client.get("/sso/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    assert "already%20used" in replay.headers["location"] or "already used" in unquote(replay.headers["location"])

    start = client.post("/v1/sso/start", json={"purpose": "admin"}).json()
    state = parse_qs(urlparse(start["redirect_url"]).query)["state"][0]
    idp.next_claims = {"nonce": "attacker-nonce", "sub": "u-it", "email": "it@acme.se"}
    bad = client.get("/sso/callback", params={"code": "good-code", "state": state}, follow_redirects=False)
    assert "does not belong to this attempt" in unquote(bad.headers["location"])


def test_token_for_another_audience_is_rejected(sso):
    client, _db, owner, idp = sso
    add_admin(client, owner, "it@acme.se", "admin")
    start = client.post("/v1/sso/start", json={"purpose": "admin"}).json()
    q = parse_qs(urlparse(start["redirect_url"]).query)
    idp.next_claims = {"nonce": q["nonce"][0], "sub": "u-it", "email": "it@acme.se", "aud": "some-other-app"}
    bad = client.get("/sso/callback", params={"code": "good-code", "state": q["state"][0]}, follow_redirects=False)
    assert "could not be verified" in unquote(bad.headers["location"])


def test_employee_confirms_personal_invite_then_joins(sso):
    client, db, owner, idp = sso
    anna = add_employee(client, owner, "anna@acme.se")
    invite = personal_invite(client, owner, anna["employee_id"])
    assert invite["require_sso"] is True  # default when SSO is configured
    assert invite["verify_url"].startswith("https://gw.acme.test/join/verify#code=owgjoin1.")
    assert enroll(client, invite["token"], "laptop-1").status_code == 403

    wrong, _ = _sign_in(client, idp, "join", {"sub": "u-erik", "email": "erik@acme.se"}, invite_token=invite["token"])
    assert "this invitation is for anna@acme.se" in _fragment(wrong)["error"]
    assert enroll(client, invite["token"], "laptop-1").status_code == 403

    ok, _ = _sign_in(client, idp, "join", {"sub": "u-anna", "email": "anna@acme.se"}, invite_token=invite["token"])
    assert ok == "/join/verify#verified=anna%40acme.se"
    preview = client.get("/v1/devices/join-preview", headers={"Authorization": "Bearer " + invite["token"]}).json()
    assert preview["identity"]["sso_verified"] is True
    joined = enroll(client, invite["token"], "laptop-1")
    assert joined.status_code == 200 and joined.json()["identity_source"] == "sso_verified"
    people = client.get("/v1/admin/people/acme", headers=owner).json()["employees"]
    assert people[0]["sso_linked"] is True


def test_employee_opens_me_with_company_sign_in(sso):
    client, _db, owner, idp = sso
    add_employee(client, owner, "anna@acme.se", ["sales"])
    location, _ = _sign_in(client, idp, "me", {"sub": "u-anna", "email": "anna@acme.se"})
    assert location.startswith("/me#session=owg_me_session_")
    overview = client.get("/v1/me/overview", headers={"Authorization": "Bearer " + _fragment(location)["session"]}).json()
    assert overview["you"]["email"] == "anna@acme.se" and overview["signed_in_with"] == "sso"
    stranger, _ = _sign_in(client, idp, "me", {"sub": "u-x", "email": "stranger@acme.se"})
    assert stranger.startswith("/me#error=") and "roster" in _fragment(stranger)["error"]


def test_provider_error_and_unconfigured_sso(sso, tmp_path, monkeypatch):
    client, _db, _owner, _idp = sso
    denied = client.get("/sso/callback", params={"error": "access_denied", "error_description": "User cancelled"}, follow_redirects=False)
    assert denied.headers["location"] == "/?error=User%20cancelled"
    for var in ("OWG_GATEWAY_SSO_ISSUER", "OWG_GATEWAY_SSO_CLIENT_ID", "OWG_GATEWAY_PUBLIC_URL"):
        monkeypatch.delenv(var)
    app, _db2 = make_app(tmp_path / "plain")
    with TestClient(app) as plain:
        assert plain.post("/v1/sso/start", json={"purpose": "admin"}).status_code == 404
        assert plain.get("/v1/admin/auth/state").json()["sso_enabled"] is False


def test_sso_public_url_must_be_https(monkeypatch):
    from gateway.sso import SSOSettings

    monkeypatch.setenv("OWG_GATEWAY_SSO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OWG_GATEWAY_PUBLIC_URL", "http://gw.acme.test")
    with pytest.raises(ValueError):
        SSOSettings.from_env()
    monkeypatch.setenv("OWG_GATEWAY_PUBLIC_URL", "http://localhost:8790")
    assert SSOSettings.from_env().public_url == "http://localhost:8790"


def test_human_api_maps_sso_subject_to_the_roster_employee(tmp_path, monkeypatch):
    """A token whose actor claim is an opaque `sub` reads the employee's own evidence."""

    class StaticVerifier:
        def verify(self, token):
            return {"sub": "opaque-123", "owg_org": "acme", "groups": []}

    settings = HumanAccessSettings(issuer=ISSUER, audience="api", jwks_url=ISSUER + "/jwks")
    app, db = make_app(tmp_path, human_access=settings, verifier=StaticVerifier())
    with TestClient(app) as client:
        owner, _ = bootstrap_owner(client)
        anna = add_employee(client, owner, "anna@acme.se")
        joined = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "laptop-1").json()
        client.post("/v1/evidence/batch", headers={"Authorization": "Bearer " + joined["token"]}, json={"events": [{
            "event_id": "e1", "observed_at": "2026-09-27T10:00:00Z", "event_type": "focus_span", "app": "Outlook", "window_title": "Inbox",
        }]})
        human = {"Authorization": "Bearer any.jwt.value"}
        assert client.get("/v1/human/me", headers=human).json()["actor_id"] == "opaque-123"  # not yet linked
        with db.connect() as conn:
            conn.execute("UPDATE gateway_employees SET sso_subject = 'opaque-123'")
        assert client.get("/v1/human/me", headers=human).json()["actor_id"] == "anna@acme.se"
        trace = client.get("/v1/human/workflow-trace", headers=human).json()
        assert [r["window_title"] for r in trace["rows"]] == ["Inbox"]


def test_docker_deployment_passes_every_identity_setting():
    """The compose file lists variables explicitly; a missing one silently disables the feature."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    used = set()
    for module in ("admin_accounts.py", "sso.py", "employees.py", "me_access.py", "identity_routes.py"):
        used |= set(re.findall(r'getenv\("(OWG_GATEWAY_[A-Z_]+)"', (root / "gateway" / module).read_text()))
    assert {"OWG_GATEWAY_SSO_CLIENT_ID", "OWG_GATEWAY_PUBLIC_URL", "OWG_GATEWAY_ADMIN_TOKEN_API"} <= used
    compose = (root / "deploy" / "docker-compose.yml").read_text()
    example = (root / "deploy" / ".env.example").read_text()
    for var in sorted(used):
        assert f"      {var}: ${{{var}" in compose, var
        assert re.search(rf"^{var}=", example, re.M), var
