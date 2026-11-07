"""Side-by-side implementation alternatives in opportunity review (roadmap #264 Stage 4)."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node(code: str) -> None:
    result = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


_LOAD = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/opportunity_review.js','utf8'),scope);
const model=scope.window.__owgOpportunityModel;
const shared={currency:'SEK',monthly_cases:200,loaded_hourly_cost:600};
const row=(minutes,pct,recurring,once,extra={})=>({minutes_saved_per_case:minutes,eligible_fraction_percent:pct,
  recurring_monthly_cost:recurring,one_time_implementation_cost:once,...extra});
const rows={
  eliminate_step:row(10,20,0,5000),
  deterministic_automation:row(8,60,1000,40000),
  ai_agent:row(9,80,4000,60000),
  ai_agent_with_approval:row(9,80,4000,60000,{review_minutes_per_case:3}),
};
"""


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_alternatives_are_computed_from_their_own_assumptions_and_never_recommended():
    _node(_LOAD + r"""
assert.deepEqual(Array.from(model.alternatives),['eliminate_step','deterministic_automation','ai_agent','ai_agent_with_approval','human_only']);
const result=model.compare(shared,rows);
const by=Object.fromEntries(result.alternatives.map(a=>[a.id,a]));
// 200 cases * 20% = 40 cases * 10 min = 6.67 h * 600 = 4000 gross, no recurring cost.
assert.equal(by.eliminate_step.gross_labor_value_per_month,4000);
assert.equal(by.eliminate_step.simple_payback_months,1.25);
// 120 eligible * 8 min = 16 h * 600 = 9600, minus 1000 recurring.
assert.equal(by.deterministic_automation.net_monthly_value_before_other_costs,8600);
// Agent: 160 * 9 min = 24 h -> 14400 - 4000 = 10400. With approval: 6 net min -> 9600 - 4000 = 5600.
assert.equal(by.ai_agent.net_monthly_value_before_other_costs,10400);
assert.equal(by.ai_agent_with_approval.net_minutes_saved_per_case,6);
assert.equal(by.ai_agent_with_approval.net_monthly_value_before_other_costs,5600);
assert.equal(by.ai_agent_with_approval.human_approval_required,true);
assert.equal(by.ai_agent_with_approval.autonomous_business_actions,false);
assert.equal(by.ai_agent.autonomous_business_actions,true);
assert.equal(by.human_only.net_monthly_value_before_other_costs,0);
assert.equal(by.human_only.requires_confirmed_business_rules,false);
assert.deepEqual(Array.from(result.ordered_by_modeled_net_value),
  ['ai_agent','deterministic_automation','ai_agent_with_approval','eliminate_step','human_only']);
assert.equal(result.recommendation,null);assert.equal(result.approved_alternative,null);
assert.equal(result.authorized_agent_actions,false);assert.equal(result.guaranteed_savings,false);
// Review time can exceed the saving: the alternative then shows a loss, not a clamp to zero.
const slow=model.compare(shared,{...rows,ai_agent_with_approval:row(5,100,0,0,{review_minutes_per_case:8})});
assert(slow.alternatives.find(a=>a.id==='ai_agent_with_approval').net_monthly_value_before_other_costs<0);
""")


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_missing_or_invalid_assumptions_are_refused_instead_of_defaulted():
    _node(_LOAD + r"""
const refuses=(s,r,pattern)=>assert.throws(()=>model.compare(s,r),pattern);
refuses({...shared,currency:'XYZ'},rows,/currency/);
refuses({...shared,monthly_cases:''},rows,/monthly_cases/);
refuses(shared,{...rows,ai_agent:undefined},/ai_agent\.minutes_saved_per_case/);
refuses(shared,{...rows,ai_agent_with_approval:row(9,80,4000,60000)},/review_minutes_per_case/);
refuses(shared,{...rows,eliminate_step:row(10,101,0,0)},/eliminate_step\.eligible_fraction_percent/);
refuses(shared,{...rows,deterministic_automation:row(-1,50,0,0)},/deterministic_automation\.minutes_saved_per_case/);
refuses(shared,{...rows,ai_agent:row('NaN',50,0,0)},/ai_agent\.minutes_saved_per_case/);
// The existing single-scenario calculator is unchanged.
const single=model.calculate({currency:'SEK',monthly_cases:100,minutes_saved_per_case:6,eligible_fraction_percent:50,
  loaded_hourly_cost:900,recurring_monthly_cost:1000,one_time_implementation_cost:9000});
assert.equal(single.projection.gross_labor_value_per_month,4500);
""")


