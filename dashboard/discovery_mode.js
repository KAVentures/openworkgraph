(() => {
  const esc = value => typeof window.esc === 'function'
    ? window.esc(String(value ?? ''))
    : String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  let state = null;
  let families = [];

  async function call(url, options={}) {
    await window.__owgAuthReady;
    const response = await fetch(url, {cache:'no-store', ...options});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(String(data.detail || 'Discovery Mode request failed'));
    return data;
  }

  function splitValues(value) {
    return String(value || '').split(/[\n,]/).map(x => x.trim()).filter(Boolean);
  }

  function formatDate(value) {
    try { return new Date(value).toLocaleString(); } catch (_) { return String(value || ''); }
  }

  function host() {
    const overview = document.querySelector('#panel-overview');
    if (!overview) return null;
    let card = document.querySelector('#discoveryModeCard');
    if (card) return card;
    card = document.createElement('div');
    card.id = 'discoveryModeCard';
    card.className = 'card';
    card.style.border = '1px solid #d9dfd4';
    const metrics = overview.querySelector('.metrics');
    if (metrics) overview.insertBefore(card, metrics); else overview.prepend(card);
    return card;
  }

  function render() {
    const card = host();
    if (!card || !state) return;
    if (!state.enabled) {
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap">
          <div style="max-width:760px">
            <div class="tag">Optional</div>
            <h2 style="margin:7px 0 5px">Workflow discovery</h2>
            <div class="muted">Run a temporary, purpose-scoped study for an AI implementation. Canonical workflow evidence is restricted to the allowlisted apps/sites before persistence; nothing is shared automatically.</div>
          </div>
          <button id="startDiscovery">Start discovery mode</button>
        </div>`;
      card.querySelector('#startDiscovery').onclick = openStart;
      return;
    }

    const expired = !!state.expired;
    const reviewing = state.status === 'review' || expired;
    const scope = [
      ...(state.allowed_apps || []).map(x => `App: ${x}`),
      ...(state.allowed_browser_hosts || []).map(x => `Site: ${x}`)
    ];
    const answered = (state.questions || []).filter(x => String(x.answer || '').trim()).length;
    const suggested = state.suggested_targeted_questions || [];
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap">
        <div style="max-width:780px">
          <div class="tag">${reviewing ? 'Review required' : 'Discovery active'}</div>
          <h2 style="margin:7px 0 5px">${esc(state.name || 'Workflow discovery')}</h2>
          <div class="muted">${esc(state.purpose || 'Purpose not specified')}</div>
          <div style="margin-top:10px"><strong>Scope:</strong> ${scope.map(x => `<span class="pill">${esc(x)}</span>`).join(' ') || '—'}</div>
          <div class="muted" style="margin-top:7px">Study window: ${esc(formatDate(state.starts_at))} → ${esc(formatDate(state.ends_at))}. Positive allowlist is enforced before persistence. Unresolved browser-container events are ${state.allow_unresolved_browser_container ? 'allowed when the browser app is allowlisted' : 'dropped'}.</div>
          <div class="muted" style="margin-top:5px">${Number(state.candidate_workflow_families || 0)} candidate workflow families · ${answered} employee answers saved · automatic sharing off · detailed canonical evidence retained for ${Number(state.retention_days_after_end || 14)} day(s) after the study unless deleted sooner.</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
          ${reviewing ? '<button id="reviewDiscovery">Review discovery</button>' : '<button id="finishDiscovery">Finish & review</button>'}
          <button class="secondary" id="exitDiscovery">Exit discovery mode</button>
        </div>
      </div>
      ${reviewing && suggested.length ? `<div class="note" style="margin-top:12px"><strong>${suggested.length} evidence-grounded question${suggested.length===1?'':'s'} suggested</strong><div class="muted" style="margin-top:3px">These come from observed structural variations. OWG does not treat them or your answers as captured facts.</div></div>` : ''}`;

    card.querySelector('#finishDiscovery')?.addEventListener('click', async () => {
      if (!confirm('End the observation window now and move to employee review?')) return;
      await call('/v1/discovery/finish', {method:'POST'});
      await refresh();
      openReview();
    });
    card.querySelector('#reviewDiscovery')?.addEventListener('click', openReview);
    card.querySelector('#exitDiscovery')?.addEventListener('click', async () => {
      if (!confirm('Exit Discovery Mode and restore ordinary OpenWorkGraph capture? The discovery package will no longer be available from this session view.')) return;
      await call('/v1/discovery/deactivate', {method:'POST'});
      await refresh();
    });
  }

  function openStart() {
    if (typeof window.openModal !== 'function') return;
    window.openModal('Start workflow discovery', 'Purpose-limited observation', `
      <p>Use this when a worker is temporarily mapping a workflow for an AI/automation implementation. The allowlist is applied before evidence is stored.</p>
      <label><strong>Study name</strong><input id="discName" type="text" value="Workflow discovery" style="width:100%;margin-top:5px"></label>
      <label style="display:block;margin-top:10px"><strong>Purpose</strong><textarea id="discPurpose" rows="3" style="width:100%;margin-top:5px" placeholder="e.g. Map customer pricing requests for an agent implementation"></textarea></label>
      <label style="display:block;margin-top:10px"><strong>Native apps to include</strong><textarea id="discApps" rows="3" style="width:100%;margin-top:5px" placeholder="Salesforce Desktop&#10;Microsoft Excel"></textarea><div class="muted">One per line. Do not add Chrome/Safari here when you want site-level scoping; use browser hosts below.</div></label>
      <label style="display:block;margin-top:10px"><strong>Browser hosts to include</strong><textarea id="discHosts" rows="3" style="width:100%;margin-top:5px" placeholder="mail.google.com&#10;docs.google.com&#10;*.salesforce.com"></textarea></label>
      <label style="display:block;margin-top:10px"><strong>Duration (days)</strong><input id="discDays" type="number" min="0.1" max="31" step="0.1" value="5" style="width:120px;margin-left:8px"></label>
      <label style="display:block;margin-top:10px"><strong>Delete detailed Discovery evidence after</strong><input id="discRetention" type="number" min="1" max="90" step="1" value="14" style="width:90px;margin:0 7px">days after the study</label>
      <label style="display:flex;gap:8px;align-items:flex-start;margin-top:12px"><input id="discUnresolvedBrowser" type="checkbox"><span><strong>Keep unresolved browser-container events</strong><span class="muted" style="display:block">Less private. Leave off to drop Chrome/Safari desktop events whose site cannot be proven to be in scope.</span></span></label>
      <div class="note" style="margin-top:12px">Nothing in the Discovery study is uploaded through normal Gateway sync before review. Site-level browser scoping works best with the browser sensor; unresolved Chrome/Safari context is dropped by default. Separate opt-in visible agent-session message storage keeps its existing independent local retention policy.</div>
      <div class="modal-actions"><button id="discStartNow">Start scoped discovery</button></div>`);
    document.querySelector('#discStartNow').onclick = async () => {
      const body = {
        name: document.querySelector('#discName').value,
        purpose: document.querySelector('#discPurpose').value,
        allowed_apps: splitValues(document.querySelector('#discApps').value),
        allowed_browser_hosts: splitValues(document.querySelector('#discHosts').value),
        duration_days: Number(document.querySelector('#discDays').value || 5),
        retention_days_after_end: Number(document.querySelector('#discRetention').value || 14),
        allow_unresolved_browser_container: !!document.querySelector('#discUnresolvedBrowser').checked
      };
      try {
        await call('/v1/discovery/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
        window.closeModal?.();
        await refresh();
      } catch (error) {
        window.toast?.(error.message);
      }
    };
  }

  async function loadFamilies() {
    return Array.isArray(state?.review_candidates) ? state.review_candidates : [];
  }

  function workflowTitle(item) {
    const steps = item.high_support_structural_steps || [];
    return steps.length ? steps.slice(0,4).map(x => String(x).replace(/^surface:|^action:/,'').replace(/_/g,' ')).join(' → ') : 'Observed repeated work';
  }

  async function saveSelection() {
    const excluded = [...document.querySelectorAll('[data-disc-run]')].filter(x => !x.checked).map(x => x.value);
    await call('/v1/discovery/selection', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({excluded_execution_ids: excluded})
    });
    state.excluded_execution_ids = excluded;
  }

  async function saveSuggestedQuestion(index) {
    const item = (state.suggested_targeted_questions || [])[index];
    const area = document.querySelector(`[data-disc-answer="${index}"]`);
    if (!item || !area) return;
    const answer = area.value.trim();
    if (!answer) { window.toast?.('Add an answer first.'); return; }
    await call('/v1/discovery/questions', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        question:item.question,
        answer,
        source:'observed_structural_variation',
        related_execution_ids:item.related_execution_ids || []
      })
    });
    window.toast?.('Employee answer saved separately from observed evidence.');
    await refresh();
    openReview();
  }

  async function previewPackage() {
    const data = await call('/v1/discovery/package?representation=redacted');
    const blob = new Blob([JSON.stringify(data, null, 2) + '\n'], {type:'application/json'});
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href; a.download = 'openworkgraph-discovery-preview-redacted.json'; a.style.display='none';
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(href),5000);
  }

  async function approveAndExport() {
    if (!confirm('I reviewed the selected examples/questions and approve creating this redacted Discovery Package. This still does not upload it anywhere.')) return;
    await saveSelection();
    await call('/v1/discovery/approve-share', {method:'POST'});
    const response = await fetch('/v1/discovery/export?representation=redacted', {cache:'no-store'});
    if (!response.ok) {
      let detail='Could not export Discovery Package.';
      try { detail=String((await response.clone().json())?.detail || detail); } catch (_) {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=href; a.download='openworkgraph-discovery-package.zip'; a.style.display='none';
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(href),5000);
    await refresh();
  }

  async function openReview() {
    if (typeof window.openModal !== 'function') return;
    families = await loadFamilies();
    const excluded = new Set((state.excluded_execution_ids || []).map(String));
    const runRows = families.map((family, familyIndex) => {
      const runs = family.executions || [];
      return `<div style="border-top:${familyIndex?'1px solid #eceee8':'0'};padding:10px 0">
        <strong>${esc(workflowTitle(family))}</strong><div class="muted">${Number(family.execution_count||0)} observed runs. Grouping is derived, not ground truth.</div>
        <div style="margin-top:5px">${runs.map(run => `<label style="display:flex;gap:8px;align-items:flex-start;margin-top:5px"><input type="checkbox" data-disc-run value="${esc(run.execution_id||'')}" ${excluded.has(String(run.execution_id||''))?'':'checked'}><span>${esc(formatDate(run.started_at))} · ${esc(run.outcome_status||'unknown')}</span></label>`).join('')}</div>
      </div>`;
    }).join('') || '<div class="muted">No repeated workflow family with at least two runs was observed in this study window.</div>';

    const questions = (state.suggested_targeted_questions || []).map((item,index) => `
      <div style="border-top:${index?'1px solid #eceee8':'0'};padding:10px 0">
        <strong>${esc(item.question)}</strong>
        <textarea data-disc-answer="${index}" rows="2" style="width:100%;margin-top:7px" placeholder="Employee answer — stored as attributed business context, not observed evidence"></textarea>
        <button class="secondary" data-save-disc-answer="${index}" style="margin-top:6px">Save answer</button>
      </div>`).join('') || '<div class="muted">No structural divergence generated a targeted question yet. Rare exceptions may still be unobserved.</div>';

    const saved = (state.questions || []).filter(x => String(x.answer || '').trim()).map(x => `
      <div style="padding:7px 0;border-top:1px solid #eceee8"><strong>${esc(x.question)}</strong><div style="margin-top:3px">${esc(x.answer)}</div><div class="muted">Employee-stated context · ${esc(formatDate(x.answered_at))}</div></div>`).join('');

    window.openModal('Review workflow discovery', 'Employee review before handoff', `
      <h3>1. Select observed examples</h3>
      <p class="muted">Uncheck runs that do not belong. This affects only this Discovery Package; it does not delete canonical OWG history.</p>
      <div style="max-height:250px;overflow:auto">${runRows}</div>
      <div class="modal-actions"><button class="secondary" id="discSaveSelection">Save example selection</button></div>
      <h3 style="margin-top:18px">2. Explain observed variations</h3>
      <div>${questions}</div>
      ${saved ? `<h3 style="margin-top:18px">Saved employee answers</h3><div>${saved}</div>` : ''}
      <h3 style="margin-top:18px">3. Review and export</h3>
      <div class="note">The package states its observation window and limitations, keeps employee statements separate from captured evidence, and labels structural cases as non-replayable without source-system test data.</div>
      <div class="modal-actions">
        <button class="secondary" id="discPreviewPackage">Download redacted preview JSON</button>
        <button id="discApproveExport">Approve & download package</button>
        <button class="ghost" id="discDeleteEvidence">Delete Discovery evidence now</button>
      </div>`);

    document.querySelector('#discSaveSelection').onclick = async () => { await saveSelection(); window.toast?.('Discovery example selection saved.'); };
    document.querySelectorAll('[data-save-disc-answer]').forEach(button => {
      button.onclick = () => saveSuggestedQuestion(Number(button.dataset.saveDiscAnswer || 0));
    });
    document.querySelector('#discPreviewPackage').onclick = async () => { await saveSelection(); await previewPackage(); };
    document.querySelector('#discApproveExport').onclick = async () => {
      try { await approveAndExport(); } catch (error) { window.toast?.(error.message); }
    };
    document.querySelector('#discDeleteEvidence').onclick = async () => {
      if (!confirm('Permanently delete canonical evidence observed inside this Discovery window? This cannot be undone.')) return;
      try {
        const result = await call('/v1/discovery/purge', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({confirm:'DELETE DISCOVERY'})
        });
        window.toast?.(`Deleted ${Number(result.deleted_event_rows || 0)} Discovery evidence rows.`);
        window.closeModal?.();
        await refresh();
      } catch (error) { window.toast?.(error.message); }
    };
  }

  async function refresh() {
    try {
      state = await call('/v1/discovery');
      render();
    } catch (_) {}
  }

  function install() {
    host();
    refresh();
    setInterval(() => { if (!document.hidden && state?.enabled && state?.status === 'active') refresh(); }, 15000);
  }

  window.refreshDiscoveryMode = refresh;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
