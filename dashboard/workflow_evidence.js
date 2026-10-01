(() => {
  const esc = value => typeof window.esc === 'function'
    ? window.esc(String(value ?? ''))
    : String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = seconds => typeof window.fmtTime === 'function' ? window.fmtTime(seconds) : `${Math.round(Number(seconds||0)/60)}m`;
  let families=[];
  let loading=false;

  function repeatedCard(){
    return [...document.querySelectorAll('#panel-overview .card')].find(card =>
      /repeated workflows/i.test(card.querySelector('h2')?.textContent || '')
    ) || null;
  }

  function removeOldPlaceholderActions(){
    document.querySelectorAll('#patternList .pattern-actions button').forEach(button=>{
      const text=String(button.textContent||'').trim();
      if (/prepare automation context|export evidence for pattern/i.test(text)) button.remove();
    });
    document.querySelectorAll('#patternList .pattern-runs').forEach(node=>{
      if (/execution-level windows.*stacked pattern endpoint/i.test(node.textContent||'')) node.remove();
    });
  }

  function ensureCard(){
    const parent=repeatedCard();
    if(!parent) return null;
    removeOldPlaceholderActions();
    let host=parent.querySelector('#workflowEvidenceDrafting');
    if(host) return host;
    host=document.createElement('div');
    host.id='workflowEvidenceDrafting';
    host.style.cssText='border-top:1px solid #eceee8;margin-top:14px;padding-top:14px';
    host.innerHTML=`
      <div style="display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap">
        <div style="max-width:760px">
          <h3 style="margin:0 0 5px">Teach your AI from observed work</h3>
          <div class="muted">OpenWorkGraph supplies evidence; your AI writes the skill or procedure. Derived groupings are only a way to find examples — select the runs you actually want your AI to learn from.</div>
        </div>
        <span class="badge">Evidence → external AI</span>
      </div>
      <div id="workflowEvidenceCandidates" style="margin-top:10px"><div class="muted">Loading observed examples…</div></div>`;
    const patternList=parent.querySelector('#patternList');
    if(patternList) parent.insertBefore(host,patternList); else parent.appendChild(host);
    return host;
  }

  function stepLabel(value){
    const text=String(value||'');
    if(text.startsWith('surface:')) return text.slice(8).replace(/-/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
    if(text.startsWith('action:')) return text.slice(7).replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
    if(text.startsWith('tool:')) {
      const parts=text.split(':'); return `Agent tool · ${String(parts[1]||'other').replace(/_/g,' ')}`;
    }
    return text.replace(/_/g,' ');
  }

  function candidateTitle(item){
    const steps=(item.high_support_structural_steps||[]).map(stepLabel).filter(Boolean);
    return steps.length ? steps.slice(0,4).join(' → ') : 'Observed repeated work';
  }

  function renderFamilies(){
    const host=ensureCard()?.querySelector('#workflowEvidenceCandidates');
    if(!host) return;
    if(!families.length){
      host.innerHTML='<div class="muted">No repeated structural family has at least two observed executions yet. You can still use Stored/Redacted exports or ask a connected AI to inspect canonical evidence directly.</div>';
      return;
    }
    host.innerHTML=families.slice(0,8).map((item,index)=>{
      const actors=(item.actor_kinds||[]).join(' + ');
      return `<div style="padding:11px 0;border-top:${index?'1px solid #eceee8':'0'}">
        <div style="display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap">
          <div style="min-width:240px;flex:1"><strong>${esc(candidateTitle(item))}</strong><div class="muted" style="margin-top:4px">${Number(item.execution_count||0)} observed runs · ${esc(actors||'observed work')} · median ${esc(fmt(item.median_execution_duration_seconds||0))}</div></div>
          <button class="secondary" type="button" data-review-workflow="${index}">Review examples & use with AI</button>
        </div>
        <div class="muted" style="margin-top:5px">Grouping is derived and may be wrong. Nothing here is treated as the task's meaning, policy, or permission.</div>
      </div>`;
    }).join('');
    host.querySelectorAll('[data-review-workflow]').forEach(button=>{
      button.onclick=()=>openReview(Number(button.dataset.reviewWorkflow||0));
    });
  }

  function selectedIds(index){
    return [...document.querySelectorAll(`#workflowEvidenceRunList input[data-family-index="${index}"]:checked`)].map(input=>input.value);
  }

  function aiRequest(ids){
    const joined=ids.join(',');
    return `Draft a reusable skill or procedure from these OpenWorkGraph examples. Use get_workflow_evidence(execution_ids="${joined}") as the evidence bundle. Reconstruct the outcome from canonical evidence rather than trusting a derived workflow label; distinguish repeated observations from variations using support/provenance; prefer current authorized connectors/APIs/tools over replaying human clicks; ask me about missing business rules, escalation criteria, source-of-truth choices, and approval boundaries; do not infer permission from repetition or invent clipboard values; draft an agent-neutral procedure first and adapt it to your supported skill format only after the procedure is clear. Do not execute the historical workflow against live systems unless I separately authorize that.`;
  }

  function updateSelectionActions(index){
    const ids=selectedIds(index);
    const count=document.querySelector('#workflowEvidenceSelectedCount');
    if(count) count.textContent=`${ids.length} run${ids.length===1?'':'s'} selected`;
    document.querySelectorAll('[data-workflow-selection-action]').forEach(button=>button.disabled=!ids.length);
  }

  async function downloadEvidence(index,representation){
    const ids=selectedIds(index); if(!ids.length) return;
    const query=new URLSearchParams({execution_ids:ids.join(','),representation,archive:'true'});
    try{
      await window.__owgAuthReady;
      const response=await fetch(`/v1/workflow-evidence/export?${query.toString()}`,{cache:'no-store'});
      if(!response.ok){
        let detail='Could not export workflow evidence.';
        try{detail=String((await response.clone().json())?.detail||detail);}catch(_){}
        throw new Error(detail);
      }
      const blob=await response.blob();
      const href=URL.createObjectURL(blob);
      const anchor=document.createElement('a');
      anchor.href=href;
      anchor.download='openworkgraph-workflow-evidence.zip';
      anchor.style.display='none';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(()=>URL.revokeObjectURL(href),5000);
      if(typeof window.toast==='function') window.toast(`${representation==='redacted'?'Redacted':'Stored'} workflow evidence exported.`);
    }catch(error){
      if(typeof window.openModal==='function') window.openModal('Export unavailable','Local security',`<p>${esc(error?.message||'Could not authorize this export.')}</p><div class="note">If OpenWorkGraph restarted, reopen the dashboard from the current launcher and try again.</div>`);
    }
  }

  function openReview(index){
    const item=families[index];
    if(!item) return;
    const runs=item.executions||[];
    const rows=runs.map(run=>{
      const start=run.started_at?new Date(run.started_at).toLocaleString():'Time unavailable';
      const actor=String(run.actor_kind||'observed');
      const outcome=String(run.outcome_status||'unknown').replace(/_/g,' ');
      return `<label style="display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-bottom:1px solid #eceee8;cursor:pointer">
        <input type="checkbox" data-family-index="${index}" value="${esc(run.execution_id||'')}" checked style="width:18px;height:18px;margin-top:2px">
        <span><strong>${esc(start)}</strong><span class="muted"> · ${esc(actor)} · ${esc(outcome)}</span><div class="muted mono" style="margin-top:3px">${esc(run.execution_id||'')}</div></span>
      </label>`;
    }).join('');
    const body=`
      <p><strong>Select the examples your AI should learn from.</strong> OWG's grouping is only a suggestion; uncheck anything that does not belong.</p>
      <div class="note"><strong>What the AI receives</strong><div class="muted" style="margin-top:4px">The evidence bundle contains bounded canonical evidence plus support-counted structural observations, resource types, copy/paste linkage, timing and provenance. With MCP, your AI-context setting controls Redacted vs Full context. Manual export uses the Redacted or Stored choice below. OWG does not write the skill.</div></div>
      <div id="workflowEvidenceRunList" style="margin-top:10px;max-height:310px;overflow:auto">${rows||'<div class="muted">No selectable executions were returned.</div>'}</div>
      <div class="muted" id="workflowEvidenceSelectedCount" style="margin-top:8px"></div>
      <div class="modal-actions">
        <button type="button" data-workflow-selection-action="copy">Copy request for connected AI</button>
        <button type="button" class="secondary" data-workflow-selection-action="redacted">Export redacted evidence</button>
        <button type="button" class="secondary" data-workflow-selection-action="stored">Export stored evidence</button>
      </div>
      <div class="muted" style="margin-top:8px"><strong>Redacted</strong> is recommended for upload/share. <strong>Stored</strong> means the local privacy-hardened representation, not pre-privacy capture; review it before sharing outside its intended context.</div>`;
    if(typeof window.openModal==='function') window.openModal('Draft a skill with your AI','Evidence, not inference',body);
    document.querySelectorAll(`#workflowEvidenceRunList input[data-family-index="${index}"]`).forEach(input=>input.onchange=()=>updateSelectionActions(index));
    const copy=document.querySelector('[data-workflow-selection-action="copy"]');
    const redacted=document.querySelector('[data-workflow-selection-action="redacted"]');
    const stored=document.querySelector('[data-workflow-selection-action="stored"]');
    if(copy) copy.onclick=()=>{
      const ids=selectedIds(index); if(!ids.length) return;
      const text=aiRequest(ids);
      if(typeof window.copyText==='function') window.copyText(text,copy);
      else navigator.clipboard?.writeText(text);
      if(typeof window.toast==='function') window.toast('Copied. Paste this into any connected AI; with OWG MCP it can fetch the evidence itself.');
    };
    if(redacted) redacted.onclick=()=>downloadEvidence(index,'redacted');
    if(stored) stored.onclick=()=>{
      if(confirm('Export the stored privacy-hardened representation? Redacted is safer for sharing outside your intended context.')) downloadEvidence(index,'stored');
    };
    updateSelectionActions(index);
  }

  async function refresh(){
    ensureCard();
    removeOldPlaceholderActions();
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if(active!=='overview'||loading) return;
    loading=true;
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/workflow-evidence/families?min_runs=2&limit=20',{cache:'no-store'});
      if(!response.ok) throw new Error('workflow evidence unavailable');
      const payload=await response.json();
      families=Array.isArray(payload.families)?payload.families:[];
      renderFamilies();
    }catch(_){
      const host=ensureCard()?.querySelector('#workflowEvidenceCandidates');
      if(host) host.innerHTML='<div class="muted">Workflow evidence examples are unavailable right now. Stored/Redacted evidence export and canonical MCP trace access are unchanged.</div>';
    }finally{loading=false;}
  }

  const observer=new MutationObserver(()=>{ensureCard();removeOldPlaceholderActions();});
  function install(){
    ensureCard();
    const overview=document.querySelector('#panel-overview');
    if(overview) observer.observe(overview,{childList:true,subtree:true});
    document.querySelector('#tab-overview')?.addEventListener('click',()=>setTimeout(refresh,0));
    refresh();
    setInterval(()=>{if(!document.hidden) refresh();},7000);
  }
  window.refreshWorkflowEvidenceDrafting=refresh;
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
