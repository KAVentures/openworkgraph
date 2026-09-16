const ext = globalThis.browser ?? globalThis.chrome;
let lastHref = location.href;
let lastNavAt = 0;
let lastPointer = {at: 0, key: ""};
let lastFocus = {at: 0, key: ""};

function cleanText(value, max = 160) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
}

function isSensitiveControl(el) {
  if (!(el instanceof Element)) return false;
  const type = cleanText(el.getAttribute?.("type"), 80).toLowerCase();
  const autocomplete = cleanText(el.getAttribute?.("autocomplete"), 120).toLowerCase();
  const name = cleanText(el.getAttribute?.("name"), 120).toLowerCase();
  const aria = cleanText(el.getAttribute?.("aria-label"), 160).toLowerCase();
  return type === "password" || /current-password|new-password|cc-|one-time-code/.test(autocomplete) || /password|passcode|otp|security code/.test(`${name} ${aria}`);
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

  const interactive = el.matches?.("button,a,[role='button'],[role='link'],[role='tab'],[role='menuitem'],[role='option'],summary,option") ||
    ["BUTTON", "A", "SUMMARY", "OPTION"].includes(el.tagName);
  return interactive ? cleanText(el.textContent) : "";
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
    const maybePromise = ext.runtime.sendMessage({
      type: "workflow_observer_event",
      observed_at: new Date().toISOString(),
      action,
      page: {url: location.href, title: document.title},
      target,
      metadata: {...metadata, frame_url: location.href, top_frame: window.top === window}
    });
    if (maybePromise?.catch) maybePromise.catch(() => {});
  } catch (_) {}
}

function sendNavigation(reason) {
  const now = Date.now();
  if (location.href === lastHref && now - lastNavAt < 300) return;
  lastHref = location.href;
  lastNavAt = now;
  send("page_view", {}, {reason});
}

// At document_start, register the visit immediately. This does not wait for
// DOMContentLoaded, scrolling, clicking, or a dwell threshold. The URL itself is
// enough for the background/server to classify the work surface.
sendNavigation("document_start");

// Capture before a click can trigger navigation/unmount. This removes dwell-time dependence.
addEventListener("pointerdown", (e) => {
  if (e.button !== undefined && e.button > 2) return;
  const target = semanticTarget(e);
  const key = targetKey(target);
  lastPointer = {at: Date.now(), key};
  send(e.button === 2 ? "right_click" : "click", target, {phase: "pointerdown"});
}, true);

// Keyboard-activated controls (Enter/Space) may fire click without pointerdown.
addEventListener("click", (e) => {
  const target = semanticTarget(e);
  const key = targetKey(target);
  if (Date.now() - lastPointer.at < 900 && key === lastPointer.key) return;
  send("click", target, {phase: "click"});
}, true);

// Focusing an editor/input is useful workflow evidence, but values are never captured.
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

for (const method of ["pushState", "replaceState"]) {
  const original = history[method];
  history[method] = function(...args) {
    const result = original.apply(this, args);
    queueMicrotask(() => sendNavigation(method));
    return result;
  };
}

// Fallback only; semantic actions above do not depend on this timer.
setInterval(() => {
  if (location.href !== lastHref) sendNavigation("url_poll");
}, 1000);
