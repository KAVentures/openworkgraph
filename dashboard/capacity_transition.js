/* R14: exact finite-horizon capacity planning over an existing simulated grid.
 * No new simulation, source access, extrapolation or execution. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.OWGCapacityTransition=api;})(typeof globalThis==='object'?globalThis:this,function(){
'use strict';
const FORMAT='openworkgraph.simulation-capacity-report.v1',OUTPUT='openworkgraph.capacity-transition-plan.v1',MAX_BYTES=8*1024*1024,MAX_PERIODS=32;
const bad=m=>{throw Error(m);};
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const int=(x,min,max,name)=>{if(!Number.isSafeInteger(x)||x<min||x>max)bad('Invalid '+name);return x;};
const num=(x,min,max,name)=>{if(typeof x!=='number'||!Number.isFinite(x)||x<min||x>max)bad('Invalid '+name);return x;};
const label=x=>{if(typeof x!=='string'||x.length<1||x.length>80||x.trim()!==x||/[\u0000-\u001f\u007f-\u009f]/.test(x))bad('Invalid design label');return x;};
const unique=(xs,name)=>{if(new Set(xs).size!==xs.length)bad('Duplicate '+name);return xs;};
const same=(a,b)=>a.length===b.length&&a.every(x=>b.includes(x));
function readReport(r){
  if(!obj(r)||r.format!==FORMAT||r.single_calibration_used!==true||r.simulated_not_observed!==true||r.execution_authorized!==false||r.production_capacity_certified!==false||r.auto_selected_design!==false||r.source_content_independently_verified!==false)bad('Choose an unsigned OWG capacity-grid report');
  const i=r.inputs;if(!obj(i)||i.format!=='openworkgraph.simulation-capacity-grid.v1'||!obj(i.baseline)||!obj(i.target)||!Array.isArray(i.designs)||i.designs.length<1||i.designs.length>3||!Array.isArray(i.arrival_rates_per_hour)||i.arrival_rates_per_hour.length<2||i.arrival_rates_per_hour.length>8)bad('Invalid capacity grid inputs');
  const designs=unique(['Baseline',...i.designs.map(x=>label(x?.label))],'design'),rates=unique(i.arrival_rates_per_hour.map(x=>num(x,.001,10000,'tested rate')),'rate').sort((a,b)=>a-b);
  const reps=int(i.replications,2,8,'replications'),cases=int(i.baseline.cases,50,3000,'simulated cases'),first=int(i.baseline.seed,0,2147483649-reps,'base seed');
  const seeds=Array.from({length:reps},(_,k)=>first+k),deadline=num(i.target.cycle_time_minutes,.001,525600,'cycle-time deadline');
  const originalShare=num(i.target.minimum_completion_share,.01,1,'original target share');
  if(!Array.isArray(r.seeds)||!same(unique(r.seeds,'seed'),seeds)||designs.length*rates.length>24||designs.length*rates.length*reps*cases>100000||r.simulated_cases!==designs.length*rates.length*reps*cases||!Array.isArray(r.cells)||r.cells.length!==designs.length*rates.length)bad('Incomplete tested grid');
  if(typeof r.calibration_and_inputs_sha256!=='string'||!/^[a-f0-9]{64}$/.test(r.calibration_and_inputs_sha256))bad('Missing original calibration/input digest');
  const cells=new Map();let arrivalCount=null;
  for(const c of r.cells){
    if(!obj(c)||!designs.includes(c.design)||!rates.includes(c.arrival_rate_per_hour)||!Array.isArray(c.runs)||c.runs.length!==reps||c.replications!==reps)bad('Unexpected design, demand or replication cell');
    const key=designs.indexOf(c.design)+':'+rates.indexOf(c.arrival_rate_per_hour);if(cells.has(key))bad('Duplicate grid cell');
    const seen=new Set(),runs=[];
    for(const run of c.runs){
      if(!obj(run)||!seeds.includes(run.seed)||seen.has(run.seed)||typeof run.overloaded!=='boolean')bad('Invalid or duplicate seed');
      seen.add(run.seed);
      const expectedArrivals=cases-Math.floor(cases*0.1);
      const n=int(run.post_warmup_arrivals,expectedArrivals,expectedArrivals,'post-warmup arrivals'),met=int(run.completed_on_time,0,n,'on-time completions'),late=int(run.completed_late,0,n,'late completions'),abandoned=int(run.abandoned,0,n,'abandonments'),unresolved=int(run.unresolved,0,n,'unresolved arrivals');
      if(run.completed!==met+late||met+late+abandoned+unresolved!==n||(arrivalCount!==null&&n!==arrivalCount))bad('Inconsistent complete-arrival accounting');
      arrivalCount=n;runs.push({seed:run.seed,n,met,late,abandoned,unresolved,overloaded:run.overloaded});
    }
    cells.set(key,runs.sort((a,b)=>a.seed-b.seed));
  }
  return {designs,rates,seeds,cells,deadline,originalShare,calibrationDigest:r.calibration_and_inputs_sha256};
}
function options(p,g){
  if(!obj(p)||p.confirmScenario!==true||typeof p.sourceSha256!=='string'||!/^[a-f0-9]{64}$/.test(p.sourceSha256))bad('Review and confirm exact report and scenario');
  if(typeof p.currency!=='string'||!/^[A-Z]{3}$/.test(p.currency))bad('Declare a three-letter currency');
  if(!Array.isArray(p.periods)||p.periods.length<1||p.periods.length>MAX_PERIODS)bad('Choose 1–32 ordered demand periods');
  if(!Array.isArray(p.costs)||p.costs.length!==g.designs.length)bad('Declare every design staffed-hour cost');
  const costs=new Map();
  for(const c of p.costs){if(!obj(c)||!g.designs.includes(c.design)||costs.has(c.design))bad('Cost labels must match exact tested designs');costs.set(c.design,int(c.hourlyMinorUnits,0,1000000000,'hourly minor-unit cost'));}
  const periods=p.periods.map((v,k)=>{
    if(!obj(v)||!g.rates.includes(v.rate))bad('Period '+(k+1)+' must use an exact tested demand rate');
    return {period:k+1,rate:v.rate,minutes:int(v.minutes,1,10080,'period minutes')};
  });
  const initial=p.initialDesign===null?null:p.initialDesign;
  if(initial!==null&&!g.designs.includes(initial))bad('Initial design must match a tested design or be unspecified');
  const cap=int(p.maxSwitches,0,periods.length,'switch cap'),fee=int(p.switchMinorUnits,0,1000000000,'per-switch minor-unit cost'),target=int(p.targetBasisPoints,1,10000,'service target basis points');
  const budget=p.budgetMinorUnits===null?null:int(p.budgetMinorUnits,0,100000000000000,'budget minor units');
  return {currency:p.currency,periods,costs,initial,cap,fee,target,budget,sourceSha256:p.sourceSha256};
}
function eligibility(runs,target){
  const over=runs.filter(r=>r.overloaded).map(r=>r.seed);
  const miss=runs.filter(r=>(r.met+r.unresolved)*10000<target*r.n).map(r=>r.seed);
  const uncertain=runs.filter(r=>r.met*10000<target*r.n&&(r.met+r.unresolved)*10000>=target*r.n).map(r=>r.seed);
  return {eligible:!over.length&&!miss.length&&!uncertain.length,overloaded_seeds:over,known_miss_seeds:miss,indeterminate_seeds:uncertain,
    worst_seed_lower_share:Math.min(...runs.map(r=>r.met/r.n)),worst_seed_upper_share:Math.min(...runs.map(r=>(r.met+r.unresolved)/r.n))};
}
// Cost numerator in sixtieths of a currency minor unit. All arithmetic
// including switch fees, budget comparison and ties uses integers.
const amount=n=>({minor_units_numerator:n.toString(),denominator:60});
function solve(report,proposal){
  const g=readReport(report),p=options(proposal,g);
  const evidence=p.periods.map(v=>({...v,designs:g.designs.map((d,k)=>({design:d,...eligibility(g.cells.get(k+':'+g.rates.indexOf(v.rate)),p.target)}))}));
  const fee=BigInt(p.fee)*60n,initial=p.initial===null?-1:g.designs.indexOf(p.initial),budget=p.budget===null?null:BigInt(p.budget)*60n;
  const periodCost=(v,d)=>BigInt(v.minutes)*BigInt(p.costs.get(g.designs[d]));
  // Each (last design, exact switch count) retains a minimum cost,
  // count of tied optimal paths, and deterministic first witness.
  let states=new Map([['-1:0',{last:initial,k:0,cost:0n,count:1n,path:[]}]]);
  for(let t=0;t<p.periods.length;t++){
    const next=new Map(),v=p.periods[t];
    for(const s of states.values())for(let d=0;d<g.designs.length;d++){
      if(!evidence[t].designs[d].eligible)continue;
      const change=Number(s.last!==-1&&s.last!==d),k=s.k+change;
      // Compute the entire exact-switch frontier, even above the user's cap.
      const cost=s.cost+periodCost(v,d)+BigInt(change)*fee,key=d+':'+k,existing=next.get(key),path=[...s.path,d];
      if(!existing||cost<existing.cost)next.set(key,{last:d,k,cost,count:s.count,path});
      else if(cost===existing.cost){existing.count+=s.count;if(path.join('')<existing.path.join(''))existing.path=path;}
    }
    states=next;
  }
  const minima=new Map();
  for(const s of states.values()){
    const x=minima.get(s.k);
    if(!x||s.cost<x.cost)minima.set(s.k,{...s});
    else if(s.cost===x.cost){x.count+=s.count;if(s.path.join('')<x.path.join(''))x.path=s.path;}
  }
  const frontier=[...minima.values()].sort((a,b)=>a.k-b.k).map(s=>({switches:s.k,cost:amount(s.cost),within_switch_cap:s.k<=p.cap,within_budget:budget===null?null:s.cost<=budget,tied_minimum_paths:s.count.toString(),witness_designs:s.path.map(d=>g.designs[d])}));
  const candidates=[...minima.values()].filter(s=>s.k<=p.cap&&(budget===null||s.cost<=budget)).sort((a,b)=>a.cost<b.cost?-1:a.cost>b.cost?1:a.k-b.k);
  const best=candidates[0],equal=best?candidates.filter(s=>s.cost===best.cost):[];
  let witness=null;
  if(best){
    let last=initial,total=0n;
    const steps=best.path.map((d,k)=>{const change=last!==-1&&last!==d,c=periodCost(p.periods[k],d),f=change?fee:0n;last=d;total+=c+f;return {...p.periods[k],design:g.designs[d],switch_from_previous:change,staffed_cost:amount(c),switch_cost:amount(f),cumulative_cost:amount(total),evidence:evidence[k].designs[d]};});
    witness={switches:best.k,total_cost:amount(best.cost),tied_minimum_paths_across_allowed_switch_counts:equal.reduce((n,x)=>n+x.count,0n).toString(),steps};
  }
  const fixed=g.designs.map((design,d)=>{
    const feasible=evidence.every(v=>v.designs[d].eligible),switches=Number(initial!==-1&&initial!==d);
    const cost=p.periods.reduce((n,v)=>n+periodCost(v,d),0n)+BigInt(switches)*fee;
    return {design,service_eligible_every_period:feasible,switches,cost:amount(cost),within_switch_cap:switches<=p.cap,within_budget:budget===null?null:cost<=budget,qualifies:feasible&&switches<=p.cap&&(budget===null||cost<=budget)};
  });
  const reason=best?'finite_plan_found':evidence.some(v=>!v.designs.some(d=>d.eligible))?'no_service_eligible_design_in_one_or_more_periods':![...minima.keys()].some(k=>k<=p.cap)?'switch_cap_prevents_any_service_eligible_plan':'budget_prevents_any_service_eligible_plan';
  return {format:OUTPUT,source_report_sha256:p.sourceSha256,original_calibration_and_inputs_sha256:g.calibrationDigest,
    source_authenticity_verified:false,original_calibration_digest_recomputed:false,deadline_minutes:g.deadline,original_target_share:g.originalShare,reviewed_target_basis_points:p.target,
    assumptions:{currency:p.currency,costs:[...p.costs].map(([design,hourlyMinorUnits])=>({design,hourly_minor_units:hourlyMinorUnits})),initial_design:p.initial,max_switches:p.cap,per_switch_minor_units:p.fee,budget_minor_units:p.budget,periods:p.periods},
    status:reason,period_eligibility:evidence,exact_switch_cost_frontier:frontier,fixed_design_comparators:fixed,minimum_cost_tested_witness:witness,
    queue_carryover_simulated:false,transition_feasibility_verified:false,production_capacity_certified:false,staffing_recommendation_made:false,execution_authorized:false,
    interpretation:'Exact minimum declared cost among the finite sequences of tested designs that pass the selected target in every original seed at each exact tested demand rate. Switches, costs, demand and period durations are hypothetical. The independent steady-demand simulations do not model transient queues, carryover, training, shifts, worker availability or switching feasibility. This is not a production staffing schedule, SLA, or observed saving.'};
}
return {readReport,solve,MAX_BYTES,MAX_PERIODS};
});
