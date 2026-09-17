const API = "http://127.0.0.1:8787";
const ext = globalThis.browser ?? globalThis.chrome;
const manifest = ext.runtime.getManifest();
const SENSOR_VERSION = manifest.version_name || manifest.version || "unknown";
const BROWSER_RUNTIME_ID = globalThis.crypto?.randomUUID?.() || `runtime-${Date.now()}-${Math.random()}`;
const IDENTITY_KEY = "openworkgraph_sensor_id";
const QUEUE_KEY = "openworkgraph_pending_browser_events";
const WORK_CONTEXT_KEY = "openworkgraph_work_context";
const recentNav = new Map();
let storageLock = Promise.resolve();
let cachedWorkContext = null;
let cachedWorkContextAt = 0;

function uuid() {
  return globalThis.crypto?.randomUUID?.() || `evt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sanitizePathname(pathname) {
  const sensitive = new Set(["auth","authenticate","callback","confirm","invite","invitation","login","magic","oauth","recover","recovery","reset","signin","token","verify","verification"]);
  const parts = String(pathname || "/").split("/");
  let previous = "";
  return parts.map(part => {
    if (!part) return part;
    let replacement = part;
    if (sensitive.has(String(previous).toLowerCase()) && part.length >= 6) replacement = ":token";
    else if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(part)) replacement = ":id";
    else if (/^\d{7,}$/.test(part)) replacement = ":id";
    else if (/^[0-9a-f]{20,}$/i.test(part) || /^[A-Za-z0-9_-]{24,}$/.test(part)) replacement = ":token";
    previous = part;
    return replacement;
  }).join("/") || "/";
}

function safeUrl(raw) {
  try {
    const u = new URL(raw || "");
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return {origin: u.origin, hostname: u.hostname, pathname: sanitizePathname(u.pathname || "/")};
  } catch (_) {
    return null;
  }
}

function sanitizeUrlString(value) {
  const safe = safeUrl(value);
  if (!safe) return String(value || "");
  return `${safe.origin}${safe.pathname}`;
}

function sanitizeMetadata(value, key = "") {
  if (Array.isArray(value)) return value.map(v => sanitizeMetadata(v, key));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      const low = String(k).toLowerCase();
      if (low === "pathname" && typeof v === "string") out[k] = sanitizePathname(v);
      else if (typeof v === "string" && (low.includes("url") || ["href","uri","origin","frame_url"].includes(low))) out[k] = sanitizeUrlString(v);
      else out[k] = sanitizeMetadata(v, k);
    }
    return out;
  }
  if (typeof value === "string" && /^https?:\/\//i.test(value)) return sanitizeUrlString(value);
  return value;
}

function sanitizeBrowserEvent(body) {
  const out = {...(body || {})};
  const page = out.page || {};
  const rawPage = page.origin ? `${page.origin}${page.pathname || "/"}` : "";
  const safePage = safeUrl(rawPage) || (page.hostname ? {origin: page.origin || "", hostname: String(page.hostname), pathname: sanitizePathname(page.pathname || "/")} : null);
  out.page = safePage ? {...safePage, title: String(page.title || "").slice(0, 240)} : {};
  out.metadata = sanitizeMetadata(out.metadata || {});
  return out;
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

async function getJson(path) {
  try {
    const response = await fetch(`${API}${path}`, {method: "GET", cache: "no-store"});
    if (!response.ok) return null;
    return await response.json();
  } catch (_) {
    return null;
  }
}

async function refreshWorkContext() {
  const fresh = await getJson("/v1/browser-context");
  if (!fresh) return null;
  const context = {
    organization_id: String(fresh.organization_id || ""),
    actor_id: String(fresh.actor_id || ""),
    device_id: String(fresh.device_id || ""),
    work_session_id: String(fresh.work_session_id || "")
  };
  cachedWorkContext = context;
  cachedWorkContextAt = Date.now();
  try { await ext.storage.local.set({[WORK_CONTEXT_KEY]: context}); } catch (_) {}
  return context;
}

async function captureWorkContext() {
  if (cachedWorkContext && Date.now() - cachedWorkContextAt < 5000) return cachedWorkContext;
  const fresh = await refreshWorkContext();
  if (fresh) return fresh;
  if (cachedWorkContext) return cachedWorkContext;
  try {
    const stored = await ext.storage.local.get(WORK_CONTEXT_KEY);
    const value = stored?.[WORK_CONTEXT_KEY];
    if (value && typeof value === "object") {
      cachedWorkContext = value;
      return value;
    }
  } catch (_) {}
  return {organization_id: "", actor_id: "", device_id: "", work_session_id: ""};
}

function serializedStorage(fn) {
  const next = storageLock.then(fn, fn);
  storageLock = next.catch(() => {});
  return next;
}

async function sanitizePendingBrowserQueue() {
  return serializedStorage(async () => {
    try {
      const stored = await ext.storage.local.get(QUEUE_KEY);
      const queue = Array.isArray(stored?.[QUEUE_KEY]) ? stored[QUEUE_KEY] : [];
      const sanitized = queue.map(sanitizeBrowserEvent).slice(-5000);
      await ext.storage.local.set({[QUEUE_KEY]: sanitized});
      return sanitized.length;
    } catch (_) {
      return 0;
    }
  });
}

async function enqueueBrowserEvent(body) {
  return serializedStorage(async () => {
    try {
      const stored = await ext.storage.local.get(QUEUE_KEY);
      const queue = Array.isArray(stored?.[QUEUE_KEY]) ? stored[QUEUE_KEY].map(sanitizeBrowserEvent) : [];
      const safeBody = sanitizeBrowserEvent(body);
      if (!queue.some(x => x?.event_id === safeBody.event_id)) queue.push(safeBody);
      const bounded = queue.slice(-5000);
      await ext.storage.local.set({[QUEUE_KEY]: bounded});
    } catch (_) {}
  });
}

async function flushBrowserQueue() {
  return serializedStorage(async () => {
    try {
      const stored = await ext.storage.local.get(QUEUE_KEY);
      const queue = Array.isArray(stored?.[QUEUE_KEY]) ? stored[QUEUE_KEY].map(sanitizeBrowserEvent) : [];
      if (!queue.length) return 0;
      const remaining = [];
      let delivered = 0;
      for (const item of queue.slice(0, 200)) {
        if (await postDirect("/v1/browser-events", item)) delivered += 1;
        else remaining.push(item);
      }
      remaining.push(...queue.slice(200));
      await ext.storage.local.set({[QUEUE_KEY]: remaining.map(sanitizeBrowserEvent)});
      return delivered;
    } catch (_) {
      return 0;
    }
  });
}

async function sendBrowserEvent(body) {
  const work = await captureWorkContext();
  const enriched = sanitizeBrowserEvent({
    ...body,
    event_id: body.event_id || uuid(),
    sensor_id: body.sensor_id || await sensorId(),
    sensor_version: SENSOR_VERSION,
    browser_session_id: body.browser_session_id || BROWSER_RUNTIME_ID,
    organization_id: body.organization_id ?? work.organization_id ?? "",
    actor_id: body.actor_id ?? work.actor_id ?? "",
    device_id: body.device_id ?? work.device_id ?? "",
    work_session_id: body.work_session_id ?? work.work_session_id ?? "",
  });
  if (await postDirect("/v1/browser-events", enriched)) {
    flushBrowserQueue();
    return true;
  }
  await enqueueBrowserEvent(enriched);
  return false;
}

async function heartbeat(status = "connected") {
  const id = await sensorId();
  await refreshWorkContext();
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
  const safe = sanitizeUrlString(url);
  const key = `${kind}:${tabId}:${safe}`;
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
  const topFrame = message.metadata?.top_frame !== false;
  const pageSource = topFrame ? (message.page?.url || sender.tab?.url || "") : (sender.tab?.url || "");
  const page = safeUrl(pageSource);
  if (!page) return;
  const metadata = {...(message.metadata || {})};
  delete metadata.frame_url;
  metadata.top_frame = topFrame;
  metadata.frame_kind = topFrame ? "top" : "subframe";
  sendBrowserEvent({
    observed_at: message.observed_at || new Date().toISOString(),
    action: String(message.action || "browser_event"),
    page: {...page, title: String(topFrame ? (message.page?.title || sender.tab?.title || "") : (sender.tab?.title || "")).slice(0, 240)},
    target: message.target || {},
    metadata: {...metadata, tab_id: sender.tab?.id ?? null, window_id: sender.tab?.windowId ?? null}
  });
});

ext.tabs.onActivated.addListener(async ({tabId}) => {
  try { await emitTab(await ext.tabs.get(tabId), "tab_activated"); } catch (_) {}
});

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
  await sanitizePendingBrowserQueue();
  await refreshWorkContext();
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

startup("background_started");
