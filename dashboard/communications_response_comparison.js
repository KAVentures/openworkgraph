/* Local, unsigned aggregate comparison; never an individual-case or causal estimator. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.OWGResponseComparison=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const FORMAT='openworkgraph.communications-response-episodes.v1';
  const OUTPUT='openworkgraph.communications-response-comparison.v1';
  const STATES=['observed_within_deadline','observed_after_deadline','no_reply_deadline_elapsed','no_reply_within_deadline'];
  const MAX_COUNT=1000000000,MIN_SUPPORT=5,MAX_BYTES=8*1024*1024;
  const fail=(why)=>{throw new Error(why);};
  const object=(x)=>x!==null&&typeof x==='object'&&!Array.isArray(x);
  function count(x){if(!Number.isSafeInteger(x)||x<0||x>MAX_COUNT)fail('Invalid aggregate count');return x;}
  function nonnegative(x){if(typeof x!=='number'||!Number.isFinite(x)||x<0)fail('Invalid aggregate duration');return x;}
  function ratio(a,b){return b?a/b:null;}
  function stamp(x){
    if(typeof x!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(x)||!Number.isFinite(Date.parse(x)))fail('Invalid UTC cutoff');
    return x;
  }
  function deadlineList(x){
    if(!Array.isArray(x)||!x.length||x.length>8||x.some(v=>!Number.isInteger(v)||v<1||v>43200)||new Set(x).size!==x.length)fail('Invalid deadline list');
    return [...x].sort((a,b)=>a-b);
  }
  function summary(s,deadlines){
    if(!object(s)||!Array.isArray(s.deadlines))fail('Missing response summary');
    const n=count(s.episodes),threads=count(s.distinct_threads),replied=count(s.observed_reply_episodes),open=count(s.without_observed_reply);
    const first=count(s.first_observed_episodes),follow=count(s.follow_up_episodes),inbound=count(s.inbound_messages),repeat=count(s.episodes_with_repeat_inbound),additional=count(s.additional_inbound_before_observed_reply_or_cutoff);
    if(threads>n||(!threads)!==(!n)||replied+open!==n||first+follow!==n||inbound!==n+additional||repeat>n||repeat>additional)fail('Inconsistent episode totals');
    if(s.deadlines.length!==deadlines.length)fail('Missing deadline aggregates');
    const byDeadline=new Map();
    for(const d of s.deadlines){
      if(!object(d)||!deadlines.includes(d.deadline_minutes)||byDeadline.has(d.deadline_minutes)||!object(d.counts)||Object.keys(d.counts).length!==4)fail('Invalid deadline aggregate');
      const [met,late,elapsed,pending]=STATES.map(k=>count(d.counts[k]));
      if(met+late!==replied||elapsed+pending!==open)fail('Deadline counts disagree with episode totals');
      byDeadline.set(d.deadline_minutes,{met,late,elapsed,pending});
    }
    let prior={met:0,pending:0};
    for(const minutes of deadlines){const row=byDeadline.get(minutes);if(row.met<prior.met||row.pending<prior.pending)fail('Non-monotone deadline counts');prior=row;}
    const duration=(v,expected)=>{
      if(!object(v)||count(v.count)!==expected)fail('Inconsistent duration support');
      if(!expected){if(v.median!==null||v.p90_nearest_rank!==null)fail('Empty duration must be unknown');return {median:null,p90:null,count:0};}
      // A nearest-rank p90 is at least the median for positive support.
      const median=nonnegative(v.median),p90=nonnegative(v.p90_nearest_rank);
      if(p90<median)fail('Inconsistent duration quantiles');
      return {median,p90,count:expected};
    };
    return {n,threads,replied,open,first,follow,inbound,repeat,additional,byDeadline,
      replyDuration:duration(s.observed_reply_seconds,replied),openAge:duration(s.open_age_seconds,open)};
  }
  function groups(g,deadlines,overall,kind){
    if(!object(g)||!Array.isArray(g.rows)||g.rows.length>100||g.minimum_distinct_threads!==MIN_SUPPORT)fail('Invalid group report');
    const result=new Map();let total=0;
    const additive=['n','replied','open','first','follow','inbound','repeat','additional'];
    const totals=Object.fromEntries(additive.map(k=>[k,0]));
    const deadlineTotals=new Map(deadlines.map(d=>[d,{met:0,late:0,elapsed:0,pending:0}]));
    for(const row of g.rows){
      if(!object(row))fail('Invalid group');
      let key,label;
      if(kind==='intent'){
        if(row.group_kind==='mixed_first_timestamp'&&row.label===null){key='mixed';label=null;}
        else if(row.group_kind==='first_inbound_label'&&typeof row.label==='string'&&row.label.length>0&&row.label.length<=200&&!/[\u0000-\u001f\u007f]/.test(row.label)){key='intent:'+row.label;label=row.label;}
        else fail('Invalid intent group label');
      }else{
        if(row.group_kind!=='episode_kind'||!['first_observed','follow_up'].includes(row.label))fail('Invalid episode-kind group');
        key=row.label;label=row.label;
      }
      if(result.has(key))fail('Duplicate group');
      const s=summary(row,deadlines);
      if(s.threads<MIN_SUPPORT||s.threads>overall.threads||s.n>overall.n)fail('Invalid group support');
      if(kind==='kind'&&((key==='first_observed'&&s.follow!==0)||(key==='follow_up'&&s.first!==0)))fail('Episode-kind group contradicts its label');
      for(const field of additive){totals[field]+=s[field];if(totals[field]>overall[field])fail('Visible group counts exceed overall counts');}
      for(const d of deadlines){const row=deadlineTotals.get(d),parent=overall.byDeadline.get(d),child=s.byDeadline.get(d);
        for(const field of ['met','late','elapsed','pending']){row[field]+=child[field];if(row[field]>parent[field])fail('Visible group deadline counts exceed overall counts');}}
      total+=s.n;result.set(key,{label,s});
    }
    const suppressed=count(g.episodes_in_suppressed_or_limited_groups);
    const omitted=count(g.suppressed_or_limited_groups);
    if(total+suppressed!==overall.n||(suppressed===0)!==(omitted===0))fail('Inconsistent group partition');
    return {result,omitted,suppressed};
  }
  function normalize(value){
    if(!object(value)||value.format!==FORMAT)fail('Choose an OWG response-episode aggregate v1 JSON file');
    for(const flag of ['thread_identifiers_returned','message_text_returned','export_completeness_verified','actual_response_or_resolution_verified','production_sla_verified','execution_authorized']){
      if(value[flag]!==false)fail('Unsupported evidence claim in aggregate');
    }
    const cutoff=stamp(value.observation_cutoff),deadlines=deadlineList(value.deadline_minutes),overall=summary(value.overall,deadlines);
    const c=value.coverage;
    if(!object(c))fail('Missing coverage');
    const coverage={threaded:count(c.threaded_population),eligible:count(c.eligible_threads),excluded:count(c.excluded_threads),unthreaded_messages:count(c.unthreaded_messages),eligible_messages:count(c.eligible_messages)};
    if(coverage.eligible+coverage.excluded!==coverage.threaded||coverage.eligible!==overall.threads||coverage.eligible_messages<overall.inbound)fail('Inconsistent coverage');
    const intent=groups(value.by_first_inbound_intent,deadlines,overall,'intent'),kind=groups(value.by_episode_kind,deadlines,overall,'kind');
    const w=value.workload;
    if(!object(w)||typeof w.available!=='boolean'||w.available!==Boolean(overall.n))fail('Inconsistent workload availability');
    let workload=null;
    if(w.available){
      const o=w.overall;
      if(!object(o))fail('Missing workload');
      const span=nonnegative(w.observation_span_seconds),exposure=nonnegative(o.episode_wait_seconds),peak=count(o.peak_observed_backlog);
      if(count(o.opening_observed_backlog)!==0||count(o.new_episodes)!==overall.n||count(o.observed_replies)!==overall.replied||count(o.closing_observed_backlog)!==overall.open||peak<overall.open||peak>overall.n||exposure>peak*span+0.000001*Math.max(1,peak))fail('Inconsistent workload totals');
      workload={span_seconds:span,episode_wait_seconds:exposure,peak_observed_backlog:peak,time_weighted_observed_backlog:ratio(exposure,span)};
    }
    return {cutoff,deadlines,overall,coverage,intent,kind,workload};
  }
  function side(s,d){
    const row=s.byDeadline.get(d);
    return {episodes:s.n,distinct_threads:s.threads,met:row.met,late:row.late,elapsed:row.elapsed,pending:row.pending,
      lower_share:ratio(row.met,s.n),upper_share:ratio(row.met+row.pending,s.n)};
  }
  function compareDeadline(a,b,d){
    const baseline=side(a,d),comparison=side(b,d);
    // Exact cross-products determine the sign, without floating-point ties.
    const lo=BigInt(comparison.met)*BigInt(a.n)-BigInt(baseline.met+baseline.pending)*BigInt(b.n);
    const hi=BigInt(comparison.met+comparison.pending)*BigInt(a.n)-BigInt(baseline.met)*BigInt(b.n);
    const denom=BigInt(a.n)*BigInt(b.n);
    return {deadline_minutes:d,baseline,comparison,
      conditional_change_percentage_points:{lower:100*Number(lo)/Number(denom),upper:100*Number(hi)/Number(denom)},
      observed_within_deadline_change_percentage_points:100*(comparison.lower_share-baseline.lower_share),
      direction:lo>0n?'higher_under_all_pending_outcomes':hi<0n?'lower_under_all_pending_outcomes':'overlapping_or_touching_bounds'};
  }
  function measures(s,w){
    return {episodes:s.n,distinct_threads:s.threads,observed_replies:s.replied,open_episodes:s.open,
      repeat_contact_share:ratio(s.repeat,s.n),follow_up_share:ratio(s.follow,s.n),inbound_messages:s.inbound,
      observed_reply_seconds:s.replyDuration,open_age_seconds:s.openAge,workload:w};
  }
  function grouped(a,b,deadlines){
    const rows=[];
    for(const [key,v] of a.result){
      const other=b.result.get(key);
      if(other)rows.push({label:v.label,baseline:measures(v.s,null),comparison:measures(other.s,null),deadlines:deadlines.map(d=>compareDeadline(v.s,other.s,d))});
    }
    rows.sort((a,b)=>String(a.label)<String(b.label)?-1:String(a.label)>String(b.label)?1:0);
    return {rows,baseline_visible_only:a.result.size-rows.length,comparison_visible_only:b.result.size-rows.length,
      baseline_withheld_groups:a.omitted,comparison_withheld_groups:b.omitted};
  }
  function compare(baseline,comparison,options){
    if(!object(options)||options.confirmComparable!==true)fail('Confirm the reports use comparable selection, classification and direction conventions');
    for(const x of [options.baselineSha256,options.comparisonSha256])if(typeof x!=='string'||!/^[a-f0-9]{64}$/.test(x))fail('Exact input SHA-256 digests required');
    if(options.baselineSha256===options.comparisonSha256)fail('Choose two different report files');
    const a=normalize(baseline),b=normalize(comparison);
    const common=a.deadlines.filter(d=>b.deadlines.includes(d));
    if(!common.length)fail('The two reports have no identical elapsed-time deadline');
    if(a.overall.threads<MIN_SUPPORT||b.overall.threads<MIN_SUPPORT)fail('Both reports need at least five eligible distinct threads');
    const measureA=measures(a.overall,a.workload),measureB=measures(b.overall,b.workload);
    return {format:OUTPUT,baseline:{input_sha256:options.baselineSha256,cutoff:a.cutoff,coverage:a.coverage,measures:measureA},
      comparison:{input_sha256:options.comparisonSha256,cutoff:b.cutoff,coverage:b.coverage,measures:measureB},
      comparable_conventions_declared:true,common_deadline_minutes:common,baseline_only_deadlines:a.deadlines.filter(d=>!common.includes(d)),comparison_only_deadlines:b.deadlines.filter(d=>!common.includes(d)),
      deadlines:common.map(d=>compareDeadline(a.overall,b.overall,d)),by_intent:grouped(a.intent,b.intent,common),by_episode_kind:grouped(a.kind,b.kind,common),
      distinct_populations_verified:false,case_matching_performed:false,source_authenticity_verified:false,causal_effect_verified:false,production_sla_verified:false,
      interpretation:'Unsigned, descriptive comparison of selected exports only. Same labels need not mean identical cases. Bounds vary only the future on-time status of pending episodes; they are not confidence intervals and do not account for missing history. Workload measures are elapsed observed exposure, not staffing effort. No claim of source authenticity, disjoint populations, actual resolution or causal effect.'};
  }
  return {compare,normalize,MAX_BYTES};
});
