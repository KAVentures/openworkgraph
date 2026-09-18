const ext = globalThis.browser ?? globalThis.chrome;
let lastHref = location.href;
let lastNavAt = 0;
let lastPointer = {at: 0, key: ""};
let lastFocus = {at: 0, key: ""};

const FALLBACK_LABEL_MAX_CHARS = 60;
const FALLBACK_LABEL_MAX_WORDS = 8;
const FALLBACK_CONTROL_TAGS = new Set(["BUTTON", "SUMMARY", "OPTION"]);
const FALLBACK_CONTROL_ROLES = new Set(["button", "tab", "menuitem", "option", "switch", "checkbox", "radio"]);

function cleanText(value, max = 160) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
}

function isTopFrame() {
  try { return window.top === window; } catch (_) { return false; }
}

function sanitizePathname(pathname) {
  const sensitive = new Set(["auth","authenticate","callback","confirm","invite","invitation","login","magic","oauth","recover","recovery","reset","signin","token","verify","verification"]);
  const parts = String(pathname || "/").split("/");
  let previous = "";
  return parts.map((part, index) => {
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

function safePageUrl(raw) {
  try {
    const u = new URL(raw || "");
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return `${u.origin}${sanitizePathname(u.pathname || "/")}`;
  } catch (_) {
    return "";
  }
}

function isSensitiveControl(el) {
  if (!(el instanceof Element)) return false;
  const type = cleanText(el.getAttribute?.("type"), 80).toLowerCase();
  const autocomplete = cleanText(el.getAttribute?.("autocomplete"), 120).toLowerCase();
  const name = cleanText(el.getAttribute?.("name"), 120).toLowerCase();
  const aria = cleanText(el.getAttribute?.("aria-label"), 160).toLowerCase();
  return type === "password" || /current-password|new-password|cc-|one-time-code/.test(autocomplete) || /password|passcode|otp|security code/.test(`${name} ${aria}`);
}

function safeFallbackControlText(el) {
  const tag = String(el?.tagName || "").toUpperCase();
  const role = cleanText(el?.getAttribute?.("role"), 80).toLowerCase();
  const isControl = FALLBACK_CONTROL_TAGS.has(tag) || FALLBACK_CONTROL_ROLES.has(role);
  const isLink = tag === "A" || role === "link";
  if (!isControl && !isLink) return "";
  const text = cleanText(el?.textContent);
  if (!text) return "";
  if (text.length > FALLBACK_LABEL_MAX_CHARS) return "";
  if (text.split(/\s+/).filter(Boolean).length > FALLBACK_LABEL_MAX_WORDS) return "";
  return text;
}

function labelForControl(el) {
  if (!(el instanceof Element)) return "";
  if (isSensitiveControl(el)) return "secure field";
  const aria = cleanText(el.getAttribute("aria-label"));
  if (aria) return aria;
  const title = cleanText(el.getAttribute("title"));
  if (title) return title;
  const placeholder = cleanText(el.getAttribute("placeholder"));
  if (placeholder) return placeholder;
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" ");
    if (cleanText(text)) return cleanText(text);
  }
  if (el.id) {
    try {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab && cleanText(lab.textContent)) return cleanText(lab.textContent);
    } catch (_) {}
  }
  const closestLabel = el.closest?.("label");
  if (closestLabel && cleanText(closestLabel.textContent)) return cleanText(closestLabel.textContent);

  // textContent is only a fallback. Modern apps often make an entire mail,
  // record, or chat row clickable; capturing the row body as a "label" leaks
  // far more content than workflow inference needs. Keep short control/link
  // labels and otherwise rely on action + role + page context.
  return safeFallbackControlText(el);
}

function pathElements(eventOrStart) {
  if (eventOrStart?.composedPath) {
    return eventOrStart.composedPath().filter(x => x instanceof Element);
  }
  const out = [];
  let el = eventOrStart instanceof Element ? eventOrStart : eventOrStart?.parentElement;
  while (el && out.length < 20) { out.push(el); el = el.parentElement; }
  return out;
}

