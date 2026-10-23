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
