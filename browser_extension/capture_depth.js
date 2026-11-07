/* Opt-in high-fidelity interaction signals (#634): hover dwell, drag and drop, sampled pointer movement.
 *
 * Runs after content.js and reuses its semanticTarget/isSensitiveControl/send helpers. Every signal is off
 * unless the local server's capture depth turns it on; the server enforces the same setting at ingest.
 * Never records typed text, clipboard contents, dragged data, file names or page contents.
 */
const owgDepthExt = globalThis.browser ?? globalThis.chrome;
const OWG_DEPTH_KEY = "openworkgraph_capture_depth_v1";
const OWG_DEPTH_OFF = {signals: {hover_dwell: false, drag_drop: false, pointer_trajectory: false},
  parameters: {hover_dwell_ms: 750, trajectory_hz: 4, max_events_per_minute: 60}};
let owgDepth = OWG_DEPTH_OFF;
let owgDepthWindow = {start: 0, count: 0};

function owgNormalizeDepth(value) {
  const v = value && typeof value === "object" ? value : {};
  const s = v.signals && typeof v.signals === "object" ? v.signals : {};
  const p = v.parameters && typeof v.parameters === "object" ? v.parameters : {};
  const num = (x, lo, hi, d) => Number.isInteger(x) && x >= lo && x <= hi ? x : d;
  // Detail is time-limited: without an expiry in the future, nothing beyond Standard is emitted.
  const expires = Date.parse(v.expires_at || "");
  if (!(expires > Date.now())) return OWG_DEPTH_OFF;
  return {signals: {hover_dwell: s.hover_dwell === true, drag_drop: s.drag_drop === true, pointer_trajectory: s.pointer_trajectory === true},
    parameters: {hover_dwell_ms: num(p.hover_dwell_ms, 300, 5000, 750), trajectory_hz: num(p.trajectory_hz, 1, 20, 4),
      max_events_per_minute: num(p.max_events_per_minute, 10, 600, 60)}, expiresAt: expires};
}

async function owgLoadDepth() {
  try { owgDepth = owgNormalizeDepth((await owgDepthExt.storage.local.get(OWG_DEPTH_KEY))?.[OWG_DEPTH_KEY]); }
  catch (_) { owgDepth = OWG_DEPTH_OFF; }
}
if (owgDepthExt?.storage?.onChanged) {
  owgDepthExt.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes?.[OWG_DEPTH_KEY]) { owgDepth = owgNormalizeDepth(changes[OWG_DEPTH_KEY].newValue); owgFlushTrajectory("settings_changed"); }
  });
}
owgLoadDepth();

function owgDepthAllowed() {
  const now = Date.now();
  if (owgDepth.expiresAt && now >= owgDepth.expiresAt) { owgDepth = OWG_DEPTH_OFF; return false; }
  if (now - owgDepthWindow.start > 60000) owgDepthWindow = {start: now, count: 0};
  if (owgDepthWindow.count >= owgDepth.parameters.max_events_per_minute) return false;
  owgDepthWindow.count += 1;
  return true;
}

const OWG_HOVER_TAGS = new Set(["button", "a", "input", "select", "textarea", "summary", "option"]);
const OWG_HOVER_ROLES = new Set(["button", "link", "tab", "menuitem", "menu", "menubar", "option", "checkbox", "radio", "switch", "combobox", "treeitem"]);

function owgInteractiveElement(event) {
  for (const el of pathElements(event)) {
    const tag = String(el.tagName || "").toLowerCase();
    const role = String(el.getAttribute?.("role") || "").toLowerCase();
    if (OWG_HOVER_TAGS.has(tag) || OWG_HOVER_ROLES.has(role)) return el;
  }
  return null;
}

// Hover and drag targets are structural only: tag, role and type. No label is recorded at all, because
// element text, aria-label, title, placeholder and <label> text can all hold or be set from the user's content.
function owgEditable(el) {
  for (let node = el; node instanceof Element; node = node.parentElement) {
    const tag = String(node.tagName || "").toLowerCase();
    if (node.isContentEditable === true || node.getAttribute?.("contenteditable") === "true" ||
        ["input", "textarea", "select"].includes(tag)) return true;
  }
  return false;
}