function semanticTarget(eventOrStart) {
  const path = pathElements(eventOrStart);
  for (const el of path) {
    const tag = String(el.tagName || "").toLowerCase();
    const role = cleanText(el.getAttribute?.("role"), 80);
    const type = cleanText(el.getAttribute?.("type"), 80);
    const name = cleanText(el.getAttribute?.("name"), 100);
    const contenteditable = el.getAttribute?.("contenteditable") === "true" || el.isContentEditable === true;
    const label = labelForControl(el);
    const isInteractive = ["button","a","input","select","textarea","summary","option"].includes(tag) ||
      contenteditable || ["button","link","textbox","searchbox","combobox","tab","menuitem","option","checkbox","radio","switch","slider","spinbutton"].includes(role);
    if (isInteractive || label) {
      return {
        tag,
        role,
        type: isSensitiveControl(el) ? "secure" : type,
        name: isSensitiveControl(el) ? "" : name,
        label,
        contenteditable: !!contenteditable
      };
    }
  }
  return {};
}

function targetKey(target) {
  return [target.tag, target.role, target.type, target.name, target.label].map(x => String(x || "")).join("|");
}

function send(action, target = {}, metadata = {}) {
  try {
    const topFrame = isTopFrame();
    const maybePromise = ext.runtime.sendMessage({
      type: "workflow_observer_event",
      observed_at: new Date().toISOString(),
      action,
      page: {url: safePageUrl(location.href), title: document.title},
      target,
      metadata: {...metadata, top_frame: topFrame, frame_kind: topFrame ? "top" : "subframe"}
    });
    if (maybePromise?.catch) maybePromise.catch(() => {});
  } catch (_) {}
}

function sendNavigation(reason) {
  // Automatic subframe navigation is mostly ad/SSO/login noise and can expose
  // unrelated embedded origins. User interactions inside subframes are still
  // captured and attributed to the top-level tab by background.js.
  if (!isTopFrame()) return;
  const now = Date.now();
  if (location.href === lastHref && now - lastNavAt < 300) return;
  lastHref = location.href;
  lastNavAt = now;
  send("page_view", {}, {reason});
}

sendNavigation("document_start");

addEventListener("pointerdown", (e) => {
  if (e.button !== undefined && e.button > 2) return;
  const target = semanticTarget(e);
  const key = targetKey(target);
  lastPointer = {at: Date.now(), key};
  send(e.button === 2 ? "right_click" : "click", target, {phase: "pointerdown"});
}, true);

addEventListener("click", (e) => {
  const target = semanticTarget(e);
  const key = targetKey(target);
  if (Date.now() - lastPointer.at < 900 && key === lastPointer.key) return;
  send("click", target, {phase: "click"});
}, true);

addEventListener("focusin", (e) => {
  const target = semanticTarget(e);
  const role = String(target.role || "");
  const tag = String(target.tag || "");
  const editable = !!target.contenteditable || ["input","textarea","select"].includes(tag) || ["textbox","searchbox","combobox"].includes(role);
  if (!editable) return;
  const key = targetKey(target);
  if (Date.now() - lastFocus.at < 700 && key === lastFocus.key) return;
  lastFocus = {at: Date.now(), key};
  send("focus_control", target);
}, true);

addEventListener("submit", (e) => send("form_submit", semanticTarget(e)), true);
addEventListener("change", (e) => send("control_change", semanticTarget(e)), true);
addEventListener("copy", (e) => send("copy", semanticTarget(e)), true);
addEventListener("paste", (e) => send("paste", semanticTarget(e)), true);
addEventListener("popstate", () => sendNavigation("popstate"));
addEventListener("hashchange", () => sendNavigation("hashchange"));
addEventListener("DOMContentLoaded", () => sendNavigation("dom_ready"), {once: true});
addEventListener("pageshow", () => sendNavigation("pageshow"));