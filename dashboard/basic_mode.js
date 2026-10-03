(() => {
  'use strict';
  // Basic / Advanced dashboard (v0.118).
  //
  // Basic shows four tabs (Today, Activity, AI apps, Privacy) and hides the
  // power-user surfaces other dashboard layers build. Nothing is removed:
  // Advanced (one switch in the top bar, remembered on this computer) shows
  // everything. This layer runs last and only adds classes and the Privacy tab;
  // it never replaces another layer's markup or behavior.

  const MODE_KEY = 'owg_view_mode_v1';
  const CHECKLIST_KEY = 'owg_setup_checklist_hidden_v1';
  const HIDE = 'owg-basic-hide';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const $ = selector => document.querySelector(selector);
  const toast = message => { if (typeof window.toast === 'function') window.toast(message); };
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) {} },
  };

  async function api(path, options) {
    await window.__owgAuthReady;
    const response = await fetch(path, {cache: 'no-store', ...(options || {})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }
  const send = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

  // ------------------------------------------------------------------ mode

  function mode() { return store.get(MODE_KEY) === 'advanced' ? 'advanced' : 'basic'; }

  function setMode(next) {
    store.set(MODE_KEY, next === 'advanced' ? 'advanced' : 'basic');
    applyMode(true);
    toast(next === 'advanced' ? 'Advanced view: every tab and setting is shown.' : 'Basic view: just the essentials.');
  }

  function ensureModeSwitch() {
    if ($('#owgModeSwitch')) return;
    const bar = $('.topbar');
    if (!bar) return;
    const wrap = document.createElement('div');
    wrap.id = 'owgModeSwitch';
    wrap.className = 'mode-switch';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Dashboard view');
    wrap.innerHTML = '<button type="button" data-mode="basic">Basic</button><button type="button" data-mode="advanced">Advanced</button>';
    wrap.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => setMode(button.dataset.mode));
    const capture = $('#captureControlMount');
    if (capture && capture.parentNode === bar) bar.insertBefore(wrap, capture); else bar.appendChild(wrap);
  }

  // Facts that decide whether a normally advanced tab is useful in Basic.
  const facts = {agentsSeen: false, orgConnected: false};

  function mark(element, hideInBasic) {
    if (element) element.classList.toggle(HIDE, !!hideInBasic);
  }

  function metricCard(id) { return $(`#${id}`)?.closest('.metric-card'); }

  // redirect: leave a tab Basic hides (on a mode switch or at load). Not done on
  // every refresh, so "Export…" from Activity can open the Export panel in Basic.
  function applyMode(redirect = false) {
    const basic = mode() === 'basic';
    document.body.classList.toggle('mode-basic', basic);
    document.body.classList.toggle('mode-advanced', !basic);
    document.querySelectorAll('#owgModeSwitch [data-mode]').forEach(button => {
      const on = button.dataset.mode === mode();
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });

    // Tab names that say what is inside, in both views.
    const names = {overview: 'Today', evidence: 'Activity', connect: 'AI apps', privacy: 'Privacy'};
    for (const [tab, name] of Object.entries(names)) {
      const button = $(`#tab-${tab}`);
      if (button && button.textContent !== name) button.textContent = name;
    }

    // Tabs: History, Export and Settings are advanced; Agents and Organization
    // appear in Basic as soon as they are in use.
    for (const tab of ['history', 'export', 'settings']) mark($(`#tab-${tab}`), true);
    mark($('#tab-agents'), !facts.agentsSeen);
    mark($('#tab-organization'), !facts.orgConnected);
    mark($('#gatewayStatusChip'), !facts.orgConnected);

    // Today: the status hero (with "See what happened" and the setup
    // checklist) supersedes the first-run reconstruction and retention cards in
    // both views; the reconstruction itself stays one click away in the hero.
    $('#firstValueCard')?.classList.add('owg-superseded');
    $('#historyOnboarding')?.classList.add('owg-superseded');
    const hero = $('#todayHero'), overview = $('#panel-overview');
    if (hero && overview && overview.firstElementChild !== hero) overview.insertBefore(hero, overview.firstChild);
    mark($('#workProfileCard'), true);
    mark(metricCard('idle'), true);
    mark(metricCard('keys'), true);
    mark($('#hiddenLoopLine'), true);
    const discovery = $('#discoveryModeCard');
    const discoveryActive = /discovery active|review required/i.test(discovery?.querySelector('.tag')?.textContent || '');
    mark(discovery, !discoveryActive);
    const drafting = $('#workflowEvidenceDrafting');
    mark(drafting, !drafting?.querySelector('#workflowEvidenceCandidates input, #workflowEvidenceCandidates button'));

    // Activity: the event list and delete; loops/transitions analysis is advanced.
    mark($('#panel-evidence [role="group"][aria-label="Evidence view"]'), true);
    if (basic && $('#evidenceTableView')?.hidden && typeof window.setEvidenceView === 'function') window.setEvidenceView('all');

    // AI apps: the redaction choice lives in Privacy; scripting help is advanced.
    mark($('#owgConnections .cli'), true);
    mark($('#owgAiDetail'), true);

    // Agents (when shown in Basic): runs and reports; setup and learning are advanced.
    mark($('#agentSessionContinuity'), true);
    mark($('#agentLearning'), true);

    // Privacy: the restart preference is an advanced detail.
    mark($('#pvRestartRow'), true);

    const active = $('[role="tab"][aria-selected="true"]');
    if (redirect && basic && active && active.classList.contains(HIDE) && typeof window.activateTab === 'function') window.activateTab('overview');
    if (typeof window.updateTabScrollHint === 'function') window.updateTabScrollHint();
  }

  // ------------------------------------------------------------------ Privacy tab

  function ensurePrivacyTab() {
    if ($('#tab-privacy')) return;
    const tabs = $('.tabs'), main = $('main');
    if (!tabs || !main) return;
    const tab = document.createElement('button');
    tab.setAttribute('role', 'tab');
    tab.id = 'tab-privacy';
    tab.dataset.tab = 'privacy';
    tab.setAttribute('aria-controls', 'panel-privacy');
    tab.setAttribute('aria-selected', 'false');
    tab.textContent = 'Privacy';
    tabs.insertBefore(tab, $('#tab-settings') || null);
    tab.addEventListener('click', () => { window.activateTab?.('privacy'); refreshPrivacy(); });

    const panel = document.createElement('section');
    panel.className = 'tabpanel';
    panel.setAttribute('role', 'tabpanel');
    panel.id = 'panel-privacy';
    panel.dataset.panel = 'privacy';
    panel.setAttribute('aria-labelledby', 'tab-privacy');
    panel.hidden = true;
    panel.innerHTML = `
      <div class="card pv-intro">
        <div class="pv-shield" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M12 2.5 4.5 5.5v5.7c0 4.6 3.1 8.7 7.5 10.3 4.4-1.6 7.5-5.7 7.5-10.3V5.5L12 2.5Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="m8.6 12.1 2.3 2.3 4.6-4.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
        <div><h2>Your privacy</h2><div class="muted">Your evidence is stored locally by default and is never sent to OpenWorkGraph. If you connect an organization, only evidence allowed by your sharing settings can be sent to that organization's Gateway. Names, email addresses, phone numbers and personal numbers are replaced with tokens before anything is saved. Typed text, passwords and clipboard contents are never captured.</div></div>
      </div>

      <div class="card">
        <div class="pv-row" id="pvRecordingRow">
          <div><h3>Recording</h3><div class="muted" id="pvRecordingText">Checking…</div></div>
          <div class="pv-control" id="pvRecordingControl"></div>
        </div>
        <div class="pv-row" id="pvRetentionRow">
          <div><h3>Keep my history</h3><div class="muted">Applies to everything OpenWorkGraph keeps, including the small run summaries it uses to spot repeated work.</div></div>
          <div class="pv-control"><select id="pvRetention" aria-label="Keep my history">
            <option value="ephemeral">This session only</option><option value="days:7">7 days</option><option value="days:30">30 days</option>
            <option value="days:90">90 days</option><option value="days:365">1 year</option><option value="forever">Until I delete it</option>
            <option value="custom" disabled hidden>Custom (set in History)</option></select></div>
        </div>
        <div class="pv-row" id="pvAiRow">
          <div><h3>AI apps can read my work</h3><div class="muted" id="pvAiText">Off: connected AI apps can't read anything.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvAiSwitch" aria-checked="false" aria-label="AI apps can read my work"></button></div>
        </div>
        <div class="pv-row pv-sub" id="pvHistoryRow">
          <div><h3>Include older history</h3><div class="muted" id="pvHistoryText">Off: AI apps only see the current run.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvHistorySwitch" aria-checked="false" aria-label="Let AI apps read older history"></button></div>
        </div>
        <div class="pv-row pv-sub" id="pvRestartRow">
          <div><h3>Turn AI access off every time OpenWorkGraph starts</h3><div class="muted">Off: your choice above is remembered after a restart.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvRestartSwitch" aria-checked="false" aria-label="Turn AI access off every time OpenWorkGraph starts"></button></div>
        </div>
        <div class="pv-row" id="pvRedactRow">
          <div><h3>Hide names and contact details from AI apps</h3><div class="muted" id="pvRedactText">Recommended. Titles keep their meaning: "Re: Contract for PERSON_1A2B3C - Gmail".</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvRedactSwitch" aria-checked="true" aria-label="Hide names and contact details from AI apps"></button></div>
        </div>
        <div class="pv-row" id="pvBrowserRow">
          <div><h3>Browser detail</h3><div class="muted" id="pvBrowserText">How much the browser sensor keeps about the pages you use.</div></div>
          <div class="pv-control"><div class="seg" role="radiogroup" aria-label="Browser detail">
            <button type="button" role="radio" data-profile="privacy_first">Standard</button><button type="button" role="radio" data-profile="context">More context</button></div></div>
        </div>
      </div>

      <div class="card" id="pvNeverRecord">
        <h2>Never record</h2>
        <div class="muted">Activity in these apps and sites, or in any window whose title contains one of these words, is not recorded. Changes apply right away to new activity; to remove what was already recorded, use Delete below.</div>
        <div class="pv-lists">
          <div class="pv-list" data-list="apps"><h3>Apps</h3><div class="chips"></div><form class="chip-add"><input type="text" placeholder="e.g. Signal" aria-label="Add an app"><button type="submit" class="secondary">Add</button></form></div>
          <div class="pv-list" data-list="hosts"><h3>Websites</h3><div class="chips"></div><form class="chip-add"><input type="text" placeholder="e.g. mybank.com" aria-label="Add a website"><button type="submit" class="secondary">Add</button></form></div>
          <div class="pv-list" data-list="title_words"><h3>Words in a window title</h3><div class="chips"></div><form class="chip-add"><input type="text" placeholder="e.g. medical" aria-label="Add a word"><button type="submit" class="secondary">Add</button></form></div>
        </div>
        <div class="muted" style="margin-top:10px"><button type="button" class="linkish" id="pvRestoreDefaults">Restore the default list</button> <span id="pvNeverStatus"></span></div>
      </div>

      <div class="card" id="pvDelete">
        <h2>Delete recorded activity</h2>
        <div class="muted">Removes activity from this computer. It cannot be undone. Anything already shared with an organization is not recalled.</div>
        <div class="pv-delete">
          <button type="button" class="danger-quiet" data-delete="15">Last 15 minutes</button>
          <button type="button" class="danger-quiet" data-delete="60">Last hour</button>
          <button type="button" class="danger-quiet" data-delete="today">Today</button>
          <button type="button" class="danger-quiet" data-delete="all">Everything</button>
        </div>
      </div>
      <div class="muted pv-more">More controls (separate retention for agents, saved sessions, browser profiles, organization sharing) are in the <button type="button" class="linkish" id="pvShowAdvanced">Advanced</button> view.</div>`;
    main.insertBefore(panel, $('#panel-settings') || null);
    bindPrivacy(panel);
  }

  const privacy = {capture: null, policy: null, access: null, history: null, detail: null, browser: null, lists: null};

  function retentionValue(value) {
    if (!value) return 'ephemeral';
    if (value.mode === 'days') return `days:${Number(value.days) || 90}`;
    return value.mode === 'forever' ? 'forever' : 'ephemeral';
  }

  function renderPrivacy() {
    const p = privacy;
    // Recording
    const state = String(p.capture?.state || '');
    const text = $('#pvRecordingText'), control = $('#pvRecordingControl');
    if (text && control) {
      if (p.capture?.demo) { text.textContent = 'Demo data: nothing is being recorded.'; control.innerHTML = ''; }
      else if (state === 'paused') { text.textContent = 'Paused. Nothing is recorded until you resume.'; control.innerHTML = '<button type="button" class="green" data-capture="resume">Resume</button>'; }
      else if (state === 'stopped') { text.textContent = 'Stopped. Your data is kept on this computer.'; control.innerHTML = '<button type="button" class="green" data-capture="start">Start recording</button>'; }
      else if (state) { text.textContent = 'On. Pause any time; nothing from a paused stretch is ever recorded.'; control.innerHTML = '<button type="button" class="secondary" data-capture="pause">Pause</button>'; }
      control.querySelectorAll('[data-capture]').forEach(button => button.onclick = () => captureAction(button.dataset.capture));
    }
    // Retention
    const select = $('#pvRetention');
    if (select && p.policy) {
      const human = retentionValue(p.policy.human_retention), agent = retentionValue(p.policy.agent_retention);
      const custom = select.querySelector('option[value="custom"]');
      if (human === agent) { custom.hidden = true; select.value = human; } else { custom.hidden = false; select.value = 'custom'; }
    }
    // AI access
    const on = !!p.access?.enabled;
    setSwitch('#pvAiSwitch', on);
    const aiText = $('#pvAiText');
    if (aiText) aiText.textContent = on ? 'On: AI apps you connected can read your recent work, with names shown as tokens. Every read is listed under AI apps.' : 'Off: connected AI apps can\'t read anything.';
    setSwitch('#pvRestartSwitch', !!p.access?.resets_on_restart);
    const restartText = $('#pvRestartRow .muted');
    if (restartText) restartText.textContent = p.access?.resets_on_restart ? 'On: AI access is off after every restart until you turn it on again.' : 'Off: your choice above is remembered after a restart.';
    const historyMode = String(p.history?.mode || 'off');
    setSwitch('#pvHistorySwitch', historyMode !== 'off');
    const historySwitch = $('#pvHistorySwitch');
    if (historySwitch) historySwitch.disabled = !on && historyMode === 'off';
    const historyText = $('#pvHistoryText');
    if (historyText) {
      const until = p.history?.expires_at ? new Date(p.history.expires_at).toLocaleString([], {weekday: 'short', hour: '2-digit', minute: '2-digit'}) : '';
      historyText.textContent = historyMode === 'off'
        ? (on ? 'Off: AI apps only see the current run.' : 'Turn on AI access first.')
        : `On${historyMode === 'selected_range' ? ' for selected dates' : ''}${until ? ` until ${until}` : ''}. It always switches itself off again.`;
    }
    // Redaction
    if (p.detail) {
      const redacted = p.detail.detail_level !== 'full';
      setSwitch('#pvRedactSwitch', redacted);
      const sw = $('#pvRedactSwitch');
      if (sw) sw.disabled = !!p.detail.locked_by_organization;
      const t = $('#pvRedactText');
      if (t) t.textContent = p.detail.locked_by_organization ? 'On, set by your organization.'
        : redacted ? 'Recommended. Titles keep their meaning: "Re: Contract for PERSON_1A2B3C - Gmail".'
        : 'Off: AI apps get titles exactly as stored. Names are already tokenized before storage, but anything the detector missed is shown.';
    }
    // Browser detail
    const profile = String(p.browser?.profile || '');
    document.querySelectorAll('#pvBrowserRow [data-profile]').forEach(button => button.setAttribute('aria-checked', button.dataset.profile === profile ? 'true' : 'false'));
    const browserText = $('#pvBrowserText');
    if (browserText) browserText.textContent = profile === 'context' ? 'More context: repeated work on the same document, ticket or record is linked, without storing its ID.'
      : profile === 'privacy_first' ? 'Standard: the site and page title, with document and record IDs masked.'
      : profile ? 'Custom: set in Advanced → Settings.' : 'How much the browser sensor keeps about the pages you use.';
    // Never record
    renderLists();
  }

  function setSwitch(selector, on) {
    const el = $(selector);
    if (el) el.setAttribute('aria-checked', on ? 'true' : 'false');
  }

  function renderLists() {
    const lists = privacy.lists;
    if (!lists) return;
    document.querySelectorAll('#pvNeverRecord .pv-list').forEach(box => {
      const key = box.dataset.list;
      const values = lists[key] || [];
      box.querySelector('.chips').innerHTML = values.length
        ? values.map((value, i) => `<span class="chip">${esc(value)}<button type="button" aria-label="Remove ${esc(value)}" data-remove="${i}">×</button></span>`).join('')
        : '<span class="muted">None</span>';
      box.querySelectorAll('[data-remove]').forEach(button => button.onclick = () => {
        const next = values.filter((_, i) => i !== Number(button.dataset.remove));
        saveLists({[key]: next});
      });
    });
  }

  async function saveLists(change) {
    const status = $('#pvNeverStatus');
    try {
      privacy.lists = await api('/v1/capture-exclusions', send('PUT', change));
      renderLists();
      if (status) status.textContent = 'Saved. Applies to new activity right away.';
    } catch (error) {
      if (status) status.textContent = error.message || 'Could not save.';
    }
  }

  async function captureAction(action) {
    try {
      await api(`/v1/capture/${action}`, {method: 'POST'});
      toast(action === 'pause' ? 'Recording paused. Nothing from the paused stretch will be stored.' : action === 'resume' ? 'Recording resumed.' : 'Recording started.');
      window.refreshCaptureStatus?.();
    } catch (error) { toast(error.message || 'Could not change recording.'); }
    refreshPrivacy();
  }

  function bindPrivacy(panel) {
    panel.querySelector('#pvRetention').onchange = async event => {
      const value = event.target.value;
      if (value === 'custom') return;
      const [kind, days] = value.startsWith('days:') ? ['days', Number(value.slice(5))] : [value, null];
      if (kind === 'ephemeral' && !confirm('Keep nothing after each session? Saved history and the small run summaries kept so far are deleted when this session ends.')) { renderPrivacy(); return; }
      try {
        await api('/v1/history-policy', send('PUT', {human_mode: kind, human_days: days, agent_mode: kind, agent_days: days, onboarding_complete: true}));
        // One choice covers everything kept, including run summaries.
        await api('/v1/run-memory/policy', send('PUT', {enabled: kind !== 'ephemeral', days: kind === 'days' ? days : kind === 'forever' ? 3650 : Number(privacy.policy?.run_memory?.days || 90)}));
        toast(kind === 'ephemeral' ? 'History is deleted when each session ends.' : `History is kept ${kind === 'forever' ? 'until you delete it' : `for ${days} days`}.`);
        window.refreshHistory?.();
      } catch (error) { toast(error.message || 'Could not save.'); }
      refreshPrivacy();
    };
    panel.querySelector('#pvAiSwitch').onclick = async () => {
      try { await api('/v1/ai-access', send('POST', {enabled: !privacy.access?.enabled})); }
      catch (error) { toast(error.message || 'Could not change AI access.'); }
      window.refreshConnections?.(); window.refreshDashboardAiAccess?.(); window.refreshAiPanel?.();
      refreshPrivacy();
    };
    panel.querySelector('#pvRestartSwitch').onclick = async () => {
      try { await api('/v1/ai-access', send('POST', {reset_on_restart: !privacy.access?.resets_on_restart})); }
      catch (error) { toast(error.message || 'Could not save.'); }
      refreshPrivacy();
    };
    panel.querySelector('#pvHistorySwitch').onclick = async () => {
      const on = String(privacy.history?.mode || 'off') !== 'off';
      try {
        await api('/v1/history/ai-access', send('POST', on ? {mode: 'off'} : {mode: 'all_saved', expires_minutes: 1440}));
        toast(on ? 'AI apps can no longer read older history.' : 'AI apps can read older history for the next 24 hours.');
        window.refreshHistory?.();
      } catch (error) { toast(error.message || 'Could not change history access.'); }
      refreshPrivacy();
    };
    panel.querySelector('#pvRedactSwitch').onclick = async () => {
      const redacted = privacy.detail?.detail_level !== 'full';
      if (redacted && !confirm('Show AI apps titles exactly as stored? Names are already tokenized before storage, but anything the detector missed would be visible.')) return;
      try { privacy.detail = await api('/v1/ai-context', send('POST', {detail: redacted ? 'full' : 'redacted'})); }
      catch (error) { toast(error.message || 'Could not save.'); }
      window.refreshAiContextDetail?.();
      renderPrivacy();
    };
    panel.querySelectorAll('#pvBrowserRow [data-profile]').forEach(button => button.onclick = async () => {
      try { privacy.browser = await api('/v1/browser-signal-settings', send('POST', {profile: button.dataset.profile})); toast('Browser detail saved.'); }
      catch (error) { toast(error.message || 'Could not save.'); }
      renderPrivacy();
    });
    panel.querySelectorAll('#pvNeverRecord .chip-add').forEach(form => form.onsubmit = event => {
      event.preventDefault();
      const input = form.querySelector('input');
      const value = String(input.value || '').trim();
      if (!value) return;
      const key = form.closest('.pv-list').dataset.list;
      input.value = '';
      saveLists({[key]: [...(privacy.lists?.[key] || []), value]});
    });
    panel.querySelector('#pvRestoreDefaults').onclick = () => {
      if (privacy.lists?.defaults && confirm('Replace your lists with the default list (password managers, and titles containing password, private, incognito or bank)?')) saveLists(privacy.lists.defaults);
    };
    panel.querySelectorAll('[data-delete]').forEach(button => button.onclick = () => confirmDelete(button.dataset.delete));
    panel.querySelector('#pvShowAdvanced').onclick = () => setMode('advanced');
  }

  function confirmDelete(kind) {
    const until = new Date();
    let since;
    if (kind === 'all') since = new Date('1970-01-01T00:00:00Z');
    else if (kind === 'today') { since = new Date(until); since.setHours(0, 0, 0, 0); }
    else since = new Date(until.getTime() - Number(kind) * 60 * 1000);
    const label = kind === 'all' ? 'everything OpenWorkGraph has recorded' : kind === 'today' ? 'everything recorded today' : `the last ${kind === '60' ? 'hour' : `${kind} minutes`}`;
    if (typeof window.openModal !== 'function') return;
    window.openModal('Delete recorded activity?', 'Privacy', `<p>This deletes ${esc(label)} from this computer. It cannot be undone.</p><div class="note">Anything already shared with an organization is not recalled.</div><div class="modal-actions"><button type="button" id="pvConfirmDelete" style="background:var(--red);border-color:var(--red)">Delete</button><button type="button" class="secondary" onclick="closeModal()">Cancel</button></div>`);
    const button = $('#pvConfirmDelete');
    if (button) button.onclick = async () => {
      button.disabled = true; button.textContent = 'Deleting…';
      try {
        const result = await api('/v1/evidence/delete', send('POST', {since: since.toISOString(), until: until.toISOString()}));
        window.closeModal?.();
        toast(`${Number(result.deleted_events || 0)} recorded event${Number(result.deleted_events || 0) === 1 ? '' : 's'} deleted.`);
        window.load?.(); window.refreshHistory?.();
      } catch (error) {
        button.disabled = false; button.textContent = 'Delete';
        toast(error.message || 'Could not delete.');
      }
    };
  }

  let privacyLoading = false;
  async function refreshPrivacy() {
    if (privacyLoading) return;
    privacyLoading = true;
    try {
      const [capture, policy, access, history, detail, browser, lists] = await Promise.all([
        api('/v1/capture/status').catch(() => null), api('/v1/history-policy').catch(() => null), api('/v1/ai-access').catch(() => null),
        api('/v1/history/ai-access').catch(() => null), api('/v1/ai-context').catch(() => null),
        api('/v1/browser-signal-settings').catch(() => null), api('/v1/capture-exclusions').catch(() => null),
      ]);
      Object.assign(privacy, {capture, policy, access, history: history?.access || null, detail, browser, lists});
      renderPrivacy();
      renderHero();
    } finally { privacyLoading = false; }
  }
  window.refreshPrivacyTab = refreshPrivacy;

  // ------------------------------------------------------------------ Today hero

  const today = {connections: null, gateway: null};

  function ensureHero() {
    const overview = $('#panel-overview');
    if (!overview || $('#todayHero')) return;
    const hero = document.createElement('div');
    hero.id = 'todayHero';
    hero.className = 'card hero';
    hero.innerHTML = `
      <div class="hero-main">
        <div class="hero-status"><span class="hero-dot" id="heroDot"></span><div><h2 id="heroTitle">Checking…</h2><div class="muted" id="heroSub"></div></div></div>
        <div class="hero-actions" id="heroActions"></div>
      </div>
      <div class="checklist" id="setupChecklist" hidden>
        <div class="checklist-head"><strong>Get set up</strong><button type="button" class="linkish" id="checklistHide">Hide</button></div>
        <ol id="setupSteps"></ol>
      </div>`;
    overview.insertBefore(hero, overview.firstChild);
    hero.querySelector('#checklistHide').onclick = () => { store.set(CHECKLIST_KEY, '1'); renderHero(); };
  }

  function fmtDuration(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${m}m` : m ? `${m} min` : `${s}s`;
  }

  function renderHero() {
    ensureHero();
    const summary = window.__owgLastSummary || {};
    const capture = privacy.capture || {};
    const state = String(capture.state || '');
    const tools = new Set((summary.surfaces || summary.apps || []).map(x => String(x.surface || x.app || ''))).size;
    const engaged = Number(summary.total_engaged_seconds || 0);
    const events = Number(summary.events || (summary.recent_evidence || []).length || 0);
    const aiOn = !!privacy.access?.enabled;
    const shared = !!today.gateway?.enrolled && !!today.gateway?.gateway_enabled && !today.gateway?.sharing_paused;
    const title = $('#heroTitle'), sub = $('#heroSub'), dot = $('#heroDot'), actions = $('#heroActions');
    if (!title) return;
    if (window.__owgAuthLost) {
      title.textContent = 'This page needs to be reopened';
      sub.textContent = 'Open the dashboard from the OpenWorkGraph launcher to continue.';
      dot.className = 'hero-dot off';
    } else if (capture.demo) {
      title.textContent = 'Showing demo data'; sub.textContent = 'Nothing from this computer is being recorded.'; dot.className = 'hero-dot off';
    } else if (state === 'paused') {
      title.textContent = 'Recording is paused'; sub.textContent = 'Nothing is recorded until you resume.'; dot.className = 'hero-dot paused';
    } else if (state === 'stopped') {
      title.textContent = 'Recording is stopped'; sub.textContent = 'Your data is kept on this computer.'; dot.className = 'hero-dot off';
    } else {
      title.textContent = engaged >= 60 ? `${fmtDuration(engaged)} of work recorded so far` : 'Recording your work';
      const parts = [];
      if (tools) parts.push(`${tools} ${tools === 1 ? 'tool' : 'tools'}`);
      parts.push(aiOn ? 'AI apps can read it' : 'AI apps can\'t read it');
      parts.push(shared ? 'shared with your organization' : 'nothing shared');
      sub.textContent = parts.join(' · ');
      dot.className = 'hero-dot rec';
    }
    actions.innerHTML = (events > 0 && typeof window.firstValueReconstruction === 'function' ? '<button type="button" class="green" id="heroSee">See what happened</button>' : '')
      + '<button type="button" class="secondary" id="heroPrivacy">Privacy</button>';
    $('#heroSee')?.addEventListener('click', () => window.firstValueReconstruction());
    $('#heroPrivacy').onclick = () => { window.activateTab?.('privacy'); refreshPrivacy(); };

    // Setup checklist: three steps, hidden when done or dismissed.
    const sensor = summary.browser_sensor;
    const steps = [
      {done: !!privacy.policy?.onboarding_complete, title: 'Choose how long to keep your history', text: 'This session only, a few days, or longer so patterns across days show up.', action: 'Choose', run: () => { window.activateTab?.('privacy'); refreshPrivacy(); setTimeout(() => $('#pvRetention')?.focus(), 50); }},
      {done: !!(sensor && sensor.version_ok !== false), title: 'Add the browser sensor', text: 'Lets OpenWorkGraph tell Gmail from Salesforce instead of just "Chrome".', action: 'How', run: browserHelp},
      {done: (today.connections?.clients || []).some(c => c.mcp?.on), title: 'Connect an AI app (optional)', text: 'Let Claude, ChatGPT or Cursor use your work context when you ask.', action: 'Connect', run: () => window.activateTab?.('connect')},
    ];
    const box = $('#setupChecklist');
    const allDone = steps.every(s => s.done);
    box.hidden = allDone || store.get(CHECKLIST_KEY) === '1' || !!window.__owgAuthLost;
    $('#setupSteps').innerHTML = steps.map((s, i) => `<li class="${s.done ? 'done' : ''}"><span class="step-mark" aria-hidden="true">${s.done ? '✓' : i + 1}</span><div><strong>${esc(s.title)}</strong><div class="muted">${esc(s.text)}</div></div>${s.done ? '<span class="muted">Done</span>' : `<button type="button" class="secondary" data-step="${i}">${esc(s.action)}</button>`}</li>`).join('');
    box.querySelectorAll('[data-step]').forEach(button => button.onclick = () => steps[Number(button.dataset.step)].run());
  }

  function browserHelp() {
    window.openModal?.('Add the browser sensor', 'About 2 minutes', `<ol class="steps"><li>Run <strong>ADD_BROWSER_SENSOR</strong> from the OpenWorkGraph folder. It opens a folder and your browser's extensions page.</li><li>Turn on <strong>Developer mode</strong>, click <strong>Load unpacked</strong> and choose the folder that opened.</li><li>Come back here. This step ticks itself off when the sensor connects.</li></ol><div class="note">The sensor only reads the site and page title of the tab in front, never page contents or what you type.</div><div class="modal-actions"><button type="button" class="secondary" id="heroPair">Pair / repair sensor</button></div>`);
    $('#heroPair')?.addEventListener('click', () => window.showBrowserPairingCode?.());
  }

  // ------------------------------------------------------------------ stale tab banner

  function renderAuthBanner() {
    const lost = !!window.__owgAuthLost;
    let banner = $('#owgAuthBanner');
    if (!lost) { banner?.remove(); return; }
    if (banner) return;
    banner = document.createElement('div');
    banner.id = 'owgAuthBanner';
    banner.className = 'auth-banner';
    banner.setAttribute('role', 'alert');
    banner.innerHTML = '<strong>This dashboard page is from an earlier start of OpenWorkGraph.</strong> For your security it can\'t load data. Open the dashboard again from the OpenWorkGraph launcher (or run START_OPENWORKGRAPH), then close this tab.';
    const shell = $('.shell');
    if (shell) shell.insertBefore(banner, shell.firstChild);
  }

  // ------------------------------------------------------------------ data + lifecycle

  async function checkForUpdate() {
    try {
      const status = await api('/v1/update-status');
      let chip = $('#updateStatusChip');
      if (!status?.update_available || !status?.release_url) {
        chip?.remove();
        return;
      }
      if (!chip) {
        chip = document.createElement('button');
        chip.id = 'updateStatusChip';
        chip.type = 'button';
        chip.className = 'status-chip action on';
        const row = $('.status-row');
        const spacer = row?.querySelector('.status-spacer');
        if (row) row.insertBefore(chip, spacer || null);
      }
      chip.textContent = `Update v${status.latest_version} available`;
      chip.title = 'Open the official OpenWorkGraph GitHub release. The version check sends no work evidence.';
      chip.onclick = () => window.open(status.release_url, '_blank', 'noopener,noreferrer');
    } catch (_) {}
  }

  let factsLoading = false;
  async function refreshFacts() {
    if (factsLoading || window.__owgAuthLost) return;
    factsLoading = true;
    try {
      const [connections, gateway, runs] = await Promise.all([
        api('/v1/connections').catch(() => null), api('/v1/gateway-status').catch(() => null),
        api('/v1/agent-execution-traces?limit=1&evidence_limit=3000&max_events_per_execution=1').catch(() => null),
      ]);
      today.connections = connections; today.gateway = gateway;
      const observeOn = (connections?.clients || []).some(c => c.observe?.supported && c.observe?.on);
      facts.agentsSeen = observeOn || (runs?.executions || []).length > 0;
      facts.orgConnected = !!gateway?.enrolled || !!gateway?.managed || String(gateway?.mode || '').startsWith('connected');
      applyMode(); renderHero();
    } finally { factsLoading = false; }
  }

  let scheduled = false;
  function scheduleApply() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; applyMode(); renderAuthBanner(); });
  }

  // Tab and panel exist before the base dashboard wires its tabs (it runs on
  // DOMContentLoaded), so keyboard navigation includes Privacy too.
  ensurePrivacyTab();

  function install() {
    ensureModeSwitch(); ensurePrivacyTab(); ensureHero(); applyMode(true);
    // Re-render the hero whenever the dashboard summary arrives (every 5 s).
    const baseRenderGlobal = window.renderGlobal;
    if (typeof baseRenderGlobal === 'function' && !baseRenderGlobal.__owgHero) {
      window.renderGlobal = function (data) { baseRenderGlobal(data); try { renderHero(); } catch (_) {} };
      window.renderGlobal.__owgHero = true;
    }
    new MutationObserver(scheduleApply).observe($('main') || document.body, {childList: true, subtree: true});
    refreshPrivacy(); refreshFacts(); checkForUpdate();
    // Light polling, only while visible: the hero and Privacy reflect switches flipped elsewhere.
    setInterval(() => { if (!document.hidden) { refreshPrivacy(); renderAuthBanner(); } }, 5000);
    setInterval(() => { if (!document.hidden) refreshFacts(); }, 30000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshPrivacy(); refreshFacts(); } });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