// A control is secure if it, or any element around it, is a secure field or widget.
function owgInSecureContext(el) {
  for (let node = el; node instanceof Element; node = node.parentElement) if (isSensitiveControl(node)) return true;
  return false;
}

// Secure if any element on the event path, or anything stacked under the pointer (a field hidden by an overlay), is.
function owgSecureAt(event) {
  if (pathElements(event).some(el => isSensitiveControl(el))) return true;
  try {
    if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY))
      return document.elementsFromPoint(event.clientX, event.clientY).some(el => owgInSecureContext(el));
  } catch (_) {}
  return false;
}

function owgSafeTarget(start) {
  const el = start instanceof Element ? start : (pathElements(start)[0] || null);
  if (!(el instanceof Element)) return {};
  for (let node = el; node instanceof Element; node = node.parentElement) {
    const tag = String(node.tagName || "").toLowerCase();
    const role = cleanText(node.getAttribute?.("role"), 80);
    if (OWG_HOVER_TAGS.has(tag) || OWG_HOVER_ROLES.has(role.toLowerCase()) || node.getAttribute?.("draggable") === "true") {
      const editable = owgEditable(node);
      return {tag, role, type: owgInSecureContext(node) ? "secure" : cleanText(node.getAttribute?.("type"), 80),
        ...(editable ? {editable: true} : {})};
    }
  }
  return {};
}

// --- hover dwell -------------------------------------------------------------
let owgHover = null; // {el, target, start}
const owgRecentHover = new WeakMap(); // element -> last recorded hover time

function owgEndHover(endedBy) {
  const current = owgHover;
  owgHover = null;
  if (!current || !owgDepth.signals.hover_dwell) return;
  const duration = Math.round((performance.now() - current.start) / 50) * 50;
  if (duration < owgDepth.parameters.hover_dwell_ms) return;
  const last = owgRecentHover.get(current.el) || 0;
  if (Date.now() - last < 2000) return;
  owgRecentHover.set(current.el, Date.now());
  if (!owgDepthAllowed()) return;
  send("control_hover_dwell", current.target, {duration_ms: Math.min(duration, 600000), ended_by: endedBy,
    threshold_ms: owgDepth.parameters.hover_dwell_ms, ...(current.rerendered ? {control_rerendered: true} : {})});
}

addEventListener("pointerover", (e) => {
  if (!owgDepth.signals.hover_dwell || e.pointerType === "touch") return;
  const el = owgInteractiveElement(e);
  // A single-page app can re-render the control under a resting pointer: when the hovered element
  // has left the document and an equivalent one (same tag, role and type) takes its place, the
  // hover carries on rather than restarting.
  if (owgHover && el && el !== owgHover.el && !owgHover.el.isConnected) {
    const next = owgSafeTarget(el);
    if (next.tag === owgHover.target.tag && next.role === owgHover.target.role && next.type === owgHover.target.type &&
        !owgInSecureContext(el)) { owgHover.el = el; owgHover.target = next; owgHover.rerendered = true; return; }
  }
  // Some Chromium versions skip pointerout when the pointer moves to plain page space, so
  // arriving anywhere outside the current control also ends its hover.
  if (owgHover && owgHover.el !== el && !(e.target instanceof Node && owgHover.el.contains(e.target))) owgEndHover("pointer_left");
  if (!el || (owgHover && owgHover.el === el)) return;
  if (owgInSecureContext(el) || owgSecureAt(e) || el.disabled === true || el.getAttribute?.("aria-disabled") === "true") return;
  const target = owgSafeTarget(el);
  owgHover = {el, target, start: performance.now()};
}, true);
addEventListener("pointerout", (e) => {
  if (!owgHover) return;
  const next = e.relatedTarget;
  if (next instanceof Node && owgHover.el.contains(next)) return;
  owgEndHover("pointer_left");
}, true);
addEventListener("pointerdown", () => { if (owgHover) owgEndHover("activated"); }, true);
addEventListener("visibilitychange", () => { if (document.hidden && owgHover) owgEndHover("page_hidden"); });

// --- drag and drop -------------------------------------------------------------
let owgDrag = null; // {id, source, dropped}

