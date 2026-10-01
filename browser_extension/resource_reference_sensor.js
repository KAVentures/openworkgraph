(() => {
  "use strict";
  const ext = globalThis.browser ?? globalThis.chrome;
  const SETTINGS_KEY = "openworkgraph_resource_reference_settings";
  let lastKey = "";

  function isTopFrame() {
    try { return window.top === window; } catch (_) { return false; }
  }

  async function settings() {
    try {
      let stored = await ext.storage.local.get(SETTINGS_KEY);
      let value = stored?.[SETTINGS_KEY];
      const stale = !value || !value.checked_at || Date.now() - Number(value.checked_at) > 5000;
      if (stale) {
        try { await ext.runtime.sendMessage({type: "workflow_observer_resource_reference_settings_request"}); } catch (_) {}
        stored = await ext.storage.local.get(SETTINGS_KEY);
        value = stored?.[SETTINGS_KEY];
      }
      return value?.settings || {};
    } catch (_) {
      return {};
    }
  }

  async function emit(reason) {
    if (!isTopFrame()) return;
    const current = await settings();
    if (!current.business_object_references) return;
    const parser = globalThis.OpenWorkGraphResourceReferences;
    if (!parser?.parse) return;
    const reference = parser.parse(location.href);
    if (!reference) {
      lastKey = "";
      return;
    }
    const key = JSON.stringify(reference);
    if (key === lastKey) return;
    lastKey = key;
    try {
      await ext.runtime.sendMessage({
        type: "workflow_observer_resource_reference",
        observed_at: new Date().toISOString(),
        reason,
        resource_reference: reference,
      });
    } catch (_) {}
  }

  function schedule(reason) {
    queueMicrotask(() => emit(reason));
  }

  if (!isTopFrame()) return;
  schedule("document_start");
  addEventListener("hashchange", () => schedule("hashchange"));
  addEventListener("popstate", () => schedule("popstate"));
  addEventListener("pageshow", () => schedule("pageshow"));

  for (const name of ["pushState", "replaceState"]) {
    try {
      const original = history[name];
      if (typeof original !== "function") continue;
      history[name] = function (...args) {
        const result = original.apply(this, args);
        schedule(`history_${name}`);
        return result;
      };
    } catch (_) {}
  }
})();
