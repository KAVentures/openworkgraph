(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let period = 'week', start = '', end = '', generation = 0, pending = null;
  const staleScope = 'Work period changed. Prepare a new AI handoff for the selected period.';
  const dateInput = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  function selection() {
    if (period === 'current' || period === 'all') return {scope: period};
    const now = new Date(), a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const b = new Date(a); b.setDate(b.getDate()+1);
    if (period === 'week') a.setDate(a.getDate()-6);
    if (period === 'custom') {
      if (!start || !end) throw new Error('Choose both dates.');
      const aa = new Date(start+'T00:00:00'), bb = new Date(end+'T00:00:00');
      bb.setDate(bb.getDate()+1);
      if (!Number.isFinite(+aa) || !Number.isFinite(+bb) || aa >= bb) throw new Error('End date must be on or after start.');
      return {scope:'range',since:aa.toISOString(),until:bb.toISOString()};
    }
    return {scope:'range',since:a.toISOString(),until:b.toISOString()};
  }
  window.owgEvidenceSelection = selection;
  async function api(path, options, stillCurrent=()=>true) {
    await window.__owgAuthReady;
    // An old auth wait must not start a read/export for a superseded scope.
    if(!stillCurrent())throw new Error(staleScope);
    const r = await fetch(path,{cache:'no-store',...options});
    const d = await r.json().catch(()=>({}));
    if (!r.ok) throw new Error(d.detail || 'Could not prepare evidence.');
    return d;
  }
  async function download(fmt, detailed, redacted=true, chosen=selection(), stillCurrent=()=>true) {
    const d = await api('/v1/export-ticket',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({format:fmt,...chosen,include_raw:!!detailed,redact_names:!!redacted})},stillCurrent);
    // A delayed ticket response cannot initiate a download after period change.
    if(!stillCurrent())throw new Error(staleScope);
    const a = document.createElement('a');a.href=d.url;a.download='';a.hidden=true;
    document.body.appendChild(a);a.click();a.remove();
  }
  function syncControls() {
    document.querySelectorAll('.owg-period').forEach(node => {
      node.querySelector('select').value=period;
      node.querySelector('[data-custom]').hidden=period!=='custom';
      node.querySelector('[data-start]').value=start;node.querySelector('[data-end]').value=end;
    });
  }
  async function refresh() {
    const seq=++generation;
    pending?.abort(); pending=new AbortController();
    document.querySelectorAll('[data-period-summary]').forEach(x=>{
      x.setAttribute('aria-busy','true');
      x.innerHTML='<progress aria-label="Checking selected evidence"></progress> Checking selected evidence…';
    });
    try {
      const d=await api('/v1/analysis-preview?'+new URLSearchParams(selection()),{signal:pending.signal},()=>seq===generation);
      if(seq!==generation)return null;
      const text=d.canonical_rows
        ? `${d.canonical_rows} recorded events · observed ${new Date(d.first_observed_at).toLocaleString()} – ${new Date(d.last_observed_at).toLocaleString()}`
        : d.retained_rows ? 'No activity in this selection. Saved work is available; choose another period.' : 'No saved evidence yet. Work normally after enabling recording.';
      document.querySelectorAll('[data-period-summary]').forEach(x=>{x.textContent=text;x.removeAttribute('aria-busy');});
      return d;
    } catch(e) {
      if(seq!==generation)return null;
      document.querySelectorAll('[data-period-summary]').forEach(x=>{x.textContent=e.message;x.removeAttribute('aria-busy');});
      return null;
    }
  }
  async function prepare() {
    const seq=generation+1;
    const d=await refresh();
    // The response must belong to this click, not a newer period refresh.
    if(seq!==generation)return;
    if(!d){window.toast?.('Could not prepare selected evidence. Check the period and dashboard connection.');return;}
    const request=d.request_text;
    const chosen={scope:d.window.scope,...(d.window.scope==='range'?{since:d.window.since,until:d.window.until}:{})};
    const access=d.local_ai_history_access || {};
    const localRead=!d.local_ai_enabled ? 'Local AI access is off.' : access.mode==='off' ? 'Local AI can read the current run only; saved-history access is off.' : `Local saved-history permission: ${access.mode}${access.expires_at?' until '+new Date(access.expires_at).toLocaleString():''}.`;
    window.openModal?.('Prepare for AI','Discover opportunities from recorded work',`
      <p>No workflow description or repeated pattern is required. Your AI will interpret the evidence; this panel does not run a model.</p>
      <div class="note"><strong>What the AI will receive</strong>
        <p>${esc(d.canonical_rows)} recorded events · ${esc(d.event_types.focus_span||0)} focus spans · ${esc(d.event_types.capture_gap||0)} unobserved-gap records.</p>
        <p>Selected: ${esc(d.window.since||'all available history')} to ${esc(d.window.until||'the latest retained evidence')} (end exclusive, UTC). Observed: ${esc(d.first_observed_at||'none')} to ${esc(d.last_observed_at||'none')}.</p>
        <p>Sources: ${esc(Object.entries(d.sources).map(([k,v])=>`${k}: ${v}`).join(', ')||'none')}. Visible work text is not included in this activity package. Typed values and clipboard contents are not included.</p>
        <p>Latest work event: ${esc(d.work_evidence?.last_work_observed_at||'none in this period')}. Capture health and gaps are shown separately; they do not confirm the recorder is running.</p>
        <p>${esc(localRead)} Hosted AI availability must be checked separately: it only sees previously shared evidence. Preparing a file does not change those permissions.</p>
      </div>
      <details><summary>Analyze a large history in smaller parts</summary><p>Ask your AI to process one day at a time, follow every page, and keep track of what it has examined. Repeated-work maps are optional. The file download still contains the full selected period.</p><ul>${(d.analysis_plan?.date_chunks||[]).map(x=>`<li>${esc(x.since)} to ${esc(x.until)}: ${esc(x.event_rows)} events</li>`).join('')}</ul>${d.analysis_plan?.more_dates_not_listed?'<p>Only the first 31 dates are listed. Continue through the remaining selected dates.</p>':''}</details>
      <details><summary>Preview redacted context examples</summary><ul>${d.redacted_examples.map(x=>`<li>${esc(x.app)} · ${esc(x.window_title||'Untitled activity')}</li>`).join('')||'<li>No focus spans in this selection.</li>'}</ul><p>Filtering is best effort; inspect the downloaded package before sharing it.</p></details>
      <label style="display:flex;gap:8px;margin:12px 0"><input id="analysisDetailed" type="checkbox">Include detailed chronological evidence (more events and labels for deeper investigation)</label>
      <p>Downloaded evidence is redacted. Gaps and foreground time are not measured lost work or savings.</p>
      <div class="modal-actions"><button type="button" id="analysisDownload" ${d.canonical_rows?'':'disabled'}>Download AI evidence package</button><button type="button" class="secondary" id="analysisCopy">Copy request for my AI</button></div>
      <details style="margin-top:12px"><summary>Read the request</summary><pre style="white-space:pre-wrap" id="analysisRequest"></pre></details>
      <div class="muted" id="analysisActionStatus" role="status">Nothing has been sent to an AI. Downloads work with any capable AI, without an OWG cloud account.</div>`);
    $('#analysisRequest').textContent=request;
    const current=()=>seq===generation;
    const expired=()=>{
      if(current())return false;
      $('#analysisActionStatus').textContent=staleScope+' Nothing was copied or downloaded by this action.';
      return true;
    };
    $('#analysisCopy').onclick=async()=>{
      if(expired())return;
      try{
        await navigator.clipboard.writeText(request);
        $('#analysisActionStatus').textContent=current()
          ? 'Request copied. Paste it into your AI; no model was invoked.'
          : 'The work period changed during copying. The copied request refers to the previous period; prepare again.';
      }catch(_){$('#analysisActionStatus').textContent='Clipboard unavailable. Open “Read the request” and copy the text.';}
    };
    $('#analysisDownload').onclick=async()=>{
      if(expired())return;
      try{
        await download('csvzip',$('#analysisDetailed').checked,true,chosen,current);
        $('#analysisActionStatus').textContent='Download started. Inspect the file, then attach it to your AI. No data was uploaded.';
      }catch(e){$('#analysisActionStatus').textContent=e.message;}
    };
  }
  function install() {
    const style=document.createElement('style');style.textContent='.owg-period{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#fff}.owg-period select,.owg-period input{font:inherit;padding:7px;border:1px solid var(--line);border-radius:7px}.owg-period [data-period-summary]{flex-basis:100%;font-size:12px;color:var(--muted)}.owg-period [data-custom]{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.owg-period [hidden]{display:none!important}';document.head.appendChild(style);
    const today=new Date();start=dateInput(new Date(today.getFullYear(),today.getMonth(),today.getDate()-6));end=dateInput(today);
    for(const id of ['overview','evidence','connect','export']) {
      const panel=$('#panel-'+id);if(!panel)continue;
      const bar=document.createElement('div');bar.className='owg-period';
      bar.innerHTML=`<strong>Work period</strong><select aria-label="Work period"><option value="today">Today</option><option value="week">Last 7 days</option><option value="custom">Custom dates</option><option value="current">This run</option><option value="all">All retained work</option></select><span data-custom hidden><label>From <input type="date" data-start></label><label>Through <input type="date" data-end></label></span><span class="muted">${esc(Intl.DateTimeFormat().resolvedOptions().timeZone||'local time')}</span><button type="button" class="secondary">Prepare for AI</button><span data-period-summary role="status"></span>`;
      // Scope applies to handoff/exports; existing current-run dashboards retain their labels.
      const note=document.createElement('small');note.className='muted';note.textContent='Select the period to view in Activity or prepare for AI. Activity uses this period; current-run charts keep their labeled scope.';bar.appendChild(note);
      panel.insertBefore(bar,panel.firstChild);
      bar.querySelector('select').onchange=e=>{period=e.target.value;syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));};
      bar.querySelectorAll('input').forEach(x=>x.onchange=()=>{start=bar.querySelector('[data-start]').value;end=bar.querySelector('[data-end]').value;syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));});
      bar.querySelector('button').onclick=prepare;
    }
    syncControls();refresh();document.dispatchEvent(new Event('owg:period-change'));
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
