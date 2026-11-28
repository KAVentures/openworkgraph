/* Optional compatibility with the earlier sectioned proposal contract. */
export function mountImplementationCompat(host,{getState,post,lock,download,onSelected,onClear}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,n)=>{const l=el('label',label+' ');l.style.display='block';l.append(n);return l;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const old=file(),returned=file(),evidencePin=el('input'),reviewPin=el('input');evidencePin.maxLength=reviewPin.maxLength=64;
  const prepare=button('Prepare compatibility handoff'),saveHandoff=button('Download compatibility handoff'),review=button('Review re-authored proposal'),saveReview=button('Download compatibility review'),use=button('Use re-authored proposal for individual review');
  const status=el('p','Prepare an evidence packet from one original source first.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');let basis=null,result=null,epoch=0;
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  function ready(s){return !!s.packet&&s.files.length===1&&hex(s.packet.packet_sha256);}
  function refresh(){
    const s=getState(),active=ready(s)&&!s.busy;
    for(const n of [old,evidencePin,reviewPin])n.disabled=!active;
    prepare.disabled=!active||!old.files?.[0]||!hex(evidencePin.value.trim())||!hex(reviewPin.value.trim());
    saveHandoff.disabled=!active||!basis;returned.disabled=!active||!basis;
    review.disabled=!active||!basis||!returned.files?.[0];saveReview.disabled=use.disabled=!active||!result;
  }
  function clearReview(){epoch++;result=null;output.replaceChildren();onClear();}
  function invalidate(){clearReview();basis=null;returned.value='';status.textContent='Inputs changed. Prepare the compatibility handoff again.';refresh();}
  for(const n of [old,evidencePin,reviewPin])n.addEventListener('input',invalidate);
  returned.addEventListener('change',()=>{clearReview();status.textContent='Returned proposal changed. Review it again.';refresh();});
  async function read(f){if(!f||!f.size||f.size>1024*1024)throw Error('Choose a proposal JSON file of at most 1 MiB.');return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());}
  prepare.onclick=async()=>{
    const s=getState();if(prepare.disabled||!ready(s))return;invalidate();const ticket=epoch,f=old.files[0];lock(true);
    try{
      const request={source_json:await s.files[0].text(),packet_sha256:s.packet.packet_sha256,evidence_sha256:evidencePin.value.trim(),sectioned_review_sha256:reviewPin.value.trim(),sectioned_proposal_json:await read(f)};
      const r=await post('/v1/ai-opportunity-handoff/compat-prepare',request);if(ticket!==epoch)return;
      const h=JSON.parse(r.handoff_json);
      if(r.format!=='openworkgraph.ai-implementation-compatibility-handoff-result.v1'||!hex(r.handoff_sha256)||r.execution_authorized!==false||r.portfolio_written!==false||h.format!=='openworkgraph.ai-implementation-compatibility-handoff.v1'||h.handoff_sha256!==r.handoff_sha256||h.packet_sha256!==request.packet_sha256||h.sectioned_evidence_sha256!==request.evidence_sha256||h.sectioned_review_sha256!==request.sectioned_review_sha256||h.evidence_packet?.packet_sha256!==request.packet_sha256||h.human_review_required!==true||h.semantic_conversion_performed!==false||h.external_sharing_authorized!==false||h.execution_authorized!==false||!Array.isArray(h.section_reference_map)||!hex(h.sectioned_proposal_sha256))throw Error('Unexpected compatibility handoff.');
      basis={request,h,raw:r.handoff_json};status.textContent='Compatibility handoff ready. Inspect locally before sharing with an authorized AI. Handoff SHA-256: '+h.handoff_sha256+'.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}finally{lock(false);refresh();}
  };
  review.onclick=async()=>{
    if(review.disabled||!basis)return;const b=basis,f=returned.files[0];clearReview();const ticket=epoch;lock(true);
    try{
      const r=await post('/v1/ai-opportunity-handoff/compat-review',{...b.request,handoff_sha256:b.h.handoff_sha256,integrated_proposal_json:await read(f)});if(ticket!==epoch)return;
      const q=JSON.parse(r.report_json),p=JSON.parse(r.proposal_json);
      if(r.format!=='openworkgraph.ai-implementation-compatibility-review-result.v1'||!hex(r.compatibility_review_sha256)||r.execution_authorized!==false||r.portfolio_written!==false||q.format!=='openworkgraph.ai-implementation-compatibility-review.v1'||q.compatibility_review_sha256!==r.compatibility_review_sha256||q.handoff_sha256!==b.h.handoff_sha256||q.packet_sha256!==b.h.packet_sha256||q.sectioned_review_sha256!==b.h.sectioned_review_sha256||q.sectioned_proposal_sha256!==b.h.sectioned_proposal_sha256||!hex(q.integrated_proposal_sha256)||q.structural_review?.proposal_sha256!==q.integrated_proposal_sha256||p.packet_sha256!==q.packet_sha256||q.human_review_required!==true||['semantic_preservation_verified','policy_preservation_verified','execution_authorized','portfolio_written'].some(k=>q[k]!==false)||!Array.isArray(q.section_reference_review)||q.section_reference_review.length!==b.h.section_reference_map.length)throw Error('Unexpected compatibility review.');
      output.append(el('p',q.retained_distinct_citation_count+' of '+q.original_distinct_citation_count+' original scoped citations retained. This does not establish semantic preservation.'));
      for(const [i,row] of q.section_reference_review.entries()){
        const prior=b.h.section_reference_map[i];if(row.section!==prior.section||row.entry_index!==prior.entry_index||row.kind!==prior.kind||JSON.stringify(row.scoped_refs)!==JSON.stringify(prior.scoped_refs)||!Array.isArray(row.not_cited_in_returned_proposal))throw Error('Original section entry mismatch.');
        output.append(el('p',row.section+' · entry '+(row.entry_index+1)+' · '+row.not_cited_in_returned_proposal.length+' original citations not reused.'));
      }
      output.append(el('p','Review each original entry, especially unknowns, policy and approvals. No idea or skill has been created.'));result=r;status.textContent='Compatibility review complete. Review SHA-256: '+r.compatibility_review_sha256+'.';
    }catch(e){if(ticket===epoch){output.replaceChildren();status.textContent=e.message;}}finally{lock(false);refresh();}
  };
  saveHandoff.onclick=()=>{if(basis&&!getState().busy)download('owg-compatibility-handoff.json','application/json',basis.raw);};
  saveReview.onclick=()=>{if(result&&!getState().busy)download('owg-compatibility-review.json','application/json',result.report_json);};
  use.onclick=()=>{if(result&&!getState().busy){onSelected(result.proposal_json);status.textContent='Transferred the exact re-authored proposal to individual review. No approval or execution transferred.';}};
  const details=el('details');details.id='owgImplementationCompat';details.append(el('summary','Optional: reuse an earlier sectioned proposal'),el('p','Use one original source and its prepared packet. Retain the independent earlier evidence and review digests. The handoff preserves prior interpretation and exact citation mappings; it does not convert it automatically. Review the original and returned proposals before proceeding.'),field('Original sectioned proposal JSON',old),field('Earlier evidence SHA-256',evidencePin),field('Earlier sectioned review SHA-256',reviewPin),prepare,saveHandoff,field('Re-authored integrated proposal JSON',returned),review,status,output,saveReview,use);host.append(details);refresh();return {refresh,invalidate};
}
