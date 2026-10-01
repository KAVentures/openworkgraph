(() => {
  "use strict";
  const ext = globalThis.browser ?? globalThis.chrome;
  const API = "http://127.0.0.1:8787";
  const SETTINGS_KEY = "openworkgraph_resource_reference_settings";
  const recent = new Map();

  async function refreshPolicy() {
    let data = null;
    try {
      if (typeof getJson === "function") data = await getJson("/v1/browser-context");
      else {
        const response = await fetch(`${API}/v1/browser-context`, {method: "GET", cache: "no-store"});
        if (response.ok) data = await response.json();
      }
    } catch (_) {}
    const policy = {
      settings: data?.signal_settings || {},
      excluded_browser_host_patterns: Array.isArray(data?.excluded_browser_host_patterns) ? data.excluded_browser_host_patterns : [],
      excluded_title_patterns: Array.isArray(data?.excluded_title_patterns) ? data.excluded_title_patterns : [],
      checked_at: Date.now(),
    };
    try { await ext.storage.local.set({[SETTINGS_KEY]: policy}); } catch (_) {}
    return policy;
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

  async function sha256Hex(value) {
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, "0")).join("");
  }

  async function normalizeCandidate(candidate, includeLocator) {
    if (!candidate || typeof candidate !== "object" || !allowedHost(candidate)) return null;
    const provider = String(candidate.provider || "").toLowerCase();
    const resourceKind = String(candidate.resource_kind || "").toLowerCase();
    const host = String(candidate.host || "").toLowerCase();
    const locator = String(candidate.resolver_locator || "");
    if (!provider || !resourceKind || !locator || locator.length > 320) return null;
    const digest = await sha256Hex(canonical(candidate));
    const out = {
      provider,
      resource_kind: resourceKind,
      host,
      resource_ref: `owg:r:${digest.slice(0, 24)}`,
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

  ext.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "workflow_observer_resource_reference_settings_request") {
      refreshPolicy().then(() => sendResponse?.({ok: true})).catch(() => sendResponse?.({ok: false}));
      return true;
    }
    if (message?.type !== "workflow_observer_resource_reference") return;

    (async () => {
      const policy = await refreshPolicy();
      const settings = policy.settings || {};
      if (!settings.business_object_references) return;

      let tab = sender?.tab || null;
      try {
        if (!tab?.url && sender?.tab?.id != null) tab = await ext.tabs.get(sender.tab.id);
      } catch (_) {}
      const page = typeof safeUrl === "function" ? safeUrl(tab?.url || "") : null;
      if (!page) return;

      const candidateHost = String(message?.resource_reference?.host || "").toLowerCase();
      if (!candidateHost || candidateHost !== String(page.hostname || "").toLowerCase()) return;
      if (matchesPattern(page.hostname, policy.excluded_browser_host_patterns)) return;
      if (matchesPattern(tab?.title || "", policy.excluded_title_patterns)) return;

      const reference = await normalizeCandidate(
        message.resource_reference,
        !!settings.resource_reference_locators,
      );
      if (!reference || recentlySent(reference)) return;

      await sendBrowserEvent({
        observed_at: message.observed_at || new Date().toISOString(),
        action: "resource_reference_observed",
        page: {...page, title: String(tab?.title || "").slice(0, 240)},
        target: {},
        metadata: {
          resource_reference: reference,
          resource_reference_reason: String(message.reason || "navigation").slice(0, 80),
          tab_id: tab?.id ?? null,
          window_id: tab?.windowId ?? null,
        },
      });
    })().catch(() => {});
  });

  refreshPolicy();
})();
