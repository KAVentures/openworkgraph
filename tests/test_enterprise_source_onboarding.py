"""Deployment onboarding using actual catalog parsers and local profile bindings."""
from datetime import timedelta
import base64
import json
import sqlite3

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from server import enterprise_source_onboarding as onboarding
from server import enterprise_rest_feed as rest
from server import enterprise_sql_feed as sql
from server import event_log_workspace as ws
from server import workday_raas_feed as wd
from server import bigquery_event_feed as bq
from server import enterprise_connector_secrets as secrets
from shared.enterprise_connector_contract import PROVIDERS

URL = "https://private-customer.example.test/events"
OTHER_URL = "https://other-customer.example.test/events"
REST_REF = "OWG_REST_TOKEN_TEST"
REST_TOKEN = "private-fixture-token-123456"
WORKDAY_REF = "OWG_WORKDAY_PASSWORD_TEST"
WORKDAY_PASSWORD = "private-fixture-password"
BIGQUERY_REF = "OWG_BIGQUERY_KEY_TEST"
WD = {"host": "wd2-impl-services1.workday.com", "tenant": "private-tenant",
      "report_owner": "private-owner", "event_report": "Private_Events",
      "count_report": "Private_Control", "integration_user": "private-isu",
      "credential_env": WORKDAY_REF, "since_prompt": "Since", "until_prompt": "Until",
      "event_field": "Event_ID", "case_field": "Case_ID", "activity_field": "Activity",
      "timestamp_field": "Event_At", "resource_field": "Resource_ID",
      "count_field": "Total", "activity_labels": {"open": "Private activity"}}
BQ = {"billing_project": "private-billing", "data_project": "private-data",
      "dataset": "private_dataset", "view": "private_view", "location": "EU",
      "service_account": "private-reader@private-data.iam.gserviceaccount.com",
      "key_id": "a"*40, "credential_env": BIGQUERY_REF,
      "event_column": "event_id", "case_column": "case_id", "activity_column": "activity",
      "timestamp_column": "event_at", "activity_labels": {"open": "Private activity"},
      "maximum_bytes_billed": 104857600}
MAPPING = {"event_id": "eid", "case_id": "cid", "activity": "label",
           "event_time": "happened", "change_time": "changed", "resource": "actor"}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    folder = tmp_path / "retained"
    folder.mkdir()
    monkeypatch.setattr(ws, "DATA_DIR", folder)
    for env, _, _ in onboarding.CATALOGS.values():
        monkeypatch.delenv(env, raising=False)
    for name in ("OWG_REST_ALLOWED_ENDPOINTS", "OWG_REST_ENDPOINT_CREDENTIALS",
                 secrets.CATALOG, secrets.ROOT, WORKDAY_REF, REST_REF, BIGQUERY_REF):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(httpx.Client, "stream", lambda *a, **k: pytest.fail("unexpected provider read"))
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("unexpected provider request"))
    monkeypatch.setenv(WORKDAY_REF, WORKDAY_PASSWORD)
    monkeypatch.setenv(REST_REF, REST_TOKEN)
    monkeypatch.setenv(wd.CATALOG, json.dumps({"private-source": WD}))
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS", json.dumps([URL, OTHER_URL]))
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS", json.dumps({URL: REST_REF}))


def wd_source(monkeypatch, **overrides):
    monkeypatch.setenv(wd.CATALOG, json.dumps({"private-source": {**WD, **overrides}}))


def wd_profile():
    return wd.create({"name": "Private Workday profile", "source_alias": "private-source",
                      "source_sha256": wd.catalog_public()["sources"][0]["source_sha256"],
                      "initial_since": rest._iso(rest._now()-timedelta(days=3)),
                      "confirm_configuration": True, "confirm_source_read": True,
                      "confirm_metadata_retention": True})


def rest_profile():
    return rest.create({"name": "Private REST profile", "endpoint": URL, "credential_env": REST_REF,
                        "initial_since": rest._iso(rest._now()-timedelta(days=3)),
                        "mapping": {"event_id": "id", "case_id": "case", "activity": "step",
                            "event_time": "time", "change_time": "changed", "resource": "actor"},
                        "confirm_configuration": True, "confirm_activity_labels": True})


def catalog(report, provider):
    return next(x for x in report["catalogs"] if x["provider"] == provider)


def source(report, provider, alias=None):
    return next(x for x in report["sources"] if x["provider"] == provider
                and (alias is None or x["source_alias"] == alias))


