"""Independent reference definitions for R23 quality-management packs."""
import pytest
from server.event_log_domain_packs_quality_management import QUALITY_MANAGEMENT_PACKS

CASES = {
    "corrective_action_management": ("action_case", "opened investigated queried planned approved implemented verified closed", "queried", "investigate_hours", "opened", "investigated", "approved", ("implemented", "verified"), "capa_opened_to_investigated"),
    "calibration_administration": ("calibration_case", "requested scheduled performed exception certificate reviewed closed", "exception", "schedule_hours", "requested", "scheduled", "requested", ("performed", "reviewed"), "calq_requested_to_scheduled"),
    "controlled_document_revision": ("revision_case", "requested drafted returned reviewed approved published acknowledged closed", "returned", "draft_hours", "requested", "drafted", "published", ("drafted", "approved"), "docctl_requested_to_drafted"),
    "product_recall_notification_admin": ("notification_case", "opened scoped drafted approved dispatched exception reconciled closed", "exception", "scope_hours", "opened", "scoped", "approved", ("dispatched", "reconciled"), "recall_opened_to_scoped"),
}


def test_catalog_keys_and_metric_names():
    assert set(QUALITY_MANAGEMENT_PACKS) == set(CASES)
    names = [m["metric"]["name"] for versions in QUALITY_MANAGEMENT_PACKS.values()
             for definition in versions.values() for m in definition["metrics"]]
    assert len(names) == len(set(names)) == 28
    assert all(len(n) <= 40 for n in names)


@pytest.mark.parametrize("key", CASES)
def test_reference_and_rules(key):
    case, sequence, optional, parameter, start, end, unique, separation, metric = CASES[key]
    roles = sequence.split()
    pack = QUALITY_MANAGEMENT_PACKS[key]["1.0.0"]
    assert pack["pack_id"] == key
    assert pack["object_types"][0]["role"] == case
    assert [r["role"] for r in pack["activities"]] == roles
    assert [r["role"] for r in pack["activities"] if not r["required"]] == [optional]
    assert pack["reference_model"]["types"][0]["steps"] == [
        {"optional": "@"+r} if r == optional else "@"+r for r in roles]
    assert len(pack["reference_model"]["arcs"]) == len(roles)
    assert len(pack["parameters"]) == 4
    assert len(pack["metrics"]) == 7
    rules = [r["rule"] for r in pack["compliance"]]
    assert {"template": "response_within", "a": "@"+start, "b": "@"+end,
            "seconds": "$"+parameter} in rules
    assert {"template": "existence", "a": "@"+unique, "min": 1, "max": 1} in rules
    assert {"template": "segregation_of_duties", "a": "@"+separation[0],
            "b": "@"+separation[1]} in rules
    assert any(m["metric"]["name"] == metric for m in pack["metrics"])
    # Every precedence edge must follow the reference order; skipping the
    # optional role cannot accidentally trigger a prerequisite to a later step.
    for r in rules:
        if r["template"] == "precedence":
            a, b = r["a"][1:], r["b"][1:]
            assert roles.index(a) < roles.index(b)
            assert a != optional
    assert not pack["object_rules"]
    assert len(pack["assumptions"]) >= 7
