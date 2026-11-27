"""Synthetic BigQuery REST acceptance, real JWT signature and retained replay."""
from datetime import timedelta
import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from server import bigquery_event_feed as bq
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as ws
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_BIGQUERY_KEY_TEST"
TOKEN = "fixture-ephemeral-google-token"
EMAIL = "owg-reader@fixture-data.iam.gserviceaccount.com"
PROJECT = "fixture-billing"
KEY_ID = "a" * 40
PATH = "/bigquery/v2/projects/" + PROJECT + "/queries"


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
    src = {"billing_project": PROJECT, "data_project": "fixture-data",
           "dataset": "approved", "view": "events", "location": "EU",
           "service_account": EMAIL, "key_id": KEY_ID, "credential_env": ENV,
           "event_column": "event_id", "case_column": "case_id", "activity_column": "activity",
           "timestamp_column": "event_at", "resource_column": "resource",
           "activity_labels": {"open": "Open", "close": "Close"},
           "maximum_bytes_billed": 104857600}
    monkeypatch.setenv(bq.CATALOG, json.dumps({"primary": src}))
    return src


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "BigQuery test", "source_alias": "primary",
            "source_sha256": bq.catalog_public()["sources"][0]["source_sha256"],
            "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
            "ingestion_lag_minutes": 15, "page_size": 1, "confirm_configuration": True,
            "confirm_source_read": True, "confirm_metadata_retention": True, **extra}


