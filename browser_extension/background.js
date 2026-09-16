const API = "http://127.0.0.1:8787";
const ext = globalThis.browser ?? globalThis.chrome;

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

async function emitTab(tab, action) {
  if (!tab) return;
  const page = safeUrl(tab.url);
  if (!page) return;
  await post("/v1/browser-events", {
    observed_at: new Date().toISOString(),
    action,
    page: {...page, title: String(tab.title || "").slice(0, 240)},
    target: {},
    metadata: {tab_id: tab.id ?? null, window_id: tab.windowId ?? null}
  });
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

ext.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" || changeInfo.url) emitTab(tab, "navigation");
});

ext.runtime.onInstalled.addListener(() => {
  post("/v1/browser-heartbeat", {observed_at: new Date().toISOString(), status: "installed"});
});
