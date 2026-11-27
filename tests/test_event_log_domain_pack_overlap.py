from copy import deepcopy
from io import BytesIO
import json

import duckdb
import pytest

from server import event_log_domain_pack_overlap as overlap
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from order_to_cash_fixture import order_to_cash, MAPPING, TYPES


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def installed():
    log = workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),
                               name="overlap", source_format="ocel")["log_id"]
    spec = {"version": "1.0.0", "mapping": deepcopy(MAPPING)}
    packs.install(log, "order_to_cash", spec)
    return log, spec


def test_invoice_overlap_counts_unique_cases_not_rule_instances():
    log, _ = installed()
    kind = TYPES["invoice"]
    req = {"case_type": kind, "rule_indices": [5, 4]}
    r = overlap.review(log, "order_to_cash", req)
    assert r["cases"] == 20
    assert r["unique_cases_with_confirmed_violation"] == 1
    assert r["rule_violation_instances"] == 2
    assert r["pairs"][0]["both_violated"] == 1
    assert r["pairs"][0]["confirmed_jaccard"] == 1
    assert [h["cases"] for h in r["confirmed_violation_count_histogram"]] == [19, 0, 1]
    assert r == overlap.review(log, "order_to_cash", {**req, "rule_indices": [4, 5]})
    assert not r["configuration_modified"]
    page = overlap.review(log, "order_to_cash", {**req, "expected_basis_sha256": r["basis_sha256"],
        "evidence": {"rule_indices": [4, 5], "mode": "all_violated", "after": None, "limit": 1}})
    assert page["total"] == 1 and page["next_after"] is None
    assert page["cases"][0]["case_id"] == "bd9"
    assert page["cases"][0]["violated_rule_indices"] == [4, 5]


def test_catalog_and_exact_rule_scope():
    log, _ = installed()
    groups = overlap.available(log, "order_to_cash")["groups"]
    assert {g["case_type"] for g in groups} == {TYPES["order"], TYPES["invoice"]}
    with pytest.raises(workspace.EventLogWorkspaceError, match="same object type"):
        overlap.review(log, "order_to_cash", {"case_type": TYPES["invoice"], "rule_indices": [1, 4]})


def test_pinned_evidence_refuses_changed_installation():
    log, spec = installed()
    req = {"case_type": TYPES["invoice"], "rule_indices": [4, 5]}
    r = overlap.review(log, "order_to_cash", req)
    packs.install(log, "order_to_cash", {**spec, "parameters": {"payment_terms_days": 10}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="source changed"):
        overlap.review(log, "order_to_cash", {**req, "expected_basis_sha256": r["basis_sha256"],
            "evidence": {"rule_indices": [4], "mode": "any_violated", "after": None, "limit": 1}})


def test_independent_mask_oracle_and_paging():
    conn = duckdb.connect()
    try:
        conn.execute("CREATE TABLE ov(case_id VARCHAR, violated BIGINT, unknown BIGINT, activated BIGINT)")
        conn.executemany("INSERT INTO ov VALUES (?,?,?,?)", [
            ("a", 3, 0, 3), ("b", 1, 2, 3), ("c", 0, 3, 3),
            ("d", 0, 0, 3), ("e", 0, 0, 0), ("f", 2, 0, 2)])
        basis = {"rules": [{"index": 4}, {"index": 5}]}
        r = overlap._summarize(conn, basis, 6)
        assert r["unique_cases_with_confirmed_violation"] == 3
        assert r["unique_cases_with_confirmed_or_unknown_violation"] == 4
        assert r["rule_violation_instances"] == 4
        assert [x["cases"] for x in r["confirmed_violation_count_histogram"]] == [3, 2, 1]
        p = r["pairs"][0]
        assert (p["both_violated"], p["possible_both_violated"],
                p["either_violated"], p["possible_either_violated"]) == (1, 3, 3, 4)
        ids, after = [], None
        while True:
            page = overlap._page(conn, basis, {"rule_indices": [4,5], "mode": "possible_all", "after": after, "limit": 1})
            assert page["total"] == 3
            ids += [x["case_id"] for x in page["cases"]]
            after = page["next_after"]
            if after is None:
                break
        assert ids == ["a","b","c"]
        assert r["rules"][0]["nonactivated"] == 2
        assert r["rules"][0]["satisfied"] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("spec", [
    {"case_type": "X", "rule_indices": [0]},
    {"case_type": "X", "rule_indices": [0,0]},
    {"case_type": "X", "rule_indices": [0,True]},
    {"case_type": "X", "rule_indices": [-1,0]},
    {"case_type": "X", "rule_indices": list(range(13))},
    {"case_type": "X", "rule_indices": [0,1], "extra": 1},
])
def test_bad_selection(spec):
    with pytest.raises(workspace.EventLogWorkspaceError):
        overlap.review("unused", "order_to_cash", spec)


def test_budget_refuses_partial_population(monkeypatch):
    log, _ = installed()
    req = {"case_type": TYPES["invoice"], "rule_indices": [4,5]}
    monkeypatch.setattr(overlap, "MAX_OUTCOME_ROWS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="outcome-row budget"):
        overlap.review(log, "order_to_cash", req)
    monkeypatch.setattr(overlap, "MAX_SOURCE_ROWS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="source-row budget"):
        overlap.review(log, "order_to_cash", req)
