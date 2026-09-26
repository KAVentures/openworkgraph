(() => {
  const KNOWN = [
    [/mail\.google\.com|\bgmail\b/i, 'Gmail'],
    [/docs\.google\.com\/spreadsheets|google sheets|\bsheets\b/i, 'Google Sheets'],
    [/docs\.google\.com\/document|google docs/i, 'Google Docs'],
    [/salesforce/i, 'Salesforce'],
    [/chatgpt|chat\.openai\.com/i, 'ChatGPT'],
    [/claude/i, 'Claude'],
    [/github/i, 'GitHub'],
    [/slack/i, 'Slack'],
    [/outlook|office\.com.*mail/i, 'Outlook'],
    [/microsoft teams|teams\.microsoft/i, 'Microsoft Teams'],
    [/notion/i, 'Notion'],
    [/figma/i, 'Figma'],
    [/linear/i, 'Linear'],
    [/jira/i, 'Jira'],
    [/confluence/i, 'Confluence'],
    [/cursor/i, 'Cursor'],
  ];
  const PALETTE = [
    '#2f6b4f','#8a4b2a','#315a8a','#7a4f9a','#9a6b1f','#3f7a7a',
    '#8b3f5e','#566b2f','#5e5aa0','#a14a3b','#3f6f8f','#6a5a3c',
  ];
  const colorByKey = new Map();
  const keyByColor = new Map();

  function canonicalSurfaceName(value) {
    let raw=String(value||'Unknown')
      .replace(/^Google Chrome\s*[·|\-]\s*/i,'')
      .replace(/^Chrome\s*[·|\-]\s*/i,'')
      .replace(/^Microsoft Edge\s*[·|\-]\s*/i,'')
      .trim() || 'Unknown';
    for (const [matcher,name] of KNOWN) if (matcher.test(raw)) return name;
    return raw;
  }

  function surfaceKey(value) {
    return canonicalSurfaceName(value).toLocaleLowerCase('en-US');
  }

  function hash(value) {
    let h=2166136261;
    for (const c of String(value)) { h^=c.charCodeAt(0); h=Math.imul(h,16777619); }
    return h>>>0;
  }

  function unifiedSurfaceColor(value) {
    const key=surfaceKey(value);
    if (colorByKey.has(key)) return colorByKey.get(key);
    let index=hash(key)%PALETTE.length;
    for (let i=0;i<PALETTE.length;i++) {
      const candidate=PALETTE[(index+i)%PALETTE.length];
      const owner=keyByColor.get(candidate);
      if (!owner || owner===key) {
        colorByKey.set(key,candidate); keyByColor.set(candidate,key); return candidate;
      }
    }
    const hue=hash(key)%360;
    const fallback=`hsl(${hue} 42% 38%)`;
    colorByKey.set(key,fallback);
    return fallback;
  }

  window.canonicalSurfaceName=canonicalSurfaceName;
  window.surfaceName=canonicalSurfaceName;
  window.surfaceKey=surfaceKey;
  window.surfaceColor=unifiedSurfaceColor;

  function to24Hour(text) {
    const value=String(text||'').trim();
    const match=value.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (!match) return value.replace(/^0(?=\d:)/,'');
    let hour=Number(match[1])%12;
    if (match[3].toUpperCase()==='PM') hour+=12;
    return `${String(hour).padStart(2,'0')}:${match[2]}`;
  }

  function polishTimeline() {
    const host=document.querySelector('#timelineMount');
    if (!host) return;
    const tickSpans=[...host.querySelectorAll('span.muted')].filter(span=>/\b[AP]M\b/i.test(span.textContent||''));
    tickSpans.forEach((span,index)=>{
      span.textContent=to24Hour(span.textContent);
      span.style.whiteSpace='nowrap';
      span.style.fontVariantNumeric='tabular-nums';
      if (index===0) { span.style.left='0'; span.style.right='auto'; span.style.transform='none'; }
      else if (index===tickSpans.length-1) { span.style.left='auto'; span.style.right='0'; span.style.transform='none'; }
    });
  }

  function removeDeveloperNotes() {
    document.querySelectorAll('#evidenceTableView .evidence-meta .muted').forEach(node=>{
      if (/cursor paging is added/i.test(node.textContent||'')) node.remove();
    });
  }

  function findSharingCard() {
    return [...document.querySelectorAll('#panel-organization .card')].find(card=>
      /what would be shared/i.test(card.querySelector('h2')?.textContent||'')
    );
  }

  function yn(value) { return value ? 'Yes' : 'No'; }

  function renderPolicy(payload) {
    const card=findSharingCard();
    if (!card) return;
    const effective=payload.effective_policy;
    const local=payload.local_policy||{};
    if (!payload.policy_current && payload.connected) {
      card.innerHTML=`<h2>What would be shared?</h2><div class="note"><strong>Organization policy is temporarily unavailable.</strong><div class="muted" style="margin-top:5px">OpenWorkGraph sync fails closed in this state; it does not upload using a broader assumed policy.</div></div>${policyTable(local,'Local policy floor')}`;
      return;
    }
    card.innerHTML=`<h2>What would be shared?</h2>${policyTable(effective||local,payload.connected?'Effective local + organization policy':'Local policy — no organization connected')}<div class="muted" style="margin-top:10px">Typed text, clipboard contents, ordinary key identities, password values, and screenshot bytes are never shared by the Gateway path.</div>`;
  }

  function policyTable(policy,title) {
    const allowed=policy.deny_all_event_types ? 'None' : (policy.allowed_event_types||[]).length ? (policy.allowed_event_types||[]).join(', ') : 'All privacy-hardened event types';
    const stripped=(policy.strip_metadata_keys||[]).length ? (policy.strip_metadata_keys||[]).join(', ') : 'None beyond baseline privacy hardening';
    return `<div class="muted" style="margin:4px 0 9px">${escapeHtml(title)}</div><div class="table-wrap"><table style="min-width:0"><tbody><tr><th>Window titles</th><td>${yn(policy.share_window_titles)}</td></tr><tr><th>Safe metadata</th><td>${yn(policy.share_metadata)}</td></tr><tr><th>Locally excluded events</th><td>${yn(policy.share_excluded)}</td></tr><tr><th>Allowed event types</th><td>${escapeHtml(allowed)}</td></tr><tr><th>Additional stripped metadata</th><td>${escapeHtml(stripped)}</td></tr></tbody></table></div>`;
  }

  function escapeHtml(value) {
    if (typeof window.esc==='function') return window.esc(String(value??''));
    const node=document.createElement('div'); node.textContent=String(value??''); return node.innerHTML;
  }

  let policyLoading=false;
  async function refreshSharingPolicy() {
    if (policyLoading) return;
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if (active!=='organization') return;
    policyLoading=true;
    try {
      await window.__owgAuthReady;
      const response=await fetch('/v1/sharing-policy',{cache:'no-store'});
      if (!response.ok) throw new Error('policy unavailable');
      renderPolicy(await response.json());
    } catch (_) {
      const card=findSharingCard();
      if (card) card.innerHTML='<h2>What would be shared?</h2><div class="muted">Could not read the effective sharing policy.</div>';
    } finally { policyLoading=false; }
  }
  window.refreshSharingPolicy=refreshSharingPolicy;

  let agentLoading=false;
  let lastAgentPayload=null;

  function ensureAgentDashboardSurface() {
    const tabs=document.querySelector('.tabs');
    const connectTab=document.querySelector('#tab-connect');
    const main=document.querySelector('main');
    const connectPanel=document.querySelector('#panel-connect');
    if (!tabs || !main || document.querySelector('#tab-agents')) return;

    const tab=document.createElement('button');
    tab.setAttribute('role','tab');
    tab.id='tab-agents';
    tab.setAttribute('aria-controls','panel-agents');
    tab.setAttribute('aria-selected','false');
    tab.dataset.tab='agents';
    tab.textContent='Agents';
    tabs.insertBefore(tab,connectTab||null);

    const panel=document.createElement('section');
    panel.className='tabpanel';
    panel.setAttribute('role','tabpanel');
    panel.id='panel-agents';
    panel.setAttribute('aria-labelledby','tab-agents');
    panel.dataset.panel='agents';
    panel.hidden=true;
    panel.innerHTML=`
      <div class="metrics agent-metrics">
        <div class="metric-card"><div class="metric" id="agentRunCount">—</div><div class="label">Agent runs loaded</div></div>
        <div class="metric-card"><div class="metric" id="agentSuccessCount">—</div><div class="label">Successful</div></div>
        <div class="metric-card"><div class="metric" id="agentFailureCount">—</div><div class="label">Error / denied / cancelled</div></div>
        <div class="metric-card"><div class="metric" id="agentToolCount">—</div><div class="label">Tool calls</div></div>
        <div class="metric-card"><div class="metric" id="agentApprovalCount">—</div><div class="label">Approval requests</div></div>
        <div class="metric-card"><div class="metric" id="agentHandoffCount">—</div><div class="label">Handoffs</div></div>
      </div>
      <div class="card"><h2>Agent reports</h2><div class="muted">Provider-neutral aggregates over privacy-safe structural execution evidence. These are observed runs, not productivity scores.</div><div id="agentReportTable" class="table-wrap" style="margin-top:10px"></div></div>
      <div class="card"><div style="display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap"><div><h2>Recent agent runs</h2><div class="muted">Click a run to inspect model calls, tool calls, handoffs, approvals, errors, context-delivery assertions and observation coverage.</div></div><button id="agentRefreshButton" class="secondary" type="button">Refresh</button></div><div class="note" style="margin-top:12px"><strong>Coverage is evidence-presence based.</strong> A missing signal means <em>not observed</em>, not that the underlying agent did not perform it. Hidden reasoning is never reported as observable.</div><div id="agentRunsTable" class="table-wrap" style="margin-top:10px"></div><div id="agentCoverageNote" class="muted" style="margin-top:8px"></div></div>`;
    main.insertBefore(panel,connectPanel||null);

    if (!document.querySelector('#agent-observability-style')) {
      const style=document.createElement('style');
      style.id='agent-observability-style';
      style.textContent='.agent-metrics{grid-template-columns:repeat(6,minmax(0,1fr))}.agent-status{display:inline-flex;border-radius:999px;padding:3px 7px;font-size:11px;font-weight:750;background:#f0f1ed}.agent-status.success{background:#edf7f0;color:#285e42}.agent-status.error,.agent-status.denied,.agent-status.cancelled{background:#fff0ef;color:#8b3232}.agent-coverage{font-size:11px}.agent-step{padding:9px 0;border-bottom:1px solid #eceee8;display:grid;grid-template-columns:120px 1fr;gap:10px}.agent-step:last-child{border-bottom:0}@media(max-width:1000px){.agent-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:620px){.agent-metrics{grid-template-columns:1fr 1fr}.agent-step{grid-template-columns:1fr}}';
      document.head.appendChild(style);
    }
  }

  function executionDuration(execution) {
    const start=Date.parse(String(execution?.started_at||''));
    const end=Date.parse(String(execution?.ended_at||''));
    return Number.isFinite(start)&&Number.isFinite(end)&&end>=start ? (end-start)/1000 : 0;
  }

  function operationCount(execution,name) {
    return Number((execution?.operation_counts||{})[name]||0);
  }

  function agentIdentity(execution) {
    const agent=execution?.agent||{};
    return {
      name:String(agent.name||'Agent'),
      provider:String(agent.provider||''),
      framework:String(agent.framework||''),
    };
  }

  function bindTraceButtons() {
    document.querySelectorAll('[data-agent-execution]').forEach(button=>{
      button.onclick=()=>openAgentExecutionTrace(button.dataset.agentExecution||'');
    });
  }

  function renderAgentDashboard(payload) {
    lastAgentPayload=payload;
    const runs=Array.isArray(payload?.executions)?payload.executions:[];
    const success=runs.filter(x=>x.outcome_status==='success').length;
    const failures=runs.filter(x=>['error','denied','cancelled'].includes(String(x.outcome_status||''))).length;
    const tools=runs.reduce((n,x)=>n+operationCount(x,'tool_call'),0);
    const approvals=runs.reduce((n,x)=>n+Number(x.approval_request_count||0),0);
    const handoffs=runs.reduce((n,x)=>n+operationCount(x,'handoff'),0);
    document.querySelector('#agentRunCount').textContent=String(runs.length);
    document.querySelector('#agentSuccessCount').textContent=String(success);
    document.querySelector('#agentFailureCount').textContent=String(failures);
    document.querySelector('#agentToolCount').textContent=String(tools);
    document.querySelector('#agentApprovalCount').textContent=String(approvals);
    document.querySelector('#agentHandoffCount').textContent=String(handoffs);

    const grouped=new Map();
    for (const run of runs) {
      const id=agentIdentity(run);
      const key=`${id.name}\u0000${id.provider}\u0000${id.framework}`;
      const row=grouped.get(key)||{...id,runs:0,success:0,failures:0,tools:0,approvals:0,handoffs:0,duration:0,durationCount:0,levels:new Set()};
      row.runs+=1;
      if (run.outcome_status==='success') row.success+=1;
      if (['error','denied','cancelled'].includes(String(run.outcome_status||''))) row.failures+=1;
      row.tools+=operationCount(run,'tool_call');
      row.approvals+=Number(run.approval_request_count||0);
      row.handoffs+=operationCount(run,'handoff');
      const duration=executionDuration(run); if (duration>0) { row.duration+=duration; row.durationCount+=1; }
      for (const level of (run.observed_coverage?.observation_levels_observed||[])) row.levels.add(String(level));
      grouped.set(key,row);
    }
    const reportRows=[...grouped.values()].sort((a,b)=>b.runs-a.runs);
    const report=document.querySelector('#agentReportTable');
    report.innerHTML=reportRows.length?`<table><thead><tr><th>Agent</th><th>Runs</th><th>Successful</th><th>Failures</th><th>Tool calls</th><th>Approvals</th><th>Avg duration</th><th>Observed level</th></tr></thead><tbody>${reportRows.map(row=>`<tr><td><strong>${escapeHtml(row.name)}</strong><div class="muted">${escapeHtml([row.provider,row.framework].filter(Boolean).join(' · ')||'Provider/framework not reported')}</div></td><td>${row.runs}</td><td>${row.success}</td><td>${row.failures}</td><td>${row.tools}</td><td>${row.approvals}</td><td>${row.durationCount?fmtTime(row.duration/row.durationCount):'—'}</td><td>${[...row.levels].map(x=>`<span class="pill">${escapeHtml(x)}</span>`).join('')||'<span class="muted">not reported</span>'}</td></tr>`).join('')}</tbody></table>`:'<div class="muted">No native or instrumented agent runs have been observed yet. Connect an agent adapter and its runs will appear here automatically.</div>';

    const runsHost=document.querySelector('#agentRunsTable');
    runsHost.innerHTML=runs.length?`<table><thead><tr><th>Started</th><th>Agent</th><th>Status</th><th>Duration</th><th>Observed work</th><th>Coverage</th><th></th></tr></thead><tbody>${runs.map(run=>{const id=agentIdentity(run);const status=String(run.outcome_status||'unknown');const coverage=run.observed_coverage||{};const levels=coverage.observation_levels_observed||[];const complete=run.complete_boundary_observed===true;return `<tr><td>${run.started_at?escapeHtml(new Date(run.started_at).toLocaleString()):'—'}</td><td><strong>${escapeHtml(id.name)}</strong><div class="muted">${escapeHtml([id.provider,id.framework].filter(Boolean).join(' · '))}</div></td><td><span class="agent-status ${escapeHtml(status)}">${escapeHtml(status)}</span></td><td>${executionDuration(run)?fmtTime(executionDuration(run)):'—'}</td><td>${operationCount(run,'model_call')} model · ${operationCount(run,'tool_call')} tools · ${operationCount(run,'handoff')} handoffs<div class="muted">${Number(run.approval_request_count||0)} approval requests · ${operationCount(run,'error')} explicit errors</div></td><td><span class="agent-coverage">${complete?'Complete observed boundaries':'Partial observed boundaries'}</span><div class="muted">${escapeHtml(levels.join(', ')||run.observation_level||'unknown')}</div></td><td><button class="secondary" type="button" data-agent-execution="${escapeHtml(run.execution_id||'')}">View trace</button></td></tr>`;}).join('')}</tbody></table>`:'<div class="muted">No agent executions are available in the current evidence window.</div>';
    bindTraceButtons();
    const considered=Number(payload?.agent_execution_count_considered||runs.length);
    document.querySelector('#agentCoverageNote').textContent=considered>runs.length?`Showing ${runs.length} of ${considered} observed executions in this bounded view.`:`${runs.length} observed execution${runs.length===1?'':'s'} in this bounded view.`;
  }

  function openAgentExecutionTrace(executionId) {
    const run=(lastAgentPayload?.executions||[]).find(item=>String(item.execution_id||'')===String(executionId||''));
    if (!run) return;
    const id=agentIdentity(run);
    const coverage=run.observed_coverage||{};
    const observed=(coverage.observed_signal_names||[]).map(x=>`<span class="pill">${escapeHtml(x)}</span>`).join('')||'<span class="muted">No structural signal classes reported.</span>';
    const unobserved=(coverage.unobserved_signal_names||[]).map(x=>escapeHtml(x)).join(', ')||'None';
    const events=(run.events||[]).map(event=>{const tool=event.tool||{};const usage=event.usage||{};const detail=[event.model?`model ${escapeHtml(event.model)}`:'',tool.name?`tool ${escapeHtml(tool.name)}`:'',tool.category&&tool.category!=='none'?escapeHtml(tool.category):'',event.structural_step?escapeHtml(event.structural_step):''].filter(Boolean).join(' · ');const context=event.task_context||{};const contextLine=context.handoff_status&&context.handoff_status!=='not_asserted'?`<div class="muted">Context handoff: ${escapeHtml(context.handoff_status)} · model consumption attested: ${context.model_context_consumption_attested?'yes':'no'}</div>`:'';const tokenLine=Object.keys(usage).length?`<div class="muted">Tokens: ${Object.entries(usage).map(([k,v])=>`${escapeHtml(k)} ${Number(v)||0}`).join(' · ')}</div>`:'';return `<div class="agent-step"><div><strong>${event.observed_at?escapeHtml(new Date(event.observed_at).toLocaleTimeString()):''}</strong><div class="muted">${fmtTime(event.duration_seconds||0)}</div></div><div><strong>${escapeHtml(event.operation||'unknown')}</strong> <span class="agent-status ${escapeHtml(event.status||'unknown')}">${escapeHtml(event.status||'unknown')}</span>${detail?`<div>${detail}</div>`:''}${tokenLine}${contextLine}</div></div>`;}).join('')||'<div class="muted">No event rows were returned for this run.</div>';
    openModal(`${id.name} execution`,'Agent trace',`<div class="note"><strong>${escapeHtml(run.outcome_status||'unknown')}</strong> · ${executionDuration(run)?fmtTime(executionDuration(run)):'duration not observed'} · ${Number(run.event_count_total||0)} structural events${run.events_truncated?' · event list truncated':''}</div><h3 style="margin-top:16px">Observed coverage</h3><div>${observed}</div><div class="muted" style="margin-top:7px">Not observed: ${unobserved}. Missing signals are not proof of non-occurrence. Hidden reasoning observed: no.</div><h3 style="margin-top:18px">Execution timeline</h3>${events}`);
  }
  window.openAgentExecutionTrace=openAgentExecutionTrace;

  async function refreshAgentDashboard(force=false) {
    if (agentLoading) return;
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if (!force && active!=='agents') return;
    agentLoading=true;
    try {
      await window.__owgAuthReady;
      const response=await fetch('/v1/agent-execution-traces?limit=50&evidence_limit=25000&max_events_per_execution=100',{cache:'no-store'});
      if (!response.ok) throw new Error('agent traces unavailable');
      renderAgentDashboard(await response.json());
    } catch (_) {
      const host=document.querySelector('#agentRunsTable');
      if (host) host.innerHTML='<div class="muted">Could not read agent execution reports from the local observer.</div>';
    } finally { agentLoading=false; }
  }
  window.refreshAgentDashboard=refreshAgentDashboard;

  function restyleVisibleSurfaces() {
    document.querySelectorAll('.surface-dot').forEach(dot=>{
      const text=(dot.parentElement?.textContent||'').replace(/^[·\s]+/,'').trim();
      if (text) dot.style.setProperty('--surface',unifiedSurfaceColor(text));
    });
  }

  function polish() {
    removeDeveloperNotes();
    polishTimeline();
    restyleVisibleSurfaces();
  }

  ensureAgentDashboardSurface();

  const observer=new MutationObserver(()=>polish());
  function install() {
    polish();
    observer.observe(document.body,{childList:true,subtree:true});
    document.querySelector('#tab-organization')?.addEventListener('click',()=>setTimeout(refreshSharingPolicy,0));
    const agentTab=document.querySelector('#tab-agents');
    agentTab?.addEventListener('click',()=>setTimeout(()=>refreshAgentDashboard(true),0));
    agentTab?.addEventListener('focus',()=>{if(agentTab.getAttribute('aria-selected')==='true')setTimeout(()=>refreshAgentDashboard(true),0);});
    document.querySelector('#agentRefreshButton')?.addEventListener('click',()=>refreshAgentDashboard(true));
    refreshSharingPolicy();
    refreshAgentDashboard();
    setInterval(()=>{ if (!document.hidden) { polish(); refreshSharingPolicy(); refreshAgentDashboard(); } },5000);
    if (window.__owgLastSummary && typeof window.renderActive==='function') window.renderActive(window.__owgLastSummary);
    if (typeof window.refreshOverviewDerived==='function') window.refreshOverviewDerived();
  }

  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();