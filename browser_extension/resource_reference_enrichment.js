(() => {
  "use strict";
  const ext = globalThis.browser ?? globalThis.chrome;
  const recent = new Map();
  let cachedPolicy = null;
  let cachedPolicyAt = 0;

  const SENSOR_NAMESPACE = "openworkgraph-resource-reference-sensor-v1:";

  async function loadPolicy({force = false} = {}) {
    if (!force && cachedPolicy && Date.now() - cachedPolicyAt < 5000) return cachedPolicy;
    let data = null;
    try {
      if (typeof getJson === "function") data = await getJson("/v1/browser-context");
    } catch (_) {}
    cachedPolicy = {
      settings: data?.signal_settings || {},
      excluded_browser_host_patterns: Array.isArray(data?.excluded_browser_host_patterns) ? data.excluded_browser_host_patterns : [],
      excluded_title_patterns: Array.isArray(data?.excluded_title_patterns) ? data.excluded_title_patterns : [],
    };
    cachedPolicyAt = Date.now();
    return cachedPolicy;
  }

  function allowedHost(candidate) {
    const host = String(candidate?.host || "").toLowerCase();
    const provider = String(candidate?.provider || "");
    if (provider === "gmail") return host === "mail.google.com";
    if (provider === "google_drive") return host === "docs.google.com" || host === "drive.google.com";
    if (provider === "github") return host === "github.com";
    if (provider === "salesforce") return host.endsWith(".salesforce.com") || host.endsWith(".force.com");
    if (provider === "jira") return host.endsWith(".atlassian.net");
    if (provider === "linear") return host === "linear.app";
    return false;
  }

  function matchesPattern(value, patterns) {
    const text = String(value || "");
    for (const raw of patterns || []) {
      const pattern = String(raw || "");
      if (!pattern) continue;
      try {
        if (new RegExp(pattern, "i").test(text)) return true;
      } catch (_) {
        if (text.toLowerCase().includes(pattern.toLowerCase())) return true;
      }
    }
    return false;
  }

  function canonical(candidate) {
    return [
      String(candidate?.provider || "").toLowerCase(),
      String(candidate?.resource_kind || "").toLowerCase(),
      String(candidate?.host || "").toLowerCase(),
      String(candidate?.resolver_locator || ""),
    ].join("|");
  }

  async function hmacHex(secret, message) {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(String(secret || "")),
      {name: "HMAC", hash: "SHA-256"},
      false,
      ["sign"],
    );
    const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(String(message || "")));
    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, "0")).join("");
  }

  async function sensorResourceRef(candidate) {
    // The durable retry queue lives on disk. Do not place a dictionary-attackable
    // SHA of a short Jira/GitHub/Linear identifier there. The paired extension
    // already has a high-entropy installation secret, so use it to make the
    // queue-side fingerprint opaque. The local server HMACs this value again
    // with a separate installation secret before database persistence.
    let secret = "";
    try { secret = String(await globalThis.OWGBrowserAuth?.pairingSecret?.() || ""); } catch (_) {}
    if (!secret) return "";
    const digest = await hmacHex(secret, SENSOR_NAMESPACE + canonical(candidate));
    return `owg:e:${digest.slice(0, 24)}`;
  }

  async function normalizeCandidate(candidate, includeLocator) {
    if (!candidate || typeof candidate !== "object" || !allowedHost(candidate)) return null;
    const provider = String(candidate.provider || "").toLowerCase();
    const resourceKind = String(candidate.resource_kind || "").toLowerCase();
    const host = String(candidate.host || "").toLowerCase();
    const locator = String(candidate.resolver_locator || "");
    if (!provider || !resourceKind || !locator || locator.length > 320) return null;
    const resourceRef = await sensorResourceRef(candidate);
    if (!resourceRef) return null;
    const out = {
      provider,
      resource_kind: resourceKind,
      host,
      resource_ref: resourceRef,
      resolution: "observed",
    };
    if (includeLocator) out.resolver_locator = locator;
    return out;
  }

  function recentlySent(ref) {
    const key = String(ref?.resource_ref || "");
    if (!key) return true;
    const now = Date.now();
    const previous = recent.get(key) || 0;
    recent.set(key, now);
    if (recent.size > 200) {
      for (const [k, at] of recent) if (now - at > 60000) recent.delete(k);
    }
    return now - previous < 1000;
  }

  async function observeUrl(rawUrl, tab, reason) {
    const policy = await loadPolicy();
    const settings = policy.settings || {};
    if (!settings.business_object_references) return;

    const parser = globalThis.OpenWorkGraphResourceReferences;
    if (!parser?.parse) return;
    const candidate = parser.parse(rawUrl);
    if (!candidate) return;

    let currentTab = tab || null;
    try {
      if (!currentTab?.url && currentTab?.id != null) currentTab = await ext.tabs.get(currentTab.id);
    } catch (_) {}

    const page = typeof safeUrl === "function" ? safeUrl(rawUrl || currentTab?.url || "") : null;
    if (!page) return;
    const candidateHost = String(candidate.host || "").toLowerCase();
    if (!candidateHost || candidateHost !== String(page.hostname || "").toLowerCase()) return;

    const title = String(currentTab?.title || "").slice(0, 240);
    if (matchesPattern(page.hostname, policy.excluded_browser_host_patterns)) return;
    // If title exclusions exist, do not emit until a title is actually known.
    // That prevents a rich locator from entering the retry queue before the
    // local exclusion policy can be evaluated.
    if (policy.excluded_title_patterns.length && !title) return;
    if (matchesPattern(title, policy.excluded_title_patterns)) return;

    const reference = await normalizeCandidate(candidate, !!settings.resource_reference_locators);
    if (!reference || recentlySent(reference)) return;

    await sendBrowserEvent({
      observed_at: new Date().toISOString(),
      action: "resource_reference_observed",
      page: {...page, title},
      target: {},
      metadata: {
        resource_reference: reference,
        resource_reference_reason: String(reason || "navigation").slice(0, 80),
        tab_id: currentTab?.id ?? null,
        window_id: currentTab?.windowId ?? null,
      },
    });
  }

  async function observeTab(tabId, rawUrl, reason) {
    try {
      const tab = tabId != null ? await ext.tabs.get(tabId) : null;
      await observeUrl(rawUrl || tab?.url || "", tab, reason);
    } catch (_) {}
  }

  // Use browser/background navigation signals that OWG already has permission to
  // observe. No page-world history functions are wrapped or modified.
  ext.tabs.onActivated.addListener(({tabId}) => observeTab(tabId, "", "tab_activated"));
  ext.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.url) observeUrl(changeInfo.url, tab, "tab_url_changed");
    else if (changeInfo.status === "complete" && tab?.url) observeUrl(tab.url, tab, "navigation_complete");
  });

  if (ext.webNavigation?.onHistoryStateUpdated) {
    ext.webNavigation.onHistoryStateUpdated.addListener((details) => {
      if (details.frameId === 0) observeTab(details.tabId, details.url, "history_state");
    });
  }
  if (ext.webNavigation?.onCompleted) {
    ext.webNavigation.onCompleted.addListener((details) => {
      if (details.frameId === 0) observeTab(details.tabId, details.url, "web_navigation_complete");
    });
  }

  loadPolicy({force: true});
})();
