/* Local-only fast path for the dashboard's Never record browser host/title lists.
 * The server remains authoritative at ingest; this prevents excluded activity
 * from being assembled or placed into the extension's offline evidence queue.
 */
(() => {
  const ext = globalThis.browser ?? globalThis.chrome;
  const KEY = "openworkgraph_work_text_exclusions_v1";
  const DEFAULT_TITLES = ["password", "private", "incognito", "bank"];
  let current = {hosts: [], titles: DEFAULT_TITLES};
  let policyLoaded = false;

  function normalize(value) {
    const v = value && typeof value === "object" ? value : {};
    const bounded = list => Array.isArray(list) ? list.slice(0, 100)
      .map(x => String(x || "").toLowerCase().trim().slice(0, 120)).filter(Boolean) : [];
    return {hosts: bounded(v.hosts), titles: bounded(v.titles)};
  }

  function setPolicy(value) { current = normalize(value); policyLoaded = true; }
  function isExcluded(hostname, title) {
    // Fail closed during initialization or a storage read failure.
    if (!policyLoaded) return true;
    const host = String(hostname || "").toLowerCase().trim().replace(/\.$/, "");
    const pageTitle = String(title || "").toLowerCase();
    return current.hosts.some(raw => {
      const pattern = raw.startsWith("*.") ? raw.slice(2) : raw;
      return pattern && (host === pattern || host.endsWith("." + pattern));
    }) || current.titles.some(pattern => pattern && pageTitle.includes(pattern));
  }

  // The first event must not bypass a saved exclusion while storage is loading.
  const ready = ext.storage.local.get(KEY)
    .then(data => {
      if (data && Object.hasOwn(data, KEY)) setPolicy(data[KEY]);
      else policyLoaded = true; // New install: use the default exclusions.
    })
    .catch(() => { policyLoaded = false; });
  ext.storage.onChanged?.addListener((changes, area) => {
    if (area === "local" && changes?.[KEY]) setPolicy(changes[KEY].newValue);
  });
  globalThis.OWGBrowserExclusions = Object.freeze({ready, setPolicy, isExcluded});
})();
