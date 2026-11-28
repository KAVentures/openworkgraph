import {createRequire} from 'node:module';
import {test} from 'node:test';
const api=createRequire(import.meta.url)('../../dashboard/capacity_transition.js');
function exercise(api){
  const assert=(b,m='assertion failed')=>{if(!b)throw Error(m);};
  const eq=(a,b)=>assert(JSON.stringify(a)===JSON.stringify(b),JSON.stringify([a,b]));
  const reject=f=>{let ok=false;try{f();}catch(e){ok=true;}assert(ok,'expected refusal');};
  function grid(eligible,unstable=[]){
    const labels=['Baseline','Lean','Surge'],rates=[1,2],seeds=[7,8],cells=[];
    for(let d=0;d<labels.length;d++)for(let r=0;r<rates.length;r++)cells.push({design:labels[d],arrival_rate_per_hour:rates[r],replications:2,runs:seeds.map(seed=>{
      const met=eligible[d][r]?41:31,unresolved=eligible[d][r]?0:10;
      return {seed,post_warmup_arrivals:45,completed:45-unresolved,completed_on_time:met,completed_late:45-unresolved-met,abandoned:0,unresolved,overloaded:unstable.some(x=>x[0]===d&&x[1]===r&&x[2]===seed)};
    })});
    return {format:'openworkgraph.simulation-capacity-report.v1',inputs:{format:'openworkgraph.simulation-capacity-grid.v1',baseline:{cases:50,seed:7},designs:labels.slice(1).map(label=>({label})),arrival_rates_per_hour:rates,replications:2,target:{cycle_time_minutes:30,minimum_completion_share:.9}},seeds,cells,simulated_cases:600,
      calibration_and_inputs_sha256:'a'.repeat(64),single_calibration_used:true,simulated_not_observed:true,execution_authorized:false,production_capacity_certified:false,auto_selected_design:false,source_content_independently_verified:false};
  }
  const base={confirmScenario:true,sourceSha256:'b'.repeat(64),currency:'SEK',targetBasisPoints:9000,costs:[{design:'Baseline',hourlyMinorUnits:100},{design:'Lean',hourlyMinorUnits:50},{design:'Surge',hourlyMinorUnits:200}],periods:[{rate:1,minutes:60},{rate:2,minutes:60},{rate:1,minutes:60}],initialDesign:null,maxSwitches:3,switchMinorUnits:10,budgetMinorUnits:null};
  const g=grid([[true,true],[true,false],[true,true]]);
  let out=api.solve(g,base);
  eq(out.minimum_cost_tested_witness.steps.map(x=>x.design),['Lean','Baseline','Lean']);
  eq(out.minimum_cost_tested_witness.total_cost,{minor_units_numerator:'13200',denominator:60});
  eq(out.minimum_cost_tested_witness.switches,2);
  eq(api.solve(g,{...base,maxSwitches:0}).minimum_cost_tested_witness.steps.map(x=>x.design),['Baseline','Baseline','Baseline']);
  eq(api.solve(g,{...base,budgetMinorUnits:219}).status,'budget_prevents_any_service_eligible_plan');
  eq(api.solve(g,{...base,budgetMinorUnits:220}).status,'finite_plan_found');
  eq(api.solve(grid([[true,false],[true,false],[true,false]]),base).status,'no_service_eligible_design_in_one_or_more_periods');
  eq(api.solve(grid([[true,false],[false,true],[false,false]]),{...base,maxSwitches:0}).status,'switch_cap_prevents_any_service_eligible_plan');
  assert(!api.solve(grid([[true,true],[true,true],[true,true]],[[1,0,8]]),base).period_eligibility[0].designs[1].eligible);
  const copy=x=>JSON.parse(JSON.stringify(x));
  for(const change of [r=>r.cells.pop(),r=>r.cells[0].runs[0].completed++,r=>r.cells[0].runs[0].seed=8,r=>r.cells[0].runs[0].overloaded='false',r=>r.cells[0].runs[0].post_warmup_arrivals++,r=>r.execution_authorized=true]){const bad=copy(g);change(bad);reject(()=>api.solve(bad,base));}
  for(const change of [p=>p.confirmScenario=false,p=>p.periods[0].rate=1.5,p=>p.periods[0].minutes=1.1,p=>p.costs.pop(),p=>p.costs[1].design='unknown',p=>p.maxSwitches=-1,p=>p.budgetMinorUnits=NaN]){const bad=copy(base);change(bad);reject(()=>api.solve(g,bad));}
  let seed=381;const random=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed>>>8)%n;};
  // Independent full sequence enumeration. Verify exact cost and tied count
  // at every switch count, not just the selected optimum.
  for(let iteration=0;iteration<600;iteration++){
    const allowed=Array.from({length:3},()=>[!!random(2),!!random(2)]);
    const periods=Array.from({length:1+random(6)},()=>({rate:1+random(2),minutes:1+random(120)}));
    const costs=base.costs.map(c=>({...c,hourlyMinorUnits:random(5)})),fee=random(5),initial=random(4)-1;
    const maxSwitches=random(periods.length+1),budget=random(2)?null:random(40);
    const p={...base,periods,costs,switchMinorUnits:fee,initialDesign:initial<0?null:costs[initial].design,maxSwitches,budgetMinorUnits:budget};
    const oracle=new Map();
    function visit(t,last,k,cost,path){
      if(t===periods.length){const prior=oracle.get(k);if(!prior||cost<prior.cost)oracle.set(k,{cost,count:1,path});else if(cost===prior.cost)prior.count++;return;}
      for(let d=0;d<3;d++)if(allowed[d][periods[t].rate-1]){const change=Number(last>=0&&last!==d);visit(t+1,d,k+change,cost+periods[t].minutes*costs[d].hourlyMinorUnits+change*fee*60,[...path,d]);}
    }
    visit(0,initial,0,0,[]);
    const result=api.solve(grid(allowed),p);
    eq(result.exact_switch_cost_frontier.map(x=>[x.switches,Number(x.cost.minor_units_numerator),Number(x.tied_minimum_paths)]),[...oracle].sort((a,b)=>a[0]-b[0]).map(([k,v])=>[k,v.cost,v.count]));
    const all=[...oracle].filter(([k,v])=>k<=maxSwitches&&(budget===null||v.cost<=budget*60)).sort((a,b)=>a[1].cost-b[1].cost||a[0]-b[0]);
    if(!all.length)eq(result.minimum_cost_tested_witness,null);else{
      const best=result.minimum_cost_tested_witness;eq(Number(best.total_cost.minor_units_numerator),all[0][1].cost);eq(best.switches,all[0][0]);
      eq(Number(best.tied_minimum_paths_across_allowed_switch_counts),all.filter(x=>x[1].cost===all[0][1].cost).reduce((n,x)=>n+x[1].count,0));
      eq(best.steps.map(x=>costs.findIndex(c=>c.design===x.design)),all[0][1].path);
    }
  }
  const tied=api.solve(grid([[true,true],[true,true],[true,true]]),{...base,periods:Array.from({length:32},()=>({rate:1,minutes:1})),costs:base.costs.map(x=>({...x,hourlyMinorUnits:0})),switchMinorUnits:0,maxSwitches:32});
  eq(tied.minimum_cost_tested_witness.tied_minimum_paths_across_allowed_switch_counts,(3n**32n).toString());
  return '600 independent exhaustive sequence oracles and deterministic edge, refusal, exact tie and budget checks passed';
}
test('finite capacity transition exact oracle and admission',()=>exercise(api));
