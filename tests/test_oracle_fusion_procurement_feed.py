"""Oracle Fusion procurement exact selected-header transport and retention tests."""
import base64
from datetime import timedelta
import json

import httpx
import pytest

from server import oracle_fusion_procurement_feed as oracle
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import enterprise_connector_operations as ops
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_ORACLE_FUSION_PASSWORD_TEST"
PASSWORD = "fixture-secret-password"
HOST = "fixture.fa.us2.oraclecloud.com"
BU = "9007199254740993"
REQ = "9007199254740995"
PO = "9007199254740997"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv(ENV, PASSWORD)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    src = {"host": HOST, "integration_user": "owg_readonly", "credential_env": ENV,
           "requisition_business_units": [int(BU)], "purchase_order_business_units": [int(BU)]}
    monkeypatch.setenv(oracle.CATALOG, json.dumps({"primary": src}))
    return src


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "Oracle procurement fixture", "source_alias": "primary",
            "source_sha256": oracle.catalog_public()["sources"][0]["source_sha256"],
            "selections": [{"object_type": kind, "business_unit_id": BU, "header_ids": [rid],
                            "include_created": True, "include_milestone": True,
                            "include_latest_update": True}
                           for kind, rid in (("requisition", REQ), ("purchase_order", PO))],
            "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
            "ingestion_lag_minutes": 15, "confirm_configuration": True,
            "confirm_source_read": True, "confirm_snapshot_semantics": True,
            "confirm_metadata_retention": True, **extra}


def run(profile, *, change=None, empty_window=False):
    seen = []
    def handler(req):
        assert req.method == "GET" and req.url.host == HOST
        assert req.headers["authorization"] == "Basic " + base64.b64encode(
            ("owg_readonly:" + PASSWORD).encode()).decode()
        assert req.headers["rest-framework-version"] == "4"
        seen.append(req.url.path)
        kind = next(k for k, v in oracle.RESOURCES.items() if req.url.path == oracle.BASE + v[0])
        selection = next(s for s in profile["config"]["selections"] if s["object_type"] == kind)
        _, identity, bu, milestone, _ = oracle.RESOURCES[kind]
        rid = selection["header_ids"][0]
        assert req.url.params["q"] == f"{identity}={rid};{bu}={BU}"
        assert req.url.params["fields"] == ",".join(oracle.fields(selection))
        assert req.url.params["onlyData"] == "true" and req.url.params["limit"] == "2"
        at = rest._stamp(profile["config"]["initial_since"]) + timedelta(hours=23, minutes=30)
        if empty_window:
            at -= timedelta(days=1)
        item = {identity: int(rid), bu: int(BU), "CreationDate": rest._iso(at)}
        if selection["include_milestone"]:
            item[milestone] = rest._iso(at + timedelta(seconds=2))
        if selection["include_latest_update"]:
            item["LastUpdateDate"] = rest._iso(at + timedelta(seconds=3))
        payload = {"items": [item], "count": 1, "hasMore": False, "offset": 0, "limit": 2}
        if change:
            overridden = change(payload, len(seen), kind, identity, bu, milestone)
            if isinstance(overridden, httpx.Response):
                return overridden
        return httpx.Response(200, json=payload)
    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_two_resources_replay_and_privacy():
    p = oracle.create(config())
    assert p["config"]["selections"][0]["business_unit_id"] == BU
    first, paths = run(p)
    assert len(paths) == 4 and first["events_added"] == 6
    assert first["last_result"]["repeat_scan_agreement"]
    assert first["last_result"]["mutable_header_dates_are_observations"]
    assert first["last_result"]["provider_completeness_verified"] is False
    second, _ = run(first)
    assert second["events_added"] == 0 and second["duplicates_skipped"] == 6
    assert ws.list_logs()["logs"][0]["events"] == 6
    with ws._LOCK:
        c = rest._connect()
        try:
            raw = str(c.execute("SELECT * FROM events").fetchall()) + str(c.execute("SELECT * FROM objects").fetchall())
        finally:
            c.close()
    assert all(x not in raw for x in (PASSWORD, BU, REQ, PO))
    assert "oracle_fusion.requisition.submission_date_observed" in raw
    assert "oracle_fusion.purchase_order.communication_date_observed" in raw
    assert rest.schedule_pin(p["profile_id"]) == p["revision"]
    assert capabilities("oracle_fusion")["exact_snapshot_publication"]
    assert any(x["provider"] == "oracle_fusion" for x in ops.inventory()["retained_adapter_capabilities"])


