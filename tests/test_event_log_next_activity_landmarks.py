"""Strict as-of next-activity validation; independent arithmetic and saved-log evidence."""
from datetime import datetime,timedelta
from io import BytesIO
import pytest

from server import event_log_next_activity_landmarks as lm
from server import event_log_workspace as workspace

BASE=datetime(2026,1,1)
def t(days): return BASE+timedelta(days=days)

def packet(**kwargs):
    return {"format":lm.FORMAT,"cutoffs":["2026-01-05T00:00:00Z"],"horizon_days":3,
            "end_activities":["Done"],"history":2,"min_support":1,"minimum_training_cases":1,**kwargs}

def histories():
    return {
        "train":[(t(0),"A"),(t(1),"B"),(t(2),"Done")],
        "open":[(t(1),"A"),(t(4),"B"),(t(5),"Done")],
        "late":[(t(2),"A"),(t(10),"C")],
        "tie":[(t(2),"A"),(t(5),"B"),(t(5),"C")],
        "future":[(t(5),"A"),(t(6),"Done")],
        "ambiguous_train":[(t(0),"A"),(t(0),"C"),(t(1),"Done")],
        "ambiguous_open":[(t(1),"A"),(t(1),"C"),(t(5),"B")],
    }

def test_strict_cutoff_and_all_open_population():
    report,rows=lm.evaluate_landmark(histories(),t(4),t(7),lm.normalize(packet()))
    assert report["training_cases"]==1
    assert report["training_cases_excluded_ambiguous_timestamps"]==1
    assert report["open_at_cutoff"]==4
    by={r["case_id"]:r for r in rows}
    assert by["open"]["prefix_events"]==1
    assert by["open"]["actual_next_activity"]=="B"
    assert by["open"]["model"][0]["activity"]=="B"
    assert by["late"]["outcome_reason"]=="no_event_by_observation_end"
    assert by["tie"]["outcome_reason"]=="ambiguous_next_timestamp"
    assert by["ambiguous_open"]["abstention_reason"]=="ambiguous_prefix_timestamp"
    assert report["top1"]["predicted"]==3
    assert report["top1"]["hits"]==1
    assert report["top1"]["whole_population_hit_share_bounds"]=={"lower":.25,"upper":.75}
    assert report["top1"]["resolved_accuracy"]==1

def test_future_edits_cannot_change_training_or_predictions():
    paths=histories()
    settings=lm.normalize(packet())
    _,before=lm.evaluate_landmark(paths,t(4),t(7),settings)
    paths["open"]=[(t(1),"A"),(t(4),"Unseen"),(t(5),"New")]
    paths["future"].append((t(7),"Done"))
    _,after=lm.evaluate_landmark(paths,t(4),t(7),settings)
    assert [(r["case_id"],r["model"],r["history_used"]) for r in before]==[
        (r["case_id"],r["model"],r["history_used"]) for r in after]
    assert before[0]["actual_next_activity"]!=after[0]["actual_next_activity"]

def test_same_timestamp_same_label_is_resolved():
    assert lm._next_observed([(t(4),"B"),(t(4),"B")],0,t(5))==("B",None)

def test_no_training_abstains_not_false_predictions():
    report,_=lm.evaluate_landmark(histories(),t(4),t(7),lm.normalize(packet(minimum_training_cases=2)))
    assert report["top1"]["predicted"]==0
    assert report["top1"]["whole_population_hit_share_bounds"]=={"lower":0,"upper":0}

def test_paired_bounds_respect_identical_guesses_and_end_label():
    def row(a,b,actual=None,reason="unresolved"):
        return {"model":[{"activity":a}],"baseline":[{"activity":b}],
                "actual_next_activity":actual,"outcome_reason":reason}
    rows=[row("A","A"),row("A","B"),row(None,"B"),row("A",None),row("A","B","A",None)]
    result=lm._paired(rows,"baseline")
    assert result["paired_predictions"]==5 and result["paired_resolved"]==1
    assert result["paired_hit_share_change_bounds"]=={"lower":-.2,"upper":.6}

@pytest.mark.parametrize("change",[
    {"cutoffs":[]},{"cutoffs":["2026-01-05"]},{"cutoffs":["2026-01-05T01:00:00+01:00"]},
    {"cutoffs":["2026-01-05T00:00:00Z"]*2},{"horizon_days":True},{"horizon_days":366},
    {"end_activities":["Done","Done"]},{"history":5},{"cohort":{"contains":["Done"]}},
    {"minimum_training_cases":0},{"min_support":False}])
