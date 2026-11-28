/* Explicit paired context experiment; no automatic proposal selection. */
export function mountContextTrial(host,{getState,post,lock,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const order=el('select');
  for(const [value,label] of [['original-first','Original first'],['compact-first','Compact first']]){
    const o=el('option',label);o.value=value;order.append(o);
  }
  const confirm=el('input');confirm.type='checkbox';
  const start=button('Run paired context experiment'),save=button('Download complete context trial'),
    saveOriginal=button('Download original trial proposal'),saveCompact=button('Download compact trial proposal');
  const status=el('p','Prepare a compact-eligible packet and configure the local model.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');let result=null,epoch=0;
  const hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
  const metrics=['request_bytes','transmitted_context_bytes','elapsed_ms','prompt_tokens','completion_tokens','total_tokens'];
  const labels=['Request bytes','Evidence bytes','Elapsed milliseconds','Reported prompt tokens','Reported completion tokens','Reported total tokens'];
  const count=v=>v===null||(Number.isSafeInteger(v)&&v>=0);
  const display=v=>v===null?'unknown':String(v);
  function ready(s){
    const p=s.packet,v=s.settings;
    return !!p&&p.max_packet_bytes<=524288&&p.compaction?.available===true&&hex(p.packet_sha256)&&hex(p.compaction.compact_sha256)&&
      /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$/.test(v.model)&&Number.isInteger(v.port)&&v.port>=1&&v.port<=65535&&
      Number.isInteger(v.max_output_tokens)&&v.max_output_tokens>=128&&v.max_output_tokens<=16384&&typeof v.json_mode==='boolean';
  }
  function refresh(){
    const s=getState(),enabled=ready(s)&&!s.busy;
    order.disabled=confirm.disabled=!enabled;start.disabled=!enabled||!confirm.checked;
    save.disabled=s.busy||!result;
    saveOriginal.disabled=s.busy||!result?.proposal_json.original;
    saveCompact.disabled=s.busy||!result?.proposal_json.compact;
  }
  function invalidate(){
    epoch++;result=null;confirm.checked=false;output.replaceChildren();
    status.textContent='Trial inputs changed. Confirm two new requests after preparing evidence and settings.';refresh();
  }
  order.addEventListener('change',invalidate);confirm.addEventListener('change',refresh);
  function validate(r,p,v,selectedOrder){
    if(r.format!=='openworkgraph.ai-implementation-context-trial-handoff.v1'||!hex(r.trial_sha256)||r.execution_authorized!==false||
      r.independent_quality_review_required!==true||typeof r.trial_json!=='string'||new TextEncoder().encode(r.trial_json).length>4*1024*1024||
      !r.proposal_json||Object.keys(r.proposal_json).sort().join(',')!=='compact,original')throw Error('Unexpected context trial response.');
    const q=JSON.parse(r.trial_json),s=q.settings;
    if(q.format!=='openworkgraph.ai-implementation-context-trial.v1'||q.trial_sha256!==r.trial_sha256||q.packet_sha256!==p.packet_sha256||
      q.compact_sha256!==p.compaction.compact_sha256||q.requested_model!==v.model||q.planned_attempt_count!==2||
      q.independent_quality_review_required!==true||['model_quality_equivalence_verified','token_savings_verified','actual_cost_verified',
        'model_identity_independently_verified','inference_location_verified','business_outcomes_verified','execution_authorized'].some(k=>q[k]!==false)||
      !s||s.order!==selectedOrder||s.port!==v.port||s.max_context_bytes!==p.max_packet_bytes||s.max_output_tokens!==v.max_output_tokens||
      s.json_mode!==v.json_mode||s.timeout_seconds!==120||q.planned_evidence_bytes_saved!==p.compaction.bytes_saved||
      !Array.isArray(q.attempts)||q.attempts.length!==2)throw Error('Trial does not match the confirmed evidence and settings.');
    const arms=selectedOrder==='original-first'?['original','compact']:['compact','original'];
    let validated=0;
    for(let i=0;i<2;i++){
      const a=q.attempts[i],arm=arms[i],raw=r.proposal_json[arm];
      if(!a||a.representation!==arm||!count(a.elapsed_ms)||a.elapsed_ms===null)throw Error('Unexpected trial attempt.');
      if(a.status==='proposal_not_validated'){
        if(a.receipt!==null||a.proposal!==null||raw!==null||typeof a.error_class!=='string')throw Error('Unexpected failed attempt.');
        continue;
      }
      const receipt=a.receipt;
      if(a.status!=='structurally_validated_proposal'||a.error_class!==null||!receipt||typeof raw!=='string'||
        receipt.packet_sha256!==p.packet_sha256||receipt.requested_model!==v.model||receipt.context_representation!==arm||
        receipt.compact_sha256!==(arm==='compact'?p.compaction.compact_sha256:null)||receipt.execution_authorized!==false||
        receipt.transmitted_context_bytes!==(arm==='compact'?p.compaction.compact_bytes:p.packet_bytes)||!hex(receipt.proposal_sha256)||
        !receipt.usage||metrics.slice(0,3).some(k=>!count(receipt[k])||receipt[k]===null)||metrics.slice(3).some(k=>!count(receipt.usage[k])))throw Error('Unexpected validated attempt.');
      const proposal=JSON.parse(raw);
      if(proposal.packet_sha256!==p.packet_sha256||proposal.author_model!==v.model||JSON.stringify(proposal)!==JSON.stringify(a.proposal))throw Error('Trial proposal mismatch.');
      validated++;
    }
    if(q.structurally_validated_count!==validated||q.both_receipts_available!==(validated===2)||!q.original_minus_compact)throw Error('Trial attempt totals disagree.');
    const receipts=Object.fromEntries(q.attempts.map(a=>[a.representation,a.receipt]));
    for(const k of metrics){
      const value=r=>r===null?null:metrics.slice(3).includes(k)?r.usage[k]:r[k];
      const a=value(receipts.original),b=value(receipts.compact),expected=a===null||b===null?null:a-b;
      if(q.original_minus_compact[k]!==expected)throw Error('Trial delta disagrees with reported measurements.');
    }
    return q;
  }
  start.onclick=async()=>{
    const state=getState();if(start.disabled||state.busy||!ready(state)||!confirm.checked)return;
    const p=state.packet,v={...state.settings},selectedOrder=order.value,files=state.files.slice(),ticket=++epoch;
    result=null;output.replaceChildren();confirm.checked=false;lock(true);
    status.textContent='Running two explicitly authorized requests. No retry or cloud fallback.';
    try{
      const r=await post('/v1/ai-opportunity-handoff/context-trial',{
        sources:await Promise.all(files.map(f=>f.text())),expected_packet_sha256:p.packet_sha256,
        expected_compact_sha256:p.compaction.compact_sha256,max_bytes:p.max_packet_bytes,...v,order:selectedOrder,confirm_two_requests:true});
      if(ticket!==epoch)return;
      const q=validate(r,p,v,selectedOrder);
      for(const a of q.attempts){
        const section=el('section');section.append(el('h4',a.representation+' — '+a.status.replaceAll('_',' ')));
        if(a.receipt){const list=el('ul');metrics.forEach((k,i)=>list.append(el('li',labels[i]+': '+display(i<3?a.receipt[k]:a.receipt.usage[k]))));section.append(list);}
        else section.append(el('p','No validated proposal or usage receipt. Costs may still have been incurred. '+a.error_class+'; attempt elapsed '+a.elapsed_ms+' ms.'));
        output.append(section);
      }
      const delta=el('ul');metrics.forEach((k,i)=>delta.append(el('li',labels[i]+': '+display(q.original_minus_compact[k]))));
      output.append(el('h4','Original minus compact (one pair only)'),delta,el('p','Independent quality review required. Unknown is not zero. No verified savings, quality equivalence or rollout decision.'));
      result=r;status.textContent='Paired context experiment complete: '+q.structurally_validated_count+' of 2 structurally validated proposals. Trial SHA-256: '+r.trial_sha256+'.';
    }catch(e){if(ticket===epoch){output.replaceChildren();status.textContent=e.message+' Requests may already have incurred costs; do not retry automatically.';}}
    finally{lock(false);refresh();}
  };
  save.onclick=()=>{if(!getState().busy&&result)download('owg-context-trial.json','application/json',result.trial_json);};
  saveOriginal.onclick=()=>{if(!getState().busy&&result?.proposal_json.original)download('owg-trial-original-proposal.json','application/json',result.proposal_json.original);};
  saveCompact.onclick=()=>{if(!getState().busy&&result?.proposal_json.compact)download('owg-trial-compact-proposal.json','application/json',result.proposal_json.compact);};
  const details=el('details');details.id='owgContextTrial';
  details.append(el('summary','Compare original and compact model context'),
    el('p','Uses the selected packet and local model settings. Two sequential requests to the configured loopback service may incur costs. Order, cache and backend effects are uncontrolled. This report is retained in browser memory only; download before changing inputs.'),
    field('Paired experiment order',order),field('I explicitly authorize both local model requests for this experiment',confirm),start,status,output,save,saveOriginal,saveCompact);
  host.append(details);refresh();return {refresh,invalidate};
}
