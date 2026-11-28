import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import testRunner from 'node:test';
const require=createRequire(import.meta.url);
const api=require('../../dashboard/deadline_schedule.js');

const id=i=>'idea_'+i.toString(16).padStart(16,'0'), pin='a'.repeat(64);
function make(tasks,deps,capacities,alternatives=[]){
  const ts=tasks.map((t,i)=>({idea_id:id(i),revision:1,pool:t.pool,duration_days:t.duration,units:t.units,release_day:t.release,deadline_day:t.deadline}));
  const cases=[{label:'Baseline',capacities},...alternatives].map(({label,capacities})=>{
    if(ts.some(t=>t.units>capacities[t.pool]))return {label,capacities,status:'infeasible_resource_demand',schedule:null};
    let now=0;const schedule=ts.map(t=>{const start=Math.max(now,t.release_day);now=start+t.duration_days;return {...t,start_day:start,finish_day_exclusive:now};});
    return {label,capacities,status:'scheduled_under_assumptions',schedule_verified_against_assumptions:true,schedule,makespan_days:now};
  });
  return {format:'openworkgraph.portfolio-resource-schedule.v1',status:'evaluated',execution_authorized:false,portfolio_modified:false,source_systems_queried:false,
    snapshot_sha256:pin,scenario_sha256:pin,blockers:[],reviewed_inputs:{format:'openworkgraph.portfolio-resource-schedule.v1',snapshot_sha256:pin,tasks:ts,capacities,alternatives},
    selected_ideas:ts.map((t,i)=>({idea_id:t.idea_id,revision:1,state:'approved',unfinished_selected_prerequisites:deps[i].map(id)})),scenarios:cases};
}
function brute(tasks,deps,cap,weights){
  const upper=Math.max(...tasks.map(t=>t.release))+tasks.reduce((s,t)=>s+t.duration,0), starts=[],n=tasks.length;
  let best=null;
  function go(i){
    if(i===n){
      const ends=starts.map((s,k)=>s+tasks[k].duration);
      if(deps.some((ps,k)=>ps.some(p=>ends[p]>starts[k])))return;
      for(const [pool,c] of Object.entries(cap))for(let day=0;day<Math.max(...ends);day++)
        if(tasks.reduce((s,t,k)=>s+(t.pool===pool&&starts[k]<=day&&day<ends[k]?t.units:0),0)>c)return;
      const v=[0,0,Math.max(...ends)];
      tasks.forEach((t,k)=>{if(t.deadline!==null&&ends[k]>t.deadline){v[0]+=weights[id(k)];v[1]+=weights[id(k)]*(ends[k]-t.deadline);}});
      if(!best||v.some((x,k)=>x<best[k]&&v.slice(0,k).every((a,j)=>a===best[j])))best=v;
      return;
    }
    for(let s=tasks[i].release;s<=upper-tasks[i].duration;s++){starts[i]=s;go(i+1);}
  }
  go(0);return best;
}
function exercise(api,assert){
  let seed=81234;const rnd=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return Math.floor(seed/4294967296*n);};
  let checked=0;
  for(let z=0;z<180;z++){
    const n=2+rnd(3),cap={team:1+rnd(3),other:1+rnd(2)};
    const tasks=Array.from({length:n},(_,i)=>{const pool=i===0?'team':i===1?'other':rnd(2)?'team':'other';return {pool,duration:1+rnd(3),units:1+rnd(cap[pool]),release:rnd(3),deadline:i===0||rnd(3)?1+rnd(7):null};});
    const deps=tasks.map((_,i)=>Array.from({length:i},(_,j)=>j).filter(()=>rnd(3)===0));
    const weights=Object.fromEntries(tasks.flatMap((t,i)=>t.deadline===null?[]:[[id(i),1+rnd(5)]]));
    const report=make(tasks,deps,cap,[{label:'More',capacities:{team:cap.team+1,other:cap.other}}]);
    const original=JSON.stringify(report);
    const result=api.review(report,weights);
    for(const s of result.scenarios){assert.deepEqual(s.objective,brute(tasks,deps,s.capacities,weights));assert.equal(s.optimality_proven,true);assert.equal(s.all_deadlines_infeasible_proven,s.objective[0]>0);checked++;}
    assert.equal(JSON.stringify(report),original);
  }
  const tasks=[{pool:'team',duration:3,units:1,release:0,deadline:5},{pool:'team',duration:2,units:1,release:0,deadline:2}],deps=[[],[]],report=make(tasks,deps,{team:1});
  const result=api.review(report,{[id(0)]:1,[id(1)]:5}).scenarios[0];assert.deepEqual(result.original_objective,[5,15,5]);assert.deepEqual(result.objective,[0,0,5]);
  for(const weights of [{}, {[id(0)]:true,[id(1)]:1},{[id(0)]:1,[id(1)]:1001}])assert.throws(()=>api.review(report,weights));
  const clone=()=>JSON.parse(JSON.stringify(report));
  for(const mutate of [r=>r.selected_ideas[0].unfinished_selected_prerequisites=[id(1)],r=>r.scenarios[0].schedule[1].start_day=0,r=>r.blockers.push({code:'blocked'}),r=>r.execution_authorized=true]){
    const x=clone();mutate(x);assert.throws(()=>api.review(x,{[id(0)]:1,[id(1)]:1}));}
  const bad=make(tasks.map(t=>({...t,units:2})),deps,{team:1},[{label:'Enough',capacities:{team:2}}]);
  const b=api.review(bad,{[id(0)]:1,[id(1)]:1});assert.equal(b.scenarios[0].status,'infeasible_resource_demand');assert.equal(b.scenarios[1].all_declared_deadlines_met,true);
  const large=Array.from({length:9},()=>({pool:'team',duration:2,units:2,release:0,deadline:1}));
  const l=api.review(make(large,large.map(()=>[]),{team:3}),Object.fromEntries(large.map((_,i)=>[id(i),1]))).scenarios[0];
  assert.equal(l.optimality_proven,false);assert.equal(l.all_deadlines_infeasible_proven,true);
  return checked;
}

testRunner('deadline-first schedules match independent integer-start oracle and reject invalid reports',()=>assert.equal(exercise(api,assert),360));
