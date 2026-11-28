import { createRequire } from 'node:module';
import { test } from 'node:test';
const require=createRequire(import.meta.url);
const api=require('../../dashboard/communications_response_comparison.js');
function runTests(api){
  const check=(v,m='assertion failed')=>{if(!v)throw new Error(m);};
  const reject=(fn)=>{let threw=false;try{fn();}catch(e){threw=true;}check(threw,'expected rejection');};
  const eq=(x,y)=>check(JSON.stringify(x)===JSON.stringify(y),JSON.stringify([x,y]));
  const copy=x=>JSON.parse(JSON.stringify(x));
  const options={confirmComparable:true,baselineSha256:'a'.repeat(64),comparisonSha256:'b'.repeat(64)};
  function fixture(met,late,elapsed,pending,minutes=60){
    const n=met+late+elapsed+pending,replied=met+late,open=elapsed+pending;
    const d=count=>({count,median:count?60:null,p90_nearest_rank:count?120:null});
    const s={episodes:n,distinct_threads:n,observed_reply_episodes:replied,without_observed_reply:open,first_observed_episodes:n,follow_up_episodes:0,inbound_messages:n,episodes_with_repeat_inbound:0,additional_inbound_before_observed_reply_or_cutoff:0,observed_reply_seconds:d(replied),open_age_seconds:d(open),deadlines:[{deadline_minutes:minutes,counts:{observed_within_deadline:met,observed_after_deadline:late,no_reply_deadline_elapsed:elapsed,no_reply_within_deadline:pending}}]};
    const g=(kind,label)=>({minimum_distinct_threads:5,suppressed_or_limited_groups:n>=5?0:1,episodes_in_suppressed_or_limited_groups:n>=5?0:n,rows:n>=5?[{group_kind:kind,label,...s}]:[]});
    return {format:'openworkgraph.communications-response-episodes.v1',observation_cutoff:'2026-01-01T12:00:00Z',deadline_minutes:[minutes],overall:s,
      coverage:{threaded_population:n,eligible_threads:n,excluded_threads:0,unthreaded_messages:0,eligible_messages:n+replied},
      workload:{available:true,observation_span_seconds:120,overall:{opening_observed_backlog:0,new_episodes:n,observed_replies:replied,closing_observed_backlog:open,peak_observed_backlog:n,episode_wait_seconds:n*60}},
      by_first_inbound_intent:g('first_inbound_label','support'),by_episode_kind:g('episode_kind','first_observed'),
      thread_identifiers_returned:false,message_text_returned:false,export_completeness_verified:false,actual_response_or_resolution_verified:false,production_sla_verified:false,execution_authorized:false};
  }
  const a=fixture(2,3,2,3),b=fixture(8,1,1,0),r=api.compare(a,b,options);
  eq(r.deadlines[0].conditional_change_percentage_points,{lower:30,upper:60});
  eq(r.deadlines[0].direction,'higher_under_all_pending_outcomes');
  eq(r.by_intent.rows.length,1);eq(r.by_episode_kind.rows.length,1);
  eq(r.baseline.measures.workload.time_weighted_observed_backlog,5);
  check(!r.causal_effect_verified&&!r.case_matching_performed&&!r.production_sla_verified);
  const x=api.compare(b,a,options);eq(x.deadlines[0].conditional_change_percentage_points,{lower:-60,upper:-30});
  eq(x.deadlines[0].direction,'lower_under_all_pending_outcomes');
  eq(api.compare(a,a,options).deadlines[0].direction,'overlapping_or_touching_bounds');
  reject(()=>api.compare(a,b,{...options,confirmComparable:false}));
  reject(()=>api.compare(a,b,{...options,comparisonSha256:options.baselineSha256}));
  reject(()=>api.compare(a,fixture(4,0,1,0,120),options));
  reject(()=>api.compare(a,fixture(1,1,1,1),options));
  for(const mutate of [
    v=>v.overall.episodes++,v=>v.overall.deadlines[0].counts.observed_after_deadline++,
    v=>v.coverage.eligible_threads++,v=>v.by_first_inbound_intent.rows.push(copy(v.by_first_inbound_intent.rows[0])),
    v=>v.by_first_inbound_intent.rows[0].distinct_threads=4,v=>v.workload.overall.episode_wait_seconds=100000,
    v=>v.message_text_returned=true,v=>v.overall.observed_reply_seconds.median=-1,
    v=>v.by_episode_kind.episodes_in_suppressed_or_limited_groups=1,
    v=>v.by_first_inbound_intent.rows[0].label='bad\nlabel',
    v=>{const d=v.by_first_inbound_intent.rows[0].deadlines[0].counts;d.observed_within_deadline++;d.observed_after_deadline--;},
    v=>{const s=v.by_episode_kind.rows[0];s.first_observed_episodes--;s.follow_up_episodes++;},
    v=>v.deadline_minutes=[60,60]
  ]){const bad=copy(a);mutate(bad);reject(()=>api.compare(bad,b,options));}
  const hidden=copy(b);hidden.by_first_inbound_intent.rows=[];hidden.by_first_inbound_intent.suppressed_or_limited_groups=1;hidden.by_first_inbound_intent.episodes_in_suppressed_or_limited_groups=10;
  eq(api.compare(a,hidden,options).by_intent.rows,[]);
  const unmatched=copy(b);unmatched.by_first_inbound_intent.rows[0].label='different';
  eq(api.compare(a,unmatched,options).by_intent.baseline_visible_only,1);
  const aBefore=JSON.stringify(a);api.compare(a,b,options);eq(JSON.stringify(a),aBefore);
  let seed=142;const random=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n;};
  for(let i=0;i<500;i++){
    const vals=()=>[random(12)+5,random(12),random(12),random(12)];
    const p=vals(),q=vals(),u=api.compare(fixture(...p),fixture(...q),options).deadlines[0];
    const n=p.reduce((a,b)=>a+b),m=q.reduce((a,b)=>a+b),oracle=[];
    for(let j=0;j<=p[3];j++)for(let k=0;k<=q[3];k++)oracle.push(100*((q[0]+k)/m-(p[0]+j)/n));
    check(Math.abs(Math.min(...oracle)-u.conditional_change_percentage_points.lower)<1e-10);
    check(Math.abs(Math.max(...oracle)-u.conditional_change_percentage_points.upper)<1e-10);
    check(u.direction===(q[0]*n>(p[0]+p[3])*m?'higher_under_all_pending_outcomes':(q[0]+q[3])*n<p[0]*m?'lower_under_all_pending_outcomes':'overlapping_or_touching_bounds'));
  }
  return '500 exhaustive pending-outcome oracle comparisons plus deterministic validation and suppression regressions passed';
}
test('response report comparison and independent bounds oracle',()=>runTests(api));
