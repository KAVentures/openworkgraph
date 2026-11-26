from copy import deepcopy
from io import BytesIO
import json

import duckdb
import pytest

from server import event_log_domain_pack_priority as priority
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from order_to_cash_fixture import order_to_cash, MAPPING, TYPES


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def installed():
    log = workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),
                               name="priority", source_format="ocel")["log_id"]
    spec = {"version":"1.0.0","mapping":deepcopy(MAPPING)}
    saved = packs.install(log,"order_to_cash",spec)
    return log,spec,saved


def request(**changes):
    return {"case_type":TYPES["invoice"],"weights":[{"rule_index":4,"weight":5},{"rule_index":5,"weight":2}],
            "minimum_confirmed":7,"include_uncertain":True,"limit":1,**changes}


def test_installed_invoice_worklist_exact_points_and_no_write():
    log,_,saved=installed()
    result=priority.review(log,"order_to_cash",request())
    assert result["cases_evaluated"]==20
    assert result["confirmed_threshold_cases"]==1
    assert result["uncertain_threshold_cases"]==0
    assert result["below_threshold_cases"]==19
    assert result["maximum_score"]==7
    assert result["cases"]==[{"case_id":"bd9","lower":7,"upper":7,"threshold_status":"confirmed",
                              "violated_rule_indices":[4,5],"unknown_rule_indices":[]}]
    assert result["next_after"] is None
    assert not result["configuration_modified"]
    assert packs.installed(log)["installs"][0]["installation_token"]==saved["installation_token"]


def test_catalog_and_mixed_case_types_refused():
    log,_,_=installed()
    groups=priority.available(log,"order_to_cash")["groups"]
    assert {g["case_type"] for g in groups}=={TYPES["order"],TYPES["invoice"],TYPES["item"]}
    with pytest.raises(workspace.EventLogWorkspaceError,match="one exact case type"):
        priority.review(log,"order_to_cash",request(weights=[{"rule_index":1,"weight":7}]))


def test_exact_ordering_and_uncertainty_oracle():
    conn=duckdb.connect()
    try:
        conn.execute("CREATE TABLE priority_cases(case_id VARCHAR,lo BIGINT,hi BIGINT,violations BIGINT,unknowns BIGINT)")
        conn.executemany("INSERT INTO priority_cases VALUES (?,?,?,?,?)",[
            ("a",7,7,3,0),("b",5,7,1,2),("c",5,7,1,2),("d",5,5,1,0),
            ("e",0,7,0,3),("f",2,2,2,0),("g",0,0,0,0)])
        basis={"rules":[{"rule_index":4},{"rule_index":5}],"minimum_confirmed":5,"include_uncertain":True}
        all_ids=[];after=None
        while True:
            result=priority._page(conn,basis,after,1)
            assert result["cases_evaluated"]==7
            assert result["confirmed_threshold_cases"]==4
            assert result["uncertain_threshold_cases"]==1
            assert result["below_threshold_cases"]==2
            assert result["matching_cases"]==5
            all_ids.extend(c["case_id"] for c in result["cases"])
            after=result["next_after"]
            if after is None:break
        assert all_ids==["a","b","c","d","e"]
        certain=priority._page(conn,{**basis,"include_uncertain":False},None,100)
        assert [x["case_id"] for x in certain["cases"]]==["a","b","c","d"]
        assert certain["uncertain_threshold_cases"]==1
    finally:
        conn.close()


def test_pagination_refuses_changed_installation_and_selection():
    log,spec,_=installed()
    req=request(minimum_confirmed=1)
    result=priority.review(log,"order_to_cash",req)
    continuation={**req,"expected_basis_sha256":result["basis_sha256"],
                  "after":{"case_id":"bd9","lower":7,"upper":7}}
    assert priority.review(log,"order_to_cash",continuation)["cases"]==[]
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.review(log,"order_to_cash",{**continuation,"minimum_confirmed":2})
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.review(log,"order_to_cash",continuation)


@pytest.mark.parametrize("changes",[
    {"weights":[]},{"weights":[{"rule_index":4,"weight":True}]},
    {"weights":[{"rule_index":4,"weight":101}]},
    {"weights":[{"rule_index":4,"weight":4},{"rule_index":4,"weight":4}]},
    {"minimum_confirmed":8},{"include_uncertain":1},{"limit":0},
    {"after":{"lower":1,"upper":2,"case_id":"x"}}
])
def test_strict_invalid_request(changes):
    with pytest.raises(workspace.EventLogWorkspaceError):
        priority.review("unused","order_to_cash",request(**changes))


def test_complete_work_budgets(monkeypatch):
    log,_,_=installed()
    monkeypatch.setattr(priority,"MAX_OUTCOMES",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="outcome budget"):
        priority.review(log,"order_to_cash",request())
    monkeypatch.setattr(priority,"MAX_SOURCE_ROWS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="source-row budget"):
        priority.review(log,"order_to_cash",request())


def test_complete_export_exact_and_pinned(monkeypatch):
    log,spec,_=installed()
    req=request(minimum_confirmed=1)
    first=priority.review(log,"order_to_cash",req)
    export={k:v for k,v in req.items() if k!="limit"}
    export["expected_basis_sha256"]=first["basis_sha256"]
    result=priority.export(log,"order_to_cash",export)
    assert result["complete_export"] is True
    assert result["basis_sha256"]==first["basis_sha256"]
    assert result["cases"]==first["cases"]
    assert len(result["cases"])==result["matching_cases"]
    assert result["next_after"] is None
    assert first["complete_export"] is False
    monkeypatch.setattr(priority,"MAX_EXPORT_CASES",0)
    with pytest.raises(workspace.EventLogWorkspaceError,match="Complete export exceeds"):
        priority.export(log,"order_to_cash",export)
    monkeypatch.setattr(priority,"MAX_EXPORT_CASES",5000)
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.export(log,"order_to_cash",export)


def test_complete_export_requires_pin():
    log,_,_=installed()
    req={k:v for k,v in request().items() if k!="limit"}
    with pytest.raises(workspace.EventLogWorkspaceError,match="exact reviewed selection"):
        priority.export(log,"order_to_cash",req)


def test_complete_export_empty_population():
    log,_,_=installed()
    req=request(case_type=TYPES["order"],weights=[{"rule_index":0,"weight":1}],
                minimum_confirmed=1)
    first=priority.review(log,"order_to_cash",req)
    result=priority.export(log,"order_to_cash",{
        **{k:v for k,v in req.items() if k!="limit"},"expected_basis_sha256":first["basis_sha256"]})
    assert result["complete_export"] is True
    assert result["matching_cases"]==0 and result["cases"]==[] and result["next_after"] is None
