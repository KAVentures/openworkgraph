(() => {
  'use strict';

  const DISMISSED_KEY = 'owg_first_value_dismissed_v1';
  const VIEWED_KEY = 'owg_first_value_reconstruction_viewed_at';
  const POLL_MS = 5000;
  let latest = null;
  let busy = false;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const safeArray = value => Array.isArray(value) ? value : [];

  function dismissed(){
    try{return localStorage.getItem(DISMISSED_KEY)==='1';}catch(_){return false;}
  }
  function markDismissed(){
    try{localStorage.setItem(DISMISSED_KEY,'1');}catch(_){}
    document.querySelector('#firstValueCard')?.remove();
  }
  function markViewed(){
    try{if(!localStorage.getItem(VIEWED_KEY))localStorage.setItem(VIEWED_KEY,new Date().toISOString());}catch(_){}
  }

  async function getJson(url){
    await window.__owgAuthReady;
    const response = await fetch(url,{cache:'no-store'});
    if(!response.ok)throw new Error(`GET ${url} failed`);
    return response.json();
  }

  function surfaceOf(item){
    const host=String(item?.hostname||'').trim().toLowerCase();
    if(host){
      const parts=host.replace(/^www\./,'').split('.');
      if(parts.length>1)return parts.slice(0,-1).join('.');
      return host;
    }
    return String(item?.app||'Unknown surface').trim()||'Unknown surface';
  }

  function actionOf(item){
    const label=String(item?.label||'').trim();
    const action=String(item?.action||'').trim().replace(/[_-]+/g,' ');
    if(label&&label.toLowerCase()!==surfaceOf(item).toLowerCase())return label.slice(0,120);
    return action ? action.charAt(0).toUpperCase()+action.slice(1) : 'Observed activity';
  }

  function recentWindow(summary){
    const rows=safeArray(summary?.recent_evidence).slice().reverse();
    const cutoff=Date.now()-(15*60*1000);
    const filtered=rows.filter(row=>{
      const t=Date.parse(String(row?.observed_at||''));
      return !Number.isFinite(t)||t>=cutoff;
    });
    return filtered.length?filtered:rows.slice(-30);
  }

  function collapsedEvidence(summary){
    const rows=recentWindow(summary);
    const output=[];
    for(const row of rows){
      const surface=surfaceOf(row),action=actionOf(row),observed_at=row?.observed_at||'';
      const prior=output[output.length-1];
      if(prior&&prior.kind==='human'&&prior.surface===surface&&prior.action===action){
        prior.count+=1;prior.observed_at=observed_at||prior.observed_at;continue;
      }
      output.push({kind:'human',surface,action,observed_at,count:1,source:String(row?.source||'')});
    }
    return output;
  }

  function agentItems(agentPayload){
    const result=[];
    for(const run of safeArray(agentPayload?.executions)){
      const agent=run?.agent||{};
      const name=agent.framework||agent.provider||agent.name||'Agent';
      const operations=run?.operation_counts||{};
      const tools=n(operations.tool_call);
      const models=n(operations.model_call);
      const approvals=n(run?.approval_request_count);
      const failures=n(run?.failure_count)+n(operations.error);
      const status=String(run?.outcome_status||'observed');
      const details=[];
      if(models)details.push(`${models} model ${models===1?'call':'calls'}`);
      if(tools)details.push(`${tools} tool ${tools===1?'call':'calls'}`);
      if(approvals)details.push(`${approvals} approval ${approvals===1?'request':'requests'}`);
      if(failures)details.push(`${failures} observed ${failures===1?'failure':'failures'}`);
      details.push(status);
      result.push({
        kind:'agent',surface:String(name).slice(0,80),action:details.join(' · '),
        observed_at:run?.ended_at||run?.started_at||'',count:1,
        observation_level:String(run?.observation_level||agent?.observation_level||'partial observation')
      });
    }
    return result;
  }

  function reconstruction(summary,agentPayload){
    const combined=[...collapsedEvidence(summary),...agentItems(agentPayload)];
    combined.sort((a,b)=>{
      const ta=Date.parse(String(a.observed_at||'')),tb=Date.parse(String(b.observed_at||''));
      if(!Number.isFinite(ta)&&!Number.isFinite(tb))return 0;
      if(!Number.isFinite(ta))return -1;if(!Number.isFinite(tb))return 1;return ta-tb;
    });
    const compact=[];
    for(const item of combined){
      const prior=compact[compact.length-1];
      if(prior&&item.kind==='human'&&prior.kind==='human'&&prior.surface===item.surface){
        if(prior.action!==item.action)prior.action=`${prior.action} → ${item.action}`.slice(0,180);
        prior.count+=item.count||1;prior.observed_at=item.observed_at||prior.observed_at;continue;
      }
      compact.push({...item});
    }
    return compact.slice(-12);
  }

  function stateFrom(summary,agentPayload){
    const evidence=recentWindow(summary),surfaces=new Set(evidence.map(surfaceOf).filter(Boolean));
    let transitions=0,last='';
    for(const row of evidence){const s=surfaceOf(row);if(last&&s&&s!==last)transitions+=1;if(s)last=s;}
    const runs=safeArray(agentPayload?.executions);
    const agentEvents=runs.reduce((total,run)=>total+n(run?.event_count_total),0);
    const ready=(evidence.length>=8&&surfaces.size>=2)||(transitions>=2&&evidence.length>=5)||agentEvents>=3;
    const apps=safeArray(summary?.apps).map(x=>String(x?.app||'').toLowerCase());
    const browserHeavy=apps.some(x=>/(chrome|edge|safari|firefox|arc|brave)/.test(x));
    const browserConnected=Boolean(summary?.browser_sensor?.connected||summary?.browser_sensor?.paired||summary?.browser_sensor?.active);
    const shallowAgent=runs.some(run=>['os_observed','outcome_only'].includes(String(run?.observation_level||run?.agent?.observation_level||'')));
    return {evidence_count:evidence.length,surface_count:surfaces.size,transitions,agent_runs:runs.length,agent_events:agentEvents,ready,browserHeavy,browserConnected,shallowAgent};
  }

  function aiEnabled(payload){return Boolean(payload?.enabled??payload?.ai_access_enabled??payload?.access?.enabled);}

  function ensureStyle(){
    if(document.querySelector('#first-value-style'))return;
    const style=document.createElement('style');style.id='first-value-style';
    style.textContent=`
      .first-value-card{border:2px solid #285e42;background:linear-gradient(135deg,#ffffff,#f5faf6)}
      .first-value-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.first-value-head button{min-height:32px;padding:4px 8px}
      .first-value-progress{display:flex;gap:8px;flex-wrap:wrap;margin:13px 0}.first-value-progress span{border:1px solid #d8e5dc;background:#fff;border-radius:999px;padding:6px 9px;font-size:12px}
      .first-value-ready{padding:10px 12px;border-radius:11px;background:#edf7f0;color:#285e42;font-weight:750;margin:10px 0}
      .first-value-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.first-value-actions button{min-height:38px}
      .first-value-trace{margin:10px 0 0;padding:0;list-style:none}.first-value-trace li{display:grid;grid-template-columns:18px minmax(0,1fr);gap:8px;padding:8px 0;border-bottom:1px solid #eceee8}.first-value-trace li:last-child{border-bottom:0}.first-value-node{width:9px;height:9px;border-radius:50%;background:#285e42;margin-top:5px}.first-value-node.agent{background:#3159a5}.first-value-trace strong{font-size:13px}.first-value-trace .muted{margin-top:2px}
    `;
    document.head.appendChild(style);
  }

  function ensureCard(){
    if(dismissed())return null;
    const overview=document.querySelector('#panel-overview');if(!overview)return null;
    let card=document.querySelector('#firstValueCard');
    if(card)return card;
    card=document.createElement('div');card.id='firstValueCard';card.className='card first-value-card';
    card.innerHTML=`<div class="first-value-head"><div><div class="setup-tag">FIRST VALUE</div><h2>See what OpenWorkGraph understands</h2><div id="firstValueLead" class="muted">Keep working normally. OpenWorkGraph will use the evidence already being captured on this computer to show you a factual reconstruction.</div></div><button id="firstValueDismiss" class="ghost" type="button" aria-label="Dismiss first-value guide">Dismiss</button></div><div id="firstValueProgress" class="first-value-progress"></div><div id="firstValueStatus" class="note">Waiting for enough observed activity to form a useful reconstruction.</div><div id="firstValueActions" class="first-value-actions"></div>`;
    const onboarding=document.querySelector('#historyOnboarding');
    if(onboarding&&onboarding.parentNode===overview)onboarding.insertAdjacentElement('afterend',card);else overview.insertBefore(card,overview.firstChild);
    card.querySelector('#firstValueDismiss').onclick=markDismissed;
    return card;
  }

  function hideEmptyPlaceholders(summary){
    const overview=document.querySelector('#panel-overview');if(!overview)return;
    for(const card of overview.querySelectorAll('.card')){
      const text=card.textContent||'';
      if(text.includes('Timeline lanes will activate in the stacked capture/timeline PR. Existing evidence collection is unchanged.'))card.style.display='none';
    }
    const patternList=document.querySelector('#patternList');
    if(patternList){
      const card=patternList.closest('.card');
      if(card)card.style.display=n(summary?.repeated_task_pattern_count)>0?'':'none';
    }
  }

  function render(state,summary,agentPayload,aiPayload){
    hideEmptyPlaceholders(summary);
    const card=ensureCard();if(!card)return;
    card.querySelector('#firstValueProgress').innerHTML=`<span><strong>${state.evidence_count}</strong> recent evidence rows</span><span><strong>${state.surface_count}</strong> work ${state.surface_count===1?'surface':'surfaces'}</span><span><strong>${state.transitions}</strong> cross-surface ${state.transitions===1?'transition':'transitions'}</span><span><strong>${state.agent_runs}</strong> agent ${state.agent_runs===1?'run':'runs'}</span>`;
    const status=card.querySelector('#firstValueStatus'),actions=card.querySelector('#firstValueActions');
    if(state.ready){
      status.className='first-value-ready';status.textContent='OpenWorkGraph has enough observed activity to show a reconstruction. No AI interpretation is required for this view.';
      actions.innerHTML='<button id="firstValueSee" class="green" type="button">See what happened</button>';
      card.querySelector('#firstValueSee').onclick=()=>openReconstruction(summary,agentPayload,aiPayload,state);
    }else{
      status.className='note';
      status.textContent=state.evidence_count===0?'No activity from this run has reached the local evidence store yet. Keep working normally.':'Evidence is arriving. A few more actions or a cross-tool transition will make the reconstruction more useful.';
      actions.innerHTML='<button id="firstValueEvidence" class="secondary" type="button">View raw evidence</button>';
      card.querySelector('#firstValueEvidence').onclick=()=>window.activateTab?.('evidence');
    }
  }

  function traceHtml(items){
    if(!items.length)return '<div class="note">There is not enough bounded recent evidence to render a trace yet.</div>';
    return `<ol class="first-value-trace">${items.map(item=>`<li><span class="first-value-node ${item.kind==='agent'?'agent':''}"></span><div><strong>${esc(item.surface)}</strong><div class="muted">${esc(item.action)}${item.count>1?` · ${item.count} observations`:''}${item.kind==='agent'?` · ${esc(item.observation_level)}`:''}</div></div></li>`).join('')}</ol>`;
  }

  function openReconstruction(summary,agentPayload,aiPayload,state){
    markViewed();
    const items=reconstruction(summary,agentPayload),enabled=aiEnabled(aiPayload);
    const prompt='Using OpenWorkGraph, reconstruct what I was doing during the last 10 minutes. Distinguish observed facts from inference.';
    let next='';
    if(enabled){
      next=`<h3 style="margin-top:18px">Let your AI inspect the same work</h3><div class="muted">AI access is currently on. Ask it to use OpenWorkGraph rather than relying on chat context alone.</div><div class="codebox" id="firstValuePrompt">${esc(prompt)}</div><div class="modal-actions"><button id="firstValueCopyPrompt" type="button">Copy starter question</button></div>`;
    }else{
      next='<h3 style="margin-top:18px">Let your AI understand this too</h3><div class="muted">AI access is still off. Connecting an AI is optional and does not change capture or retention.</div><div class="modal-actions"><button id="firstValueConnectAI" type="button">Connect AI</button></div>';
    }
    if(state.browserHeavy&&!state.browserConnected)next+='<div class="note" style="margin-top:12px"><strong>Browser context can be richer.</strong> Your work includes a browser, but no active browser sensor was detected. The browser sensor is optional.</div><div class="modal-actions"><button id="firstValueBrowser" class="secondary" type="button">Browser sensor setup</button></div>';
    if(state.shallowAgent)next+='<div class="note" style="margin-top:12px"><strong>Agent internals are only partially observed.</strong> You can add native/OTel telemetry for deeper structural traces without capturing prompts or responses.</div><div class="modal-actions"><button id="firstValueAgent" class="secondary" type="button">Agent telemetry setup</button></div>';
    const body=`<div class="note"><strong>Observed evidence only.</strong> This reconstruction is assembled from the current session's existing privacy-hardened evidence. It does not infer intent or read hidden reasoning.</div>${traceHtml(items)}${next}`;
    if(typeof window.openModal==='function')window.openModal('Your last few minutes','Observed reconstruction',body);else alert(items.map(x=>`${x.surface}: ${x.action}`).join('\n'));
    setTimeout(()=>{
      const copy=document.querySelector('#firstValueCopyPrompt');if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(prompt);copy.textContent='Copied';}catch(_){window.prompt('Copy this:',prompt);}};
      const connect=document.querySelector('#firstValueConnectAI');if(connect)connect.onclick=()=>{window.closeModal?.();window.activateTab?.('connect');};
      const browser=document.querySelector('#firstValueBrowser');if(browser)browser.onclick=()=>{window.closeModal?.();window.activateTab?.('organization');};
      const agent=document.querySelector('#firstValueAgent');if(agent)agent.onclick=()=>{window.closeModal?.();window.activateTab?.('connect');setTimeout(()=>document.querySelector('#agent-observation-setup')?.scrollIntoView({behavior:'smooth',block:'start'}),0);};
    },0);
  }

  async function refresh(){
    if(busy)return;busy=true;
    try{
      const [summaryResult,agentResult,aiResult]=await Promise.allSettled([
        getJson('/v1/summary?scope=current&limit=500'),
        getJson('/v1/agent-execution-traces?limit=10&evidence_limit=3000&max_events_per_execution=20'),
        getJson('/v1/ai-access')
      ]);
      if(summaryResult.status!=='fulfilled')return;
      const summary=summaryResult.value,agentPayload=agentResult.status==='fulfilled'?agentResult.value:{executions:[]},aiPayload=aiResult.status==='fulfilled'?aiResult.value:{};
      const state=stateFrom(summary,agentPayload);latest={summary,agentPayload,aiPayload,state};render(state,summary,agentPayload,aiPayload);
    }finally{busy=false;}
  }

  function install(){
    ensureStyle();refresh();
    document.querySelector('#tab-overview')?.addEventListener('click',()=>setTimeout(refresh,0));
    setInterval(()=>{if(!document.hidden)refresh();},POLL_MS);
  }

  window.refreshFirstValue=refresh;
  window.firstValueReconstruction=()=>latest?openReconstruction(latest.summary,latest.agentPayload,latest.aiPayload,latest.state):refresh();
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
