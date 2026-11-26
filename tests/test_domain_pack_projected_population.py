"""Eventless retained objects are excluded explicitly, not treated as passes."""
from copy import deepcopy
from io import BytesIO
import json

import pytest

from order_to_cash_fixture import order_to_cash, MAPPING, TYPES
from server import event_log_workspace as workspace
from server import event_log_domain_packs as packs
from server import event_log_domain_pack_population as population
from server import event_log_domain_pack_deadlines as deadlines
from server import event_log_domain_pack_overlap as overlap
from server import event_log_domain_pack_priority as priority


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    doc = order_to_cash()
    doc["objects"].extend([
        {"id":"unlinked-invoice","type":TYPES["invoice"]},
        {"id":"unlinked-order","type":TYPES["order"]},
        {"id":"unlinked-item","type":TYPES["item"]},
    ])
    log_id = workspace.import_log(BytesIO(json.dumps(doc).encode()),
                                  name="Projection coverage", source_format="ocel")["log_id"]
    packs.install(log_id, "order_to_cash", {"version":"1.0.0","mapping":deepcopy(MAPPING)})
    return log_id


def expected(kind):
    return {"case_type":kind,"retained_objects":21,"projected_cases":20,
            "objects_without_linked_events":1,"all_retained_objects_projected":False,
            "source_completeness_verified":False}


def test_deadline_population_and_pinned_evidence(log):
    req={"parameter":"payment_terms_days","values":[60]}
    result=deadlines.review(log,"order_to_cash",req)
    assert result["basis"]["case_populations"][TYPES["invoice"]]==expected(TYPES["invoice"])
    rule=result["rules"][0]
    assert rule["population"]==expected(TYPES["invoice"])
    assert rule["baseline"]["cases"]==20
    assert rule["alternatives"][0]["violating_cases"]==1
    page=deadlines.review(log,"order_to_cash",{
        **req,"expected_basis_sha256":result["basis_sha256"],
        "evidence":{"rule_index":rule["rule_index"],"value":60,
                    "state":"missing_response","after":None,"limit":20}})
    assert page["population"]==expected(TYPES["invoice"])
    assert [r["case_id"] for r in page["cases"]]==["bd9"]


def test_overlap_population_does_not_count_unlinked_case(log):
    req={"case_type":TYPES["invoice"],"rule_indices":[4,5]}
    result=overlap.review(log,"order_to_cash",req)
    assert result["basis"]["population"]==expected(TYPES["invoice"])
    assert result["cases"]==20
    assert sum(x["cases"] for x in result["confirmed_violation_count_histogram"])==20
    assert result["unique_cases_with_confirmed_violation"]==1


def test_priority_export_preserves_coverage(log):
    req={"case_type":TYPES["invoice"],"weights":[{"rule_index":4,"weight":5}],
         "minimum_confirmed":1,"include_uncertain":True,"limit":10}
    result=priority.review(log,"order_to_cash",req)
    assert result["basis"]["population"]==expected(TYPES["invoice"])
    assert result["cases_evaluated"]==20
    exported=priority.export(log,"order_to_cash",{
        **{k:v for k,v in req.items() if k!="limit"},
        "expected_basis_sha256":result["basis_sha256"]})
    assert exported["basis"]["population"]==expected(TYPES["invoice"])
    assert [r["case_id"] for r in exported["cases"]]==["bd9"]


def test_population_mismatch_fails_closed():
    with pytest.raises(workspace.EventLogWorkspaceError,match="does not match"):
        population.require_count({"projected_cases":20},21)
