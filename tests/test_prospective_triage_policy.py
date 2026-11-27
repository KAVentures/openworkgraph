"""Independent exact binary-label oracles and retained-log policy lifecycle."""
from __future__ import annotations

from contextlib import closing
from itertools import product
from datetime import timedelta
import json
import random

import pytest

from shared import prospective_triage_policy as core
from server import event_log_prediction_triage as triage
from server import event_log_prediction_snapshots as snapshots
from server import event_log_workspace as workspace
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO

START=datetime(2024,1,1,tzinfo=timezone.utc)
MAPPING={"case_column":"case","activity_column":"activity","timestamp_column":"time"}
SPEC={"end_activities":["Close"],"min_support":5}

def _csv(rows):
    out=StringIO()
    writer=csv.writer(out)
    writer.writerow(["case","activity","time"])
    writer.writerows(rows)
    return BytesIO(out.getvalue().encode())

def _history():
    rows=[]
    for i in range(20):
        t=START+timedelta(days=i)
        rows.extend([(f"c{i}","Receive",t.isoformat()),(f"c{i}","Check",(t+timedelta(hours=1)).isoformat()),
                     (f"c{i}","Close",(t+timedelta(hours=11)).isoformat())])
    t=START+timedelta(days=30)
    for i in range(6):
        rows.extend([(f"o{i}","Receive",t.isoformat()),(f"o{i}","Check",(t+timedelta(hours=1)).isoformat())])
    return rows



@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def settings(**extra):
    return {"label":"Capacity review","target":"remaining_time_over","remaining_seconds":20*3600,
            "capacity":2,"tie_seed":17,"false_positive_cost":2,"false_negative_cost":5,
            "minimum_group_cases":5,**extra}


def example():
    return [{"case_id":str(i),"last_activity":"A" if i%2 else "B","basis":"same_activity",
             "median_remaining_seconds":i*100,"naive_median_remaining_seconds":(5-i)*100,
             "outcome_probability":i/5,"base_rate":.4} for i in range(6)]


def test_equal_capacity_exact_ranking_and_exclusions():
    rows=example()
    rows[0]["naive_median_remaining_seconds"]=None
    spec=core.parse(settings())
    a=core.plan(rows,spec,frozen_outcome_available=False)
    b=core.plan(list(reversed(rows)),spec,frozen_outcome_available=False)
    assert a==b
    assert a["population"]==6 and a["eligible"]==5
    assert a["excluded"]==[{"case_id":"0","reason":"missing_or_invalid_paired_estimate"}]
    assert {r["case_id"] for r in a["rows"] if r["model_selected"]}=={"4","5"}
    assert {r["case_id"] for r in a["rows"] if r["baseline_selected"]}=={"1","2"}
    assert a["assignment_counts"]=={"both":0,"model_only":2,"baseline_only":2,"neither":1}
    same=[{**r,"median_remaining_seconds":100,"naive_median_remaining_seconds":100} for r in example()]
    p=core.plan(same,spec,frozen_outcome_available=False)
    assert p["assignment_counts"]["both"]==2
    assert p["model_cutoff_ties"]==p["baseline_cutoff_ties"]==6
    assert core.plan(same,core.parse(settings(capacity=100)),frozen_outcome_available=False)["effective_capacity"]==6


def test_outcome_ranking_uses_frozen_probabilities_and_rejects_missing_model():
    spec=core.parse(settings(target="frozen_outcome",remaining_seconds=None))
    with pytest.raises(core.PolicyError,match="no frozen outcome"):
        core.plan(example(),spec,frozen_outcome_available=False)
    plan=core.plan(example(),spec,frozen_outcome_available=True)
    assert {r["case_id"] for r in plan["rows"] if r["model_selected"]}=={"4","5"}
    assert plan["baseline_cutoff_ties"]==6


