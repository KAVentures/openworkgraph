"""Gateway 2FA setup QR works offline; reload never rotates a scanned secret."""
from __future__ import annotations

from fastapi.testclient import TestClient
from gateway_identity_helpers import ADMIN_TOKEN, PASSWORD, Totp, make_app
from pathlib import Path


def test_setup_start_is_idempotent_after_reload_and_activates(tmp_path):
    app,db=make_app(tmp_path)
    with TestClient(app) as client:
        created=client.post("/v1/admin/auth/bootstrap",json={
            "bootstrap_token":ADMIN_TOKEN,"email":"owner@acme.se",
        })
        assert created.status_code==200,created.text
        link=created.json()["setup_token"]
        first=client.post("/v1/admin/auth/setup/start",json={"setup_token":link})
        again=client.post("/v1/admin/auth/setup/start",json={"setup_token":link})
        assert first.status_code==again.status_code==200
        assert first.json()["totp_secret"]==again.json()["totp_secret"]
        assert first.json()["otpauth_uri"]==again.json()["otpauth_uri"]
        code=Totp(first.json()["totp_secret"]).next()
        complete=client.post("/v1/admin/auth/setup/complete",json={
            "setup_token":link,"password":PASSWORD,"totp_code":code,
        })
        assert complete.status_code==200,complete.text
        assert client.post("/v1/admin/auth/setup/start",json={"setup_token":link}).status_code==401


def test_offline_qr_asset_and_admin_csp_require_nonce(tmp_path):
    app,_db=make_app(tmp_path)
    with TestClient(app) as client:
        asset=client.get("/admin/qrcode.js")
        assert asset.status_code==200
        assert asset.headers["x-content-type-options"]=="nosniff"
        assert asset.headers["cache-control"]=="no-store"
        assert "QRCode" in asset.text
        page=client.get("/admin")
        assert page.status_code==200
        assert 'src="/admin/qrcode.js"' in page.text
        assert "new QRCode(qr" in page.text
        assert "img-src data:" in page.headers["content-security-policy"]
        assert "script-src 'nonce-" in page.headers["content-security-policy"]
        assert "owg_admin_pending_setup" in page.text
        assert "S.removeItem('owg_admin_pending_setup')" in page.text
        assert "qrcode.min.js" not in page.text


def test_mit_notice_bundled():
    root=Path(__file__).resolve().parents[1]
    license=(root/"gateway"/"qrcode.LICENSE").read_text()
    assert "MIT License" in license
    assert "davidshimjs" in license
    assert (root/"gateway"/"qrcode.min.js").stat().st_size>10000
