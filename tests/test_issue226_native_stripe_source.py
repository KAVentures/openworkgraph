"""Issue #226: local OS-vault Stripe credentials, opt-in, and AI bearer isolation."""
from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from server import local_source_credentials as secrets_store
from server import stripe_source_evidence as stripe


ACCOUNT = "acct_1234567890ABCDEF"
OTHER = "acct_ABCDEF1234567890"
KEY = "rk_test_" + "syntheticOnlyNoRealSecret12345"
REF = "owg:r:" + "a" * 32
INVOICE = "in_1234567890ABCDEF"
INFO = {
    "provider": "stripe", "resource_kind": "invoice",
    "host": "dashboard.stripe.com", "resolver_locator": "invoice:" + INVOICE,
}


class FakeVault:
    def __init__(self):
        self.values = {}
        self.written = []
        self.removed = []

    def write(self, account, secret):
        self.values[account] = secret
        self.written.append(account)

    def read(self, account):
        return self.values.get(account)

    def remove(self, account):
        self.values.pop(account, None)
        self.removed.append(account)


def _reader(url, key):
    assert url == "https://api.stripe.com/v1/account"
    assert key == KEY
    return {"object": "account", "id": ACCOUNT, "email": "secret-patient@example.test"}


def test_user_must_explicitly_authorize_correct_restricted_account(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    assert secrets_store.load_stripe_source(directory=tmp_path) is None
    assert secrets_store.stripe_source_status(directory=tmp_path)["configured"] is False

    with pytest.raises(secrets_store.SourceCredentialError, match="restricted"):
        secrets_store.configure_stripe_source(
            ACCOUNT, "sk_live_forbidden", directory=tmp_path, reader=_reader,
        )
    with pytest.raises(secrets_store.SourceCredentialError, match="different account"):
        secrets_store.configure_stripe_source(
            OTHER, KEY, directory=tmp_path, reader=_reader,
        )
    assert not vault.written
    assert list(tmp_path.iterdir()) == []

    result = secrets_store.configure_stripe_source(
        ACCOUNT, KEY, directory=tmp_path, reader=_reader,
    )
    assert result["configured"] is True
    assert vault.written == [secrets_store._vault_account(ACCOUNT, tmp_path)]
    stored = json.loads((tmp_path / ".stripe_source_connection.json").read_text())
    assert stored == {"version": 1, "enabled": True, "account": ACCOUNT}
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / ".stripe_source_connection.json").stat().st_mode) == 0o600
    assert KEY not in json.dumps(stored)
    assert "secret-patient" not in json.dumps(stored)
    assert KEY not in json.dumps(result)
    assert KEY not in str(secrets_store.stripe_source_status(directory=tmp_path))
    assert secrets_store.load_stripe_source(directory=tmp_path)["OWG_STRIPE_RESTRICTED_READ_KEY"] == KEY


