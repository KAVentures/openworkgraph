"""Version-specific rollout evidence: exact finite-population arithmetic and real store."""
from copy import deepcopy
from itertools import product
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_monitoring as m
from server import automation_rollout_review as rollout
from test_automation_run_evidence import run, report, imported


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)


A="a"*64
B="b"*64


def fixture():
    left=imported([run("same","succeeded"),run("failure"),run("pending","running")],A)
    right=imported([run("same","succeeded"),run("another","succeeded"),
                    run("handoff","handed_to_human"),run("pending","running")],B)
    m.import_report(report([run("unhashed","succeeded")]),right)
    return {"baseline":{"deployment_id":left,"spec_sha256":A},
            "candidate":{"deployment_id":right,"spec_sha256":B},
            "started_from":"2026-01-01T00:00:00Z","started_before":"2026-02-01T00:00:00Z",
            "minimum_attributed_runs":3,"minimum_candidate_success_share":.5,
            "maximum_success_share_decrease":.1,"maximum_unknown_attribution_share":.2}


def test_real_store_partitions_bounds_and_unknown_sensitivity():
    spec=fixture()
    r=rollout.review(spec)
    b,c=r["cohorts"]["baseline"],r["cohorts"]["candidate"]
    assert r["matching_runs"]==8
    assert b["attributed_eventual_success_share_bounds"]=={"lower":1/3,"upper":2/3}
    assert c["attributed_eventual_success_share_bounds"]=={"lower":.5,"upper":.75}
    assert c["possible_version_success_share_with_unhashed_bounds"]=={"lower":.5,"upper":.8}
    assert c["unhashed_runs"]==1 and c["unknown_attribution_share"]==.2
    assert r["candidate_minus_baseline_attributed_success_share_bounds"]["lower"]==pytest.approx(-1/6)
    assert r["candidate_minus_baseline_attributed_success_share_bounds"]["upper"]==pytest.approx(5/12)
    assert r["review_gates"][-1]["status"]=="unresolved"
    assert not r["all_declared_gates_met"]
    assert not r["release_approved"] and not r["execution_authorized"]


def test_paging_and_evidence_filters_share_full_population_pin():
    spec=fixture()
    first=rollout.review({**spec,"limit":1})
    pin=first["population_sha256"]
    next_page=rollout.review({**spec,"offset":1,"limit":1,"expected_population_sha256":pin})
    assert first["evidence_page"]["rows"][0]["run_id"]!=next_page["evidence_page"]["rows"][0]["run_id"]
    unknown=rollout.review({**spec,"evidence_attribution":"unknown","expected_population_sha256":pin})
    assert unknown["population_sha256"]==pin
    assert unknown["evidence_page"]["rows"][0]["run_id"]=="unhashed"
    from server import automation_run_evidence as evidence
    ref=unknown["evidence_page"]["rows"][0]["reference"]
    assert evidence.detail(ref["deployment_id"],ref["run_id"],ref["run_sha256"])["reference"]==ref
    dep=spec["candidate"]["deployment_id"]
    changed=run("same","succeeded");changed["steps"][0]["activity"]="Revised evidence only"
    m.import_report(report([changed],B),dep)
    with pytest.raises(m.MonitoringError,match="changed"):
        rollout.review({**spec,"expected_population_sha256":pin})


def test_exact_other_version_and_no_attributed_population():
    spec=fixture()
    spec["candidate"]["spec_sha256"]="c"*64
    r=rollout.review({**spec,"evidence_attribution":"other"})
    c=r["cohorts"]["candidate"]
    assert c["attributed_runs"]==0 and c["other_version_runs"]==4
    assert c["attributed_eventual_success_share_bounds"] is None
    assert c["possible_version_success_share_with_unhashed_bounds"] is None
    assert r["review_gates"][-1]["status"]=="insufficient"
    assert r["evidence_page"]["matching_runs"]==4


def test_window_excludes_upper_boundary_and_unrelated_append_preserves_pin():
    spec=fixture();pin=rollout.review(spec)["population_sha256"]
    dep=spec["candidate"]["deployment_id"]
    outside=run("outside","succeeded")
    outside["started_at"]=outside["finished_at"]="2026-02-01T00:00:00Z"
    for step in outside["steps"]:step["at"]=outside["started_at"]
    m.import_report(report([outside],B),dep)
    assert rollout.review({**spec,"expected_population_sha256":pin})["matching_runs"]==8
    m.delete_deployment(dep)
    with pytest.raises(m.MonitoringError):
        rollout.review(spec)


def test_unknown_attribution_bounds_match_exhaustive_subset_and_running_oracle():
    def counts(states):
        return {s:states.count(s) for s in m.RUN_STATUSES}
    exact=["succeeded","failed","running"]
    unknown=["succeeded","failed","running"]
    observed=[]
    for a,b in product([0,1],repeat=2):
        e=[1,0,a];u=[1,0,b]
        for included in product([False,True],repeat=3):
            items=e+[v for v,keep in zip(u,included) if keep]
            observed.append(Fraction(sum(items),len(items)))
    assert rollout._attribution_sensitivity(counts(exact),counts(unknown))==(min(observed),max(observed))


@pytest.mark.parametrize("patch",[
    {"offset":1},{"limit":101},{"minimum_attributed_runs":True},
    {"minimum_candidate_success_share":float("nan")},{"maximum_success_share_decrease":-1},
    {"maximum_unknown_attribution_share":1.1},{"evidence_attribution":"guessed"},
    {"started_before":"2028-01-01T00:00:00Z"},{"started_from":"bad"},{"unexpected":True},
])
def test_strict_review_settings(patch):
    spec=fixture()
    with pytest.raises(m.MonitoringError):
        rollout.review({**spec,**patch})


def test_resource_limits_and_distinct_version_requirements(monkeypatch):
    spec=fixture()
    for bad in ({**spec,"baseline":spec["candidate"]},
                {**spec,"candidate":{**spec["candidate"],"spec_sha256":A}}):
        with pytest.raises(m.MonitoringError,match="distinct"):
            rollout.review(bad)
    monkeypatch.setattr(rollout,"MAX_RUNS",7)
    with pytest.raises(m.MonitoringError,match="too many rollout runs"):
        rollout.review(spec)
    monkeypatch.setattr(rollout,"MAX_RUNS",50_000)
    monkeypatch.setattr(rollout,"MAX_STEPS",1)
    with pytest.raises(m.MonitoringError,match="too many rollout steps"):
        rollout.review(spec)


def test_http_owner_only_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_rollout_review import fixture
with TestClient(app) as client:
    spec=fixture()
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    path="/v1/automation-monitoring/rollout-review"
    assert client.post(path,json=spec,headers=bearer).status_code in (401,403)
    response=client.post(path,json=spec,headers=h)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.json()["matching_runs"]==8
    assert client.post(path,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert client.post(path,content=b"{}"+b" "*17000,headers={**h,"Content-Type":"application/json"}).status_code==413
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
                        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
