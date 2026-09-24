(() => {
  const esc = value => typeof window.esc === 'function'
    ? window.esc(String(value ?? ''))
    : String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function fmt(seconds) {
    const s=Math.max(0,Number(seconds||0));
    if(s<60) return `${Math.round(s)}s`;
    const m=Math.floor(s/60), r=Math.round(s%60);
    if(m<60) return r?`${m}m ${r}s`:`${m}m`;
    const h=Math.floor(m/60), mm=m%60;
    return mm?`${h}h ${mm}m`:`${h}h`;
  }
  function fmtMs(ms){ return fmt(Number(ms||0)/1000); }

  function ensureCard(){
    const panel=document.querySelector('#panel-overview');
    if(!panel) return null;
    let card=document.querySelector('#workProfileCard');
    if(card) return card;
    card=document.createElement('section');
    card.className='card';
    card.id='workProfileCard';
    card.innerHTML=`
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
        <div><h2 style="margin-bottom:4px">Work profile</h2><div class="muted">Locally derived workflow signals. These are not employee productivity scores.</div></div>
        <span class="badge">Evidence-backed · review candidates</span>
      </div>
      <div id="workProfileMetrics" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:14px"></div>
      <div class="row" style="margin-top:13px">
        <div><h3>Manual transfers</h3><div class="muted">Linked copy/cut → paste actions; clipboard contents are never read.</div><div id="workProfileTransfers" class="table-wrap"></div></div>
        <div><h3>AI-tool usage</h3><div class="muted">Observed time/actions on AI surfaces only; OWG does not infer prompt content.</div><div id="workProfileAi" class="table-wrap"></div></div>
      </div>
      <div class="row" style="margin-top:10px">
        <div><h3>Observed rhythm</h3><div id="workProfileRhythm" class="muted">—</div></div>
        <div><h3>Navigation / hunting candidates</h3><div class="muted">Repeated-resource signals only; review before interpreting them as friction.</div><div id="workProfileHunting"></div></div>
      </div>
      <div class="row" style="margin-top:10px">
        <div><h3>Tool waiting</h3><div class="muted">Rounded browser navigation timing. Observed loading time is not automatically wasted time.</div><div id="workProfileWaiting"></div></div>
        <div><h3>Friction candidates</h3><div class="muted">Derived from existing safe click/navigation evidence; no extra click or login sensor.</div><div id="workProfileFriction"></div></div>
      </div>
      <div id="workProfileFileMoves" style="margin-top:10px"></div>
      <div class="note" style="margin-top:13px">
        <strong>Voluntary self-tag</strong>
        <div class="muted" style="margin:3px 0 9px">Add ground truth for recent work. This is user-provided context, not sensed behavior.</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
          <label style="font-size:12px;font-weight:700">Category<br><select id="selfTagCategory" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:7px 9px;background:#fff"><option>Routine admin</option><option>Firefighting</option><option>Blocked</option><option>Deep work</option><option>Customer work</option><option>Other</option></select></label>
          <label style="font-size:12px;font-weight:700">Recent span<br><select id="selfTagMinutes" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:7px 9px;background:#fff"><option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option></select></label>
          <button type="button" class="secondary" id="saveSelfTag">Tag recent work</button><span id="selfTagStatus" class="muted"></span>
        </div>
      </div>`;
    const metrics=panel.querySelector('.metrics');
    if(metrics?.nextSibling) panel.insertBefore(card,metrics.nextSibling); else panel.prepend(card);
    card.querySelector('#saveSelfTag')?.addEventListener('click',saveTag);
    return card;
  }

  function metric(label,value,help=''){
    return `<div class="metric-card" style="min-height:82px"><div class="metric" style="font-size:21px">${esc(value)}</div><div class="label">${esc(label)}</div>${help?`<div class="muted" style="margin-top:4px">${esc(help)}</div>`:''}</div>`;
  }

  function render(profile){
    ensureCard();
    const f=profile.fragmentation||{};
    const metrics=document.querySelector('#workProfileMetrics');
    if(metrics) metrics.innerHTML=[
      metric('Surface switches',Number(f.surface_switches||0),`${Number(f.surface_switches_per_foreground_hour||0).toFixed(1)}/foreground hour`),
      metric('Median focus span',fmt(f.median_focus_span_seconds)),
      metric('Longest stretch',fmt(f.longest_uninterrupted_surface_seconds),f.longest_uninterrupted_surface||''),
      metric('Manual transfers',Number(profile.cross_surface_manual_transfer_count||0),'cross-surface linked transfers'),
    ].join('');

    const transfers=(profile.manual_transfer_patterns||[]).filter(x=>x.cross_surface).slice(0,8);
    const transferHost=document.querySelector('#workProfileTransfers');
    if(transferHost) transferHost.innerHTML=transfers.length
      ? `<table style="min-width:0"><thead><tr><th>From</th><th>To</th><th>Count</th></tr></thead><tbody>${transfers.map(x=>`<tr><td>${esc(x.source_surface)}</td><td>${esc(x.destination_surface)}</td><td>${Number(x.count||0)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="muted" style="margin-top:10px">No linked cross-surface transfers in this scope.</div>';

    const ai=profile.ai_tool_usage||[];
    const aiHost=document.querySelector('#workProfileAi');
    if(aiHost) aiHost.innerHTML=ai.length
      ? `<table style="min-width:0"><thead><tr><th>Surface</th><th>Engaged</th><th>In / out</th></tr></thead><tbody>${ai.map(x=>`<tr><td>${esc(x.surface)}</td><td>${esc(fmt(x.engaged_seconds))}</td><td>${Number(x.transfers_in||0)} / ${Number(x.transfers_out||0)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="muted" style="margin-top:10px">No supported AI surface observed in this scope.</div>';

    const days=profile.daily_rhythm?.days||[];
    const rhythm=document.querySelector('#workProfileRhythm');
    if(rhythm){
      if(!days.length) rhythm.textContent='No timing evidence yet.';
      else {
        const latest=days[days.length-1];
        const first=new Date(latest.first_observed_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
        const last=new Date(latest.last_observed_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
        rhythm.innerHTML=`Latest observed day: <strong>${esc(first)}–${esc(last)}</strong> · ${esc(fmt(latest.engaged_seconds))} engaged · ${Number(latest.long_gap_count||0)} observed gaps ≥5m.<br><span class="muted">Gaps are not automatically treated as breaks.</span>`;
      }
    }

    const hunting=(profile.navigation_hunting_candidates||[]).slice(0,5);
    const huntingHost=document.querySelector('#workProfileHunting');
    if(huntingHost) huntingHost.innerHTML=hunting.length
      ? hunting.map(x=>`<div style="padding:7px 0;border-bottom:1px solid #eceee8"><strong>${esc(x.surface)}</strong> · ${Number(x.visit_count||0)} visits<div class="muted mono" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.resource_locator||'')}</div></div>`).join('')
      : '<div class="muted" style="margin-top:8px">No repeated-resource candidates yet.</div>';

    const waiting=(profile.tool_waiting||[]).slice(0,5);
    const waitingHost=document.querySelector('#workProfileWaiting');
    if(waitingHost) waitingHost.innerHTML=waiting.length
      ? waiting.map(x=>`<div style="padding:7px 0;border-bottom:1px solid #eceee8"><strong>${esc(x.surface)}</strong> · median load ${esc(fmtMs(x.median_load_ms))}<div class="muted">${Number(x.navigation_count||0)} observed navigations · p95 ${esc(fmtMs(x.p95_load_ms))}</div></div>`).join('')
      : '<div class="muted" style="margin-top:8px">No browser timing samples yet.</div>';

    const clicks=(profile.rapid_click_candidates||[]).slice(0,3);
    const auth=(profile.auth_flow_candidates||[]).slice(-3);
    const frictionHost=document.querySelector('#workProfileFriction');
    if(frictionHost){
      const rows=[];
      for(const x of clicks) rows.push(`<div style="padding:7px 0;border-bottom:1px solid #eceee8"><strong>Rapid-click candidate</strong> · ${esc(x.surface)}<div class="muted">${Number(x.max_clicks_in_1_5s||0)} clicks/1.5s on one safe target · Needs review</div></div>`);
      for(const x of auth) rows.push(`<div style="padding:7px 0;border-bottom:1px solid #eceee8"><strong>Auth-flow candidate</strong> · ${esc(x.surface)}<div class="muted">Observed ${esc(fmt(x.duration_seconds))} in auth-path sequence · Needs review</div></div>`);
      frictionHost.innerHTML=rows.join('')||'<div class="muted" style="margin-top:8px">No friction candidates yet.</div>';
    }

    const uploads=profile.file_upload_categories||[];
    const uploadHost=document.querySelector('#workProfileFileMoves');
    if(uploadHost) uploadHost.innerHTML=uploads.length
      ? `<h3>Optional file-upload categories</h3><div class="muted">Coarse MIME categories only; no filenames, paths, sizes or file contents.</div><div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:7px">${uploads.slice(0,12).map(x=>`<span class="pill">${esc(x.surface)} · ${esc(x.category)} ×${Number(x.file_count||0)}</span>`).join('')}</div>`
      : '';
  }

  let loading=false;
  async function refresh(){
    ensureCard();
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if(active!=='overview' || loading) return;
    loading=true;
    try{
      await window.__owgAuthReady;
      const r=await fetch('/v1/work-profile?scope=current',{cache:'no-store'});
      if(!r.ok) throw new Error('Could not load work profile');
      render(await r.json());
    }catch(_){
      const metrics=document.querySelector('#workProfileMetrics');
      if(metrics && !metrics.innerHTML) metrics.innerHTML='<div class="muted">Work profile unavailable.</div>';
    }finally{loading=false;}
  }
  window.refreshWorkProfile=refresh;

  async function saveTag(){
    const status=document.querySelector('#selfTagStatus');
    try{
      await window.__owgAuthReady;
      const category=document.querySelector('#selfTagCategory')?.value||'';
      const minutes=Number(document.querySelector('#selfTagMinutes')?.value||15);
      const r=await fetch('/v1/self-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category,minutes_back:minutes})});
      const d=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(d.detail||'Could not save tag');
      if(status) status.textContent='Saved locally.';
      if(typeof window.showToast==='function') window.showToast('Self-tag saved locally.');
      refresh();
    }catch(e){ if(status) status.textContent=e.message||'Could not save tag.'; }
  }

  function install(){ensureCard();document.querySelector('#tab-overview')?.addEventListener('click',()=>setTimeout(refresh,0));refresh();setInterval(()=>{if(!document.hidden) refresh();},5000);}
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
