(() => {
  let setupCache=null;
  let statusLoading=false;
  let configState={};
  const ONE_CLICK={claude_code:'Claude Code',codex:'Codex'};

  const h=value=>{
    const node=document.createElement('div');
    node.textContent=String(value??'');
    return node.innerHTML;
  };

  function installStyle(){
    if(document.querySelector('#agent-control-plane-style'))return;
    const style=document.createElement('style');
    style.id='agent-control-plane-style';
    style.textContent=`
      .connection-section{margin-bottom:13px}.connection-section h2{margin-bottom:5px}
      .agent-connect-status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:760;background:#f0f1ed;color:#555c55;margin-bottom:10px}
      .agent-connect-status.live{background:#edf7f0;color:#285e42}.agent-connect-status .dot{width:7px;height:7px;border-radius:50%;background:currentColor}
      .setup-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.setup-card{border:1px solid var(--line);border-radius:14px;padding:15px;background:#fafbf8;display:flex;flex-direction:column;min-height:205px}.setup-card h3{font-size:15px;margin:0 0 5px}.setup-card p{font-size:12.5px;color:var(--muted);line-height:1.45;margin:0 0 12px}.setup-card button{width:100%}.setup-actions{margin-top:auto;display:flex;flex-direction:column;gap:6px}.setup-card .linkish{background:none;border:0;color:var(--muted);font-size:12px;text-decoration:underline;padding:2px;cursor:pointer}.agent-config-line{font-size:11.5px;color:var(--muted);margin:-4px 0 10px}.agent-config-line.on{color:#285e42;font-weight:700}.setup-tag{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:#666e66;margin-bottom:8px}.setup-privacy{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}.setup-privacy div{padding:8px 10px;border-radius:10px;background:#f5f6f2;font-size:12px}.agent-control-intro{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
      @media(max-width:1000px){.setup-grid{grid-template-columns:1fr 1fr}}@media(max-width:620px){.setup-grid,.setup-privacy{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function ensureConnectSections(){
    const panel=document.querySelector('#panel-connect');
    if(!panel)return;
    const tab=document.querySelector('#tab-connect');
    if(tab)tab.textContent='Connect';
    const grid=panel.querySelector('.connect-grid');
    if(grid&&!grid.id)grid.id='ai-context-grid';

    if(!document.querySelector('#ai-context-intro')){
      const intro=document.createElement('div');
      intro.id='ai-context-intro';intro.className='card connection-section';
      intro.innerHTML=`<h2>Give AI my context</h2><div class="muted">Connect an AI client to OpenWorkGraph through MCP so it can read the privacy-hardened work context you allow. This does <strong>not</strong> automatically let OpenWorkGraph observe how that agent executes its own work.</div>`;
      panel.insertBefore(intro,panel.firstChild);
    }

    if(!document.querySelector('#agent-observation-setup')){
      const section=document.createElement('div');
      section.id='agent-observation-setup';section.className='card connection-section';
      section.innerHTML=`
        <div class="agent-control-intro"><div><h2>Observe an agent</h2><div class="muted">Instrument an agent's native lifecycle or telemetry surface so OpenWorkGraph can measure structural execution: runs, models, tools, handoffs, approvals, failures, timings and coverage. Prompts, responses, reasoning, tool arguments and tool results are not collected.</div></div><button id="refreshAgentConnections" class="secondary" type="button">Check telemetry</button></div>
        <div class="note" style="margin-top:12px"><strong>Status means telemetry observed.</strong> The green badge appears only when evidence actually arrives. <strong>Connect</strong> adds OpenWorkGraph's entries to that agent's own settings file (a backup is written first and your other settings are kept); <strong>Disconnect</strong> removes only what OpenWorkGraph added.</div>
        <div class="setup-grid" style="margin-top:12px">
          ${setupCard('claude_code','Hooks + OTel logs','Claude Code','Observe per-request model calls and tokens, tool use, approval requests/decisions, failures and subagent handoffs without capturing prompt or tool content.')}
          ${setupCard('codex','OTel logs + trace','Codex','Observe structural API, tool, approval and multi-agent events plus trace hierarchy. Content-bearing log options stay disabled.')}
          ${setupCard('openai_agents','Tracing processor','OpenAI Agents SDK','Register OpenWorkGraph as an additional tracing processor for model, tool, handoff, hierarchy, usage and timing signals.')}
          ${setupCard('otel','Provider-neutral','OpenTelemetry / custom','Send portable GenAI OTLP/HTTP JSON traces or canonical structural events from another agent runtime.')}
        </div>`;
      const anchor=grid||panel.lastElementChild;
      if(anchor&&anchor.parentNode===panel)anchor.insertAdjacentElement('afterend',section);else panel.appendChild(section);
      section.querySelector('#refreshAgentConnections').onclick=()=>refreshObservationStatuses(true);
      section.querySelectorAll('[data-agent-setup]').forEach(button=>button.onclick=()=>openAgentSetup(button.dataset.agentSetup||''));
      section.querySelectorAll('[data-agent-connect]').forEach(button=>button.onclick=()=>toggleAgentConfig(button.dataset.agentConnect||'',button));
      refreshConfigState();
    }
  }

  function setupCard(kind,tag,title,description){
    const oneClick=kind in ONE_CLICK;
    const actions=oneClick
      ?`<button type="button" data-agent-connect="${h(kind)}" id="agent-connect-${h(kind)}">Connect</button><button class="linkish" type="button" data-agent-setup="${h(kind)}">Manual setup</button>`
      :`<button class="secondary" type="button" data-agent-setup="${h(kind)}">Show setup code</button>`;
    const configLine=oneClick
      ?`<div class="agent-config-line" id="agent-config-${h(kind)}">Checking…</div>`
      :`<div class="agent-config-line">Added in your own code, so it can't be one-click.</div>`;
    return `<div class="setup-card"><div class="setup-tag">${h(tag)}</div><h3>${h(title)}</h3><div class="agent-connect-status" id="agent-status-${h(kind)}"><span class="dot"></span><span>No telemetry observed</span></div>${configLine}<p>${h(description)}</p><div class="setup-actions">${actions}</div></div>`;
  }

  function renderConfigState(){
    for(const kind of Object.keys(ONE_CLICK)){
      const state=configState[kind]||{};
      const line=document.querySelector(`#agent-config-${kind}`),button=document.querySelector(`#agent-connect-${kind}`);
      if(line){
        line.classList.toggle('on',!!state.configured);
        line.textContent=state.error?'Settings file needs attention — use manual setup':state.configured?'✓ Connected in settings':'Not connected';
        line.title=state.path||'';
      }
      if(button){
        button.disabled=false;
        button.textContent=state.configured?'Disconnect':'Connect';
        button.className=state.configured?'secondary':'';
      }
    }
  }

  async function refreshConfigState(){
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/agent-config',{cache:'no-store'});
      if(response.ok){configState=(await response.json()).integrations||{};renderConfigState();}
    }catch(_){}
  }

  async function toggleAgentConfig(kind,button){
    const label=ONE_CLICK[kind]||'Agent';
    const action=configState[kind]?.configured?'disconnect':'connect';
    button.disabled=true;button.textContent=action==='connect'?'Connecting…':'Disconnecting…';
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/agent-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:kind,action}),cache:'no-store'});
      const result=await response.json().catch(()=>({}));
      if(!response.ok){
        await refreshConfigState();
        window.openModal?.(`Couldn't ${action} ${label} automatically`,'Agent observation',`<p>${h(result.detail||'The settings file could not be changed safely.')}</p><div class="note">Nothing was changed. You can still set it up by hand.</div><div class="modal-actions"><button type="button" id="agentManualFallback">Manual setup</button></div>`);
        const fallback=document.querySelector('#agentManualFallback');if(fallback)fallback.onclick=()=>openAgentSetup(kind);
        return;
      }
      configState[kind]={configured:!!result.configured,path:result.path};
      renderConfigState();
      const where=result.path?`<code>${h(result.path)}</code>`:'its settings file';
      const backup=result.backup?`<div class="note">Backup of the previous file: <code>${h(result.backup)}</code></div>`:'';
      if(action==='connect')window.openModal?.(`${label} connected`,'Agent observation',`<p>OpenWorkGraph's observation settings were added to ${where}. ${h(result.note||'')}</p>${backup}<div class="note">The status turns green once the first telemetry arrives. Disconnect removes only OpenWorkGraph's entries.</div>`);
      else window.openModal?.(`${label} disconnected`,'Agent observation',`<p>OpenWorkGraph's entries were removed from ${where}. Your other settings were left as they were.</p>${backup}`);
    }catch(_){
      renderConfigState();
      window.openModal?.('Agent setup unavailable','Agent observation','<p>Could not reach the local OpenWorkGraph observer. Confirm it is running and reload the dashboard.</p>');
    }
  }
  window.toggleAgentConfig=toggleAgentConfig;

  function enhanceAgentsPanel(){
    const panel=document.querySelector('#panel-agents');
    if(!panel||document.querySelector('#agent-control-intro'))return;
    const intro=document.createElement('div');
    intro.id='agent-control-intro';intro.className='card';
    intro.innerHTML=`<div class="agent-control-intro"><div><h2>Agent observation</h2><div class="muted">Runs appear below when a native hook, tracing processor, OpenTelemetry exporter or custom structural adapter actually sends evidence to this local observer. MCP context access and agent observation are separate connections.</div></div><button id="openAgentSetup" type="button">Set up an agent</button></div>`;
    panel.insertBefore(intro,panel.firstChild);
    intro.querySelector('#openAgentSetup').onclick=()=>{window.activateTab?.('connect');setTimeout(()=>document.querySelector('#agent-observation-setup')?.scrollIntoView({behavior:'smooth',block:'start'}),0);};
  }

  async function loadSetup(){
    if(setupCache)return setupCache;
    await window.__owgAuthReady;
    const response=await fetch('/v1/agent-setup',{cache:'no-store'});
    if(!response.ok)throw new Error('agent setup unavailable');
    setupCache=await response.json();
    return setupCache;
  }

  function privacyHtml(payload){
    const p=payload?.privacy||{};
    return `<div class="setup-privacy"><div>✓ Structural execution only</div><div>✓ Write-only telemetry credential</div><div>${p.prompts_collected?'⚠':'✓'} Prompts not collected</div><div>${p.chain_of_thought_collected?'⚠':'✓'} Reasoning not collected</div><div>${p.tool_arguments_collected?'⚠':'✓'} Tool arguments not collected</div><div>${p.tool_results_collected?'⚠':'✓'} Tool results not collected</div></div>`;
  }

  function codeBox(text,id){return `<div class="codebox" id="${h(id)}">${h(text)}</div><div class="modal-actions"><button type="button" data-copy-id="${h(id)}">Copy</button></div>`;}

  async function openAgentSetup(kind){
    try{
      const payload=await loadSetup();
      const integrations=payload.integrations||{};
      let title='Observe an agent',body='';
      if(kind==='claude_code'){
        const x=integrations.claude_code||{};title='Observe Claude Code';
        body=`<p>Prefer the <strong>Connect</strong> button, which does this for you. To do it by hand, merge the <code>hooks</code> and <code>env</code> objects below into your Claude Code settings.</p>${privacyHtml(payload)}<h3>Dashboard-generated settings</h3>${codeBox(JSON.stringify(x.settings||{},null,2),'agentSetupCode')}<h3 style="margin-top:16px">Equivalent hook command</h3>${codeBox(x.command||'python -m adapters.claude_code_hook --print-settings','agentSetupCommand')}<div class="note">Hooks are asynchronous and fail-open. OTel content logging is explicitly disabled; the OWG ingest path independently strict-allowlists structural fields.</div>`;
      }else if(kind==='codex'){
        const x=integrations.codex||{};title='Observe Codex';
        body=`<p>Prefer the <strong>Connect</strong> button, which adds this for you unless you already have your own <code>[otel]</code> settings. To do it by hand, merge these keys into your existing <code>[otel]</code> section. The dashboard includes only the dedicated local write-only telemetry credential; it does not grant access to your OWG history.</p>${privacyHtml(payload)}${codeBox(x.config||'', 'agentSetupCode')}<div class="note">OpenWorkGraph enables structural Codex log events and trace export. User prompts, agent responses and guardian assessments remain disabled, and content-bearing fields are discarded server-side.</div>`;
      }else if(kind==='openai_agents'){
        const x=integrations.openai_agents||{};title='Observe OpenAI Agents SDK';
        body=`<p>Add OpenWorkGraph as an additional tracing processor in the agent application's Python environment.</p>${privacyHtml(payload)}${codeBox(x.python||'', 'agentSetupCode')}<div class="note">This does not replace existing SDK tracing and does not make OpenWorkGraph a dependency for the agent's control flow.</div>`;
      }else{
        const x=integrations.otel||{},custom=integrations.custom||{};title='Observe another agent';
        body=`<p>Use standard OTLP/HTTP JSON when the runtime supports it, or send OpenWorkGraph's canonical structural event envelope from a custom adapter.</p>${privacyHtml(payload)}<h3>POSIX / macOS / Linux</h3>${codeBox(x.posix||'', 'agentSetupPosix')}<h3 style="margin-top:16px">PowerShell</h3>${codeBox(x.powershell||'', 'agentSetupPowerShell')}<h3 style="margin-top:16px">Custom structural endpoint</h3>${codeBox(`${custom.endpoint||''}\nAuthorization: ${custom.authorization||''}`, 'agentSetupCustom')}<div class="note">Use the traces endpoint exactly as shown. The generic OWG endpoint currently accepts OTLP/HTTP JSON, not protobuf or gRPC.</div>`;
      }
      window.openModal?.(title,'Agent observation',body);
      document.querySelectorAll('[data-copy-id]').forEach(button=>button.onclick=()=>copyElement(button.dataset.copyId||'',button));
    }catch(_){window.openModal?.('Agent setup unavailable','Agent observation','<p>Could not generate local setup material. Confirm the OpenWorkGraph observer is still running and reload the dashboard.</p>');}
  }
  window.openAgentSetup=openAgentSetup;

  async function copyElement(id,button){
    const text=document.querySelector(`#${CSS.escape(id)}`)?.textContent||'';
    try{await navigator.clipboard.writeText(text);const before=button.textContent;button.textContent='Copied';setTimeout(()=>button.textContent=before,1200);}catch(_){window.prompt('Copy this:',text);}
  }

  function classifyRun(run){
    const agent=run?.agent||{};
    const text=`${agent.name||''} ${agent.provider||''} ${agent.framework||''}`.toLowerCase();
    if(text.includes('claude'))return 'claude_code';
    if(text.includes('codex'))return 'codex';
    if(text.includes('openai-agents')||text.includes('openai agents'))return 'openai_agents';
    return 'otel';
  }

  function relativeTime(value){
    const t=Date.parse(String(value||''));if(!Number.isFinite(t))return '';
    const seconds=Math.max(0,Math.round((Date.now()-t)/1000));
    if(seconds<60)return `${seconds}s ago`;if(seconds<3600)return `${Math.floor(seconds/60)}m ago`;if(seconds<86400)return `${Math.floor(seconds/3600)}h ago`;return `${Math.floor(seconds/86400)}d ago`;
  }

  function renderStatuses(payload){
    const latest={};
    for(const run of (payload?.executions||[])){
      const kind=classifyRun(run),at=run.ended_at||run.started_at||'';
      if(!latest[kind]||String(at)>String(latest[kind]))latest[kind]=at;
    }
    for(const kind of ['claude_code','codex','openai_agents','otel']){
      const el=document.querySelector(`#agent-status-${kind}`);if(!el)continue;
      const when=latest[kind];el.classList.toggle('live',!!when);
      const label=el.querySelector('span:last-child');if(label)label.textContent=when?`Telemetry observed · ${relativeTime(when)}`:'No telemetry observed';
    }
  }

  async function refreshObservationStatuses(force=false){
    if(statusLoading)return;
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if(!force&&active!=='connect'&&active!=='agents')return;
    statusLoading=true;
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/agent-execution-traces?limit=50&evidence_limit=25000&max_events_per_execution=1',{cache:'no-store'});
      if(response.ok)renderStatuses(await response.json());
    }finally{statusLoading=false;}
  }
  window.refreshAgentObservationStatuses=refreshObservationStatuses;

  function install(){
    installStyle();ensureConnectSections();enhanceAgentsPanel();refreshObservationStatuses(true);
    const connect=document.querySelector('#tab-connect'),agents=document.querySelector('#tab-agents');
    connect?.addEventListener('click',()=>setTimeout(()=>{refreshObservationStatuses(true);refreshConfigState();},0));
    agents?.addEventListener('click',()=>setTimeout(()=>refreshObservationStatuses(true),0));
    setInterval(()=>{if(!document.hidden)refreshObservationStatuses(false);},5000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();