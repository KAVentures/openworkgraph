const API = "http://127.0.0.1:8787";
const ext = globalThis.browser ?? globalThis.chrome;
const manifest = ext.runtime.getManifest();
const SENSOR_VERSION = manifest.version_name || manifest.version || "unknown";
const BROWSER_RUNTIME_ID = globalThis.crypto?.randomUUID?.() || `runtime-${Date.now()}-${Math.random()}`;
const IDENTITY_KEY = "openworkgraph_sensor_id";
const QUEUE_KEY = "openworkgraph_pending_browser_events";
const recentNav = new Map();
let storageLock = Promise.resolve();

function uuid() {
  return globalThis.crypto?.randomUUID?.() || `evt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function safeUrl(raw) {
  try {
    const u = new URL(raw || "");
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return {origin: u.origin, hostname: u.hostname, pathname: u.pathname || "/"};
  } catch (_) {
    return null;
  }
}

async function sensorId() {
  try {
    const stored = await ext.storage.local.get(IDENTITY_KEY);
    if (stored?.[IDENTITY_KEY]) return String(stored[IDENTITY_KEY]);
    const id = `browser:${uuid()}`;
    await ext.storage.local.set({[IDENTITY_KEY]: id});
    return id;
  } catch (_) {
    return `browser:${BROWSER_RUNTIME_ID}`;
  }
}

async function postDirect(path, body) {
  try {
    const response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
      cache: "no-store"
    });
    return response.ok;
  } catch (_) {
    return false;
  }
}

function serializedStorage(fn) {
  const next = storageLock.then(fn, fn);
  storageLock = next.catch(() => {});
  return next;
}

async function enqueueBrowserEvent(body) {
  return serializedStorage(async () => {
    try {
      const stored = await ext.storage.local.get(QUEUE_KEY);
      const queue = Array.isArray(stored?.[QUEUE_KEY]) ? stored[QUEUE_KEY] : [];
      if (!queue.some(x => x?.event_id === body.event_id)) queue.push(body);
      // Bound local storage in pathological offline cases while preserving the
      // newest evidence. 5000 semantic events is already many hours of activity.
      const bounded = queue.slice(-5000);
      await ext.storage.local.set({[QUEUE_KEY]: bounded});
    } catch (_) {}
  });
}

async function flushBrowserQueue() {
  return serializedStorage(async () => {
    try {
      const stored = await ext.storage.local.get(QUEUE_KEY);
      const queue = Array.isArray(stored?.[QUEUE_KEY]) ? stored[QUEUE_KEY] : [];
      if (!queue.length) return 0;
      const remaining = [];
      let delivered = 0;
      for (const item of queue.slice(0, 200)) {
        if (await postDirect("/v1/browser-events", item)) delivered += 1;
        else remaining.push(item);
      }
      remaining.push(...queue.slice(200));
      await ext.storage.local.set({[QUEUE_KEY]: remaining});
      return delivered;
    } catch (_) {
      return 0;
    }
  });
}

async function sendBrowserEvent(body) {
  const enriched = {
    ...body,
    event_id: body.event_id || uuid(),
    sensor_id: body.sensor_id || await sensorId(),
    sensor_version: SENSOR_VERSION,
    browser_session_id: body.browser_session_id || BROWSER_RUNTIME_ID,
  };
  if (await postDirect("/v1/browser-events", enriched)) {
    // Opportunistically drain older events whenever the local API is healthy.
    flushBrowserQueue();
    return true;
  }
  await enqueueBrowserEvent(enriched);
  return false;
}

async function heartbeat(status = "connected") {
  const id = await sensorId();
  await postDirect("/v1/browser-heartbeat", {
    observed_at: new Date().toISOString(),
    status,
    sensor_id: id,
    sensor_version: SENSOR_VERSION,
    browser_session_id: BROWSER_RUNTIME_ID,
  });
}

async function emitTab(tab, action, extra = {}, urlOverride = null) {
  if (!tab && !urlOverride) return;
  const page = safeUrl(urlOverride || tab?.url);
  if (!page) return;
  await sendBrowserEvent({
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
  if (now - prev < 800) return false;
  recentNav.set(key, now);
  if (recentNav.size > 300) {
    for (const [k, at] of recentNav) if (now - at > 30000) recentNav.delete(k);
  }
  return true;
}

ext.runtime.onMessage.addListener((message, sender) => {
  if (!message || message.type !== "workflow_observer_event") return;
  const page = safeUrl(message.page?.url || sender.tab?.url || "");
  if (!page) return;
  sendBrowserEvent({
    observed_at: message.observed_at || new Date().toISOString(),
    action: String(message.action || "browser_event"),
    page: {...page, title: String(message.page?.title || sender.tab?.title || "").slice(0, 240)},
    target: message.target || {},
    metadata: {...(message.metadata || {}), tab_id: sender.tab?.id ?? null, window_id: sender.tab?.windowId ?? null}
  });
});

ext.tabs.onActivated.addListener(async ({tabId}) => {
  try { await emitTab(await ext.tabs.get(tabId), "tab_activated"); } catch (_) {}
});

// Address-bar navigation can surface as a URL change or only as a loading update
// depending on browser/version. Capture both; webNavigation remains authoritative.
ext.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const candidate = changeInfo.url || (changeInfo.status === "loading" ? tab?.url : null);
  if (!candidate || !safeUrl(candidate)) return;
  if (!shouldEmitNav(tabId, candidate, "tabs")) return;
  await emitTab(tab, "navigation_started", {trigger: "tabs.onUpdated", update_status: changeInfo.status || null}, candidate);
});

if (ext.webNavigation?.onBeforeNavigate) {
  ext.webNavigation.onBeforeNavigate.addListener(async (details) => {
    if (details.frameId !== 0 || !safeUrl(details.url)) return;
    if (!shouldEmitNav(details.tabId, details.url, "before")) return;
    try {
      await emitTab(await ext.tabs.get(details.tabId), "navigation_requested", {trigger: "webNavigation.onBeforeNavigate"}, details.url);
    } catch (_) {
      await emitTab({id: details.tabId}, "navigation_requested", {trigger: "webNavigation.onBeforeNavigate", tab_id: details.tabId}, details.url);
    }
  });
}

if (ext.webNavigation?.onCommitted) {
  ext.webNavigation.onCommitted.addListener(async (details) => {
    if (details.frameId !== 0 || !safeUrl(details.url)) return;
    if (!shouldEmitNav(details.tabId, details.url, "committed")) return;
    try {
      await emitTab(await ext.tabs.get(details.tabId), "navigation_committed", {
        trigger: "webNavigation.onCommitted",
        transition_type: details.transitionType || null,
      }, details.url);
    } catch (_) {
      await emitTab({id: details.tabId}, "navigation_committed", {
        trigger: "webNavigation.onCommitted",
        transition_type: details.transitionType || null,
      }, details.url);
    }
  });
}

// Backup for browsers where the earlier navigation signal is missed or the
// service worker was waking while navigation began.
if (ext.webNavigation?.onCompleted) {
  ext.webNavigation.onCompleted.addListener(async (details) => {
    if (details.frameId !== 0 || !safeUrl(details.url)) return;
    if (!shouldEmitNav(details.tabId, details.url, "completed")) return;
    try { await emitTab(await ext.tabs.get(details.tabId), "navigation_completed", {trigger: "webNavigation.onCompleted"}, details.url); } catch (_) {}
  });
}

if (ext.webNavigation?.onHistoryStateUpdated) {
  ext.webNavigation.onHistoryStateUpdated.addListener(async (details) => {
    if (details.frameId !== 0 || !safeUrl(details.url)) return;
    if (!shouldEmitNav(details.tabId, details.url, "history")) return;
    try { await emitTab(await ext.tabs.get(details.tabId), "navigation", {trigger: "history_state"}, details.url); } catch (_) {}
  });
}

async function startup(status) {
  await heartbeat(status);
  await flushBrowserQueue();
  try { ext.alarms.create("openworkgraph-flush", {periodInMinutes: 1}); } catch (_) {}
}

ext.runtime.onInstalled.addListener(() => startup("installed"));
if (ext.runtime.onStartup) ext.runtime.onStartup.addListener(() => startup("startup"));
if (ext.alarms?.onAlarm) {
  ext.alarms.onAlarm.addListener((alarm) => {
    if (alarm?.name !== "openworkgraph-flush") return;
    heartbeat("connected");
    flushBrowserQueue();
  });
}

// Service workers can start without onStartup (for example after an extension
// reload). A one-shot bootstrap makes version/status visible immediately.
startup("background_started");
