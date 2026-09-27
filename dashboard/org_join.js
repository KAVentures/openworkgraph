(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let previewed = null;

  function ensureUi() {
    if (!$('owgOrgJoinStyles')) {
      const style = document.createElement('style');
      style.id = 'owgOrgJoinStyles';
      style.textContent = '.scope-label{margin-left:10px;font-size:12px;padding:2px 8px;border-radius:99px;background:#eef3ee;color:#2d4a37;white-space:nowrap}.managed-badge{margin-left:8px;font-size:12px;padding:2px 8px;border-radius:99px;background:#e8ecff;color:#1d2566;font-weight:600;white-space:nowrap}.managed-note{border-left:4px solid #2e3a8c}.org-join-list{margin:8px 0;padding-left:0;list-style:none}.org-join-list li{margin:3px 0}.org-join-field{width:100%;min-height:40px;border:1px solid #cfd3cb;border-radius:9px;padding:8px 10px;background:#fff}';
      document.head.appendChild(style);
    }
    const brand = document.querySelector('.brand');
    if (brand && !$('personalDashboardScope')) {
      const scope = document.createElement('span');
      scope.id = 'personalDashboardScope';
      scope.className = 'scope-label';
      scope.title = 'This dashboard describes data stored on this computer.';
      scope.textContent = 'Personal dashboard · this computer';
      brand.appendChild(scope);
      const managed = document.createElement('span');
      managed.id = 'managedBadge';
      managed.className = 'managed-badge';
      managed.hidden = true;
      brand.appendChild(managed);
    }
    const panel = $('panel-organization');
    if (panel && !$('orgJoinCard')) {
      const managedNote = document.createElement('div');
      managedNote.id = 'managedNote';
      managedNote.className = 'card managed-note';
      managedNote.hidden = true;
      panel.insertBefore(managedNote, panel.firstChild);

      const card = document.createElement('div');
      card.id = 'orgJoinCard';
      card.className = 'card';
      card.innerHTML = '<h2>Join your organization</h2>' +
        '<div class="muted">If your IT admin sent you a join code, paste it here. You will see exactly what will be shared before anything is sent.</div>' +
        '<label for="orgJoinCode" style="display:block;margin-top:10px;font-weight:600">Join code</label>' +
        '<input id="orgJoinCode" class="org-join-field" autocomplete="off" spellcheck="false" placeholder="owgjoin1.…">' +
        '<div style="margin-top:10px"><button id="orgJoinPreviewButton" class="secondary">Review what will be shared</button></div>' +
        '<div id="orgJoinPreview" hidden style="margin-top:12px"><div><b>Organization:</b> <span id="orgJoinOrg"></span></div><div id="orgJoinShares"></div>' +
        '<label for="orgJoinActor" style="display:block;margin-top:8px;font-weight:600">Your work email or username</label>' +
        '<input id="orgJoinActor" class="org-join-field" autocomplete="email">' +
        '<label style="display:flex;gap:8px;margin-top:10px"><input type="checkbox" id="orgJoinConsent"> I reviewed what will be shared with my organization.</label>' +
        '<div style="margin-top:10px"><button id="orgJoinButton">Join</button></div></div>' +
        '<div id="orgJoinResult" role="status" aria-live="polite" style="margin-top:8px"></div>';
      const gatewayPanel = $('gatewayPanel');
      panel.insertBefore(card, gatewayPanel || managedNote.nextSibling);
    }
  }

  async function call(url, body) {
    const r = await fetch(url, {
      method: body ? 'POST' : 'GET',
      cache: 'no-store',
      headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || ('Request failed (' + r.status + ')'));
    return data;
  }

  function sharingList(s) {
    if (!s) return '';
    const yes = (on, text) => `<li>${on ? '✓' : '✗'} ${esc(text)}</li>`;
    const limited = Array.isArray(s.limited_to_event_types) && s.limited_to_event_types.length
      ? `Only these event types: ${s.limited_to_event_types.join(', ')}`
      : 'All eligible structural event types';
    return '<ul class="org-join-list">' +
      yes(true, 'Which apps and sites you use, and for how long') +
      yes(s.shares_metadata, 'Structural details (actions such as Send, durations, counts)') +
      yes(s.shares_window_titles, 'Window and page titles (can contain names and subjects)') +
      yes(s.shares_agent_activity, 'AI-agent activity, only if you also allow it locally') +
      yes(true, limited) +
      yes(s.forces_redacted_ai_context, 'Organization requires Redacted AI context on this computer') +
      '</ul>';
  }

  async function preview() {
    const code = $('orgJoinCode').value.trim();
    $('orgJoinResult').textContent = '';
    if (!code) {
      $('orgJoinResult').textContent = 'Paste the join code your IT admin sent you.';
      return;
    }
    try {
      previewed = await call('/v1/org-join/preview', {join_code: code});
      $('orgJoinPreview').hidden = false;
      $('orgJoinOrg').textContent = previewed.organization_name;
      $('orgJoinShares').innerHTML = sharingList(previewed.sharing) +
        '<p class="muted"><b>Never shared:</b> ' + esc((previewed.never_shared || []).join(', ')) + '.</p>' +
        '<p class="muted"><b>You can:</b> ' + esc((previewed.you_can || []).join(', ')) + '.</p>';
    } catch (e) {
      previewed = null;
      $('orgJoinPreview').hidden = true;
      $('orgJoinResult').textContent = e.message;
    }
  }

  async function join() {
    if (!previewed) return;
    const actor = $('orgJoinActor').value.trim();
    if (!actor) {
      $('orgJoinResult').textContent = 'Enter your work email or username.';
      return;
    }
    if (!$('orgJoinConsent').checked) {
      $('orgJoinResult').textContent = 'Confirm that you reviewed what will be shared.';
      return;
    }
    $('orgJoinButton').disabled = true;
    try {
      const r = await call('/v1/org-join', {
        join_code: $('orgJoinCode').value.trim(),
        actor_id: actor,
        accept_sharing: true,
      });
      previewed = null;
      $('orgJoinCard').innerHTML = `<h2>Joined ${esc(r.organization_name)}</h2><div class="muted">Sharing starts from enrollment forward. Anything recorded before joining stays on this computer. You can pause or disconnect in the Organization section.</div>`;
      if (typeof window.refreshGatewayPanel === 'function') window.refreshGatewayPanel();
    } catch (e) {
      $('orgJoinResult').textContent = e.message;
    } finally {
      $('orgJoinButton').disabled = false;
    }
  }

  async function refreshManaged() {
    try {
      const m = await call('/v1/managed-status');
      const badge = $('managedBadge');
      if (!badge || !m.managed) return;
      const failed = m.status && m.status !== 'joined';
      badge.hidden = false;
      badge.textContent = failed
        ? `Managed by ${m.organization_name || 'your organization'} · setup needs attention`
        : `Managed by ${m.organization_name || 'your organization'}`;
      badge.title = failed
        ? (m.error || 'See the Organization tab')
        : 'Your IT admin deployed organization sharing on this computer. See the Organization tab for what is shared.';
      const note = $('managedNote');
      if (note) {
        note.hidden = false;
        note.innerHTML = `<b>This computer is managed by ${esc(m.organization_name || 'your organization')}.</b> ` +
          (failed ? `Automatic setup did not finish: ${esc(m.error || m.status)}.` : 'Organization sharing was deployed by your IT admin.') +
          (m.sharing_preview ? sharingList(m.sharing_preview) : '') +
          '<span class="muted">Typed text, clipboard contents, screenshots and passwords are never shared. You can pause sharing at any time.</span>';
      }
      const card = $('orgJoinCard');
      if (card) card.hidden = true;
    } catch (_) {
      // Local-only installs have no managed config; there is nothing to show.
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    ensureUi();
    const p = $('orgJoinPreviewButton');
    if (p) p.addEventListener('click', preview);
    const j = $('orgJoinButton');
    if (j) j.addEventListener('click', join);
    setTimeout(refreshManaged, 300);
  });
})();