def test_comparison_ui_is_bounded_safe_and_optional_in_the_export():
    source = (ROOT / "dashboard" / "opportunity_review.js").read_text(encoding="utf-8")
    assert "Compare implementation alternatives" in source
    assert "it does not recommend, approve or authorize any alternative" in source
    assert "...(comparison?{alternatives_comparison:comparison}:{})" in source
    # Untrusted labels and numbers go through textContent helpers only.
    section = source[source.index("let comparison=null;"):source.index("const style=el('style')")]
    assert "innerHTML" not in section


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_horizon_net_after_upfront_and_eligible_break_even_are_explicit_modeled_values():
    _node(_LOAD + r"""
const report=model.compare(shared,rows);
const by=Object.fromEntries(report.alternatives.map(a=>[a.id,a]));
assert.equal(report.shared_inputs.labor_impact_sensitivity_percent,25);
assert.equal(report.first_year_sensitivity_is_statistical_confidence_interval,false);
assert.equal(report.horizon_results_are_undiscounted_assumptions_not_cash_receipts,true);
assert.equal(report.future_volume_and_costs_verified,false);
assert.equal(by.eliminate_step.modeled_year_1_net_after_upfront,43000);
assert.equal(by.eliminate_step.modeled_36_month_net_after_upfront,139000);
assert.equal(by.eliminate_step.year_1_sensitivity_net_low,31000);
assert.equal(by.eliminate_step.year_1_sensitivity_net_high,55000);
assert.equal(by.eliminate_step.break_even_eligible_percent_12m,2.0833);
assert.equal(by.deterministic_automation.modeled_year_1_net_after_upfront,63200);
assert.equal(by.deterministic_automation.modeled_36_month_net_after_upfront,269600);
assert.equal(by.deterministic_automation.break_even_eligible_percent_12m,27.0833);
assert.equal(by.ai_agent.modeled_year_1_net_after_upfront,64800);
assert.equal(by.ai_agent.modeled_36_month_net_after_upfront,314400);
assert.equal(by.ai_agent.break_even_eligible_percent_12m,50);
assert.equal(by.ai_agent_with_approval.modeled_year_1_net_after_upfront,7200);
assert.equal(by.ai_agent_with_approval.year_1_sensitivity_net_low,-21600);
assert.equal(by.ai_agent_with_approval.year_1_sensitivity_net_high,36000);
assert.equal(by.ai_agent_with_approval.break_even_eligible_percent_12m,75);
assert.equal(by.human_only.modeled_year_1_net_after_upfront,0);
assert.equal(by.human_only.modeled_36_month_net_after_upfront,0);
assert.equal(by.human_only.break_even_eligible_percent_12m,null);
assert.equal(report.recommendation,null);
assert.equal(report.authorized_agent_actions,false);
""")


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_sensitivity_is_adjustable_and_impossible_or_negative_break_even_not_fabricated():
    _node(_LOAD + r"""
const noSpread=model.compare({...shared,labor_impact_sensitivity_percent:0},rows);
const noSpreadAI=noSpread.alternatives.find(a=>a.id==='ai_agent');
assert.equal(noSpreadAI.year_1_sensitivity_net_low,noSpreadAI.modeled_year_1_net_after_upfront);
assert.equal(noSpreadAI.year_1_sensitivity_net_high,noSpreadAI.modeled_year_1_net_after_upfront);
const oneHundred=model.compare({...shared,labor_impact_sensitivity_percent:100},rows);
const totalAI=oneHundred.alternatives.find(a=>a.id==='ai_agent');
assert.equal(totalAI.year_1_sensitivity_net_low,-108000);
assert.equal(totalAI.year_1_sensitivity_net_high,237600);
const impossible=model.compare(shared,{
  ...rows,ai_agent:row(1,100,900000,900000),
  ai_agent_with_approval:row(5,50,0,1000,{review_minutes_per_case:8}),
});
const agent=impossible.alternatives.find(a=>a.id==='ai_agent');
const approval=impossible.alternatives.find(a=>a.id==='ai_agent_with_approval');
assert.equal(agent.break_even_at_declared_volume_and_net_minutes_12m,false);
assert.equal(agent.break_even_eligible_percent_12m,null);
assert(agent.modeled_year_1_net_after_upfront<0);
assert.equal(approval.break_even_at_declared_volume_and_net_minutes_12m,false);
assert.equal(approval.break_even_eligible_percent_12m,null);
assert(approval.year_1_sensitivity_net_low<=approval.year_1_sensitivity_net_high);
assert(approval.modeled_36_month_net_after_upfront<0);
for(const raw of [-1,101,NaN,Infinity,'']){
  assert.throws(()=>model.compare({...shared,labor_impact_sensitivity_percent:raw},rows),
    /labor_impact_sensitivity_percent/);
}
""")


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_oversized_horizon_values_refuse_false_cent_precision():
    _node(_LOAD + r"""
const maxVolume={...shared,monthly_cases:10000000,loaded_hourly_cost:1000000};
const huge={...rows,ai_agent:row(10080,100,0,1000000000)};
assert.throws(()=>model.compare(maxVolume,huge),/cent-precision range/);
""")
