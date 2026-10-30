import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../dashboard/analysis_handoff.js', import.meta.url), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return {promise, resolve, reject};
};
const settle = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};

function harness(auth = Promise.resolve(true)) {
  const reads = [], tickets = [], copied = [], toasts = [], summaries = [], bars = [];
  let downloads = 0, modals = 0;
  const modal = Object.fromEntries(['#analysisRequest', '#analysisCopy', '#analysisDownload',
    '#analysisDetailed', '#analysisActionStatus'].map(key => [key, {textContent: '', checked: false}]));
  function element(tag) {
    return {
      tagName: tag.toUpperCase(), value: '', textContent: '', hidden: false, attributes: {},
      setAttribute(key, value) { this.attributes[key] = String(value); },
      removeAttribute(key) { delete this.attributes[key]; },
      appendChild() {}, remove() {},
      click() { if (tag === 'a') downloads++; }
    };
  }
  function bar() {
    const node = element('div');
    const select = element('select'), custom = element('span');
    const start = element('input'), end = element('input'), button = element('button');
    node.querySelector = key => ({select, '[data-custom]': custom,
      '[data-start]': start, '[data-end]': end, button})[key] || null;
    node.querySelectorAll = key => key === 'input' ? [start, end] : [];
    node.controls = {select, custom, start, end, button};
    return node;
  }
  const panel = {firstChild: null, insertBefore(node) {bars.push(node);}};
  const document = {
    readyState: 'loading',
    addEventListener(event, callback) { if (event === 'DOMContentLoaded') this.ready = callback; },
    createElement(tag) {return tag === 'div' ? bar() : element(tag);},
    querySelector(key) {return key === '#panel-overview' ? panel : modal[key] || null;},
    querySelectorAll(key) {
      if (key === '.owg-period') return bars;
      if (key === '[data-period-summary]') return summaries;
      return [];
    },
    dispatchEvent() {},
    head: {appendChild() {}}, body: {appendChild() {}}
  };
  const summary = element('span'); summaries.push(summary);
  const window = {
    __owgAuthReady: auth,
    toast(message) {toasts.push(message);},
    openModal() {modals++;}
  };
  const fetch = (url, options = {}) => {
    const item = {...deferred(), url: String(url), options};
    if (String(url).startsWith('/v1/analysis-preview?')) reads.push(item);
    else if (String(url) === '/v1/export-ticket') tickets.push(item);
    else throw new Error('Unexpected request: ' + url);
    return item.promise;
  };
  const navigator = {clipboard: {async writeText(value) {copied.push(value);}}};
  vm.runInNewContext(source, {window, document, fetch, navigator, Date, Intl,
    URLSearchParams, AbortController, Event: class Event {}, setTimeout});
  document.ready();
  const reply = (item, data) => item.resolve({ok: true, async json() {return data;}});
  const preview = (scope, request = scope) => ({
    request_text: 'request-' + request,
    canonical_rows: 2, retained_rows: 2,
    first_observed_at: '2026-10-29T10:00:00Z',
    last_observed_at: '2026-10-29T10:01:00Z',
    window: scope === 'all' ? {scope: 'all'} :
      {scope: 'range', since: '2026-10-23T00:00:00Z', until: '2026-10-30T00:00:00Z'},
    event_types: {focus_span: 2, capture_gap: 0}, sources: {desktop: 2},
    redacted_examples: [], analysis_plan: {date_chunks: []},
    work_evidence: {}, local_ai_enabled: false
  });
  return {reads, tickets, copied, toasts, modal, bars, summary, window,
    get modals() {return modals;}, get downloads() {return downloads;},
    reply, preview};
}

test('superseded prepare never opens a modal using a newer period response', async () => {
  const h = harness();
  await settle();
  h.reply(h.reads[0], h.preview('range', 'initial'));
  await settle();
  const prepare = h.bars[0].controls.button.onclick();
  await settle();
  assert.equal(h.reads.length, 2);
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle();
  assert.equal(h.reads.length, 3);
  h.reply(h.reads[2], h.preview('all', 'new-period'));
  await settle();
  h.reply(h.reads[1], h.preview('range', 'old-click'));
  await prepare;
  assert.equal(h.modals, 0);
  assert.equal(h.summary.textContent.includes('recorded events'), true);
  assert.deepEqual(h.toasts, []);
});

test('changing period invalidates modal copy and download; current scope can still export', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed-week')); await open;
  assert.equal(h.modals, 1);
  assert.equal(h.modal['#analysisRequest'].textContent, 'request-reviewed-week');
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle();
  await h.modal['#analysisCopy'].onclick();
  await h.modal['#analysisDownload'].onclick();
  assert.deepEqual(h.copied, []);
  assert.equal(h.tickets.length, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed/i);
  h.reply(h.reads[2], h.preview('all', 'all-period')); await settle();
  const current = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[3], h.preview('all', 'current')); await current;
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-current']);
  const download = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(h.tickets.length, 1);
  const posted = JSON.parse(h.tickets[0].options.body);
  assert.equal(posted.scope, 'all');
  assert.equal(posted.include_raw, false);
  h.reply(h.tickets[0], {url: '/v1/download-ticket/owned'});
  await download;
  assert.equal(h.downloads, 1);
});

test('late ticket cannot start download after scope change', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'week')); await open;
  const download = h.modal['#analysisDownload'].onclick();
  await settle(); assert.equal(h.tickets.length, 1);
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle();
  h.reply(h.tickets[0], {url: '/v1/download-ticket/old'});
  await download;
  assert.equal(h.downloads, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed/i);
  h.reply(h.reads[2], h.preview('all', 'new')); await settle();
});

test('old auth wait does not start a superseded evidence request', async () => {
  const auth = deferred();
  const h = harness(auth.promise);
  await settle();
  assert.equal(h.reads.length, 0);
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle();
  auth.resolve(true); await settle();
  assert.equal(h.reads.length, 1);
  assert.match(h.reads[0].url, /scope=all/);
  h.reply(h.reads[0], h.preview('all', 'authorized')); await settle();
  assert.equal(h.summary.textContent.includes('recorded events'), true);
});

test('open handoff is proactively disabled and announced stale before any click', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed-week')); await open;
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  // This is synchronous; a slow preview cannot leave old export controls active.
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed/i);
  assert.equal(h.modal['#analysisRequest'].textContent, 'request-reviewed-week');
  assert.equal(h.tickets.length, 0);
  await settle(); // the current preview begins after the async local-auth gate
  assert.equal(h.reads.length, 3);
  h.reply(h.reads[2], h.preview('all', 'all-period')); await settle();
  const again = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[3], h.preview('all', 'new-request')); await again;
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  assert.equal(h.modal['#analysisRequest'].textContent, 'request-new-request');
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-new-request']);
});
