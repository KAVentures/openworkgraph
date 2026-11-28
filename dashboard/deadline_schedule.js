/* Deadline-first review of an existing retained resource schedule.
   UMD so the exact browser implementation can be tested in Node. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.OWGDeadlineSchedule=api;})(globalThis,function(){
'use strict';
const FORMAT='openworkgraph.deadline-first-schedule.v1', SOURCE='openworkgraph.portfolio-resource-schedule.v1';
const ID=/^idea_[a-f0-9]{16}$/, POOL=/^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/, PIN=/^[a-f0-9]{64}$/;
const fail=m=>{throw Error(m);};
const int=(x,a,b)=>Number.isSafeInteger(x)&&x>=a&&x<=b;
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const compare=(a,b)=>{for(let i=0;i<a.length;i++){if(a[i]!==b[i])return a[i]-b[i];}return 0;};
const keys=(a,b)=>Object.keys(a).sort().join('|')===Object.keys(b).sort().join('|');
function prepare(report){
  if(!obj(report)||report.format!==SOURCE||report.status!=='evaluated'||report.execution_authorized!==false||
     report.portfolio_modified!==false||report.source_systems_queried!==false||!PIN.test(report.snapshot_sha256||'')||
     !PIN.test(report.scenario_sha256||'')||!Array.isArray(report.blockers)||report.blockers.length)fail('Use an evaluated, unblocked resource schedule report.');
  const s=report.reviewed_inputs;
  if(!obj(s)||s.format!==SOURCE||s.snapshot_sha256!==report.snapshot_sha256||!Array.isArray(s.tasks)||!int(s.tasks.length,1,30)||
     !obj(s.capacities)||!int(Object.keys(s.capacities).length,1,8)||!Array.isArray(s.alternatives)||s.alternatives.length>3||
     !Array.isArray(report.selected_ideas)||report.selected_ideas.length!==s.tasks.length||!Array.isArray(report.scenarios)||report.scenarios.length!==1+s.alternatives.length)fail('Incomplete resource schedule assumptions.');
  const tasks=new Map(), deps=new Map();
  for(const t of s.tasks){
    if(!obj(t)||typeof t.idea_id!=='string'||!ID.test(t.idea_id)||tasks.has(t.idea_id)||!int(t.revision,1,Number.MAX_SAFE_INTEGER)||
       typeof t.pool!=='string'||!POOL.test(t.pool)||!int(t.duration_days,1,180)||!int(t.units,1,50)||!int(t.release_day,0,730)||
       !(t.deadline_day===null||int(t.deadline_day,1,730)))fail('Invalid task assumptions.');
    tasks.set(t.idea_id,{id:t.idea_id,rev:t.revision,pool:t.pool,duration:t.duration_days,units:t.units,release:t.release_day,deadline:t.deadline_day});
  }
  const pools=new Set([...tasks.values()].map(t=>t.pool));
  function caps(c){if(!obj(c)||Object.keys(c).length!==pools.size||Object.entries(c).some(([p,v])=>!pools.has(p)||!int(v,1,50)))fail('Invalid capacity assumptions.');return {...c};}
  const scenarios=[{label:'Baseline',capacities:caps(s.capacities)}],names=new Set(['baseline']);
  for(const a of s.alternatives){
    if(!obj(a)||typeof a.label!=='string'||!a.label.trim()||a.label.length>60||names.has(a.label.trim().toLowerCase()))fail('Invalid capacity alternative.');
    names.add(a.label.trim().toLowerCase());scenarios.push({label:a.label,capacities:caps(a.capacities)});
  }
  for(const i of report.selected_ideas){
    const t=tasks.get(i?.idea_id);
    if(!t||deps.has(t.id)||i.revision!==t.rev||!['idea','assessing','approved'].includes(i.state)||!Array.isArray(i.unfinished_selected_prerequisites)||
       new Set(i.unfinished_selected_prerequisites).size!==i.unfinished_selected_prerequisites.length||
       i.unfinished_selected_prerequisites.some(d=>!tasks.has(d)||d===t.id))fail('Invalid selected dependency population.');
    deps.set(t.id,[...i.unfinished_selected_prerequisites]);
  }
  const ids=[...tasks.keys()].sort(),finish={},tail={},order=[];
  while(order.length<ids.length){
    const ready=ids.filter(k=>!(k in finish)&&deps.get(k).every(p=>p in finish));
    if(!ready.length)fail('Selected dependencies contain a cycle.');
    for(const k of ready){const t=tasks.get(k);finish[k]=Math.max(t.release,...deps.get(k).map(p=>finish[p]))+t.duration;order.push(k);}
  }
  for(const k of [...order].reverse())tail[k]=tasks.get(k).duration+Math.max(0,...ids.filter(x=>deps.get(x).includes(k)).map(x=>tail[x]));
  for(let i=0;i<scenarios.length;i++){
    const a=scenarios[i],b=report.scenarios[i];
    if(!obj(b)||b.label!==a.label||!obj(b.capacities)||!keys(a.capacities,b.capacities)||Object.keys(a.capacities).some(p=>a.capacities[p]!==b.capacities[p]))fail('Scenario capacity mismatch.');
    const oversized=ids.filter(k=>tasks.get(k).units>a.capacities[tasks.get(k).pool]);
    if(oversized.length){if(b.status!=='infeasible_resource_demand'||b.schedule!==null)fail('Invalid infeasible scenario.');a.oversized=oversized;continue;}
    if(b.status!=='scheduled_under_assumptions'||b.schedule_verified_against_assumptions!==true||!Array.isArray(b.schedule)||b.schedule.length!==ids.length)fail('Missing feasible original witness.');
    const original={};
    for(const row of b.schedule){const t=tasks.get(row?.idea_id);if(!t||t.id in original||row.pool!==t.pool||row.units!==t.units||
       row.duration_days!==t.duration||row.release_day!==t.release||row.deadline_day!==t.deadline||
       !int(row.start_day,0,6130)||row.finish_day_exclusive!==row.start_day+t.duration)fail('Invalid original schedule row.');
       original[t.id]=[row.start_day,row.finish_day_exclusive];}
    verify(tasks,deps,a.capacities,original);
    if(Math.max(...Object.values(original).map(x=>x[1]))!==b.makespan_days)fail('Original makespan mismatch.');
    a.original=original;
  }
  return {tasks,deps,ids,finish,tail,scenarios,snapshot:report.snapshot_sha256,scenario:report.scenario_sha256};
}
function verify(tasks,deps,cap,schedule){
  if(Object.keys(schedule).length!==tasks.size)fail('Incomplete schedule.');
  const changes={};for(const p of Object.keys(cap))changes[p]=new Map();
  for(const [k,t] of tasks){const x=schedule[k];
    if(!x||!int(x[0],t.release,6130)||x[1]!==x[0]+t.duration||deps.get(k).some(p=>!schedule[p]||schedule[p][1]>x[0]))fail('Schedule violates timing or prerequisites.');
    const m=changes[t.pool];m.set(x[0],(m.get(x[0])||0)+t.units);m.set(x[1],(m.get(x[1])||0)-t.units);}
  for(const p of Object.keys(cap)){let load=0;for(const [,d] of [...changes[p]].sort((a,b)=>a[0]-b[0])){load+=d;if(load<0||load>cap[p])fail('Schedule exceeds capacity.');}if(load)fail('Unclosed reservation.');}
}
function score(tasks,schedule,weights,finish={}){
  let misses=0,tardy=0,span=0;
  for(const [k,t] of tasks){const end=schedule[k]?.[1]??finish[k];span=Math.max(span,end);
    if(t.deadline!==null&&end>t.deadline){misses+=weights[k];tardy+=weights[k]*(end-t.deadline);}}
  return [misses,tardy,span];
}
function optimize(p,scenario,weights){
  if(scenario.oversized)return {label:scenario.label,capacities:scenario.capacities,status:'infeasible_resource_demand',oversized_task_ids:scenario.oversized};
  const {tasks,deps,ids,finish,tail}=p, cap=scenario.capacities;
  const original=score(tasks,scenario.original,weights),bound=score(tasks,{},weights,finish);
  for(const [pool,c] of Object.entries(cap)){
    const ts=[...tasks.values()].filter(t=>t.pool===pool);
    bound[2]=Math.max(bound[2],Math.min(...ts.map(t=>finish[t.id]-t.duration))+Math.ceil(ts.reduce((s,t)=>s+t.duration*t.units,0)/c));}
  let best=scenario.original,bestScore=original,visited=0,leaves=0,pruned=0;
  const schedule={},reservations=Object.fromEntries(Object.keys(cap).map(k=>[k,[]]));
  function place(k){const t=tasks.get(k),ready=Math.max(t.release,...deps.get(k).map(d=>schedule[d][1]));
    const list=reservations[t.pool],c=cap[t.pool],candidates=[...new Set([ready,...list.map(x=>x[1]).filter(x=>x>ready)])].sort((a,b)=>a-b);
    for(const start of candidates){const end=start+t.duration,events=new Map([[start,0]]);
      for(const [a,b,u] of list)if(a<end&&b>start){const x=Math.max(a,start),y=Math.min(b,end);events.set(x,(events.get(x)||0)+u);events.set(y,(events.get(y)||0)-u);}
      let load=t.units,ok=true;for(const [day,d] of [...events].sort((a,b)=>a[0]-b[0])){load+=d;if(day<end&&load>c){ok=false;break;}}
      if(ok){schedule[k]=[start,end];list.push([start,end,t.units]);return;}}
    fail('No feasible serial placement.');
  }
  const ready=()=>ids.filter(k=>!(k in schedule)&&deps.get(k).every(d=>d in schedule));
  const orderings=[
    k=>[tasks.get(k).deadline??100000,-(weights[k]||0),-tail[k]],
    k=>[-(weights[k]||0),tasks.get(k).deadline??100000,-tail[k]],
    k=>[-tail[k],tasks.get(k).deadline??100000, -(weights[k]||0)],
    k=>[(tasks.get(k).deadline??100000)-finish[k],-(weights[k]||0),-tail[k]]
  ];
  const ordered=f=>ready().sort((a,b)=>compare(f(a),f(b))||a.localeCompare(b));
  function consider(){const v=score(tasks,schedule,weights);if(compare(v,bestScore)<0){bestScore=v;best=Object.fromEntries(Object.entries(schedule).map(([k,x])=>[k,[...x]]));}}
  function undo(k){reservations[tasks.get(k).pool].pop();delete schedule[k];}
  for(const f of orderings){const placed=[];while(placed.length<ids.length){const k=ordered(f)[0];place(k);placed.push(k);}consider();while(placed.length)undo(placed.pop());}
  // All nondecreasing completion-time objectives have an optimal serial
  // schedule: order a feasible solution by start times, then move each task
  // left without crossing its release, dependencies or resource occupancy.
  // Search all topological orders for small populations. Partial bound uses
  // fixed ends plus resource-free earliest ends for unplaced tasks.
  const exact=ids.length<=7;
  function search(){
    visited++;if(compare(bestScore,bound)===0)return;
    const lower=score(tasks,schedule,weights,finish);lower[2]=Math.max(lower[2],bound[2]);
    if(compare(lower,bestScore)>=0){pruned++;return;}
    if(Object.keys(schedule).length===ids.length){leaves++;consider();return;}
    for(const k of ordered(orderings[0])){place(k);search();undo(k);if(compare(bestScore,bound)===0)break;}
  }
  if(exact&&compare(bestScore,bound)>0)search();
  verify(tasks,deps,cap,best);
  const proven=exact||compare(bestScore,bound)===0;
  return {label:scenario.label,capacities:cap,status:'scheduled_under_assumptions',original_objective:original,
    objective:bestScore,lower_bound:proven?bestScore:bound,analytical_lower_bound:bound,optimality_proven:proven,
    method:compare(bestScore,bound)===0?'matched_lower_bound':exact?'complete_serial_order_search':'four_serial_priorities_plus_original',
    search_nodes:visited,complete_orders_scored:leaves,pruned_prefixes:pruned,
    all_declared_deadlines_met:bestScore[0]===0,all_deadlines_infeasible_proven:bound[0]>0||(proven&&bestScore[0]>0),
    schedule:ids.map(k=>{const t=tasks.get(k),[start,end]=best[k];return {idea_id:k,pool:t.pool,units:t.units,start_day:start,finish_day_exclusive:end,
      deadline_day:t.deadline,deadline_weight:t.deadline===null?null:weights[k],late_days:t.deadline===null?null:Math.max(0,end-t.deadline)};})};
}
function review(report,weights){
  const p=prepare(report),dated=p.ids.filter(k=>p.tasks.get(k).deadline!==null);
  if(!dated.length)fail('Declare at least one deadline in the resource schedule.');
  if(!obj(weights)||Object.keys(weights).length!==dated.length||dated.some(k=>!Object.prototype.hasOwnProperty.call(weights,k)||!int(weights[k],1,1000)))fail('Declare one integer weight from 1 to 1000 per dated opportunity.');
  const results=p.scenarios.map(s=>optimize(p,s,weights));
  return {format:FORMAT,snapshot_sha256:p.snapshot,source_scenario_sha256:p.scenario,weights:{...weights},
    objective_order:['weighted_missed_deadlines','weighted_late_days','makespan_days'],scenarios:results,
    source_digest_authenticated:false,current_portfolio_verified:false,execution_authorized:false,portfolio_modified:false,
    interpretation:'Lexicographic minimum under declared static assumptions, not a calendar forecast. Original report pins are retained but not independently authenticated. Exact for at most seven selected tasks or a matched analytical bound. A non-optimal missed deadline does not prove infeasibility.'};
}
return {prepare,review};
});
