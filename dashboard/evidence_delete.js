(() => {
  const esc = value => {
    if (typeof window.esc === 'function') return window.esc(String(value ?? ''));
    const node=document.createElement('div'); node.textContent=String(value ?? ''); return node.innerHTML;
  };

  async function apiDelete(since, until) {
    await window.__owgAuthReady;
    const response=await fetch('/v1/evidence/delete',{
      method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({since,until}),
    });
    let payload={}; try { payload=await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail||'Could not delete local evidence.');
    return payload;
  }

  function localInputValue(date) {
    const pad=n=>String(n).padStart(2,'0');
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function readable(iso) {
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  function showConfirm(host, since, until) {
    host.innerHTML=`<div class="note" style="margin-top:10px"><strong>Delete local evidence?</strong><div style="margin-top:5px">Delete everything overlapping <strong>${esc(readable(since))}</strong> through <strong>${esc(readable(until))}</strong>? This cannot be undone on this computer.</div><div class="muted" style="margin-top:5px">Unsent and late local copies are blocked from future organization sharing. Evidence already synchronized to an organization Gateway is not automatically recalled.</div><div class="modal-actions"><button id="confirmEvidenceDelete">Delete evidence</button><button class="secondary" id="cancelEvidenceDelete">Cancel</button></div></div>`;
    host.querySelector('#cancelEvidenceDelete').onclick=()=>{host.innerHTML='';};
    host.querySelector('#confirmEvidenceDelete').onclick=async()=>{
      const button=host.querySelector('#confirmEvidenceDelete');
      button.disabled=true; button.textContent='Deleting…';
      try {
        const result=await apiDelete(since,until);
        host.innerHTML='';
        if (typeof window.toast==='function') window.toast(`${Number(result.deleted_events||0)} local evidence event${Number(result.deleted_events||0)===1?'':'s'} deleted.`);
        if (typeof window.load==='function') await window.load();
        if (typeof window.refreshCaptureStatus==='function') window.refreshCaptureStatus();
      } catch (error) {
        button.disabled=false; button.textContent='Delete evidence';
        if (typeof window.toast==='function') window.toast(error.message||'Could not delete local evidence.');
      }
    };
  }

  function preset(host, minutes) {
    const until=new Date();
    const since=new Date(until.getTime()-minutes*60*1000);
    showConfirm(host,since.toISOString(),until.toISOString());
  }

  function install() {
    const panel=document.querySelector('#panel-evidence .card');
    const table=document.querySelector('#evidenceTableView');
    if (!panel || !table || document.querySelector('#evidenceDeleteControls')) return;
    const controls=document.createElement('div');
    controls.id='evidenceDeleteControls';
    controls.style.margin='10px 0 4px';
    controls.innerHTML=`<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap"><button class="secondary" id="delete15m">Delete last 15 minutes</button><button class="secondary" id="delete1h">Delete last 1 hour</button><button class="secondary" id="deleteCustom">Custom range</button><span class="muted">Deletes local evidence; already-shared Gateway copies are not recalled.</span></div><div id="evidenceDeleteCustom" hidden style="margin-top:10px"><div class="filter-row" style="align-items:end"><div class="search-box" style="margin-left:0"><label for="deleteSince">From</label><input id="deleteSince" type="datetime-local"></div><div class="search-box" style="margin-left:0"><label for="deleteUntil">Until</label><input id="deleteUntil" type="datetime-local"></div><button id="reviewCustomDelete" class="secondary">Review deletion</button></div></div><div id="evidenceDeleteConfirm"></div>`;
    table.parentNode.insertBefore(controls,table);
    const confirmHost=controls.querySelector('#evidenceDeleteConfirm');
    controls.querySelector('#delete15m').onclick=()=>preset(confirmHost,15);
    controls.querySelector('#delete1h').onclick=()=>preset(confirmHost,60);
    controls.querySelector('#deleteCustom').onclick=()=>{
      const custom=controls.querySelector('#evidenceDeleteCustom');
      custom.hidden=!custom.hidden;
      if (!custom.hidden) {
        const until=new Date(), since=new Date(until.getTime()-15*60*1000);
        controls.querySelector('#deleteSince').value=localInputValue(since);
        controls.querySelector('#deleteUntil').value=localInputValue(until);
      }
    };
    controls.querySelector('#reviewCustomDelete').onclick=()=>{
      const sinceValue=controls.querySelector('#deleteSince').value;
      const untilValue=controls.querySelector('#deleteUntil').value;
      if (!sinceValue || !untilValue) {
        if (typeof window.toast==='function') window.toast('Choose both deletion times.');
        return;
      }
      const since=new Date(sinceValue), until=new Date(untilValue);
      if (!Number.isFinite(since.getTime()) || !Number.isFinite(until.getTime()) || until<=since) {
        if (typeof window.toast==='function') window.toast('The deletion end must be after the start.');
        return;
      }
      showConfirm(confirmHost,since.toISOString(),until.toISOString());
    };
  }

  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
