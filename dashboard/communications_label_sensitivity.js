/* Exact finite annotation-error sensitivity over paired correctness states.
   Predictions and abstentions are fixed. Each binary intent label can flip once.
   No threshold fitting, new model, sampling CI, or per-message evidence. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.OWGLabelSensitivity=api;})(globalThis,function(){
'use strict';
const FORMAT='openworkgraph.communications-label-sensitivity.v1';
const STATES=['correct','incorrect','abstained'],PIN=/^[a-f0-9]{64}$/;
const integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const flip=s=>s==='correct'?'incorrect':s==='incorrect'?'correct':'abstained';
const delta=(k,l)=>Number(l==='correct')-Number(k==='correct');
const fail=m=>{throw Error(m);};
function population(pair,n){
  if(!pair||pair.examples!==n||!Array.isArray(pair.transitions)||pair.transitions.length!==9)fail('Incomplete paired transition population.');
  const seen=new Set(), effects={'-2':0,'-1':0,'0':0,'1':0,'2':0};let total=0,net=0,learned=0,keyword=0;
  let lc=0,kc=0,ld=0,kd=0;
  for(const t of pair.transitions){
    if(!t||!STATES.includes(t.keyword)||!STATES.includes(t.learned)||!integer(t.count,0,n))fail('Invalid paired transition.');
    const key=t.keyword+'/'+t.learned;if(seen.has(key))fail('Duplicate paired transition.');seen.add(key);
    const d=delta(t.keyword,t.learned);
    effects[delta(flip(t.keyword),flip(t.learned))-d]+=t.count;
    net+=d*t.count;total+=t.count;if(d>0)learned+=t.count;if(d<0)keyword+=t.count;
    if(t.learned==='correct')lc+=t.count;if(t.keyword==='correct')kc+=t.count;
    if(t.learned!=='abstained')ld+=t.count;if(t.keyword!=='abstained')kd+=t.count;
  }
  if(total!==n||pair.learned_only_correct!==learned||pair.keyword_only_correct!==keyword)fail('Paired transition accounting failed.');
  return {n,net,effects,learned_correct:lc,keyword_correct:kc,learned_decided:ld,keyword_decided:kd};
}
function spend(effects,budget,sign){
  let change=0,used=0;
  for(const amount of [2,1]){const n=Math.min(budget-used,effects[sign*amount]);used+=n;change+=sign*amount*n;}
  return {change,flips_used:used};
}
function minimum(effects,need,sign){
  if(need<=0)return 0;
  const two=Math.min(effects[sign*2],Math.ceil(need/2));
  const one=Math.max(0,need-2*two);
  return one<=effects[sign]?two+one:null;
}
function bounds(p,bps){
  if(!integer(bps,0,10000))fail('Enter a whole-number error budget from 0 to 10000 basis points.');
  const budget=Math.floor(p.n*bps/10000),lo=spend(p.effects,budget,-1),hi=spend(p.effects,budget,1);
  const lower=p.net+lo.change,upper=p.net+hi.change;
  return {label_decisions:p.n,observed_net_correct:p.net,observed_difference:p.n?p.net/p.n:null,
    flip_effect_counts:{...p.effects},max_label_flips:budget,
    lower_net_correct:lower,upper_net_correct:upper,lower_difference:p.n?lower/p.n:null,upper_difference:p.n?upper/p.n:null,
    lower_witness_flips:lo.flips_used,upper_witness_flips:hi.flips_used,
    minimum_flips_to_erase_observed_lead:p.net===0?0:minimum(p.effects,Math.abs(p.net),p.net>0?-1:1),
    minimum_flips_to_reverse_observed_lead:p.net===0?null:minimum(p.effects,Math.abs(p.net)+1,p.net>0?-1:1),
    strict_lead_under_budget:p.n===0?'no_data':lower>0?'learned':upper<0?'keyword':'not_robust'};
}
function verifyMethod(method,n,correct,decided){
  if(!method||method.examples!==n)fail('Invalid method population.');
  const k=['tp','fp','tn','fn','abstained_positive','abstained_negative'];
  if(k.some(x=>!integer(method[x],0,n))||k.reduce((a,x)=>a+method[x],0)!==n||
     method.tp+method.tn!==correct||method.tp+method.fp+method.tn+method.fn!==decided)fail('Method counts disagree with paired states.');
}
function review(data,subset,language,bps){
  if(!data||data.format!=='openworkgraph.communications-paired-evaluation.v1'||data.local_only!==true||
     data.execution_authorized!==false||data.message_text_or_example_ids_returned!==false||
     ![data.model_sha256,data.training_basis_sha256,data.taxonomy_sha256].every(x=>typeof x==='string'&&PIN.test(x))||
     !['all_holdout','without_detected_lexical_overlap'].includes(subset)||typeof language!=='string'||
     !Array.isArray(data.by_language)||data.by_language.length>30)fail('Use a current aggregate paired evaluation report.');
  if(!integer(bps,0,10000))fail('Enter a whole-number error budget from 0 to 10000 basis points.');
  if(subset==='without_detected_lexical_overlap'&&data.lexical_overlap?.complete!==true)fail('Overlap-screened population is unavailable.');
  let selected=data;
  if(language){const groups=data.by_language.filter(x=>x.language===language);if(groups.length!==1)fail('Unknown or ambiguous language group.');selected=groups[0];}
  const group=selected[subset];
  if(!group||!integer(group.message_count,0,2000)||!integer(group.declared_intents,1,40)||
     !Array.isArray(group.per_intent)||group.per_intent.length!==group.declared_intents)fail('Invalid selected population.');
  const n=group.message_count,names=new Set(),pooled={n:0,net:0,effects:{'-2':0,'-1':0,'0':0,'1':0,'2':0}};
  const intents=[];
  for(const row of group.per_intent){
    if(!row||typeof row.intent!=='string'||!row.intent||names.has(row.intent))fail('Invalid intent population.');
    names.add(row.intent);const p=population(row.paired,n);
    verifyMethod(row.learned,n,p.learned_correct,p.learned_decided);verifyMethod(row.keyword,n,p.keyword_correct,p.keyword_decided);
    if(row.learned.tp+row.learned.fn+row.learned.abstained_positive!==row.keyword.tp+row.keyword.fn+row.keyword.abstained_positive)fail('Methods do not share the same reviewer-label prevalence.');
    pooled.n+=p.n;pooled.net+=p.net;for(const k of Object.keys(pooled.effects))pooled.effects[k]+=p.effects[k];
    intents.push({intent:row.intent,...bounds(p,bps)});
  }
  return {format:FORMAT,model_sha256:data.model_sha256,training_basis_sha256:data.training_basis_sha256,taxonomy_sha256:data.taxonomy_sha256,
    subset,language:language||null,reviewed_messages:n,declared_intents:names.size,error_budget_basis_points:bps,
    per_intent:intents,pooled_intent_label_cells:bounds(pooled,bps),
    source_currently_reverified:false,predictions_changed:false,thresholds_selected:false,execution_authorized:false,
    interpretation:'Exact adversarial bounds conditional on a budget of binary reviewer-label flips, with all predictions and abstentions fixed. The pooled budget is over independent intent-label cells, not distinct messages; one message can contain several labels. These are attainable extrema, not confidence intervals, estimates of annotation error, causal effects, or external validation. Erasing a lead means reaching or crossing zero, not necessarily an exact tie. No message-level correctness sensitivity, model fitting or threshold selection is inferred.'};
}
return {population,bounds,review};
});
