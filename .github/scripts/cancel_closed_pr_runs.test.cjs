'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {run,closedRunCandidate}=require('./cancel_closed_pr_runs.cjs');
const id=123, sha='abc', number=7;
const association={number,base:{repo:{id}},head:{repo:{id},sha}};
const queued={id:99,event:'pull_request',status:'queued',head_sha:sha,pull_requests:[association]};
test('rejects non-PR, completed, missing, ambiguous, foreign or mismatched associations',()=>{
  assert.equal(closedRunCandidate(queued,id,number),number);
  for(const item of [{...queued,event:'push'},{...queued,status:'completed'},{...queued,pull_requests:[]},
    {...queued,pull_requests:[association,{...association,number:8}]},
    {...queued,pull_requests:[{...association,base:{repo:{id:456}}}]},
    {...queued,pull_requests:[{...association,head:{repo:{id:456},sha}}]},
    {...queued,head_sha:'different'}]) assert.equal(closedRunCandidate(item,id,number),null);
  assert.equal(closedRunCandidate(queued,id,8),null);
});
function mock({state='closed',openBranch=false,dry=false,reopened=false,changed=false}={}){
  const calls=[];let reads=0;
  const pr={number,state,head:{sha,ref:'feature',repo:{id,owner:{login:'owner'}}},base:{repo:{id}}};
  const github={rest:{actions:{
    listWorkflowRunsForRepo:async args=>{assert.equal(args.head_sha,sha);return {data:{workflow_runs:[queued]}};},
    getWorkflowRun:async()=>({data:changed?{...queued,status:'completed'}:queued}),
    cancelWorkflowRun:async a=>calls.push(a.run_id)
  },pulls:{get:async()=>({data:{...pr,state:reopened&&++reads>1?'open':state}}),
    list:async args=>({data:args.state==='closed'?[{...pr,state:'closed'}]:openBranch?[{number:8}]:[]})}}};
  const context={repo:{owner:'owner',repo:'repo'},eventName:dry?'workflow_dispatch':'schedule',
    payload:{repository:{id},inputs:{dry_run:'true'}}};
  const core={info(){},warning(){},setFailed(m){throw Error(m)}};
  return {calls,github,context,core};
}
test('cancels exactly one closed-PR run',async()=>{let m=mock();await run(m);assert.deepEqual(m.calls,[99]);});
test('preserves active PRs, open branch reuse, dry runs, reopened PRs and completed runs',async()=>{
  for(const config of [{state:'open'},{openBranch:true},{dry:true},{reopened:true},{changed:true}]){
    let m=mock(config);await run(m);assert.deepEqual(m.calls,[]);
  }
});
test('closed-event targets the exact PR head without querying other closed PRs',async()=>{
  let m=mock();
  m.context.eventName='pull_request_target';
  m.context.payload.pull_request={...association,state:'closed'};
  const list=m.github.rest.pulls.list;
  m.github.rest.pulls.list=async args=>{assert.equal(args.state,'open');return list(args);};
  await run(m);assert.deepEqual(m.calls,[99]);
});
