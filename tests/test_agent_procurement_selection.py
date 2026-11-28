"""Procurement nomination and held-out review with actual frozen trial inputs."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_trials import freeze
from shared.agent_procurement_selection import nominate,assess,decide,operation
from test_agent_procurement_trials import trial,capture,ledger
from test_agent_procurement import study


@pytest.fixture
def selection(trial):
    packet,req,design,dp=trial
    held=deepcopy(req)
    for c in held["candidates"]:
        c["report"]["suite_sha256"]="b"*64
        for row in c["report"]["cases"]:
            row["case_sha256"]=digest(["holdout",row["case_id"]])
    hd=deepcopy(design)
    hd["reference_report"]=held["candidates"][0]["report"]
    hp=freeze(packet,hd)
    dc=ledger(dp,[capture(req,cid,r) for cid in ("baseline","cheap") for r in (1,2)])
    hc=ledger(hp,[capture(held,cid,r) for cid in ("baseline","cheap") for r in (1,2)])
    request={"candidate_id":"cheap","nominator_alias":"reviewer-a","confirmed":True,
             "policy":{"minimum_discovery_cases":3,"minimum_holdout_cases":3,
                       "minimum_candidate_match_basis_points":10000,
                       "maximum_candidate_cost_ratio_basis_points":5000}}
    n=nominate(packet,dp,dc,hp,request)
    return packet,dp,dc,hp,hc,n,request


def assessment(selection):
    packet,dp,dc,hp,hc,n,_=selection
    return assess(packet,dp,dc,hp,hc,n)


def request_for(a,decision="select_for_substitution_rehearsal"):
    return {"decision":decision,"reviewer_alias":"reviewer-b","confirmed":True,
            "expected_assessment_sha256":a["assessment_sha256"],
            "expected_nomination_sha256":a["nomination_sha256"]}


def test_disjoint_holdout_and_explicit_selection_receipt(selection):
    packet,dp,dc,hp,hc,n,_=selection
    a=assessment(selection)
    assert a["eligible_for_rehearsal_review"]
    assert a["holdout"]["checks"]["cost_ratio"]=="pass"
    assert a["holdout"]["paired_match_count_change_bounds"]=={"lower":2,"upper":2}
    assert a["holdout"]["paired_regression_bounds"]=={"lower":0,"upper":0}
    receipt=decide(packet,dp,dc,hp,hc,n,request_for(a))
    assert receipt["rehearsal_candidate_id"]=="cheap"
    assert not receipt["execution_authorized"] and not receipt["deployment_changed"]
    assert not receipt["reviewer_identity_verified"] and not a["independent_holdout_proven"]
    assert receipt["receipt_sha256"]==digest({k:v for k,v in receipt.items() if k!="receipt_sha256"})


def test_stale_capture_or_nomination_refused(selection):
    packet,dp,dc,hp,hc,n,_=selection
    a=assessment(selection)
    changed=deepcopy(hc);changed["captures"][0]["attempts"][0]["elapsed_ms"]=12345
    with pytest.raises(ValueError,match="evidence changed"):
        decide(packet,dp,dc,hp,changed,n,request_for(a))
    changed=deepcopy(dc);changed["captures"][0]["attempts"][0]["elapsed_ms"]=12345
    with pytest.raises(ValueError,match="nomination or discovery"):
        assess(packet,dp,changed,hp,hc,n)
    changed=deepcopy(n);changed["candidate"]["model"]="different"
    with pytest.raises(ValueError,match="nomination or discovery"):
        assess(packet,dp,dc,hp,hc,changed)


def test_holdout_failure_or_missing_evidence_allows_only_non_selection(selection):
    packet,dp,dc,hp,hc,n,_=selection
    changed=deepcopy(hc);changed["captures"].pop()
    a=assess(packet,dp,dc,hp,changed,n)
    assert not a["eligible_for_rehearsal_review"]
    assert a["holdout"]["checks"]["baseline_and_candidate_outcomes_complete"]=="unknown"
    with pytest.raises(ValueError,match="holdout evidence"):
        decide(packet,dp,dc,hp,changed,n,request_for(a))
    retained=decide(packet,dp,dc,hp,changed,n,request_for(a,"retain_baseline"))
    assert retained["rehearsal_candidate_id"] is None
    assert retained["request"]["decision"]=="retain_baseline"


@pytest.mark.parametrize("mutation",["suite_reused","input_overlap","duplicate_input","configuration","currency","gates","too_few_cases"])
def test_holdout_design_refusals(selection,mutation):
    packet,dp,dc,hp,hc,n,request=selection
    hd=deepcopy(hp["basis"])
    if mutation=="suite_reused":hd["reference_report"]["suite_sha256"]=dp["basis"]["reference_report"]["suite_sha256"]
    elif mutation=="input_overlap":hd["reference_report"]["cases"][0]["case_sha256"]=dp["basis"]["reference_report"]["cases"][0]["case_sha256"]
    elif mutation=="duplicate_input":hd["reference_report"]["cases"][0]["case_sha256"]=hd["reference_report"]["cases"][1]["case_sha256"]
    elif mutation=="configuration":hd["candidates"][1]["configuration_sha256"]="f"*64
    elif mutation=="currency":hd["currency"]="EUR"
    elif mutation=="gates":hd["gates"]["minimum_match_basis_points"]=0
    elif mutation=="too_few_cases":request=deepcopy(request);request["policy"]["minimum_holdout_cases"]=4
    with pytest.raises(ValueError):nominate(packet,dp,dc,freeze(packet,hd),request)


def test_explicit_review_and_distinct_declared_alias(selection):
    packet,dp,dc,hp,hc,n,_=selection
    a=assessment(selection)
    req=request_for(a);req["reviewer_alias"]="REVIEWER-A"
    with pytest.raises(ValueError,match="distinct"):decide(packet,dp,dc,hp,hc,n,req)
    req=request_for(a);req["confirmed"]=False
    with pytest.raises(ValueError,match="confirmation"):decide(packet,dp,dc,hp,hc,n,req)


def test_cli_roundtrip_and_exclusive_receipt(selection,tmp_path):
    packet,dp,dc,hp,hc,n,_=selection
    a=assessment(selection)
    bundle={"discovery_plan":dp,"discovery_captures":dc,"holdout_plan":hp,
            "holdout_captures":hc,"nomination":n,"request":request_for(a)}
    assert operation(packet,"decide",bundle)["rehearsal_candidate_id"]=="cheap"
    p=tmp_path/"process.json";b=tmp_path/"bundle.json";o=tmp_path/"receipt.json"
    p.write_text(json.dumps(packet));b.write_text(json.dumps(bundle))
    args=[sys.executable,"-m","shared.agent_procurement_selection","decide",
          "--process-export",str(p),"--bundle",str(b),"--out",str(o)]
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run(args,cwd=root,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    assert json.loads(o.read_text())["rehearsal_candidate_id"]=="cheap"
    assert subprocess.run(args,cwd=root,capture_output=True,text=True,timeout=30).returncode==2