def test_native_store_unavailable_disables_reads_and_explicit_env_off_wins(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    secrets_store.configure_stripe_source(ACCOUNT, KEY, directory=tmp_path, reader=_reader)
    monkeypatch.setattr(secrets_store, "_vault", lambda: (_ for _ in ()).throw(
        secrets_store.SourceCredentialError("Keychain locked")
    ))
    assert secrets_store.load_stripe_source(directory=tmp_path) is None
    called = []
    monkeypatch.setattr(stripe, "load_stripe_source", lambda: called.append("native") or {
        "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
        "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
        "OWG_STRIPE_RESTRICTED_READ_KEY": KEY,
    })
    out = stripe.stripe_record_receipt(
        REF, environment={"OWG_STRIPE_SOURCE_READ_ENABLED": "false"},
        resolver=lambda *_: called.append("resolver"),
    )
    assert out["reason"] == "stripe_source_read_opt_in_off"
    assert called == []
    monkeypatch.setenv("OWG_STRIPE_SOURCE_READ_ENABLED", "false")
    out = stripe.stripe_record_receipt(REF, resolver=lambda *_: called.append("resolver"))
    assert out["reason"] == "stripe_source_read_opt_in_off"
    assert called == []


def test_implicit_native_connection_keeps_exact_account_and_record_guards(monkeypatch):
    monkeypatch.delenv("OWG_STRIPE_SOURCE_READ_ENABLED", raising=False)
    monkeypatch.setattr(stripe, "load_stripe_source", lambda: {
        "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
        "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
        "OWG_STRIPE_RESTRICTED_READ_KEY": KEY,
    })
    urls = []
    def reader(url, key):
        assert key == KEY
        urls.append(url)
        if url.endswith("/account"):
            return {"object": "account", "id": ACCOUNT}
        return {"object": "invoice", "id": INVOICE, "status": "paid", "paid": True}
    snapshot = stripe.stripe_record_receipt(
        REF, resolver=lambda *_: INFO, reader=reader,
    )
    assert snapshot["availability"] == "source_reported"
    assert snapshot["verified_operational_outcome"]["verified"] is True
    assert urls == [
        "https://api.stripe.com/v1/account",
        "https://api.stripe.com/v1/invoices/" + INVOICE,
    ]
    urls.clear()
    bad = stripe.stripe_record_receipt(
        REF, resolver=lambda *_: {**INFO, "host": "evil.invalid"}, reader=reader,
    )
    assert bad["availability"] == "unknown"
    assert urls == []


def test_native_credential_is_scoped_to_this_installation(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    secrets_store.configure_stripe_source(ACCOUNT, KEY, directory=first, reader=_reader)
    assert secrets_store.load_stripe_source(directory=first) is not None
    assert secrets_store._vault_account(ACCOUNT, first) != secrets_store._vault_account(ACCOUNT, second)
    # Copying the innocuous enabled metadata cannot import a different
    # installation's Keychain credential or authorize source reads.
    destination = second / ".stripe_source_connection.json"
    destination.write_text((first / destination.name).read_text())
    if os.name == "posix":
        destination.chmod(0o600)
    assert secrets_store.load_stripe_source(directory=second) is None


def test_connection_is_disabled_before_credential_deletion_if_vault_locked(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    secrets_store.configure_stripe_source(ACCOUNT, KEY, directory=tmp_path, reader=_reader)

    def denied():
        raise secrets_store.SourceCredentialError("Credential vault unavailable")
    monkeypatch.setattr(secrets_store, "_vault", denied)
    with pytest.raises(secrets_store.SourceCredentialError, match="disabled"):
        secrets_store.disconnect_stripe_source(directory=tmp_path)
    assert secrets_store.load_stripe_source(directory=tmp_path) is None
    assert secrets_store.stripe_source_status(directory=tmp_path)["configured"] is False


def test_disconnect_removes_secret_and_disallows_future_local_source_read(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    secrets_store.configure_stripe_source(ACCOUNT, KEY, directory=tmp_path, reader=_reader)
    outcome = secrets_store.disconnect_stripe_source(directory=tmp_path)
    assert outcome["configured"] is False
    assert vault.removed == [secrets_store._vault_account(ACCOUNT, tmp_path)]
    assert vault.read(secrets_store._vault_account(ACCOUNT, tmp_path)) is None


def test_symlink_or_world_readable_metadata_never_loads_credential(tmp_path, monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(secrets_store, "_vault", lambda: vault)
    vault.write(secrets_store._vault_account(ACCOUNT, tmp_path), KEY)
    metadata = tmp_path / ".stripe_source_connection.json"
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"version": 1, "enabled": True, "account": ACCOUNT}))
    metadata.symlink_to(target)
    assert secrets_store.load_stripe_source(directory=tmp_path) is None
    metadata.unlink()
    metadata.write_text(target.read_text())
    if os.name == "posix":
        metadata.chmod(0o644)
        assert secrets_store.load_stripe_source(directory=tmp_path) is None
    assert secrets_store.stripe_source_status(directory=tmp_path)["credential_storage"] == "native_user_keychain"


def test_dashboard_api_does_not_allow_ordinary_ai_bearer_to_manage_keys(tmp_path):
    """Actual local FastAPI guard, fresh interpreter to prevent shared app state."""
    import subprocess
    import sys
    from pathlib import Path

    script = """
from fastapi.testclient import TestClient
import server.secure_app
from server.secure_app import app
from server.local_auth import ensure_api_token, create_dashboard_session
client = TestClient(app)
url = '/v1/local-source/stripe'
bearer = {'Authorization': 'Bearer '+ensure_api_token()}
for method in ('GET', 'POST', 'DELETE'):
    response = client.request(method, url, headers=bearer, json={
        'enabled': True, 'account': 'acct_1234567890ABCDEF',
        'restricted_key': 'rk_test_synthetic123456789'
    })
    assert response.status_code == 403, (method, response.text)
session = create_dashboard_session()
r = client.get(url, headers={'Authorization':'OWG-Session '+session})
assert r.status_code == 200, r.text
body = r.json()
assert body['configured'] is False
assert body['credentials_exposed'] is False
assert 'restricted_key' not in str(body)
from server import local_source_routes as routes
calls = []
routes.configure_stripe_source = lambda account, key: calls.append(('connect',account,key))
routes.disconnect_stripe_source = lambda: calls.append(('disconnect',))
routes.stripe_source_status = lambda: {
    'configured': True, 'provider': 'stripe', 'credential_storage': 'native_user_keychain'
}
proper = {'Authorization': 'OWG-Session '+session}
result = client.post(url, headers=proper, json={
    'enabled': True, 'account': 'acct_1234567890ABCDEF',
    'restricted_key': 'rk_test_synthetic123456789'
})
assert result.status_code == 200, result.text
assert len(calls) == 1 and calls[0][0] == 'connect'
assert 'rk_test_synthetic' not in result.text
assert result.json()['credentials_exposed'] is False
revoke = client.delete(url, headers=proper)
assert revoke.status_code == 200, revoke.text
assert calls[-1] == ('disconnect',)
assert 'rk_test_synthetic' not in revoke.text
"""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
    })
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=80 if os.name == "nt" else 40,
    )
    assert result.returncode == 0, result.stderr[-2500:]


def test_dashboard_ux_has_explicit_password_entry_no_key_readback():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "dashboard/gateway_panel.js").read_text()
    assert "openLocalStripeSource" in script
    assert 'type="password"' in script
    assert "OWG-Session" not in script  # shared session transport supplies it
    assert "restricted_key:secret.value" in script
