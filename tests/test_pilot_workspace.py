"""Transactional accepted pilot heads, minimized inventory and withdrawal."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from evals import prospective_pilot_cohort as cohort
from evals import pilot_workspace as workspace
from evals import pilot_execution as execution
from test_pilot_blinded_batches import study,complete,sheet,bundle
from test_pilot_execution import start,finish,disposition


def open_study(tmp_path,n=2,protocol="single"):
    p,s,l,truth=study(n,protocol)
    db=tmp_path/"study.db"
    v=workspace.initialize(db,p,s,expected_plan_sha256=p["plan_sha256"],
                           expected_schedule_sha256=s["schedule_sha256"],confirmed=True)
    return db,v,truth


def update(db,v,actions):
    return workspace.append(db,actions,expected_head_sha256=v["head_sha256"],confirmed=True)


def completion(db,v,eid,arm,now):
    l,material=complete(v["plan"],v["schedule"],v["ledger"],eid,arm,now)
    actions=[e["action"] for e in l["events"][v["ledger"]["revision"]:]]
    next=update(db,v,actions)
    assert next["ledger"]==l
    return next,material


def batch(tmp_path,db,v,truth,materials,name):
    packet,key=tmp_path/(name+"-packet.json"),tmp_path/(name+"-key.json")
    next=workspace.prepare_batch(db,truth,materials,expected_head_sha256=v["head_sha256"],
                                 packet_path=packet,key_path=key,confirmed=True)
    return next,cohort._load(packet),cohort._load(key)


def test_durable_exact_head_and_single_winner(tmp_path):
    db,v,truth=open_study(tmp_path)
    assert workspace.status(db)==v and v["revision"]==0
    rows=v["schedule"]["episodes"]
    actions=[[start(v["plan"],row["episode_id"],row["arm_order"][0],1)] for row in rows]
    def run(a):
        try:return update(db,v,a)["revision"]
        except workspace.Conflict:return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,actions))
    assert results.count(1)==1 and results.count("conflict")==1
    current=workspace.status(db)
    assert current["previous_head_sha256"]==v["head_sha256"]
    h=workspace.history(db,expected_head_sha256=current["head_sha256"])
    assert [r["operation"] for r in h["history"]]==["initialize","append"]
    with pytest.raises(workspace.Conflict):workspace.status(db,expected_head_sha256=v["head_sha256"])
    assert workspace.status(db)==current


def test_minimized_registered_batches_partial_and_complete(tmp_path):
    db,v,truth=open_study(tmp_path,1)
    row=v["schedule"]["episodes"][0];eid=row["episode_id"]
    materials=[]
    for arm in row["arm_order"]:
        v,m=completion(db,v,eid,arm,v["ledger"]["revision"]+1);materials.append(m)
    v,p,k=batch(tmp_path,db,v,truth,materials[:1],"first")
    v,p2,k2=batch(tmp_path,db,v,truth,materials[1:],"second")
    result=workspace.reconcile(db,truth,[bundle(p,k,sheet(p))],expected_head_sha256=v["head_sha256"],confirmed=True)
    assert result["result"]["reviewed_attempts"]==1
    assert result["result"]["score"]["coverage"]["complete_paired_episodes"]==0
    assert result["active_batches"]==2 and result["submitted_batches"]==1
    result=workspace.reconcile(db,truth,[bundle(p,k,sheet(p)),bundle(p2,k2,sheet(p2))],
                              expected_head_sha256=v["head_sha256"],confirmed=True)
    assert result["result"]["score"]["canonical_scorer_invoked"]
    assert workspace.status(db)==v
    with sqlite3.connect(db) as conn:
        raw=conn.execute("SELECT body FROM workspace").fetchone()[0]
        for forbidden in ("review_text","Approved neutral prompt","Approved neutral response","independent_review"):
            assert forbidden not in raw


def test_batch_conflict_retirement_and_reissue(tmp_path):
    db,v,truth=open_study(tmp_path,1);row=v["schedule"]["episodes"][0]
    v,m=completion(db,v,row["episode_id"],row["arm_order"][0],1)
    v,p,k=batch(tmp_path,db,v,truth,[m],"one")
    with pytest.raises(ValueError):batch(tmp_path,db,v,truth,[m],"duplicate")
    assert not (tmp_path/"duplicate-packet.json").exists()
    assert workspace.status(db)==v
    v=workspace.retire_batch(db,k["batch_sha256"],expected_head_sha256=v["head_sha256"],confirmed=True)
    assert v["batches"][0]["retirement_reason"]=="reissue"
    with pytest.raises(workspace.Conflict):
        workspace.reconcile(db,truth,[bundle(p,k,sheet(p))],expected_head_sha256=v["head_sha256"],confirmed=True)
    v,p2,k2=batch(tmp_path,db,v,truth,[m],"reissued")
    assert k["batch_sha256"]!=k2["batch_sha256"] and p!=p2
    assert [b["status"] for b in v["batches"]]==["retired","active"]


def test_withdrawal_retires_mixed_batch_and_preserves_prior_failures(tmp_path):
    db,v,truth=open_study(tmp_path);rows=v["schedule"]["episodes"]
    eid=rows[0]["episode_id"];other=rows[1]["episode_id"]
    a,b,c=rows[0]["arm_order"]
    v,m=completion(db,v,eid,a,1)
    v=update(db,v,[start(v["plan"],eid,b,3),disposition(eid,b,4,"model_failed")])
    v,m2=completion(db,v,other,rows[1]["arm_order"][0],5)
    v,p,k=batch(tmp_path,db,v,truth,[m,m2],"mixed")
    before=v
    v=workspace.withdraw_episode(db,eid,7,expected_head_sha256=v["head_sha256"],confirmed=True)
    assert v["ledger"]["events"][:before["ledger"]["revision"]]==before["ledger"]["events"]
    assert v["withdrawn_episode_ids"]==[eid] and v["batches"][0]["retirement_reason"]=="withdrawal"
    coverage=workspace.coverage(db,expected_head_sha256=v["head_sha256"])["coverage"]
    by_cell={(r["episode_id"],r["arm"]):r["status"] for r in coverage["incomplete_worklist"]}
    assert by_cell[eid,a]=="withdrawn" and by_cell[eid,b]=="model_failed" and by_cell[eid,c]=="withdrawn"
    assert by_cell[other,m2["arm"]]=="review_unavailable"
    with pytest.raises(ValueError):batch(tmp_path,db,v,truth,[m],"withdrawn")
    v,p2,k2=batch(tmp_path,db,v,truth,[m2],"remaining")
    result=workspace.reconcile(db,truth,[bundle(p2,k2,sheet(p2))],expected_head_sha256=v["head_sha256"],confirmed=True)
    assert result["result"]["reviewed_attempts"]==1
    assert result["result"]["score"]["coverage"]["enrolled"]==2


@pytest.mark.parametrize("kind",["body","history_operation","history_prev","missing_history","duplicate_json"])
def test_corrupt_storage_refused(tmp_path,kind):
    db,v,truth=open_study(tmp_path,1);row=v["schedule"]["episodes"][0]
    v=update(db,v,[start(v["plan"],row["episode_id"],row["arm_order"][0],1)])
    with sqlite3.connect(db) as conn:
        if kind=="body":
            value=deepcopy(v);value["withdrawn_episode_ids"]=["e0"]
            conn.execute("UPDATE workspace SET body=?",(json.dumps(value),))
        elif kind=="history_operation":conn.execute("UPDATE history SET operation='retire_batch' WHERE revision=1")
        elif kind=="history_prev":conn.execute("UPDATE history SET previous_head=? WHERE revision=1",("0"*64,))
        elif kind=="missing_history":conn.execute("DELETE FROM history WHERE revision=0")
        else:conn.execute("UPDATE workspace SET body=?",(cohort.canonical(v).decode()[:-1]+',"revision":1}',))
    with pytest.raises(ValueError):workspace.status(db)


def test_unregistered_packet_and_failed_output_not_published(tmp_path,monkeypatch):
    db,v,truth=open_study(tmp_path,1);row=v["schedule"]["episodes"][0]
    v,m=completion(db,v,row["episode_id"],row["arm_order"][0],1)
    from evals import pilot_blinded_batches as blinded
    p,k=blinded.prepare(v["plan"],v["schedule"],v["ledger"],truth,[m],**workspace._pins(v))
    with pytest.raises(workspace.Conflict):
        workspace.reconcile(db,truth,[bundle(p,k,sheet(p))],expected_head_sha256=v["head_sha256"],confirmed=True)
    real=cohort._write
    def fail(path,data):
        if str(path).endswith("-packet.json"):raise OSError("test failure")
        real(path,data)
    monkeypatch.setattr(cohort,"_write",fail)
    with pytest.raises(OSError):batch(tmp_path,db,v,truth,[m],"failed")
    assert workspace.status(db)==v
    assert (tmp_path/"failed-key.json").exists() and not (tmp_path/"failed-packet.json").exists()


def test_exclusive_db_and_cli(tmp_path):
    db,v,truth=open_study(tmp_path,1)
    with pytest.raises(FileExistsError):
        workspace.initialize(db,v["plan"],v["schedule"],expected_plan_sha256=v["plan"]["plan_sha256"],
                             expected_schedule_sha256=v["schedule"]["schedule_sha256"],confirmed=True)
    absent=tmp_path/"absent.db"
    with pytest.raises(ValueError):workspace.status(absent)
    assert not absent.exists()
    out=tmp_path/"status.json"
    assert workspace.main(["status","--db",str(db),"--out",str(out),"--confirm"])==0
    assert cohort._load(out)==v
    with pytest.raises(SystemExit):workspace.main(["status","--db",str(db),"--out",str(out),"--confirm"])