def test_sharp_bounds_against_independent_exhaustive_label_oracle():
    rng=random.Random(243)
    for _ in range(120):
        n=rng.randrange(1,8)
        rows=[{"case_id":str(i),"model_selected":rng.choice([True,False]),
               "baseline_selected":rng.choice([True,False]),"label":rng.choice([0,1,None]),
               "observation":"unknown"} for i in range(n)]
        spec=core.parse(settings(false_positive_cost=rng.randrange(1,9),false_negative_cost=rng.randrange(1,9)))
        result=core.summarize(rows,spec)
        unknown=[i for i,r in enumerate(rows) if r["label"] is None]
        arm_values={"model":[],"baseline":[]};paired=[]
        for assignment in product([0,1],repeat=len(unknown)):
            labels=[r["label"] for r in rows]
            for i,y in zip(unknown,assignment):
                labels[i]=y
            positives=sum(labels)
            for name in arm_values:
                selected=[r[name+"_selected"] for r in rows]
                tp=sum(a and y==1 for a,y in zip(selected,labels))
                fp=sum(a and y==0 for a,y in zip(selected,labels))
                fn=sum(not a and y==1 for a,y in zip(selected,labels))
                tn=sum(not a and y==0 for a,y in zip(selected,labels))
                cost=fp*spec.false_positive_cost+fn*spec.false_negative_cost
                arm_values[name].append((tp,fp,fn,tn,tp/sum(selected) if sum(selected) else None,
                                         tp/positives if positives else None,cost))
            a,b=arm_values["model"][-1],arm_values["baseline"][-1]
            paired.append((a[0]-b[0],a[6]-b[6]))
        for name,values in arm_values.items():
            report=result[name]
            for j,k in enumerate(["tp","fp","fn","tn"]):
                assert report["eventual_confusion_bounds"][k]==[min(v[j] for v in values),max(v[j] for v in values)]
            for j,k in [(4,"eventual_precision_bounds"),(5,"eventual_recall_bounds_when_defined")]:
                defined=[v[j] for v in values if v[j] is not None]
                expected=[round(min(defined),8),round(max(defined),8)] if defined else [None,None]
                assert report[k]==expected
            assert report["eventual_error_cost_bounds"]==[min(v[6] for v in values),max(v[6] for v in values)]
            assert report["recall_undefined_possible"]==any(v[5] is None for v in values)
        assert result["paired"]["captured_positive_change_bounds"]==[min(v[0] for v in paired),max(v[0] for v in paired)]
        assert result["paired"]["model_minus_baseline_error_cost_bounds"]==[min(v[1] for v in paired),max(v[1] for v in paired)]


def test_observation_strict_threshold_and_unknown_states():
    spec=core.parse(settings(remaining_seconds=100))
    row={"case_id":"a"}
    def check(current,valid,since):
        return core.observation(row,current,valid,since,spec)
    assert check({"a":(True,100,None)},True,101)==("finished",0)
    assert check({"a":(True,101,None)},True,101)==("finished",1)
    assert check({"a":(False,0,None)},True,100)==("still_open",None)
    assert check({"a":(False,0,None)},True,101)==("open_known_late",1)
    assert check({"a":(True,-1,None)},True,101)==("inconsistent",None)
    assert check({"a":(False,0,None)},False,101)==("inconsistent",None)
    assert check({},True,101)==("missing",None)


@pytest.mark.parametrize("extra",[
    {"capacity":True},{"capacity":0},{"tie_seed":-1},{"remaining_seconds":None},
    {"target":"frozen_outcome"},{"false_positive_cost":0,"false_negative_cost":0},
    {"label":" "},{"execute":True}])
def test_strict_settings(extra):
    with pytest.raises(core.PolicyError):
        core.parse(settings(**extra))


def imported(outcome=False):
    log=workspace.import_log(_csv(_history()),name="orders",source_format="csv",mapping=MAPPING)["log_id"]
    spec={**SPEC,"outcome":{"kind":"duration_over","seconds":20*3600}} if outcome else SPEC
    snap=snapshots.take(log,spec)
    return log,snap["snapshot_id"]


def freeze(log,sid,policy=None):
    policy=policy or settings()
    preview=triage.preview(log,sid,policy)
    result=triage.freeze(log,sid,{"policy":policy,"expected_policy_sha256":preview["policy_sha256"],"confirm_freeze":True})
    return preview,result


def test_real_snapshot_preview_freeze_append_score_exact_evidence_and_delete():
    log,sid=imported()
    preview,saved=freeze(log,sid)
    assert preview["paired_eligible_cases"]==6 and preview["effective_capacity"]==2
    assert preview["assignment_counts"]["both"]==2  # equal frozen scores in both arms
    assert saved["saved"] and not saved["already_saved"]
    assert freeze(log,sid)[1]["already_saved"]
    pid=saved["policy_id"]
    assert len(triage.listing(log,sid)["policies"])==1
    initial=triage.evaluate(log,sid,pid)
    assert initial["summary"]["unknown_labels"]==6
    assert initial["summary"]["paired"]["captured_positive_change_bounds"]==[0,0]
    assert initial["summary"]["paired"]["model_minus_baseline_error_cost_bounds"]==[0,0]
    assert initial["by_frozen_activity"]["groups"][0]["eligible_cases"]==6
    request={"expected_evaluation_sha256":initial["evaluation_sha256"],"filter":"all","offset":0,"limit":2}
    page=triage.evidence(log,sid,pid,request)
    assert len(page["rows"])==2 and page["next_offset"]==2
    assert {r["label"] for r in page["rows"]}=={None}
    late=START+timedelta(days=30,hours=1)
    newer=[(f"o{i}","Close",(late+timedelta(hours=10)).isoformat()) for i in range(4)]
    newer += [("o4","Close",(late+timedelta(hours=40)).isoformat()),("x1","Receive",(late+timedelta(hours=50)).isoformat())]
    workspace.append_log(log,_csv(newer),source_format="csv",mapping=MAPPING)
    report=triage.evaluate(log,sid,pid)
    assert report["summary"]["known_labels"]==6
    assert report["summary"]["known_positive_labels"]==2
    assert report["summary"]["observation_counts"]=={"finished":5,"open_known_late":1}
    assert report["evaluation_sha256"]!=initial["evaluation_sha256"]
    assert not report["intervention_executed"] and not report["actual_savings_verified"]
    assert "case_id" not in json.dumps(report)
    with pytest.raises(workspace.EventLogWorkspaceError,match="evaluation changed"):
        triage.evidence(log,sid,pid,request)
    request.update(expected_evaluation_sha256=report["evaluation_sha256"],filter="known_positive",limit=100)
    assert {r["case_id"] for r in triage.evidence(log,sid,pid,request)["rows"]}=={"o4","o5"}
    with pytest.raises(workspace.EventLogWorkspaceError,match="observation changed"):
        triage.preview(log,sid,settings(label="post-hoc"))
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed or unavailable"):
        triage.delete(log,sid,pid,{"expected_policy_sha256":"0"*64,"confirm_delete":True})
    assert triage.delete(log,sid,pid,{"expected_policy_sha256":saved["policy_sha256"],"confirm_delete":True})["deleted"]
    assert not triage.listing(log,sid)["policies"]
    assert len(snapshots.listing(log)["snapshots"])==1