def decode_part(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_assertion(value, key):
    header, claims, signature = value.split(".")
    assert json.loads(decode_part(header)) == {"alg": "RS256", "typ": "JWT", "kid": KEY_ID}
    data = json.loads(decode_part(claims))
    assert data["iss"] == EMAIL and data["scope"] == bq.SCOPE
    assert data["aud"] == bq.TOKEN_URL and data["exp"] - data["iat"] == 120
    assert "sub" not in data
    key.public_key().verify(decode_part(signature), (header + "." + claims).encode(),
                            padding.PKCS1v15(), hashes.SHA256())


def micros(at):
    delta = at - bq.EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def make_rows(profile):
    start = rest._stamp(profile["config"]["initial_since"])
    at = start + timedelta(hours=23, minutes=30)
    return [["private-event-" + str(i), "private-case", label,
             str(micros(at + timedelta(seconds=i))), "private-resource", EMAIL]
            for i, label in enumerate(("open", "close"))]


def result(job, names, types, rows, *, total=None, token=None):
    value = {"jobReference": {"projectId": PROJECT, "jobId": job, "location": "EU"},
             "jobComplete": True, "schema": {"fields": [
                 {"name": n, "type": t, "mode": "NULLABLE"} for n, t in zip(names, types)]},
             "totalRows": str(len(rows) if total is None else total),
             "rows": [{"f": [{"v": cell} for cell in row]} for row in rows],
             "totalBytesBilled": "10485760"}
    if token is not None:
        value["pageToken"] = token
    return value


def run(profile, rows, key, *, change=None, pending=False, token_overrides=None):
    seen = []
    stage = 0
    types = ("STRING", "STRING", "STRING", "INTEGER", "STRING", "STRING")
    def page(first):
        return result("event-job", bq.FIELDS, types, rows[:1] if first else rows[1:],
                      total=len(rows), token="next-page" if first and len(rows) > 1 else None)

    def handler(req):
        nonlocal stage
        seen.append((req.method, req.url.path))
        if str(req.url) == bq.TOKEN_URL:
            assert req.method == "POST" and "authorization" not in req.headers
            form = parse_qs(req.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            verify_assertion(form["assertion"][0], key)
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer",
                                            "expires_in": 3600, **(token_overrides or {})})
        assert req.url.host == "bigquery.googleapis.com"
        assert req.headers["authorization"] == "Bearer " + TOKEN
        if req.method == "POST":
            assert req.url.path == PATH
            body = json.loads(req.content)
            assert body["useLegacySql"] is False and body["useQueryCache"] is False
            assert body["location"] == "EU" and body["maximumBytesBilled"] == "104857600"
            assert body["jobTimeoutMs"] == "60000" and body["requestId"]
            if body["query"] == "SELECT SESSION_USER() AS owg_principal":
                return httpx.Response(200, json=result("identity-job", ("owg_principal",), ("STRING",), [[EMAIL]]))
            assert body["query"] == bq.query(bq.source(profile["config"]))
            assert body["parameterMode"] == "NAMED"
            assert [x["name"] for x in body["queryParameters"]] == ["owg_since", "owg_until"]
            assert all(x["parameterType"] == {"type": "TIMESTAMP"} for x in body["queryParameters"])
            stage += 1
            if pending:
                return httpx.Response(200, json={"jobReference": {"projectId": PROJECT,
                                      "jobId": "event-job", "location": "EU"}, "jobComplete": False})
            payload = page(True)
        else:
            assert req.method == "GET" and req.url.path == PATH + "/event-job"
            assert req.url.params["location"] == "EU"
            stage += 1
            if req.url.params.get("pageToken"):
                assert req.url.params["pageToken"] == "next-page"
                payload = page(False)
            else:
                assert pending
                payload = page(True)
        if change:
            override = change(payload, stage)
            if isinstance(override, httpx.Response):
                return override
        return httpx.Response(200, json=payload)

    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_ingestion_poll_pagination_and_replay(signing_key):
    p = bq.create(config())
    rows = make_rows(p)
    first, paths = run(p, rows, signing_key, pending=True)
    assert first["events_added"] == 2 and len(paths) == 5
    assert first["last_result"]["principal_readback_verified"]
    assert first["last_result"]["query_bytes_billed"] == 10485760
    assert first["last_result"]["query_maximum_bytes_billed"] == 104857600
    second, _ = run(first, rows, signing_key)
    assert second["events_added"] == 0 and second["duplicates_skipped"] == 2
    assert ws.list_logs()["logs"][0]["events"] == 2
    with ws._LOCK:
        c = rest._connect()
        try:
            raw = str(c.execute("SELECT * FROM events").fetchall()) + str(c.execute("SELECT * FROM objects").fetchall())
            stored = str(rest._get(c, p["profile_id"]))
        finally:
            c.close()
    assert all(x not in raw + stored for x in (TOKEN, "private-event", "private-case", "private-resource", encode_key(signing_key)))
    assert rest.schedule_pin(p["profile_id"]) == p["revision"]
    assert capabilities("bigquery")["exact_snapshot_publication"]
    assert capabilities("bigquery")["private_native_crosswalk"]
    assert any(x["provider"] == "bigquery" for x in ops.inventory()["retained_adapter_capabilities"])


def test_empty_window_requires_identity_and_exact_schema(signing_key):
    p = bq.create(config())
    report, paths = run(p, [], signing_key)
    assert report["events_added"] == 0 and report["checkpoint"] and report["log_id"] is None
    assert len(paths) == 3


@pytest.mark.parametrize("kind", ["job", "project", "location", "over_cap", "billing_drift",
                                  "total_high", "total_drift", "schema", "repeated", "cell",
                                  "missing", "token_loop", "token_bad", "warning", "redirect", "rate_limit"])
