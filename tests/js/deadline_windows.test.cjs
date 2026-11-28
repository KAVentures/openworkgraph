const {test}=require('node:test');
const assert=require('node:assert/strict');
const {analyze}=require('../../dashboard/deadline_windows.js');
function make(rows,edges){
  const ids=rows.map((_,i)=>String(i).padStart(2,'0'));
  return {ids,tasks:new Map(rows.map((r,i)=>[ids[i],r])),deps:new Map(edges.map((d,i)=>[ids[i],d.map(x=>ids[x])])),scenarios:[],snapshot:'a'.repeat(64),scenario:'b'.repeat(64)};
}
function oracle(p,k){
  const paths=[[k]], all=[];
  while(paths.length){const path=paths.pop();all.push(path);for(const n of p.ids)if(p.deps.get(n).includes(path.at(-1)))paths.push([...path,n]);}
  const bound=all.filter(x=>p.tasks.get(x.at(-1)).deadline!==null).map(x=>[p.tasks.get(x.at(-1)).deadline-x.reduce((v,n)=>v+p.tasks.get(n).duration,0),x.at(-1)]).sort((a,b)=>a[0]-b[0]||a[1].localeCompare(b[1]));
  const prev=[[k]],back=[];
  while(prev.length){const path=prev.pop();back.push(path);for(const n of p.deps.get(path.at(-1)))prev.push([...path,n]);}
  // Reverse path omits the current task, not the source task, from the sum.
  const exact=Math.max(...back.map(x=>p.tasks.get(x.at(-1)).release+x.slice(1).reduce((v,n)=>v+p.tasks.get(n).duration,0)));
  return {earliest:exact,limit:bound.length?bound[0][0]:null,target:bound.length?bound[0][1]:null};
}
test('all-path independent oracle on 600 randomized DAGs',()=>{
  let seed=501;const rnd=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n;};
  for(let trial=0;trial<600;trial++){
    const n=1+rnd(9), rows=Array.from({length:n},()=>({duration:1+rnd(5),release:rnd(8),deadline:rnd(3)?rnd(25)+1:null}));
    const edges=rows.map((_,i)=>Array.from({length:i},(_,j)=>j).filter(()=>rnd(3)===0));
    const p=make(rows,edges),r=analyze(p);
    for(const row of r.rows){const e=oracle(p,row.idea_id);assert.equal(row.earliest_start_day,e.earliest);assert.equal(row.latest_start_for_reachable_deadlines,e.limit);assert.equal(row.controlling_deadline_idea_id,e.target);
      assert.equal(row.precedence_window_infeasible,e.limit!==null&&e.earliest>e.limit);
      const path=row.earliest_start_witness;assert.equal(path.at(-1),row.idea_id);
      assert.equal(p.tasks.get(path[0]).release+path.slice(0,-1).reduce((v,k)=>v+p.tasks.get(k).duration,0),e.earliest);
      if(e.target!==null){const w=row.deadline_path;assert.equal(w[0],row.idea_id);assert.equal(w.at(-1),e.target);assert.equal(p.tasks.get(e.target).deadline-w.reduce((v,k)=>v+p.tasks.get(k).duration,0),e.limit);}
    }
    assert.deepEqual(r.impossible_deadline_idea_ids,r.rows.filter(x=>x.own_deadline_day!==null&&x.earliest_finish_day_exclusive>x.own_deadline_day).map(x=>x.idea_id));
  }
});
test('inherited deadline and negative slack, unconstrained branch and baseline margins',()=>{
  const p=make([{duration:3,release:2,deadline:null},{duration:4,release:0,deadline:7},{duration:1,release:0,deadline:null}],[[],[0],[]]);
  p.scenarios=[{label:'Baseline',original:{'00':[2,5],'01':[5,9],'02':[0,1]}},{label:'Unavailable'}];
  const r=analyze(p);assert.equal(r.rows[0].latest_start_for_reachable_deadlines,0);assert.equal(r.rows[0].dependency_slack_days,-2);assert.equal(r.rows[2].dependency_slack_days,null);assert.deepEqual(r.impossible_deadline_idea_ids,['01']);assert.equal(r.scenarios[0].rows[0].original_start_margin_days,-2);assert.equal(r.scenarios.length,1);
});
test('ties, cycles and no deadlines',()=>{
  const p=make([{duration:1,release:0,deadline:null},{duration:1,release:0,deadline:5},{duration:2,release:0,deadline:6}],[[],[0],[0]]);
  assert.equal(analyze(p).rows[0].controlling_deadline_idea_id,'01');
  for(const t of p.tasks.values())t.deadline=null;
  assert.ok(analyze(p).rows.every(x=>x.dependency_slack_days===null));
  p.deps.set('00',['01']);assert.throws(()=>analyze(p),/cycle/);
});
