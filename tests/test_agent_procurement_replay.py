"""Replay the real generated offline validator, without calling any candidate."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_replay import prepare,score,_output
from shared.process_agent_project import build_runtime_spec
from test_process_shadow_review import model


@pytest.fixture
def inputs():
    packet={"format":"openworkgraph.versioned-process-export.v1","workflow_id":"reviewed_process",
            "revision":1,"content_sha256":"a"*64,"execution_authorization":False,"process_model":model()}
    spec=build_runtime_spec(packet)
    reference={k:spec[k] for k in ("workflow_id","revision","content_sha256")}
    suite={"format":"openworkgraph.reviewed-shadow-cases.v1","process_reference":reference,
           "spec_sha256":hashlib.sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest(),
           "reviewed_by_customer":True,"case_inputs_authorized":True,
           "cases":[{"id":"case_1","category":"normal","input":{"text":"PRIVATE_CASE_INPUT"},
                     "expected":{"status":"proposal","accepted_routes":[["a","b"]],"review_confirmed":True}},
                    {"id":"case_2","category":"missing_facts","input":{},
                     "expected":{"status":"needs_clarification","accepted_routes":[],"review_confirmed":True}}]}
    design={"format":"openworkgraph.procurement-trial-design.v1","baseline_candidate_id":"current",
            "currency":"USD","repeats":1,"schedule_seed":1,"confirmed":True,
            "gates":{"minimum_match_basis_points":0,"require_all_categories":False},
            "candidates":[{"candidate_id":cid,"provider":"provider","model":cid,"runtime":"external",
                           "configuration_sha256":digest(cid)} for cid in ("current","other")]}
    return packet,suite,design


@pytest.fixture
def frozen(inputs):
    packet,suite,design=inputs
    plan,challenge=prepare(packet,suite,design,confirm_export_inputs=True)
    return packet,suite,plan,challenge


def raw(plan,packet):
    def attempt(cid,case,route,status="completed"):
        slot=next(s for s in plan["slots"] if s["candidate_id"]==cid and s["case_id"]==case)
        row={"case_id":case,"slot_id":slot["slot_id"],"status":status,"cost_microunits":100,"elapsed_ms":500}
        if status=="completed":
            row["plan"]={"revision":packet["revision"],"content_sha256":packet["content_sha256"],
                         "status":"proposal" if route else "needs_clarification",
                         "activity_ids":route,"missing_business_inputs":[] if route else ["PRIVATE_MODEL_QUESTION"]}
        return row
    return {"format":"openworkgraph.procurement-replay-raw-captures.v1","plan_sha256":plan["plan_sha256"],
            "captures":[
                {"candidate_id":"current","repeat":1,"configuration_sha256":digest("current"),
                 "attempts":[attempt("current","case_1",["a","b"]),attempt("current","case_2",["a","b"])]},
                {"candidate_id":"other","repeat":1,"configuration_sha256":digest("other"),
                 "attempts":[attempt("other","case_1",[],status="timeout"),attempt("other","case_2",[])]}]}


def test_blind_challenge_contains_inputs_but_no_case_answer_or_labels(inputs):
    packet,suite,design=inputs
    with pytest.raises(ValueError,match="confirmation"):prepare(packet,suite,design)
    plan,challenge=prepare(packet,suite,design,confirm_export_inputs=True)
    assert plan["scheduled_attempts"]==4
    assert challenge["plan_sha256"]==plan["plan_sha256"]
    assert challenge["cases"][0]["input"]["text"]=="PRIVATE_CASE_INPUT"
    assert set(challenge["cases"][0])=={"case_id","input","input_sha256"}
    assert "reference_report" not in challenge and "basis" not in challenge
    assert "accepted_routes" not in json.dumps(challenge)
    assert challenge["expected_case_answers_included"] is False
    assert challenge["business_tools_authorized"] is False
    assert "PRIVATE_CASE_INPUT" not in json.dumps(plan)


def test_actual_offline_scores_recomputed_and_failed_attempt_cost_included(frozen):
    packet,suite,plan,_=frozen
    ledger,result=score(packet,suite,plan,raw(plan,packet))
    current,other=result["candidates"]
    assert current["match_bounds"]["lower"]==1 and current["critical_bounds"]["lower"]==1
    assert other["match_bounds"]["lower"]==1 and other["critical_bounds"]["lower"]==0
    assert other["status_counts"]["timeout"]==1 and other["cost"]["total_microunits"]==200
    assert ledger["captures"][0]["report"]["reason_counts"]=={"expected_clarification":1,"matches_reviewed_expectation":1}
    assert ledger["captures"][1]["report"]["reason_counts"]=={"invalid_or_stale_plan":1,"matches_reviewed_expectation":1}
    assert result["offline_scores_recomputed_from_returned_plans"] is True
    assert result["external_candidate_execution_observed"] is False
    assert not result["execution_authorized"]
    assert "PRIVATE_" not in json.dumps([ledger,result])


@pytest.mark.parametrize("mutation",["wrong_slot","changed_suite","claimed_report","duplicate_capture","unconfirmed_suite",
                                    "noncompleted_plan","missing_plan","wrong_plan"])
def test_replay_refuses_unbound_or_cherry_picked_inputs(frozen,mutation):
    packet,suite,plan,_=frozen
    value=raw(plan,packet)
    if mutation=="wrong_slot":value["captures"][0]["attempts"][0]["slot_id"]="f"*64
    elif mutation=="changed_suite":suite["cases"][0]["input"]["text"]="Changed"
    elif mutation=="claimed_report":value["captures"][0]["report"]={}
    elif mutation=="duplicate_capture":value["captures"].append(deepcopy(value["captures"][0]))
    elif mutation=="unconfirmed_suite":suite["reviewed_by_customer"]=False
    elif mutation=="noncompleted_plan":value["captures"][1]["attempts"][0]["plan"]=None
    elif mutation=="missing_plan":del value["captures"][0]["attempts"][0]["plan"]
    elif mutation=="wrong_plan":value["plan_sha256"]="f"*64
    with pytest.raises(ValueError):score(packet,suite,plan,value)


def test_invalid_returned_plan_is_scored_critical_not_excluded(frozen):
    packet,suite,plan,_=frozen
    value=raw(plan,packet);value["captures"][0]["attempts"][0]["plan"]={"raw":"PRIVATE_OUTPUT"}
    _,result=score(packet,suite,plan,value)
    assert result["candidates"][0]["critical_bounds"]["lower"]==2
    assert result["candidates"][0]["match_bounds"]["lower"]==0
    assert "PRIVATE_OUTPUT" not in json.dumps(result)


def test_empty_ledger_keeps_every_planned_slot(frozen):
    packet,suite,plan,_=frozen
    value=raw(plan,packet);value["captures"]=[]
    ledger,result=score(packet,suite,plan,value)
    assert ledger["captures"]==[]
    assert result["scheduled_attempts"]==4
    assert all(c["match_bounds"]=={"lower":0,"upper":2,"unknown":2,"total":2} for c in result["candidates"])


def test_private_output_and_cli_roundtrip(inputs,tmp_path):
    packet,suite,design=inputs
    p=tmp_path/"process.json";s=tmp_path/"suite.json";d=tmp_path/"design.json"
    for path,value in ((p,packet),(s,suite),(d,design)):path.write_text(json.dumps(value),encoding="utf-8")
    root=Path(__file__).resolve().parents[1]
    base=[sys.executable,"-m","shared.agent_procurement_replay"]
    out=tmp_path/"challenge";result=tmp_path/"scored"
    args=["prepare","--process-export",str(p),"--suite",str(s),"--design",str(d),"--out-dir",str(out)]
    def call(args):return subprocess.run(base+args,cwd=root,capture_output=True,text=True,timeout=150)
    assert call(args).returncode==2 and not out.exists()
    prepared=call(args+["--confirm-export-inputs"])
    assert prepared.returncode==0,prepared.stderr
    plan=json.loads((out/"plan.json").read_text())
    assert call(args+["--confirm-export-inputs"]).returncode==2
    if sys.platform!="win32":assert (out/"challenge.private.json").stat().st_mode & 0o777==0o600
    captures=tmp_path/"raw.json";captures.write_text(json.dumps(raw(plan,packet)),encoding="utf-8")
    scored=call(["score","--process-export",str(p),"--suite",str(s),"--plan",str(out/"plan.json"),
                 "--raw-captures",str(captures),"--out-dir",str(result)])
    assert scored.returncode==0,scored.stderr
    assert json.loads((result/"review.json").read_text())["offline_scores_recomputed_from_returned_plans"]