def test_failed_job_never_advances_checkpoint(kind, signing_key):
    p = bq.create(config())
    def change(payload, stage):
        if kind == "job" and stage == 2:
            payload["jobReference"]["jobId"] = "other"
        elif kind == "project":
            payload["jobReference"]["projectId"] = "other-project"
        elif kind == "location":
            payload["jobReference"]["location"] = "US"
        elif kind == "over_cap":
            payload["totalBytesBilled"] = "104857601"
        elif kind == "billing_drift" and stage == 2:
            payload["totalBytesBilled"] = "10485761"
        elif kind == "total_high":
            payload["totalRows"] = "10001"
        elif kind == "total_drift" and stage == 2:
            payload["totalRows"] = "3"
        elif kind == "schema":
            payload["schema"]["fields"][3]["type"] = "FLOAT"
        elif kind == "repeated":
            payload["schema"]["fields"][0]["mode"] = "REPEATED"
        elif kind == "cell":
            payload["rows"][0]["f"][0]["v"] = {"nested": "secret"}
        elif kind == "missing" and stage == 2:
            payload["rows"] = []
        elif kind == "token_loop" and stage == 2:
            payload["pageToken"] = "next-page"
        elif kind == "token_bad":
            payload["pageToken"] = "https://untrusted.invalid"
        elif kind == "warning":
            payload["errors"] = [{"reason": "internalError"}]
        elif kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        elif kind == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "120"})
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), signing_key, change=change)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "outside", "null", "wrong_principal", "fraction"])
def test_semantic_refusals(kind, signing_key):
    p = bq.create(config())
    rows = make_rows(p)
    if kind == "duplicate":
        rows[1][0] = rows[0][0]
    elif kind == "unknown":
        rows[0][2] = "unknown"
    elif kind == "outside":
        rows[0][3] = "0"
    elif kind == "null":
        rows[0][1] = None
    elif kind == "wrong_principal":
        rows[0][5] = "other@example.invalid"
    elif kind == "fraction":
        rows[0][3] += ".1"
    with pytest.raises(rest.FeedError):
        run(p, rows, signing_key)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


@pytest.mark.parametrize("override", [
    {"token_type": "other"}, {"expires_in": 1}, {"access_token": ""},
    {"scope": "https://www.googleapis.com/auth/cloud-platform"},
])
def test_bad_token_refused(override, signing_key):
    p = bq.create(config())
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), signing_key, token_overrides=override)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_consent_and_credential_pin(signing_key, monkeypatch, setup):
    assert bq.catalog_public()["provider_reads_performed"] is False
    with pytest.raises(rest.FeedError):
        bq.create(config(confirm_source_read=False))
    p = bq.create(config())
    monkeypatch.setenv(ENV, encode_key(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, encode_key(signing_key))
    setup["maximum_bytes_billed"] += 1
    monkeypatch.setenv(bq.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("billing_project", "other/queries"),
    ("view", "events; DELETE FROM other"),
    ("case_column", "EVENT_ID"),
    ("location", "EU/other"),
    ("maximum_bytes_billed", 0),
    ("service_account", "other@example.com"),
    ("activity_labels", {"open": "Same", "close": "Same"}),
])
def test_invalid_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(bq.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_bigquery_deployment_catalog"):
        bq.catalog()


def test_retained_native_case_crosswalk(signing_key):
    from server import enterprise_connector_crosswalk as crosswalk
    from server import enterprise_connector_snapshot as snapshot
    p = bq.create(config())
    run(p, make_rows(p), signing_key)
    start = rest._stamp(p["config"]["initial_since"])
    snap = snapshot.prepare(p["profile_id"], rest._iso(start), rest._iso(start + timedelta(days=1)))
    mapping = crosswalk.Input.model_validate({"rows":[{"native_case_id":"private-case","business_key":"reviewed-order-1"}],
        "basis":"reviewed_business_record","confirm_reviewed_mapping":True,"confirm_match_tag_disclosure":True})
    art = crosswalk.prepare(snap,mapping,job_id="cj_"+"1"*32,grant_id="cep_"+"2"*32,
        publication_revision=1,batch_id="3"*32,domain="purchase_order",secret=bytes(range(32)))
    assert len(art.rows)==1 and art.rows[0].case_id in {e.case_id for e in snap.events}
    assert "private-case" not in art.model_dump_json()
    assert "reviewed-order-1" not in art.model_dump_json()
