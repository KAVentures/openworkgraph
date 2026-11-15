"""R15: selected current opportunity budget comparison, no spending authorization."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shared.process_opportunity_portfolio import Preview, summarize
from gateway.process_opportunity_portfolio import SelectedOpportunityBudget
from gateway.process_opportunity_review import SharedOpportunityStore
from gateway.process_collaboration import ProcessCollaborationError, SharedProcessWrite
from test_process_collaboration import store, person, model, app, grant, review
from test_shared_opportunity_review import root, save, decide, data, scenario


def selected(opps, process):
    artifact = opps.detail(person(), process["workflow_id"])["artifact"]
    return {"workflow_id": process["workflow_id"], "revision": artifact["revision"],
            "content_sha256": artifact["content_sha256"]}


def request(*selections, currency="USD", ceiling="3500.00", confirmed=True):
    return Preview(currency=currency, ceiling=ceiling, selections=list(selections), user_confirmed=confirmed)


def test_two_independent_current_opportunities_preview_only_estimated_budget(store):
    opps = SharedOpportunityStore(store)
    p1, p2 = root(store), root(store)
    save(opps, store, p1)
    save(opps, store, p2)
    decide(opps, store, p1, "approved")
    preview = SelectedOpportunityBudget(store)
    result = preview.preview(person("reader"), request(selected(opps, p1), selected(opps, p2)))
    assert result["status"] == "descriptive_budget_preview_only"
    assert result["selected_count"] == 2 and not result["blockers"]
    assert result["totals"] == {
        "selected_budget_estimate": "4000.00",
        "currently_approved_stage_budget_estimate": "2000.00",
        "unapproved_stage_budget_estimate": "2000.00",
        "envelope_headroom": "-500.00",
        "within_declared_envelope": False,
        "currency": "USD",
        "spending_authorized": False,
        "actual_expenditure_verified": False,
    }
    assert result["selection_scope"].startswith("explicit_pinned")
    assert result["possible_business_scope_overlap_not_deduplicated"]
    assert not result["realized_savings_verified"] and not result["execution_authorized"]
    assert not result["spending_authorized"]


def test_one_blocked_entry_withholds_all_totals_including_visible_valid_entries(store):
    opps = SharedOpportunityStore(store)
    p1, p2 = root(store), root(store)
    save(opps, store, p1)
    save(opps, store, p2)
    pin = selected(opps, p2)
    pin["content_sha256"] = "0" * 64
    result = SelectedOpportunityBudget(store).preview(person(), request(selected(opps, p1), pin))
    assert result["status"] == "withheld"
    assert result["totals"] is None
    assert result["blockers"] == [{
        "workflow_id": p2["workflow_id"],
        "reasons": ["opportunity_revision_or_content_changed"],
    }]
    assert result["rows"][1]["implementation_budget"] is None
    assert not result["spending_authorized"]


def test_changed_opportunity_revision_and_changed_review_basis_are_not_reused(store):
    opps = SharedOpportunityStore(store)
    process = root(store)
    save(opps, store, process)
    original = selected(opps, process)
    save(opps, store, process, expected=1)
    result = SelectedOpportunityBudget(store).preview(person(), request(original))
    assert result["status"] == "withheld" and result["totals"] is None
    assert "opportunity_revision_or_content_changed" in result["blockers"][0]["reasons"]
    current = selected(opps, process)
    changed_model = deepcopy(process["process_model"])
    changed_model["opportunity_scenario"]["one_time_implementation_cost"] = 2000.0
    store.save(person(), SharedProcessWrite(
        title="New process basis", process_model=changed_model,
        expected_revision=1, user_confirmed=True, organization_sharing_confirmed=True,
    ), process["workflow_id"])
    result = SelectedOpportunityBudget(store).preview(person(), request(current))
    assert result["status"] == "withheld" and result["totals"] is None
    assert "revision_changed" in result["blockers"][0]["reasons"]


def test_unreviewed_scenario_missing_currency_and_stage_are_blocked(store):
    opps = SharedOpportunityStore(store)
    unreviewed = root(store, reviewed=False)
    missing = root(store, estimate=False)
    normal = root(store)
    save(opps, store, unreviewed)
    save(opps, store, missing)
    save(opps, store, normal)
    preview = SelectedOpportunityBudget(store)
    assert "independent_design_review_required" in preview.preview(
        person(), request(selected(opps, unreviewed)))["blockers"][0]["reasons"]
    assert "scenario_missing" in preview.preview(
        person(), request(selected(opps, missing)))["blockers"][0]["reasons"]
    assert "currency_conversion_not_supported" in preview.preview(
        person(), request(selected(opps, normal), currency="EUR"))["blockers"][0]["reasons"]
    decide(opps, store, normal, "rejected")
    assert "opportunity_stage_not_in_active_pipeline" in preview.preview(
        person(), request(selected(opps, normal)))["blockers"][0]["reasons"]


def test_cross_tenant_scope_and_missing_root_fail_closed(store):
    opps = SharedOpportunityStore(store)
    r = root(store)
    save(opps, store, r)
    ref = selected(opps, r)
    preview = SelectedOpportunityBudget(store)
    foreign = preview.preview(person(org="another"), request(ref))
    assert foreign["status"] == "withheld"
    assert foreign["blockers"][0]["reasons"] == ["unavailable_or_inaccessible"]
    assert foreign["totals"] is None
    nonexistent = preview.preview(person(), request({**ref, "workflow_id": "unknown_root"}))
    assert nonexistent["status"] == "withheld"
    assert nonexistent["blockers"][0]["reasons"] == ["unavailable_or_inaccessible"]
    with pytest.raises(ProcessCollaborationError):
        preview.preview(person(scopes=frozenset()), request(ref))
    with pytest.raises(ProcessCollaborationError):
        preview.preview(person(), request(ref, confirmed=False))


@pytest.mark.parametrize("bad", [
    "1e6", "-1", "01.00", "5.001", "Infinity", "100,000", "100.00.00",
    2000, 100.0, "", "1_000",
])
def test_invalid_ceiling_is_not_coerced(bad):
    with pytest.raises(ValidationError):
        Preview(currency="USD", ceiling=bad, selections=[{
            "workflow_id": "process_A", "revision": 1, "content_sha256": "a" * 64,
        }], user_confirmed=True)


def test_duplicate_roots_and_boolean_revision_rejected():
    ref = {"workflow_id": "process_A", "revision": 1, "content_sha256": "a" * 64}
    with pytest.raises(ValidationError):
        request(ref, ref)
    with pytest.raises(ValidationError):
        request({**ref, "revision": True})


def test_precise_decimal_and_negative_headroom_without_false_savings():
    rows = [
        {"workflow_id": "one", "implementation_budget": "0.01",
         "current_approved_assessment": True, "blockers": []},
        {"workflow_id": "two", "implementation_budget": "0.02",
         "current_approved_assessment": False, "blockers": []},
    ]
    result = summarize(rows, "0.02", "SEK")
    assert result["totals"]["selected_budget_estimate"] == "0.03"
    assert result["totals"]["envelope_headroom"] == "-0.01"
    assert result["totals"]["within_declared_envelope"] is False
    assert result["totals"]["currently_approved_stage_budget_estimate"] == "0.01"
    assert not result["actual_expenditure_verified"] and not result["spending_authorized"]


def test_authenticated_http_budget_preview_bound_to_active_saved_revisions(tmp_path):
    with TestClient(app(tmp_path)) as client:
        h = {"Authorization": "Bearer alice"}
        design = model().model_dump()
        design["opportunity_scenario"] = scenario()
        saved = client.post("/v1/human/processes", headers=h,
            json={"title": "Candidate", "process_model": design,
                  "user_confirmed": True, "organization_sharing_confirmed": True})
        assert saved.status_code == 200, saved.text
        r = saved.json()["process"]
        path = "/v1/human/processes/" + r["workflow_id"]
        access = client.post(path + "/access", headers=h,
            json={"expected_access_revision": 1, "members": [{"actor_id": "bob", "role": "reviewer"}],
                  "user_confirmed": True})
        assert access.status_code == 200, access.text
        peer = client.post(path + "/reviews", headers={"Authorization": "Bearer bob"},
            json={"revision": 1, "content_sha256": r["content_sha256"],
                  "decision": "reviewed_design", "expected_review_number": 0, "user_confirmed": True})
        assert peer.status_code == 200, peer.text
        outcome = client.post(path + "/opportunity", headers=h,
            json={"data": data(r).model_dump(mode="json"),
                  "expected_revision": 0, "expected_access_revision": 2,
                  "user_confirmed": True, "organization_sharing_confirmed": True})
        assert outcome.status_code == 200, outcome.text
        chosen = client.get(path + "/opportunity", headers=h).json()["artifact"]
        payload = request({"workflow_id": r["workflow_id"], "revision": chosen["revision"],
                           "content_sha256": chosen["content_sha256"]}).model_dump(mode="json")
        base = "/v1/human/process-opportunity-pipeline/preview"
        valid = client.post(base, headers={"Authorization": "Bearer reader"}, json=payload)
        assert valid.status_code == 200, valid.text
        assert valid.json()["totals"]["selected_budget_estimate"] == "2000.00"
        assert valid.headers["cache-control"] == "no-store"
        denied = client.post(base, headers={"Authorization": "Bearer foreign"}, json=payload)
        assert denied.status_code == 200 and denied.json()["totals"] is None
        assert client.post(base, headers={"Authorization": "Bearer reader"},
                           json={**payload, "user_confirmed": False}).status_code == 422
        assert client.post(base, headers={"Authorization": "Bearer reader", "Content-Type": "application/json"},
                           content='{"currency":"USD","currency":"EUR"}').status_code == 422
        assert client.post(base, headers={"Authorization": "Bearer reader", "Content-Type": "application/json"},
                           content=" " * 17000).status_code == 413