def summary(report, provider):
    return next(x for x in report["summary"]["providers"] if x["provider"] == provider)


def test_all_registered_providers_are_classified():
    report = onboarding.inspect()
    assert set(onboarding.CATALOGS) | set(onboarding.OTHER) | {"rest"} == PROVIDERS
    assert {x["provider"] for x in report["summary"]["providers"]} == PROVIDERS
    assert catalog(report, "workday")["state"] == "valid"
    assert catalog(report, "sql")["state"] == "empty"
    assert not catalog(report, "sql")["deployment_setting_present"]
    row = source(report, "workday")
    assert row["local_credential_state"] == "locally_available"
    assert not row["provider_identity_verified"]
    assert row["profile_ids"] == []
    assert summary(report, "workday")["unconfigured_ready_sources"] == 1
    assert report["summary"]["rest_endpoint_policy"]["unpaired_endpoint_count"] == 1
    assert report["summary"]["rest_endpoint_policy"]["locally_available_pairs"] == 1
    assert report["provider_reads_performed"] is False
    assert report["execution_authorized"] is False


def test_binding_and_definition_drift_are_distinct(monkeypatch):
    p = wd_profile()
    q = rest_profile()
    initial = onboarding.inspect()
    assert source(initial, "workday")["profile_ids"] == [p["profile_id"]]
    assert summary(initial, "workday")["unconfigured_ready_sources"] == 0
    monkeypatch.setenv(WORKDAY_REF, "new-password")
    binding = onboarding.inspect()
    item = next(x for x in binding["profiles"] if x["profile_id"] == p["profile_id"])
    assert item["definition_state"] == "matches_reviewed_definition"
    assert item["binding_state"] == "blocked"
    assert item["error_code"] == "credential_changed_reconfigure_required"
    monkeypatch.setenv(WORKDAY_REF, WORKDAY_PASSWORD)
    wd_source(monkeypatch, count_report="New_Control")
    drift = onboarding.inspect()
    assert source(drift, "workday")["definition_drift_profiles"] == [p["profile_id"]]
    item = next(x for x in drift["profiles"] if x["profile_id"] == p["profile_id"])
    assert item["definition_state"] == "definition_changed"
    assert item["binding_state"] == "blocked" and item["requires_operator_review"]
    assert summary(drift, "workday")["profiles_requiring_review"] == 1
    assert next(x for x in drift["profiles"] if x["profile_id"] == q["profile_id"])["binding_state"] == "locally_available"
    monkeypatch.setenv(wd.CATALOG, "{}")
    missing = onboarding.inspect()
    assert next(x for x in missing["profiles"] if x["profile_id"] == p["profile_id"])["definition_state"] == "source_missing"
    monkeypatch.setenv(wd.CATALOG, '{"bad":')
    invalid = onboarding.inspect()
    assert next(x for x in invalid["profiles"] if x["profile_id"] == p["profile_id"])["definition_state"] == "catalog_invalid"


def test_invalid_catalog_isolated_and_empty_or_unknown_not_ready(monkeypatch):
    monkeypatch.setenv(bq.CATALOG, '{"invalid":true}')
    report = onboarding.inspect()
    assert catalog(report, "bigquery")["state"] == "invalid"
    assert summary(report, "bigquery")["configured_sources"] is None
    assert summary(report, "workday")["ready_local_sources"] == 1
    monkeypatch.delenv(WORKDAY_REF)
    blocked = onboarding.inspect()
    assert source(blocked, "workday")["error_code"] == "workday_credential_unavailable"
    assert summary(blocked, "workday")["blocked_local_sources"] == 1


def test_local_sqlite_validation_does_not_open_database(tmp_path, monkeypatch):
    path = (tmp_path/"private-source.sqlite").resolve()
    # Readiness is path/identity validation, not a SQLite schema probe.
    path.write_bytes(b"not a valid SQLite database")
    monkeypatch.setenv("OWG_SQL_SOURCE_CATALOG", json.dumps({"private-sql": {
        "engine": "sqlite", "path": str(path), "relation": "events", "mapping": MAPPING}}))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("source DB opened"))
    row = source(onboarding.inspect(), "sql")
    assert row["local_credential_state"] == "locally_available"
    assert row["credential_delivery"] == "local_file_source"
    assert row["source_schema_verified"] is False
    path.unlink()
    assert source(onboarding.inspect(), "sql")["error_code"] == "sql_readonly_file_unavailable"


