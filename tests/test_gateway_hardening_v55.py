from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.enterprise_app import create_enterprise_app
from gateway.hardening import HardeningSettings, PooledGatewayDB, SlidingWindowRateLimiter
from gateway.settings import PRODUCT_VERSION, GatewaySettings


def _settings(tmp_path) -> GatewaySettings:
    return GatewaySettings(
        database_url=f"sqlite:///{tmp_path / 'gateway-v55.db'}",
        admin_token="test-admin-token",
        enrollment_token="test-enrollment-token",
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sliding_window_zero_is_disabled_and_enabled_limit_is_deterministic():
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    for _ in range(100):
        assert limiter.check("disabled", 0, now=1.0) == (True, 0)

    assert limiter.check("service", 2, now=10.0) == (True, 0)
    assert limiter.check("service", 2, now=11.0) == (True, 0)
    allowed, retry = limiter.check("service", 2, now=12.0)
    assert allowed is False
    assert retry == 58
    assert limiter.check("service", 2, now=71.0) == (True, 0)


def test_pooling_is_postgres_only_and_opt_in():
    direct = PooledGatewayDB("postgresql://example.invalid/db")
    assert direct.pooling_enabled is False

    pooled = PooledGatewayDB(
        "postgresql://example.invalid/db",
        pool_min_size=1,
        pool_max_size=10,
    )
    assert pooled.pooling_enabled is True
    assert pooled.pool_min_size == 1
    assert pooled.pool_max_size == 10

    sqlite = PooledGatewayDB("sqlite:///:memory:", pool_min_size=1, pool_max_size=10)
    assert sqlite.pooling_enabled is False


def test_admin_inventory_never_exposes_secrets_and_device_revoke_keeps_evidence(tmp_path):
    app = create_enterprise_app(settings=_settings(tmp_path), hardening=HardeningSettings())
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == PRODUCT_VERSION
        assert health.json()["database_pooling"] is False

        enrolled = client.post(
            "/v1/devices/enroll",
            headers=_auth("test-enrollment-token"),
            json={"organization_id": "acme", "actor_id": "alice", "device_id": "alice-laptop"},
        )
        assert enrolled.status_code == 200, enrolled.text
        device_token = enrolled.json()["token"]
        device_token_id = enrolled.json()["token_id"]

        service = client.post(
            "/v1/admin/integration-tokens",
            headers=_auth("test-admin-token"),
            json={
                "organization_id": "acme",
                "actor_id": "",
                "label": "test integration",
                "scopes": ["evidence:read", "context:read"],
            },
        )
        assert service.status_code == 200, service.text
        service_token = service.json()["token"]
        service_token_id = service.json()["token_id"]

        inventory = client.get(
            "/v1/admin/tokens/acme?include_revoked=true",
            headers=_auth("test-admin-token"),
        )
        assert inventory.status_code == 200, inventory.text
        items = inventory.json()["items"]
        assert {item["token_id"] for item in items} == {device_token_id, service_token_id}
        assert inventory.json()["secrets_exposed"] is False
        for item in items:
            assert "token" not in item
            assert "token_hash" not in item
            assert "scopes_json" not in item
            assert isinstance(item["scopes"], list)

        devices = client.get("/v1/admin/devices/acme", headers=_auth("test-admin-token"))
        assert devices.status_code == 200
        assert devices.json()["returned"] == 1
        assert devices.json()["items"][0]["device_id"] == "alice-laptop"
        assert devices.json()["items"][0]["active"] is True

        revoke = client.delete(
            "/v1/admin/devices/acme/alice-laptop",
            headers=_auth("test-admin-token"),
        )
        assert revoke.status_code == 200
        assert revoke.json()["revoked_credentials"] == 1
        assert revoke.json()["evidence_deleted"] is False

        # Device auth is gone, while the independent integration credential
        # remains valid and no evidence lifecycle operation was invoked.
        assert client.get("/v1/device-policy", headers=_auth(device_token)).status_code == 401
        assert client.get("/v1/workflow-trace", headers=_auth(service_token)).status_code == 200

        revoked_devices = client.get(
            "/v1/admin/devices/acme?include_revoked=true",
            headers=_auth("test-admin-token"),
        )
        assert revoked_devices.status_code == 200
        assert revoked_devices.json()["items"][0]["active"] is False


def test_principal_rate_limit_is_opt_in_and_credential_scoped(tmp_path):
    hardening = HardeningSettings(principal_rate_limit_per_minute=2)
    app = create_enterprise_app(settings=_settings(tmp_path), hardening=hardening)
    with TestClient(app) as client:
        service = client.post(
            "/v1/admin/integration-tokens",
            headers=_auth("test-admin-token"),
            json={
                "organization_id": "acme",
                "scopes": ["evidence:read"],
                "label": "limited service",
            },
        )
        assert service.status_code == 200
        token = service.json()["token"]

        assert client.get("/v1/workflow-trace", headers=_auth(token)).status_code == 200
        assert client.get("/v1/workflow-trace", headers=_auth(token)).status_code == 200
        limited = client.get("/v1/workflow-trace", headers=_auth(token))
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1

        # Public health/capability probes are intentionally not consumed by a
        # credential bucket, so load balancer health checks remain stable.
        assert client.get("/health").status_code == 200
        assert client.get("/v1/capabilities").status_code == 200


def test_runtime_status_reports_configuration_without_secrets(tmp_path):
    hardening = HardeningSettings(
        postgres_pool_min_size=1,
        postgres_pool_max_size=10,
        principal_rate_limit_per_minute=600,
        admin_rate_limit_per_minute=120,
        enrollment_rate_limit_per_minute=60,
    )
    app = create_enterprise_app(settings=_settings(tmp_path), hardening=hardening)
    with TestClient(app) as client:
        runtime = client.get("/v1/admin/runtime", headers=_auth("test-admin-token"))
        assert runtime.status_code == 200
        body = runtime.json()
        # SQLite development ignores PostgreSQL pool configuration.
        assert body["database_pooling"]["enabled"] is False
        assert body["rate_limits_per_minute"] == {
            "principal": 600,
            "admin": 120,
            "enrollment": 60,
        }
        assert body["secrets_exposed"] is False
        assert "admin_token" not in body
        assert "enrollment_token" not in body
