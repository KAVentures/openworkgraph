/* Local-only fast path for the dashboard's Never record browser host/title lists.
 * The server remains authoritative at ingest; this prevents excluded activity
 * from being assembled or placed into the extension's offline evidence queue.
 */
(() => {
  const ext = globalThis.browser ?? globalThis.chrome;
  const KEY = "openworkgraph_work_text_exclusions_v1";
  const LOCAL_KEY = "openworkgraph_browser_site_exclusions_v1";
  const DEFAULT_TITLES = ["password", "private", "incognito", "bank"];
  let current = {hosts: [], titles: DEFAULT_TITLES};
  let localHosts = [];
  let policyLoaded = false;
  let localPolicyLoaded = false;

  function normalize(value) {
    const v = value && typeof value === "object" ? value : {};
    const bounded = list => Array.isArray(list) ? list.slice(0, 100)
      .map(x => String(x || "").toLowerCase().trim().slice(0, 120)).filter(Boolean) : [];
    return {hosts: bounded(v.hosts), titles: bounded(v.titles)};
  }

  function setPolicy(value) { current = normalize(value); policyLoaded = true; }
  function setLocalPolicy(value) {
    localHosts = normalize(value).hosts;
    localPolicyLoaded = true;
  }
  function isExcluded(hostname, title) {
    // Fail closed until BOTH independent policy sources are loaded. The local
    // OWG server periodically refreshes KEY; it must never clear popup hosts.
    if (!policyLoaded || !localPolicyLoaded) return true;
    const host = String(hostname || "").toLowerCase().trim().replace(/\.$/, "");
    const pageTitle = String(title || "").toLowerCase();
    return [...current.hosts, ...localHosts].some(raw => {
      const pattern = raw.startsWith("*.") ? raw.slice(2) : raw;
      return pattern && (host === pattern || host.endsWith("." + pattern));
    }) || current.titles.some(pattern => pattern && pageTitle.includes(pattern));
  }

  // The first event must not bypass a saved exclusion while storage is loading.
  const serverReady = ext.storage.local.get(KEY)
    .then(data => {
      if (data && Object.hasOwn(data, KEY)) setPolicy(data[KEY]);
      else policyLoaded = true; // New install: use the default exclusions.
    })
    .catch(() => { policyLoaded = false; });
  const localReady = ext.storage.local.get(LOCAL_KEY)
    .then(data => {
      if (data && Object.hasOwn(data, LOCAL_KEY)) setLocalPolicy(data[LOCAL_KEY]);
      else localPolicyLoaded = true; // New install: no popup-specific hosts.
    })
    .catch(() => { localPolicyLoaded = false; });
  const ready = Promise.all([serverReady, localReady]);
  ext.storage.onChanged?.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes?.[KEY]) setPolicy(changes[KEY].newValue);
    if (changes?.[LOCAL_KEY]) setLocalPolicy(changes[LOCAL_KEY].newValue);
  });
  globalThis.OWGBrowserExclusions = Object.freeze({ready, setPolicy, isExcluded});
})();