def test_bigquery_key_format_checks_without_oauth(monkeypatch):
    monkeypatch.setenv(bq.CATALOG, json.dumps({"private-bq": BQ}))
    monkeypatch.setenv(BIGQUERY_REF, "invalid-base64")
    assert source(onboarding.inspect(), "bigquery")["error_code"] == "invalid_bigquery_rsa_private_key"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encoded = base64.b64encode(key.private_bytes(serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()
    monkeypatch.setenv(BIGQUERY_REF, encoded)
    report = onboarding.inspect()
    assert source(report, "bigquery")["local_credential_state"] == "locally_available"
    assert encoded not in json.dumps(report)


@pytest.mark.parametrize("pairs", [
    "invalid-json", "{}", '{"https://unapproved.example.test/events":"OWG_REST_TOKEN_TEST"}',
    '{"https://private-customer.example.test/events":"PATH"}',
])
def test_bad_rest_policy_not_interpreted_as_empty(monkeypatch, pairs):
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS", pairs)
    report = onboarding.inspect()
    assert report["summary"]["rest_endpoint_policy"]["state"] == "invalid"
    assert report["summary"]["rest_endpoint_policy"]["approved_pair_count"] is None


def test_missing_pair_policy_does_not_imply_approved_credentials(monkeypatch):
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS")
    report = onboarding.inspect()
    assert report["summary"]["rest_endpoint_policy"]["state"] == "no_explicit_pair_policy"
    assert report["summary"]["rest_endpoint_policy"]["approved_pair_count"] == 0


def test_shared_reference_and_support_projection(monkeypatch):
    monkeypatch.setenv(wd.CATALOG, json.dumps({"private-source": WD,
                                               "private-second": {**WD, "event_report": "Second_Events"}}))
    p = wd_profile()
    q = rest_profile()
    report = onboarding.inspect()
    assert all(x["shared_credential_reference"] for x in report["sources"] if x["provider"] == "workday")
    support = onboarding.inspect(support_only=True)
    assert support["scope"] == "counts_only_support"
    assert not {"sources", "profiles", "catalogs"} & set(support)
    assert support["summary"] == report["summary"]
    exported = json.dumps(support)
    for value in (URL, OTHER_URL, WORKDAY_REF, WORKDAY_PASSWORD, REST_REF, REST_TOKEN,
                  WD["tenant"], WD["host"], WD["event_report"], WD["integration_user"],
                  "private-source", "private-second", p["profile_id"], q["profile_id"],
                  "Private Workday profile", "Private REST profile", "Private activity"):
        assert value not in exported
    local = json.dumps(report)
    for value in (URL, OTHER_URL, WORKDAY_REF, WORKDAY_PASSWORD, REST_REF, REST_TOKEN,
                  WD["tenant"], WD["host"], WD["event_report"], WD["integration_user"]):
        assert value not in local


def test_private_file_mappings_are_authoritative_and_unused_not_opened(tmp_path, monkeypatch):
    import os
    if os.name != "posix":
        pytest.skip("owner-only managed secret files require POSIX")
    root = tmp_path/"secrets"
    root.mkdir(mode=0o700)
    path = root/"private-password-file"
    path.write_text(WORKDAY_PASSWORD+"\n")
    path.chmod(0o600)
    monkeypatch.setenv(secrets.ROOT, str(root))
    monkeypatch.setenv(secrets.CATALOG, json.dumps({WORKDAY_REF: path.name,
        "OWG_CONNECTOR_WORKER_TOKEN": "unopened-worker-file"}))
    report = onboarding.inspect()
    assert source(report, "workday")["credential_delivery"] == "private_file"
    assert source(report, "workday")["local_credential_state"] == "locally_available"
    counts = report["summary"]["secret_file_inventory"]
    assert counts["configured_mappings"] == 2 and counts["referenced_mappings"] == 1
    assert counts["not_referenced_by_inspected_sources_or_profiles"] == 1
    assert counts["unreferenced_mappings_are_not_proven_unused"] is True
    path.unlink()
    blocked = onboarding.inspect()
    assert source(blocked, "workday")["error_code"] == "connector_secret_file_unavailable"
    assert source(blocked, "workday")["local_credential_state"] == "blocked"


def test_malformed_secret_catalog_isolated(monkeypatch):
    monkeypatch.setenv(secrets.CATALOG, "[]")
    report = onboarding.inspect()
    assert report["summary"]["secret_file_inventory"]["state"] == "invalid"
    assert source(report, "workday")["error_code"] == "invalid_connector_secret_catalog"
    assert report["summary"]["rest_endpoint_policy"]["blocked_pairs"] == 1
