"""Three disjoint case windows, validation-only selection and exact test evidence."""
from datetime import datetime,timedelta
from io import BytesIO
from copy import deepcopy
import pytest
from server import event_log_temporal_model_quality as tm
from server import event_log_workspace as workspace

BASE=datetime(2026,1,1)
def t(n):return BASE+timedelta(days=n)
def packet(**kw):
    return {"format":tm.FORMAT,"boundaries":[tm._stamp(t(i)) for i in (0,5,10,15)],
            "end_activities":["Done"],"minimum_cases":1,"noise_levels":[0,.2],**kw}

def histories():
    return {
        "train-a":[(t(0),"A"),(t(1),"Done")],
        "train-b":[(t(2),"A"),(t(3),"Done"),(t(6),"Reopened")],
        "crossing":[(t(4),"A"),(t(5),"Done")],
        "validation-a":[(t(5),"A"),(t(6),"Done")],
        "validation-b":[(t(7),"A"),(t(8),"Done")],
        "test-fit":[(t(10),"A"),(t(11),"Done")],
        "test-new":[(t(12),"Novel"),(t(13),"Done")],
        "test-open":[(t(14),"A")],
        "outside":[(t(15),"A"),(t(16),"Done")],
    }

def test_windows_are_disjoint_and_use_only_visible_prefix():
    windows,outside=tm.partition(histories(),[t(i) for i in (0,5,10,15)],{"Done"})
    assert outside==1
    assert [len(w["cases"]) for w in windows]==[3,2,3]
    by={c["case_id"]:c for w in windows for c in w["cases"]}
    assert by["train-b"]["path"]==("A","Done")
    assert by["crossing"]["exclusion_reason"]=="not_completed_by_window_end"
    assert by["test-open"]["exclusion_reason"]=="not_completed_by_window_end"

def test_test_activity_does_not_influence_selected_model():
    paths=histories();settings=tm.normalize(packet())
    original,cases=tm.evaluate(paths,settings)
    assert original["test_scored_candidate_count"]==1
    assert not original["test_scores_used_for_selection"]
    assert original["test"]["fitting_cases_exact"]==1
    assert original["test"]["whole_assigned_case_fitting_share_bounds"]=={"lower":1/3,"upper":2/3}
    paths["test-new"]=[(t(12),"A"),(t(13),"Done")]
    changed,_=tm.evaluate(paths,settings)
    assert original["selection"]==changed["selection"]
    assert original["candidates"]==changed["candidates"]
    assert changed["test"]["fitting_cases_exact"]==2

def test_selection_never_scores_nonselected_models_on_test(monkeypatch):
    original=tm._scores;calls=[]
    def score(candidate,rows):
        calls.append((candidate["noise"],rows))
        return original(candidate,rows)
    monkeypatch.setattr(tm,"_scores",score)
    report,_=tm.evaluate(histories(),tm.normalize(packet()))
    assert len(calls)==5
    assert calls[-1][0]==report["selection"]["noise"]
    assert any("Novel" in path for path,_ in calls[-1][1])
    assert all("Novel" not in path for _,rows in calls[:-1] for path,_ in rows)

def test_tied_labels_and_long_traces_unknown(monkeypatch):
    paths=histories()
    paths["tie"]=[(t(11),"A"),(t(11),"Novel"),(t(12),"Done")]
    paths["long"]=[(t(12),"A"),(t(13),"A"),(t(14),"Done")]
    monkeypatch.setattr(tm,"MAX_TRACE",2)
    windows,_=tm.partition(paths,[t(i) for i in (0,5,10,15)],{"Done"})
    by={c["case_id"]:c for c in windows[2]["cases"]}
    assert by["tie"]["exclusion_reason"]=="ambiguous_timestamp"
    assert by["long"]["exclusion_reason"]=="trace_length_limit"

@pytest.mark.parametrize("change",[
    {"boundaries":[]},{"boundaries":[tm._stamp(t(0))]*4},
    {"end_activities":[]},{"end_activities":["Done","Done"]},
    {"noise_levels":[True]},{"noise_levels":[float("nan")]},{"noise_levels":[0,0]},
    {"noise_levels":[1]},{"minimum_cases":False},{"cohort":{}},
])
def test_strict_settings(change):
    with pytest.raises(workspace.EventLogWorkspaceError):tm.normalize(packet(**change))

@pytest.fixture
def log(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    rows=["case,activity,time"]
    for key,path in histories().items():
        for ts,label in path:rows.append(f"{key},{label},{tm._stamp(ts)}")
    return workspace.import_log(BytesIO(("\n".join(rows)+"\n").encode()),name="temporal",
        source_format="csv",mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]

def test_source_bound_real_log_and_exact_paging(log):
    spec=packet();result=tm.review(log,spec)
    assert result==tm.review(log,result["inputs"])
    assert len(result["selection"]["pnml_sha256"])==64
    body={"settings":spec,"expected_review_sha256":result["review_sha256"],"status":"all","offset":0,"limit":2}
    first=tm.evidence(log,body);second=tm.evidence(log,{**body,"offset":first["next_offset"]})
    assert first["total"]==3 and len(second["cases"])==1 and second["next_offset"] is None
    assert tm.evidence(log,{**body,"status":"unknown"})["total"]==1
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        tm.evidence(log,{**body,"expected_review_sha256":"0"*64})
    with workspace._LOCK:
        conn=workspace._connect()
        try:conn.execute("UPDATE events SET activity='Changed' WHERE log_id=? AND activity='Novel'",[log])
        finally:conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):tm.evidence(log,body)

def test_complete_budget_and_minimum(log,monkeypatch):
    with pytest.raises(workspace.EventLogWorkspaceError,match="minimum"):
        tm.review(log,packet(minimum_cases=3))
    monkeypatch.setattr(tm,"MAX_EVENTS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):tm.review(log,packet())