def test_refuses_invalid_inputs(change):
    with pytest.raises(workspace.EventLogWorkspaceError):
        lm.normalize(packet(**change))

@pytest.fixture
def log(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    lines=["case,activity,time"]
    for key,path in histories().items():
        for ts,activity in path:
            lines.append(f"{key},{activity},{ts.isoformat()}Z")
    return workspace.import_log(BytesIO(("\n".join(lines)+"\n").encode()),name="landmarks",
        source_format="csv",mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]

def test_real_log_replay_and_source_bound_evidence(log):
    spec=packet(cutoffs=["2026-01-05T00:00:00Z","2026-01-06T00:00:00Z"])
    report=lm.review(log,spec)
    assert len(report["landmarks"])==2
    assert report["landmarks"][0]["open_at_cutoff"]==4
    assert report["repeated_open_cases_across_landmarks"]>0
    assert report["historical_reconstruction_not_prospective_snapshot"]
    assert lm.review(log,report["inputs"])==report
    body={"settings":spec,"expected_review_sha256":report["review_sha256"],"cutoff":spec["cutoffs"][0],
          "status":"all","offset":0,"limit":2}
    first=lm.evidence(log,body)
    second=lm.evidence(log,{**body,"offset":first["next_offset"]})
    assert first["total"]==4 and len(second["cases"])==2 and second["next_offset"] is None
    assert not ({r["case_id"] for r in first["cases"]}&{r["case_id"] for r in second["cases"]})
    assert lm.evidence(log,{**body,"status":"unresolved"})["total"]==2
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        lm.evidence(log,{**body,"expected_review_sha256":"0"*64})
    with workspace._LOCK:
        conn=workspace._connect()
        try:conn.execute("UPDATE events SET activity='Other' WHERE log_id=? AND activity='B'",[log])
        finally:conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        lm.evidence(log,body)

def test_budget_refuses_complete_review(log,monkeypatch):
    monkeypatch.setattr(lm,"MAX_EVENTS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):
        lm.review(log,packet())


def test_synthetic_end_is_not_a_possible_recorded_next_event():
    r={"model":[{"activity":None,"probability":1.0}],"actual_next_activity":None,
       "outcome_reason":"no_event_by_observation_end"}
    score=lm._score([r],"model",1)
    assert score["whole_population_hit_share_bounds"]=={"lower":0,"upper":0}
    assert lm._calibration([r])["bins"][0]["observed_hit_share_bounds"]=={"lower":0,"upper":0}


def test_http_landmark_scope_and_stale_evidence(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code=r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as ws
from server.event_log_next_activity_landmarks import FORMAT
log=ws.import_log(BytesIO(b"case,activity,time\ntrain,A,2026-01-01T00:00:00Z\ntrain,Done,2026-01-02T00:00:00Z\nopen,A,2026-01-02T00:00:00Z\nopen,Done,2026-01-04T00:00:00Z\n"),
    name="landmark",source_format="csv",mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
body={"format":FORMAT,"cutoffs":["2026-01-03T00:00:00Z"],"horizon_days":3,"end_activities":["Done"],
      "minimum_training_cases":1,"min_support":1}
path=f"/v1/event-logs/{log}/next-activity/landmarks"
with TestClient(app) as client:
    assert client.post(path,json=body).status_code in (401,403)
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.post(path,json=body,headers=bearer).status_code in (401,403)
    auth={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    good=client.post(path,json=body,headers=auth)
    assert good.status_code==200,good.text
    assert good.headers["cache-control"]=="no-store"
    report=good.json()
    assert report["landmarks"][0]["top1"]["hits"]==1
    assert report["landmarks"][0]["observation_window_shortened_by_log_end"]
    evidence={"settings":body,"expected_review_sha256":report["review_sha256"],"cutoff":body["cutoffs"][0],
              "status":"all","offset":0,"limit":50}
    page=client.post(path+"/evidence",json=evidence,headers=auth)
    assert page.status_code==200,page.text
    assert page.json()["cases"][0]["case_id"]=="open"
    assert client.post(path+"/evidence",json=evidence,headers=bearer).status_code in (401,403)
    assert client.post(path+"/evidence",json={**evidence,"expected_review_sha256":"0"*64},headers=auth).status_code==422
    assert client.post(path,json={**body,"cohort":{}},headers=auth).status_code==422
    assert client.post(path,headers={**auth,"Content-Type":"application/json"},content=b"{}"+b" "*40000).status_code==413
'''
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    done=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                        env=env,text=True,capture_output=True,timeout=60)
    assert done.returncode==0,done.stdout+done.stderr
