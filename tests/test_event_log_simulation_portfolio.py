"""R14 simulation portfolio: one calibration, bounded alternatives, withheld biased deltas."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_portfolio as portfolio
from server import event_log_workspace as workspace
from test_event_log_simulation import _log

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def proposal():
    return {
        "format":portfolio.FORMAT,
        "baseline":{"cases":400,"seed":42},
        "alternatives":[
            {"label":"Two reviewers","activities":{"Check":{"resources":2}}},
            {"label":"Four reviewers","activities":{"Check":{"resources":4}}},
        ],
    }


def test_two_staffing_options_use_exactly_one_calibration_and_shared_seed(monkeypatch):
    log=_log()
    original=sim.calibrate
    call=[]
    def counted(*args,**kwargs):
        call.append((args,kwargs))
        return original(*args,**kwargs)
    monkeypatch.setattr(sim,"calibrate",counted)
    result=portfolio.compare(log,proposal())
    assert len(call)==1
    assert result["format"]==portfolio.FORMAT
    assert result["single_calibration_used"] and result["identical_case_count_and_seed"]
    assert result["baseline"]["cases_simulated"]==400
    assert [x["label"] for x in result["alternatives"]]==[
        "Two reviewers","Four reviewers"
    ]
    for item in result["alternatives"]:
        assert item["result"]["cases_simulated"]==400
        assert item["result"]["cases_measured"]<=360
        assert len(item["result"]["longest_wait_activities"])<=5
        assert item["completion_population_basis"]=="all_post_warmup_cases"
        assert item["shared_random_streams_not_identical_observed_cases"]
    assert result["observed_or_measured_improvement"] is False
    assert result["causal_attribution_or_production_forecast"] is False
    assert result["execution_authorized"] is False
    assert result["auto_selected_recommended_alternative"] is False
    assert result==portfolio.compare(log,proposal())


def test_same_alternative_under_another_label_returns_identical_simulated_metrics():
    log=_log();body=proposal()
    body["alternatives"][1]["activities"]["Check"]["resources"]=2
    result=portfolio.compare(log,body)
    assert result["alternatives"][0]["result"]==result["alternatives"][1]["result"]
    assert result["alternatives"][0]["cycle_time_change_seconds"]==result["alternatives"][1]["cycle_time_change_seconds"]
    assert len(result["calibration_and_shared_inputs_sha256"])==64


def test_calibration_digest_differentiates_distinct_sources_with_same_numeric_model(monkeypatch):
    log=_log()
    first=portfolio.compare(log,proposal())
    original=sim.calibrate
    def different_source(*args,**kwargs):
        data=original(*args,**kwargs)
        data["log"]={"test_only_alternate_log_identity":"another"}
        return data
    monkeypatch.setattr(sim,"calibrate",different_source)
    second=portfolio.compare(log,proposal())
    assert first["baseline"]==second["baseline"]
    assert first["alternatives"]==second["alternatives"]
    assert first["calibration_and_shared_inputs_sha256"]!=second["calibration_and_shared_inputs_sha256"]


def test_simulator_withholds_comparison_for_incomplete_or_unstable_population():
    baseline={"cases_simulated":100,"cases_measured":90,"unstable":False,
              "cycle_time_seconds":{"p50":20,"p90":40,"mean":30}}
    faster={**baseline,"cases_measured":80,"cycle_time_seconds":{"p50":4,"p90":8,"mean":5}}
    report=portfolio._difference(baseline,faster)
    assert not report["descriptive_cycle_time_comparable"]
    assert "incomplete_or_different_completion_population" in report["reasons_deltas_withheld"]
    assert report["cycle_time_change_seconds"]=={}
    overloaded={**baseline,"unstable":True}
    assert "overloaded_resource_pools" in portfolio._difference(baseline,overloaded)["reasons_deltas_withheld"]
    abandoning={**baseline,"cases_abandoned_after_warmup":2}
    assert "simulated_abandonment_changes_completion_population" in portfolio._difference(baseline,abandoning)["reasons_deltas_withheld"]
    batched={**baseline,"cases_waiting_in_unfilled_batches":7}
    assert "unfinished_batched_cases" in portfolio._difference(baseline,batched)["reasons_deltas_withheld"]
    stable=portfolio._difference(baseline,{**baseline,"cycle_time_seconds":{"p50":10,"p90":35,"mean":15}})
    assert stable["descriptive_cycle_time_comparable"]
    assert stable["cycle_time_change_seconds"]=={"p50":-10,"p90":-5,"mean":-15}


@pytest.mark.parametrize("mutate", [
    lambda x:x.update(unknown=True),
    lambda x:x.update(format="wrong"),
    lambda x:x.update(alternatives=[]),
    lambda x:x.update(alternatives=x["alternatives"]*3),
    lambda x:x["baseline"].update(cases=True),
    lambda x:x["baseline"].update(cases=100000),
    lambda x:x["baseline"].update(seed=-1),
    lambda x:x["baseline"].update(seed=False),
    lambda x:x["baseline"].update(unknown=True),
    lambda x:x["alternatives"][0].update(seed=999),
    lambda x:x["alternatives"][0].update(cases=999),
    lambda x:x["alternatives"][0].update(label=""),
    lambda x:x["alternatives"][0].update(label="name\nwith newline"),
    lambda x:x["alternatives"][0].update(label=" Four reviewers"),
    lambda x:x["alternatives"][0].update(label="Four reviewers"),
    lambda x:x["alternatives"][0].update(secret="private-plaintext"),
    lambda x:x["alternatives"][0].update(activities={"Check":{"resources":-1}}),
    lambda x:x["alternatives"][0].update(paths=[]),
])
def test_unsupported_or_unbounded_portfolio_is_refused(mutate):
    log=_log()
    body=proposal();mutate(body)
    with pytest.raises(workspace.EventLogWorkspaceError):
        portfolio.compare(log,body)


def test_same_calibration_and_seed_but_changed_arrivals_are_not_claimed_same_observed_cases():
    log=_log();body=proposal()
    body["baseline"]["arrivals_per_hour"]=0.5
    body["alternatives"][0]["arrivals_per_hour"]=0.8
    result=portfolio.compare(log,body)
    assert result["single_calibration_used"]
    assert result["shared_inputs"]["arrivals_per_hour"]==0.5
    assert result["alternatives"][0]["changes_reviewed"]["arrivals_per_hour"]==0.8
    assert result["alternatives"][0]["shared_random_streams_not_identical_observed_cases"]


def test_real_http_rejects_non_dashboard_tokens_and_large_or_bad_request(tmp_path):
    code=r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
log=workspace.import_log(BytesIO(
    b"case,activity,time\na,Receive,2026-01-01T00:00:00Z\na,Finish,2026-01-01T01:00:00Z\n"
    b"b,Receive,2026-01-02T00:00:00Z\nb,Finish,2026-01-02T02:00:00Z\n"
    b"c,Receive,2026-01-03T00:00:00Z\nc,Finish,2026-01-03T03:00:00Z\n"),
    name="portfolio",source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
path=f"/v1/event-logs/{log}/simulation/portfolio"
body={"format":"openworkgraph.simulation-portfolio.v1","baseline":{"cases":100,"seed":3},
      "alternatives":[{"label":"A","activities":{"Finish":{"resources":1}}},
                      {"label":"B","activities":{"Finish":{"resources":2}}}]}
with TestClient(app) as c:
    assert c.post(path,json=body).status_code==401
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert c.post(path,headers=bearer,json=body).status_code in (401,403)
    auth={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    valid=c.post(path,headers=auth,json=body)
    assert valid.status_code==200,valid.text
    report=valid.json()
    assert report["format"]==body["format"]
    assert report["single_calibration_used"] and len(report["alternatives"])==2
    assert report["execution_authorized"] is False
    assert valid.headers["cache-control"]=="no-store"
    assert c.post(path,headers=auth,json={**body,"alternatives":[body["alternatives"][0]]}).status_code==422
    assert c.post(path,headers={**auth,"Content-Type":"application/json"},
                  content=b'{"format":"one","format":"two"}').status_code==422
    assert c.post(path,headers={**auth,"Content-Type":"application/json"},
                  content=b'{"format":NaN}').status_code==422
    assert c.post(path,headers={**auth,"Content-Type":"application/json"},
                  content=b"{}"+b" "*70000).status_code==413
    assert c.get(path,headers=auth).status_code==405
'''
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    done=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,
                        capture_output=True,text=True,timeout=120)
    assert done.returncode==0,f"stdout={done.stdout}\nstderr={done.stderr}"
