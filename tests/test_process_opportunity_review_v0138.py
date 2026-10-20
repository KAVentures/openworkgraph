"""Review-signal aggregates and explicit-assumption ROI regression checks."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from server.process_mining_csv import mine_csv_events

ROOT=Path(__file__).resolve().parents[1]


def test_interleaved_case_transitions_have_observed_gap_stats_not_verified_wait():
    csv=(
        "case_id,activity,timestamp\n"
        "a,Open,2026-10-20T10:00:00Z\n"
        "b,Open,2026-10-20T11:00:00Z\n"
        "a,Review,2026-10-20T10:05:00Z\n"
        "a,Review,2026-10-20T10:06:00Z\n"
        "b,Review,2026-10-20T11:05:00Z\n"
        "a,Close,2026-10-20T10:20:00Z\n"
        "b,Close,2026-10-20T11:45:00Z\n"
    ).encode()
    result=mine_csv_events(csv)
    edges={(item["from"],item["to"]):item for item in result["transitions"]}
    assert edges[("Open","Review")]["median_elapsed_gap_seconds"]==300
    assert edges[("Review","Close")]["median_elapsed_gap_seconds"]==1620
    assert edges[("Review","Close")]["max_elapsed_gap_seconds"]==2400
    assert edges[("Review","Close")]["case_count"]==2
    assert edges[("Review","Close")]["gap_is_verified_waiting"] is False
    assert edges[("Review","Review")]["transition_count"]==1
    assert result["review_signals_are_confirmed_bottlenecks"] is False
    assert result["estimated_labor_savings_from_timestamps"] is None
    signals=result["review_signals"]
    assert signals[0]["type"]=="long_observed_transition_gap"
    assert signals[0]["from"]=="Review" and signals[0]["to"]=="Close"
    repetition=next(x for x in signals if x["type"]=="repeated_activity")
    assert repetition["activity"]=="Review"
    assert repetition["additional_observed_events"]==1
    assert repetition["not_verified_rework_or_automation_opportunity"] is True


@pytest.mark.skipif(not shutil.which("node"),reason="Node.js unavailable")
def test_calculator_refuses_empty_or_unverified_inputs_and_does_not_infer_roi():
    code=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/opportunity_review.js','utf8'),scope);
const calculate=scope.window.__owgOpportunityModel.calculate;
const values={
  currency:'SEK',monthly_cases:100,minutes_saved_per_case:6,
  eligible_fraction_percent:50,loaded_hourly_cost:900,
  recurring_monthly_cost:1000,one_time_implementation_cost:9000
};
const result=calculate(values);
assert.equal(result.projection.eligible_cases_per_month,50);
assert.equal(result.projection.assumed_hours_saved_per_month,5);
assert.equal(result.projection.gross_labor_value_per_month,4500);
assert.equal(result.projection.net_monthly_value_before_other_costs,3500);
assert.equal(result.projection.simple_payback_months,2.57);
assert.equal(result.guaranteed_savings,false);
assert(result.basis.includes('user_supplied'));
assert.throws(()=>calculate({...values,monthly_cases:''}),/Missing assumption/);
assert.throws(()=>calculate({...values,eligible_fraction_percent:101}),/Invalid/);
assert.throws(()=>calculate({...values,currency:'INVALID'}),/currency/);
assert.throws(()=>calculate({...values,loaded_hourly_cost:Infinity}),/Invalid/);
assert.equal(calculate({...values,recurring_monthly_cost:999999}).projection.simple_payback_months,null);
"""
    r=subprocess.run(["node","-e",code],cwd=ROOT,text=True,capture_output=True,timeout=20)
    assert r.returncode==0,r.stderr


def test_review_is_additive_to_existing_csv_minimal_case_ui():
    dashboard=(ROOT/"dashboard/index.html").read_text(encoding="utf-8")
    app=(ROOT/"dashboard/opportunity_review.js").read_text(encoding="utf-8")
    miner=(ROOT/"dashboard/process_mining.js").read_text(encoding="utf-8")
    routes=(ROOT/"server/workflow_evidence_routes.py").read_text(encoding="utf-8")
    ci=(ROOT/".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert '<script src="/opportunity-review.js"></script>' in dashboard
    assert 'window.owgOpportunityReview?.reset?.()' in miner
    assert 'window.owgOpportunityReview?.render?.(report)' in miner
    assert '@app.get("/opportunity-review.js")' in routes
    assert "FileResponse" in routes
    assert "node --check dashboard/opportunity_review.js" in ci
    assert "review_signals" in app
    assert "eligible_fraction_percent" in app
    assert "guaranteed_savings:false" in app
    for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "indexedDB",
                      "innerHTML", "sendBeacon", "eval(", "document.cookie"):
        assert forbidden not in app,forbidden
