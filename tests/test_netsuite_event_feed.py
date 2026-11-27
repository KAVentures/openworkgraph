"""Synthetic NetSuite transport, actual RSA assertions and retained-feed regressions."""
from datetime import timedelta
import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from server import netsuite_event_feed as ns
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import enterprise_connector_operations as ops
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_NETSUITE_KEY_TEST"
TOKEN = "synthetic-ephemeral-access-token"
ACCOUNT = "123456-sb1"
HOST = ACCOUNT + ".suitetalk.api.netsuite.com"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def encode_key(key):
    return base64.b64encode(key.private_bytes(serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch, signing_key):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv(ENV, encode_key(signing_key))
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    source = {"account_domain": ACCOUNT, "client_id": "fixture-client",
              "certificate_id": "fixture-cert", "credential_env": ENV,
              "event_record": "customrecord_owg_events", "case_column": "custrecord_case",
              "activity_column": "custrecord_activity", "epoch_micros_column": "custrecord_micros",
              "resource_column": "custrecord_resource",
              "activity_labels": {"open": "Open", "close": "Close"}}
    monkeypatch.setenv(ns.CATALOG, json.dumps({"primary": source}))
    return source


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "NetSuite fixture", "source_alias": "primary",
            "source_sha256": ns.catalog_public()["sources"][0]["source_sha256"],
            "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
            "ingestion_lag_minutes": 15, "page_size": 1, "confirm_configuration": True,
            "confirm_source_read": True, "confirm_metadata_retention": True, **extra}


def decode_part(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_assertion(value, key):
    header, claims, signature = value.split(".")
    assert json.loads(decode_part(header)) == {"typ": "JWT", "alg": "PS256", "kid": "fixture-cert"}
    data = json.loads(decode_part(claims))
    assert data["iss"] == "fixture-client" and data["scope"] == ["rest_webservices"]
    assert data["aud"] == "https://" + HOST + ns.TOKEN_PATH
    assert data["exp"] - data["iat"] == 120 and len(data["jti"]) == 32
    key.public_key().verify(decode_part(signature), (header + "." + claims).encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                            hashes.SHA256())
    return data


def make_rows(profile):
    start = rest._stamp(profile["config"]["initial_since"])
    at = start + timedelta(hours=23, minutes=30)
    return [{"owg_event": str(i + 100), "owg_case": "private-case", "owg_activity": label,
             "owg_micros": str(ns.micros(at + timedelta(seconds=i))),
             "owg_resource": "private-resource", "links": []}
            for i, label in enumerate(("open", "close"))]


def run(profile, rows, key, *, alter=None, drift=False, token_overrides=None):
    seen = []
    scans = 0

    def handler(req):
        nonlocal scans
        assert req.url.host == HOST and req.method == "POST"
        seen.append(req.url.path)
        if req.url.path == ns.TOKEN_PATH:
            assert "authorization" not in req.headers
            form = parse_qs(req.content.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["client_assertion_type"] == ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"]
            verify_assertion(form["client_assertion"][0], key)
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer",
                                            "expires_in": 3600, **(token_overrides or {})})
        assert req.url.path == ns.QUERY_PATH
        assert req.headers["authorization"] == "Bearer " + TOKEN
        assert req.headers["prefer"] == "transient"
        size, offset = int(req.url.params["limit"]), int(req.url.params["offset"])
        assert size == profile["config"]["page_size"]
        query = json.loads(req.content)["q"]
        assert query.startswith("SELECT id AS owg_event,")
        assert query.endswith("ORDER BY custrecord_micros, id")
        assert "FROM customrecord_owg_events WHERE custrecord_micros >= " in query
        if offset == 0:
            scans += 1
        items = [dict(r) for r in rows[offset:offset + size]]
        if drift and scans == 2 and items:
            items[0]["owg_case"] = "changed-case"
        payload = {"count": len(items), "offset": offset, "totalResults": len(rows),
                   "hasMore": offset + len(items) < len(rows), "items": items,
                   "links": [{"rel": "next", "href": "https://untrusted.invalid/never-follow"}]}
        if alter:
            alter(payload, offset, scans)
        return httpx.Response(200, json=payload)

    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_ingestion_fresh_grant_double_scan_and_replay(signing_key):
    p = ns.create(config())
    rows = make_rows(p)
    first, requests = run(p, rows, signing_key)
    assert first["events_added"] == 2
    assert requests == [ns.TOKEN_PATH] + [ns.QUERY_PATH] * 4
    assert first["last_result"]["repeat_scan_agreement"]
    assert first["last_result"]["transaction_snapshot_verified"] is False
    assert first["last_result"]["independent_role_readback_verified"] is False
    second, requests = run(first, rows, signing_key)
    assert requests[0] == ns.TOKEN_PATH
    assert second["events_added"] == 0 and second["duplicates_skipped"] == 2
    assert ws.list_logs()["logs"][0]["events"] == 2
    with ws._LOCK:
        c = rest._connect()
        try:
            raw = str(c.execute("SELECT * FROM events").fetchall()) + str(c.execute("SELECT * FROM objects").fetchall())
            stored = str(rest._get(c, p["profile_id"]))
        finally:
            c.close()
    assert all(x not in raw + stored for x in (TOKEN, "private-case", "private-resource", encode_key(signing_key)))
    assert rest.schedule_pin(p["profile_id"]) == p["revision"]
    assert capabilities("netsuite")["exact_snapshot_publication"]
    assert any(x["provider"] == "netsuite" for x in ops.inventory()["retained_adapter_capabilities"])


