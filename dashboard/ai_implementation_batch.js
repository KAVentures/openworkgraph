/* Explicit batch review and individual proposal selection; no portfolio writes. */
export function mountImplementationBatch(host,{getState,post,lock,download,onSelected,onClear}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.append(node);return n;};
  const file=el('input');file.type='file';file.accept='.json,application/json';
  const review=button('Review complete proposal batch'),save=button('Download complete batch review');
  const status=el('p','Prepare a shared evidence packet and select the returned proposal array.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');let basis=null,choices=[],epoch=0,planningPanel=null;
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  const LIMIT=2*1024*1024;
  function refresh(){
    const s=getState();file.disabled=s.busy;review.disabled=s.busy||!s.packet||!file.files?.[0];save.disabled=s.busy||!basis;
    for(const n of choices)n.disabled=s.busy||!basis;
    planningPanel?.refresh();
  }
  function invalidate(){
    epoch++;basis=null;choices=[];output.replaceChildren();onClear();planningPanel?.invalidate();
    status.textContent='Inputs changed. Review the complete batch again.';refresh();
  }
  file.addEventListener('change',invalidate);
  function validate(r,p,items){
    if(r.format!=='openworkgraph.ai-implementation-batch-preview.v1'||!hex(r.batch_review_sha256)||r.execution_authorized!==false||r.portfolio_written!==false||typeof r.report_json!=='string')throw Error('Unexpected batch response.');
    const q=JSON.parse(r.report_json),statuses=['structurally_reviewed','structural_review_refused','exact_duplicate'];
    if(q.format!=='openworkgraph.ai-implementation-batch-review.v1'||q.packet_sha256!==p.packet_sha256||q.batch_review_sha256!==r.batch_review_sha256||!hex(q.batch_sha256)||
      q.human_review_required!==true||['semantic_correctness_verified','source_authenticity_verified','representative_coverage_verified','opportunity_ranking_performed','portfolio_written','execution_authorized'].some(k=>q[k]!==false)||
      !Array.isArray(q.items)||q.items.length!==items.length||q.item_count!==items.length||!q.counts||!Array.isArray(q.coverage_gaps)||!Array.isArray(q.uncited_run_refs)||
      q.selected_event_count!==p.event_count||!Number.isSafeInteger(q.distinct_cited_event_count)||q.distinct_cited_event_count<0||q.distinct_cited_event_count>q.selected_event_count)throw Error('Unexpected batch population.');
    const seen=new Map();
    for(let i=0;i<q.items.length;i++){
      const a=q.items[i];if(a.item_index!==i||!hex(a.proposal_sha256)||!statuses.includes(a.status))throw Error('Unexpected batch item.');
      if(a.status==='exact_duplicate'){
        if(seen.get(a.proposal_sha256)!==a.duplicate_of_index||!seen.has(a.proposal_sha256)||a.review!==null||a.review_sha256!==null)throw Error('Unexpected duplicate.');
      }else{
        if(seen.has(a.proposal_sha256)||a.duplicate_of_index!==null)throw Error('Unexpected distinct item.');seen.set(a.proposal_sha256,i);
        if(a.status==='structurally_reviewed'){
          if(!hex(a.review_sha256)||a.review?.proposal_sha256!==a.proposal_sha256||a.review.packet_sha256!==p.packet_sha256||a.review.status!=='structural_review_only'||a.review.execution_authorized!==false||a.review.human_review_required!==true)throw Error('Unexpected reviewed proposal.');
        }else if(a.review!==null||a.review_sha256!==null)throw Error('Unexpected refused proposal.');
      }
    }
    if(statuses.some(s=>q.counts[s]!==q.items.filter(a=>a.status===s).length))throw Error('Batch counts disagree.');
    return q;
  }
  async function select(index){
    if(!basis||getState().busy)return;
    const selected=basis,ticket=epoch;lock(true);
    try{
      const r=await post('/v1/ai-opportunity-handoff/batch-select',{...selected.request,batch_review_sha256:selected.report.batch_review_sha256,item_index:index});
      if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-batch-selection.v1'||r.packet_sha256!==selected.report.packet_sha256||r.batch_review_sha256!==selected.report.batch_review_sha256||r.item_index!==index||r.proposal_sha256!==selected.report.items[index].proposal_sha256||r.execution_authorized!==false||r.portfolio_written!==false||r.human_review_required!==true||typeof r.proposal_json!=='string')throw Error('Selected proposal does not match the reviewed item.');
      const p=JSON.parse(r.proposal_json);if(p.packet_sha256!==r.packet_sha256)throw Error('Selected proposal packet mismatch.');
      onSelected(r.proposal_json);status.textContent='Item '+(index+1)+' transferred to individual review. No idea or skill has been created.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}finally{lock(false);refresh();}
  }
  review.onclick=async()=>{
    const state=getState(),f=file.files?.[0];if(review.disabled||!state.packet||!f)return;
    invalidate();const ticket=epoch;lock(true);status.textContent='Reviewing the complete returned proposal array.';
    try{
      if(!f.size||f.size>LIMIT)throw Error('Select a proposal array of at most 2 MiB.');
      const proposals_json=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());
      const items=JSON.parse(proposals_json);if(!Array.isArray(items)||items.length<1||items.length>20)throw Error('Select one to twenty proposal items.');
      const request={sources:await Promise.all(state.files.map(f=>f.text())),packet_sha256:state.packet.packet_sha256,proposals_json};
      const r=await post('/v1/ai-opportunity-handoff/batch-review',request);if(ticket!==epoch)return;
      const q=validate(r,state.packet,items);
      output.append(el('h4',q.item_count+' returned items'),el('p',q.distinct_cited_event_count+' / '+q.selected_event_count+' selected events cited; '+q.uncited_run_refs.length+' uncited runs; '+q.coverage_gaps.length+' coverage gaps. Not a measure of discovery completeness.'));
      for(const row of q.items){
        const section=el('section');section.append(el('h4','Item '+(row.item_index+1)+' — '+row.status.replaceAll('_',' ')));
        if(row.status==='structurally_reviewed'){
          section.append(el('p',String(items[row.item_index].title??'').slice(0,240)),el('p',row.review.cited_event_count+' cited events; '+row.review.unresolved_question_count+' unresolved questions.'));
          const b=button('Use item '+(row.item_index+1)+' for individual review');b.onclick=()=>select(row.item_index);choices.push(b);section.append(b);
        }else if(row.status==='exact_duplicate')section.append(el('p','Exact duplicate of item '+(row.duplicate_of_index+1)+'.'));
        output.append(section);
      }
      basis={request,report:q,raw:r.report_json};status.textContent='Batch review complete: '+q.counts.structurally_reviewed+' distinct structurally reviewed, '+q.counts.structural_review_refused+' refused, '+q.counts.exact_duplicate+' exact duplicates. Review SHA-256: '+q.batch_review_sha256+'.';
    }catch(e){if(ticket===epoch){basis=null;choices=[];output.replaceChildren();status.textContent=e.message;}}finally{lock(false);refresh();}
  };
  save.onclick=()=>{if(basis&&!getState().busy)download('owg-implementation-batch-review.json','application/json',basis.raw);};
  const planningHost=el('div');
  const details=el('details');details.id='owgImplementationBatch';
  details.append(el('summary','Review multiple AI implementation proposals'),el('p','Provide the shared original or compact evidence and matching instructions to an authorized AI. Ask it to return a JSON array of 1–20 independent existing v1 proposal objects with the same original packet pin. Do not invent candidates to fill a quota. Review the original proposals yourself; OWG validates references, not business meaning.'),field('Returned proposal array JSON (2 MiB max)',file),review,status,output,save,planningHost);
  host.append(details);refresh();
  import('/ai-implementation-planning.js').then(module=>{
    planningPanel=module.mountImplementationPlanning(planningHost,{getBasis:()=>basis,getBusy:()=>getState().busy,post,lock,download});
  }).catch(()=>{planningHost.textContent='Implementation planning unavailable; batch review remains available.';});
  return {refresh,invalidate};
}
