"""Shared helpers for the Gateway identity tests (admins, roster, SSO, /me)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gateway.admin_accounts import _totp_at
from gateway.hardening import HardeningSettings, PooledGatewayDB
from gateway.human_access import HumanAccessSettings
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.settings import GatewaySettings

ADMIN_TOKEN = "admin-" + "x" * 40
LEGACY_ENROLL = "enroll-" + "y" * 40
BOOT = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
PASSWORD = "correct horse battery"
GATEWAY = "https://gw.acme.test"


def make_app(tmp_path: Path, **kwargs: Any):
    url = f"sqlite:///{tmp_path / 'gateway.db'}"
    db = PooledGatewayDB(url)
    app = create_human_enterprise_app(
        settings=GatewaySettings(database_url=url, admin_token=ADMIN_TOKEN, enrollment_token=LEGACY_ENROLL),
        hardening=HardeningSettings(),
        human_access=kwargs.pop("human_access", HumanAccessSettings()),
        db=db,
        **kwargs,
    )
    return app, db


class Totp:
    """Produces a fresh, not-yet-used authenticator code for a secret."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.step = int(time.time() // 30) - 1

    def next(self) -> str:
        self.step += 1
        return _totp_at(self.secret, self.step)


def bootstrap_owner(client: TestClient, email: str = "owner@acme.se") -> tuple[dict[str, str], Totp]:
    created = client.post("/v1/admin/auth/bootstrap", json={"bootstrap_token": ADMIN_TOKEN, "email": email, "display_name": "Olga Owner"})
    assert created.status_code == 200, created.text
    return activate(client, created.json()["setup_token"])


def activate(client: TestClient, setup_token: str, password: str = PASSWORD) -> tuple[dict[str, str], Totp]:
    started = client.post("/v1/admin/auth/setup/start", json={"setup_token": setup_token})
    assert started.status_code == 200, started.text
    totp = Totp(started.json()["totp_secret"])
    done = client.post("/v1/admin/auth/setup/complete", json={"setup_token": setup_token, "password": password, "totp_code": totp.next()})
    assert done.status_code == 200, done.text
    return {"Authorization": "Bearer " + done.json()["session_token"]}, totp


def add_admin(client: TestClient, owner: dict[str, str], email: str, role: str) -> tuple[dict[str, str], Totp, str]:
    created = client.post("/v1/admin/accounts", headers=owner, json={"email": email, "display_name": email.split("@")[0], "role": role})
    assert created.status_code == 200, created.text
    headers, totp = activate(client, created.json()["setup_token"])
    return headers, totp, created.json()["admin_id"]


def add_employee(client: TestClient, admin: dict[str, str], email: str, teams: list[str] | None = None, org: str = "acme") -> dict[str, Any]:
    r = client.post(f"/v1/admin/employees/{org}", headers=admin, json={"email": email, "display_name": email.split("@")[0].title(), "teams": teams or []})
    assert r.status_code == 200, r.text
    return r.json()


def personal_invite(client: TestClient, admin: dict[str, str], employee_id: str, org: str = "acme", **body: Any) -> dict[str, Any]:
    r = client.post(f"/v1/admin/employees/{org}/{employee_id}/invites", headers=admin,
                    json={"organization_name": "Acme AB", "gateway_url": GATEWAY, **body})
    assert r.status_code == 200, r.text
    return r.json()


def enroll(client: TestClient, token: str, device: str, *, actor: str = "", org: str = "acme"):
    return client.post("/v1/devices/enroll", headers={"Authorization": "Bearer " + token},
                       json={"organization_id": org, "actor_id": actor, "device_id": device})


def ingest(client: TestClient, device_token: str, event_id: str, title: str = "Inbox") -> None:
    r = client.post("/v1/evidence/batch", headers={"Authorization": "Bearer " + device_token}, json={"events": [{
        "event_id": event_id, "observed_at": "2026-09-27T10:00:00Z", "event_type": "focus_span",
        "app": "Outlook", "window_title": title, "duration_seconds": 5,
    }]})
    assert r.status_code == 200, r.text
