"""Snowflake SQL API transport and retained-feed regressions (synthetic only)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import httpx
import pytest

from server import snowflake_event_feed as snow
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import enterprise_connector_operations as ops
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_SNOWFLAKE_TOKEN_TEST"
TOKEN = "synthetic-snowflake-pat"
HANDLE = "536fad38-b564-4dc5-9892-a4543504df6c"
ACCOUNT, USER, ROLE = "FIXTURE", "SERVICE_USER", "OWG_READ_ONLY"
HOST = "fixture-account.snowflakecomputing.com"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv(ENV, TOKEN)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    source = {"host": HOST, "warehouse": "INGEST", "database": "BUSINESS",
              "schema_name": "APPROVED", "view": "EVENTS", "role": ROLE,
              "expected_account": ACCOUNT, "expected_user": USER, "credential_env": ENV,
              "event_column": "EVENT_ID", "case_column": "CASE_ID", "activity_column": "ACTIVITY",
              "timestamp_column": "EVENT_AT", "resource_column": "RESOURCE",
              "activity_labels": {"open": "Open", "close": "Close"}}
    monkeypatch.setenv(snow.CATALOG, json.dumps({"primary": source}))
    return source


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "Snowflake fixture", "source_alias": "primary",
            "source_sha256": snow.catalog_public()["sources"][0]["source_sha256"],
            "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
            "ingestion_lag_minutes": 15, "confirm_configuration": True,
            "confirm_source_read": True, "confirm_metadata_retention": True, **extra}


def result(names, types, rows, *, split=False):
    chunks = [rows[:1], rows[1:]] if split else [rows]
    return {"statementHandle": HANDLE, "code": "090001", "sqlState": "00000",
            "statementStatusUrl": "https://untrusted.invalid/never-follow",
            "resultSetMetaData": {"format": "jsonv2", "numRows": len(rows),
                "rowType": [{"name": n, "type": t} for n, t in zip(names, types)],
                "partitionInfo": [{"rowCount": len(chunk)} for chunk in chunks]},
            "data": chunks[0]}


def make_rows(profile):
    start = rest._stamp(profile["config"]["initial_since"])
    at = start + timedelta(hours=23, minutes=30)
    def epoch(stamp):
        return str(Decimal(snow.nanos(stamp)) / Decimal(10**9))
    return [["private-event-1", "private-case", "open", epoch(at), "private-resource"],
            ["private-event-2", "private-case", "close", epoch(at + timedelta(seconds=2)),
             "private-resource"]]


def run(profile, rows, *, alter=None, wrong_identity=False, split=False,
        native_type="timestamp_ltz", bad_partition=False, status=200):
    seen = []
    def handler(req):
        assert req.url.host == HOST
        assert req.headers["authorization"] == "Bearer " + TOKEN
        assert req.headers["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN"
        assert req.url.params["requestId"]
        seen.append((req.method, req.url.path))
        if req.method == "GET":
            assert req.url.path == snow.BASE + "/" + HANDLE
            assert req.url.params["partition"] == "1"
            return httpx.Response(200, json={"data": [] if bad_partition else rows[1:]})
        assert req.method == "POST" and req.url.path == snow.BASE
        assert req.url.params["async"] == "false"
        body = json.loads(req.content)
        assert body["timeout"] == 20 and body["role"] == ROLE
        assert body["warehouse"] == "INGEST" and body["database"] == "BUSINESS"
        assert body["parameters"]["USE_CACHED_RESULT"] == "false"
        if body["statement"].startswith("SELECT CURRENT_ACCOUNT()"):
            payload = result(snow.IDENTITY_FIELDS, ("text",) * 3,
                             [[ACCOUNT, USER, "WRONG_ROLE" if wrong_identity else ROLE]])
        else:
            assert body["statement"] == snow.query(snow.source(profile["config"]))
            assert set(body["bindings"]) == {"1", "2"}
            assert all(x["type"] == "FIXED" for x in body["bindings"].values())
            payload = result(snow.FIELDS, ("text", "text", "text", native_type, "text"), rows, split=split)
            if alter:
                alter(payload)
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json=payload)
    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_ingestion_partition_replay_and_privacy():
    p = snow.create(config())
    rows = make_rows(p)
    first, requests = run(p, rows, split=True)
    assert first["events_added"] == 2 and len(requests) == 4
    assert first["last_result"]["account_user_role_readback_verified"]
    assert ws.summary(first["log_id"], "source_case")["cases"] == 1
    assert ws.summary(first["log_id"], "source_case")["events"] == 2
    second, _ = run(first, rows)
    assert second["events_added"] == 0 and second["duplicates_skipped"] == 2
    with ws._LOCK:
        c = rest._connect()
        try:
            raw = str(c.execute("SELECT * FROM events").fetchall()) + str(c.execute("SELECT * FROM objects").fetchall())
        finally:
            c.close()
    assert all(x not in raw for x in (TOKEN, "private-event", "private-case", "private-resource"))
    assert rest.schedule_pin(p["profile_id"]) == p["revision"]
    assert capabilities("snowflake")["exact_snapshot_publication"]
    assert any(x["provider"] == "snowflake" for x in ops.inventory()["retained_adapter_capabilities"])


@pytest.mark.parametrize("alter", [
    lambda p: p.update(code="391908"),
    lambda p: p["resultSetMetaData"].update(numRows=99999),
    lambda p: p["resultSetMetaData"]["partitionInfo"][0].update(rowCount=1),
    lambda p: p["data"].pop(),
    lambda p: p.update(statementHandle="../../invalid"),
    lambda p: p.update(statementHandles=[HANDLE]),
])
def test_bad_result_never_advances_checkpoint(alter):
    p = snow.create(config())
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), alter=alter)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


@pytest.mark.parametrize("kind", ["duplicate", "unapproved_activity", "outside", "null_identity",
                                  "wrong_identity", "ntz", "partition", "pending", "rate_limit"])
def test_semantic_refusals(kind):
    p = snow.create(config())
    rows = make_rows(p)
    if kind == "duplicate":
        rows[1][0] = rows[0][0]
    elif kind == "unapproved_activity":
        rows[0][2] = "unreviewed"
    elif kind == "outside":
        rows[0][3] = "0"
    elif kind == "null_identity":
        rows[0][0] = None
    with pytest.raises(rest.FeedError):
        run(p, rows, wrong_identity=kind == "wrong_identity", split=kind == "partition",
            bad_partition=kind == "partition", native_type="timestamp_ntz" if kind == "ntz" else "timestamp_ltz",
            status=202 if kind == "pending" else 429 if kind == "rate_limit" else 200)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_exact_timestamp_encodings():
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert snow.parse_timestamp("-0.000001", "timestamp_ltz") == epoch - timedelta(microseconds=1)
    assert snow.parse_timestamp("1.123456000 1500", "timestamp_tz") == epoch + timedelta(seconds=1, microseconds=123456)
    for value, typ in [("1.000000001", "timestamp_ltz"), ("1", "timestamp_tz"),
                       ("1 9999", "timestamp_tz"), ("1 1440", "timestamp_ltz"),
                       ("NaN", "timestamp_ltz"), ("1", "timestamp_ntz")]:
        with pytest.raises(rest.FeedError):
            snow.parse_timestamp(value, typ)


def test_passive_catalog_consent_and_credential_drift(monkeypatch, setup):
    assert snow.catalog_public()["provider_reads_performed"] is False
    with pytest.raises(rest.FeedError):
        snow.create(config(confirm_source_read=False))
    p = snow.create(config())
    monkeypatch.setenv(ENV, "different-synthetic-token")
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, TOKEN)
    setup["role"] = "OTHER"
    monkeypatch.setenv(snow.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("host", "fixture.snowflakecomputing.com.evil.invalid"),
    ("host", "https://fixture.snowflakecomputing.com"),
    ("view", "events; DELETE FROM other"),
    ("case_column", "event_id"),
    ("activity_labels", {"open": "Same", "close": "Same"}),
])
def test_bad_deployment_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(snow.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_snowflake_deployment_catalog"):
        snow.catalog()
