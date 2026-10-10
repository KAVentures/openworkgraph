/* Opt-in browser text sensor. Never active before the user enables it.
 * Page and editable-draft snapshots are sent only to the paired local browser
 * background. They never enter OWG's durable structural event queue.
 */
(() => {
  const ext = globalThis.browser ?? globalThis.chrome;
  const KEY = "openworkgraph_work_text_capture_v1";
  let enabled = false;
  let pageTimer = null;
  let draftTimer = null;
  let lastPage = 0;
  let lastDraft = 0;
  let latestDraft = null;
  const DENY = /(password|passcode|one.time|security.code|api.key|secret|token|auth|login|sign.in|mfa|2fa|bank|payment|credit.card|patient|medical)/i;
  const DENY_SELECTOR = 'script,style,noscript,template,svg,iframe,input,textarea,select,option,[hidden],[aria-hidden="true"],[data-private],[data-sensitive],[contenteditable]';
  const MAX_TEXT = 4000;

  function normal(value, max=MAX_TEXT) {
    return String(value || '').replace(/[\\u0000-\\u001F\\u007F]/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, max);
  }

  function isTop() {
    try { return window.top === window; } catch (_) { return false; }
  }

  function sensitiveSurface() {
    if (DENY.test(location.hostname)) return true;
    if (/(?:^|\\/)(?:login|signin|sign-in|oauth|authorize|password|reset|recovery|mfa|2fa|payment|checkout|health|patient|medical)(?:\\/|$)/i.test(location.pathname)) return true;
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
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const parts = [];
    let size=0;
    let inspected=0;
    while (inspected < 2500) {
      const node = walker.nextNode();
      if (!node) break;
      inspected++;
      const el = node.parentElement;
      if (!safeElement(el)) continue;
      const value = normal(node.nodeValue, 500);
      if (!value) continue;
      try {
        const style = getComputedStyle(el);
        if (style?.display === 'none' || style?.visibility === 'hidden') continue;
      } catch (_) { continue; }
      parts.push(value);
      size += value.length + 1;
      if (size >= MAX_TEXT) break;
    }
    return normal(parts.join(' '));
  }

  function emit(kind, text) {
    if (!enabled || !isTop() || document.visibilityState !== 'visible' ||
        sensitiveSurface() || !text) return;
    try {
      ext.runtime.sendMessage({
        type: 'owg_work_text_snapshot',
        kind,
        text: normal(text),
        hostname: location.hostname,
        pathname: location.pathname,
        title: document.title
      }).catch?.(() => {});
    } catch (_) {}
  }

  function capturePage() {
    pageTimer = null;
    if (!enabled || !isTop() || Date.now() - lastPage < 20000) return;
    const text = visiblePageText();
    if (!text) return;
    lastPage = Date.now();
    emit('page', text);
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
    return normal(text);
  }

  function scheduleDraft(target) {
    if (!enabled || !isTop()) return;
    const text = editableText(target);
    if (!text) return;
    latestDraft = text;
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      draftTimer = null;
      if (!enabled || !latestDraft || Date.now() - lastDraft < 4500) return;
      lastDraft = Date.now();
      emit('draft', latestDraft);
      latestDraft = null;
    }, 2200);
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
  ext.storage.local.get(KEY).then(result => setEnabled(result?.[KEY] === true)).catch(() => setEnabled(false));
  ext.storage.onChanged?.addListener((changes, area) => {
    if (area === 'local' && changes?.[KEY]) setEnabled(changes[KEY].newValue === true);
  });
  addEventListener('DOMContentLoaded', schedulePage);
  addEventListener('pageshow', schedulePage);
  addEventListener('click', schedulePage, true);
  addEventListener('input', event => scheduleDraft(event.target), true);
  new MutationObserver(schedulePage).observe(document.documentElement, {subtree:true, childList:true});
})();
