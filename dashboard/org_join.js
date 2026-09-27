(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let previewed = null;

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
      if (!badge) return;
      if (!m.managed) return;
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
    const p = $('orgJoinPreviewButton');
    if (p) p.addEventListener('click', preview);
    const j = $('orgJoinButton');
    if (j) j.addEventListener('click', join);
    setTimeout(refreshManaged, 300);
  });
})();
