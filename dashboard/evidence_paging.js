(() => {
  const PAGE_SIZE=100;
  let selectedSurface='';
  let cursors=[null];
  let pageIndex=0;
  let lastQuery='';
  let lastSummary=null;
  let lastPayload=null;
  let debounceTimer=null;
  let requestSerial=0;
  let inFlight=false;

  const html=value=>typeof window.esc==='function'?window.esc(String(value??'')):String(value??'');
  const displaySurface=value=>typeof window.surfaceName==='function'?window.surfaceName(value):String(value||'Unknown');
  const color=value=>typeof window.surfaceColor==='function'?window.surfaceColor(value):'#657';

  function currentQuery() {
    return String(document.querySelector('#evidenceSearch')?.value||'').trim();
  }

  function resetPages() {
    cursors=[null];
    pageIndex=0;
    lastPayload=null;
  }

  function endpoint(cursor) {
    const params=new URLSearchParams({scope:'current',limit:String(PAGE_SIZE)});
    const q=currentQuery();
    if (q) params.set('q',q);
    if (selectedSurface) params.set('surface',selectedSurface);
    if (cursor) params.set('cursor',cursor);
    return `/v1/evidence?${params.toString()}`;
  }

  function diagnostics(summary) {
    if (!summary) return;
    const seq=document.querySelector('#seqTable');
    if (seq) seq.innerHTML=(summary.frequent_sequences||[]).map(x=>`<tr><td>${(x.sequence||[]).map(a=>`<span class="pill">${html(displaySurface(a))}</span>`).join(' → ')}</td><td>${Number(x.count||0)}</td></tr>`).join('')||'<tr><td colspan="2">No navigation loops yet.</td></tr>';
    const transitions=document.querySelector('#transitionsTable');
    if (transitions) transitions.innerHTML=(summary.transitions||[]).map(x=>`<tr><td>${html(displaySurface(x.from))} → ${html(displaySurface(x.to))}</td><td>${Number(x.count||0)}</td></tr>`).join('')||'<tr><td colspan="2">No transitions yet.</td></tr>';
  }

  function ensureControls() {
    const meta=document.querySelector('#evidenceTableView .evidence-meta');
    if (!meta) return null;
    let controls=document.querySelector('#evidencePagingControls');
    if (!controls) {
      controls=document.createElement('div');
      controls.id='evidencePagingControls';
      controls.style.cssText='display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-left:auto';
      controls.innerHTML='<button type="button" class="secondary" id="evidencePrevPage">Previous</button><span class="muted" id="evidencePageNumber"></span><button type="button" class="secondary" id="evidenceNextPage">Next</button>';
      meta.appendChild(controls);
      controls.querySelector('#evidencePrevPage').onclick=()=>{
        if (pageIndex<=0) return;
        pageIndex-=1;
        loadPage();
      };
      controls.querySelector('#evidenceNextPage').onclick=()=>{
        if (!lastPayload?.next_cursor) return;
        const next=lastPayload.next_cursor;
        cursors=cursors.slice(0,pageIndex+1);
        cursors.push(next);
        pageIndex+=1;
        loadPage();
      };
    }
    return controls;
  }

  function renderSurfaceFilters(facets) {
    const host=document.querySelector('#surfaceFilters');
    if (!host) return;
    const rows=Array.isArray(facets)?facets:[];
    host.innerHTML=`<button type="button" class="filter-chip ${selectedSurface?'':'active'}" data-page-surface="">All</button>`+rows.map(row=>{
      const raw=String(row.surface||'Unknown');
      const label=displaySurface(raw);
      return `<button type="button" class="filter-chip ${selectedSurface===raw?'active':''}" data-page-surface="${html(raw)}"><span class="surface-dot" style="--surface:${color(label)}"></span>${html(label)} <span aria-hidden="true">·</span> ${Number(row.count||0)}</button>`;
    }).join('');
    host.querySelectorAll('[data-page-surface]').forEach(button=>button.onclick=()=>{
      selectedSurface=button.dataset.pageSurface||'';
      resetPages();
      loadPage();
    });
  }

  function renderPage(payload) {
    lastPayload=payload;
    renderSurfaceFilters(payload.surfaces||[]);
    const body=document.querySelector('#evidenceTable');
    if (body) body.innerHTML=(payload.items||[]).map(item=>{
      const surface=displaySurface(item.surface||'Unknown');
      const page=item.page||item.resource_title||'';
      return `<tr><td>${item.observed_at?new Date(item.observed_at).toLocaleTimeString():''}</td><td><span class="surface-dot" style="--surface:${color(surface)}"></span>${html(surface)}</td><td class="mono">${html(page)}</td><td>${html(item.action||item.event_type||'')}</td></tr>`;
    }).join('')||'<tr><td colspan="4">No matching evidence.</td></tr>';

    const count=document.querySelector('#evidenceCount');
    if (count) {
      count.textContent=payload.total
        ? `Showing ${Number(payload.position_start||0)}–${Number(payload.position_end||0)} of ${Number(payload.total||0)} events`
        : '0 of 0 events';
    }
    const controls=ensureControls();
    if (controls) {
      const prev=controls.querySelector('#evidencePrevPage');
      const next=controls.querySelector('#evidenceNextPage');
      const label=controls.querySelector('#evidencePageNumber');
      prev.disabled=pageIndex===0;
      next.disabled=!payload.next_cursor;
      label.textContent=`Page ${pageIndex+1}`;
    }
  }

  async function loadPage() {
    if (document.querySelector('#evidenceTableView')?.hidden) return;
    if (inFlight) requestSerial+=1;
    const serial=++requestSerial;
    inFlight=true;
    try {
      await window.__owgAuthReady;
      const response=await fetch(endpoint(cursors[pageIndex]),{cache:'no-store'});
      let payload={};
      try { payload=await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(payload.detail||'Could not load evidence.');
      if (serial!==requestSerial) return;
      renderPage(payload);
    } catch (error) {
      if (serial!==requestSerial) return;
      const body=document.querySelector('#evidenceTable');
      if (body) body.innerHTML=`<tr><td colspan="4">${html(error.message||'Could not load evidence.')}</td></tr>`;
    } finally {
      if (serial===requestSerial) inFlight=false;
    }
  }
  window.refreshPagedEvidence=loadPage;

  function schedulePageLoad(delay=0) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer=setTimeout(loadPage,delay);
  }

  function pagedRenderEvidence(summary) {
    lastSummary=summary||lastSummary;
    diagnostics(lastSummary);
    if (document.querySelector('#evidenceTableView')?.hidden) return;
    const q=currentQuery();
    if (q!==lastQuery) {
      lastQuery=q;
      resetPages();
      schedulePageLoad(250);
      return;
    }
    schedulePageLoad(0);
  }

  function install() {
    window.renderEvidence=pagedRenderEvidence;
    const input=document.querySelector('#evidenceSearch');
    if (input) input.addEventListener('input',()=>{
      const q=currentQuery();
      if (q!==lastQuery) {
        lastQuery=q;
        resetPages();
      }
      schedulePageLoad(250);
    });
    document.querySelectorAll('[data-evidence-view]').forEach(button=>button.addEventListener('click',()=>{
      if (button.dataset.evidenceView==='all') schedulePageLoad(0);
    }));
    ensureControls();
    if (window.__owgLastSummary) pagedRenderEvidence(window.__owgLastSummary);
  }

  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
