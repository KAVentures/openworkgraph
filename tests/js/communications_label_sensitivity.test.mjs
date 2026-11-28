import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import test from 'node:test';
const require=createRequire(import.meta.url);
const api=require('../../dashboard/communications_label_sensitivity.js');

const states=['correct','incorrect','abstained'];
const pin='a'.repeat(64);
function makeRow(name,examples){
  const counts=()=>({examples:examples.length,tp:0,fp:0,tn:0,fn:0,abstained_positive:0,abstained_negative:0});
  const keyword=counts(),learned=counts(),transitions=states.flatMap(k=>states.map(l=>({keyword:k,learned:l,count:0})));
  const state=(p,y)=>p===null?'abstained':p===y?'correct':'incorrect';
  function add(m,p,y){m[p===null?(y?'abstained_positive':'abstained_negative'):p?(y?'tp':'fp'):(y?'fn':'tn')]++;}
  for(const [k,l,y] of examples){
    add(keyword,k,y);add(learned,l,y);
    transitions.find(t=>t.keyword===state(k,y)&&t.learned===state(l,y)).count++;
  }
  return {intent:name,keyword,learned,paired:{examples:examples.length,transitions,
    learned_only_correct:transitions.filter(t=>t.learned==='correct'&&t.keyword!=='correct').reduce((s,t)=>s+t.count,0),
    keyword_only_correct:transitions.filter(t=>t.keyword==='correct'&&t.learned!=='correct').reduce((s,t)=>s+t.count,0)}};
}
function makeReport(groups){
  const summary={message_count:groups[0][1].length,declared_intents:groups.length,per_intent:groups.map(([name,examples])=>makeRow(name,examples))};
  return {format:'openworkgraph.communications-paired-evaluation.v1',local_only:true,execution_authorized:false,message_text_or_example_ids_returned:false,
    model_sha256:pin,training_basis_sha256:pin,taxonomy_sha256:pin,lexical_overlap:{complete:true},all_holdout:summary,
    without_detected_lexical_overlap:summary,by_language:[{language:'sv',all_holdout:summary,without_detected_lexical_overlap:summary}]};
}
function brute(examples,budget){
  const value=(k,l,y)=>Number(l===y)-Number(k===y);
  const original=examples.reduce((s,[k,l,y])=>s+value(k,l,y),0);
  let lower=Infinity,upper=-Infinity,erase=null,reverse=null;
  for(let mask=0;mask<2**examples.length;mask++){
    let flips=0,net=0;
    for(let i=0;i<examples.length;i++){
      const [k,l,y]=examples[i],bit=(mask>>i)&1;
      flips+=bit;net+=value(k,l,bit?1-y:y);
    }
    if(flips<=budget){lower=Math.min(lower,net);upper=Math.max(upper,net);}
    if((original>0&&net<=0)||(original<0&&net>=0)||original===0)erase=erase===null?flips:Math.min(erase,flips);
    if((original>0&&net<0)||(original<0&&net>0))reverse=reverse===null?flips:Math.min(reverse,flips);
  }
  return {original,lower,upper,erase,reverse};
}
function verify(row,examples,bps,assert){
  const budget=Math.floor(examples.length*bps/10000),truth=brute(examples,budget);
  assert.equal(row.max_label_flips,budget);
  assert.equal(row.observed_net_correct,truth.original);
  assert.equal(row.lower_net_correct,truth.lower);
  assert.equal(row.upper_net_correct,truth.upper);
  assert.equal(row.minimum_flips_to_erase_observed_lead,truth.erase);
  assert.equal(row.minimum_flips_to_reverse_observed_lead,truth.reverse);
  assert.equal(row.strict_lead_under_budget,examples.length===0?'no_data':truth.lower>0?'learned':truth.upper<0?'keyword':'not_robust');
}
function exercise(api,assert){
  let seed=34172;
  const rnd=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return Math.floor(seed/4294967296*n);};
  const prediction=()=>[0,1,null][rnd(3)];
  let cases=0;
  for(let i=0;i<500;i++){
    const n=rnd(7),examples=()=>Array.from({length:n},()=>[prediction(),prediction(),rnd(2)]);
    const a=examples(),b=examples(),report=makeReport([['Refund',a],['Access',b]]);
    const bps=[0,1,1000,2500,5000,7500,10000,rnd(10001)][rnd(8)];
    const original=JSON.stringify(report);
    const out=api.review(report,i%2?'all_holdout':'without_detected_lexical_overlap',i%3?'':'sv',bps);
    verify(out.per_intent[0],a,bps,assert);verify(out.per_intent[1],b,bps,assert);
    verify(out.pooled_intent_label_cells,[...a,...b],bps,assert);
    assert.equal(JSON.stringify(report),original);cases+=3;
  }
  const exact=makeReport([['Refund',[[0,1,1],[0,1,1],[null,1,1],[1,null,0]]]]);
  const r=api.review(exact,'all_holdout','',2500);
  assert.equal(r.per_intent[0].observed_net_correct,3);
  assert.equal(r.per_intent[0].lower_net_correct,1);
  assert.equal(r.per_intent[0].minimum_flips_to_erase_observed_lead,2);
  assert.equal(r.per_intent[0].minimum_flips_to_reverse_observed_lead,2);
  const clone=()=>JSON.parse(JSON.stringify(exact));
  for(const mutate of [x=>x.execution_authorized=true,x=>x.model_sha256='bad',
    x=>x.all_holdout.per_intent[0].paired.transitions[0].count++,
    x=>x.all_holdout.per_intent[0].paired.transitions[1]=x.all_holdout.per_intent[0].paired.transitions[0],
    x=>x.all_holdout.per_intent[0].learned.tp++,
    x=>x.all_holdout.per_intent[0].paired.learned_only_correct=0]){
    const x=clone();mutate(x);assert.throws(()=>api.review(x,'all_holdout','',0));
  }
  for(const bps of [true,-1,10001,1.5,'100'])assert.throws(()=>api.review(exact,'all_holdout','',bps));
  const no=clone();no.lexical_overlap.complete=false;assert.throws(()=>api.review(no,'without_detected_lexical_overlap','',0));
  assert.throws(()=>api.review(exact,'all_holdout','fr',0));
  return cases;
}

test('exact flip extrema match exhaustive label assignment oracle',()=>assert.equal(exercise(api,assert),1500));
