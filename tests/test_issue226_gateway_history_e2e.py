"""Issue #226: production Gateway API + local historical consent integration.

Unlike mocked receipt tests, this runs the real Gateway FastAPI app and DB,
one-time grant enrollment, real device redaction, evidence ingestion, actor
scope, revocation, and the history preview/confirmation state machine. HTTP
transport is routed in-process; no customer credentials or external network.
"""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from connector import history_backfill, service
from connector.state import SyncState
from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings


GW = "https://seeded-gateway.invalid"
ADMIN = {"Authorization": "Bearer admin-secret"}


def _create_local_db(path, *, count=3):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, event_id TEXT, "
            "observed_at TEXT, schema_version TEXT, source TEXT, device_id TEXT, "
            "sensor_id TEXT, session_id TEXT, app TEXT, window_title TEXT, "
            "event_type TEXT, duration_seconds REAL, metadata_json TEXT)"
        )
        for n in range(1, count + 1):
            meta = {
                "action": "click",
                "page": {"hostname": "mail.example.com", "pathname": "/private/customer",
                         "preview": "Patient: Karl Gustafsson, 54 år"},
                "target": {"role": "button", "label": "Svar till Michaela"},
                "typed_value": "Karl Gustafsson",
            }
            if n == 3:
                meta["excluded"] = True
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (n, "historic-" + str(n), yesterday, "1.0", "browser_extension",
                 "device-sensor", "browser:sensor", "session-" + str(n),
                 "Google Chrome", "Gmail · Katarina kommenterade avtalet",
                 "browser_click", 0, json.dumps(meta)),
            )


