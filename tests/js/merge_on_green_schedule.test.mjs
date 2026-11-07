import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const merger = require('../../.github/scripts/merge_on_green.cjs');
const sha = 'a'.repeat(40);

function setup({approved=true,complete=true,mainFresh=true}={}){
  const PR = {
    number: 701, state: 'open', draft: false, mergeable: true, mergeable_state: 'clean',
    head: {sha, ref: 'feat/explicit-opt-in', repo:{full_name:'KAVentures/openworkgraph'}},
    base: {ref:'main', repo:{full_name:'KAVentures/openworkgraph'}},
    body: approved ? `<!-- OWG-MERGE-ON-GREEN:${sha} -->` : '',
    requested_reviewers: [], requested_teams: [],
  };
  const dummy = {...PR,number:702,body:''};
  const draft = {...PR,number:703,draft:true};
  const listPR = async () => {},listRuns=async () => {},
    listJobs=async () => {},listReviews=async()=>{};
  const calls=[], merges=[], logs=[];
  const github={
    rest: {
      pulls:{
        list:listPR, get:async args=>{calls.push('get:'+args.pull_number);return{data:PR};},
        listReviews, merge:async arg=>{merges.push(arg);return{data:{sha:'merged'}};},
      },
      actions:{listWorkflowRunsForRepo:listRuns,listJobsForWorkflowRun:listJobs},
      repos:{
        getBranch:async()=>({data:{commit:{sha:'main-sha'}}}),
        compareCommits:async()=>({data:{status:mainFresh?'ahead':'behind'}}),
      },
    },
    paginate:async fn => {
      if(fn===listPR)return [PR,dummy,draft];
      if(fn===listRuns)return merger.REQUIRED_WORKFLOWS.map((name,i)=>({
        id:500+i,name,head_sha:sha,event:'pull_request',status:complete?'completed':'in_progress',
        conclusion:complete?'success':null,run_attempt:1,
      }));
      if(fn===listJobs)return merger.REQUIRED_TEST_JOBS.map((name,i)=>({
        id:i,name,status:'completed',conclusion:'success',
      }));
      if(fn===listReviews)return [];
      throw Error('Unexpected GitHub endpoint');
    },
    graphql:async()=>({repository:{pullRequest:{reviewThreads:{
      nodes:[],pageInfo:{hasNextPage:false},
    }}}}),
  };
  const context={
    repo:{owner:'KAVentures',repo:'openworkgraph'},
    eventName:'schedule',payload:{},
  };
  return {github,context,core:{info:x=>logs.push(x)},calls,merges,logs};
}

test('hourly scheduled reconciliation scans only explicit, non-draft PRs', async()=>{
  const a=setup();
  await merger.run(a);
  assert.deepEqual(a.calls,['get:701','get:701']);
  assert.equal(a.merges.length,1);
  assert.equal(a.merges[0].sha,sha);
  assert.equal(a.merges[0].pull_number,701);
  assert.equal(a.merges[0].merge_method,'squash');
});

test('hourly schedule never merges an unapproved, incomplete, or stale PR',async()=>{
  for(const scenario of [{approved:false},{complete:false},{mainFresh:false}]){
    const a=setup(scenario);
    await merger.run(a);
    assert.equal(a.merges.length,0);
    assert.ok(a.logs.length===0||a.logs.some(t=>t.includes('no merge')));
  }
});
