"""Actual accepted-head and blinded-batch integration for prospective decisions."""
from copy import deepcopy
import json

import pytest

from evals import pilot_decision as decision
from evals import pilot_workspace as workspace
from evals import prospective_pilot_cohort as cohort
from test_pilot_workspace import open_study,completion,batch,update
from test_pilot_blinded_batches import bundle,sheet
from test_pilot_execution import start
from test_pilot_decision_bounds import thresholds


def freeze(db,state):
    return decision.freeze(db,thresholds(),expected_head_sha256=state["head_sha256"],confirmed=True)


def report(db,state,protocol,truth,batches,**changes):
    return decision.report(db,protocol,truth,batches,**{
        "expected_protocol_sha256":protocol["protocol_sha256"],
        "expected_head_sha256":state["head_sha256"],"confirmed":True,**changes})


def test_freeze_is_exact_initial_state_and_does_not_mutate(tmp_path):
    db,v,truth=open_study(tmp_path)
    p=freeze(db,v)
    assert p["initial_head_sha256"]==v["head_sha256"]
    assert workspace.status(db)==v
    assert freeze(db,v)==p
    with pytest.raises(ValueError):
        decision.freeze(db,thresholds(),expected_head_sha256=v["head_sha256"],confirmed=False)
    row=v["schedule"]["episodes"][0]
    v2=update(db,v,[start(v["plan"],row["episode_id"],row["arm_order"][0],1)])
    with pytest.raises(ValueError,match="before first"):
        freeze(db,v2)
    with pytest.raises(workspace.Conflict):freeze(db,v)
    assert report(db,v2,p,truth,[])["summary"]["complete_paired_episodes"]==0


def test_partial_and_complete_adjudication_exact_population(tmp_path):
    db,v,truth=open_study(tmp_path,2)
    p=freeze(db,v)
    materials=[]
    for row in v["schedule"]["episodes"]:
        for arm in row["arm_order"]:
            v,m=completion(db,v,row["episode_id"],arm,v["ledger"]["revision"]+1)
            materials.append(m)
    v,p1,k1=batch(tmp_path,db,v,truth,materials[:3],"first")
    v,p2,k2=batch(tmp_path,db,v,truth,materials[3:],"second")
    first=bundle(p1,k1,sheet(p1));second=bundle(p2,k2,sheet(p2))
    partial=report(db,v,p,truth,[first])
    assert partial["reviewed_attempts"]==3
    assert partial["summary"]["enrolled"]==2
    assert partial["summary"]["complete_paired_episodes"]==1
    assert not partial["decision_thresholds_met"]
    assert not partial["canonical_scorer_invoked"]
    full=report(db,v,p,truth,[first,second])
    assert full["reviewed_attempts"]==6
    assert full["summary"]["complete_paired_episodes"]==2
    assert full["canonical_scorer_invoked"]
    assert full["head_checked_at_finish"]
    assert not full["rollout_authorized"]
    assert not full["product_effectiveness_verified"]
    assert decision.verify_report(full,full["decision_sha256"])["self_consistency_verified"]
    for row in v["plan"]["episodes"]:
        for key in ("episode_id","team_id","model_id","source_state_id","truth_sha256"):
            assert row[key] not in json.dumps(full).split('"')
    for key in ("incomplete_worklist","review_text","canonical_report","review_id","batch_inventory"):
        assert key not in full
    assert workspace.status(db)==v
    changed=deepcopy(full);changed["summary"]["teams"]=99
    with pytest.raises(ValueError,match="digest mismatch"):
        decision.verify_report(changed,full["decision_sha256"])


