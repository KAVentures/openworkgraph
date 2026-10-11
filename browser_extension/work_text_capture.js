/* Opt-in browser text sensor. Never active before the user enables it.
 * Page and editable-draft snapshots are sent only to the paired local browser
 * background. They never enter OWG's durable structural event queue.
 */
(() => {
  const ext = globalThis.browser ?? globalThis.chrome;
  const KEY = "openworkgraph_work_text_capture_v1";
  const EXCLUSIONS_KEY = "openworkgraph_work_text_exclusions_v1";
  let exclusions = {hosts: [], titles: []};
  let enabled = false;
  let pageTimer = null;
  let draftTimer = null;
  let lastPage = 0;
  let lastDraft = 0;
  let latestDraft = null;
  let pageCoverage = {};
  const DENY = /(?:password|passcode|one.time|security.code|api.key|secret|credential|login|sign.in|mfa|2fa|bank|payment|credit.card|patient|medical)/i;
  const DENY_SELECTOR = 'script,style,noscript,template,svg,iframe,input,textarea,select,option,[hidden],[aria-hidden="true"],[data-private],[data-sensitive],[contenteditable]';
  const MAX_TEXT = 4000;

  function normal(value, max=MAX_TEXT) {
    return String(value || '').replace(/[\u0000-\u001F\u007F]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
  }

  function isTop() {
    try { return window.top === window; } catch (_) { return false; }
  }

  function sensitiveSurface() {
    const host = location.hostname.toLowerCase();
    if (['accounts.google.com', 'login.microsoftonline.com', 'account.microsoft.com',
         '1177.se', 'paypal.com', 'stripe.com', 'bankid.se', 'bankid.com'].some(
           blocked => host === blocked || host.endsWith('.' + blocked))) return true;
    const title = String(document.title || "").toLowerCase();
    if (exclusions.hosts.some(raw => {
      const input = String(raw || "").toLowerCase().trim();
      const pattern = input.startsWith("*.") ? input.slice(2) : input;
      return pattern && (host === pattern || host.endsWith("." + pattern));
    })) return true;
    if (exclusions.titles.some(raw => String(raw || "").trim() &&
        title.includes(String(raw).toLowerCase()))) return true;
    if (DENY.test(location.hostname)) return true;
    const components = location.pathname.toLowerCase().split(/[/._-]+/);
    if (components.some(part => [
      'auth','authenticate','oauth','authorize','login','signin','password','reset',
      'recover','recovery','mfa','2fa','payment','checkout','token','patient',
      'patients','medical','health','ehr','emr','journal','banking'
    ].includes(part))) return true;
    return DENY.test(document.title);
  }

  function safeElement(el) {
    if (!el || !el.closest) return false;
    const blocked = el.closest(DENY_SELECTOR);
    if (blocked) return false;
    for (let cur=el, count=0; cur && count < 12; cur=cur.parentElement, count++) {
      if (DENY.test([cur.getAttribute?.('name'),cur.getAttribute?.('id'),
        cur.getAttribute?.('aria-label'),cur.getAttribute?.('autocomplete'),
        cur.getAttribute?.('placeholder')].filter(Boolean).join(' '))) return false;
    }
    return true;
  }

  function visiblePageText() {
    if (!document.body || sensitiveSurface()) return '';
    // Sample rendered viewport elements first so a long conversation's old
    // DOM prefix cannot consume the entire scan budget before its visible tail.
    const roots = new Set();
    if (document.elementFromPoint) {
      for (const x of [0.25, 0.5, 0.75]) for (const y of [0.15, 0.4, 0.65, 0.9]) {
        const el = document.elementFromPoint(window.innerWidth * x, window.innerHeight * y);
        if (el && el !== document.body && el !== document.documentElement && safeElement(el)) roots.add(el);
      }
    }
    roots.add(document.body);
    const walkers = [...roots].map(root => document.createTreeWalker(root, NodeFilter.SHOW_TEXT));
    const seen = new Set();
    let walkerIndex=0;
    const parts = [];
    const viewport = [];
    let size=0;
    let inspected=0;
    let nodeTruncated=false;
    while (inspected < 2500) {
      const node = walkers[walkerIndex]?.nextNode();
      if (!node) {
        if (++walkerIndex >= walkers.length) break;
        continue;
      }
      if (seen.has(node)) continue;
      seen.add(node);
      inspected++;
      const el = node.parentElement;
      if (!safeElement(el)) continue;
      const value = normal(node.nodeValue, MAX_TEXT);
      nodeTruncated ||= normal(node.nodeValue, MAX_TEXT + 1).length > MAX_TEXT;
      if (!value) continue;
      try {
        // A styled child can appear "display:inline" beneath a hidden parent.
        // getClientRects() is empty if any ancestor prevents rendering.
        if (!el.getClientRects?.().length) continue;
        const style = getComputedStyle(el);
        if (style?.display === 'none' || style?.visibility === 'hidden' ||
            style?.visibility === 'collapse' || style?.opacity === '0') continue;
      } catch (_) { continue; }
      let rect;
      try { rect = el.getBoundingClientRect?.(); } catch (_) {}
      if (rect && rect.bottom > 0 && rect.top < window.innerHeight) viewport.push(value);
      else parts.push(value);
      size += value.length + 1;
      if (size >= MAX_TEXT * 8) break;
    }
    const text = [...viewport, ...parts].join(' ');
    pageCoverage = {selection: 'viewport_then_page', inspected_characters: size,
      capture_truncated: nodeTruncated || text.length > MAX_TEXT || inspected >= 2500 || size >= MAX_TEXT * 8};
    return normal(text);
  }

  function emit(kind, text, context = {}) {
    if (!enabled || !isTop() || document.visibilityState !== 'visible' ||
        sensitiveSurface() || !text) return;
    try {
      ext.runtime.sendMessage({
        type: 'owg_work_text_snapshot',
        kind,
        text: normal(text),
        hostname: location.hostname,
        pathname: location.pathname,
        title: document.title,
        context
      }).catch?.(() => {});
    } catch (_) {}
  }

  function capturePage() {
    pageTimer = null;
    if (!enabled || !isTop() || Date.now() - lastPage < 20000) return;
    const text = visiblePageText();
    if (!text) return;
    lastPage = Date.now();
    emit('page', text, pageCoverage);
  }

  function schedulePage() {
    if (!enabled || !isTop() || pageTimer) return;
    pageTimer = setTimeout(capturePage, 2400);
  }

  function editableText(target) {
    if (!target || sensitiveSurface()) return '';
    const element = target.closest?.('textarea,[contenteditable="true"],[role="textbox"]');
    if (!element) return '';
    const tag = String(element.tagName || '').toUpperCase();
    const type = String(element.getAttribute?.('type') || '').toLowerCase();
    if (type && type !== 'text') return '';
    if (element.closest?.('form')?.querySelector?.('input[type="password"]')) return '';
    const descriptors = [element.getAttribute?.('name'), element.getAttribute?.('id'),
      element.getAttribute?.('autocomplete'), element.getAttribute?.('aria-label'),
      element.getAttribute?.('placeholder')].filter(Boolean).join(' ');
    if (DENY.test(descriptors) || element.getAttribute?.('data-private') !== null ||
        element.getAttribute?.('aria-hidden') === 'true') return '';
    const text = tag === 'TEXTAREA' ? element.value : element.innerText;
    return {text: normal(text), context: {selection: 'editable_draft',
      inspected_characters: String(text || '').length,
      capture_truncated: String(text || '').length > MAX_TEXT}};
  }

  function scheduleDraft(target) {
    if (!enabled || !isTop()) return;
    const text = editableText(target);
    if (!text || !text.text) return;
    latestDraft = text;
    if (draftTimer) clearTimeout(draftTimer);
    // Debounce until typing stops, then respect the cooldown without
    // discarding the final draft when no further input event follows.
    const cooldown = Math.max(0, 4500 - (Date.now() - lastDraft));
    draftTimer = setTimeout(() => {
      draftTimer = null;
      if (!enabled || !latestDraft) return;
      const finalText = latestDraft;
      latestDraft = null;
      lastDraft = Date.now();
      emit('draft', finalText.text, finalText.context);
    }, Math.max(2200, cooldown));
  }

  function setEnabled(value) {
    const next = value === true;
    if (enabled === next) return;
    enabled = next;
    if (!enabled) {
      if (pageTimer) clearTimeout(pageTimer);
      if (draftTimer) clearTimeout(draftTimer);
      pageTimer = draftTimer = latestDraft = null;
    } else {
      schedulePage();
    }
  }

  if (!isTop()) return;
  ext.storage.local.get([KEY, EXCLUSIONS_KEY]).then(result => {
    const value = result?.[EXCLUSIONS_KEY] || {};
    exclusions = {
      hosts: Array.isArray(value.hosts) ? value.hosts : [],
      titles: Array.isArray(value.titles) ? value.titles : []
    };
    setEnabled(result?.[KEY] === true);
  }).catch(() => setEnabled(false));
  ext.storage.onChanged?.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes?.[EXCLUSIONS_KEY]) {
      const value = changes[EXCLUSIONS_KEY].newValue || {};
      exclusions = {
        hosts: Array.isArray(value.hosts) ? value.hosts : [],
        titles: Array.isArray(value.titles) ? value.titles : []
      };
    }
    if (changes?.[KEY]) setEnabled(changes[KEY].newValue === true);
  });
  addEventListener('DOMContentLoaded', schedulePage);
  addEventListener('pageshow', schedulePage);
  addEventListener('click', schedulePage, true);
  addEventListener('scroll', schedulePage, true);
  addEventListener('input', event => scheduleDraft(event.target), true);
  // At document_start some browsers have not created documentElement yet.
  // Do not crash the entire sensor on those pages.
  const observer = new MutationObserver(schedulePage);
  function observeDocument() {
    if (document.documentElement) {
      observer.observe(document.documentElement, {subtree:true, childList:true});
      schedulePage();
    }
  }
  if (document.documentElement) observeDocument();
  else addEventListener('DOMContentLoaded', observeDocument, {once: true});
})();
