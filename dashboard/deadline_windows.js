/* Dependency-only deadline windows for a validated resource schedule. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.OWGDeadlineWindows=api;})(globalThis,function(){
'use strict';
function analyze(prepared){
  const {tasks,deps,ids}=prepared;
  // The caller must pass the validated result of OWGDeadlineSchedule.prepare.
  const successors=new Map(ids.map(k=>[k,[]]));
  for(const k of ids)for(const d of deps.get(k))successors.get(d).push(k);
  const earliest=new Map(), predecessor=new Map(),order=[];
  while(order.length<ids.length){
    const ready=ids.filter(k=>!earliest.has(k)&&deps.get(k).every(d=>earliest.has(d)));
    if(!ready.length)throw Error('Dependency cycle.');
    for(const k of ready){
      const t=tasks.get(k);let start=t.release, via=null;
      for(const d of [...deps.get(k)].sort()){
        const finish=earliest.get(d)+tasks.get(d).duration;
        if(finish>start||(finish===start&&via===null)){start=finish;via=d;}
      }
      earliest.set(k,start);predecessor.set(k,via);order.push(k);
    }
  }
  const latest=new Map(), controlling=new Map(), next=new Map();
  for(const k of [...order].reverse()){
    const t=tasks.get(k);let limit=t.deadline===null?null:t.deadline-t.duration;
    let target=t.deadline===null?null:k, via=null;
    for(const s of successors.get(k).sort()){
      if(latest.get(s)===null)continue;
      const candidate=latest.get(s)-t.duration, deadline=controlling.get(s);
      if(limit===null||candidate<limit||(candidate===limit&&deadline<target)){
        limit=candidate;target=deadline;via=s;
      }
    }
    latest.set(k,limit);controlling.set(k,target);next.set(k,via);
  }
  const path=(k,links)=>{const out=[];while(k!==null){out.push(k);k=links.get(k);}return out;};
  const rows=ids.map(k=>{
    const t=tasks.get(k),start=earliest.get(k),limit=latest.get(k),target=controlling.get(k);
    return {idea_id:k,earliest_start_day:start,earliest_finish_day_exclusive:start+t.duration,
      own_deadline_day:t.deadline,latest_start_for_reachable_deadlines:limit,
      dependency_slack_days:limit===null?null:limit-start,controlling_deadline_idea_id:target,
      controlling_deadline_day:target===null?null:tasks.get(target).deadline,
      earliest_start_witness:path(k,predecessor).reverse(),deadline_path:target===null?[]:path(k,next),
      precedence_window_infeasible:limit!==null&&start>limit};
  });
  const impossible=rows.filter(r=>r.own_deadline_day!==null&&r.earliest_finish_day_exclusive>r.own_deadline_day);
  const scenarios=prepared.scenarios.filter(s=>s.original).map(s=>({label:s.label,rows:rows.map(r=>{
    const start=s.original[r.idea_id][0],limit=r.latest_start_for_reachable_deadlines;
    return {idea_id:r.idea_id,original_start_day:start,original_start_margin_days:limit===null?null:limit-start};
  })}));
  return {format:'openworkgraph.dependency-deadline-windows.v1',snapshot_sha256:prepared.snapshot,
    source_scenario_sha256:prepared.scenario,rows,scenarios,
    impossible_deadline_idea_ids:impossible.map(r=>r.idea_id),
    all_deadlines_feasible_without_resources:impossible.length===0,
    execution_authorized:false,portfolio_modified:false,resource_feasibility_established:false,
    interpretation:'Release and precedence bounds only. Negative slack proves a reachable deadline cannot be satisfied even with unlimited resources; nonnegative slack does not prove resource feasibility. Original start margins do not account for other reservations or unchanged successor start times.'};
}
return {analyze};
});
