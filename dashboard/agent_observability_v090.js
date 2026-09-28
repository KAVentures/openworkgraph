(() => {
  // Shared with the other Agents-tab script: both must request the same run list.
  const agentHistoryParam=()=>{try{return localStorage.getItem('owg_show_disconnected_agents')==='1'?'':'&hide_disconnected=true';}catch(_){return '&hide_disconnected=true';}};
  let latest=null;
  let loading=false;
  let patchScheduled=false;

  const esc=value=>{
    if(typeof window.esc==='function')return window.esc(String(value??''));
    const node=document.createElement('div');node.textContent=String(value??'');return node.innerHTML;
  };
  const fmt=value=>typeof window.fmtTime==='function'?window.fmtTime(value):`${Math.round(Number(value)||0)}s`;

  function count(run,name){
    const source=run?.observed_operation_counts||run?.operation_counts||{};
    return Number(source[name]||0);
  }

  function capability(run,name){
    const value=run?.signal_capabilities?.[name];
    return value&&typeof value==='object'?value:{status:'unknown',basis:'adapter_capability_not_declared'};
  }

  function supported(run,name){
    return ['observable','partial'].includes(capability(run,name).status);
  }

  function signalText(run,name,label){
    const cap=capability(run,name);
    if(!['observable','partial'].includes(cap.status)){
      return `<span class="muted" title="${esc(cap.basis||'Signal not observable')}">— ${esc(label)}</span>`;
    }
    const qualifier=cap.status==='partial'?'≈ ':'';
    return `<span title="${esc(cap.basis||'Observed structural signal')}">${qualifier}${count(run,name)} ${esc(label)}</span>`;
  }

  function tokenText(run){
    const cap=capability(run,'token_usage');
    if(!['observable','partial'].includes(cap.status))return '';
    const usage=run?.usage_totals||{};
    const total=Number(usage.total_tokens||0);
    if(!total&&!Object.keys(usage).length)return cap.status==='partial'?'<span class="muted">tokens not reported on this run</span>':'<span class="muted">0 tokens observed</span>';
    const input=Number(usage.input_tokens||0),output=Number(usage.output_tokens||0),cached=Number(usage.cached_input_tokens||0);
    return `<span class="muted">${total.toLocaleString()} tokens${input||output?` · ${input.toLocaleString()} in · ${output.toLocaleString()} out`:''}${cached?` · ${cached.toLocaleString()} cached`:''}</span>`;
  }

  function modelText(run){
    const models=Array.isArray(run?.models_observed)?run.models_observed.filter(Boolean):[];
    if(models.length)return `<span class="muted">${models.map(esc).join(' · ')}</span>`;
    const cap=capability(run,'model_identity');
    return ['not_observable','unknown'].includes(cap.status)?'<span class="muted">model identity not observable</span>':'';
  }

  function depthLabel(run){
    const value=String(run?.telemetry_depth||'');
    const labels={
      rich_native_events_plus_hooks:'rich native + hooks',
      rich_native_events:'rich native',
      hooks_only:'hooks only',
      provider_neutral_structural:'portable structural',
    };
    return labels[value]||value.replaceAll('_',' ')||'structural telemetry';
  }

  function aggregate(runs,name){
    const observable=runs.filter(run=>supported(run,name));
    if(!observable.length)return {text:'—',title:'This signal is not observable with the integrations in this view.'};
    const total=observable.reduce((sum,run)=>sum+count(run,name),0);
    const mixed=observable.length!==runs.length;
    return {text:`${total}${mixed?'*':''}`,title:mixed?'Some runs use integrations that cannot observe this signal.':'Observed structural events.'};
  }

  function patchMetrics(runs){
    const mappings=[
      ['agentToolCount','tool_call'],
      ['agentApprovalCount','human_approval_requested'],
      ['agentHandoffCount','handoff'],
    ];
    for(const [id,key] of mappings){
      const el=document.querySelector(`#${id}`);if(!el)continue;
      const value=aggregate(runs,key);el.textContent=value.text;el.title=value.title;
    }
  }

  function groupRuns(runs){
    const grouped=new Map();
    for(const run of runs){
      const agent=run?.agent||{};
      const name=String(agent.name||'Agent'),provider=String(agent.provider||''),framework=String(agent.framework||'');
      const key=`${name}\u0000${provider}\u0000${framework}`;
      const row=grouped.get(key)||{name,provider,framework,runs:[],success:0,failures:0,duration:0,durationCount:0};
      row.runs.push(run);
      if(run.outcome_status==='success')row.success+=1;
      if(['error','denied','cancelled'].includes(String(run.outcome_status||'')))row.failures+=1;
      const start=Date.parse(String(run.started_at||'')),end=Date.parse(String(run.ended_at||''));
      if(Number.isFinite(start)&&Number.isFinite(end)&&end>=start){row.duration+=(end-start)/1000;row.durationCount+=1;}
      grouped.set(key,row);
    }
    return [...grouped.values()].sort((a,b)=>b.runs.length-a.runs.length);
  }

  function groupedSignal(row,name){
    const value=aggregate(row.runs,name);
    return `<span title="${esc(value.title)}">${esc(value.text)}</span>`;
  }

  function groupedTokens(row){
    const supportedRuns=row.runs.filter(run=>supported(run,'token_usage'));
    if(!supportedRuns.length)return '<span class="muted">—</span>';
    const total=supportedRuns.reduce((sum,run)=>sum+Number(run?.usage_totals?.total_tokens||0),0);
    return total?total.toLocaleString():'0';
  }

  function patchReports(runs){
    const host=document.querySelector('#agentReportTable');
    if(!host||host.querySelector('.owg-rich-agent-report'))return;
    const rows=groupRuns(runs);
    if(!rows.length)return;
    host.innerHTML=`<table class="owg-rich-agent-report"><thead><tr><th>Agent</th><th>Runs</th><th>Successful</th><th>Failures</th><th>Model calls</th><th>Tool calls</th><th>Handoffs</th><th>Approval requests</th><th>Tokens</th><th>Avg duration</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.name)}</strong><div class="muted">${esc([row.provider,row.framework].filter(Boolean).join(' · ')||'Provider/framework not reported')}</div></td><td>${row.runs.length}</td><td>${row.success}</td><td>${row.failures}</td><td>${groupedSignal(row,'model_call')}</td><td>${groupedSignal(row,'tool_call')}</td><td>${groupedSignal(row,'handoff')}</td><td>${groupedSignal(row,'human_approval_requested')}</td><td>${groupedTokens(row)}</td><td>${row.durationCount?fmt(row.duration/row.durationCount):'—'}</td></tr>`).join('')}</tbody></table>`;
  }

  function patchRunRows(runs){
    const byId=new Map(runs.map(run=>[String(run.execution_id||''),run]));
    const table=document.querySelector('#agentRunsTable table');
    if(!table||table.classList.contains('owg-capability-aware'))return;
    for(const button of table.querySelectorAll('[data-agent-execution]')){
      const run=byId.get(String(button.dataset.agentExecution||''));
      const row=button.closest('tr');if(!run||!row)continue;
      const cells=row.querySelectorAll('td');if(cells.length<6)continue;
      cells[4].innerHTML=`<div>${signalText(run,'model_call','model')} · ${signalText(run,'tool_call','tools')} · ${signalText(run,'handoff','handoffs')}</div><div class="muted">${signalText(run,'human_approval_requested','approval requests')} · ${count(run,'error')} explicit errors</div>${modelText(run)?`<div>${modelText(run)}</div>`:''}${tokenText(run)?`<div>${tokenText(run)}</div>`:''}`;
      const existing=cells[5].innerHTML;
      cells[5].innerHTML=`${existing}<div class="muted" style="margin-top:4px">${esc(depthLabel(run))}</div>`;
    }
    table.classList.add('owg-capability-aware');
  }

  function patchNote(){
    const note=document.querySelector('#agentCoverageNote');
    if(!note)return;
    const base=String(note.textContent||'').replace(/\s*0 = observed zero\..*$/,'').trim();
    note.textContent=`${base}${base?' ':''}0 = observed zero. — = this integration cannot observe that signal; it does not mean zero runtime activity.`;
  }

  function patchHiddenNote(){
    const note=document.querySelector('#agentHiddenNote');if(!note)return;
    const names={'claude-code':'Claude Code','codex':'Codex','github-copilot':'GitHub Copilot','gemini-cli':'Gemini CLI','cursor':'Cursor'};
    const hidden=(latest.hidden_frameworks||[]).map(f=>names[f]||f);
    note.textContent=hidden.length?`Past runs from ${(hidden.length>1?hidden.slice(0,-1).join(', ')+' and '+hidden[hidden.length-1]:hidden[0])} are hidden because Observe is off for ${hidden.length>1?'them':'it'}. Nothing was deleted.`:'';
  }

  function patch(){
    patchScheduled=false;
    if(!latest)return;
    patchHiddenNote();
    const runs=Array.isArray(latest.executions)?latest.executions:[];
    patchMetrics(runs);patchReports(runs);patchRunRows(runs);patchNote();
  }

  function schedulePatch(){
    if(patchScheduled)return;patchScheduled=true;requestAnimationFrame(patch);
  }

  async function refresh(force=false){
    if(loading)return;
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if(!force&&active!=='agents')return;
    loading=true;
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/agent-execution-traces?limit=50&evidence_limit=25000&max_events_per_execution=100'+agentHistoryParam(),{cache:'no-store'});
      if(response.ok){latest=await response.json();schedulePatch();}
    }finally{loading=false;}
  }

  window.refreshAgentObservability=force=>{latest=null;return refresh(force);};

  function install(){
    document.querySelector('#tab-agents')?.addEventListener('click',()=>setTimeout(()=>refresh(true),0));
    document.querySelector('#agentRefreshButton')?.addEventListener('click',()=>setTimeout(()=>refresh(true),30));
    const observer=new MutationObserver(()=>{
      if(!latest)return;
      const report=document.querySelector('#agentReportTable');
      const table=document.querySelector('#agentRunsTable table');
      if((report&&!report.querySelector('.owg-rich-agent-report'))||(table&&!table.classList.contains('owg-capability-aware')))schedulePatch();
    });
    observer.observe(document.body,{childList:true,subtree:true});
    refresh(false);
    setInterval(()=>{if(!document.hidden)refresh(false);},5000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