def test_frozen_outcome_open_case_not_imputed_and_backfill_inconsistent():
    log,sid=imported(outcome=True)
    _,saved=freeze(log,sid,settings(target="frozen_outcome",remaining_seconds=None))
    late=START+timedelta(days=30,hours=1)
    workspace.append_log(log,_csv([("o1","Close",(late+timedelta(hours=30)).isoformat()),
                                  ("o2","Close",(late-timedelta(minutes=1)).isoformat()),
                                  ("x1","Receive",(late+timedelta(hours=50)).isoformat())]),
                         source_format="csv",mapping=MAPPING)
    report=triage.evaluate(log,sid,saved["policy_id"])
    # o2's backfill precedes its Check and leaves it open under latest-activity semantics.
    assert report["summary"]["known_labels"]==1 and report["summary"]["unknown_labels"]==5
    assert report["summary"]["known_positive_labels"]==1


def test_preview_stale_digest_and_source_deletion_cascade():
    log,sid=imported()
    preview=triage.preview(log,sid,settings())
    with pytest.raises(workspace.EventLogWorkspaceError,match="preview changed"):
        triage.freeze(log,sid,{"policy":settings(capacity=1),"expected_policy_sha256":preview["policy_sha256"],"confirm_freeze":True})
    _,saved=freeze(log,sid)
    snapshots.delete(log,sid)
    with closing(snapshots._connect()) as db:
        assert db.execute("SELECT count(*) FROM triage_policies").fetchone()[0]==0
    snap=snapshots.take(log,SPEC)
    freeze(log,snap["snapshot_id"])
    workspace.delete_log(log)
    with closing(snapshots._connect()) as db:
        assert db.execute("SELECT count(*) FROM triage_policies").fetchone()[0]==0


def test_snapshot_integrity_and_corrupt_policy_never_scores():
    log,sid=imported()
    _,saved=freeze(log,sid)
    pid=saved["policy_id"]
    with closing(snapshots._connect()) as db,db:
        db.execute("UPDATE triage_policies SET definition=? WHERE policy_id=?",[json.dumps(settings(capacity=1)),pid])
    with pytest.raises(workspace.EventLogWorkspaceError,match="integrity changed"):
        triage.evaluate(log,sid,pid)


def test_authenticated_routes_and_no_store():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import event_log_routes as routes
    from unittest.mock import patch
    log,sid=imported()
    app=FastAPI();app.include_router(routes.router)
    base=f"/v1/event-logs/{log}/prediction-snapshots/{sid}/triage-policies"
    owner={"Authorization":"OWG-Session local"}
    with patch.object(routes,"_require_dashboard",side_effect=lambda r: None if r.headers.get("authorization")==owner["Authorization"] else (_ for _ in ()).throw(__import__("fastapi").HTTPException(403,"session required"))):
        with TestClient(app) as client:
            assert client.get(base).status_code==403
            assert client.post(base+"/preview",json=settings(),headers={"Authorization":"Bearer api"}).status_code==403
            preview=client.post(base+"/preview",json=settings(),headers=owner)
            assert preview.status_code==200,preview.text
            assert preview.headers["cache-control"]=="no-store"
            payload={"policy":settings(),"expected_policy_sha256":preview.json()["policy_sha256"],"confirm_freeze":True}
            saved=client.post(base,json=payload,headers=owner)
            assert saved.status_code==200,saved.text
            endpoint=base+"/"+saved.json()["policy_id"]
            scored=client.get(endpoint+"/evaluation",headers=owner)
            assert scored.status_code==200,scored.text
            assert scored.headers["cache-control"]=="no-store"
            assert client.post(endpoint+"/evidence",json={"filter":"all","offset":0,"limit":10,
                "expected_evaluation_sha256":scored.json()["evaluation_sha256"]},headers=owner).status_code==200
