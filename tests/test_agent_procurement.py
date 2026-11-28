"""Vendor-neutral procurement comparison against generated OWG reports."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from shared.process_agent_project import build_runtime_spec
from shared.process_shadow_review import ShadowEvaluation
from evals.agent_procurement import compare, digest, _load
from test_process_shadow_review import generated_report, model


@pytest.fixture
def study(tmp_path):
    packet={"format":"openworkgraph.versioned-process-export.v1","workflow_id":"test",
            "revision":1,"content_sha256":"a"*64,"execution_authorization":False,"process_model":model()}
    report=generated_report(packet,tmp_path/"project")
    def candidate(key, reasons, costs, times):
        r=deepcopy(report)
        r["results_sha256"]=digest(key)
        r["cases"]=[{"case_id":f"case_{i+1}","case_sha256":digest(i),
                      "category":cat,"reason":reason,
                      "matches_reviewed_expectation":reason=="matches_reviewed_expectation",
                      "review_required":reason!="matches_reviewed_expectation"}
                     for i,(cat,reason) in enumerate(zip(("normal","exception","missing_facts"),reasons))]
        r["case_count"]=3
        r["matching_case_count"]=sum(c["matches_reviewed_expectation"] for c in r["cases"])
        r["review_required"]=any(c["review_required"] for c in r["cases"])
        r["reason_counts"]={x:reasons.count(x) for x in set(reasons)}
        ShadowEvaluation.model_validate(r)
        return {"candidate_id":key,"provider":"provider","model":"model-"+key,"runtime":"cli",
                "report":r,"measurements":[{"case_id":f"case_{i+1}","cost_microunits":c,"elapsed_ms":t}
                  for i,(c,t) in enumerate(zip(costs,times))]}
    ok="matches_reviewed_expectation"
    baseline=candidate("baseline",[ok,"unexpected_route",ok],[10,10,10],[100,200,300])
    cheap=candidate("cheap",[ok,ok,ok],[5,5,5],[90,100,110])
    fast=candidate("fast",[ok,ok,ok],[20,20,20],[20,30,40])
    return packet,{"format":"openworkgraph.agent-procurement.v1","baseline_candidate_id":"baseline",
        "currency":"USD","gates":{"minimum_match_basis_points":6000,"maximum_critical_cases":0},
        "candidates":[baseline,cheap,fast]}


def test_exact_comparison_and_pareto_tradeoffs(study):
    packet,request=study
    result=compare(packet,request)
    assert result["pareto"]["candidate_ids"]==["cheap","fast"]
    assert result["comparisons"][0]["counts"]=={"improved":1,"regressed":0,"unchanged_match":2,"unchanged_mismatch":0}
    assert result["candidates"][1]["cost"]["total_microunits"]==15
    assert result["candidates"][1]["latency"]["p95_ms"]==110
    assert result["comparisons"][0]["cases"][0]["cost_delta_microunits"]==-5
    assert not result["execution_authorized"] and not result["automatic_selection_performed"]
    assert not result["telemetry_independently_verified"]
    assert "PRIVATE_" not in json.dumps(result)


def test_missing_measurement_not_zero_and_gates_fail_or_unknown(study):
    packet,r=study
    r["gates"]["maximum_total_cost_microunits"]=25
    r["gates"]["maximum_p95_elapsed_ms"]=500
    r["candidates"][1]["measurements"][0]["cost_microunits"]=None
    r["candidates"][1]["measurements"][0]["elapsed_ms"]=None
    result=compare(packet,r)
    cheap=result["candidates"][1]
    assert cheap["cost"]["total_microunits"] is None
    assert cheap["cost"]["known_cost_lower_bound_microunits"]==10
    assert cheap["latency"]["p95_ms"] is None and cheap["gate_status"]=="unknown"
    assert result["pareto"]["candidate_ids"]==[]
    assert result["comparisons"][0]["cases"][0]["cost_delta_microunits"] is None
    r["gates"]["maximum_total_cost_microunits"]=9
    assert compare(packet,r)["candidates"][1]["gates"]["total_cost"]=="fail"


def test_equal_score_can_hide_critical_and_ordinary_regressions(study):
    packet,r=study
    c=r["candidates"][1]["report"]
    row=c["cases"][0]
    row.update(reason="unresolved_review_bypassed",matches_reviewed_expectation=False,review_required=True)
    c.update(matching_case_count=2,review_required=True,
             reason_counts={"matches_reviewed_expectation":2,"unresolved_review_bypassed":1})
    r["gates"].update(maximum_critical_cases=1,prohibit_baseline_regressions=True)
    out=compare(packet,r)
    assert out["comparisons"][0]["new_critical_cases"]==1
    assert out["comparisons"][0]["counts"]["regressed"]==1
    assert out["candidates"][1]["gates"]["no_new_critical_cases"]=="fail"
    assert out["candidates"][1]["gates"]["no_baseline_regressions"]=="fail"


@pytest.mark.parametrize("mutation",["suite","spec","category","case_hash","duplicate_report","duplicate_id",
                                    "missing_measurement","duplicate_measurement","boolean_cost","raw_field"])
def test_incompatible_or_ambiguous_input_refused(study,mutation):
    packet,r=study
    c=r["candidates"][1]
    if mutation=="suite":c["report"]["suite_sha256"]="f"*64
    elif mutation=="spec":c["report"]["spec_sha256"]="f"*64
    elif mutation=="category":c["report"]["cases"][0]["category"]="exception"
    elif mutation=="case_hash":c["report"]["cases"][0]["case_sha256"]="f"*64
    elif mutation=="duplicate_report":c["report"]=deepcopy(r["candidates"][0]["report"])
    elif mutation=="duplicate_id":c["candidate_id"]="baseline"
    elif mutation=="missing_measurement":c["measurements"].pop()
    elif mutation=="duplicate_measurement":c["measurements"][1]=deepcopy(c["measurements"][0])
    elif mutation=="boolean_cost":c["measurements"][0]["cost_microunits"]=False
    elif mutation=="raw_field":c["raw_case"]="PRIVATE"
    with pytest.raises(ValueError):compare(packet,r)


def test_shared_result_hash_is_disclosed_without_claiming_independence(study):
    packet,r=study
    r["candidates"][1]["report"]["results_sha256"]=r["candidates"][2]["report"]["results_sha256"]
    # Make distinct report envelopes; identical output hashes can be legitimate.
    r["candidates"][1]["report"]["evaluated_at"]="2026-01-01T00:00:00Z"
    out=compare(packet,r)
    assert out["shared_results_hashes"][0]["candidate_ids"]==["cheap","fast"]


def test_cli_exclusive_output_and_duplicate_keys(study,tmp_path):
    packet,r=study
    p=tmp_path/"process.json";q=tmp_path/"request.json";o=tmp_path/"out.json"
    p.write_text(json.dumps(packet),encoding="utf-8")
    q.write_text(json.dumps(r),encoding="utf-8")
    args=[sys.executable,"-m","evals.agent_procurement","--process-export",str(p),"--request",str(q),"--out",str(o)]
    root=Path(__file__).resolve().parents[1]
    first=subprocess.run(args,cwd=root,capture_output=True,text=True,timeout=30)
    assert first.returncode==0,first.stderr
    original=o.read_bytes()
    again=subprocess.run(args,cwd=root,capture_output=True,text=True,timeout=30)
    assert again.returncode==2 and o.read_bytes()==original
    q.write_text('{"x":1,"x":2}',encoding="utf-8")
    with pytest.raises(ValueError,match="duplicate"): _load(q)