function owgPayloadKinds(dataTransfer) {
  // Only the declared kinds, never the data itself.
  const kinds = new Set();
  for (const type of Array.from(dataTransfer?.types || [])) {
    const t = String(type).toLowerCase();
    if (t === "files") kinds.add("files");
    else if (t === "text/plain") kinds.add("text");
    else if (t === "text/html") kinds.add("html");
    else if (t === "text/uri-list" || t === "url") kinds.add("uri");
    else kinds.add("other");
  }
  return Array.from(kinds).slice(0, 6);
}

addEventListener("dragstart", (e) => {
  if (!owgDepth.signals.drag_drop) return;
  if (owgInSecureContext(e.target instanceof Element ? e.target : null) || owgSecureAt(e)) { owgDrag = null; return; }
  const source = owgSafeTarget(e.target);
  owgDrag = {id: Math.random().toString(36).slice(2, 12), source, dropped: false};
  if (owgDepthAllowed()) send("drag_start", source, {drag_id: owgDrag.id, payload_kinds: owgPayloadKinds(e.dataTransfer), completion: "unknown"});
}, true);
addEventListener("drop", (e) => {
  if (!owgDepth.signals.drag_drop) return;
  if (owgInSecureContext(e.target instanceof Element ? e.target : null) || owgSecureAt(e)) return;
  const destination = owgSafeTarget(e.target);
  const kinds = owgPayloadKinds(e.dataTransfer);
  // A drop with no drag in this page came from elsewhere (another window or the desktop).
  const id = owgDrag ? owgDrag.id : "external";
  if (owgDrag) owgDrag.dropped = true;
  if (owgDepthAllowed()) send("drag_drop", destination, {drag_id: id, payload_kinds: owgDrag ? kinds : (kinds.length ? kinds : ["other"]),
    completion: "complete", source_target: owgDrag ? owgDrag.source : {}});
}, true);
addEventListener("dragend", (e) => {
  if (!owgDepth.signals.drag_drop || !owgDrag) return;
  const finished = owgDrag;
  owgDrag = null;
  const effect = String(e.dataTransfer?.dropEffect || "");
  const completion = finished.dropped || (effect && effect !== "none") ? "complete" : "incomplete";
  if (owgDepthAllowed()) send("drag_end", finished.source, {drag_id: finished.id, payload_kinds: [], completion});
}, true);

// --- sampled pointer trajectory (Full fidelity) -----------------------------------
let owgTrajectory = {start: 0, points: [], lastSample: 0};

function owgFlushTrajectory() {
  const batch = owgTrajectory;
  owgTrajectory = {start: 0, points: [], lastSample: 0};
  if (!batch.points.length || !owgDepth.signals.pointer_trajectory) return;
  if (!owgDepthAllowed()) return;
  const w = Math.max(1, innerWidth), h = Math.max(1, innerHeight);
  send("pointer_trajectory", {}, {points: batch.points, sample_hz: owgDepth.parameters.trajectory_hz,
    viewport_bucket: `${Math.round(w / 100) * 100}x${Math.round(h / 100) * 100}`});
}

addEventListener("pointermove", (e) => {
  if (!owgDepth.signals.pointer_trajectory || e.pointerType === "touch") return;
  const now = performance.now();
  if (now - owgTrajectory.lastSample < 1000 / owgDepth.parameters.trajectory_hz) return;
  if (owgSecureAt(e)) {
    // Never sample over a secure field, even one under an overlay: end the batch here and skip until it is left.
    owgFlushTrajectory();
    return;
  }
  if (!owgTrajectory.start) owgTrajectory.start = now;
  owgTrajectory.lastSample = now;
  const x = Math.min(1, Math.max(0, e.clientX / Math.max(1, innerWidth)));
  const y = Math.min(1, Math.max(0, e.clientY / Math.max(1, innerHeight)));
  owgTrajectory.points.push([Math.round(now - owgTrajectory.start), Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
  if (owgTrajectory.points.length >= 200 || now - owgTrajectory.start > 10000) owgFlushTrajectory();
}, true);
addEventListener("visibilitychange", () => { if (document.hidden) owgFlushTrajectory(); });
addEventListener("pagehide", () => owgFlushTrajectory());
