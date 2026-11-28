/* Explicit, single-idea portfolio planning handoff. No assessment or approval. */
export function mountPortfolioPlanningHandoff(host,{getBasis,getBusy,post,lock,onSaved}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const pick=el('select'),estimate=el('input'),confirm=el('input');estimate.maxLength=20;estimate.inputMode='decimal';confirm.type='checkbox';
  const preview=button('Preview one saved idea planning update'),save=button('Save exact planning update');
  const status=el('p','Reconcile the current portfolio before selecting an idea.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('pre');output.style.whiteSpace='pre-wrap';output.style.overflowWrap='anywhere';
  let snapshot=null,pinned=null,epoch=0;
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  function clear(){epoch++;pinned=null;confirm.checked=false;output.textContent='';}
  function invalidate(){clear();snapshot=null;pick.replaceChildren();estimate.value='';status.textContent='Refresh the saved portfolio before planning handoff.';refresh();}
  function refresh(){
    const b=getBasis(),busy=getBusy();
    if(snapshot!==b?.report.reconciliation_sha256){
      clear();snapshot=b?.report.reconciliation_sha256??null;pick.replaceChildren();estimate.value='';
      const blank=el('option','Select an editable matched idea');blank.value='';pick.append(blank);
      if(b)for(const row of b.report.items){if(row.mapping_status==='matched'&&row.scenario_assessed&&['idea','assessing','on_hold'].includes(row.saved_idea?.state)){
        const o=el('option','Item '+(row.item_index+1)+' · '+row.saved_idea.idea_id+' · revision '+row.saved_idea.revision);o.value=String(row.item_index);pick.append(o);
      }}
    }
    pick.disabled=busy||!b;estimate.disabled=busy||!b;preview.disabled=busy||!b||!pick.value;
    confirm.disabled=busy||!pinned;save.disabled=busy||!pinned||!confirm.checked;
  }
  for(const n of [pick,estimate])n.addEventListener('input',()=>{clear();status.textContent='Selection changed. Preview the exact planning update again.';refresh();});
  confirm.addEventListener('change',refresh);
  preview.onclick=async()=>{
    const b=getBasis();if(preview.disabled||!b)return;clear();const ticket=epoch,index=Number(pick.value),row=b.report.items[index],value=estimate.value.trim()||null;
    const request={...b.request,reconciliation_sha256:b.report.reconciliation_sha256,item_index:index,budget_estimate:value};
    lock(true);status.textContent='Checking exact saved idea revision, dependencies and budget.';
    try{
      const r=await post('/v1/ai-opportunity-handoff/planning-save-preview',request);if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-portfolio-plan-preview.v1'||r.reconciliation_sha256!==b.report.reconciliation_sha256||r.planning_review_sha256!==b.report.planning_review_sha256||r.item_index!==index||r.idea_id!==row.saved_idea.idea_id||r.idea_state!==row.saved_idea.state||r.proposed_planning?.expected_revision!==row.saved_idea.revision||!hex(r.preview_sha256)||r.portfolio_written!==false||r.assessment_changed!==false||r.approval_changed!==false||r.execution_authorized!==false)throw Error('Unexpected planning preview.');
      output.textContent='Existing planning:\n'+JSON.stringify(r.previous_planning,null,2)+'\nProposed planning:\n'+JSON.stringify(r.proposed_planning,null,2)+'\nPreview SHA-256: '+r.preview_sha256;
      pinned={request,preview:r};status.textContent='Review the exact changes before confirming. No planning has been saved.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}finally{lock(false);refresh();}
  };
  save.onclick=async()=>{
    if(save.disabled||!pinned)return;const selected=pinned,ticket=epoch;confirm.checked=false;lock(true);
    try{
      const r=await post('/v1/ai-opportunity-handoff/planning-save-confirm',{...selected.request,expected_preview_sha256:selected.preview.preview_sha256,confirm_save_planning:true});if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-portfolio-plan-saved.v1'||r.idea_id!==selected.preview.idea_id||r.revision!==selected.preview.proposed_planning.expected_revision+1||r.state!==selected.preview.idea_state||r.preview_sha256!==selected.preview.preview_sha256||r.portfolio_written!==true||r.assessment_changed!==false||r.approval_changed!==false||r.execution_authorized!==false)throw Error('Unexpected save response. Reconcile before trying again.');
      onSaved(r);status.textContent='Saved planning for '+r.idea_id+' at revision '+r.revision+'. Refresh the portfolio before another update.';
    }catch(e){if(ticket===epoch){clear();status.textContent=e.message+' Refresh the portfolio before trying again.';}}finally{lock(false);refresh();}
  };
  const details=el('details');details.id='owgPortfolioPlanningHandoff';details.append(el('summary','Transfer reviewed planning to one saved idea'),el('p','Only editable matched and assessed ideas are listed. The server requires plausible declared feasibility and resolved dependencies. Existing unrelated links, owner, team and quarter stay intact. Leave estimate blank to preserve the saved budget; an entered estimate can only fill a missing budget within the reviewed range. No assessment, approval or execution changes.'),field('Saved idea to update',pick),field('Optional implementation budget estimate (exact decimal, scenario currency)',estimate),preview,status,output,field('I inspected and confirm this exact single-idea planning update',confirm),save);host.append(details);refresh();return {refresh,invalidate};
}