def test_wrong_protocol_scope_and_pins_refused(tmp_path):
    db,v,truth=open_study(tmp_path)
    p=freeze(db,v)
    with pytest.raises(ValueError,match="protocol"):
        report(db,v,p,truth,[],expected_protocol_sha256="f"*64)
    changed=deepcopy(p);changed["thresholds"]["min_enrolled"]=1
    with pytest.raises(ValueError,match="protocol"):report(db,v,changed,truth,[])
    changed=deepcopy(p);changed["initial_head_sha256"]="f"*64
    changed["protocol_sha256"]=cohort.digest({k:x for k,x in changed.items() if k!="protocol_sha256"})
    with pytest.raises(ValueError,match="protocol"):report(db,v,changed,truth,[])
    with pytest.raises(ValueError,match="explicit private"):report(db,v,p,truth,[],confirmed=False)


def test_head_change_during_reconciliation_fails_closed(tmp_path,monkeypatch):
    db,v,truth=open_study(tmp_path)
    p=freeze(db,v)
    real=workspace.reconcile
    def race(*args,**kw):
        result=real(*args,**kw)
        row=v["schedule"]["episodes"][0]
        update(db,v,[start(v["plan"],row["episode_id"],row["arm_order"][0],1)])
        return result
    monkeypatch.setattr(workspace,"reconcile",race)
    with pytest.raises(workspace.Conflict):report(db,v,p,truth,[])


def test_cli_freeze_and_verify_are_exclusive(tmp_path):
    db,v,truth=open_study(tmp_path)
    policy=tmp_path/"thresholds.json";policy.write_text(json.dumps(thresholds()))
    out=tmp_path/"protocol.json"
    args=["freeze","--db",str(db),"--thresholds",str(policy),"--head-sha256",v["head_sha256"],
          "--out",str(out),"--confirm"]
    assert decision.main(args)==0
    p=cohort._load(out)
    assert p==freeze(db,v)
    with pytest.raises(SystemExit) as exc:decision.main(args)
    assert exc.value.code==2
    value=report(db,v,p,truth,[])
    packet=tmp_path/"report.json";packet.write_text(json.dumps(value))
    verify=tmp_path/"verified.json"
    assert decision.main(["verify","--report",str(packet),"--decision-sha256",value["decision_sha256"],
                          "--out",str(verify),"--confirm"])==0
    assert cohort._load(verify)["self_consistency_verified"]


def test_offline_html_report_uses_fixed_fields_and_exact_digest(tmp_path):
    from evals import pilot_decision_html as html
    db,v,truth=open_study(tmp_path)
    p=freeze(db,v);r=report(db,v,p,truth,[])
    out=html.render(r,r["decision_sha256"],confirmed=True)
    assert "<table>" in out and "Full-denominator sensitivity" in out
    assert "Not granted" in out and r["decision_sha256"] in out
    assert "<script" not in out and "http://" not in out and "https://" not in out
    assert "Content-Security-Policy" in out
    with pytest.raises(ValueError):html.render(r,r["decision_sha256"],confirmed=False)
    with pytest.raises(ValueError):html.render(r,"0"*64,confirmed=True)
    changed=deepcopy(r)
    changed["summary"]["gates"][0]["gate"]="<script>alert(1)</script>"
    changed["decision_sha256"]=cohort.digest({k:x for k,x in changed.items() if k!="decision_sha256"})
    with pytest.raises(ValueError,match="unexpected gate"):html.render(changed,changed["decision_sha256"],confirmed=True)
    packet=tmp_path/"report.json";packet.write_text(json.dumps(r))
    file=tmp_path/"report.html"
    args=["--report",str(packet),"--decision-sha256",r["decision_sha256"],"--out",str(file),"--confirm"]
    assert html.main(args)==0 and file.read_text()==out
    with pytest.raises(SystemExit):html.main(args)


def test_outward_rounding_does_not_overstate_precision():
    from evals.pilot_decision_html import _percent
    assert _percent({"lower":{"numerator":1,"denominator":3},"upper":{"numerator":1,"denominator":3}})=="33.33% to 33.34%"
    assert _percent({"lower":{"numerator":-1,"denominator":3},"upper":{"numerator":-1,"denominator":3}})=="-33.34% to -33.33%"
