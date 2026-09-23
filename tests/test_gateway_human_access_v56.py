from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from gateway.auth import Principal
from gateway.hardening import HardeningSettings
from gateway.human_access import HumanAccessSettings
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.settings import GatewaySettings


class FakeVerifier:
    def __init__(self, claims: dict[str, dict[str, Any]]) -> None:
        self.claims = claims

    def verify(self, token: str) -> dict[str, Any]:
        if token not in self.claims:
            raise ValueError("invalid token")
        return dict(self.claims[token])


def _settings(tmp_path):
    return GatewaySettings(
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        admin_token="admin-secret",
        enrollment_token="enrollment-secret",
    )


def _human_settings() -> HumanAccessSettings:
    return HumanAccessSettings(
        issuer="https://idp.example.test",
        audience="openworkgraph",
        jwks_url="https://idp.example.test/jwks",
        organization_claim="org",
        actor_claim="actor",
        groups_claim="groups",
        group_scope_map={
            "eng-managers": frozenset({"team:engineering:evidence:read"}),
            "org-auditors": frozenset({"org:evidence:read"}),
            "aggregate-analysts": frozenset({"aggregate:read"}),
            "pseudonymous-analysts": frozenset({"pseudonymous:evidence:read"}),
        },
        self_read_enabled=True,
        aggregate_min_actors=3,
        pseudonym_key="p" * 64,
    )


def _claims(actor: str, groups: list[str] | None = None) -> dict[str, Any]:
    return {
        "sub": f"subject-{actor}",
        "org": "acme",
        "actor": actor,
        "groups": groups or [],
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed(app, actors: list[str]) -> None:
    db = app.state.db
    for index, actor in enumerate(actors):
        principal = Principal(
            token_id=f"seed-{actor}",
            token_type="device",
            organization_id="acme",
            actor_id=actor,
            device_id=f"device-{actor}",
            scopes=frozenset({"evidence:write"}),
        )
        events = [
            {
                "event_id": f"common-{actor}",
                "observed_at": f"2026-09-22T10:{index:02d}:00+00:00",
                "event_type": "app_focus",
                "app": "Docs",
                "session_id": f"session-{actor}",
                "duration_seconds": 30,
                "metadata": {"safe": True},
            }
        ]
        if index < 2:
            events.append(
                {
                    "event_id": f"rare-{actor}",
                    "observed_at": f"2026-09-22T11:{index:02d}:00+00:00",
                    "event_type": "app_focus",
                    "app": "RareApp",
                    "session_id": f"session-{actor}",
                    "duration_seconds": 10,
                    "metadata": {},
                }
            )
        db.insert_events(principal, events)


def _app(tmp_path):
    verifier = FakeVerifier(
        {
            "alice": _claims("alice"),
            "manager": _claims("manager", ["eng-managers"]),
            "auditor": _claims("auditor", ["org-auditors"]),
            "aggregate": _claims("analyst", ["aggregate-analysts"]),
            "pseudo": _claims("analyst", ["pseudonymous-analysts"]),
        }
    )
    return create_human_enterprise_app(
        settings=_settings(tmp_path),
        hardening=HardeningSettings(),
        human_access=_human_settings(),
        verifier=verifier,
    )


def test_human_access_is_optional_and_does_not_replace_service_auth(tmp_path):
    app = create_human_enterprise_app(
        settings=_settings(tmp_path),
        hardening=HardeningSettings(),
        human_access=HumanAccessSettings(),
    )
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.56.0"
        assert health.json()["human_oidc_enabled"] is False

        human = client.get("/v1/human/me", headers=_headers("anything"))
        assert human.status_code == 503

        service = client.post(
            "/v1/admin/integration-tokens",
            headers=_headers("admin-secret"),
            json={"organization_id": "acme", "scopes": ["evidence:read"]},
        )
        assert service.status_code == 200
        token = service.json()["token"]
        trace = client.get("/v1/workflow-trace", headers=_headers(token))
        assert trace.status_code == 200


def test_self_team_and_org_scopes_are_enforced(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed(app, ["alice", "bob", "carol"])
        set_team = client.put(
            "/v1/admin/access/acme/actors/alice/teams",
            headers=_headers("admin-secret"),
            json={"teams": ["engineering"]},
        )
        assert set_team.status_code == 200

        own = client.get("/v1/human/workflow-trace", headers=_headers("alice"))
        assert own.status_code == 200
        assert own.json()["human_access_mode"] == "self"
        assert {row["actor_id"] for row in own.json()["rows"]} == {"alice"}

        forbidden = client.get(
            "/v1/human/workflow-trace?actor_id=bob",
            headers=_headers("alice"),
        )
        assert forbidden.status_code == 403

        team = client.get(
            "/v1/human/workflow-trace?actor_id=alice",
            headers=_headers("manager"),
        )
        assert team.status_code == 200
        assert team.json()["human_access_mode"] == "team"

        outside_team = client.get(
            "/v1/human/workflow-trace?actor_id=carol",
            headers=_headers("manager"),
        )
        assert outside_team.status_code == 403

        org = client.get(
            "/v1/human/workflow-trace?actor_id=carol",
            headers=_headers("auditor"),
        )
        assert org.status_code == 200
        assert org.json()["human_access_mode"] == "organization"


def test_aggregate_scope_enforces_k_and_never_returns_actor_ids(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed(app, ["alice", "bob", "carol"])
        response = client.get(
            "/v1/human/aggregate/patterns?min_actors=2",
            headers=_headers("aggregate"),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["minimum_actors"] == 3
        assert payload["actor_identifiers_returned"] is False
        assert any(row["app"] == "Docs" and row["actors"] == 3 for row in payload["patterns"])
        assert not any(row["app"] == "RareApp" for row in payload["patterns"])
        assert "actor_id" not in str(payload)


def test_aggregate_suppresses_entire_cohort_below_threshold(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed(app, ["alice", "bob"])
        response = client.get("/v1/human/aggregate/patterns", headers=_headers("aggregate"))
        assert response.status_code == 200
        payload = response.json()
        assert payload["suppressed"] is True
        assert payload["patterns"] == []
        assert payload["cohort_actor_count"] == 2


def test_pseudonymous_scope_replaces_identity_fields_but_does_not_claim_anonymity(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _seed(app, ["alice", "bob", "carol"])
        first = client.get("/v1/human/pseudonymous/workflow-trace", headers=_headers("pseudo"))
        second = client.get("/v1/human/pseudonymous/workflow-trace", headers=_headers("pseudo"))
        assert first.status_code == 200
        assert second.status_code == 200
        payload = first.json()
        assert payload["pseudonymized"] is True
        assert payload["pseudonymization_is_anonymization"] is False
        assert payload["rows"]
        for row in payload["rows"]:
            assert row["actor_id"].startswith("actor_")
            assert row["device_id"].startswith("device_")
            assert row["session_id"].startswith("session_")
            assert row["actor_id"] not in {"alice", "bob", "carol"}
        assert [row["actor_id"] for row in first.json()["rows"]] == [row["actor_id"] for row in second.json()["rows"]]


def test_human_me_exposes_effective_scope_not_raw_oidc_subject(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/human/me", headers=_headers("manager"))
        assert response.status_code == 200
        payload = response.json()
        assert payload["raw_subject_exposed"] is False
        assert payload["principal_id"].startswith("oidc:")
        assert "team:engineering:evidence:read" in payload["scopes"]
        assert "subject-manager" not in str(payload)
