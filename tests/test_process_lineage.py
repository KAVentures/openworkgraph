"""Exact same-tenant lineage view never promotes a model link into business truth."""
from fastapi.testclient import TestClient
import pytest

from gateway.process_collaboration import ProcessCollaborationError
from gateway.process_implementation_plans import ImplementationPlans
from gateway.process_lineage import ProcessLineage
from test_process_collaboration import store, person, write, app
from test_process_implementation_plans import setup, baseline_save, freeze


def test_lineage_resolves_exact_pins_without_promoting_business_claims(store):
    root, opportunities, baseline = setup(store)
    lineage = ProcessLineage(store)
    wid = root["workflow_id"]
    first = lineage.detail(person(), wid)
    assert first["process"]["current"]
    assert first["opportunity"]["reference"] is not None
    assert first["opportunity"]["pinned_process_matches_current"]
    assert first["implementation_plan"]["reference"] is None
    assert not first["complete_current_declared_chain"]
    assert not first["execution_authorized"]
    assert not first["evidence_references_independently_verified"]
    assert not first["business_outcomes_independently_verified"]

    plans = ImplementationPlans(store)
    baseline_save(plans, root, baseline)
    draft = lineage.detail(person(), wid)
    assert draft["implementation_plan"]["pinned_opportunity_matches_current"]
    assert not draft["implementation_plan"]["current_reviewed_baseline"]
    freeze(plans, root)
    reviewed = lineage.detail(person(), wid)
    assert reviewed["implementation_plan"]["current_reviewed_baseline"]
    assert reviewed["complete_current_declared_chain"]
    assert not reviewed["execution_authorized"]

    changed = store.save(person(), write("Changed source", expected=root["revision"]), wid)
    assert changed["process"]["revision"] > root["revision"]
    stale = lineage.detail(person(), wid)
    assert not stale["opportunity"]["pinned_process_matches_current"]
    assert not stale["complete_current_declared_chain"]
    assert stale["implementation_plan"]["reference"] == reviewed["implementation_plan"]["reference"]


def test_lineage_rejects_cross_tenant_and_missing_read_scope(store):
    root, _, _ = setup(store)
    wid = root["workflow_id"]
    lineage = ProcessLineage(store)
    with pytest.raises(ProcessCollaborationError) as foreign:
        lineage.detail(person(org="foreign"), wid)
    assert foreign.value.status == 404
    with pytest.raises(ProcessCollaborationError) as missing:
        lineage.detail(person(scopes=frozenset()), wid)
    assert missing.value.status == 403


def test_http_lineage_uses_human_scope_and_does_not_embed_raw_evidence(tmp_path):
    with TestClient(app(tmp_path)) as client:
        h = lambda value: {"Authorization": "Bearer " + value}
        made = client.post("/v1/human/processes", headers=h("alice"),
            json=write("Safe process").model_dump())
        assert made.status_code == 200, made.text
        wid = made.json()["process"]["workflow_id"]
        url = "/v1/human/processes/" + wid + "/lineage"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=h("admin")).status_code == 401
        assert client.get(url, headers=h("no-process")).status_code == 403
        assert client.get(url, headers=h("foreign")).status_code == 404
        read = client.get(url, headers=h("reader"))
        assert read.status_code == 200, read.text
        assert read.headers["cache-control"] == "no-store"
        result = read.json()
        assert result["process"]["current"]
        assert result["opportunity"]["reference"] is None
        assert result["implementation_plan"]["reference"] is None
        assert not result["new_sharing_authorized"]
        assert not result["execution_authorized"]
        assert not result["business_outcomes_independently_verified"]
        assert "raw_evidence" not in read.text
