(() => {
  async function gwCall(url, options={}) {
    await window.__owgAuthReady;
    const r = await fetch(url, {cache:'no-store', ...options});
    let d={}; try { d=await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(d.detail || d.error || 'Gateway request failed');
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
    const err = s.last_error ? `<div class="note" style="margin-top:8px"><strong>Sync error:</strong> ${e(s.last_error)}</div>` : '';
    const controls = !connected
      ? `<button class="green" onclick="openGatewaySetup()">Connect to organization</button>`
      : `<button class="${s.sharing_paused?'green':'secondary'}" onclick="setGatewaySharing(${s.sharing_paused?'true':'false'})">${s.sharing_paused?'Resume sharing':'Pause sharing'}</button><button class="secondary" onclick="disconnectGateway()">Disconnect</button>`;
    return `<div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap"><div><h3 style="margin:0 0 6px">Organization Gateway</h3><div><strong>${e(modeLabel(s))}</strong> · ${url}</div><div class="muted" style="margin-top:5px">${e(last)}${s.worker_running?' · sync worker running':''}</div></div><div class="modal-actions">${controls}</div></div>${err}<div class="note" style="margin-top:10px">Local recording always continues independently. No OpenWorkGraph cloud account is required; the Gateway can run entirely inside your organization. Raw rich evidence remains the canonical layer.</div>`;
  }

  window.refreshGatewayPanel = async function() {
    const panel=document.querySelector('#gatewayPanel');
    if (!panel) return;
    try { panel.innerHTML=statusHtml(await gwCall('/v1/gateway-status')); }
    catch (err) { panel.innerHTML=`<h3>Organization Gateway</h3><div class="off">Status unavailable: ${e(err.message)}</div>`; }
  };

  window.openGatewaySetup = function() {
    if (typeof window.openModal !== 'function') return;
    const content=`<p>Connect this endpoint to a <strong>customer-controlled OpenWorkGraph Gateway</strong>. The enrollment token is used once and is not stored.</p><label>Gateway URL</label><input id="gwUrl" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="https://openworkgraph.company.internal"><label>Organization ID</label><input id="gwOrg" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="acme"><label>Actor ID <span class="muted">(optional)</span></label><input id="gwActor" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="employee or pseudonymous ID"><label>Enrollment token</label><input id="gwToken" type="password" autocomplete="off" style="width:100%;box-sizing:border-box;margin:5px 0 10px" placeholder="One-time/admin enrollment token"><div class="note">Non-local Gateways require HTTPS. Nothing is shared until enrollment succeeds. You can pause sharing later without stopping local capture.</div><div class="modal-actions" style="margin-top:14px"><button id="gwConnectBtn">Connect</button><button class="secondary" onclick="closeModal()">Cancel</button></div>`;
    openModal('Connect organization Gateway','Self-hosted / customer-controlled',content);
    document.querySelector('#gwConnectBtn').onclick=async function(){
      const btn=this; btn.disabled=true; btn.textContent='Connecting…';
      try {
        const payload={gateway_url:document.querySelector('#gwUrl').value,organization_id:document.querySelector('#gwOrg').value,actor_id:document.querySelector('#gwActor').value,enrollment_token:document.querySelector('#gwToken').value,verify_tls:true,allow_insecure_http:false};
        document.querySelector('#gwToken').value='';
        await gwCall('/v1/gateway-enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        closeModal(); await refreshGatewayPanel();
      } catch(err) { btn.disabled=false; btn.textContent='Connect'; alert(err.message); }
    };
  };

  window.setGatewaySharing = async function(enabled) {
    try {
      await gwCall('/v1/gateway-sharing',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!enabled})});
      await refreshGatewayPanel();
    } catch(err) { alert(err.message); }
  };

  window.disconnectGateway = async function() {
    if (!confirm('Disconnect this computer from the organization Gateway? The device credential will be revoked first. Local OpenWorkGraph data will remain on this computer.')) return;
    try {
      await gwCall('/v1/gateway-disconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force_local:false})});
      await refreshGatewayPanel();
    } catch(err) { alert(err.message); }
  };

  document.addEventListener('DOMContentLoaded', () => {
    if (document.querySelector('#gatewayPanel')) return;
    const panel=document.createElement('div'); panel.id='gatewayPanel'; panel.className='card'; panel.style.marginTop='12px';
    const anchor=document.querySelector('#aiAccessPanel') || document.querySelector('.connect-grid');
    const parent=(anchor && anchor.parentElement) || document.querySelector('main') || document.body;
    if (anchor && anchor.parentElement) anchor.insertAdjacentElement('afterend', panel); else parent.appendChild(panel);
    refreshGatewayPanel();
    setInterval(()=>{if(!document.hidden)refreshGatewayPanel()},5000);
  });
})();