def test_empty_window_advances_checkpoint():
    p = oracle.create(config())
    report, paths = run(p, empty_window=True)
    assert len(paths) == 4 and report["events_added"] == 0
    assert report["checkpoint"] and report["log_id"] is None


def test_nullable_milestones_and_same_creation_update_not_separate_events():
    p = oracle.create(config())
    def change(payload, step, kind, identity, bu, milestone):
        item = payload["items"][0]
        item[milestone] = None
        item["LastUpdateDate"] = item["CreationDate"]
    result, _ = run(p, change=change)
    assert result["events_added"] == 2


@pytest.mark.parametrize("kind", ["wrong_header", "wrong_bu", "bool_id", "missing", "ambiguous",
                                  "extra_field", "more", "count", "offset", "drift",
                                  "naive", "precision", "before_creation", "redirect", "rate_limit"])
def test_failed_window_has_no_checkpoint(kind):
    p = oracle.create(config())
    def change(payload, step, resource, identity, bu, milestone):
        row = payload["items"][0]
        if kind == "wrong_header":
            row[identity] += 1
        elif kind == "wrong_bu":
            row[bu] += 1
        elif kind == "bool_id":
            row[identity] = True
        elif kind == "missing":
            payload["items"] = []
            payload["count"] = 0
        elif kind == "ambiguous":
            payload["items"].append(row.copy())
            payload["count"] = 2
        elif kind == "extra_field":
            row["Supplier"] = "private"
        elif kind == "more":
            payload["hasMore"] = True
        elif kind == "count":
            payload["count"] = True
        elif kind == "offset":
            payload["offset"] = 1
        elif kind == "drift" and step == 3:
            row[milestone] = rest._iso(rest._stamp(row[milestone]) + timedelta(seconds=1))
        elif kind == "naive":
            row["CreationDate"] = row["CreationDate"].rstrip("Z")
        elif kind == "precision":
            row["CreationDate"] = row["CreationDate"].rstrip("Z") + ".1234567Z"
        elif kind == "before_creation":
            row[milestone] = "2001-01-01T00:00:00Z"
        elif kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        elif kind == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "120"})
    with pytest.raises(rest.FeedError):
        run(p, change=change)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


def test_exact_scope_consent_and_binding(monkeypatch, setup):
    assert oracle.catalog_public()["provider_reads_performed"] is False
    with pytest.raises(rest.FeedError):
        oracle.create(config(confirm_snapshot_semantics=False))
    bad = config()
    bad["selections"][0]["business_unit_id"] = "123"
    with pytest.raises(rest.FeedError, match="business_unit_not_approved"):
        oracle.create(bad)
    bad = config()
    bad["selections"][0]["business_unit_id"] = int(BU)
    with pytest.raises(rest.FeedError, match="invalid_oracle_fusion_business_unit_id"):
        oracle.create(bad)
    bad = config()
    bad["selections"][0]["header_ids"] = ["1;StatusCode=OPEN"]
    with pytest.raises(rest.FeedError):
        oracle.create(bad)
    p = oracle.create(config())
    monkeypatch.setenv(ENV, "different-password")
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, PASSWORD)
    setup["integration_user"] = "other"
    monkeypatch.setenv(oracle.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("host", "fixture.fa.us2.oraclecloud.com.evil.invalid"),
    ("host", "https://fixture.fa.us2.oraclecloud.com"),
    ("integration_user", "bad:user"),
    ("requisition_business_units", [True]),
    ("purchase_order_business_units", [1, 1]),
])
def test_bad_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(oracle.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_oracle_fusion_catalog"):
        oracle.catalog()
