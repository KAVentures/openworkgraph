"""Synthetic Databricks SQL API, exact retention, replay and refusal tests."""
from datetime import timedelta
import json

import httpx
import pytest

from server import databricks_event_feed as dbx
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import enterprise_connector_operations as ops
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_DATABRICKS_TOKEN_TEST"
TOKEN = "synthetic-databricks-token"
SID = "01eda0e7-e315-1846-84e2-79a963ffad44"
PRINCIPAL = "fixture-service-principal"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv(ENV, TOKEN)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    source = {"host": "fixture.cloud.databricks.com", "warehouse_id": "0123456789abcdef",
              "expected_principal": PRINCIPAL, "credential_env": ENV,
              "catalog": "business", "schema_name": "approved", "view": "events",
              "event_column": "event_id", "case_column": "case_id", "activity_column": "activity",
              "timestamp_column": "event_at", "resource_column": "resource",
              "activity_labels": {"open": "Open", "close": "Close"}}
    monkeypatch.setenv(dbx.CATALOG, json.dumps({"primary": source}))
    return source


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "Databricks fixture", "source_alias": "primary",
            "source_sha256": dbx.catalog_public()["sources"][0]["source_sha256"],
            "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
            "ingestion_lag_minutes": 15, "confirm_configuration": True,
            "confirm_source_read": True, "confirm_metadata_retention": True, **extra}


def result(names, types, rows, *, chunks=None):
    chunks = [rows] if chunks is None else chunks
    offset = 0
    metadata = []
    for i, chunk in enumerate(chunks):
        metadata.append({"chunk_index": i, "row_offset": offset, "row_count": len(chunk)})
        offset += len(chunk)
    manifest = {"format": "JSON_ARRAY", "truncated": False, "total_row_count": len(rows),
                "total_chunk_count": len(chunks), "chunks": metadata,
                "schema": {"column_count": len(names), "columns": [
                    {"name": name, "position": i, "type_name": types[i]} for i, name in enumerate(names)]}}
    return {"statement_id": SID, "status": {"state": "SUCCEEDED"}, "manifest": manifest,
            "result": {**metadata[0], "data_array": chunks[0]} if chunks else None}


def make_rows(profile):
    start = rest._stamp(profile["config"]["initial_since"])
    at = start + timedelta(hours=23, minutes=30)
    return [["private-event-1", "private-case", "open", str(dbx.micros(at)), "private-resource"],
            ["private-event-2", "private-case", "close",
             str(dbx.micros(at + timedelta(seconds=2))), "private-resource"]]


def run(profile, rows, *, alter=None, wrong_principal=False, split=False, schema_type="TIMESTAMP"):
    seen = []
    def handler(req):
        assert req.url.host == "fixture.cloud.databricks.com"
        assert req.headers["authorization"] == "Bearer " + TOKEN
        seen.append((req.method, req.url.path))
        if req.method == "GET":
            assert req.url.path == dbx.BASE + "/" + SID + "/result/chunks/1"
            return httpx.Response(200, json={"chunk_index": 1, "row_offset": 1,
                                             "row_count": 1, "data_array": rows[1:]})
        assert req.method == "POST" and req.url.path == dbx.BASE
        body = json.loads(req.content)
        assert body["disposition"] == "INLINE" and body["on_wait_timeout"] == "CANCEL"
        if body["statement"].startswith("SELECT current_user"):
            payload = result(("owg_principal",), ("STRING",),
                             [["wrong" if wrong_principal else PRINCIPAL]])
        elif body["statement"].endswith("LIMIT 0"):
            payload = result(dbx.FIELDS, ("STRING", "STRING", "STRING", schema_type, "STRING"), [])
        else:
            assert body["statement"] == dbx.query(dbx.source(profile["config"]))
            assert {x["name"] for x in body["parameters"]} == {"owg_since", "owg_until"}
            assert all(x["type"] == "BIGINT" for x in body["parameters"])
            payload = result(dbx.FIELDS, ("STRING",) * 5, rows,
                             chunks=[rows[:1], rows[1:]] if split else None)
            if alter:
                alter(payload)
        return httpx.Response(200, json=payload)
    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_ingestion_exact_cases_replay_and_privacy():
    p = dbx.create(config())
    rows = make_rows(p)
    first, requests = run(p, rows, split=True)
    assert first["events_added"] == 2
    assert len(requests) == 5
    assert first["last_result"]["principal_readback_verified"]
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
    assert capabilities("databricks")["exact_snapshot_publication"]
    assert any(x["provider"] == "databricks" for x in ops.inventory()["retained_adapter_capabilities"])


@pytest.mark.parametrize("alter", [
    lambda p: p["manifest"].update(truncated=True),
    lambda p: p["manifest"].update(total_row_count=99999),
    lambda p: p["result"].update(row_offset=1),
    lambda p: p["result"].update(external_links=[{"external_link": "https://other.invalid"}]),
    lambda p: p["result"]["data_array"].pop(),
    lambda p: p.update(statement_id="../../invalid"),
    lambda p: p["status"].update(state="PENDING"),
])
def test_bad_result_never_advances_checkpoint(alter):
    p = dbx.create(config())
    with pytest.raises(rest.FeedError):
        run(p, make_rows(p), alter=alter)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


@pytest.mark.parametrize("kind", ["duplicate", "unapproved_activity", "outside", "null_identity", "wrong_principal", "ntz"])
def test_semantic_refusals(kind):
    p = dbx.create(config())
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
        run(p, rows, wrong_principal=kind == "wrong_principal",
            schema_type="TIMESTAMP_NTZ" if kind == "ntz" else "TIMESTAMP")
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_passive_catalog_consent_and_credential_drift(monkeypatch, setup):
    assert dbx.catalog_public()["provider_reads_performed"] is False
    with pytest.raises(rest.FeedError):
        dbx.create(config(confirm_source_read=False))
    p = dbx.create(config())
    monkeypatch.setenv(ENV, "different-synthetic-token")
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, TOKEN)
    setup["view"] = "another_view"
    monkeypatch.setenv(dbx.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("host", "fixture.cloud.databricks.com.evil.invalid"),
    ("host", "https://fixture.cloud.databricks.com"),
    ("view", "events; DELETE FROM other"),
    ("case_column", "event_id"),
    ("activity_labels", {"open": "Same", "close": "Same"}),
])
def test_bad_deployment_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(dbx.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_databricks_deployment_catalog"):
        dbx.catalog()


def test_actual_retained_native_case_crosswalk():
    from server import enterprise_connector_crosswalk as crosswalk
    from server import enterprise_connector_snapshot as snapshot
    p = dbx.create(config())
    retained = run(p, make_rows(p))[0]
    start = rest._stamp(p["config"]["initial_since"])
    snap = snapshot.prepare(p["profile_id"], rest._iso(start), rest._iso(start + timedelta(days=1)))
    mapping = crosswalk.Input.model_validate({"rows":[{"native_case_id":"private-case","business_key":"reviewed-order-1"}],
        "basis":"reviewed_business_record","confirm_reviewed_mapping":True,"confirm_match_tag_disclosure":True})
    art = crosswalk.prepare(snap,mapping,job_id="cj_"+"1"*32,grant_id="cep_"+"2"*32,
        publication_revision=1,batch_id="3"*32,domain="purchase_order",secret=bytes(range(32)))
    assert len(art.rows)==1 and art.rows[0].case_id in {e.case_id for e in snap.events}
    assert "private-case" not in art.model_dump_json()
    assert "reviewed-order-1" not in art.model_dump_json()
