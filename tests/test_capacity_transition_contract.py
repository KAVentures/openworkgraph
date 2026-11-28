"""End-to-end capacity export contract with the offline JavaScript planner."""
import json
from io import BytesIO
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server import event_log_simulation_capacity as capacity
from server import event_log_workspace as workspace
from server.capacity_transition_routes import router


def test_static_page_is_data_free_and_scripts_are_served():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/capacity-transition")
        assert response.status_code == 200
        assert "connect-src 'none'" in response.headers["content-security-policy"]
        assert "no-store" in response.headers["cache-control"]
        assert "capacity_transition.js" in response.text
        assert client.get("/capacity_transition.js").status_code == 200
        assert client.get("/capacity_transition_ui.js").status_code == 200
        assert client.post("/capacity-transition", json={"report": "never sent"}).status_code == 405


def test_actual_grid_export_is_accepted_by_offline_planner(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the cross-runtime contract check")
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(
        b"case,activity,time,who\na,Receive,2026-01-01T00:00:00Z,r\n"
        b"a,Finish,2026-01-01T01:00:00Z,r\nb,Receive,2026-01-02T00:00:00Z,r\n"
        b"b,Finish,2026-01-02T01:00:00Z,r\nc,Receive,2026-01-03T00:00:00Z,r\n"
        b"c,Finish,2026-01-03T01:00:00Z,r\n"),
        name="capacity contract", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time", "resource_column": "who"})["log_id"]
    report = capacity.run_grid(log, {
        "format": capacity.FORMAT,
        "baseline": {"cases": 50, "seed": 7, "distribution": "fixed"},
        "designs": [{"label": "Two reviewers", "activities": {"Finish": {"resources": 2}}}],
        "arrival_rates_per_hour": [.1, 1.5], "replications": 2,
        "target": {"cycle_time_minutes": 10000, "minimum_completion_share": .01},
    })
    root = Path(__file__).resolve().parents[1]
    code = """
const fs=require('node:fs');
const api=require('./dashboard/capacity_transition.js');
const report=JSON.parse(fs.readFileSync(0,'utf8'));
const grid=api.readReport(report);
const plan=api.solve(report,{confirmScenario:true,sourceSha256:'a'.repeat(64),currency:'SEK',
 targetBasisPoints:100,costs:grid.designs.map((design,i)=>({design,hourlyMinorUnits:100+i*50})),
 periods:[{rate:grid.rates[0],minutes:60}],initialDesign:null,maxSwitches:0,
 switchMinorUnits:0,budgetMinorUnits:null});
process.stdout.write(JSON.stringify(plan));
"""
    result = subprocess.run([node, "-e", code], cwd=root,
                            input=json.dumps(report, allow_nan=False),
                            text=True, capture_output=True, check=True, timeout=30)
    plan = json.loads(result.stdout)
    assert plan["format"] == "openworkgraph.capacity-transition-plan.v1"
    assert plan["status"] == "finite_plan_found"
    assert plan["minimum_cost_tested_witness"]["steps"][0]["design"] == "Baseline"
    assert plan["minimum_cost_tested_witness"]["total_cost"] == {
        "minor_units_numerator": "6000", "denominator": 60}
    assert not plan["queue_carryover_simulated"]
    assert not plan["execution_authorized"]