class _InProcessGatewayClient:
    """HTTPX-compatible transport using the real Gateway route/auth handlers."""

    gateway = None

    def __init__(self, *args, **kwargs):
        # HTTPX clients accept default Authorization headers at construction.
        # Preserve them across calls; otherwise the test bridge falsely turns
        # a legitimately enrolled device into a Gateway 401.
        self.headers = dict(kwargs.get("headers") or {})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @classmethod
    def _path(cls, url):
        assert url.startswith(GW + "/"), "unexpected source origin"
        return url[len(GW):]

    def _request(self, method, url, **kwargs):
        headers = {**self.headers, **dict(kwargs.pop("headers", {}) or {})}
        return getattr(self.gateway, method)(self._path(url), headers=headers, **kwargs)

    def get(self, url, **kwargs):
        return self._request("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("post", url, **kwargs)


def _fixture(tmp_path, monkeypatch, *, count=3):
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    data.mkdir()
    auth.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("OWG_REQUIRE_DEVICE_REDACTION", "true")
    monkeypatch.delenv("OWG_GATEWAY_DEVICE_TOKEN", raising=False)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    _create_local_db(data / "workflow_observer.db", count=count)
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.sqlite"))
    app = create_app(
        settings=GatewaySettings(database_url=db.database_url,
                                 admin_token="admin-secret",
                                 enrollment_token="enroll-secret"),
        db=db,
    )
    return config, data, app


def _reader(gw, organization):
    response = gw.post("/v1/admin/integration-tokens", headers=ADMIN, json={
        "organization_id": organization, "actor_id": "worker-1",
        "scopes": ["evidence:read"], "label": "read-only-fixture",
    })
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def _onboard(gw, config):
    grant_response = gw.post("/v1/admin/enrollment-codes", headers=ADMIN,
                             json={"organization_id": "org-a",
                                   "actor_id": "worker-1", "expires_minutes": 30})
    assert grant_response.status_code == 200, grant_response.text
    grant = grant_response.json()
    result = service.enroll_endpoint(
        config, gateway_url=GW, organization_id="org-a",
        actor_id="worker-1", enrollment_token=grant["token"],
    )
    assert result["pre_enrollment_rows_kept_local"] >= 1
    assert result["history_sync_mode"] == "from_enrollment_forward"
    replay = gw.post("/v1/devices/enroll",
                     headers={"Authorization": "Bearer " + grant["token"]},
                     json={"organization_id": "org-a", "actor_id": "worker-1",
                           "device_id": result["device_id"] + "-replay"})
    assert replay.status_code == 401, replay.text
    return result


def test_real_gateway_opt_in_redaction_scope_revoke_and_no_forward_cursor_rewind(tmp_path, monkeypatch):
    config, data, app = _fixture(tmp_path, monkeypatch)
    with TestClient(app) as gw:
        bridge = _InProcessGatewayClient
        bridge.gateway = gw
        # Route external Gateway HTTP requests through the actual API and DB.
        # The TestClient is created before swapping the HTTPX constructor.
        monkeypatch.setattr(httpx, "Client", bridge)
        enrolled = _onboard(gw, config)
        reader = _reader(gw, "org-a")
        other_org = _reader(gw, "org-b")
        state = SyncState(data / "gateway_sync_state.db")
        assert state.get_int("last_local_event_id") == 3
        assert state.get_int("history_enrollment_boundary_event_id") == 3

        # Ordinary enrollment never uploads older retained work.
        response = gw.get("/v1/workflow-trace", headers=reader)
        assert response.status_code == 200
        assert response.json()["returned"] == 0

        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        until = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with pytest.raises(PermissionError):
            history_backfill.preview(config, since, until)
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 0

        state.add_skip_range(2, 2, "user-paused-capture")
        monkeypatch.setenv("OWG_ENABLE_HISTORY_BACKFILL", "true")
        p = history_backfill.preview(config, since, until)
        assert p["scanned"] == 3
        assert p["eligible"] == 1
        assert p["excluded"] == 2
        assert p["no_upload_performed"]
        assert state.get_int("last_local_event_id") == 3
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 0

        sent = history_backfill.confirm(
            config, since, until,
            confirmation_token=p["confirmation_token"],
            explicit_confirmation=history_backfill._CONFIRMATION,
        )
        assert sent["acknowledged"] == 1
        assert sent["normal_sync_cursor_unchanged"]
        assert state.get_int("last_local_event_id") == 3
        res = gw.get("/v1/workflow-trace", headers=reader)
        assert res.status_code == 200, res.text
        assert res.json()["returned"] == 1, res.text
        full = json.dumps(res.json(), ensure_ascii=False)
        for secret in ("Karl Gustafsson", "Katarina", "Michaela", "/private/customer",
                       "typed_value", '"preview":'):
            assert secret not in full
        assert gw.get("/v1/workflow-trace", headers=other_org).json()["returned"] == 0

        with pytest.raises(PermissionError):
            history_backfill.confirm(
                config, since, until,
                confirmation_token=p["confirmation_token"],
                explicit_confirmation=history_backfill._CONFIRMATION,
            )

        # A remote revoke between preview and consent must block the new upload.
        pending = history_backfill.preview(config, since, until)
        from connector.config import load_device_token, load_gateway_settings
        token = load_device_token(load_gateway_settings(config, auth_dir=tmp_path / "auth"))
        revoked = gw.post("/v1/device/revoke", headers={"Authorization": "Bearer " + token})
        assert revoked.status_code == 200 and revoked.json()["revoked"]
        # FastAPI TestClient uses a separate httpx transport implementation,
        # whose HTTPStatusError is not the same class as httpx.HTTPStatusError.
        # Assert the meaningful security contract: remote revoke returns 401.
        with pytest.raises(Exception) as revoked_error:
            history_backfill.confirm(
                config, since, until,
                confirmation_token=pending["confirmation_token"],
                explicit_confirmation=history_backfill._CONFIRMATION,
            )
        assert getattr(getattr(revoked_error.value, "response", None),
                       "status_code", None) == 401
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 1
        assert state.get_int("last_local_event_id") == 3


def test_live_gateway_policy_change_invalidates_explicit_history_consent(tmp_path, monkeypatch):
    config, data, app = _fixture(tmp_path, monkeypatch, count=1)
    with TestClient(app) as gw:
        _InProcessGatewayClient.gateway = gw
        monkeypatch.setattr(httpx, "Client", _InProcessGatewayClient)
        _onboard(gw, config)
        reader = _reader(gw, "org-a")
        monkeypatch.setenv("OWG_ENABLE_HISTORY_BACKFILL", "1")
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        until = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        p = history_backfill.preview(config, since, until)
        assert p["eligible"] == 1
        # Enforce policy through real admin route, not a mocked function.
        changed = gw.put("/v1/admin/policy/org-a", headers=ADMIN, json={
            "policy": {"share_window_titles": False},
        })
        assert changed.status_code == 200, changed.text
        with pytest.raises(PermissionError, match="changed"):
            history_backfill.confirm(
                config, since, until,
                confirmation_token=p["confirmation_token"],
                explicit_confirmation=history_backfill._CONFIRMATION,
            )
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 0
        assert SyncState(data / "gateway_sync_state.db").get_int("last_local_event_id") == 1


def test_interrupted_historical_upload_requires_new_consent_and_deduplicates(tmp_path, monkeypatch):
    """Partial Gateway writes are not silently retried after a failed upload."""
    from connector.sync import _push_batch_resilient

    config, data, app = _fixture(tmp_path, monkeypatch, count=2)
    with TestClient(app) as gw:
        _InProcessGatewayClient.gateway = gw
        monkeypatch.setattr(httpx, "Client", _InProcessGatewayClient)
        _onboard(gw, config)
        reader = _reader(gw, "org-a")
        monkeypatch.setenv("OWG_ENABLE_HISTORY_BACKFILL", "1")

        # Force separate remote batches, so failure happens after one ACK.
        root = json.loads(config.read_text(encoding="utf-8"))
        root["gateway"]["batch_size"] = 1
        config.write_text(json.dumps(root), encoding="utf-8")
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        until = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        first = history_backfill.preview(config, since, until)
        assert first["eligible"] == 2
        calls = []

        def interrupted_sender(client, url, events):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("synthetic in-transit failure after first ACK")
            return _push_batch_resilient(client, url, events)

        with pytest.raises(RuntimeError, match="synthetic in-transit failure"):
            history_backfill.confirm(
                config, since, until,
                confirmation_token=first["confirmation_token"],
                explicit_confirmation=history_backfill._CONFIRMATION,
                batch_sender=interrupted_sender,
            )
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 1
        assert SyncState(data / "gateway_sync_state.db").get_int("last_local_event_id") == 2

        # Old approval cannot be replayed; operator must preview and confirm.
        with pytest.raises(PermissionError):
            history_backfill.confirm(
                config, since, until,
                confirmation_token=first["confirmation_token"],
                explicit_confirmation=history_backfill._CONFIRMATION,
            )
        repeat = history_backfill.preview(config, since, until)
        assert repeat["eligible"] == 2
        done = history_backfill.confirm(
            config, since, until,
            confirmation_token=repeat["confirmation_token"],
            explicit_confirmation=history_backfill._CONFIRMATION,
        )
        assert done["acknowledged"] == 2  # includes an idempotent prior ACK
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 2
        assert SyncState(data / "gateway_sync_state.db").get_int("last_local_event_id") == 2


def test_deactivated_discovery_study_never_reaches_seeded_gateway(tmp_path, monkeypatch):
    """Merged #239 exclusion must survive real enroll/preview/ingest/readback.

    A historical Discovery study stays private even after it was deactivated
    *before* Gateway enrollment. The source records have real local timestamps;
    the HTTP policy and evidence calls exercise production Gateway handlers.
    """
    config, data, app = _fixture(tmp_path, monkeypatch, count=2)
    midpoint = datetime.now(timezone.utc) - timedelta(days=2)
    study_start = midpoint - timedelta(minutes=30)
    study_end = midpoint + timedelta(minutes=30)

    # One ordinary event precedes the study; the other falls inside it.
    with sqlite3.connect(data / "workflow_observer.db") as conn:
        conn.execute("UPDATE events SET observed_at=? WHERE id=1",
                     ((midpoint - timedelta(hours=2)).isoformat(),))
        conn.execute("UPDATE events SET observed_at=? WHERE id=2",
                     (midpoint.isoformat(),))

    monkeypatch.setattr(history_backfill, "read_discovery_state", lambda **_: {
        "session_id": "private-study-before-company-enrollment",
        "enabled": False,
        "status": "inactive",
        "starts_at": study_start.isoformat(),
        "ends_at": (study_end + timedelta(days=1)).isoformat(),
        "deactivated_at": study_end.isoformat(),
    })
    with TestClient(app) as gw:
        _InProcessGatewayClient.gateway = gw
        monkeypatch.setattr(httpx, "Client", _InProcessGatewayClient)
        _onboard(gw, config)
        reader = _reader(gw, "org-a")
        state = SyncState(data / "gateway_sync_state.db")
        assert state.get_int("history_enrollment_boundary_event_id") == 2
        monkeypatch.setenv("OWG_ENABLE_HISTORY_BACKFILL", "1")

        since = (midpoint - timedelta(days=1)).isoformat()
        until = (midpoint + timedelta(days=1)).isoformat()
        preview = history_backfill.preview(config, since, until)
        assert preview["scanned"] == 2
        assert preview["eligible"] == 1
        assert preview["excluded"] == 1
        assert gw.get("/v1/workflow-trace", headers=reader).json()["returned"] == 0

        confirmed = history_backfill.confirm(
            config, since, until,
            confirmation_token=preview["confirmation_token"],
            explicit_confirmation=history_backfill._CONFIRMATION,
        )
        assert confirmed["acknowledged"] == 1
        records = gw.get("/v1/workflow-trace", headers=reader)
        assert records.status_code == 200
        assert records.json()["returned"] == 1
        assert state.get_int("last_local_event_id") == 2
