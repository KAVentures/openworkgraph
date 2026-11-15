"""R16: bounded, audited process value comparison without causal/receipt overclaim."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shared.process_value_measurement import Measurement, Save, Remove, compare
from gateway.process_value_measurements import ProcessValueMeasurements, namespace
from gateway.process_collaboration import ProcessCollaborationError, ProcessDeleteWrite
from test_process_collaboration import app, grant, model, person, review, store, write


def pinned(root):
    return {k: root[k] for k in ("workflow_id", "revision", "content_sha256")}


def payload(root, **changes):
    data = {
        "organization_id": "acme",
        "root_process": pinned(root),
        "title": "Reviewed before-after aggregate comparison",
        "currency": "USD",
        "baseline": {
            "started_at": "2026-09-01T00:00:00Z",
            "ended_at": "2026-09-30T00:00:00Z",
            "case_count": 100,
            "total_handling_minutes": "1000.00",
            "receipt_sha256": ["a" * 64],
            "coverage": "complete",
        },
        "intervention": {
            "started_at": "2026-10-01T00:00:00Z",
            "ended_at": "2026-10-31T00:00:00Z",
            "case_count": 100,
            "total_handling_minutes": "800.00",
            "receipt_sha256": ["b" * 64],
            "coverage": "complete",
        },
        "assumed_hourly_cost_minor": 6000,
        "periods_and_case_cohorts_reviewed": True,
    }
    return {**data, **changes}


def ready(store):
    first = store.save(person(), write())["process"]
    grant(store, first["workflow_id"], members=[{"actor_id": "bob", "role": "reviewer"}])
    store.review(person("bob"), first["workflow_id"], review(first))
    return first


def save(values, root, *, revision=0, **changes):
    return values.save(person(), root["workflow_id"], Save(
        measurement=Measurement.model_validate(payload(root, **changes)),
        expected_access_revision=2, expected_revision=revision,
        user_confirmed=True, organization_sharing_confirmed=True,
    ))


def test_descriptive_calculation_has_no_verified_or_causal_claims(store):
    m = Measurement.model_validate(payload(ready(store)))
    result = compare(m)
    assert result["status"] == "descriptive_comparison_only"
    assert result["blockers"] == []
    assert result["comparison"]["baseline_mean_handling_minutes_per_case"] == "10.00"
    assert result["comparison"]["intervention_mean_handling_minutes_per_case"] == "8.00"
    assert result["comparison"]["descriptive_minutes_difference_per_case"] == "2.00"
    assert result["comparison"]["assumed_labor_value_difference_per_100_cases_minor"] == "20000.00"
    assert result["comparison"]["value_type"] == "assumed_labor_cost_delta_not_realized_savings"
    assert result["realized_savings_verified"] is False
    assert result["causal_effect_established"] is False
    assert result["statistical_confidence_interval_available"] is False


@pytest.mark.parametrize("edit", [
    lambda p: p["baseline"].update(started_at="2026-09-01T00:00:00"),
    lambda p: p["intervention"].update(started_at="2026-09-20T00:00:00Z"),
    lambda p: p["intervention"].update(receipt_sha256=["a" * 64]),
    lambda p: p["baseline"].update(receipt_sha256=["a" * 64, "a" * 64]),
    lambda p: p["baseline"].update(total_handling_minutes="-2"),
    lambda p: p["baseline"].update(total_handling_minutes="9.999"),
    lambda p: p["baseline"].update(total_handling_minutes="1e9"),
    lambda p: p["baseline"].update(total_handling_minutes=1000.0),
    lambda p: p["baseline"].update(case_count=True),
    lambda p: p.update(realized_savings_verified=True),
    lambda p: p.update(independent_source_receipts_verified=True),
    lambda p: p.update(execution_authorized=True),
    lambda p: p.update(unknown_private_data="a"),
    lambda p: p.update(title="Title\nnext line"),
])
def test_invalid_or_authorizing_aggregates_are_refused(store, edit):
    p = payload(ready(store)); edit(p)
    with pytest.raises(ValidationError):
        Measurement.model_validate(p)


def test_partial_or_underpowered_data_withholds_even_descriptive_comparison(store):
    root = ready(store)
    for edit in [{"periods_and_case_cohorts_reviewed": False},
                 {"baseline": {**payload(root)["baseline"], "coverage": "unknown"}},
                 {"intervention": {**payload(root)["intervention"], "case_count": 4}}]:
        result = compare(Measurement.model_validate(payload(root, **edit)))
        assert result["status"] == "withheld" and result["comparison"] is None
        assert not result["realized_savings_verified"]


def test_versioned_storage_revisions_and_process_drift_are_fail_closed(store):
    root = ready(store); values = ProcessValueMeasurements(store); wid = root["workflow_id"]
    first = save(values, root)
    assert first["revision"] == 1 and not first["realized_savings_verified"]
    detail = values.detail(person(), wid)
    assert detail["status"] == "descriptive_comparison_only"
    assert detail["artifact"]["content_sha256"] == first["content_sha256"]
    second = save(values, root, revision=1, title="A corrected cohort")
    assert second["revision"] == 2
    assert values.detail(person(), wid, 1)["artifact"]["revision"] == 1
    with pytest.raises(ProcessCollaborationError, match="changed"):
        save(values, root, revision=1)
    with pytest.raises(ProcessCollaborationError):
        values.detail(person("alice", org="another-tenant"), wid)
    with pytest.raises(ProcessCollaborationError):
        values.detail(person(scopes=frozenset()), wid)
    changed = store.save(person(), write(title="Changed", expected=1), wid)["process"]
    assert changed["revision"] == 2
    stale = values.detail(person(), wid)
    assert stale["status"] == "withheld_changed_or_unreviewed_process"
    assert stale["comparison"] is None
    with pytest.raises(ProcessCollaborationError):
        save(values, root, revision=2)


def test_measurements_delete_with_root_and_no_cross_root_mutation(store):
    root = ready(store)
    other = ready(store)
    values = ProcessValueMeasurements(store)
    save(values, root)
    save(values, other)
    wid = root["workflow_id"]
    store.delete(person(), wid, ProcessDeleteWrite(
        expected_revision=1, expected_access_revision=2, user_confirmed=True))
    with store.db.connect() as conn:
        assert store.db._execute(conn, "SELECT COUNT(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                                 (namespace("acme"), wid)).fetchone()[0] == 0
    assert values.detail(person(), other["workflow_id"])["latest_revision"] == 1


def test_explicit_delete_is_revision_pinned_and_owner_only(store):
    root = ready(store); values = ProcessValueMeasurements(store); wid = root["workflow_id"]
    save(values, root)
    with pytest.raises(ProcessCollaborationError):
        values.delete(person("bob"), wid, Remove(
            expected_revision=1, expected_access_revision=2, user_confirmed=True))
    with pytest.raises(ProcessCollaborationError):
        values.delete(person(), wid, Remove(
            expected_revision=1, expected_access_revision=1, user_confirmed=True))
    assert values.delete(person(), wid, Remove(
        expected_revision=1, expected_access_revision=2, user_confirmed=True)) == {"deleted": True}
    assert values.detail(person(), wid)["status"] == "not_saved"


def test_corrupt_stored_payload_returns_no_values_or_private_title(store):
    root = ready(store); values = ProcessValueMeasurements(store); wid = root["workflow_id"]
    save(values, root, title="PRIVATE_SECRET_TITLE")
    with store.db.connect() as conn:
        row = store.db._execute(conn, "SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                                (namespace("acme"), wid)).fetchone()
        p = json.loads(row[0]); p["measurement"]["baseline"]["total_handling_minutes"] = "0.01"
        store.db._execute(conn, "UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?",
                          (json.dumps(p), namespace("acme"), wid))
    detail = values.detail(person(), wid)
    assert detail["status"] == "integrity_failed" and detail["comparison"] is None
    assert "PRIVATE_SECRET_TITLE" not in json.dumps(detail)


def test_actual_authenticated_http_preflight_and_parser(tmp_path):
    with TestClient(app(tmp_path)) as client:
        token = lambda actor: {"Authorization": "Bearer " + actor}
        first = client.post("/v1/human/processes", headers=token("alice"), json=write().model_dump()).json()["process"]
        wid = first["workflow_id"]
        client.post("/v1/human/processes/" + wid + "/access", headers=token("alice"),
                    json={"expected_access_revision": 1, "members": [{"actor_id": "bob", "role": "reviewer"}],
                          "user_confirmed": True}).raise_for_status()
        client.post("/v1/human/processes/" + wid + "/reviews", headers=token("bob"),
                    json=review(first).model_dump()).raise_for_status()
        url = "/v1/human/processes/" + wid + "/value-measurements"
        body = Save(measurement=Measurement.model_validate(payload(first)),
                    expected_access_revision=2, user_confirmed=True,
                    organization_sharing_confirmed=True).model_dump(mode="json")
        assert client.post(url, headers=token("reader"), content="not json").status_code == 403
        assert client.get(url, headers=token("foreign")).status_code == 404
        response = client.post(url, headers=token("alice"), json=body)
        assert response.status_code == 200, response.text
        report = client.get(url, headers=token("alice"))
        assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
        assert report.json()["status"] == "descriptive_comparison_only"
        assert client.get(url + "/export?revision=1", headers=token("alice")).status_code == 200
        assert client.post(url, headers={**token("alice"), "Content-Type": "application/json"},
                           content='{"expected_revision":0,"expected_revision":1}').status_code == 422
        assert client.post(url, headers={**token("alice"), "Content-Type": "application/json"},
                           content=" " * 17000).status_code == 413
