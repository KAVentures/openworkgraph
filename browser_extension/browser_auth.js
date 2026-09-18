(() => {
  const API = "http://127.0.0.1:8787";
  const ext = globalThis.browser ?? globalThis.chrome;
  const nativeFetch = globalThis.fetch.bind(globalThis);
  const SECRET_KEY = "openworkgraph_browser_pairing_secret";
  const encoder = new TextEncoder();

  function randomNonce() {
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function hex(bytes) {
    return Array.from(new Uint8Array(bytes)).map(b => b.toString(16).padStart(2, "0")).join("");
  }

  async function sha256Hex(text) {
    return hex(await crypto.subtle.digest("SHA-256", encoder.encode(String(text || ""))));
  }

  async function hmacHex(secret, message) {
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(String(secret || "")),
      {name: "HMAC", hash: "SHA-256"},
      false,
      ["sign"]
    );
    return hex(await crypto.subtle.sign("HMAC", key, encoder.encode(String(message || ""))));
  }

  async function storedSecret() {
    try {
      const stored = await ext.storage.local.get(SECRET_KEY);
      if (stored?.[SECRET_KEY]) return String(stored[SECRET_KEY]);
    } catch (_) {}
    return "";
  }

  async function loadBundledSecret() {
    try {
      const response = await nativeFetch(ext.runtime.getURL("pairing.json"), {cache: "no-store"});
      if (!response.ok) return "";
      const payload = await response.json();
      const secret = String(payload?.secret || "");
      if (!secret) return "";
      await ext.storage.local.set({[SECRET_KEY]: secret});
      return secret;
    } catch (_) {
      return "";
    }
  }

  async function pairingSecret() {
    return (await storedSecret()) || (await loadBundledSecret());
  }

  async function proveServer(secret) {
    if (!secret) return false;
    const nonce = randomNonce();
    try {
      const response = await nativeFetch(`${API}/v1/browser-challenge`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({nonce}),
        cache: "no-store"
      });
      if (!response.ok) return false;
      const payload = await response.json();
      const expected = await hmacHex(secret, `openworkgraph-server-proof:${nonce}`);
      return String(payload?.proof || "") === expected;
    } catch (_) {
      return false;
    }
  }

  async function requestAuthorization(secret, method, path, body) {
    const ts = Math.floor(Date.now() / 1000);
    const nonce = randomNonce();
    const bodyHash = await sha256Hex(body || "");
    const canonical = `${ts}\n${nonce}\n${String(method || "GET").toUpperCase()}\n${path}\n${bodyHash}`;
    const mac = await hmacHex(secret, canonical);
    return `OWG-HMAC ${ts}.${nonce}.${mac}`;
  }

  async function authenticatedFetch(input, init = {}) {
    const rawUrl = typeof input === "string" ? input : String(input?.url || "");
    let parsed;
    try { parsed = new URL(rawUrl); } catch (_) { return nativeFetch(input, init); }
    if (parsed.origin !== API) return nativeFetch(input, init);
    if (["/health", "/v1/browser-challenge", "/v1/browser-pair"].includes(parsed.pathname)) {
      return nativeFetch(input, init);
    }

    const secret = await pairingSecret();
    if (!secret || !(await proveServer(secret))) {
      return new Response(JSON.stringify({detail: "OpenWorkGraph browser sensor is not paired with the local server"}), {
        status: 401,
        headers: {"Content-Type": "application/json"}
      });
    }

    const method = String(init?.method || (input?.method ?? "GET")).toUpperCase();
    const body = typeof init?.body === "string" ? init.body : "";
    const headers = new Headers(init?.headers || (input?.headers ?? {}));
    headers.set("Authorization", await requestAuthorization(secret, method, parsed.pathname, body));
    return nativeFetch(input, {...init, headers});
  }

  globalThis.fetch = authenticatedFetch;
  globalThis.OWGBrowserAuth = {pairingSecret, proveServer};
})();
