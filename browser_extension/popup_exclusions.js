/* User-controlled local site exclusions for structural and opt-in browser capture.
 * Standalone popup module: no server call, no pairing token or recorded URL path.
 * Uses the same storage key as the local background/content exclusion matcher.
 */
(() => {
  'use strict';
  const ext = globalThis.browser ?? globalThis.chrome;
  // Separate from server-refreshed OWG policies: a local Never record must not disappear.
  const KEY = 'openworkgraph_browser_site_exclusions_v1';
  const MAX_HOSTS = 100;
  let currentHost = '';
  let revision = 0;
  let writing = false;

  function hostFromUrl(input) {
    try {
      const url = new URL(String(input || ''));
      if (!['http:', 'https:'].includes(url.protocol)) return '';
      const host = url.hostname.toLowerCase().replace(/\.$/, '');
      // Match the shared capture matcher bound: truncated domains could silently
      // leave a site recording, so reject rather than shorten them.
      return host && host.length <= 120 ? host : '';
    } catch (_) { return ''; }
  }
  function covers(host, entry) {
    const raw = String(entry || '').toLowerCase().trim().replace(/\.$/, '');
    const pattern = raw.startsWith('*.') ? raw.slice(2) : raw;
    return !!pattern && (host === pattern || host.endsWith('.' + pattern));
  }
  function normalizedPolicy(value) {
    const v = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    // This key owns only browser-local hosts. Server-owned title rules live
    // separately and remain authoritative regardless of popup edits.
    const hosts = Array.isArray(v.hosts) ? v.hosts.filter(x => typeof x === 'string').slice(0, MAX_HOSTS) : [];
    return {...v, hosts};
  }
  async function readPolicy() {
    const value = await ext.storage.local.get(KEY);
    return normalizedPolicy(value && Object.hasOwn(value, KEY) ? value[KEY] : null);
  }
  async function activeHost() {
    const tabs = await ext.tabs.query({active:true,lastFocusedWindow:true});
    return hostFromUrl(Array.isArray(tabs) ? tabs[0]?.url : '');
  }
  function status(message, error = false) {
    const el = document.querySelector('#never-record-status');
    if (!el) return;
    el.textContent = message;
    el.className = error ? 'bad' : 'muted';
  }
  function renderHosts(hosts, host) {
    const list = document.querySelector('#never-record-sites');
    list.replaceChildren();
    for (const entry of hosts) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = entry;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'secondary';
      remove.textContent = 'Remove';
      remove.setAttribute('aria-label', 'Remove Never record exclusion for ' + entry);
      remove.disabled = writing;
      remove.addEventListener('click', () => changeSite(entry, false));
      li.append(name, remove);
      list.appendChild(li);
    }
    if (!hosts.length) {
      const empty = document.createElement('li');
      empty.textContent = 'No excluded websites added here.';
      list.appendChild(empty);
    }
    const add = document.querySelector('#never-record-current');
    add.disabled = writing || !host || hosts.some(entry => covers(host, entry)) || hosts.length >= MAX_HOSTS;
    const site = document.querySelector('#never-record-current-site');
    site.textContent = host ? 'Active website: ' + host : 'Open an http(s) website to exclude it.';
    if (!host) status('Only ordinary http(s) websites can be added.');
    else if (hosts.some(entry => covers(host, entry))) status('This site is already excluded from future browser capture.');
    else if (hosts.length >= MAX_HOSTS) status('Site exclusion limit reached (100).', true);
    else status('The list is stored in this browser only.');
  }
  async function refresh() {
    const ticket = ++revision;
    try {
      const [policy, host] = await Promise.all([readPolicy(), activeHost()]);
      if (ticket !== revision) return;
      currentHost = host;
      renderHosts(policy.hosts, host);
    } catch (_) {
      if (ticket !== revision) return;
      document.querySelector('#never-record-current').disabled = true;
      document.querySelector('#never-record-sites').replaceChildren();
      status('Could not read local exclusion settings. Recording is blocked until the policy is available.', true);
    }
  }
  async function changeSite(entry, add) {
    if (writing) return;
    writing = true;
    let failure = '';
    document.querySelector('#never-record-current').disabled = true;
    try {
      const policy = await readPolicy();
      const host = add ? await activeHost() : entry;
      if (!host) throw new Error('No ordinary active website was found.');
      // Never silently exclude another tab after focus changes while the
      // popup is open. Require the exact website the user clicked to match.
      if (add && host !== entry) throw new Error('The active website changed. Review it and try again.');
      const oldHosts = policy.hosts;
      const hosts = add
        ? (oldHosts.some(item => covers(host, item)) ? oldHosts : (
          oldHosts.length >= MAX_HOSTS ? null : [...oldHosts, host]))
        : oldHosts.filter(item => item !== host);
      if (hosts === null) throw new Error('Site exclusion limit reached.');
      // Write only if there is an actual change; never change OWG's server policy.
      if (hosts.length !== oldHosts.length) {
        await ext.storage.local.set({[KEY]: {...policy, hosts}});
      }
    } catch (error) {
      failure = String(error?.message || 'Failed to update local site exclusions.');
    } finally {
      writing = false;
      await refresh();
      // A failed write must remain visible: a routine refresh must not imply
      // that Never record was saved successfully when storage refused it.
      if (failure) status(failure, true);
    }
  }
  function init() {
    const add = document.querySelector('#never-record-current');
    if (!add) return;
    add.addEventListener('click', () => changeSite(currentHost, true));
    ext.storage.onChanged?.addListener((changes, area) => {
      if (area === 'local' && changes?.[KEY] && !writing) refresh();
    });
    refresh();
  }
  globalThis.__OWG_NEVER_RECORD_POPUP_TESTS__ = Object.freeze({hostFromUrl, covers, normalizedPolicy});
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
    else init();
  }
})();
