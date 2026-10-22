(() => {
  const esc = value => typeof window.esc === 'function'
    ? window.esc(String(value ?? ''))
    : String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = seconds => typeof window.fmtTime === 'function' ? window.fmtTime(seconds) : `${Math.round(Number(seconds||0)/60)}m`;
  let families=[];
  let recordLanes=[];
  let recordLaneCoverageLimited=false;
  let loading=false;

  function repeatedCard(){
    return [...document.querySelectorAll('#panel-overview .card')].find(card =>
      /repeated workflows/i.test(card.querySelector('h2')?.textContent || '')
    ) || document.querySelector('#panel-overview #patternCard') || null;
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
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="ghost" type="button" id="refreshWorkflowEvidence">Refresh examples</button><span class="badge">Evidence → external AI</span></div>
      </div>
      <div id="workflowEvidenceCandidates" style="margin-top:10px"><div class="muted">Loading observed examples…</div></div>
      <div style="border-top:1px solid #eceee8;padding-top:13px;margin-top:13px">
        <h3>Record-linked observations</h3>
        <div class="muted">Separate records stay separate even when work is interleaved. Only exact, opted-in OWG record references are matched; this is not a verified business process, handoff or outcome.</div>
        <div id="workflowRecordCaseLanes" style="margin-top:10px"></div>
      </div>`;
    host.querySelector('#refreshWorkflowEvidence').onclick=()=>refresh();
    const patternList=parent.querySelector('#patternList');
    if(patternList) patternList.insertAdjacentElement("afterend",host); else parent.appendChild(host);
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

  function sourceExplorerButton(host,process,label){
    const open=document.createElement('button');open.type='button';open.className='secondary';
    open.textContent='Explore '+label+' source process';
    open.addEventListener('click',()=>{
      try{
        if(!window.owgProcessMining?.showSourcePreview)throw new Error('Process explorer unavailable. Reopen the local dashboard.');
        window.owgProcessMining.showSourcePreview(process);
      }catch(error){host.appendChild(document.createTextNode(String(error.message||'Source projection unavailable.')));}
    });host.appendChild(open);
  }

  function renderRecordLanes(){
    const host=ensureCard()?.querySelector('#workflowRecordCaseLanes');
    if(!host) return;
    const rows=recordLanes.slice(0,8);
    if(!rows.length){
      host.innerHTML='<p class="muted">No repeated exact-record observations in this evidence window. This does not mean no business cases exist; exact source references require opt-in capture.</p>';
      return;
    }
    const coverage=recordLaneCoverageLimited
      ? '<p class="muted">Source event cap reached. Earlier observations may be missing; narrow the period before assessing coverage.</p>' : '';
    host.innerHTML=coverage+rows.map((item,index)=>{
      const steps=(item.observed_steps||[]).slice(0,24);
      const lines=steps.map(step=>'<li><span class="muted">'+esc(step.observed_at||'')+'</span> · '+
        esc(step.source||'unknown')+' · '+esc(step.action||'unknown')+'</li>').join('');
      const more=item.steps_truncated?'<li class="muted">Additional events not shown. Read the exact record via existing record-graph tooling for a bounded drilldown.</li>':'';
      const jiraOption=item.provider==='jira'
        ? '<button class="secondary" type="button" data-jira-process="'+index+'">Read opted-in Jira audit</button><div role="status" data-jira-process-result="'+index+'" class="muted" style="margin-top:7px"></div>'
        : '';
      const githubOption=item.provider==='github'&&item.resource_kind==='pull_request'
        ? '<button class="secondary" type="button" data-github-process="'+index+'">Read opted-in GitHub review history</button><div role="status" data-github-process-result="'+index+'" class="muted" style="margin-top:7px"></div>' : '';
      const compareOption=item.provider==='jira'
        ? '<label style="display:block;margin:8px 0"><input type="checkbox" data-jira-batch-select="'+index+'"> Include this observed Jira record in manual source comparison</label>'
        : '';
      return '<details style="padding:9px 0;border-bottom:1px solid #eceee8" data-record-lane="'+index+'">'+
        '<summary style="cursor:pointer"><strong>'+esc(item.provider||'Record')+' · '+esc(item.resource_kind||'object')+'</strong>'+
        ' <span class="muted">· '+Number(item.observed_event_count||0)+' observed events, '+
        Number(item.observed_actor_count||0)+' actors, '+Number(item.observed_session_count||0)+' sessions</span></summary>'+
        '<p class="muted">Exact opaque reference: <code>'+esc(item.resource_ref||'')+
        '</code>. No cross-record join, verified handoff or successful business outcome is implied.</p>'+
        '<ol style="margin-top:6px">'+lines+more+'</ol>'+jiraOption+githubOption+compareOption+
        '</details>';
    }).join('');
    if(rows.filter(item=>item.provider==='jira').length>=2){
      const bar=document.createElement('div');
      bar.style.cssText='padding:10px 0;border-bottom:1px solid #eceee8';
      const compare=document.createElement('button');
      compare.type='button';
      compare.className='secondary';
      compare.textContent='Compare selected Jira source audits';
      compare.disabled=true;
      const output=document.createElement('div');
      output.setAttribute('role','status');
      output.className='muted';
      output.style.marginTop='8px';
      const selected=()=>[...host.querySelectorAll('[data-jira-batch-select]:checked')]
        .map(input=>rows[Number(input.dataset.jiraBatchSelect||0)])
        .filter(item=>item&&item.provider==='jira');
      host.querySelectorAll('[data-jira-batch-select]').forEach(checkbox=>{
        checkbox.addEventListener('change',()=>{compare.disabled=selected().length<2;});
      });
      compare.onclick=async()=>{
        const refs=selected().map(item=>String(item.resource_ref||''));
        if(refs.length<2||refs.length>8)return;
        if(!confirm('Read '+refs.length+' exact Jira issue changelogs for a one-time local process comparison? A separate Jira read-only OAuth opt-in is required.'))return;
        compare.disabled=true;
        output.textContent='Reading explicitly selected Jira audit records…';
        try{
          await window.__owgAuthReady;
          const response=await fetch('/v1/process-mining/jira-selected-cases',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({resource_refs:refs,confirm_source_reads:true}),
            cache:'no-store'
          });
          if(!response.ok)throw new Error('Selected Jira audit read unavailable ('+response.status+').');
          const data=await response.json();
          output.replaceChildren();
          const info=document.createElement('p');
          info.textContent=String(data.audited_record_count||0)+' of '+
            String(data.requested_record_count||0)+' selected records supplied eligible source audits; '+
            String(data.partial_audit_record_count||0)+' have partial audit coverage. '+
            'Missing data does not prove missing work.';
          output.appendChild(info);
          const process=data.process;
          if(process){
            sourceExplorerButton(output,process,'Jira');
            const metrics=document.createElement('p');
            metrics.textContent=String(process.variant_count||0)+' observed variants across '+
              String(process.case_count||0)+' distinct source issues. This is not a joined cross-issue business case.';
            output.appendChild(metrics);
            const list=document.createElement('ol');
            (process.variants||[]).slice(0,8).forEach(variant=>{
              const li=document.createElement('li');
              li.textContent=String(variant.case_count||0)+' issue(s): '+
                (variant.observed_path||[]).slice(0,24).join(' → ')+
                (variant.path_truncated?' (path truncated)':'');
              if(!variant.path_truncated){
                const draft=document.createElement('button');draft.type='button';draft.className='secondary';
                draft.textContent='Draft this source variant';
                draft.addEventListener('click',()=>{
                  try{
                    if(!window.owgProcessCatalog?.draftObserved)throw new Error('Process catalog unavailable.');
                    window.owgProcessCatalog.draftObserved({path:variant.observed_path,count:variant.case_count,
                      basis:'selected_source_audit',partial:!data.all_selected_source_audits_complete,evidenceRefs:[]});
                  }catch(error){output.appendChild(document.createTextNode(String(error.message||'Could not draft source variant.')));}
                });li.appendChild(draft);
              }
              list.appendChild(li);
            });
            output.appendChild(list);
          }
          const warning=document.createElement('p');
          warning.textContent='Audit events are source-reported, not company policy, customer success, or authorization for an AI agent.';
          output.appendChild(warning);
        }catch(error){output.textContent=String(error?.message||'Audit comparison unavailable.');}
        finally{compare.disabled=selected().length<2;}
      };
      bar.append(compare,output);
      host.prepend(bar);
    }
    host.querySelectorAll('[data-jira-process]').forEach(button=>{
      button.onclick=async()=>{
        const index=Number(button.dataset.jiraProcess||0);
        const item=rows[index];
        const result=host.querySelector('[data-jira-process-result="'+index+'"]');
        if(!result||!item||item.provider!=='jira')return;
        if(!confirm('Read Jira source audit for this exact observed issue? This requires a separately enabled Jira OAuth read-only source connection.'))return;
        button.disabled=true;
        result.replaceChildren(document.createTextNode('Reading bounded Jira issue audit…'));
        try{
          await window.__owgAuthReady;
          const params=new URLSearchParams({
            resource_ref:String(item.resource_ref||''),
            confirm_source_read:'true'
          });
          const response=await fetch('/v1/process-mining/jira-case?'+params,{cache:'no-store'});
          if(!response.ok)throw new Error('Jira audit read unavailable ('+response.status+').');
          const data=await response.json();
          if(data.availability!=='source_audit'||!data.process){
            result.textContent='No eligible opted-in Jira issue changelog available ('+
              String(data.availability||'unavailable')+'). No unobserved work can be inferred.';
            return;
          }
          const process=data.process;
          result.replaceChildren();
          sourceExplorerButton(result,process,'Jira');
          result.appendChild(document.createTextNode(
            Number(process.input_rows||0)+' Jira-audited status/assignee events. '+
            (data.source_audit_complete?'Source audit page complete.':'Source audit is partial; earlier changes may be missing.')
          ));
          const list=document.createElement('ol');
          const example=(process.variants||[])[0]?.observed_path||[];
          example.slice(0,24).forEach(step=>{
            const li=document.createElement('li');li.textContent=String(step||'');
            list.appendChild(li);
          });
          result.appendChild(list);
          const interpretation=document.createElement('p');
          interpretation.textContent='These are provider-reported audit actions, not employee task acceptance, business policy, customer success or agent authorization.';
          result.appendChild(interpretation);
        }catch(error){
          result.textContent=String(error?.message||'The source audit was unavailable.');
        }finally{button.disabled=false;}
      };
    });
    host.querySelectorAll('[data-github-process]').forEach(button=>{
      button.onclick=async()=>{
        const item=rows[Number(button.dataset.githubProcess||0)];
        const result=host.querySelector('[data-github-process-result="'+button.dataset.githubProcess+'"]');
        if(!item||!result||item.provider!=='github'||item.resource_kind!=='pull_request')return;
        if(!confirm('Read a bounded review/lifecycle projection for this exact observed pull request? This requires your separate existing GitHub source-status opt-in and local GitHub CLI login.'))return;
        button.disabled=true;result.textContent='Reading selected GitHub source history…';
        try{
          await window.__owgAuthReady;
          const response=await fetch('/v1/process-mining/github-selected-cases',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({resource_refs:[item.resource_ref],confirm_source_reads:true}),cache:'no-store'});
          if(!response.ok)throw new Error('GitHub source history unavailable ('+response.status+').');
          const data=await response.json();result.replaceChildren();
          result.appendChild(document.createTextNode(data.process?
            data.process.input_rows+' supported review/lifecycle events. This is a partial source projection, not a complete business-case history.':
            'No eligible source history is available. Missing data does not prove missing work.'));
          if(data.process)sourceExplorerButton(result,data.process,'GitHub');
        }catch(error){result.textContent=String(error.message||'GitHub source projection unavailable.');}
        finally{button.disabled=false;}
      };
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
    const button=document.querySelector('#refreshWorkflowEvidence');
    if(button){button.disabled=true;button.textContent='Refreshing…';}
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/workflow-evidence/families?min_runs=2&limit=20',{cache:'no-store'});
      if(!response.ok) throw new Error('workflow evidence unavailable');
      const payload=await response.json();
      families=Array.isArray(payload.families)?payload.families:[];
      recordLanes=Array.isArray(payload.record_case_lanes?.lanes)?payload.record_case_lanes.lanes:[];
      recordLaneCoverageLimited=!!payload.source_limit_reached;
      renderFamilies();
      renderRecordLanes();
    }catch(_){
      families=[];
      recordLanes=[];
      recordLaneCoverageLimited=false;
      renderRecordLanes();
      const host=ensureCard()?.querySelector('#workflowEvidenceCandidates');
      if(host) host.innerHTML='<div class="muted">Workflow evidence examples are unavailable right now. Stored/Redacted evidence export and canonical MCP trace access are unchanged.</div>';
    }finally{
      loading=false;
      const current=document.querySelector('#refreshWorkflowEvidence');
      if(current){current.disabled=false;current.textContent='Refresh examples';}
    }
  }

  const observer=new MutationObserver(()=>{ensureCard();removeOldPlaceholderActions();});
  function install(){
    ensureCard();
    const overview=document.querySelector('#panel-overview');
    if(overview) observer.observe(overview,{childList:true,subtree:true});
    document.querySelector('#tab-overview')?.addEventListener('click',()=>setTimeout(refresh,0));
    refresh();
  }
  window.refreshWorkflowEvidenceDrafting=refresh;
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
