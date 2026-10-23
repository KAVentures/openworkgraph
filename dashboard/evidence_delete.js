(() => {
  const esc = value => {
    if (typeof window.esc === 'function') return window.esc(String(value ?? ''));
    const node=document.createElement('div'); node.textContent=String(value ?? ''); return node.innerHTML;
  };

  async function apiDelete(since, until, includeAgentConversations) {
    await window.__owgAuthReady;
    const response=await fetch('/v1/evidence/delete',{
      method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({since,until,include_agent_conversations:includeAgentConversations===true}),
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

  // Shows the server's independent re-check of every local store, never a client-side guess.
  function deletionSummary(result) {
    const verification=result && typeof result.verification==='object' ? result.verification : null;
    if (!verification) return '';
    const separate=verification.remaining_in_separately_deleted_stores||{};
    const messages=Number(separate.agent_session_messages||0), details=Number(separate.agent_working_detail||0);
    const receipt=String((result.receipt&&result.receipt.sha256)||'').slice(0,16);
    const status=verification.verified_clean===true
      ? '<strong>Verified:</strong> no evidence from this time range remains in the local stores this deletion covers.'
      : '<strong>Not verified:</strong> some evidence from this time range is still present locally. Retry the deletion.';
    const removed=result.agent_conversations_included===true
      ? `<div style="margin-top:5px">${esc(Number(result.agent_conversation_messages_deleted||0))} AI conversation message${Number(result.agent_conversation_messages_deleted||0)===1?'':'s'} and ${esc(Number(result.agent_working_detail_deleted||0))} agent working detail record${Number(result.agent_working_detail_deleted||0)===1?'':'s'} deleted.</div>`
      : '';
    const kept=messages||details
      ? `<div style="margin-top:5px">${esc(messages)} agent conversation message${messages===1?'':'s'} and ${esc(details)} agent working detail record${details===1?'':'s'} from this time are kept; choose to include them next time, or delete them under agent sessions.</div>`
      : '';
    return `<div class="note" id="evidenceDeleteVerification" role="status" style="margin-top:10px">${status}${removed}${kept}${receipt?`<div class="muted" style="margin-top:5px">Receipt ${esc(receipt)}</div>`:''}</div>`;
  }

  function showConfirm(host, since, until) {
    host.innerHTML=`<div class="note" style="margin-top:10px"><strong>Delete local evidence?</strong><div style="margin-top:5px">Delete everything overlapping <strong>${esc(readable(since))}</strong> through <strong>${esc(readable(until))}</strong>? This cannot be undone on this computer.</div><div class="muted" style="margin-top:5px">Unsent and late local copies are blocked from future organization sharing. Evidence already synchronized to an organization Gateway is not automatically recalled.</div><label style="display:flex;gap:6px;align-items:center;margin-top:8px"><input type="checkbox" id="deleteAgentConversations" checked> Also delete AI conversation messages and agent working detail from this time</label><div class="modal-actions"><button id="confirmEvidenceDelete" style="background:var(--red);border-color:var(--red)">Delete evidence</button><button class="secondary" id="cancelEvidenceDelete">Cancel</button></div></div>`;
    host.querySelector('#cancelEvidenceDelete').onclick=()=>{host.innerHTML='';};
    host.querySelector('#confirmEvidenceDelete').onclick=async()=>{
      const button=host.querySelector('#confirmEvidenceDelete');
      button.disabled=true; button.textContent='Deleting…';
      try {
        const result=await apiDelete(since,until,host.querySelector('#deleteAgentConversations').checked);
        host.innerHTML=deletionSummary(result);
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
    controls.style.cssText='margin:16px 0 0;padding-top:12px;border-top:1px solid #eceee8';
    controls.innerHTML=`<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap"><span class="muted" style="font-weight:700">Delete recent evidence:</span><button class="danger-quiet small" id="delete15m">Delete last 15 minutes</button><button class="danger-quiet small" id="delete1h">Delete last 1 hour</button><button class="danger-quiet small" id="deleteCustom">Custom range</button></div><div id="evidenceDeleteCustom" hidden style="margin-top:10px"><div class="filter-row" style="align-items:end"><div class="search-box" style="margin-left:0"><label for="deleteSince">From</label><input id="deleteSince" type="datetime-local"></div><div class="search-box" style="margin-left:0"><label for="deleteUntil">Until</label><input id="deleteUntil" type="datetime-local"></div><button id="reviewCustomDelete" class="secondary">Review deletion</button></div></div><div id="evidenceDeleteConfirm"></div>`;
    // Below the evidence, not above it: deleting is available but not the first thing you see.
    panel.appendChild(controls);
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