def test_empty_window_advances_checkpoint_without_dataset(signing_key):
    p = ns.create(config())
    report, requests = run(p, [], signing_key)
    assert report["events_added"] == 0 and report["checkpoint"]
    assert report["log_id"] is None
    assert requests == [ns.TOKEN_PATH, ns.QUERY_PATH, ns.QUERY_PATH]


@pytest.mark.parametrize("alter", [
    lambda p, o, s: p.update(totalResults=99999),
    lambda p, o, s: p.update(count=0),
    lambda p, o, s: p.update(offset=99),
    lambda p, o, s: p.update(hasMore=False),
    lambda p, o, s: p["items"][0].update(unknown="secret"),
    lambda p, o, s: p.update(totalResults=3) if o else None,
])
def test_bad_pages_never_advance_checkpoint(alter, signing_key):
    p = ns.create(config())
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), signing_key, alter=alter)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


@pytest.mark.parametrize("kind", ["duplicate", "unapproved", "outside", "null_case", "drift", "reverse", "fraction"])
def test_semantic_refusals(kind, signing_key):
    p = ns.create(config())
    rows = make_rows(p)
    if kind == "duplicate":
        rows[1]["owg_event"] = rows[0]["owg_event"]
    elif kind == "unapproved":
        rows[0]["owg_activity"] = "unknown"
    elif kind == "outside":
        rows[0]["owg_micros"] = "0"
    elif kind == "null_case":
        rows[0]["owg_case"] = None
    elif kind == "reverse":
        rows.reverse()
    elif kind == "fraction":
        rows[0]["owg_micros"] += ".1"
    with pytest.raises(rest.FeedError):
        run(p, rows, signing_key, drift=kind == "drift")
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


@pytest.mark.parametrize("override", [
    {"token_type": "other"}, {"expires_in": 1}, {"access_token": ""},
    {"scope": "rest_webservices,restlets"},
])
def test_invalid_grant_refused_before_query(override, signing_key):
    p = ns.create(config())
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), signing_key, token_overrides=override)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_assertions_unique_and_passive_configuration(signing_key, monkeypatch, setup):
    assert ns.catalog_public()["provider_reads_performed"] is False
    src = ns.catalog()["primary"]
    a = verify_assertion(ns.assertion(src, signing_key), signing_key)
    b = verify_assertion(ns.assertion(src, signing_key), signing_key)
    assert a["jti"] != b["jti"]
    with pytest.raises(rest.FeedError):
        ns.create(config(confirm_source_read=False))
    p = ns.create(config())
    monkeypatch.setenv(ENV, encode_key(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, encode_key(signing_key))
    setup["certificate_id"] = "other-cert"
    monkeypatch.setenv(ns.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("account_domain", "fixture.suitetalk.api.netsuite.com"),
    ("event_record", "customrecord_events; DELETE FROM other"),
    ("case_column", "custrecord_activity"),
    ("activity_labels", {"open": "Same", "close": "Same"}),
])
def test_bad_deployment_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(ns.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_netsuite_deployment_catalog"):
        ns.catalog()
