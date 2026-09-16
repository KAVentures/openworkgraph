const API = "http://127.0.0.1:8787";
const ext = globalThis.browser ?? globalThis.chrome;
const recentNav = new Map();

function safeUrl(raw) {
  try {
    const u = new URL(raw || "");
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return {
      origin: u.origin,
      hostname: u.hostname,
      pathname: u.pathname || "/"
    };
  } catch (_) {
    return null;
  }
}

async function post(path, body) {
  try {
    await fetch(`${API}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
      cache: "no-store"
    });
  } catch (_) {
    // Desktop capture remains the fallback when the local server is unavailable.
  }
}

async function emitTab(tab, action, extra = {}, urlOverride = null) {
  if (!tab && !urlOverride) return;
  const page = safeUrl(urlOverride || tab?.url);
  if (!page) return;
  await post("/v1/browser-events", {
    observed_at: new Date().toISOString(),
    action,
    page: {...page, title: String(tab?.title || "").slice(0, 240)},
    target: {},
    metadata: {tab_id: tab?.id ?? extra.tab_id ?? null, window_id: tab?.windowId ?? null, ...extra}
  });
}

function shouldEmitNav(tabId, url, kind) {
  const key = `${kind}:${tabId}:${String(url || "")}`;
  const now = Date.now();
  const prev = recentNav.get(key) || 0;
  // Browser APIs can report the same navigation through multiple listeners.
  // Keep the first event; suppress near-identical duplicates for 800 ms.
  if (now - prev < 800) return false;
  recentNav.set(key, now);
  // Trim stale keys opportunistically.
  if (recentNav.size > 300) {
    for (const [k, at] of recentNav) if (now - at > 30000) recentNav.delete(k);
  }
  return true;
}

ext.runtime.onMessage.addListener((message, sender) => {
  if (!message || message.type !== "workflow_observer_event") return;
  const page = safeUrl(message.page?.url || sender.tab?.url || "");
  if (!page) return;
  post("/v1/browser-events", {
    observed_at: message.observed_at || new Date().toISOString(),
    action: String(message.action || "browser_event"),
    page: {...page, title: String(message.page?.title || sender.tab?.title || "").slice(0, 240)},
    target: message.target || {},
    metadata: message.metadata || {}
  });
});

ext.tabs.onActivated.addListener(async ({tabId}) => {
  try { emitTab(await ext.tabs.get(tabId), "tab_activated"); } catch (_) {}
});

// URL change is useful as an early fallback, but a committed navigation below is
// the authoritative event. Do not wait for status="complete": a user may leave a
// page before it has fully loaded.
ext.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (!changeInfo.url) return;
  if (!shouldEmitNav(tabId, changeInfo.url, "url_changed")) return;
  emitTab(tab, "navigation_started", {trigger: "tabs.onUpdated"}, changeInfo.url);
});

// Capture the destination URL as soon as the browser starts a top-frame
// navigation. This uses details.url directly rather than waiting for tab.url to
// update, so address-bar Enter navigations are not lost on very brief visits.
if (ext.webNavigation?.onBeforeNavigate) {
  ext.webNavigation.onBeforeNavigate.addListener(async (details) => {
    if (details.frameId !== 0) return;
    if (!shouldEmitNav(details.tabId, details.url, "before")) return;
    try {
      const tab = await ext.tabs.get(details.tabId);
      emitTab(tab, "navigation_requested", {trigger: "webNavigation.onBeforeNavigate"}, details.url);
    } catch (_) {
      emitTab({id: details.tabId}, "navigation_requested", {trigger: "webNavigation.onBeforeNavigate", tab_id: details.tabId}, details.url);
    }
  });
}

// First-class navigation capture. This fires when the browser commits the new
// document, independent of scrolling/clicking/dwell time.
if (ext.webNavigation?.onCommitted) {
  ext.webNavigation.onCommitted.addListener(async (details) => {
    if (details.frameId !== 0) return;
    if (!shouldEmitNav(details.tabId, details.url, "committed")) return;
    try {
      const tab = await ext.tabs.get(details.tabId);
      emitTab(tab, "navigation_committed", {
        trigger: "webNavigation.onCommitted",
        transition_type: details.transitionType || null,
      }, details.url);
    } catch (_) {}
  });
}

// Single-page applications often change routes without loading a new document.
if (ext.webNavigation?.onHistoryStateUpdated) {
  ext.webNavigation.onHistoryStateUpdated.addListener(async (details) => {
    if (details.frameId !== 0) return;
    if (!shouldEmitNav(details.tabId, details.url, "history")) return;
    try {
      emitTab(await ext.tabs.get(details.tabId), "navigation", {trigger: "history_state"}, details.url);
    } catch (_) {}
  });
}

ext.runtime.onInstalled.addListener(() => {
  post("/v1/browser-heartbeat", {observed_at: new Date().toISOString(), status: "installed-v27"});
});
