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

    // Privacy: retain source connections and both Open Work pilots, but keep
    // these optional specialist controls out of the simple Basic view.
    mark($('#pvRestartRow'), true);
    mark($('#pvSourceConnectionsRow'), true);
    mark($('#pvOpenWorkRow'), true);
    mark($('#pvGitHubOpenWorkRow'), true);
    mark($('#pvOpenWorkMcpRow'), true);

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
        <div><h2>Your privacy</h2><div class="muted">Your evidence is stored locally by default. If you connect an organization, permitted structural evidence can be shared with its Gateway. Optional cloud work-text sharing below is a separate personal-account choice that uploads redacted excerpts. Names, email addresses, phone numbers and personal numbers are filtered before storage. Typing is not captured by default; optional browser work-text capture can save visible drafts after you turn it on below. Password fields and clipboard contents are excluded.</div></div>
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
          <div class="pv-control"><select id="pvHistoryDuration" aria-label="Older history access duration"><option value="1440">24 hours</option><option value="standing">Until I revoke access</option></select><button type="button" class="sw" role="switch" id="pvHistorySwitch" aria-checked="false" aria-label="Let AI apps read older history"></button></div>
        </div>
        <div class="muted">Procedures you explicitly save in an AI conversation are separate from recording history. Ask your AI to list or forget them.</div>
        <div class="pv-row pv-sub" id="pvRestartRow">
          <div><h3>Turn AI access off every time OpenWorkGraph starts</h3><div class="muted">Off: your choice above is remembered after a restart.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvRestartSwitch" aria-checked="false" aria-label="Turn AI access off every time OpenWorkGraph starts"></button></div>
        </div>
        <div class="pv-row" id="pvRedactRow">
          <div><h3>Hide names and contact details from AI apps</h3><div class="muted" id="pvRedactText">Recommended. Titles keep their meaning: "Re: Contract for PERSON_1A2B3C - Gmail".</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvRedactSwitch" aria-checked="true" aria-label="Hide names and contact details from AI apps"></button></div>
        </div>
        <div class="pv-row" id="pvSourceConnectionsRow">
          <div><h3>Optional source connections</h3><div class="muted">Connect a Stripe account for read-only status checks on invoices and refunds already observed by OWG. This computer stores the restricted key in its operating system credential vault. Organization enrollment is not required.</div></div>
          <div class="pv-control"><button type="button" class="secondary" id="pvStripeSource" onclick="openLocalStripeSource()">Manage Stripe read-only source</button></div>
        </div>
        <div class="pv-row pv-sub" id="pvOpenWorkRow">
          <div><h3>Open Work: invoice preview</h3><div class="muted">Optional read-only check for open invoices in your connected Stripe account, including invoices never shown in OWG. Past-due dates indicate candidates for review, not verified collection duties. No customer details or amounts are stored or shared with AI.</div><div class="muted" id="pvOpenWorkResult" role="status" aria-live="polite"></div><div id="pvOpenWorkCases" aria-label="Open invoice candidates"></div><div class="muted" id="pvOpenWorkReviewResult" role="status" aria-live="polite"></div><button type="button" class="secondary" id="pvOpenWorkReviewLoad">View saved reviews</button> <label class="muted"><input type="checkbox" id="pvDispatchEnabled"> Let connected agents pick up cases I offer (no extra permissions; turning this off withdraws all offers)</label><div id="pvOpenWorkReviews" aria-label="Locally saved case reviews"></div></div>
          <div class="pv-control"><button type="button" class="secondary" id="pvOpenWorkScan">Preview open invoices</button></div>
        </div>
        <div class="pv-row pv-sub" id="pvGitHubOpenWorkRow">
          <div><h3>Open Work: GitHub preview</h3><div class="muted">Optional read-only check for open pull requests in one repository you choose. Uses your existing GitHub CLI login and its separate OWG source-status permission. An open PR is not proof an action is required.</div><label for="pvGitHubOpenWorkRepo" class="muted">Repository (owner/name)</label><input id="pvGitHubOpenWorkRepo" type="text" spellcheck="false" autocomplete="off" placeholder="owner/repository" aria-label="GitHub repository owner/name" /><div class="muted" id="pvGitHubOpenWorkResult" role="status" aria-live="polite"></div></div>
          <div class="pv-control"><button type="button" class="secondary" id="pvGitHubOpenWorkScan">Preview open PRs</button></div>
        </div>
        <div class="pv-row pv-sub" id="pvOpenWorkMcpRow">
          <div><h3>Experimental Open Work AI tools</h3><div class="muted">Allow your existing local AI connector to expose two extra read-only tools: get_case_context and verify_case_operation, plus list/claim/report tools for cases you explicitly offer to agents below. This does not enable AI access, saved-history access, source scans, or business actions. Reconnect your AI app to refresh its tool list.</div><div class="muted" id="pvOpenWorkMcpStatus" role="status" aria-live="polite">Checking…</div></div>
          <div class="pv-control"><button type="button" role="switch" class="sw" aria-label="Experimental Open Work AI tools" id="pvOpenWorkMcpSwitch" aria-checked="false"></button></div>
        </div>
        <div class="pv-row" id="pvScreenCaptureRow">
          <div><h3>Full screen capture</h3><div class="muted">Off by default. When on, the desktop recorder saves every monitor at full resolution when you switch windows, on each click or scroll, and every <span id="pvScreenInterval">20</span> seconds. Everything visible is captured, including other people's messages and personal content. Images stay on this computer. Apps, sites and title words in Never record below are still skipped, and Pause stops capture.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvScreenCaptureSwitch" aria-checked="false" aria-label="Full screen capture"></button></div>
        </div>
        <div class="pv-row pv-sub" id="pvScreenOcrRow">
          <div><h3>Also read text on screen</h3><div class="muted">Recognizes the text in each capture on this computer (needs Tesseract installed) and saves it next to the image.</div><div class="muted" id="pvScreenOcrStatus" role="status" aria-live="polite"></div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvScreenOcrSwitch" aria-checked="false" aria-label="Read text in screen captures"></button></div>
        </div>
        <div class="pv-row" id="pvWorkTextRow">
          <div><h3>Capture visible work text for AI</h3><div class="muted">Off by default. When enabled, the paired browser sensor automatically saves redacted visible page text and drafts on permitted sites. This is separate from ordinary activity recording. Sensitive surfaces are blocked, but filtering cannot guarantee perfect removal of private information. Content stays on this computer unless you separately enable cloud synchronization below.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvWorkTextSwitch" aria-checked="false" aria-label="Capture visible browser work text"></button></div>
        </div>
        <div class="pv-row pv-sub" id="pvWorkTextAiRow">
          <div><h3>Allow local AI apps to read saved work text</h3><div class="muted">A separate permission requiring AI access. Cloud apps require a separate cloud-sharing opt-in.</div></div>
          <div class="pv-control"><button type="button" class="sw" role="switch" id="pvWorkTextAiSwitch" aria-checked="false" aria-label="Allow local AI apps to read captured work text"></button></div>
        </div>
        <div class="pv-row pv-sub" id="pvWorkTextCloudRow">
          <div><h3>Share redacted work content with my cloud AI apps</h3><div class="muted">Off by default. Uploads require BOTH this computer's permission and a separate approval while signed in to your personal OWG cloud account. Until both are complete, nothing is uploaded. Only new redacted browser page text and drafts from permitted sites can synchronize. Stored for up to 7 days; connected AI can search while your computer is off. Cloud sharing can involve third-party personal or confidential information; filters are not perfect. Disabling stops new sharing immediately; removal of already uploaded copies is requested when this desktop reconnects. If it stays offline, existing copies remain accessible until their seven-day expiry.</div><label class="muted" style="display:block;margin-top:9px"><input type="checkbox" id="pvCloudAck" /> I understand content will be uploaded and may contain third-party information. I confirm I am authorized to share it.</label></div>
          <div class="pv-control"><a href="https://mcp.owg.kinvectum.com/work-text" target="_blank" rel="noopener noreferrer">Account approval and deletion</a><button type="button" class="sw" role="switch" id="pvWorkTextCloudSwitch" aria-checked="false" aria-label="Allow cloud AI to retrieve captured work text"></button></div>
        </div>
        <div class="pv-row" id="pvBrowserRow">
          <div><h3>Browser detail</h3><div class="muted" id="pvBrowserText">How much the browser sensor keeps about the pages you use. This is the same setting shown in Advanced → Settings.</div><div class="muted" style="margin-top:6px"><strong>Standard (default)</strong> protects document and conversation IDs, but an AI cannot reliably return to the exact ChatGPT/Claude conversation, document or issue. <strong>More context (optional)</strong> stores installation-keyed, opaque local object references so agents can link related work; it does not store full conversation URLs or page contents. This does not grant an AI access to older history or new apps. You can change it later.</div></div>
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

      <div class="card" id="pvSchedules">
        <h2>Scheduled jobs</h2>
        <div class="muted">Off unless you add one. A schedule repeats a job you could run by hand: refresh a retained Jira profile, re-check saved event-log signals, or create a verified local backup. Source reads keep their own consent and pause with recording. Backups require separate acknowledgement, exclude credentials, are NOT encrypted, run at most daily and rotate only scheduled backups (up to 14). Optional per-job quiet hours defer due jobs without counting a run; daylight saving follows the chosen timezone. Failures back off and pause after five in a row. Nothing is sent anywhere.</div>
        <div class="pv-delete">
          <select id="pvScheduleKind" aria-label="Job"><option value="retained_jira_profile">Retained Jira profile</option><option value="event_log_signals">Event log signals</option><option value="local_backup">Local backup (unencrypted)</option></select>
          <input type="text" id="pvScheduleTarget" placeholder="Profile or event log ID" aria-label="Profile or event log ID" spellcheck="false">
          <input type="number" id="pvScheduleInterval" min="15" value="60" aria-label="Interval in minutes">
          <button type="button" class="secondary" id="pvScheduleAdd">Add schedule</button>
        </div>
        <label class="muted" for="pvScheduleQuietEnabled">
          <input type="checkbox" id="pvScheduleQuietEnabled"> Only run outside quiet hours (optional, per schedule)
        </label>
        <div class="pv-delete" id="pvScheduleQuietFields" hidden>
          <label for="pvScheduleQuietStart">Quiet from</label>
          <input type="time" id="pvScheduleQuietStart" value="22:00" step="60">
          <label for="pvScheduleQuietEnd">Quiet until</label>
          <input type="time" id="pvScheduleQuietEnd" value="07:00" step="60">
          <label for="pvScheduleQuietTimezone">Timezone</label>
          <input type="text" id="pvScheduleQuietTimezone" placeholder="Europe/Stockholm" maxlength="64" spellcheck="false">
        </div>
        <div class="muted">For an existing job, set the window above and choose Apply quiet hours on that job, or clear its current window. Pausing or removing a job remains separate.</div>
        <label class="muted" id="pvScheduleBackupAckRow" hidden>
          <input type="checkbox" id="pvScheduleBackupAck"> I understand recurring backups save unencrypted copies of locally recorded activity outside OWG's data folder, without credentials; only automatically created backups are rotated. I will keep the backup folder private.
        </label>
        <div class="muted" id="pvScheduleStatus" role="status" aria-live="polite"></div>
        <div id="pvScheduleList" role="region" aria-label="Scheduled jobs"></div>
      </div>
      <div class="card" id="pvSemantic">
        <h2>AI help with grouping work</h2>
        <div class="muted">Optional. OWG links work by exact records, tabs, copy-paste and shared words. A model you choose can also say which of those already-linked pieces belong to the same task by meaning. It receives app names and short screen labels only (no URLs, record IDs or clipboard contents), and its answers are shown as suggestions, never merged automatically. Use any OpenAI-compatible endpoint, including one running on this computer.</div>
        <div class="pv-delete">
          <input type="url" id="pvSemanticUrl" placeholder="https://api.example.com/v1 or http://127.0.0.1:11434/v1" aria-label="Model endpoint URL" spellcheck="false">
          <input type="text" id="pvSemanticModel" placeholder="Model name" aria-label="Model name" spellcheck="false">
          <input type="text" id="pvSemanticKeyEnv" placeholder="Key variable name, e.g. OPENAI_API_KEY" aria-label="Environment variable holding the key" spellcheck="false">
          <button type="button" class="secondary" id="pvSemanticSave">Turn on</button>
          <button type="button" class="secondary" id="pvSemanticOff">Turn off</button>
        </div>
        <div class="muted" id="pvSemanticStatus" role="status" aria-live="polite"></div>
      </div>
      <div class="card" id="pvOAuth">
        <h2>Business system sign-in</h2>
        <div class="muted">Connect a business system with an app you registered in your own tenant. You approve the access at the provider; tokens stay encrypted on this computer and are never shown to AI. Connecting reads nothing by itself: each connector still asks before reading.</div>
        <div class="pv-delete">
          <select id="pvOAuthProvider" aria-label="Provider"><option value="salesforce">Salesforce</option><option value="servicenow">ServiceNow</option><option value="microsoft">Microsoft 365</option><option value="atlassian">Atlassian (Jira)</option><option value="zendesk">Zendesk</option></select>
          <input type="text" id="pvOAuthHost" placeholder="Host or tenant (e.g. acme.my.salesforce.com)" aria-label="Provider host or tenant" spellcheck="false">
          <input type="text" id="pvOAuthClient" placeholder="Client ID" aria-label="Client ID" spellcheck="false">
          <input type="password" id="pvOAuthSecret" placeholder="Client secret (only if required)" aria-label="Client secret (optional)" autocomplete="off">
          <button type="button" class="secondary" id="pvOAuthConnect">Connect</button>
        </div>
        <div class="muted" id="pvOAuthStatus" role="status" aria-live="polite"></div>
        <div id="pvOAuthList" aria-label="Connected business systems"></div>
      </div>
      <div class="card" id="pvCaptureDepth">
        <h2>Interaction detail</h2>
        <div class="muted">How much interaction detail the browser extension and desktop recorder keep on this computer. Standard keeps today's signals. Enhanced adds how long you rest the pointer on buttons and links, drag and drop between controls, and friction candidates such as repeated clicks. Full fidelity also samples pointer movement. Typed text, clipboard contents, dragged data, control labels and secure fields are never recorded. Browser hover, drag and pointer detail is suppressed over embedded frames in a parent page, or when secure-field inspection is unavailable or exceeds its safety limits. Enhanced and Full fidelity last for the period you choose, then return to Standard. Sharing, AI access, exports, screenshots and work text keep their own separate settings. Local backups include recorded pointer movement.</div>
        <div class="pv-delete" role="radiogroup" aria-label="Interaction detail profile">
          <label><input type="radio" name="pvDepth" value="standard"> Standard</label>
          <label><input type="radio" name="pvDepth" value="enhanced"> Enhanced</label>
          <label><input type="radio" name="pvDepth" value="full"> Full fidelity</label>
        </div>
        <div class="pv-delete">
          <label><input type="checkbox" id="pvDepthHover"> Hover dwell on controls</label>
          <label><input type="checkbox" id="pvDepthDrag"> Drag and drop</label>
          <label><input type="checkbox" id="pvDepthFriction"> Friction candidates</label>
          <label><input type="checkbox" id="pvDepthTrajectory"> Sampled pointer movement</label>
        </div>
        <div class="pv-delete">
          <label for="pvDepthDwell">Hover counts after (ms)</label><input type="number" id="pvDepthDwell" min="300" max="5000">
          <label for="pvDepthHz">Pointer samples per second</label><input type="number" id="pvDepthHz" min="1" max="20">
          <label for="pvDepthRetention">Keep interaction detail (days)</label><input type="number" id="pvDepthRetention" min="1" max="365">
          <label for="pvDepthStorage">Store at most (MB)</label><input type="number" id="pvDepthStorage" min="10" max="10000">
          <label for="pvDepthDuration">Record extra detail for</label><select id="pvDepthDuration"><option value="1">1 day</option><option value="7" selected>7 days</option><option value="30">30 days</option><option value="90">90 days</option></select>
        </div>
        <label class="pv-delete"><input type="checkbox" id="pvDepthAck"> <span id="pvDepthAckText"></span></label>
        <div class="pv-delete">
          <button type="button" class="secondary" id="pvDepthSave">Save interaction detail</button>
          <button type="button" class="secondary" id="pvDepthCoverage">Show capture coverage</button>
          <button type="button" class="secondary" id="pvDepthFrictionShow">Show friction candidates</button>
          <button type="button" class="secondary" id="pvDepthHeatmap">Show pointer heatmap</button>
          <button type="button" class="secondary" id="pvDepthPlayback">Replay a visit</button>
          <button type="button" class="danger-quiet" id="pvDepthDelete">Delete interaction detail now</button>
        </div>
        <div class="muted" id="pvDepthStatus" role="status" aria-live="polite"></div>
        <div id="pvDepthResult"></div>
      </div>
      <div class="card" id="pvDelete">
        <h2>Delete recorded activity</h2>
        <div class="muted">Removes activity from this computer. It cannot be undone. Anything shared with an organization is not recalled. If cloud work-text sharing was enabled, corresponding uploaded copies are removed on the next successful synchronization; while this computer is offline, they may remain available for up to seven days.</div>
        <div class="pv-delete">
          <button type="button" class="danger-quiet" data-delete="15">Last 15 minutes</button>
          <button type="button" class="danger-quiet" data-delete="60">Last hour</button>
          <button type="button" class="danger-quiet" data-delete="today">Today</button>
          <button type="button" class="danger-quiet" data-delete="all">Everything</button>
        </div>
      </div>
      <div class="card" id="pvBackup">
        <h2>Back up recorded activity</h2>
        <div class="muted">Saves a verified copy of everything OpenWorkGraph keeps on this computer to the backup folder. It includes recorded interaction detail, such as sampled pointer movement, so keep it as private as the computer itself. Add a passphrase (12+ characters) to also include this computer's keys and encrypt the file; without one the file is not encrypted. Restoring is done with OpenWorkGraph closed: <code>python -m server.local_backup restore</code>.</div>
        <div class="pv-delete"><input type="password" id="pvBackupPass" autocomplete="new-password" placeholder="Optional passphrase" aria-label="Backup passphrase (optional)"><button type="button" class="secondary" id="pvBackupCreate">Back up now</button></div>
        <div class="muted" id="pvBackupStatus" role="status" aria-live="polite"></div>
        <div class="pv-row" style="margin-top:12px">
          <div>
            <h3>Inspect a saved backup before restoring</h3>
            <div class="muted">Checks an existing backup without changing any data. Encrypted backups must be inspected with the offline command-line tool, never by entering a passphrase here. Restore itself is offline-only.</div>
            <label for="pvBackupChoice" class="muted">Backup in this computer's backup folder</label>
            <select id="pvBackupChoice" aria-label="Saved backup"><option value="">Loading saved backups…</option></select>
            <div class="muted" id="pvBackupPlan" role="status" aria-live="polite">Select a backup to inspect.</div>
          </div>
          <div class="pv-control">
            <button type="button" class="secondary" id="pvBackupRefresh">Refresh list</button>
            <button type="button" class="secondary" id="pvBackupInspect" disabled>Inspect restore safety</button>
          </div>
        </div>
        <div class="pv-row" id="pvBackupHealthPanel" style="margin-top:12px">
          <div>
            <h3>Check saved backup integrity</h3>
            <div class="muted">Explicitly verify up to five listed plaintext archives, checking every stored file against its manifest hashes. This may read large local backups. Encrypted or oversize files require offline checks. A healthy archive is not a completed restore test.</div>
            <div class="muted" id="pvBackupHealthStatus" role="status" aria-live="polite">No integrity inventory checked yet.</div>
            <div id="pvBackupHealthRows" role="region" aria-label="Saved backup integrity results"></div>
          </div>
          <div class="pv-control">
            <button type="button" class="secondary" id="pvBackupHealthRun" disabled>Verify first 5 listed backups</button>
          </div>
        </div>
      </div>
      <div class="muted pv-more">More controls (separate retention for agents, saved sessions, browser profiles, organization sharing) are in the <button type="button" class="linkish" id="pvShowAdvanced">Advanced</button> view.</div>`;
    main.insertBefore(panel, $('#panel-settings') || null);
    bindPrivacy(panel);
  }

  const privacy = {capture: null, policy: null, access: null, history: null, detail: null, browser: null, lists: null, workText: null};

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
    const duration = $('#pvHistoryDuration');
    if (duration && historyMode !== 'off') duration.value = p.history?.expires_at ? '1440' : 'standing';
    const historyText = $('#pvHistoryText');
    if (historyText) {
      const until = p.history?.expires_at ? new Date(p.history.expires_at).toLocaleString([], {weekday: 'short', hour: '2-digit', minute: '2-digit'}) : '';
      historyText.textContent = historyMode === 'off'
        ? (on ? 'Off: AI apps only see the current run.' : 'Turn on AI access first.')
        : `On${historyMode === 'selected_range' ? ' for selected dates' : ''}${until ? ` until ${until}` : ''}${until ? '. Access expires automatically.' : '. Access stays on until you revoke it.'}`;
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
    // Full screen capture is a separate, explicit choice; default OFF.
    const screenOn = p.screen?.mode === 'full';
    setSwitch('#pvScreenCaptureSwitch', screenOn);
    setSwitch('#pvScreenOcrSwitch', !!p.screen?.ocr_enabled);
    const screenOcr = $('#pvScreenOcrSwitch');
    if (screenOcr) screenOcr.disabled = !screenOn;
    const ocrStatus = $('#pvScreenOcrStatus');
    if (ocrStatus) ocrStatus.textContent = p.screen && !p.screen.ocr_available ? 'Tesseract is not installed on this computer, so text is not recognized yet.' : '';
    const screenInterval = $('#pvScreenInterval');
    if (screenInterval && p.screen?.interval_seconds) screenInterval.textContent = String(Math.round(p.screen.interval_seconds));
    // Separate work-text capture and AI read grants; both default OFF.
    setSwitch('#pvWorkTextSwitch', !!p.workText?.capture_enabled);
    setSwitch('#pvWorkTextAiSwitch', !!p.workText?.ai_read_enabled);
    setSwitch('#pvWorkTextCloudSwitch', !!p.workText?.cloud_read_enabled);
    const aiWorkText = $('#pvWorkTextAiSwitch');
    if (aiWorkText) aiWorkText.disabled = !p.workText?.capture_enabled;
    const cloudText = $('#pvWorkTextCloudSwitch');
    if (cloudText) cloudText.disabled = !p.workText?.capture_enabled || !p.workText?.ai_read_enabled;
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

  function openWorkStatus(reason, provider) {
    const messages = provider === 'stripe'
      ? {
          stripe_source_read_opt_in_off: 'Connect Stripe first to run this local read-only preview.',
          stripe_restricted_read_config_missing: 'Reconnect Stripe with a restricted read-only key.',
          stripe_account_identity_mismatch: 'This Stripe key does not match the connected account. Reconnect the correct account.',
          stripe_invoice_inventory_unavailable: 'Stripe did not return a usable invoice list. Check your read-only invoice-list permission and connection.',
          invalid_stripe_invoice_page: 'Stripe returned an unexpected invoice list format. The scan was stopped.',
          conflicting_stripe_invoice_status: 'Stripe returned conflicting invoice status information. Nothing was marked complete.',
          invalid_local_reference_secret: 'Local Open Work reference storage is unavailable.',
        }
      : {
          existing_github_outcome_tracking_opt_in_off: 'Enable optional GitHub source-status reads first.',
          github_cli_not_installed: 'Install and sign in to the GitHub CLI to preview this repository.',
          github_cli_read_failed: 'Could not read this repository using your GitHub CLI account.',
          invalid_github_open_pr_inventory: 'GitHub returned an unexpected pull-request list.',
        };
    return messages[reason] || 'This source could not be checked. Review the local connection and try again.';
  }

  function bindPrivacy(panel) {
    const experimentalToggle = panel.querySelector('#pvOpenWorkMcpSwitch');
    const experimentalStatus = panel.querySelector('#pvOpenWorkMcpStatus');
    const showExperimentalTools = state => {
      const enabled = state?.enabled === true;
      if (experimentalToggle) experimentalToggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
      if (experimentalStatus) experimentalStatus.textContent = state?.environment_override_active
        ? 'Your local launcher overrides this setting. Edit its OWG_OPEN_WORK_EXPERIMENTAL_TOOLS value to change the tool list.'
        : enabled
          ? 'On for local MCP. Reconnect your AI app to discover the two extra tools. All existing access controls remain.'
          : 'Off. Existing Open Work tools are denied immediately; reconnect your AI app to refresh its tool list.';
    };
    if (experimentalToggle) {
      api('/v1/local-open-work/mcp-tools').then(showExperimentalTools).catch(() => {
        if (experimentalStatus) experimentalStatus.textContent = 'Could not read local tool settings.';
      });
      experimentalToggle.onclick = async () => {
        const enabled = experimentalToggle.getAttribute('aria-checked') !== 'true';
        if (enabled && !confirm('Enable two extra read-only Open Work tools in local AI apps? Your existing AI and history access controls still apply. Reconnect your AI app afterwards.')) return;
        experimentalToggle.disabled = true;
        try {
          showExperimentalTools(await api('/v1/local-open-work/mcp-tools', send('POST', {enabled})));
        } catch (error) {
          if (experimentalStatus) experimentalStatus.textContent = 'Could not update local Open Work tool settings.';
        } finally { experimentalToggle.disabled = false; }
      };
    }
    const gitHubPreview = panel.querySelector('#pvGitHubOpenWorkScan');
    if (gitHubPreview) gitHubPreview.onclick = async () => {
      const input = panel.querySelector('#pvGitHubOpenWorkRepo');
      const status = panel.querySelector('#pvGitHubOpenWorkResult');
      const repository = String(input?.value || '').trim();
      if (!/^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/.test(repository)) {
        if (status) status.textContent = 'Enter one exact owner/repository name.';
        return;
      }
      if (!confirm('Read up to 50 open pull requests in this repository with your existing GitHub CLI login? Nothing will be changed or shared with a cloud AI.')) return;
      gitHubPreview.disabled = true;
      if (status) status.textContent = 'Checking the selected repository...';
      try {
        const result = await api('/v1/open-work/github-preview?confirm_scan=true&limit=50&repository=' + encodeURIComponent(repository));
        if (result.availability !== 'source_reported') {
          if (status) status.textContent = openWorkStatus(result.reason, 'github');
        } else {
          const candidates = Array.isArray(result.candidates) ? result.candidates : [];
          const awaitingReview = candidates.filter(c => c.review_status === 'review_required').length;
          const coverage = result.source_coverage_complete ? '' : ' There may be more beyond this preview.';
          if (status) status.textContent = candidates.length + ' open PR candidate(s); ' + awaitingReview + ' report review required.' + coverage + ' Source status is not a verified business obligation.';
        }
      } catch (error) {
        if (status) status.textContent = 'Preview unavailable: ' + (error.message || 'request failed');
      } finally { gitHubPreview.disabled = false; }
    };
    const reviewLoad = panel.querySelector('#pvOpenWorkReviewLoad');
    const reviews = panel.querySelector('#pvOpenWorkReviews');
    const reviewStatus = panel.querySelector('#pvOpenWorkReviewResult');
    const dispatchToggle = panel.querySelector('#pvDispatchEnabled');
    let dispatchState = null;
    const refreshSavedReviews = async () => {
      if (!reviews) return;
      dispatchState = await api('/v1/open-work/dispatch').catch(() => null);
      if (dispatchToggle) dispatchToggle.checked = !!dispatchState?.enabled;
      const offers = new Map((dispatchState?.offers || []).map(o => [o.work_item_ref, o]));
      const result = await api('/v1/open-work/reviews');
      reviews.replaceChildren();
      const items = Array.isArray(result.items) ? result.items : [];
      if (reviewStatus) reviewStatus.textContent = items.length
        ? items.length + ' locally saved review intention(s). Source status is not refreshed.'
        : 'No saved review intentions on this computer.';
      const labels = {
        assess_follow_up: 'Assess whether follow-up is appropriate',
        verify_payment_status: 'Recheck provider payment status',
        check_approved_policy: 'Check separately approved business policy',
      };
      for (const item of items) {
        if (!/^owg:w:[0-9a-f]{32}$/.test(String(item.work_item_ref || ''))) continue;
        const line = document.createElement('div');
        line.className = 'pv-row pv-sub';
        const description = document.createElement('span');
        description.textContent = (labels[item.review_intent] || 'Review candidate') +
          ' · source status not refreshed · expires ' + String(item.expires_at || '').slice(0, 10);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'secondary';
        remove.textContent = 'Forget';
        remove.onclick = async () => {
          if (!confirm('Delete this local review intention? No source record will be changed.')) return;
          remove.disabled = true;
          try {
            await api('/v1/open-work/reviews/' + encodeURIComponent(item.work_item_ref), {method: 'DELETE'});
            await refreshSavedReviews();
          } catch (_) {
            if (reviewStatus) reviewStatus.textContent = 'Could not delete the local review intention.';
          } finally { remove.disabled = false; }
        };
        const offered = offers.get(item.work_item_ref);
        const handoff = document.createElement('button');
        handoff.type = 'button';
        handoff.className = 'secondary';
        handoff.textContent = offered ? 'Withdraw from agents' : 'Offer to agents';
        handoff.disabled = !dispatchState?.enabled;
        handoff.onclick = async () => {
          handoff.disabled = true;
          try {
            if (offered) await api('/v1/open-work/dispatch/offers/withdraw', send('POST', {work_item_ref: item.work_item_ref}));
            else {
              if (!confirm('Offer this case to connected agents? They get the opaque case reference and your review intent only, and must use their own authorized tools.')) return;
              await api('/v1/open-work/dispatch/offers', send('POST', {work_item_ref: item.work_item_ref, confirm_offer: true}));
            }
            await refreshSavedReviews();
          } catch (error) {
            if (reviewStatus) reviewStatus.textContent = error.message || 'Could not change the agent offer.';
          } finally { handoff.disabled = !dispatchState?.enabled; }
        };
        if (offered) {
          const report = offered.last_report;
          description.textContent += ' · ' + (offered.claimed_by ? 'claimed by ' + offered.claimed_by
            : report ? 'agent reported "' + report.outcome + '" (unverified)' : 'offered to agents');
        }
        line.append(description, handoff, remove);
        reviews.append(line);
      }
    };
    if (dispatchToggle) dispatchToggle.onchange = async () => {
      try {
        await api('/v1/open-work/dispatch/settings', send('POST', {enabled: dispatchToggle.checked}));
        await refreshSavedReviews();
      } catch (error) {
        dispatchToggle.checked = !dispatchToggle.checked;
        if (reviewStatus) reviewStatus.textContent = error.message || 'Could not change agent dispatch.';
      }
    };
    if (reviewLoad) reviewLoad.onclick = () => refreshSavedReviews().catch(() => {
      if (reviewStatus) reviewStatus.textContent = 'Local review queue unavailable.';
    });
    const openWork = panel.querySelector('#pvOpenWorkScan');
    if (openWork) openWork.onclick = async () => {
      const status = panel.querySelector('#pvOpenWorkResult');
      if (!confirm('Read the first 100 open invoice records from the connected Stripe account? This is a local read-only preview; no customer details or amounts are saved or shared with AI.')) return;
      openWork.disabled = true;
      if (status) status.textContent = 'Checking the connected source...';
      try {
        const result = await api('/v1/open-work/stripe-preview?confirm_scan=true&max_pages=1&include_local_observation=true');
        if (result.availability !== 'source_reported') {
          if (status) status.textContent = openWorkStatus(result.reason, 'stripe');
          const sourceArea = panel.querySelector('#pvOpenWorkCases');
          if (sourceArea) {
            sourceArea.replaceChildren();
            if (['stripe_source_read_opt_in_off', 'stripe_restricted_read_config_missing',
                 'stripe_account_identity_mismatch'].includes(result.reason)) {
              const connect = document.createElement('button');
              connect.type = 'button';
              connect.className = 'secondary';
              connect.textContent = 'Connect Stripe';
              connect.onclick = () => window.openLocalStripeSource?.();
              sourceArea.append(connect);
            }
          }
        } else {
          const rows = Array.isArray(result.candidates) ? result.candidates : [];
          const pastDue = rows.filter(row => row.past_due_date === true).length;
          const coverage = result.source_coverage_complete ? 'Complete source page range.' : 'More open invoices may exist beyond this preview.';
          if (status) status.textContent = rows.length + ' open invoice candidate(s); ' + pastDue + ' past the reported due date. ' + coverage + ' No OWG observation alone proves that a person did or did not complete the work. Review in Stripe before follow-up.';
          const caseArea = panel.querySelector('#pvOpenWorkCases');
          if (caseArea) {
            caseArea.replaceChildren();
            for (const candidate of rows.slice(0, 100)) {
              if (!/^owg:w:[0-9a-f]{32}$/.test(String(candidate.work_item_ref || ''))) continue;
              const line = document.createElement('div');
              line.className = 'pv-row pv-sub';
              const summary = document.createElement('span');
              summary.textContent = candidate.past_due_date ? 'Open invoice · past source due date' : 'Open invoice · review candidate';
              const observation = document.createElement('span');
              observation.className = 'muted';
              const observationLabels = {
                observed: 'Observed in retained OWG activity',
                not_in_retained_owg: 'No matching retained OWG evidence',
                uncertain: 'OWG evidence uncertain',
                not_checked: 'OWG observation not checked',
              };
              observation.textContent = observationLabels[candidate.historically_observed_in_owg] ||
                'OWG evidence uncertain';
              const inspect = document.createElement('button');
              inspect.type = 'button';
              inspect.className = 'secondary';
              inspect.textContent = 'Inspect case context';
              inspect.onclick = async () => {
                if (!confirm('Read current source status again and inspect matching locally retained OWG work? No external messages or changes will be made.')) return;
                inspect.disabled = true;
                try {
                  const url = '/v1/open-work/stripe-preview/case?confirm_scan=true&max_pages=3&work_item_ref=' + encodeURIComponent(candidate.work_item_ref);
                  const context = await api(url);
                  const safe = esc(JSON.stringify(context, null, 2));
                  window.openModal?.('Open Work case evidence', 'Source report and previously observed activity; not an instruction to act',
                    '<div class="muted">Review and copy the context manually for an AI if you choose. OWG has not sent it to any cloud AI.</div>' +
                    '<textarea aria-label="Read-only case context" readonly rows="15" style="width:100%;max-width:100%;box-sizing:border-box">' + safe + '</textarea>');
                } catch (error) {
                  toast(error.message || 'Case evidence unavailable.');
                } finally { inspect.disabled = false; }
              };
              const save = document.createElement('button');
              save.type = 'button';
              save.className = 'secondary';
              save.textContent = 'Save for human review';
              save.onclick = async () => {
                if (!confirm('Recheck this invoice in Stripe, then save a local 7-day intention to review whether follow-up is appropriate? No message or payment will be sent.')) return;
                save.disabled = true;
                try {
                  await api('/v1/open-work/reviews', send('POST', {
                    work_item_ref: candidate.work_item_ref,
                    review_intent: 'assess_follow_up',
                    confirm_scan: true,
                    confirm_save: true,
                  }));
                  if (reviewStatus) reviewStatus.textContent = 'Saved locally for review; source status must be checked again before any action.';
                } catch (_) {
                  if (reviewStatus) reviewStatus.textContent = 'Could not confirm this candidate with the current source. No review was saved.';
                } finally { save.disabled = false; }
              };
              line.append(summary, observation, inspect, save);
              caseArea.append(line);
            }
            if (rows.length > 100) {
              const more = document.createElement('div');
              more.className = 'muted';
              more.textContent = 'Showing the first 100 candidates. Review additional records in your source system.';
              caseArea.append(more);
            }
          }
        }
      } catch (error) {
        if (status) status.textContent = 'Preview unavailable: ' + (error.message || 'source request failed');
      } finally { openWork.disabled = false; }
    };
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
        await api('/v1/history/ai-access', send('POST', on ? {mode: 'off'} : {mode: 'all_saved', expires_minutes: panel.querySelector('#pvHistoryDuration').value === 'standing' ? null : 1440}));
        toast(on ? 'AI apps can no longer read older history.' : panel.querySelector('#pvHistoryDuration').value === 'standing' ? 'AI apps can read older history until you revoke access.' : 'AI apps can read older history for the next 24 hours.');
        window.refreshHistory?.();
      } catch (error) { toast(error.message || 'Could not change history access.'); }
      refreshPrivacy();
    };
    // Changing the duration of an active history grant must persist immediately;
    // otherwise the next privacy refresh silently restores the previous 24h value.
    panel.querySelector('#pvHistoryDuration').onchange = async (event) => {
      const current = privacy.history;
      // When access is off, this is just a preference for the next grant.
      if (!current || current.mode === 'off') return;
      const selected = event.currentTarget.value;
      try {
        const result = await api('/v1/history/ai-access', send('POST', {
          mode: current.mode,
          since: current.since || null,
          until: current.until || null,
          expires_minutes: selected === 'standing' ? null : 1440,
        }));
        privacy.history = result.access;
        toast(selected === 'standing'
          ? 'AI apps can read older history until you revoke access.'
          : 'AI apps can read older history for the next 24 hours.');
        window.refreshHistory?.();
      } catch (error) {
        toast(error.message || 'Could not change history access duration.');
      }
      renderPrivacy();
    };
    panel.querySelector('#pvRedactSwitch').onclick = async () => {
      const redacted = privacy.detail?.detail_level !== 'full';
      if (redacted && !confirm('Show AI apps titles exactly as stored? Names are already tokenized before storage, but anything the detector missed would be visible.')) return;
      try { privacy.detail = await api('/v1/ai-context', send('POST', {detail: redacted ? 'full' : 'redacted'})); }
      catch (error) { toast(error.message || 'Could not save.'); }
      window.refreshAiContextDetail?.();
      renderPrivacy();
    };
    const depth = {state: null};
    const depthBox = id => panel.querySelector('#' + id);
    const depthSignals = {hover_dwell: 'pvDepthHover', drag_drop: 'pvDepthDrag', friction_candidates: 'pvDepthFriction', pointer_trajectory: 'pvDepthTrajectory'};
    const showDepth = state => {
      depth.state = state;
      for (const [key, id] of Object.entries(depthSignals)) depthBox(id).checked = !!state.signals[key];
      panel.querySelectorAll('input[name="pvDepth"]').forEach(r => { r.checked = r.value === state.profile; });
      depthBox('pvDepthDwell').value = state.parameters.hover_dwell_ms;
      depthBox('pvDepthHz').value = state.parameters.trajectory_hz;
      depthBox('pvDepthRetention').value = state.parameters.retention_days;
      depthBox('pvDepthStorage').value = state.parameters.storage_mb;
      depthBox('pvDepthAckText').textContent = state.full_acknowledgement;
      depthBox('pvDepthAck').checked = !!state.full_acknowledged_version;
      depthBox('pvDepthStatus').textContent = 'Current: ' + ({standard: 'Standard', enhanced: 'Enhanced', full: 'Full fidelity'}[state.profile] || 'Custom') + '.' +
        (state.expires_at ? ' Returns to Standard on ' + new Date(state.expires_at).toLocaleString() + '.' : '') +
        (state.expired ? ' The last period of extra detail has ended.' : '');
    };
    const loadDepth = async () => { try { showDepth(await api('/v1/capture-depth')); } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not load interaction detail.'; } };
    panel.querySelectorAll('input[name="pvDepth"]').forEach(radio => radio.addEventListener('change', () => {
      const profile = depth.state?.profiles?.[radio.value];
      if (profile) for (const [key, id] of Object.entries(depthSignals)) depthBox(id).checked = !!profile[key];
    }));
    depthBox('pvDepthSave').onclick = async () => {
      const signals = Object.fromEntries(Object.entries(depthSignals).map(([key, id]) => [key, depthBox(id).checked]));
      const payload = {signals, parameters: {hover_dwell_ms: Number(depthBox('pvDepthDwell').value), trajectory_hz: Number(depthBox('pvDepthHz').value), retention_days: Number(depthBox('pvDepthRetention').value), storage_mb: Number(depthBox('pvDepthStorage').value)}, duration_days: Number(depthBox('pvDepthDuration').value)};
      if (signals.pointer_trajectory) {
        if (!depthBox('pvDepthAck').checked) { depthBox('pvDepthStatus').textContent = 'Tick the acknowledgement to record pointer movement.'; return; }
        payload.acknowledgement_version = depth.state?.full_acknowledgement_version;
      }
      try { showDepth(await api('/v1/capture-depth', send('PUT', payload))); depthBox('pvDepthStatus').textContent += ' Saved; sharing settings are unchanged.'; }
      catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not save interaction detail.'; }
    };
    const depthTable = (rows) => { const t = document.createElement('table'); const b = document.createElement('tbody');
      rows.forEach(([k, v]) => { const tr = document.createElement('tr'); const th = document.createElement('th'); th.textContent = k; const td = document.createElement('td'); td.textContent = v; tr.append(th, td); b.appendChild(tr); });
      t.appendChild(b); return t; };
    depthBox('pvDepthCoverage').onclick = async () => {
      try {
        const m = await api('/v1/capture-coverage');
        depthBox('pvDepthResult').replaceChildren(depthTable([...Object.entries(m.modalities).map(([k, v]) => [k.replace(/_/g, ' '), String(v).replace(/_/g, ' ')]),
          ...Object.entries(m.high_fidelity_event_counts).map(([k, v]) => [k.replace(/^browser_/, '').replace(/_/g, ' ') + ' events (7 days)', String(v)]),
          ...(m.high_fidelity_storage ? [['interaction detail stored', (m.high_fidelity_storage.bytes_estimate / 1048576).toFixed(1) + ' of ' + (m.high_fidelity_storage.limit_bytes / 1048576).toFixed(0) + ' MB (' + m.high_fidelity_storage.events + ' events)' + (m.high_fidelity_storage.last_trim ? '; oldest trimmed ' + m.high_fidelity_storage.last_trim.at : '')]] : [])]));
      } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not load capture coverage.'; }
    };
    depthBox('pvDepthFrictionShow').onclick = async () => {
      try {
        const r = await api('/v1/capture-coverage/friction');
        if (!r.enabled) { depthBox('pvDepthResult').replaceChildren(Object.assign(document.createElement('div'), {className: 'muted', textContent: 'Friction candidates are off.'})); return; }
        const names = {rapid_repeated_activation: 'Repeated clicks', quick_backtrack: 'Went straight back', hover_menu_not_followed: 'Menu opened, nothing chosen', slow_completion: 'Slow to complete'};
        const list = document.createElement('ul');
        r.candidates.slice(0, 50).forEach(c => { const li = document.createElement('li'); li.textContent = (names[c.kind] || c.kind) + ' · ' + c.at + ' · ' + (c.page || '') + (c.seconds ? ' · ' + c.seconds + ' s' : '') + (c.count ? ' · ' + c.count + '×' : ''); list.appendChild(li); });
        depthBox('pvDepthResult').replaceChildren(Object.assign(document.createElement('div'), {className: 'muted', textContent: r.candidates.length + ' candidates in the last ' + r.hours + ' hours. These are observed patterns to review, not judgements about attention, intent or performance.'}), list);
      } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not load friction candidates.'; }
    };
    const depthNote = text => Object.assign(document.createElement('div'), {className: 'muted', textContent: text});
    const drawHeatmap = (r) => {
      const max = Math.max(1, ...r.grid.flat());
      const grid = document.createElement('div');
      grid.setAttribute('role', 'img');
      grid.setAttribute('aria-label', 'Pointer heatmap of ' + r.page.hostname + (r.page.pathname || '') + ': ' + r.samples + ' samples; darker cells had more samples.');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(' + r.grid_size + ',1fr);width:min(100%,360px);aspect-ratio:16/10;border:1px solid currentColor;';
      r.grid.flat().forEach(n => { const cell = document.createElement('div'); cell.style.background = 'rgba(37,99,235,' + (n / max).toFixed(3) + ')'; grid.appendChild(cell); });
      return grid;
    };
    depthBox('pvDepthHeatmap').onclick = async () => {
      try {
        const r = await api('/v1/capture-coverage/heatmap');
        if (!r.pages.length) { depthBox('pvDepthResult').replaceChildren(depthNote('No sampled pointer movement in the last 7 days. It is recorded only at Full fidelity.')); return; }
        const pick = document.createElement('select'); pick.id = 'pvDepthHeatmapPage'; pick.setAttribute('aria-label', 'Page for the pointer heatmap');
        r.pages.forEach(p => pick.appendChild(Object.assign(document.createElement('option'), {value: p.page, textContent: p.page + ' (' + p.samples + ' samples)'})));
        const out = document.createElement('div'); out.id = 'pvDepthHeatmapGrid';
        const show = async () => {
          const chosen = r.pages.find(p => p.page === pick.value);
          const slash = chosen.page.indexOf('/');
          const q = new URLSearchParams({hostname: slash < 0 ? chosen.page : chosen.page.slice(0, slash)});
          if (slash >= 0) q.set('pathname', chosen.page.slice(slash));
          const h = await api('/v1/capture-coverage/heatmap?' + q);
          out.replaceChildren(drawHeatmap(h), depthNote(h.samples + ' samples in ' + h.batches + ' batches. ' + h.interpretation));
        };
        pick.onchange = () => show().catch(error => { depthBox('pvDepthStatus').textContent = error.message || 'Could not load the heatmap.'; });
        depthBox('pvDepthResult').replaceChildren(depthNote('A local review aid: only cell counts are shown, never the recorded points.'), pick, out);
        await show();
      } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not load the heatmap.'; }
    };
    const playbackNames = {hover: 'Rested on', click: 'Clicked', drag_start: 'Started dragging', drop: 'Dropped on', drag_end: 'Drag ended'};
    const drawPlayback = (r) => {
      const ns = 'http://www.w3.org/2000/svg';
      const wrap = document.createElement('div'); wrap.id = 'pvDepthPlaybackView';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('viewBox', '0 0 160 100'); svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Pointer path on ' + r.page.hostname + (r.page.pathname || '') + ' during this visit: ' + r.path.length + ' points and ' + r.items.length + ' interactions. The step list below describes it.');
      svg.style.cssText = 'width:min(100%,420px);aspect-ratio:16/10;border:1px solid currentColor;display:block;';
      const line = document.createElementNS(ns, 'polyline');
      line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'rgb(37,99,235)'); line.setAttribute('stroke-width', '0.8');
      const dot = document.createElementNS(ns, 'circle'); dot.setAttribute('r', '2'); dot.setAttribute('fill', 'rgb(220,38,38)');
      svg.append(line, dot);
      const at = p => (p[1] * 160).toFixed(1) + ',' + (p[2] * 100).toFixed(1);
      const duration = Math.max(1, r.duration_ms || 0);
      const slider = Object.assign(document.createElement('input'), {type: 'range', min: '0', max: String(duration), value: String(duration), id: 'pvDepthPlaybackTime'});
      slider.setAttribute('aria-label', 'Position in the visit, in milliseconds');
      const now = depthNote('');
      now.id = 'pvDepthPlaybackNow'; now.setAttribute('aria-live', 'off');
      const render = (t) => {
        const shown = r.path.filter(p => p[0] <= t);
        line.setAttribute('points', shown.map(at).join(' '));
        const last = shown[shown.length - 1];
        dot.style.display = last ? '' : 'none';
        if (last) { dot.setAttribute('cx', (last[1] * 160).toFixed(1)); dot.setAttribute('cy', (last[2] * 100).toFixed(1)); }
        const done = r.items.filter(i => i.t_ms <= t);
        now.textContent = (t / 1000).toFixed(1) + ' s of ' + (duration / 1000).toFixed(1) + ' s · ' + done.length + ' of ' + r.items.length + ' interactions';
      };
      slider.oninput = () => render(Number(slider.value));
      const play = Object.assign(document.createElement('button'), {type: 'button', className: 'secondary', id: 'pvDepthPlaybackPlay', textContent: 'Play'});
      let timer = null;
      const stop = () => { if (timer) clearInterval(timer); timer = null; play.textContent = 'Play'; };
      play.onclick = () => {
        if (timer) { stop(); return; }
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) { slider.value = String(duration); render(duration); return; }
        let t = Number(slider.value) >= duration ? 0 : Number(slider.value);
        const tick = Math.max(50, Math.round(duration / 200));
        play.textContent = 'Pause';
        timer = setInterval(() => { t = Math.min(duration, t + tick); slider.value = String(t); render(t); if (t >= duration) stop(); }, 50);
      };
      const steps = document.createElement('ol'); steps.id = 'pvDepthPlaybackSteps';
      r.items.forEach(i => {
        const li = document.createElement('li');
        const what = [i.target && i.target.role, i.target && i.target.tag].filter(Boolean)[0] || 'control';
        li.textContent = (i.t_ms / 1000).toFixed(1) + ' s · ' + (playbackNames[i.kind] || i.kind) + ' a ' + what + (i.duration_ms ? ' for ' + (i.duration_ms / 1000).toFixed(1) + ' s' : '');
        steps.appendChild(li);
      });
      render(duration);
      wrap.append(svg, play, slider, now, steps,
        depthNote((r.path_downsampled ? 'The pointer path is thinned for display. ' : '') + 'Timing is approximate. ' + r.interpretation));
      return wrap;
    };
    depthBox('pvDepthPlayback').onclick = async () => {
      try {
        const r = await api('/v1/capture-coverage/heatmap');
        if (!r.pages.length) { depthBox('pvDepthResult').replaceChildren(depthNote('No recorded pointer movement in the last 7 days. Replays need Full fidelity.')); return; }
        const pick = document.createElement('select'); pick.id = 'pvDepthPlaybackPage'; pick.setAttribute('aria-label', 'Page to replay');
        r.pages.forEach(p => pick.appendChild(Object.assign(document.createElement('option'), {value: p.page, textContent: p.page})));
        const visits = document.createElement('select'); visits.id = 'pvDepthPlaybackVisit'; visits.setAttribute('aria-label', 'Visit to replay');
        const out = document.createElement('div');
        const query = (extra) => {
          const chosen = pick.value, slash = chosen.indexOf('/');
          const q = new URLSearchParams({hostname: slash < 0 ? chosen : chosen.slice(0, slash)});
          if (slash >= 0) q.set('pathname', chosen.slice(slash));
          Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v));
          return '/v1/capture-coverage/playback?' + q;
        };
        const show = async (visit) => {
          const p = await api(query(visit === undefined ? {} : {visit: String(visit)}));
          if (visit === undefined) {
            visits.replaceChildren(...p.visits.map(v => Object.assign(document.createElement('option'), {value: String(v.visit), textContent: v.started_at.slice(0, 16).replace('T', ' ') + ' (' + v.items + ' events)'})));
            if (p.visit !== null) visits.value = String(p.visit);
          }
          out.replaceChildren(p.visit === null ? depthNote('No recorded interaction detail on this page.') : drawPlayback(p));
        };
        const fail = error => { depthBox('pvDepthStatus').textContent = error.message || 'Could not load the replay.'; };
        pick.onchange = () => show().catch(fail);
        visits.onchange = () => show(Number(visits.value)).catch(fail);
        depthBox('pvDepthResult').replaceChildren(depthNote('A local replay of one visit: control kinds and the pointer path only, never labels, text or values.'), pick, visits, out);
        await show();
      } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not load the replay.'; }
    };
    depthBox('pvDepthDelete').onclick = async () => {
      if (!window.confirm('Delete all recorded hover, drag and pointer detail from this computer? Clicks, pages and other activity are kept. This cannot be undone.')) return;
      try {
        const r = await api('/v1/capture-depth/delete', send('POST', {confirm: true}));
        depthBox('pvDepthStatus').textContent = 'Deleted ' + r.deleted_events + ' interaction detail events. Other activity is kept.';
        depthBox('pvDepthResult').replaceChildren();
      } catch (error) { depthBox('pvDepthStatus').textContent = error.message || 'Could not delete interaction detail.'; }
    };
    loadDepth();
    // All recovery controls are read-only. No dashboard control can execute a restore
    // or transmit a recovery passphrase to the preflight endpoint.
    const backupChoice = panel.querySelector('#pvBackupChoice');
    const backupRefresh = panel.querySelector('#pvBackupRefresh');
    const backupInspect = panel.querySelector('#pvBackupInspect');
    const backupPlan = panel.querySelector('#pvBackupPlan');
    const healthRun = panel.querySelector('#pvBackupHealthRun');
    const healthStatus = panel.querySelector('#pvBackupHealthStatus');
    const healthRows = panel.querySelector('#pvBackupHealthRows');
    let listedBackups = [], backupEpoch = 0, healthEpoch = 0;
    async function refreshBackupList(preferred = '') {
      const epoch = ++backupEpoch;
      healthEpoch++;
      healthRows.replaceChildren();
      healthRun.disabled = true;
      healthStatus.textContent = 'Backup listing refreshed; previous integrity results no longer apply.';
      backupRefresh.disabled = true;
      backupInspect.disabled = true;
      backupPlan.textContent = 'Reading saved backups on this computer…';
      try {
        const result = await api('/v1/local-backups');
        if (epoch !== backupEpoch) return;
        listedBackups = Array.isArray(result.backups) ? result.backups.filter(item =>
          item && typeof item.name === 'string' && item.name.startsWith('owg-backup-')) : [];
        const previous = preferred || backupChoice.value;
        backupChoice.replaceChildren();
        const empty = document.createElement('option');
        empty.value = ''; empty.textContent = listedBackups.length ? 'Choose a saved backup' : 'No saved backups';
        backupChoice.appendChild(empty);
        for (const item of listedBackups) {
          const option = document.createElement('option');
          option.value = item.name;
          option.textContent = item.name + (item.encrypted ? ' (encrypted — offline CLI)' : '');
          backupChoice.appendChild(option);
        }
        if (listedBackups.some(item => item.name === previous)) backupChoice.value = previous;
        backupInspect.disabled = !backupChoice.value;
        healthRun.disabled = listedBackups.length === 0;
        backupPlan.textContent = listedBackups.length
          ? 'Choose a backup and inspect its integrity and restore blockers. No data will be changed.'
          : 'No backups found in the configured local backup folder.';
      } catch (error) {
        if (epoch !== backupEpoch) return;
        listedBackups = [];
        backupChoice.replaceChildren();
        backupInspect.disabled = true;
        backupPlan.textContent = error.message || 'Could not list local backups.';
      } finally {
        if (epoch === backupEpoch) backupRefresh.disabled = false;
      }
    }
    backupRefresh.onclick = () => refreshBackupList();
    backupChoice.onchange = () => {
      backupEpoch++;
      backupRefresh.disabled = false;
      backupInspect.disabled = !backupChoice.value;
      backupPlan.textContent = 'Selection changed. Inspect the chosen backup for current results.';
    };
    backupInspect.onclick = async () => {
      const name = backupChoice.value;
      const selected = listedBackups.find(item => item.name === name);
      if (!selected) { backupPlan.textContent = 'Choose a saved backup first.'; return; }
      if (selected.encrypted) {
        backupPlan.textContent = 'Encrypted backup: close OpenWorkGraph and use python -m server.local_backup plan with --passphrase-env. No passphrase is sent from this screen.';
        return;
      }
      const epoch = ++backupEpoch;
      backupInspect.disabled = true;
      backupPlan.textContent = 'Verifying backup contents and checking whether offline restore is safe…';
      try {
        const plan = await api('/v1/local-backups/preflight', send('POST', {name}));
        if (epoch !== backupEpoch || name !== backupChoice.value) return;
        const files = Number(plan.file_count) || 0;
        const bytes = Number(plan.total_bytes) || 0;
        const outcome = plan.ready
          ? 'The archive passes integrity checks and has no current preflight blockers.'
          : 'Offline restore is blocked: ' + (Array.isArray(plan.blockers) ? plan.blockers.join('; ') : 'unknown reason');
        backupPlan.textContent = `Verified ${files} archived files (${bytes.toLocaleString()} bytes), OWG version ${String(plan.owg_version || 'unknown')}. ${outcome} No files were changed. To restore, first close OpenWorkGraph, then use python -m server.local_backup restore with the archive path and --replace when required; run the CLI preflight again while closed.`;
      } catch (error) {
        if (epoch === backupEpoch && name === backupChoice.value) {
          backupPlan.textContent = (error.message || 'Backup verification failed.') + ' No restore was attempted.';
        }
      } finally {
        if (epoch === backupEpoch) backupInspect.disabled = !backupChoice.value;
      }
    };
    healthRun.onclick = async () => {
      const chosen = listedBackups.slice(0, 5).map(item => item.name);
      if (!chosen.length) { healthStatus.textContent = 'No saved local backups to verify.'; return; }
      if (!window.confirm('Read and verify up to five already-listed local backups now? No archive will be modified, restored or uploaded. Large archives may require the offline CLI.')) return;
      const epoch = ++healthEpoch;
      healthRun.disabled = true;
      healthRows.replaceChildren();
      healthStatus.textContent = 'Verifying local archive contents (read only)…';
      try {
        const result = await api('/v1/local-backups/health', send('POST', {
          names: chosen, confirm_local_verification: true
        }));
        if (epoch !== healthEpoch) return;
        const labels = {
          verified: 'Verified intact at this check',
          integrity_check_failed: 'Integrity check failed; do not rely on this archive',
          unavailable_or_not_regular: 'Unavailable or not a regular file',
          encrypted_requires_offline_cli: 'Encrypted; verify using offline CLI',
          exceeds_dashboard_archive_limit: 'Too large for dashboard check; use offline CLI',
          exceeds_dashboard_uncompressed_limit: 'Uncompressed data exceeds the dashboard limit; use offline CLI',
          exceeds_dashboard_request_budget: 'Check budget reached; use offline CLI',
          requires_offline_inspection: 'Requires offline inspection',
          changed_during_check: 'File changed during check; verify again offline'
        };
        for (const item of result.items || []) {
          const row = document.createElement('div');
          row.className = 'muted';
          // A saved filename is only ever inserted as text, never as markup.
          row.textContent = item.name + ' — ' +
            (labels[item.status] || 'Not verified') +
            (item.verified ? ' · ' + String(item.file_count) + ' stored files, OWG ' + String(item.owg_version || 'unknown') : '');
          healthRows.appendChild(row);
        }
        healthStatus.textContent = String(result.verified) + ' of ' +
          String(result.requested) + ' listed backup archives verified at ' +
          String(result.checked_at) + '. ' + String(result.not_verified) +
          ' not verified. ' + (listedBackups.length > chosen.length
            ? String(listedBackups.length - chosen.length) + ' additional listed backups were not inspected. ' : '') +
          'No restore or write occurred. A passing checksum does not prove recoverability.';
      } catch (error) {
        if (epoch === healthEpoch) {
          healthRows.replaceChildren();
          healthStatus.textContent = (error.message || 'Could not check archive integrity.') +
            ' No restore was attempted.';
        }
      } finally {
        if (epoch === healthEpoch) healthRun.disabled = listedBackups.length === 0;
      }
    };
    panel.querySelector('#pvBackupCreate').onclick = async () => {
      const status = panel.querySelector('#pvBackupStatus');
      const pass = panel.querySelector('#pvBackupPass');
      status.textContent = 'Backing up…';
      try {
        const result = await api('/v1/local-backups', send('POST', pass.value ? {passphrase: pass.value} : {}));
        status.textContent = `Saved ${result.file_count} files to ${result.archive}${result.encrypted ? ' (encrypted, includes keys)' : ' (keys not included)'}.`;
        pass.value = '';
        await refreshBackupList();
      } catch (error) { status.textContent = error.message || 'Backup failed.'; }
      finally { pass.value = ''; }
    };
    refreshBackupList();
    panel.querySelector('#pvScreenCaptureSwitch').onclick = async () => {
      const enabled = privacy.screen?.mode === 'full';
      if (!enabled && !confirm('Turn on full screen capture? Every monitor is saved at full resolution on each window switch, click, scroll and interval, including anything personal or confidential that is visible. Images stay on this computer.')) return;
      try {
        privacy.screen = await api('/v1/screen-capture', send('PUT', enabled ? {mode: 'off'}
          : {mode: 'full', acknowledge: privacy.screen?.acknowledgement_version || 'full-screen-capture-v1'}));
        toast(enabled ? 'Full screen capture turned off.' : 'Full screen capture on for new activity.');
      } catch (error) { toast(error.message || 'Could not change screen capture.'); }
      renderPrivacy();
    };
    panel.querySelector('#pvScreenOcrSwitch').onclick = async () => {
      if (privacy.screen?.mode !== 'full') return;
      try {
        privacy.screen = await api('/v1/screen-capture', send('PUT', {ocr_enabled: !privacy.screen.ocr_enabled}));
        toast(privacy.screen.ocr_enabled ? 'Text in captures will be recognized on this computer.' : 'Screen text recognition off.');
      } catch (error) { toast(error.message || 'Could not change screen text recognition.'); }
      renderPrivacy();
    };
    const scheduleList = panel.querySelector('#pvScheduleList');
    const scheduleStatus = panel.querySelector('#pvScheduleStatus');
    const quietEnabled = panel.querySelector('#pvScheduleQuietEnabled');
    const quietFields = panel.querySelector('#pvScheduleQuietFields');
    const quietStart = panel.querySelector('#pvScheduleQuietStart');
    const quietEnd = panel.querySelector('#pvScheduleQuietEnd');
    const quietTimezone = panel.querySelector('#pvScheduleQuietTimezone');
    quietTimezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    quietEnabled.onchange = () => {
      quietFields.hidden = !quietEnabled.checked;
      // A flex author rule can override the UA [hidden] rule; keep the
      // optional inputs truly absent from visual and keyboard layouts.
      quietFields.style.display = quietEnabled.checked ? 'flex' : 'none';
    };
    quietEnabled.onchange();
    const readQuietWindow = () => {
      const windowSpec = {timezone: quietTimezone.value.trim(), start: quietStart.value, end: quietEnd.value};
      if (!windowSpec.timezone || !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(windowSpec.start) ||
          !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(windowSpec.end) ||
          windowSpec.start === windowSpec.end)
        throw new Error('Enter a timezone and two different valid 24-hour quiet-hour times.');
      return windowSpec;
    };
    const refreshSchedules = async () => {
      const state = await api('/v1/schedules').catch(() => null);
      if (!scheduleList || !state) return;
      scheduleList.replaceChildren();
      for (const item of state.schedules || []) {
        const line = document.createElement('div');
        line.className = 'pv-row pv-sub';
        const text = document.createElement('span');
        text.textContent = (item.kind === 'local_backup' ? 'Unencrypted local backup'
          : (item.kind === 'retained_jira_profile' ? 'Jira profile ' : 'Signals for ') + item.target_id) +
          ' · every ' + item.interval_minutes + ' min · ' + (item.enabled ? 'next ' + String(item.next_run_at || '').slice(0, 16).replace('T', ' ')
          : (item.paused_reason ? 'paused after repeated failures' : 'paused')) +
          (item.quiet_hours ? ' · quiet ' + item.quiet_hours.start + '–' + item.quiet_hours.end +
            ' ' + item.quiet_hours.timezone + (item.quiet_hours_active ? ' (now)' : '') : '') +
          (item.last_deferred_until ? ' · last quiet deferral until ' + String(item.last_deferred_until).slice(0, 16).replace('T',' ') + ' UTC' : '') +
          (item.last_result ? ' · last: ' + item.last_result : '');
        const toggle = document.createElement('button');
        toggle.type = 'button'; toggle.className = 'secondary'; toggle.textContent = item.enabled ? 'Pause' : 'Resume';
        toggle.onclick = async () => { await api('/v1/schedules/update', send('POST', {schedule_id: item.schedule_id, enabled: !item.enabled})).catch(() => null); refreshSchedules(); };
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'secondary'; remove.textContent = 'Remove';
        remove.onclick = async () => { await api('/v1/schedules/delete', send('POST', {schedule_id: item.schedule_id})).catch(() => null); refreshSchedules(); };
        const setQuiet = document.createElement('button');
        setQuiet.type = 'button'; setQuiet.className = 'secondary';
        setQuiet.textContent = item.quiet_hours ? 'Update quiet hours' : 'Apply quiet hours';
        setQuiet.setAttribute('aria-label', setQuiet.textContent + ' for ' + item.kind + ' ' + item.target_id);
        setQuiet.onclick = async () => {
          if (!quietEnabled.checked) {
            if (scheduleStatus) scheduleStatus.textContent = 'Tick the optional quiet-hours checkbox and choose the window first.';
            return;
          }
          try {
            await api('/v1/schedules/update', send('POST', {schedule_id: item.schedule_id,
              quiet_hours: readQuietWindow()}));
            if (scheduleStatus) scheduleStatus.textContent = 'Quiet hours saved for this job; missed runs are not backfilled.';
          } catch (error) {
            if (scheduleStatus) scheduleStatus.textContent = error.message || 'Could not save quiet hours.';
          }
          refreshSchedules();
        };
        line.append(text, toggle, remove, setQuiet);
        if (item.quiet_hours) {
          const clear = document.createElement('button');
          clear.type = 'button'; clear.className = 'secondary'; clear.textContent = 'Clear quiet hours';
          clear.setAttribute('aria-label', 'Clear quiet hours for ' + item.kind + ' ' + item.target_id);
          clear.onclick = async () => {
            try {
              await api('/v1/schedules/update', send('POST', {schedule_id: item.schedule_id,
                quiet_hours: null}));
              if (scheduleStatus) scheduleStatus.textContent = 'Quiet hours cleared for this job.';
            } catch (error) { if (scheduleStatus) scheduleStatus.textContent = error.message || 'Could not clear quiet hours.'; }
            refreshSchedules();
          };
          line.append(clear);
        }
        scheduleList.append(line);
      }
    };
    const scheduleKind = panel.querySelector('#pvScheduleKind');
    const scheduleTarget = panel.querySelector('#pvScheduleTarget');
    const scheduleInterval = panel.querySelector('#pvScheduleInterval');
    const scheduleBackupAck = panel.querySelector('#pvScheduleBackupAck');
    const scheduleBackupAckRow = panel.querySelector('#pvScheduleBackupAckRow');
    const refreshScheduleKind = () => {
      const local = scheduleKind.value === 'local_backup';
      scheduleTarget.disabled = local;
      scheduleTarget.value = local ? 'local' : '';
      scheduleTarget.placeholder = local ? 'This computer' : 'Profile or event log ID';
      scheduleInterval.min = local ? '1440' : '15';
      scheduleInterval.value = local ? '1440' : '60';
      scheduleBackupAckRow.hidden = !local;
      scheduleBackupAck.checked = false;
    };
    scheduleKind.onchange = refreshScheduleKind;
    refreshScheduleKind();
    panel.querySelector('#pvScheduleAdd').onclick = async () => {
      const kind = scheduleKind.value;
      const isBackup = kind === 'local_backup';
      if (isBackup && !scheduleBackupAck.checked) {
        if (scheduleStatus) scheduleStatus.textContent = 'Acknowledge that scheduled backups are unencrypted and may contain private work activity.';
        return;
      }
      if (!confirm(isBackup
          ? 'Schedule an unencrypted local backup every day or longer? Copies stay on this computer and can contain private captured work. This does not include credentials or send data to cloud services.'
          : 'Run this job automatically on a schedule? You can pause or remove it at any time.')) return;
      try {
        await api('/v1/schedules', send('POST', {
          kind, target_id: isBackup ? 'local' : scheduleTarget.value.trim(),
          interval_minutes: Number(scheduleInterval.value), confirm_scheduled_runs: true,
          confirm_unencrypted_local_backup: isBackup && scheduleBackupAck.checked,
          quiet_hours: quietEnabled.checked ? readQuietWindow() : null
        }));
        if (scheduleStatus) scheduleStatus.textContent = 'Schedule added.';
        scheduleBackupAck.checked = false;
      } catch (error) { if (scheduleStatus) scheduleStatus.textContent = error.message || 'Could not add the schedule.'; }
      refreshSchedules();
    };
    refreshSchedules();
    const semanticStatus = panel.querySelector('#pvSemanticStatus');
    const showSemantic = state => { if (semanticStatus && state) semanticStatus.textContent = state.enabled ? 'On: ' + state.model + ' at ' + state.base_url : 'Off.'; };
    api('/v1/semantic-linker').then(showSemantic).catch(() => null);
    panel.querySelector('#pvSemanticSave').onclick = async () => {
      if (!confirm('Send app names and short screen labels of linked work to this model endpoint when grouping suggestions are requested?')) return;
      const value = id => panel.querySelector(id).value.trim();
      try {
        showSemantic(await api('/v1/semantic-linker', send('PUT', {enabled: true, acknowledge: 'semantic-linker-v1',
          base_url: value('#pvSemanticUrl'), model: value('#pvSemanticModel'), api_key_env: value('#pvSemanticKeyEnv')})));
      } catch (error) { if (semanticStatus) semanticStatus.textContent = error.message || 'Could not save.'; }
    };
    panel.querySelector('#pvSemanticOff').onclick = async () => {
      try { showSemantic(await api('/v1/semantic-linker', send('PUT', {enabled: false}))); }
      catch (error) { if (semanticStatus) semanticStatus.textContent = error.message || 'Could not save.'; }
    };
    const oauthList = panel.querySelector('#pvOAuthList');
    const oauthStatus = panel.querySelector('#pvOAuthStatus');
    const refreshOAuth = async () => {
      const state = await api('/v1/connector-oauth').catch(() => null);
      if (!oauthList || !state) return;
      oauthList.replaceChildren();
      for (const conn of state.connections || []) {
        const line = document.createElement('div');
        line.className = 'pv-row pv-sub';
        const text = document.createElement('span');
        text.textContent = conn.label + (conn.host ? ' · ' + conn.host : '') + ' · connected ' + String(conn.connected_at || '').slice(0, 10)
          + (conn.reauthorization_required ? ' · Reconnect required: the last token refresh could not be confirmed.' : '');
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'secondary'; remove.textContent = 'Disconnect';
        remove.onclick = async () => {
          if (!confirm('Disconnect ' + conn.label + '? OWG revokes the sign-in where the provider allows it and forgets it here.')) return;
          try {
            const result = await api('/v1/connector-oauth/disconnect', send('POST', {connection_id: conn.connection_id}));
            if (oauthStatus) oauthStatus.textContent = 'Disconnected (' + result.provider_revocation.replaceAll('_', ' ') + ').';
          } catch (error) { if (oauthStatus) oauthStatus.textContent = error.message || 'Could not disconnect.'; }
          refreshOAuth();
        };
        line.append(text, remove);
        oauthList.append(line);
      }
    };
    panel.querySelector('#pvOAuthConnect').onclick = async () => {
      const value = id => panel.querySelector(id).value.trim();
      try {
        const result = await api('/v1/connector-oauth/begin', send('POST', {provider: value('#pvOAuthProvider'),
          host: value('#pvOAuthHost') || null, client_id: value('#pvOAuthClient'), client_secret: value('#pvOAuthSecret') || null}));
        panel.querySelector('#pvOAuthSecret').value = '';
        window.open(result.authorization_url, '_blank', 'noopener');
        if (oauthStatus) oauthStatus.textContent = 'Approve access in the new tab, then return here. Register ' + result.redirect_uri + ' as the redirect URI of your app.';
        setTimeout(refreshOAuth, 15000);
      } catch (error) { if (oauthStatus) oauthStatus.textContent = error.message || 'Could not start sign-in.'; }
    };
    refreshOAuth();
    panel.querySelector('#pvWorkTextSwitch').onclick = async () => {
      const enabled = !!privacy.workText?.capture_enabled;
      if (!enabled && !confirm('Enable automatic browser work-text capture? Email/page text and drafts may contain sensitive information that filters cannot always detect. Content stays local unless you separately opt into cloud synchronization below. Local retention defaults to 7 days. Turning capture off deletes saved text.')) return;
      if (enabled && !confirm('Turn off work-text capture and delete all previously saved browser work text?')) return;
      try {
        privacy.workText = await api('/v1/work-text/policy', send('POST', {capture_enabled: !enabled}));
        toast(enabled ? 'Work-text capture disabled; saved text deleted.' : 'Local work-text capture enabled.');
      } catch (error) { toast(error.message || 'Could not change work-text capture.'); }
      renderPrivacy();
    };
    panel.querySelector('#pvWorkTextAiSwitch').onclick = async () => {
      if (!privacy.workText?.capture_enabled) return;
      const enabled = !!privacy.workText.ai_read_enabled;
      if (!enabled && !confirm('Allow connected local AI apps to read saved redacted browser page and draft text? A separate cloud permission is required for Gateway-assisted retrieval.')) return;
      try {
        privacy.workText = await api('/v1/work-text/policy', send('POST', {ai_read_enabled: !enabled}));
        toast(enabled ? 'Local AI text reads disabled.' : 'Local AI text reads enabled.');
      } catch (error) { toast(error.message || 'Could not change AI text access.'); }
      renderPrivacy();
    };
    panel.querySelector('#pvWorkTextCloudSwitch').onclick = async () => {
      if (!privacy.workText?.capture_enabled || !privacy.workText?.ai_read_enabled) return;
      const enabled = !!privacy.workText.cloud_read_enabled;
      if (!enabled && !panel.querySelector('#pvCloudAck')?.checked) {
        toast('Please confirm your authorization and cloud-sharing acknowledgement first.');
        return;
      }
      if (!enabled && !confirm('Enable cloud synchronization of new, redacted browser work text to your personal OWG account? Data remains available to connected AI for up to 7 days even when this computer is off. Your content may include third-party personal data. Changes cannot recall content already shown in an AI chat.')) return;
      try {
        privacy.workText = await api('/v1/work-text/policy', send('POST', {cloud_read_enabled: !enabled, ...(!enabled ? {cloud_ack_version: 'cloud-work-text-v1'} : {})}));
        toast(enabled ? 'Cloud uploads stopped. Deletion of already shared content will be requested when this computer connects to Gateway.' : 'Cloud sharing permission saved. Upload starts only when this device is linked and cloud sharing is available.');
      } catch (error) { toast(error.message || 'Could not change cloud text access.'); }
      if (enabled && panel.querySelector('#pvCloudAck')) panel.querySelector('#pvCloudAck').checked = false;
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
        toast(`${Number(result.deleted_events || 0)} activity events and ${Number(result.deleted_work_text_snapshots || 0)} browser text snapshots deleted.`);
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
      const [capture, policy, access, history, detail, browser, lists, workText, screen] = await Promise.all([
        api('/v1/capture/status').catch(() => null), api('/v1/history-policy').catch(() => null), api('/v1/ai-access').catch(() => null),
        api('/v1/history/ai-access').catch(() => null), api('/v1/ai-context').catch(() => null),
        api('/v1/browser-signal-settings').catch(() => null), api('/v1/capture-exclusions').catch(() => null),
        api('/v1/work-text/policy').catch(() => null), api('/v1/screen-capture').catch(() => null),
      ]);
      Object.assign(privacy, {capture, policy, access, history: history?.access || null, detail, browser, lists, workText, screen});
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
    } else if (capture.missing_permissions?.length) {
      title.textContent = 'Recorder needs permission';
      sub.textContent = 'OpenWorkGraph is waiting for the required OS permissions. Saved work remains available.';
      dot.className = 'hero-dot paused';
    } else if (capture.away) {
      title.textContent = 'Recorder is away';
      sub.textContent = 'The computer is idle or locked. Saved work remains available.';
      dot.className = 'hero-dot paused';
    } else if (capture.collector_alive === false) {
      title.textContent = 'Waiting for the recorder';
      sub.textContent = 'No collector heartbeat has arrived for this run. Saved work remains available.';
      dot.className = 'hero-dot paused';
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
      const [status, managed] = await Promise.all([
        api('/v1/update-status'),
        api('/v1/managed-status').catch(() => null),
      ]);
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
      if (managed?.managed) {
        chip.textContent = `Update v${status.latest_version} · managed by IT`;
        chip.title = 'This installation is organization-managed. IT should deploy the approved update through endpoint management.';
        chip.onclick = null;
        chip.disabled = true;
      } else {
        chip.disabled = false;
        chip.textContent = `Download update v${status.latest_version}`;
        chip.title = 'Download the current platform installer directly. The version check sends no work evidence.';
        chip.onclick = () => window.open(status.installer_url || status.release_url, '_blank', 'noopener,noreferrer');
      }
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
      // A computer that was revoked or explicitly disconnected should still
      // have a visible Organization tab in Basic so an employee with a new
      // IT-issued invitation can find the safe rejoin flow.
      facts.orgConnected = !!gateway?.enrolled || !!gateway?.managed ||
        String(gateway?.mode || '').startsWith('connected') ||
        !!gateway?.device_revoked ||
        (gateway?.mode === 'local_only' && !!gateway?.gateway_url);
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
