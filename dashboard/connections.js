(() => {
  // One table for every AI app: "Context" (MCP read access) and "Observe"
  // (structural execution telemetry), each a single on/off switch. Backed by
  // /v1/connections, the same code as the owg_connect.py CLI agents can use.
  let state=null;
  let aiAccess=null;
  let diagnostics=null;
  let busy=new Set();
  let lastMessage={};

  const esc=value=>{const node=document.createElement('div');node.textContent=String(value??'');return node.innerHTML;};

  function installStyle(){
    if(document.querySelector('#owg-connections-style'))return;
    const style=document.createElement('style');
    style.id='owg-connections-style';
    style.textContent=`
      #owgConnections{margin-bottom:13px}
      #owgConnections .conn-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
      #owgConnections .conn-master{display:flex;align-items:center;gap:8px;font-size:12.5px;border:1px solid var(--line);border-radius:999px;padding:5px 6px 5px 12px;background:#fafbf8}
      #owgConnections table{width:100%;min-width:0;border-collapse:collapse;margin-top:12px}
      #owgConnections th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:left;font-weight:750;padding:6px 8px;border-bottom:1px solid var(--line)}
      #owgConnections td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:middle;font-size:13px}
      #owgConnections tr:last-child td{border-bottom:0}
      #owgConnections .app{font-weight:750}
      #owgConnections .sub{font-size:11.5px;color:var(--muted);margin-top:2px}
      #owgConnections .sub.warn{color:#8a5a00}
      #owgConnections .sw{position:relative;width:42px;min-width:42px;height:24px;min-height:0;border-radius:999px;border:0;background:#d6d9d2;cursor:pointer;padding:0;flex:none;transition:background .15s}
      #owgConnections .sw::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:left .15s}
      #owgConnections .sw[aria-checked="true"]{background:#2f7d55}
      #owgConnections .sw[aria-checked="true"]::after{left:21px}
      #owgConnections .sw:disabled{opacity:.55;cursor:progress}
      #owgConnections .sw:focus-visible{outline:2px solid #2f7d55;outline-offset:2px}
      #owgConnections .cell{display:flex;align-items:center;gap:8px}
      #owgConnections .na{color:var(--muted);font-size:12px}
      #owgConnections .linkish{min-height:0;background:none;border:0;color:var(--muted);font-size:12px;text-decoration:underline;padding:2px;cursor:pointer}
      #owgConnections .cli{margin-top:14px;font-size:12.5px}
      #owgConnections .cli code{display:block;margin-top:6px;padding:8px 10px;border-radius:8px;background:#f3f4f0;overflow-x:auto;white-space:pre;font-size:12px}
      #owgConnections .ai-detail{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
      #owgConnections .ai-detail h3{font-size:14px;margin:0 0 4px}
      #owgConnections .ai-opts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}
      #owgConnections .ai-opt{display:flex;gap:8px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:9px 10px;font-size:12.5px;cursor:pointer;background:#fafbf8}
      #owgConnections .ai-opt input{margin-top:2px}
      #owgConnections .ai-opt code{font-size:11.5px}
      #owgConnections .ai-lists{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      #owgConnections .ai-lists textarea{width:100%;min-height:64px;font:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:8px;padding:6px 8px;box-sizing:border-box}
      #owgConnections .ai-lock{font-size:12px;color:#8a5a00;margin-top:4px}
      @media(max-width:700px){#owgConnections .ai-opts,#owgConnections .ai-lists{grid-template-columns:1fr}}
      #owgMoreWays{margin-top:6px}
      #owgMoreWays>summary{cursor:pointer;font-weight:700;padding:10px 2px;color:var(--muted)}
      #owgConnTable{overflow-x:auto;max-width:100%}
      @media(max-width:700px){#owgConnections .hide-sm{display:none}#owgConnections table{table-layout:fixed}#owgConnections th,#owgConnections td{padding:9px 4px}#owgConnections th:nth-child(1){width:46%}#owgConnections .na{font-size:11px}#owgConnections .app{font-size:13px;overflow-wrap:anywhere}}
    `;
    document.head.appendChild(style);
  }

  function ensureLayout(){
    const panel=document.querySelector('#panel-connect');
    if(!panel)return null;
    let card=document.querySelector('#owgConnections');
    if(!card){
      card=document.createElement('div');
      card.id='owgConnections';card.className='card';
      card.innerHTML=`<div class="conn-head"><div><h2 style="margin-bottom:5px">Connections</h2><div class="muted"><strong>Context</strong> lets an app read the work context you allow. <strong>Observe</strong> lets OpenWorkGraph record how an agent runs (never prompts, responses, tool arguments or results). Flip a switch to turn either on or off. The first time sets the app up (with a backup of its settings); after that, on/off is instant.</div></div><div class="conn-master" id="owgMaster"></div></div><div id="owgConnTable"><div class="muted" style="margin-top:12px">Checking your apps…</div></div><div id="owgAiDetail" class="ai-detail"></div><div class="sub" id="owgCloudNote" style="margin-top:12px;font-size:12.5px"><strong>Cloud apps</strong> (ChatGPT, Lovable, Microsoft 365 Copilot) run on their servers, so they can't reach OpenWorkGraph on this computer directly. They need a secure tunnel: <button class="linkish" type="button" onclick="openConnect('chatgpt')">how to connect a cloud app</button></div><div class="cli"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><span><strong>For agents and scripts</strong> <span class="muted">(same switches, JSON output; works from any folder)</span></span><button class="linkish" id="owgCliCopy" type="button">Copy</button></div><code id="owgCli">…</code></div>`;
      panel.insertBefore(card,panel.firstChild);
    }
    // Everything the table already covers moves into one collapsed section.
    let more=document.querySelector('#owgMoreWays');
    if(!more){
      more=document.createElement('details');
      more.id='owgMoreWays';
      more.innerHTML='<summary>More ways to connect: ChatGPT, other MCP apps, custom agents, manual setup</summary>';
      panel.appendChild(more);
    }
    for(const selector of ['#ai-context-intro','#panel-connect .connect-grid','#agent-observation-setup']){
      const node=document.querySelector(selector);
      if(node&&node.parentNode!==more)more.appendChild(node);
    }
    return card;
  }

  function relativeTime(value){
    const t=Date.parse(String(value||''));if(!Number.isFinite(t))return '';
    const s=Math.max(0,Math.round((Date.now()-t)/1000));
    if(s<60)return `${s}s ago`;if(s<3600)return `${Math.floor(s/60)}m ago`;if(s<86400)return `${Math.floor(s/3600)}h ago`;return `${Math.floor(s/86400)}d ago`;
  }

  function switchHtml(client,kind,info){
    if(!info.supported)return `<span class="na" title="${esc(info.reason||'')}">Not available</span>`;
    const key=`${client.id}:${kind}`;
    const label=`${kind==='mcp'?'Context':'Observe'} for ${client.label}`;
    return `<div class="cell"><button class="sw" role="switch" aria-checked="${info.on?'true':'false'}" aria-label="${esc(label)}" data-client="${esc(client.id)}" data-kind="${esc(kind)}" ${busy.has(key)?'disabled':''}></button></div>`;
  }

  function restartLine(client){
    // The app loads this config only at startup and has not restarted since.
    for(const [kind,label] of [['observe','Observe'],['mcp','Context']]){
      const r=client[kind]?.restart_needed;if(!r)continue;
      const since=new Date(r.running_since);
      const when=Number.isFinite(since.getTime())?since.toLocaleDateString(undefined,{month:'short',day:'numeric'}):'';
      const quit=/Mac/i.test(navigator.platform||'')?' (⌘Q)':'';
      return `<div class="sub warn">Quit and reopen ${esc(r.app)}${quit} to finish turning on ${label}${when?` (running since ${esc(when)})`:''}</div>`;
    }
    return '';
  }

  // Where each native telemetry signal stands since OpenWorkGraph started:
  // counts and reason codes only (see /v1/agent-telemetry/diagnostics).
  const CHANNELS={claude_code:[['claude_code_hooks','Hooks'],['claude_code_otel_logs','OTel logs']],codex:[['codex_otel','OTel']],
    vscode:[['copilot_otel','Copilot OTel']],gemini_cli:[['gemini_otel','OTel']],cursor:[['cursor_hooks','Hooks']]};
  function channelText(label,ch){
    if(!ch||!ch.requests)return {text:`${label}: nothing received since OpenWorkGraph started`,warn:false};
    const parts=[`${label} ${relativeTime(ch.last_received_at)}`];
    if(ch.events_stored)parts.push(`${ch.events_stored} stored`);
    else if(ch.processed&&ch.records_seen&&!ch.events_projected)parts.push('received, but nothing structural in it');
    const rejected=Object.entries(ch.rejected||{});
    if(rejected.length)parts.push(`${rejected.reduce((n,[,c])=>n+c,0)} rejected (${rejected.map(([r])=>r.replace(/_/g,' ')).join(', ')})`);
    if(ch.observation_off)parts.push('arriving while Observe is off');
    return {text:parts.join(' · '),warn:rejected.length>0};
  }
  function diagnosticsLine(client){
    const channels=CHANNELS[client.id];
    if(!channels||!diagnostics||!client.observe?.supported||!client.observe.on)return '';
    const lines=channels.map(([key,label])=>channelText(label,diagnostics.channels?.[key]));
    const warn=lines.some(l=>l.warn);
    // Specific next steps from /v1/agent-telemetry/diagnostics "checks" (server/setup_checks.py), one per line.
    const steps=(diagnostics.checks||[]).filter(c=>c.client===client.id)
      .map(c=>`<div class="sub${c.status==='warn'?' warn':''}">${esc(c.message)} <strong>${esc(c.action)}</strong></div>`).join('');
    return `<div class="sub${warn?' warn':''}">${esc(lines.map(l=>l.text).join(' · '))}</div>${steps}`;
  }

  function subline(client){
    const restart=restartLine(client);if(restart)return restart;
    const msg=lastMessage[client.id];
    if(msg)return `<div class="sub${msg.warn?' warn':''}">${esc(msg.text)}</div>`;
    const errors=[client.mcp,client.observe].map(x=>x&&x.error).filter(Boolean);
    if(errors.length)return `<div class="sub warn">${esc(errors[0])}</div>`;
    if(!client.detected)return '<div class="sub">Not found on this computer</div>';
    const diag=diagnosticsLine(client);
    // Evidence, not configuration: these only appear once data actually flowed.
    const parts=[];
    if(client.last_used)parts.push(`Context used ${relativeTime(client.last_used)}`);
    if(client.last_observed)parts.push(`Telemetry observed ${relativeTime(client.last_observed)}`);
    else if(client.observe.supported&&client.observe.on)parts.push('No telemetry observed yet');
    return (parts.length?`<div class="sub">${esc(parts.join(' · '))}</div>`:'')+diag;
  }

  function render(){
    const card=ensureLayout();if(!card||!state)return;
    const master=card.querySelector('#owgMaster');
    if(master&&aiAccess){
      master.innerHTML=`<span>AI access this run: <strong>${aiAccess.enabled?'ON':'OFF'}</strong></span><button class="sw" role="switch" id="owgMasterSwitch" aria-checked="${aiAccess.enabled?'true':'false'}" aria-label="AI access for this run"></button>`;
      master.title='Master switch for all Context connections. It resets to OFF whenever OpenWorkGraph restarts.';
      master.querySelector('#owgMasterSwitch').onclick=toggleMaster;
    }
    const rows=state.clients.map(client=>{
      const anyInstalled=(client.mcp.installed)||(client.observe.supported&&client.observe.installed);
      const manual=client.observe.supported?`<button class="linkish" data-manual="${esc(client.id)}">Manual setup</button>`:'';
      const remove=anyInstalled?`<button class="linkish" data-remove="${esc(client.id)}">Remove</button>`:'';
      return `<tr><td><div class="app">${esc(client.label)}</div>${subline(client)}</td><td>${switchHtml(client,'mcp',client.mcp)}</td><td>${switchHtml(client,'observe',client.observe)}</td><td class="hide-sm" style="text-align:right">${remove} ${manual}</td></tr>`;
    }).join('');
    card.querySelector('#owgConnTable').innerHTML=`<table><thead><tr><th>App</th><th>Context</th><th>Observe</th><th class="hide-sm"></th></tr></thead><tbody>${rows}</tbody></table>`;
    card.querySelector('#owgCli').textContent=`owg=(${state.cli})\n"\${owg[@]}" list                     # every app and its switches\n"\${owg[@]}" on claude_code           # context + observe\n"\${owg[@]}" off cursor --mcp          # instant, no restart\n"\${owg[@]}" remove codex --observe   # uninstall from the app`;
    const copy=card.querySelector('#owgCliCopy');
    if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(card.querySelector('#owgCli').textContent);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy',1200);}catch(_){window.prompt('Copy this:',card.querySelector('#owgCli').textContent);}};
    card.querySelectorAll('.sw[data-client]').forEach(button=>button.onclick=()=>toggle(button.dataset.client,button.dataset.kind,button.getAttribute('aria-checked')!=='true'));
    card.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>removeClient(button.dataset.remove));
    card.querySelectorAll('[data-manual]').forEach(button=>button.onclick=()=>window.openAgentSetup?.(button.dataset.manual));
  }

  async function api(path,options){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(options||{})});
    const body=await response.json().catch(()=>({}));
    if(!response.ok){const error=new Error(body.detail||`Request failed (${response.status})`);error.body=body;throw error;}
    return body;
  }

  async function refresh(){
    try{
      const [connections,access,activity,traces,diag]=await Promise.all([
        api('/v1/connections'),api('/v1/ai-access'),api('/v1/mcp-activity?limit=200').catch(()=>({items:[]})),
        api('/v1/agent-execution-traces?limit=50&evidence_limit=25000&max_events_per_execution=1').catch(()=>({executions:[]})),
        api('/v1/agent-telemetry/diagnostics').catch(()=>null),
      ]);
      diagnostics=diag;
      const lastUsed={},lastObserved={};
      for(const item of activity.items||[]){if(item.client&&item.status==='ok'&&!lastUsed[item.client])lastUsed[item.client]=item.observed_at;}
      for(const run of traces.executions||[]){
        const agent=run?.agent||{},text=`${agent.name||''} ${agent.framework||''}`.toLowerCase();
        const id=text.includes('claude')?'claude_code':text.includes('codex')?'codex':text.includes('copilot')?'vscode':text.includes('gemini')?'gemini_cli':text.includes('cursor')?'cursor':'';
        const at=run.ended_at||run.started_at||'';
        if(id&&(!lastObserved[id]||String(at)>String(lastObserved[id])))lastObserved[id]=at;
      }
      for(const client of connections.clients||[]){client.last_used=lastUsed[client.id]||'';client.last_observed=lastObserved[client.id]||'';}
      state=connections;aiAccess=access;render();
    }catch(_){}
  }

  async function toggleMaster(){
    try{await api('/v1/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!aiAccess?.enabled})});}catch(_){}
    await refresh();window.refreshAiPanel?.();window.refreshDashboardAiAccess?.();
  }

  function describe(result,kind,action){
    const change=(result.changes||{})[kind]||{};
    const what=kind==='mcp'?'Context':'Observe';
    if(action==='off')return {text:`${what} off. Takes effect immediately.`};
    if(action==='remove')return {text:'Removed from the app\'s settings.'};
    if(change.takes_effect&&change.takes_effect!=='immediately')return {text:`${what} set up. Takes effect ${change.takes_effect}.`,warn:true};
    return {text:`${what} on.`};
  }

  async function toggle(clientId,kind,on){
    const key=`${clientId}:${kind}`;busy.add(key);render();
    try{
      if(kind==='mcp'&&on&&aiAccess&&!aiAccess.enabled){
        await api('/v1/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:true})});
      }
      const result=await api('/v1/connections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client:clientId,kind,action:on?'on':'off'})});
      lastMessage[clientId]=describe(result,kind,on?'on':'off');
    }catch(error){
      const label=state?.clients?.find(c=>c.id===clientId)?.label||'This app';
      if(error.body?.manual_setup_required){
        window.openModal?.(`Couldn't set up ${label} automatically`,'Connections',`<p>${esc(error.message)}</p><div class="note">Nothing was changed. You can still connect it by hand from “More ways to connect”.</div>`);
      }else lastMessage[clientId]={text:error.message||'Could not change the connection.',warn:true};
    }finally{busy.delete(key);await refresh();window.refreshAiPanel?.();window.refreshDashboardAiAccess?.();}
  }

  async function removeClient(clientId){
    const label=state?.clients?.find(c=>c.id===clientId)?.label||'this app';
    if(!window.confirm(`Remove OpenWorkGraph from ${label}'s settings? A backup of the file is kept.`))return;
    try{
      const result=await api('/v1/connections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client:clientId,kind:'both',action:'remove'})});
      lastMessage[clientId]=describe(result,'mcp','remove');
    }catch(error){lastMessage[clientId]={text:error.message||'Could not remove.',warn:true};}
    await refresh();
  }

  // --- AI context detail -----------------------------------------------------------
  let aiDetail=null;
  let aiDetailDirty=false;

  function renderAiDetail(){
    const host=document.querySelector('#owgAiDetail');if(!host||!aiDetail)return;
    if(aiDetailDirty&&host.childElementCount)return;
    const locked=!!aiDetail.locked_by_organization;
    const level=aiDetail.detail_level;
    host.innerHTML=`<h3>AI context detail</h3>
      <div class="muted">What connected AI apps see when they read your work context. Your raw evidence always stays on this computer.</div>
      <div class="ai-opts" role="radiogroup" aria-label="AI context detail">
        <label class="ai-opt"><input type="radio" name="owgAiDetail" value="redacted" ${level==='redacted'?'checked':''}><span><strong>Redacted</strong> (recommended)<br>Titles and labels keep their context, but people, emails, phone numbers and IDs become stable tokens: <code>Re: Contract for PERSON_1A2B3C - Gmail</code>. The same person gets the same token in every app.</span></label>
        <label class="ai-opt"><input type="radio" name="owgAiDetail" value="full" ${level==='full'?'checked':''} ${locked?'disabled':''}><span><strong>Full</strong><br>Raw labels and titles, including names: <code>Re: Contract for Anna Svensson - Gmail</code>. Use only with an AI you trust with this data.</span></label>
      </div>
      ${locked?'<div class="ai-lock">Locked to Redacted by your organization.</div>':''}
      <div class="ai-lists">
        <label class="sub">Never redact (company, product or project names, one per line)<textarea id="owgNeverRedact" placeholder="Acme AB&#10;Q3 pipeline">${esc((aiDetail.never_redact||[]).join('\n'))}</textarea></label>
        <label class="sub">Always redact (names the detector should always hide)<textarea id="owgAlwaysRedact" placeholder="Project Falcon">${esc((aiDetail.always_redact||[]).join('\n'))}</textarea></label>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:8px"><button class="secondary" type="button" id="owgSaveAiLists">Save lists</button><span class="sub" id="owgAiDetailStatus"></span></div>`;
    host.querySelectorAll('input[name="owgAiDetail"]').forEach(input=>input.onchange=()=>saveAiDetail({detail:input.value}));
    host.querySelectorAll('textarea').forEach(t=>t.oninput=()=>{aiDetailDirty=true;});
    host.querySelector('#owgSaveAiLists').onclick=()=>saveAiDetail({
      never_redact:host.querySelector('#owgNeverRedact').value.split('\n'),
      always_redact:host.querySelector('#owgAlwaysRedact').value.split('\n'),
    });
  }

  async function refreshAiDetail(){
    try{aiDetail=await api('/v1/ai-context');renderAiDetail();}catch(_){}
  }

  async function saveAiDetail(body){
    const status=document.querySelector('#owgAiDetailStatus');
    try{
      aiDetail=await api('/v1/ai-context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      aiDetailDirty=false;renderAiDetail();
      const s=document.querySelector('#owgAiDetailStatus');
      if(s)s.textContent=body.detail?`AI context is now ${aiDetail.detail_level==='full'?'Full':'Redacted'}. Applies to the next AI request.`:'Saved.';
    }catch(error){
      if(status)status.textContent=error.message||'Could not save.';
      await refreshAiDetail();
    }
  }
  window.refreshAiContextDetail=refreshAiDetail;

  window.refreshConnections=refresh;

  function install(){
    installStyle();ensureLayout();refresh();refreshAiDetail();
    document.querySelector('#tab-connect')?.addEventListener('click',()=>setTimeout(()=>{ensureLayout();refresh();},0));
    setInterval(()=>{
      const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
      if(!document.hidden&&active==='connect'&&!busy.size){ensureLayout();refresh();}
    },5000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
