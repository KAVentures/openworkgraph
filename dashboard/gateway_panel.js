(() => {
  async function gwCall(url, options={}) {
    await window.__owgAuthReady;
    const r = await fetch(url, {cache:'no-store', ...options});
    let d={}; try { d=await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(d.detail || d.error || 'OpenWorkGraph request failed');
    return d;
  }

  function e(value) {
    if (typeof window.esc === 'function') return window.esc(String(value ?? ''));
    const x=document.createElement('div'); x.textContent=String(value ?? ''); return x.innerHTML;
  }

  function fmtDuration(seconds) {
    const s=Math.max(0,Math.floor(Number(seconds)||0));
    const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), r=s%60;
    return h ? `${h}h ${String(m).padStart(2,'0')}m ${String(r).padStart(2,'0')}s` : `${m}m ${String(r).padStart(2,'0')}s`;
  }

  function modeLabel(s) {
    if (s.mode==='demo_local_only') return 'Demo — local only';
    if (s.mode==='local_only') return 'Local only';
    if (s.mode==='configured_not_enrolled') return 'Configured, not enrolled';
    if (s.mode==='connected_paused') return 'Connected — sharing paused';
    if (s.mode==='connected') return s.sync_status==='error' ? 'Connected — sync error' : 'Connected';
    return String(s.mode || 'Local only');
  }

  function statusHtml(s) {
    const connected = !!s.enrolled && !!s.gateway_enabled;
    const url = s.gateway_url ? `<code>${e(s.gateway_url)}</code>` : 'No organization Gateway configured';
    const last = s.last_success_at ? `Last successful sync: ${e(String(s.last_success_at).replace('T',' ').slice(0,19))}` : 'No evidence has been synchronized yet';
    const quarantine = Number(s.quarantined_events || 0) > 0 ? `<div class="note" style="margin-top:8px"><strong>Sync quarantine:</strong> ${e(s.quarantined_events)} terminally invalid event(s) remain local and did not block later evidence.</div>` : '';
    const err = s.last_error ? `<div class="note" style="margin-top:8px"><strong>Sync error:</strong> ${e(s.last_error)}</div>` : '';
    const controls = !connected
      ? `<button class="green" onclick="openGatewaySetup()">Connect to organization</button>`
      : `<button class="${s.sharing_paused?'green':'secondary'}" onclick="setGatewaySharing(${s.sharing_paused?'true':'false'})">${s.sharing_paused?'Resume sharing':'Pause sharing'}</button><button class="secondary" onclick="disconnectGateway()">Disconnect</button>`;
    return `<div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap"><div><h2 style="margin:0 0 6px">Organization Gateway</h2><div><strong>${e(modeLabel(s))}</strong> · ${url}</div><div class="muted" style="margin-top:5px">${e(last)}${s.worker_running?' · sync worker running':''}</div></div><div class="modal-actions">${controls}</div></div>${err}${quarantine}<div class="note" style="margin-top:10px">Local recording always continues independently. New connections share evidence captured <strong>after enrollment</strong>; earlier local history stays local. While organization sharing is paused, local capture continues but that sharing-paused interval is <strong>never backfilled</strong> on resume. No OpenWorkGraph cloud account is required.</div>`;
  }

  function updateGatewayChip(s) {
    const chip=document.querySelector('#gatewayStatusChip');
    if (!chip) return;
    if (s.mode==='connected_paused') {
      chip.textContent='Organization sharing: paused'; chip.classList.remove('on'); chip.classList.add('off');
    } else if (s.mode==='connected' && !s.last_error) {
      chip.textContent='Organization sharing: on'; chip.classList.add('on'); chip.classList.remove('off');
    } else if (s.mode==='connected' && s.last_error) {
      chip.textContent='Organization sharing: sync error'; chip.classList.add('off'); chip.classList.remove('on');
    } else {
      chip.textContent='Organization sharing: off'; chip.classList.remove('on','off');
    }
  }

  window.refreshGatewayPanel = async function() {
    const panel=document.querySelector('#gatewayPanel');
    try {
      const status=await gwCall('/v1/gateway-status');
      updateGatewayChip(status);
      if (panel) panel.innerHTML=statusHtml(status);
    } catch (err) {
      const chip=document.querySelector('#gatewayStatusChip');
      if (chip) chip.textContent='Organization sharing: unavailable';
      if (panel) panel.innerHTML=`<h2>Organization Gateway</h2><div class="off">Status unavailable: ${e(err.message)}</div>`;
    }
  };

  async function refreshAiAccessSurface() {
    // Only the top-bar chip. The on/off switch lives in Connect > Connections
    // and the read log in the "Recent AI reads" card (server/secure_app.py).
    const chip=document.querySelector('#aiStatusChip');
    try {
      const [access, activity] = await Promise.all([gwCall('/v1/ai-access'), gwCall('/v1/mcp-activity?limit=20')]);
      const reads=(activity.items||[]).filter(x=>x.status!=='denied').length;
      if (chip) {
        chip.textContent=access.enabled ? `AI access: on · ${reads} ${reads===1?'read':'reads'}` : 'AI access: off · Enable';
        chip.title=access.enabled ? 'Connected AI apps can read your work context during this run. Resets to off when OpenWorkGraph restarts.' : 'Connected AI apps cannot read anything. Turn it on in Connect.';
        chip.classList.toggle('on',!!access.enabled); chip.classList.toggle('off',!access.enabled);
      }
    } catch (_) {
      if (chip) chip.textContent='AI access: unavailable';
    }
  }
  window.refreshDashboardAiAccess=refreshAiAccessSurface;

  async function captureAction(action) {
    try {
      if (action==='stop' && !confirm('Stop local recording? Existing data stays on this computer and the dashboard remains open.')) return;
      const status=await gwCall(`/v1/capture/${action}`,{method:'POST'});
      if (typeof window.toast==='function') {
        const message=action==='pause'?'Recording paused. Nothing from the paused interval will be stored.':action==='resume'?'Recording resumed.':action==='stop'?'Recording stopped. Existing data is kept locally.':'New recording run started.';
        window.toast(message);
      }
      renderCaptureStatus(status);
      if (action==='start' && typeof window.load==='function') window.load();
    } catch(err) {
      if (typeof window.toast==='function') window.toast(err.message||'Could not change recording state.');
    }
  }

  let clock=null;
  function tickClock() {
    const label=document.querySelector('#captureLabel');
    if (!clock || !label) return;
    const elapsed=clock.base+(Date.now()-clock.at)/1000;
    label.textContent=`Recording · ${fmtDuration(elapsed)}`+clock.suffix;
  }

  function renderCaptureStatus(s) {
    const label=document.querySelector('#captureLabel');
    const dot=document.querySelector('#captureDot');
    const buttons=document.querySelector('#captureButtons');
    if (!label || !dot || !buttons) return;
    window.__owgCaptureStatusLive=true;  // this poller now owns the recording label
    if (s.demo) {
      clock=null;
      label.textContent='Demo data · not recording'; dot.style.background='#8b8f8b'; buttons.innerHTML=''; return;
    }
    const state=String(s.state||'recording');
    if (state==='recording') {
      const names={accessibility:'Accessibility',input_monitoring:'Input Monitoring'};
      const missing=(s.missing_permissions||[]).map(p=>names[p]||p);
      // Short enough for the header; the hover text names each missing permission.
      clock={base:Number(s.run_elapsed_seconds)||0,at:Date.now(),suffix:(s.collector_alive===false?' · waiting for the recorder':'')+(s.away?' · away':'')+(missing.length?' · macOS permission missing':'')};
      tickClock();
      label.title=missing.length?`OpenWorkGraph cannot see everything: allow it under System Settings → Privacy & Security → ${missing.join(' / ')}, then restart OpenWorkGraph.`:'';
      dot.style.background='#d25a5a';
      buttons.innerHTML='<button class="secondary" id="pauseCapture">Pause</button><button class="secondary" id="stopCapture">Stop</button>';
      buttons.querySelector('#pauseCapture').onclick=()=>captureAction('pause');
      buttons.querySelector('#stopCapture').onclick=()=>captureAction('stop');
    } else if (state==='paused') {
      clock=null;
      label.textContent='Paused · not recording'; dot.style.background='#d49a2f';
      buttons.innerHTML='<button class="secondary" id="resumeCapture">Resume</button><button class="secondary" id="stopCapture">Stop</button>';
      buttons.querySelector('#resumeCapture').onclick=()=>captureAction('resume');
      buttons.querySelector('#stopCapture').onclick=()=>captureAction('stop');
    } else {
      clock=null;
      label.textContent='Stopped · data kept locally'; dot.style.background='#8b8f8b';
      buttons.innerHTML='<button class="secondary" id="startCapture">Start new run</button>';
      buttons.querySelector('#startCapture').onclick=()=>captureAction('start');
    }
  }

  async function refreshCaptureStatus() {
    try { renderCaptureStatus(await gwCall('/v1/capture/status')); } catch (_) {}
  }
  window.refreshCaptureStatus=refreshCaptureStatus;

  function renderTimeline(payload) {
    const host=document.querySelector('#timelineMount');
    if (!host) return;
    const spans=payload.spans||[];
    if (!spans.length) {
      host.className='timeline-placeholder'; host.innerHTML='<div><strong>Your timeline starts here</strong><div class="muted" style="margin-top:4px">Keep working normally. Each tool gets its own lane after its first minute in front.</div></div>'; return;
    }
    const starts=spans.map(x=>Date.parse(x.start)).filter(Number.isFinite);
    const ends=spans.map(x=>Date.parse(x.end)).filter(Number.isFinite);
    if (!starts.length || !ends.length) return;
    const lo=Math.min(...starts), hi=Math.max(...ends), total=Math.max(60000,hi-lo);
    const groups=new Map();
    for (const span of spans) {
      const surface=String(span.surface||'Unknown');
      if (!groups.has(surface)) groups.set(surface,[]);
      groups.get(surface).push(span);
    }
    const color=surface=>typeof window.surfaceColor==='function'?window.surfaceColor(surface):'#657';
    const lanes=[...groups.entries()].sort((a,b)=>b[1].reduce((n,x)=>n+Number(x.engaged_seconds||0),0)-a[1].reduce((n,x)=>n+Number(x.engaged_seconds||0),0));
    const ticks=Array.from({length:7},(_,i)=>lo+(total*i/6));
    host.className='';
    host.innerHTML=`<div style="min-width:660px;overflow:auto"><div style="display:grid;grid-template-columns:150px minmax(480px,1fr) 90px;gap:10px;align-items:end;margin-bottom:7px"><div></div><div style="position:relative;height:24px">${ticks.map((t,i)=>`<span class="muted" style="position:absolute;left:${i/6*100}%;transform:translateX(${i===0?'0':i===6?'-100%':'-50%'})">${new Date(t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span>`).join('')}</div><div class="muted">Engaged</div></div>${lanes.map(([surface,items])=>{const engaged=items.reduce((n,x)=>n+Number(x.engaged_seconds||0),0);return `<div style="display:grid;grid-template-columns:150px minmax(480px,1fr) 90px;gap:10px;align-items:center;margin:7px 0"><div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><span class="surface-dot" style="--surface:${color(surface)}"></span><strong>${e(surface)}</strong></div><div style="height:24px;background:#f0f2ed;border-radius:7px;position:relative;overflow:hidden">${items.map(x=>{const a=Date.parse(x.start),b=Date.parse(x.end);const left=Math.max(0,(a-lo)/total*100),width=Math.max(.4,(b-a)/total*100);return `<span title="${e(surface)} · ${fmtDuration(x.engaged_seconds)} engaged" style="position:absolute;left:${left}%;width:${width}%;top:3px;bottom:3px;border-radius:5px;background:${color(surface)}"></span>`;}).join('')}</div><div>${fmtDuration(engaged)}</div></div>`;}).join('')}</div>`;
  }

  function renderRichPatterns(payload) {
    const host=document.querySelector('#patternList');
    if (!host) return;
    const patterns=payload.patterns||[];
    host.innerHTML=patterns.map((p,i)=>{
      const steps=(p.steps||[]).map(step=>`<span class="pill"><span class="surface-dot" style="--surface:${typeof window.surfaceColor==='function'?window.surfaceColor(step.surface):'#657'}"></span>${e(step.surface)}${step.action?` · ${e(String(step.action).replaceAll('_',' '))}`:''}</span>`).join('');
      const runs=(p.runs||[]).map(run=>`<tr><td>${e(String(run.started_at||'').replace('T',' ').slice(0,16))}–${e(String(run.ended_at||'').slice(11,16))}</td><td>${(run.surfaces||[]).map(e).join(' → ')}</td><td>${fmtDuration(run.engaged_seconds)}</td><td>${e(String((run.boundary||{}).end_reason||'').replace(/_/g,' '))}</td></tr>`).join('');
      const data=encodeURIComponent(JSON.stringify({name:p.name,signature:p.signature,event_ids:(p.runs||[]).flatMap(r=>r.event_ids||[])}));
      return `<div class="pattern" id="rich-pattern-${i}"><div class="pattern-head"><div><strong>${e(p.name)}</strong> <span class="badge">Needs review · ${e(p.confidence||'low')}</span><div style="margin-top:7px">${steps}</div></div><div><strong>×${Number(p.observed_count||p.runs?.length||0)}</strong><div class="muted">median ${fmtDuration(p.median_engaged_seconds)} · total ${fmtDuration(p.total_engaged_seconds)}</div></div></div><div class="pattern-actions"><button class="secondary" data-show-runs="${i}">Show the ${Number(p.runs?.length||p.observed_count||0)} runs</button><button class="secondary" data-automation="${i}">Prepare automation context</button><button class="secondary" data-pattern-export="${data}">Export evidence IDs</button></div><div class="pattern-runs"><div class="table-wrap"><table><thead><tr><th>When</th><th>Steps</th><th>Engaged time</th><th>How it ended</th></tr></thead><tbody>${runs||'<tr><td colspan="4">Execution windows are unavailable for this pattern.</td></tr>'}</tbody></table></div></div></div>`;
    }).join('') || (typeof window.emptyState==='function' ? window.emptyState('No repeated workflow yet','When you do the same sequence of steps across tools at least twice, it appears here with how long each run took.') : '<div class="muted">No repeated workflow yet.</div>');
    host.querySelectorAll('[data-show-runs]').forEach(btn=>btn.onclick=()=>document.querySelector(`#rich-pattern-${btn.dataset.showRuns}`)?.classList.toggle('expanded'));
    host.querySelectorAll('[data-automation]').forEach(btn=>btn.onclick=()=>{if(typeof window.activateTab==='function')window.activateTab('connect');if(typeof window.toast==='function')window.toast('Connect your AI, then ask it to inspect this repeated workflow through OpenWorkGraph MCP.');});
    host.querySelectorAll('[data-pattern-export]').forEach(btn=>btn.onclick=()=>{
      const payload=JSON.parse(decodeURIComponent(btn.dataset.patternExport||''));
      const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='openworkgraph-pattern-evidence-ids.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
    });
  }

  async function refreshOverviewDerived() {
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if (active!=='overview') return;
    try {
      const [timeline,patterns]=await Promise.all([gwCall('/v1/timeline?scope=current'),gwCall('/v1/dashboard-patterns?scope=current')]);
      renderTimeline(timeline); renderRichPatterns(patterns);
    } catch (_) {}
  }
  window.refreshOverviewDerived=refreshOverviewDerived;

  window.openGatewaySetup = function() {
    if (typeof window.openModal !== 'function') return;
    const content=`<p>Connect this endpoint to a <strong>customer-controlled OpenWorkGraph Gateway</strong>. Prefer a short-lived, single-use enrollment code issued by the Gateway administrator.</p><div class="note" style="margin-bottom:12px"><strong>Shared by default after you connect:</strong> privacy-hardened app/window/page context, timestamps and effort/interaction metadata, safe UI/semantic labels, and copy/cut/paste occurrence/linkage. <strong>Pre-enrollment history stays local.</strong><br><br><strong>Never sent by this Gateway path:</strong> typed text, clipboard contents, ordinary key identities, password values, or screenshot bytes. Organization policy may further reduce what is shared; it cannot broaden your local sharing policy.</div><label for="gwUrl">Gateway URL</label><input id="gwUrl" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="https://openworkgraph.company.internal"><label for="gwOrg">Organization ID</label><input id="gwOrg" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="acme"><label for="gwActor">Actor ID <span class="muted">(optional; normally bound by the enrollment code)</span></label><input id="gwActor" style="width:100%;box-sizing:border-box;margin:5px 0 10px"><label for="gwToken">Enrollment code</label><input id="gwToken" type="password" autocomplete="off" style="width:100%;box-sizing:border-box;margin:5px 0 10px"><div class="note">Non-local Gateways require HTTPS. Nothing is shared until enrollment succeeds. Pausing later keeps that sharing-paused interval permanently local while local capture continues.</div><div class="modal-actions"><button id="gwConnectBtn">Connect and share from now</button><button class="secondary" onclick="closeModal()">Cancel</button></div>`;
    openModal('Connect organization Gateway','Self-hosted / customer-controlled',content);
    document.querySelector('#gwConnectBtn').onclick=async function(){
      const btn=this; btn.disabled=true; btn.textContent='Connecting…';
      try {
        const payload={gateway_url:document.querySelector('#gwUrl').value,organization_id:document.querySelector('#gwOrg').value,actor_id:document.querySelector('#gwActor').value,enrollment_token:document.querySelector('#gwToken').value,verify_tls:true,allow_insecure_http:false};
        document.querySelector('#gwToken').value='';
        await gwCall('/v1/gateway-enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        closeModal(); await refreshGatewayPanel();
      } catch(err) { btn.disabled=false; btn.textContent='Connect and share from now'; alert(err.message); }
    };
  };

  window.setGatewaySharing = async function(enabled) {
    if (!enabled && !confirm('Pause organization sharing? Local capture will continue, but evidence captured while sharing is paused will stay local permanently and will not be uploaded when you resume.')) return;
    try { await gwCall('/v1/gateway-sharing',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!enabled})}); await refreshGatewayPanel(); }
    catch(err) { alert(err.message); }
  };

  window.disconnectGateway = async function() {
    if (!confirm('Disconnect this computer from the organization Gateway? The device credential will be revoked first. Local OpenWorkGraph data will remain on this computer.')) return;
    try { await gwCall('/v1/gateway-disconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force_local:false})}); await refreshGatewayPanel(); }
    catch(err) { alert(err.message); }
  };

  document.addEventListener('DOMContentLoaded', () => {
    let panel=document.querySelector('#gatewayPanel');
    if (!panel) {
      panel=document.createElement('div'); panel.id='gatewayPanel'; panel.className='card'; panel.style.marginTop='12px';
      const anchor=document.querySelector('.connect-grid');
      const parent=(anchor && anchor.parentElement) || document.querySelector('main') || document.body;
      if (anchor && anchor.parentElement) anchor.insertAdjacentElement('afterend', panel); else parent.appendChild(panel);
    }
    const aiChip=document.querySelector('#aiStatusChip');
    if (aiChip) aiChip.onclick=()=>{if(typeof window.activateTab==='function')window.activateTab('connect');};
    const baseRender=window.renderOverview;
    if (typeof baseRender==='function') {
      window.renderOverview=function(data){baseRender(data);refreshOverviewDerived();};
    }
    refreshGatewayPanel(); refreshAiAccessSurface(); refreshCaptureStatus(); refreshOverviewDerived();
    // Server state every 5 s while the dashboard is visible; the recording
    // clock ticks locally in between so it still counts every second.
    setInterval(()=>{if(!document.hidden){refreshGatewayPanel();refreshAiAccessSurface();refreshCaptureStatus();refreshOverviewDerived();}},5000);
    setInterval(()=>{if(!document.hidden)tickClock();},1000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshCaptureStatus();refreshAiAccessSurface();refreshOverviewDerived();}});
  });
})();
