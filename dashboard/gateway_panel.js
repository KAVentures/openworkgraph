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
    return `<div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap"><div><h2 style="margin:0 0 6px">Organization Gateway</h2><div><strong>${e(modeLabel(s))}</strong> · ${url}</div><div class="muted" style="margin-top:5px">${e(last)}${s.worker_running?' · sync worker running':''}</div></div><div class="modal-actions">${controls}</div></div>${err}${quarantine}<div class="note" style="margin-top:10px">Local recording always continues independently. New connections share evidence captured <strong>after enrollment</strong>; earlier local history stays local. While sharing is paused, evidence still records locally but that paused interval is <strong>never backfilled</strong> on resume. No OpenWorkGraph cloud account is required.</div>`;
  }

  function updateGatewayChip(s) {
    const chip=document.querySelector('#gatewayStatusChip');
    if (!chip) return;
    if (s.mode==='connected_paused') {
      chip.textContent='Organization sharing: paused';
      chip.classList.remove('on'); chip.classList.add('off');
    } else if (s.mode==='connected' && !s.last_error) {
      chip.textContent='Organization sharing: on';
      chip.classList.add('on'); chip.classList.remove('off');
    } else if (s.mode==='connected' && s.last_error) {
      chip.textContent='Organization sharing: sync error';
      chip.classList.add('off'); chip.classList.remove('on');
    } else {
      chip.textContent='Organization sharing: off';
      chip.classList.remove('on','off');
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
    const panel=document.querySelector('#aiAccessPanel');
    const chip=document.querySelector('#aiStatusChip');
    try {
      const [access, activity, http] = await Promise.all([
        gwCall('/v1/ai-access'),
        gwCall('/v1/mcp-activity?limit=20'),
        gwCall('/v1/mcp-http'),
      ]);
      const reads=(activity.items||[]).filter(x=>x.status!=='denied').length;
      if (chip) {
        chip.textContent=access.enabled ? `AI access: on · ${reads} reads` : 'AI access: off · Enable';
        chip.classList.toggle('on',!!access.enabled);
        chip.classList.toggle('off',!access.enabled);
      }
      if (panel) {
        const activityHtml=(activity.items||[]).slice(0,8).map(x=>`<div style="padding:6px 0;border-bottom:1px solid #eceee8"><strong>${e(String(x.observed_at||'').replace('T',' ').slice(11,19))}</strong> · ${e(x.tool||'MCP read')} · ${Number(x.rows||0)} rows${x.status==='denied'?' · denied':''}</div>`).join('') || '<span class="muted">No MCP reads this run.</span>';
        panel.innerHTML=`<div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap"><div><h2 style="margin:0 0 6px">AI access</h2><div><strong>${access.enabled?'ON for this run':'OFF'}</strong></div><div class="muted" style="margin-top:4px">Off by default on every launch. Every MCP tool call is checked live.</div></div><div class="modal-actions"><button id="aiAccessToggle" class="${access.enabled?'secondary':'green'}">${access.enabled?'Turn AI access off':'Enable AI access'}</button>${http.running?'<button id="stopHttpMcp" class="secondary">Stop HTTP MCP</button>':''}</div></div><div style="margin-top:14px"><strong style="font-size:12px">Recent AI activity</strong><div class="muted" style="margin-top:5px">${activityHtml}</div></div>`;
        panel.querySelector('#aiAccessToggle').onclick=async()=>{
          await gwCall('/v1/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!access.enabled})});
          await refreshAiAccessSurface();
        };
        const stop=panel.querySelector('#stopHttpMcp');
        if (stop) stop.onclick=async()=>{await gwCall('/v1/mcp-http',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'stop'})});await refreshAiAccessSurface();};
      }
    } catch (_) {
      if (chip) chip.textContent='AI access: unavailable';
      if (panel) panel.innerHTML='<h2>AI access</h2><div class="muted">Secure AI-access status is unavailable.</div>';
    }
  }
  window.refreshDashboardAiAccess=refreshAiAccessSurface;

  window.openGatewaySetup = function() {
    if (typeof window.openModal !== 'function') return;
    const content=`<p>Connect this endpoint to a <strong>customer-controlled OpenWorkGraph Gateway</strong>. Prefer a short-lived, single-use enrollment code issued by the Gateway administrator.</p><div class="note" style="margin-bottom:12px"><strong>Shared by default after you connect:</strong> privacy-hardened app/window/page context, timestamps and effort/interaction metadata, safe UI/semantic labels, and copy/cut/paste occurrence/linkage. <strong>Pre-enrollment history stays local.</strong><br><br><strong>Never sent by this Gateway path:</strong> typed text, clipboard contents, ordinary key identities, password values, or screenshot bytes. Organization policy may further reduce what is shared; it cannot broaden your local sharing policy.</div><label for="gwUrl">Gateway URL</label><input id="gwUrl" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="https://openworkgraph.company.internal"><label for="gwOrg">Organization ID</label><input id="gwOrg" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="acme"><label for="gwActor">Actor ID <span class="muted">(optional; normally bound by the enrollment code)</span></label><input id="gwActor" style="width:100%;box-sizing:border-box;margin:5px 0 10px"><label for="gwToken">Enrollment code</label><input id="gwToken" type="password" autocomplete="off" style="width:100%;box-sizing:border-box;margin:5px 0 10px"><div class="note">Non-local Gateways require HTTPS. Nothing is shared until enrollment succeeds. Pausing later keeps that paused interval permanently local while local capture continues.</div><div class="modal-actions"><button id="gwConnectBtn">Connect and share from now</button><button class="secondary" onclick="closeModal()">Cancel</button></div>`;
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
    if (!enabled && !confirm('Pause organization sharing? Local capture will continue, but evidence captured while paused will stay local permanently and will not be uploaded when you resume.')) return;
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
    if (aiChip) aiChip.onclick=async()=>{
      try { const access=await gwCall('/v1/ai-access'); await gwCall('/v1/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!access.enabled})}); await refreshAiAccessSurface(); }
      catch (_) {}
    };
    refreshGatewayPanel();
    refreshAiAccessSurface();
    setInterval(()=>{if(!document.hidden){refreshGatewayPanel();refreshAiAccessSurface();}},5000);
  });
})();
