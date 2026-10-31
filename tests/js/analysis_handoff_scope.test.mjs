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

// The dashboard selects local calendar days. Keep fixture observations inside
// that selected week on every CI date and timezone, not only October 2026.
const fixtureDay = new Date();
const observedStart = new Date(fixtureDay.getFullYear(), fixtureDay.getMonth(),
  fixtureDay.getDate(), 10, 0, 0).toISOString();
const observedEnd = new Date(fixtureDay.getFullYear(), fixtureDay.getMonth(),
  fixtureDay.getDate(), 10, 1, 0).toISOString();
const dayLabel = delta => {
  const d = new Date(fixtureDay.getFullYear(), fixtureDay.getMonth(), fixtureDay.getDate());
  d.setDate(d.getDate() + delta);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
function harness(auth = Promise.resolve(true), clipboardWrite = null) {
  const reads = [], tickets = [], copied = [], toasts = [], summaries = [], bars = [];
  let downloads = 0, modals = 0, modalHtml = '';
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
    location: {origin: 'http://localhost:8765'},
    __owgAuthReady: auth,
    toast(message) {toasts.push(message);},
    openModal(_title, _subtitle, html) {modals++; modalHtml = html;}
  };
  const fetch = (url, options = {}) => {
    const item = {...deferred(), url: String(url), options};
    if (String(url).startsWith('/v1/analysis-preview?')) reads.push(item);
    else if (String(url) === '/v1/export-ticket') tickets.push(item);
    else throw new Error('Unexpected request: ' + url);
    return item.promise;
  };
  const navigator = {clipboard: {async writeText(value) {
    if (clipboardWrite) return clipboardWrite(value);
    copied.push(value);
  }}};
  vm.runInNewContext(source, {window, document, fetch, navigator, Date, Intl,
    URL, URLSearchParams, AbortController, Event: class Event {}, setTimeout});
  document.ready();
  const reply = (item, data, {preserveWindow = false, preserveTicket = false} = {}) => {
    // The real backend echoes the selected scope and normalizes UTC Z to +00:00.
    // Preserve deliberately forged windows only for adversarial response tests.
    if (!preserveWindow && item.url.startsWith('/v1/analysis-preview?')) {
      const params = new URLSearchParams(item.url.split('?')[1]);
      const scope = params.get('scope');
      const bound = key => params.get(key)?.replace(/Z$/, '+00:00');
      data = {...data, window: scope === 'range'
        ? {scope, since: bound('since'), until: bound('until')} : {scope}};
    }
    if (!preserveTicket && item.url === '/v1/export-ticket') {
      const body = JSON.parse(item.options.body), ticket = 'synthetic-ticket';
      const params = {scope: body.scope, include_raw: String(body.include_raw),
        redact_names: String(body.redact_names), ticket};
      if (body.scope === 'range') {
        params.since = body.since;
        params.until = body.until;
      }
      data = {...data, ticket, url: '/v1/export/' + body.format + '?' +
        new URLSearchParams(params)};
    }
    item.resolve({ok: true, async json() {return data;}});
  };
  const preview = (scope, request = scope) => ({
    request_text: 'request-' + request,
    canonical_rows: 2, retained_rows: 2,
    first_observed_at: observedStart,
    last_observed_at: observedEnd,
    window: scope === 'all' ? {scope: 'all'} :
      {scope: 'range', since: '2026-10-23T00:00:00Z', until: '2026-10-30T00:00:00Z'},
    event_types: {focus_span: 2, capture_gap: 0}, sources: {desktop: 2},
    redacted_examples: [], analysis_plan: {date_chunks: []},
    work_evidence: {}, local_ai_enabled: false
  });
  return {reads, tickets, copied, toasts, modal, bars, summary, window,
    get modals() {return modals;}, get downloads() {return downloads;},
    get modalHtml() {return modalHtml;},
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
  assert.equal(h.modal['#analysisDetailed'].disabled, true);
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

test('preview for a different scope is refused before opening or exporting', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const prepare = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[1], h.preview('all', 'wrong-scope'), {preserveWindow: true});
  await prepare;
  assert.equal(h.modals, 0);
  assert.equal(h.tickets.length, 0);
  assert.match(h.summary.textContent, /period did not match/i);
  assert.equal(h.toasts.length, 1);
});

test('shifted range bounds are refused but UTC-equivalent server bounds pass', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const bad = h.bars[0].controls.button.onclick();
  await settle();
  const selected = new URLSearchParams(h.reads[1].url.split('?')[1]);
  const shifted = new Date(Date.parse(selected.get('since')) + 3600_000).toISOString();
  h.reply(h.reads[1], {...h.preview('range', 'wrong-range'), window: {
    scope: 'range', since: shifted, until: selected.get('until')
  }}, {preserveWindow: true});
  await bad;
  assert.equal(h.modals, 0);
  assert.match(h.summary.textContent, /period did not match/i);
  const good = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[2], h.preview('range', 'correct-range'));
  await good;
  assert.equal(h.modals, 1);
  assert.equal(h.modal['#analysisRequest'].textContent, 'request-correct-range');
});

test('impossible custom dates cannot silently roll into another evidence period', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const controls = h.bars[0].controls;
  controls.select.value = 'custom';
  controls.select.onchange({target: controls.select});
  await settle();
  const previousReads = h.reads.length;
  controls.start.value = '2026-02-30';
  controls.end.value = '2026-03-03';
  controls.start.onchange();
  await settle();
  assert.equal(h.reads.length, previousReads);
  assert.match(h.summary.textContent, /valid dates/i);
  assert.throws(() => h.window.owgEvidenceSelection(), /valid dates/i);
  assert.equal(h.tickets.length, 0);
});

test('double-clicking Download issues one ticket and announces local pending work', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const first = h.modal['#analysisDownload'].onclick();
  const second = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(h.tickets.length, 1);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  assert.equal(h.modal['#analysisDetailed'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /preparing.*without detailed chronological evidence.*locally/i);
  h.reply(h.tickets[0], {url: '/v1/download-ticket/single'});
  await Promise.all([first, second]);
  assert.equal(h.downloads, 1);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  assert.equal(h.modal['#analysisDetailed'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /summary evidence only/i);
  assert.match(h.modal['#analysisActionStatus'].textContent, /download started/i);
});

test('detailed-evidence choice is frozen and accurately reported for a pending ticket', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  h.modal['#analysisDetailed'].checked = true;
  const download = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(h.tickets.length, 1);
  assert.equal(JSON.parse(h.tickets[0].options.body).include_raw, true);
  assert.equal(h.modal['#analysisDetailed'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /with detailed chronological evidence locally/i);
  // Even a programmatic DOM mutation cannot rewrite the already-issued ticket.
  h.modal['#analysisDetailed'].checked = false;
  h.reply(h.tickets[0], {url: '/ignored-by-valid-fixture'});
  await download;
  assert.equal(h.downloads, 1);
  assert.equal(h.modal['#analysisDetailed'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /detailed chronology included/i);
});

test('stale pending download never re-enables detailed-evidence option', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  h.modal['#analysisDetailed'].checked = true;
  const download = h.modal['#analysisDownload'].onclick();
  await settle();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  assert.equal(h.modal['#analysisDetailed'].disabled, true);
  h.reply(h.tickets[0], {url: '/ignored-by-stale-response'});
  await download;
  assert.equal(h.downloads, 0);
  assert.equal(h.modal['#analysisDetailed'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed/i);
  await settle(); h.reply(h.reads[2], h.preview('all', 'new')); await settle();
  const again = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[3], h.preview('all', 'new-request')); await again;
  assert.equal(h.modal['#analysisDetailed'].disabled, false);
  assert.equal(h.modal['#analysisDetailed'].checked, false);
});

test('failed export ticket restores retry without duplicate downloads', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const first = h.modal['#analysisDownload'].onclick();
  await settle(); assert.equal(h.tickets.length, 1);
  h.tickets[0].reject(new Error('Ticket unavailable'));
  await first;
  assert.equal(h.downloads, 0);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /ticket unavailable/i);
  const retry = h.modal['#analysisDownload'].onclick();
  await settle(); assert.equal(h.tickets.length, 2);
  h.reply(h.tickets[1], {url: '/v1/download-ticket/retry'});
  await retry;
  assert.equal(h.downloads, 1);
});

test('late clipboard rejection cannot replace a period-change warning', async () => {
  const pendingClipboard = deferred();
  const h = harness(Promise.resolve(true), () => pendingClipboard.promise);
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const copy = h.modal['#analysisCopy'].onclick();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  pendingClipboard.reject(new Error('Clipboard permission denied'));
  await copy;
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed/i);
  assert.match(h.modal['#analysisActionStatus'].textContent, /clipboard write failed/i);
  assert.deepEqual(h.copied, []);
  assert.equal(h.tickets.length, 0);
});


test('pending Copy blocks Download until the same-scope clipboard settles', async () => {
  const pendingClipboard = deferred(), writes = [];
  const h = harness(Promise.resolve(true), value => {
    writes.push(value);
    return pendingClipboard.promise;
  });
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const copy = h.modal['#analysisCopy'].onclick();
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  await h.modal['#analysisDownload'].onclick();
  assert.equal(h.tickets.length, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /copying.*locally/i);
  pendingClipboard.resolve();
  await copy;
  assert.deepEqual(writes, ['request-reviewed']);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  const download = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(h.tickets.length, 1);
  h.reply(h.tickets[0], {});
  await download;
  assert.equal(h.downloads, 1);
  assert.match(h.modal['#analysisActionStatus'].textContent, /download started/i);
});

test('pending Download blocks Copy without replacing ticket progress', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const download = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(h.tickets.length, 1);
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, []);
  assert.match(h.modal['#analysisActionStatus'].textContent, /preparing.*locally/i);
  h.tickets[0].reject(new Error('Ticket unavailable'));
  await download;
  assert.equal(h.downloads, 0);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-reviewed']);
  assert.match(h.modal['#analysisActionStatus'].textContent, /request copied/i);
});

test('period change during pending Copy keeps both actions disabled after completion', async () => {
  const pendingClipboard = deferred();
  const h = harness(Promise.resolve(true), () => pendingClipboard.promise);
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const copy = h.modal['#analysisCopy'].onclick();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  pendingClipboard.resolve();
  await copy;
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  assert.equal(h.tickets.length, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /previous period/i);
});

test('ticket URL must preserve exact selected scope, privacy flags and local endpoint', async t => {
  const attacks = [
    ['external origin', url => 'https://example.invalid' + url],
    ['protocol-relative origin', url => '//example.invalid' + url],
    ['wrong scope', url => url.replace('scope=range', 'scope=all')],
    ['unredacted export', url => url.replace('redact_names=true', 'redact_names=false')],
    ['unexpected detailed export', url => url.replace('include_raw=false', 'include_raw=true')],
    ['wrong date bound', url => {
      const u = new URL(url, 'http://localhost:8765');
      u.searchParams.set('since', new Date(Date.parse(u.searchParams.get('since')) + 3600000).toISOString());
      return u.pathname + u.search;
    }],
    ['duplicate scope parameter', url => url + '&scope=range'],
    ['unexpected parameter', url => url + '&redirect=https%3A%2F%2Fexample.invalid'],
    ['wrong endpoint', url => url.replace('/v1/export/csvzip?', '/v1/export/json?')],
    ['fragment', url => url + '#other']
  ];
  for (const [name, mutate] of attacks) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
    const download = h.modal['#analysisDownload'].onclick();
    await settle(); assert.equal(h.tickets.length, 1);
    const body = JSON.parse(h.tickets[0].options.body);
    const params = {scope: body.scope, include_raw: String(body.include_raw),
      redact_names: String(body.redact_names), ticket: 'synthetic-ticket',
      since: body.since, until: body.until};
    const valid = '/v1/export/csvzip?' + new URLSearchParams(params);
    h.reply(h.tickets[0], {ticket: 'synthetic-ticket', url: mutate(valid)},
      {preserveTicket: true});
    await download;
    assert.equal(h.downloads, 0);
    assert.equal(h.modal['#analysisDownload'].disabled, false);
    assert.match(h.modal['#analysisActionStatus'].textContent, /ticket did not match/i);
  });
});

test('ticket token disagreement fails closed, while UTC-equivalent detailed export works', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  h.modal['#analysisDetailed'].checked = true;
  const first = h.modal['#analysisDownload'].onclick();
  await settle();
  const body = JSON.parse(h.tickets[0].options.body);
  assert.equal(body.include_raw, true);
  assert.equal(body.redact_names, true);
  const params = {scope: body.scope, include_raw: 'true', redact_names: 'true',
    ticket: 'token-in-url', since: body.since, until: body.until};
  h.reply(h.tickets[0], {ticket: 'different-token',
    url: '/v1/export/csvzip?' + new URLSearchParams(params)}, {preserveTicket: true});
  await first;
  assert.equal(h.downloads, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /ticket did not match/i);
  const retry = h.modal['#analysisDownload'].onclick();
  await settle();
  const secondBody = JSON.parse(h.tickets[1].options.body);
  const second = new URLSearchParams({scope: secondBody.scope, include_raw: 'true',
    redact_names: 'true', ticket: 'matching-token',
    since: new Date(secondBody.since).toISOString(),
    until: new Date(secondBody.until).toISOString()});
  h.reply(h.tickets[1], {ticket: 'matching-token',
    url: '/v1/export/csvzip?' + second}, {preserveTicket: true});
  await retry;
  assert.equal(h.downloads, 1);
  assert.match(h.modal['#analysisActionStatus'].textContent, /download started/i);
});

test('repeated Copy clicks issue one pending clipboard write and announce local progress', async () => {
  const pendingClipboard = deferred(), writes = [];
  const h = harness(Promise.resolve(true), value => {
    writes.push(value);
    return pendingClipboard.promise;
  });
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const first = h.modal['#analysisCopy'].onclick();
  const second = h.modal['#analysisCopy'].onclick();
  await settle();
  assert.deepEqual(writes, ['request-reviewed']);
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /copying.*locally.*no ai was invoked/i);
  pendingClipboard.resolve();
  await Promise.all([first, second]);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /request copied/i);
  assert.equal(h.tickets.length, 0);
});

test('clipboard rejection allows deliberate single-flight retry for current scope', async () => {
  const writes = [], attempts = [deferred(), deferred()];
  const h = harness(Promise.resolve(true), value => {
    writes.push(value);
    return attempts[writes.length - 1].promise;
  });
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const first = h.modal['#analysisCopy'].onclick();
  await settle();
  attempts[0].reject(new Error('clipboard denied'));
  await first;
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /clipboard unavailable/i);
  const retry = h.modal['#analysisCopy'].onclick();
  const duplicate = h.modal['#analysisCopy'].onclick();
  await settle();
  assert.deepEqual(writes, ['request-reviewed', 'request-reviewed']);
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  attempts[1].resolve();
  await Promise.all([retry, duplicate]);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /request copied/i);
  assert.equal(h.tickets.length, 0);
});

test('successful pending Copy after period change remains disabled and discloses old scope', async () => {
  const pendingClipboard = deferred(), writes = [];
  const h = harness(Promise.resolve(true), value => {
    writes.push(value);
    return pendingClipboard.promise;
  });
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await open;
  const copy = h.modal['#analysisCopy'].onclick();
  await settle();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  pendingClipboard.resolve();
  await copy;
  assert.deepEqual(writes, ['request-reviewed']);
  assert.equal(h.modal['#analysisCopy'].disabled, true);
  assert.match(h.modal['#analysisActionStatus'].textContent, /period changed during copying/i);
  assert.equal(h.tickets.length, 0);
});

test('malformed counts and source maps refuse a misleading handoff', async t => {
  const mutations = [
    ['string zero', d => ({...d, canonical_rows: '0'})],
    ['negative count', d => ({...d, canonical_rows: -1})],
    ['unsafe integer', d => ({...d, retained_rows: Number.MAX_SAFE_INTEGER + 1})],
    ['negative event count', d => ({...d, event_types: {focus_span: -1}})],
    ['non-numeric source count', d => ({...d, sources: {desktop: '2'}})]
  ];
  for (const [name, mutate] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], mutate(h.preview('range', 'bad')));
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
    assert.equal(h.toasts.length, 1);
  });
});

test('missing request and malformed examples or chunks fail closed', async t => {
  const mutations = [
    ['missing request', d => ({...d, request_text: undefined})],
    ['blank request', d => ({...d, request_text: '   '})],
    ['missing examples', d => ({...d, redacted_examples: undefined})],
    ['null example', d => ({...d, redacted_examples: [null]})],
    ['bad chunks', d => ({...d, analysis_plan: {date_chunks: {since: 'x'}}})],
    ['null chunk', d => ({...d, analysis_plan: {date_chunks: [null]}})]
  ];
  for (const [name, mutate] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], mutate(h.preview('range', 'bad')));
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('valid empty evidence and absent optional plan retain safe copy-only handoff', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[1], {...h.preview('range', 'empty'), canonical_rows: 0,
    retained_rows: 0, event_types: {}, sources: {}, redacted_examples: [],
    first_observed_at: null, last_observed_at: null, analysis_plan: undefined});
  await open;
  assert.equal(h.modals, 1);
  assert.equal(h.modal['#analysisDownload'].disabled, true);
  await h.modal['#analysisDownload'].onclick();
  assert.equal(h.tickets.length, 0);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-empty']);
});

test('nonzero observed evidence requires real chronological ISO timestamps', async t => {
  const mutations = [
    ['missing first', d => ({...d, first_observed_at: null})],
    ['missing pair', d => ({...d, first_observed_at: null, last_observed_at: null})],
    ['invalid timestamp', d => ({...d, first_observed_at: 'not-a-date'})],
    ['impossible day', d => ({...d, first_observed_at: '2026-02-30T10:00:00Z'})],
    ['reversed', d => ({...d, last_observed_at: utc(Date.parse(d.first_observed_at) - 60_000)})],
    ['locale timestamp', d => ({...d, last_observed_at: '10/29/2026'})]
  ];
  for (const [name, mutate] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], mutate(h.preview('range', 'bad')));
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('date chunks require valid labels and safe integer event counts', async t => {
  const chunk = {since: dayLabel(-1), until: dayLabel(0), event_rows: 2};
  const mutations = [
    ['missing since', x => ({...x, since: undefined})],
    ['impossible day', x => ({...x, since: '2026-02-30'})],
    ['invalid until', x => ({...x, until: 'nonsense'})],
    ['missing count', x => ({...x, event_rows: undefined})],
    ['string count', x => ({...x, event_rows: '2'})],
    ['negative count', x => ({...x, event_rows: -1})],
    ['unsafe count', x => ({...x, event_rows: Number.MAX_SAFE_INTEGER + 1})]
  ];
  for (const [name, mutate] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'bad'),
      analysis_plan: {date_chunks: [mutate(chunk)]}});
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('valid offset observations and date-only chunks preserve safe copy', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'valid'),
    first_observed_at: asOffset(Date.parse(observedStart), 2),
    last_observed_at: observedEnd,
    analysis_plan: {date_chunks: [
      {since: dayLabel(0), until: dayLabel(1), event_rows: 2}],
      more_dates_not_listed: false}});
  await open;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-valid']);
  assert.equal(h.tickets.length, 0);
});

test('date chunks refuse reversed, overlapping, duplicated and out-of-order coverage', async t => {
  const a = {since: dayLabel(-3), until: dayLabel(-2), event_rows: 1};
  const b = {since: dayLabel(-2), until: dayLabel(-1), event_rows: 2};
  const mutations = [
    ['reversed bounds', [{...a, since: dayLabel(-1)}]],
    ['zero-length window', [{...a, until: a.since}]],
    ['overlapping windows', [a, {...b, since: dayLabel(-3)}]],
    ['duplicate window', [a, {...a}]],
    ['out-of-order windows', [b, a]],
    ['UTC-equivalent duplicate', [
      {since: dayLabel(-3) + 'T00:00:00Z', until: dayLabel(-2) + 'T00:00:00Z', event_rows: 1},
      {since: dayLabel(-3) + 'T02:00:00+02:00', until: dayLabel(-2) + 'T02:00:00+02:00', event_rows: 1}
    ]]
  ];
  for (const [name, chunks] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'invalid-plan'),
      analysis_plan: {date_chunks: chunks}});
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

// Derive fixtures from the actual selected range, not the wall clock of CI.
const selectedBounds = read => {
  const params = new URLSearchParams(read.url.split('?')[1]);
  return [Date.parse(params.get('since')), Date.parse(params.get('until'))];
};
const utc = ms => new Date(ms).toISOString();
const asOffset = (ms, hours) => {
  const local = utc(ms + hours * 3600_000).replace(/Z$/, '');
  return local + (hours >= 0 ? '+' : '-') + String(Math.abs(hours)).padStart(2, '0') + ':00';
};

test('contiguous date chunks and equivalent UTC-offset boundaries remain copy-only safe', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle();
  const [start] = selectedBounds(h.reads[1]), day = 86400_000;
  h.reply(h.reads[1], {...h.preview('range', 'ordered-plan'),
    canonical_rows: 7, retained_rows: 7,
    first_observed_at: utc(start + 3600_000),
    last_observed_at: utc(start + 3 * day + 3600_000),
    event_types: {focus_span: 7}, sources: {desktop: 7},
    analysis_plan: {date_chunks: [
      {since: utc(start), until: utc(start + day), event_rows: 1},
      {since: utc(start + day), until: utc(start + 2 * day), event_rows: 2},
      {since: utc(start + 2 * day), until: utc(start + 3 * day), event_rows: 3},
      {since: asOffset(start + 3 * day, 2),
        until: asOffset(start + 4 * day, 2), event_rows: 1}
    ], more_dates_not_listed: false}});
  await open;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-ordered-plan']);
  assert.equal(h.tickets.length, 0);
});

test('date chunks outside the selected range are refused even with conserved counts', async t => {
  const bad = [
    ['before selected start', start => ({since: utc(start - 60_000),
      until: utc(start + 60_000), event_rows: 1})],
    ['after selected end', (_, end) => ({since: utc(end - 60_000),
      until: utc(end + 60_000), event_rows: 1})],
    ['offset before start', start => ({since: asOffset(start - 60_000, 2),
      until: asOffset(start + 60_000, 2), event_rows: 1})],
    ['offset after end', (_, end) => ({since: asOffset(end - 60_000, 1),
      until: asOffset(end + 60_000, 1), event_rows: 1})]
  ];
  for (const [name, makeChunk] of bad) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle();
    const [start, end] = selectedBounds(h.reads[1]);
    h.reply(h.reads[1], {...h.preview('range'),
      analysis_plan: {date_chunks: [makeChunk(start, end)]}});
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('UTC-equivalent date chunks at both selected boundaries remain valid', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle();
  const [start, end] = selectedBounds(h.reads[1]);
  h.reply(h.reads[1], {...h.preview('range', 'boundary-plan'),
    first_observed_at: utc(start + 1800_000),
    last_observed_at: utc(end - 1800_000),
    analysis_plan: {date_chunks: [
      {since: asOffset(start, 2), until: utc(start + 3600_000), event_rows: 1},
      {since: utc(end - 3600_000), until: asOffset(end, 1), event_rows: 1}
    ]}});
  await open;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-boundary-plan']);
  assert.equal(h.tickets.length, 0);
});

test('unbounded all scope refuses injected date bounds', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle(); h.reply(h.reads[1], h.preview('all')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[2], {...h.preview('all'),
    window: {scope: 'all', since: '2026-10-29T00:00:00Z'}}, {preserveWindow: true});
  await open;
  assert.equal(h.modals, 0);
  assert.equal(h.tickets.length, 0);
  assert.match(h.summary.textContent, /incomplete or malformed/i);
});

test('partition and planned-row inconsistencies refuse the handoff before any copy or ticket', async t => {
  const mutations = [
    ['event type total too small', d => ({...d, event_types: {focus_span: 1}})],
    ['event type total too large', d => ({...d, event_types: {focus_span: 3}})],
    ['source total too small', d => ({...d, sources: {desktop: 1}})],
    ['source total too large', d => ({...d, sources: {desktop: 3}})],
    ['selected rows exceed retained rows', d => ({...d, retained_rows: 1})],
    ['partition addition overflows safe integer', d => ({...d,
      event_types: {focus_span: Number.MAX_SAFE_INTEGER, capture_gap: 1}})],
    ['listed date chunks exceed selected rows', d => ({...d, analysis_plan: {
      date_chunks: [
        {since: dayLabel(-2), until: dayLabel(-1), event_rows: 2},
        {since: dayLabel(-1), until: dayLabel(0), event_rows: 1}
      ]}})],
    ['zero selected rows with positive planned rows', d => ({...d,
      canonical_rows: 0, first_observed_at: null, last_observed_at: null,
      event_types: {}, sources: {}, analysis_plan: {date_chunks: [
        {since: dayLabel(-1), until: dayLabel(0), event_rows: 1}]}})]
  ];
  for (const [name, mutate] of mutations) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], mutate(h.preview('range', 'inconsistent')));
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
    assert.equal(h.toasts.length, 1);
  });
});

test('a legacy partial date plan without explicit coverage metadata remains usable', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'partial-plan'),
    analysis_plan: {date_chunks: [
      {since: dayLabel(0), until: dayLabel(1), event_rows: 1}
    ]}});
  await open;
  assert.equal(h.modals, 1);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-partial-plan']);
  assert.equal(h.tickets.length, 0);
});

test('explicit daily-plan coverage flags reconcile selected rows and 31-date truncation', async t => {
  const day = 86400_000;
  const lastDay = Math.floor(Date.parse(observedStart) / day) * day;
  const firstDay = lastDay - 31 * day;
  const chunks = Array.from({length: 31}, (_, i) => ({
    since: utc(firstDay + i * day),
    until: utc(firstDay + (i + 1) * day),
    event_rows: 1
  }));
  const cases = [
    ['valid 31-day prefix with a later 32nd observed day', true, 31,
      utc(lastDay + 3600_000), true],
    ['explicit no-omission claim with an unlisted selected row', false, 31,
      utc(lastDay + 3600_000), false],
    ['truncation claim with only 30 listed days', true, 30,
      utc(lastDay + 3600_000), false],
    ['truncation claim without a later observed day', true, 31,
      utc(lastDay - day + 3600_000), false]
  ];
  for (const [name, flag, count, lastObserved, accepted] of cases)
    await t.test(name, async () => {
      const h = harness();
      await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
      h.bars[0].controls.select.value = 'all';
      h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
      await settle(); h.reply(h.reads[1], h.preview('all', 'selected-all')); await settle();
      const opening = h.bars[0].controls.button.onclick();
      await settle();
      h.reply(h.reads[2], {...h.preview('all', 'coverage-reconciled'),
        canonical_rows: 32, retained_rows: 32,
        first_observed_at: utc(firstDay + 3600_000),
        last_observed_at: lastObserved,
        event_types: {focus_span: 32}, sources: {desktop: 32},
        analysis_plan: {date_chunks: chunks.slice(0, count),
          more_dates_not_listed: flag, complete_history_claimed: false}});
      await opening;
      assert.equal(h.modals, accepted ? 1 : 0, name);
      assert.equal(h.tickets.length, 0);
      if (accepted) {
        await h.modal['#analysisCopy'].onclick();
        assert.deepEqual(h.copied, ['request-coverage-reconciled']);
        assert.match(h.modalHtml, /Only the first 31 dates are listed/);
      } else {
        assert.deepEqual(h.copied, []);
        assert.match(h.summary.textContent, /incomplete or malformed/i);
      }
    });
  // A clipped 31st chunk can end before midnight. An event later on that
  // SAME UTC day is not a 32nd distinct date, even when the omission flag,
  // selected row totals and last-observation-after-chunk-end all agree.
  await t.test('truncation requires a later distinct UTC date, not a later hour', async t2 => {
    const clipped = chunks.map((chunk, i) => i === 30
      ? {...chunk, until: utc(lastDay - day + 10 * 3600_000)}
      : chunk);
    const variants = [
      ['later hour on 31st UTC day is not a 32nd date',
        asOffset(lastDay - day + 12 * 3600_000, 5), false],
      ['32nd UTC day remains valid with previous-day local offset label',
        asOffset(lastDay + 3600_000, -5), true]
    ];
    for (const [name, lastObserved, accepted] of variants)
      await t2.test(name, async () => {
        const h = harness();
        await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
        h.bars[0].controls.select.value = 'all';
        h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
        await settle(); h.reply(h.reads[1], h.preview('all', 'selected-all')); await settle();
        const opening = h.bars[0].controls.button.onclick();
        await settle();
        h.reply(h.reads[2], {...h.preview('all', 'clipped-last-date'),
          canonical_rows: 32, retained_rows: 32,
          first_observed_at: utc(firstDay + 3600_000),
          last_observed_at: lastObserved,
          event_types: {focus_span: 32}, sources: {desktop: 32},
          analysis_plan: {date_chunks: clipped,
            more_dates_not_listed: true, complete_history_claimed: false}});
        await opening;
        assert.equal(h.modals, accepted ? 1 : 0, name);
        assert.equal(h.tickets.length, 0);
        if (accepted) {
          await h.modal['#analysisCopy'].onclick();
          assert.deepEqual(h.copied, ['request-clipped-last-date']);
        } else {
          assert.deepEqual(h.copied, []);
          assert.match(h.summary.textContent, /incomplete or malformed/i);
        }
      });
  });
  await t.test('explicitly no omitted dates cannot hide all selected events', async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'empty-list'),
      analysis_plan: {date_chunks: [], more_dates_not_listed: false}});
    await opening;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('work evidence totals, capture-health partition and liveness metadata fail closed', async () => {
  const base = {ordinary_event_rows: 2, health_event_rows: 0,
    last_work_observed_at: observedStart,
    recorder_liveness_verified: false};
  const invalid = [
    {ordinary_event_rows: 3, health_event_rows: 0}, // exceeds selected rows
    {ordinary_event_rows: 1, health_event_rows: 0}, // undercounts selected rows
    {ordinary_event_rows: 1, health_event_rows: 1}, // no health event in partition
    {ordinary_event_rows: Number.MAX_SAFE_INTEGER, health_event_rows: 1},
    {ordinary_event_rows: -1, health_event_rows: 3},
    {health_event_rows: undefined}, // a supplied partition cannot be partial
    {recorder_liveness_verified: 'false'},
    {last_work_observed_at: utc(Date.parse(observedStart) - 1000)},
    {last_work_observed_at: utc(Date.parse(observedEnd) + 1000)},
    {last_work_observed_at: '2026-02-30T10:00:00Z'}
  ];
  for (const change of invalid) {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range'), work_evidence: {...base, ...change}});
    await opening;
    assert.equal(h.modals, 0, 'invalid work evidence must not open handoff: ' + JSON.stringify(change));
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
  }
});

test('valid selected work/health partition and optional legacy metadata still prepare safely', async () => {
  const valid = [
    {work_evidence: {ordinary_event_rows: 1, health_event_rows: 1,
      last_work_observed_at: asOffset(Date.parse(observedStart), 0),
      recorder_liveness_verified: false},
      event_types: {focus_span: 1, capture_gap: 1}},
    {work_evidence: {ordinary_event_rows: 0, health_event_rows: 2,
      last_work_observed_at: null, recorder_liveness_verified: false},
      event_types: {capture_health: 2}},
    {work_evidence: {}},
    {work_evidence: undefined}
  ];
  for (const variant of valid) {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range'), ...variant});
    await opening;
    assert.equal(h.modals, 1, 'valid/legacy evidence should open: ' + JSON.stringify(variant));
    assert.equal(h.modal['#analysisCopy'].disabled, false);
  }
});

test('out-of-window observed timestamps refuse copy and export', async t => {
  const bad = [
    ['before start', (s) => ({first_observed_at: utc(s - 1000), last_observed_at: utc(s + 60000)})],
    ['offset before start', (s) => ({first_observed_at: asOffset(s - 1000, 2), last_observed_at: utc(s + 60000)})],
    ['at exclusive end', (_, e) => ({first_observed_at: utc(e - 60000), last_observed_at: utc(e)})],
    ['offset after end', (_, e) => ({first_observed_at: utc(e - 60000), last_observed_at: asOffset(e + 1000, -3)})]
  ];
  for (const [name, mutation] of bad) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    const [start, end] = selectedBounds(h.reads[1]);
    h.reply(h.reads[1], {...h.preview('range', 'forged-observations'), ...mutation(start, end)});
    await opening;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('inclusive first and pre-exclusive last observations accept equivalent offsets', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle();
  const [start, end] = selectedBounds(h.reads[1]);
  h.reply(h.reads[1], {...h.preview('range', 'valid-observed-bounds'),
    first_observed_at: asOffset(start, 2), last_observed_at: asOffset(end - 1000, -3)});
  await opening;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-valid-observed-bounds']);
  assert.equal(h.tickets.length, 0);
});

test('current-run lower bound rejects older observations; all remains unbounded', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
  h.bars[0].controls.select.value = 'current';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle(); h.reply(h.reads[1], h.preview('current')); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[2], {...h.preview('current'),
    window: {scope: 'current', since: utc(Date.parse(observedStart) + 30000)}},
    {preserveWindow: true});
  await opening;
  assert.equal(h.modals, 0);
  assert.equal(h.tickets.length, 0);
  assert.match(h.summary.textContent, /incomplete or malformed/i);
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle(); h.reply(h.reads[3], h.preview('all')); await settle();
  const all = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[4], {...h.preview('all', 'unbounded'),
    first_observed_at: '2020-01-01T10:00:00Z',
    last_observed_at: '2020-01-01T10:01:00Z'});
  await all;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-unbounded']);
});

test('positive date chunks must intersect the actual observed evidence span', async t => {
  const first = Date.parse(observedStart), last = Date.parse(observedEnd);
  const impossible = [
    ['earlier observed day inside the selected week',
      {since: dayLabel(-2), until: dayLabel(-1), event_rows: 1}],
    ['chunk ends exactly at first observation (exclusive end)',
      {since: utc(first - 3600_000), until: asOffset(first, 2), event_rows: 1}],
    ['later time on the same observed day',
      {since: asOffset(last + 1000, -3), until: utc(last + 60_000), event_rows: 1}],
    ['later day inside the selected week is not needed: last event bounds the evidence',
      {since: utc(last + 60_000), until: utc(last + 120_000), event_rows: 2}]
  ];
  for (const [name, chunk] of impossible) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'false-chunk-coverage'),
      analysis_plan: {date_chunks: [chunk]}});
    await opening;
    assert.equal(h.modals, 0, 'out-of-observation positive chunk must be withheld');
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('calendar-covering, boundary and zero-count optional chunks preserve safe handoff', async t => {
  const first = Date.parse(observedStart), last = Date.parse(observedEnd);
  const allowed = [
    ['full observed local day', {since: dayLabel(0), until: dayLabel(1), event_rows: 2}],
    ['offset-equivalent exact first and last bounds',
      {since: asOffset(first, 2), until: asOffset(last + 1000, -3), event_rows: 1}],
    ['chunk begins at the last observed event',
      {since: asOffset(last, 2), until: utc(last + 1000), event_rows: 1}],
    ['legacy empty chunk outside observed events',
      {since: dayLabel(-2), until: dayLabel(-1), event_rows: 0}]
  ];
  for (const [name, chunk] of allowed) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'observed-intersection'),
      analysis_plan: {date_chunks: [chunk],
        ...(chunk.event_rows === 2 ? {more_dates_not_listed: false} : {})}});
    await opening;
    assert.equal(h.modals, 1, 'valid/partial plan should remain usable');
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-observed-intersection']);
    assert.equal(h.tickets.length, 0);
  });
});

test('redacted focus examples cannot contradict selected counts or observation span', async t => {
  const sample = observed_at => ({app: 'Editor', window_title: 'Redacted',
    ...(observed_at === undefined ? {} : {observed_at})});
  const cases = [
    ['no selected focus', d => {d.event_types = {capture_gap: 2}; d.redacted_examples = [sample(observedStart)];}],
    ['too many for selected focus', d => {d.event_types = {focus_span: 1, capture_gap: 1}; d.redacted_examples = [sample(observedStart), sample(observedEnd)];}],
    ['over six-row cap', d => {d.canonical_rows = 8; d.retained_rows = 8; d.event_types = {focus_span: 8}; d.sources = {desktop: 8}; d.redacted_examples = Array.from({length: 7}, () => sample(observedStart));}],
    ['before observed span', d => {d.redacted_examples = [sample(new Date(Date.parse(observedStart)-1000).toISOString())];}],
    ['after observed span', d => {d.redacted_examples = [sample(new Date(Date.parse(observedEnd)+1000).toISOString())];}],
    ['malformed timestamp', d => {d.redacted_examples = [sample('not-a-timestamp')];}],
    ['numeric timestamp', d => {d.redacted_examples = [sample(123)];}]
  ];
  for (const [name, mutate] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick(); await settle();
    const d = h.preview('range', 'reviewed'); mutate(d);
    h.reply(h.reads[1], d); await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /preview evidence was incomplete or malformed/i);
    assert.equal(h.toasts.length, 1);
  });
});

test('valid redacted focus examples preserve offset and legacy compatibility', async t => {
  const cases = [
    ['UTC-offset equivalent observations', d => {d.redacted_examples = [{app: 'Editor', observed_at: observedStart.replace(/Z$/, '+00:00')}, {app: 'Browser', observed_at: observedEnd}];}],
    ['legacy examples without timestamps', d => {d.redacted_examples = [{app: 'Editor'}];}],
    ['health-only without focus examples', d => {d.event_types = {capture_gap: 2}; d.redacted_examples = [];}]
  ];
  for (const [name, mutate] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick(); await settle();
    const d = h.preview('range', 'reviewed'); mutate(d);
    h.reply(h.reads[1], d); await open;
    assert.equal(h.modals, 1);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-reviewed']);
    assert.equal(h.tickets.length, 0);
  });
});


test('zero selected events cannot claim an observed first/last span', async t => {
  for (const retained of [0, 2]) await t.test('retained=' + retained, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'false-observation'),
      canonical_rows: 0, retained_rows: retained, event_types: {}, sources: {},
      redacted_examples: [], analysis_plan: {date_chunks: []},
      // A zero-row SQL selection cannot have first/last observed rows.
      first_observed_at: observedStart, last_observed_at: observedEnd,
      work_evidence: {ordinary_event_rows: 0, health_event_rows: 0,
        last_work_observed_at: null}});
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('health-only evidence cannot claim a latest ordinary work event', async t => {
  for (const counts of ['explicit', 'legacy-omitted']) await t.test(counts, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'false-work'),
      event_types: {capture_gap: 1, capture_health: 1},
      work_evidence: counts === 'explicit'
        ? {ordinary_event_rows: 0, health_event_rows: 2,
          last_work_observed_at: observedStart}
        : {last_work_observed_at: observedStart}});
    await open;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('health-only evidence with unknown work timestamp remains reviewable', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'health-only'),
    event_types: {capture_gap: 1, capture_health: 1},
    work_evidence: {ordinary_event_rows: 0, health_event_rows: 2,
      last_work_observed_at: null, recorder_liveness_verified: false}});
  await open;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-health-only']);
  assert.equal(h.tickets.length, 0);
});

test('ordinary events may lack a latest work timestamp for excluded apps', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'excluded'),
    work_evidence: {ordinary_event_rows: 2, health_event_rows: 0,
      last_work_observed_at: null, recorder_liveness_verified: false}});
  await open;
  assert.equal(h.modals, 1);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-excluded']);
  assert.equal(h.tickets.length, 0);
});

test('daily evidence plan rejects disjoint fragments of the same UTC day', async t => {
  const first = Date.parse(observedStart);
  const earlier = {since: utc(first - 60_000), until: utc(first + 30_000), event_rows: 1};
  const later = {since: utc(first + 30_000), until: utc(first + 120_000), event_rows: 1};
  const cases = [
    ['two non-overlapping pieces of the same UTC day', [earlier, later]],
    ['same-day split with an equivalent UTC-offset boundary', [
      earlier, {...later, since: asOffset(first + 30_000, 2),
        until: asOffset(first + 120_000, 2)}]],
    ['zero-row fragment still cannot create a second daily step', [
      {...earlier, event_rows: 2}, {...later, event_rows: 0}]]
  ];
  for (const [name, chunks] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'split-utc-day'),
      analysis_plan: {date_chunks: chunks, more_dates_not_listed: false}});
    await opening;
    assert.equal(h.modals, 0, name);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('handoff date plan refuses more than 31 displayed UTC days without truncating silently', async t => {
  // Use actual distinct UTC days over all retained history. A plan with 31
  // one-second fragments of one day would contradict the server contract.
  for (const [name, rows, accepted] of [
    ['31 distinct UTC days is the maximum supported plan', 31, true],
    ['32 distinct UTC days is not a trustworthy server preview', 32, false]
  ]) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    h.bars[0].controls.select.value = 'all';
    h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
    await settle(); h.reply(h.reads[1], h.preview('all', 'selected-all')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    const day = 86400_000;
    const lastDay = Math.floor(Date.parse(observedStart) / day) * day;
    const firstDay = lastDay - (rows - 1) * day;
    const d = h.preview('all', 'bounded-date-plan');
    d.canonical_rows = rows;
    d.retained_rows = rows;
    d.first_observed_at = utc(firstDay + 3600_000);
    d.last_observed_at = utc(lastDay + 3600_000);
    d.event_types = {focus_span: rows};
    d.sources = {desktop: rows};
    d.analysis_plan = {
      date_chunks: Array.from({length: rows}, (_, i) => ({
        since: utc(firstDay + i * day),
        until: utc(firstDay + (i + 1) * day),
        event_rows: 1
      })),
      more_dates_not_listed: false
    };
    h.reply(h.reads[2], d);
    await opening;
    assert.equal(h.modals, accepted ? 1 : 0);
    assert.equal(h.tickets.length, 0);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-bounded-date-plan']);
    } else {
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
      assert.equal(h.toasts.length, 1);
    }
  });
});

test('handoff refuses unsupported completeness and contradictory omission claims', async t => {
  const selectedDay = {since: dayLabel(0), until: dayLabel(1), event_rows: 2};
  const partialDay = {since: dayLabel(0), until: dayLabel(1), event_rows: 1};
  const cases = [
    ['unverified complete-history assertion', d => {d.analysis_plan = {
      date_chunks: [], complete_history_claimed: true};}, false],
    ['string false is not a verified boolean', d => {d.analysis_plan = {
      date_chunks: [], complete_history_claimed: 'false'};}, false],
    ['omission flag with all selected rows listed', d => {d.analysis_plan = {
      date_chunks: [selectedDay], more_dates_not_listed: true,
      complete_history_claimed: false};}, false],
    ['omission flag without a listed plan', d => {d.analysis_plan = {
      more_dates_not_listed: true, complete_history_claimed: false};}, false],
    ['one-day plan cannot claim 31-date truncation', d => {d.analysis_plan = {
      date_chunks: [partialDay], more_dates_not_listed: true,
      complete_history_claimed: false};}, false],
    ['explicitly complete listing cannot omit selected rows', d => {d.analysis_plan = {
      date_chunks: [partialDay], more_dates_not_listed: false,
      complete_history_claimed: false};}, false],
    ['legacy partial listing without coverage flag remains reviewable', d => {d.analysis_plan = {
      date_chunks: [partialDay], complete_history_claimed: false};}, true],
    ['legacy absent coverage flags', d => {d.analysis_plan = {
      date_chunks: [selectedDay]};}, true],
    ['explicitly unverified full listed plan', d => {d.analysis_plan = {
      date_chunks: [selectedDay], more_dates_not_listed: false,
      complete_history_claimed: false};}, true]
  ];
  for (const [name, mutate, accepted] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    const d = h.preview('range', 'coverage-flags'); mutate(d);
    h.reply(h.reads[1], d); await opening;
    assert.equal(h.modals, accepted ? 1 : 0);
    assert.equal(h.tickets.length, 0);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-coverage-flags']);
    } else {
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
      assert.equal(h.toasts.length, 1);
    }
  });
});


test('selected-range AI history grant shows exact bounds, expiry and export distinction', async () => {
  const futureExpiry = new Date(Date.now() + 24 * 3600_000).toISOString();
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[1], {...h.preview('range', 'selected-grant'),
    local_ai_enabled: true,
    local_ai_history_access: {mode: 'selected_range',
      since: '2026-10-20T00:00:00Z', until: '2026-10-25T00:00:00+00:00',
      expires_at: futureExpiry}});
  await open;
  assert.equal(h.modals, 1);
  assert.match(h.modalHtml, /Local saved-history grant: 2026-10-20T00:00:00\.000Z to 2026-10-25T00:00:00\.000Z/);
  assert.match(h.modalHtml, /end exclusive; snapshot only, may not cover this selected work period/);
  assert.ok(h.modalHtml.includes('Grant expiry: ' + futureExpiry));
  assert.match(h.modalHtml, /Preparing or downloading a local file does not grant AI history access/);
  assert.match(h.modalHtml, /Name redaction is best effort, not anonymization/);
  assert.match(h.modalHtml, /detailed chronology adds raw local evidence/);
  h.modal['#analysisDetailed'].checked = true;
  const downloading = h.modal['#analysisDownload'].onclick();
  await settle();
  assert.equal(JSON.parse(h.tickets[0].options.body).include_raw, true);
  h.reply(h.tickets[0], {});
  await downloading;
  assert.equal(h.downloads, 1);
});

test('AI grant disclosure fails conservatively for malformed or unrecognized lease metadata', async t => {
  const cases = [
    ['invalid selected start', true, {mode:'selected_range',
      since:'2026-02-30T00:00:00Z', until:'2026-03-10T00:00:00Z'}],
    ['reversed selected bounds', true, {mode:'selected_range',
      since:'2026-10-25T00:00:00Z', until:'2026-10-20T00:00:00Z'}],
    ['invalid expiry', true, {mode:'all_saved', expires_at:'not-a-date'}],
    ['expired grant', true, {mode:'all_saved', expires_at:'2001-01-01T00:00:00Z'}],
    ['unrecognized grant', true, {mode:'<img src=x onerror=alert(1)>'}],
    ['non-boolean enabled flag', 'false', {mode:'all_saved'}],
    ['missing grant', true, undefined]
  ];
  for(const [name, enabled, access] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'unknown'),
      local_ai_enabled: enabled, local_ai_history_access: access});
    await open;
    assert.equal(h.modals, 1);
    assert.match(h.modalHtml, /permission could not be verified from this preview/);
    assert.doesNotMatch(h.modalHtml, /Local saved-history grant:/);
    assert.doesNotMatch(h.modalHtml, /<img src=x onerror/);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-unknown']);
    assert.equal(h.tickets.length, 0);
  });
});

test('off and all-saved AI modes remain distinguishable from local file exports', async t => {
  for(const [name, enabled, access, expected] of [
    ['disabled AI', false, {mode:'all_saved'}, /Local AI access is off/],
    ['saved access off', true, {mode:'off'}, /saved-history access is off/],
    ['all saved', true, {mode:'all_saved'}, /all saved work \(snapshot only/]
  ]) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const open = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'mode'),
      local_ai_enabled: enabled, local_ai_history_access: access}); await open;
    assert.match(h.modalHtml, expected);
    assert.match(h.modalHtml, /Preparing or downloading a local file does not grant AI history access/);
    assert.equal(h.modals, 1);
  });
});

test('date-plan chunks must fit one UTC calendar day, including offset and clipped cases', async t => {
  const today = dayLabel(0), yesterday = dayLabel(-1), tomorrow = dayLabel(1);
  const ordinaryFirst = today + 'T10:00:00Z', ordinaryLast = today + 'T10:01:00Z';
  const cases = [
    ['two-day span falsely presented as one daily chunk',
      {since: yesterday, until: tomorrow, event_rows: 2},
      ordinaryFirst, ordinaryLast, false],
    ['short chunk crossing UTC midnight despite being under 24 hours',
      {since: yesterday + 'T23:30:00Z', until: today + 'T00:30:00Z', event_rows: 2},
      yesterday + 'T23:45:00Z', today + 'T00:15:00Z', false],
    ['clipped same-day chunk with UTC-equivalent offsets',
      {since: asOffset(Date.parse(today + 'T09:00:00Z'), 2),
        until: asOffset(Date.parse(today + 'T11:00:00Z'), 2), event_rows: 2},
      ordinaryFirst, ordinaryLast, true],
    ['one-day chunk ending exactly at next UTC midnight',
      {since: yesterday + 'T23:59:00Z', until: today + 'T00:00:00Z', event_rows: 2},
      yesterday + 'T23:59:20Z', yesterday + 'T23:59:50Z', true]
  ];
  for (const [name, chunk, first, last, accepted] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'daily-plan'),
      first_observed_at: first, last_observed_at: last,
      analysis_plan: {page_size: 200, chunks_respect_selected_window: true,
        date_chunks: [chunk], complete_history_claimed: false}});
    await opening;
    assert.equal(h.modals, accepted ? 1 : 0, name);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-daily-plan']);
    } else {
      assert.equal(h.tickets.length, 0);
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
    }
  });
});

test('optional date-plan provenance metadata fails closed on contradictions', async t => {
  const cases = [
    ['zero page size', {page_size: 0}, false],
    ['negative page size', {page_size: -1}, false],
    ['fractional page size', {page_size: 1.5}, false],
    ['boolean page size', {page_size: true}, false],
    ['string page size', {page_size: '200'}, false],
    ['unsafe page size', {page_size: Number.MAX_SAFE_INTEGER + 1}, false],
    ['explicit false window-respect claim', {chunks_respect_selected_window: false}, false],
    ['string true window-respect claim', {chunks_respect_selected_window: 'true'}, false],
    ['valid backend plan metadata', {page_size: 200, chunks_respect_selected_window: true}, true],
    ['legacy absent metadata', {}, true]
  ];
  for (const [name, metadata, accepted] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'plan-metadata'),
      analysis_plan: {date_chunks: [], complete_history_claimed: false, ...metadata}});
    await opening;
    assert.equal(h.modals, accepted ? 1 : 0, name);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-plan-metadata']);
    } else {
      assert.equal(h.tickets.length, 0);
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
    }
  });
});

test('contradictory work-text disclosure refuses a handoff before copy or export', async t => {
  const invalid = [
    ['text explicitly included', {included_in_activity_package: true}],
    ['string false is not a verified exclusion', {included_in_activity_package: 'false'}],
    ['numeric inclusion is not a verified exclusion', {included_in_activity_package: 0}],
    ['malformed capture consent flag', {included_in_activity_package: false,
      local_capture_enabled: 'false'}],
    ['non-object work-text metadata', []],
    ['string work-text metadata', 'excluded']
  ];
  for (const [name, work_text] of invalid) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'contradictory'),
      work_text});
    await opening;
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('explicitly excluded work text preserves accurate privacy notice and safe copy', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'text-excluded'),
    work_text: {included_in_activity_package: false, local_capture_enabled: true}});
  await opening;
  assert.equal(h.modals, 1);
  assert.match(h.modalHtml, /Visible work text is not included in this activity package/);
  assert.doesNotMatch(h.modalHtml, /does not confirm that visible work text/);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-text-excluded']);
  assert.equal(h.tickets.length, 0);
});

test('legacy or unknown work-text exclusion is disclosed as uncertain, not verified', async t => {
  const variants = [
    ['missing legacy metadata', undefined],
    ['legacy empty object', {}],
    ['unknown inclusion value', {included_in_activity_package: null}],
    ['null legacy metadata', null]
  ];
  for (const [name, work_text] of variants) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', 'text-unknown'),
      work_text});
    await opening;
    assert.equal(h.modals, 1);
    assert.match(h.modalHtml, /does not confirm that visible work text, typed values or clipboard contents are excluded/);
    assert.doesNotMatch(h.modalHtml, /Visible work text is not included in this activity package/);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-text-unknown']);
    assert.equal(h.tickets.length, 0);
  });
});


// Date.parse accepts T24:00:00 and silently shifts the date. This helper
// isolates the response validation from the local-week selection boundary.
async function openAllRetainedPreview(mutate) {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  h.bars[0].controls.select.value = 'all';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle(); h.reply(h.reads[1], h.preview('all', 'all-selected')); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle();
  h.reply(h.reads[2], mutate(h.preview('all', 'rollover')));
  await opening;
  return h;
}

test('ISO rollover hour is refused for observed evidence and UTC daily plan', async t => {
  const today = dayLabel(0), tomorrow = dayLabel(1);
  const cases = [
    ['observed last timestamp rolls into next UTC day', d => ({
      ...d, last_observed_at: today + 'T24:00:00Z'
    })],
    ['daily chunk start rolls into next UTC day', d => ({
      ...d,
      first_observed_at: tomorrow + 'T00:30:00Z',
      last_observed_at: tomorrow + 'T00:45:00Z',
      analysis_plan: {date_chunks: [{
        since: today + 'T24:00:00Z', until: tomorrow + 'T01:00:00Z',
        event_rows: 2
      }]}
    })]
  ];
  for (const [name, mutate] of cases) await t.test(name, async () => {
    const h = await openAllRetainedPreview(mutate);
    assert.equal(h.modals, 0);
    assert.equal(h.tickets.length, 0);
    assert.deepEqual(h.copied, []);
    assert.match(h.summary.textContent, /incomplete or malformed/i);
  });
});

test('rollover clock in an AI history lease never becomes an affirmative grant', async () => {
  const h = await openAllRetainedPreview(d => ({
    ...d, local_ai_enabled: true,
    local_ai_history_access: {mode: 'all_saved', expires_at: '2099-01-01T24:00:00Z'}
  }));
  assert.equal(h.modals, 1);
  assert.match(h.modalHtml, /permission could not be verified from this preview/);
  assert.doesNotMatch(h.modalHtml, /Local saved-history grant:/);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-rollover']);
  assert.equal(h.tickets.length, 0);
});

test('ticket bound with equivalent parsed instant but rollover hour is rejected', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], h.preview('range', 'reviewed')); await opening;
  const downloading = h.modal['#analysisDownload'].onclick();
  await settle();
  const body = JSON.parse(h.tickets[0].options.body);
  const selected = new Date(body.since);
  const eastMinutes = -selected.getTimezoneOffset();
  const sign = eastMinutes >= 0 ? '+' : '-';
  const magnitude = Math.abs(eastMinutes);
  const offset = sign + String(Math.floor(magnitude / 60)).padStart(2, '0') +
    ':' + String(magnitude % 60).padStart(2, '0');
  const priorDate = new Date(+selected + eastMinutes * 60000 - 86400000)
    .toISOString().slice(0, 10);
  const rollover = priorDate + 'T24:00:00' + offset;
  // Demonstrates the bypass that existed with Date.parse-only comparison.
  assert.equal(Date.parse(rollover), Date.parse(body.since));
  const params = new URLSearchParams({scope: body.scope,
    include_raw: String(body.include_raw), redact_names: String(body.redact_names),
    ticket: 'synthetic-ticket', since: rollover, until: body.until});
  h.reply(h.tickets[0], {ticket: 'synthetic-ticket',
    url: '/v1/export/csvzip?' + params}, {preserveTicket: true});
  await downloading;
  assert.equal(h.downloads, 0);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  assert.match(h.modal['#analysisActionStatus'].textContent, /ticket did not match/i);
});


test('ticket impossible calendar day cannot alias a selected valid custom date', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  h.bars[0].controls.select.value = 'custom';
  h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
  await settle();
  const observed = {...h.preview('range', 'custom'),
    first_observed_at: '2026-03-02T12:00:00Z',
    last_observed_at: '2026-03-02T12:01:00Z'};
  h.reply(h.reads.at(-1), observed); await settle();
  h.bars[0].controls.start.value = '2026-03-02';
  h.bars[0].controls.end.value = '2026-03-02';
  h.bars[0].controls.start.onchange();
  await settle(); h.reply(h.reads.at(-1), observed); await settle();
  const opening = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads.at(-1), observed); await opening;
  assert.equal(h.modals, 1);
  const downloading = h.modal['#analysisDownload'].onclick();
  await settle();
  const body = JSON.parse(h.tickets[0].options.body);
  const selected = new Date(body.since);
  const eastMinutes = -selected.getTimezoneOffset();
  const sign = eastMinutes >= 0 ? '+' : '-';
  const magnitude = Math.abs(eastMinutes);
  const offset = sign + String(Math.floor(magnitude / 60)).padStart(2, '0') +
    ':' + String(magnitude % 60).padStart(2, '0');
  const impossible = '2026-02-30T00:00:00' + offset;
  assert.equal(Date.parse(impossible), Date.parse(body.since));
  const params = new URLSearchParams({scope: body.scope,
    include_raw: String(body.include_raw), redact_names: String(body.redact_names),
    ticket: 'synthetic-ticket', since: impossible, until: body.until});
  h.reply(h.tickets[0], {ticket: 'synthetic-ticket',
    url: '/v1/export/csvzip?' + params}, {preserveTicket: true});
  await downloading;
  assert.equal(h.downloads, 0);
  assert.match(h.modal['#analysisActionStatus'].textContent, /ticket did not match/i);
});


test('explicit daily-plan endpoints must include observed evidence, not merely conserve counts', async t => {
  const firstDay = dayLabel(-40);
  const listedFirstDay = dayLabel(-39);
  const lastDay = dayLabel(-7);
  const chunks = Array.from({length: 31}, (_, i) => ({
    since: dayLabel(-39 + i), until: dayLabel(-38 + i), event_rows: 1
  }));
  // The server lists the first 31 distinct UTC dates. An otherwise plausible
  // 31-row page must not silently omit the earliest selected date.
  for (const [name, first, accepted] of [
    ['first selected day omitted despite 31 chunks', firstDay + 'T12:00:00Z', false],
    ['first selected day included; 32nd date is genuinely unlisted',
      listedFirstDay + 'T12:00:00Z', true]
  ]) await t.test(name, async () => {
    const h = await openAllRetainedPreview(d => ({
      ...d, canonical_rows: 32, retained_rows: 32,
      first_observed_at: first, last_observed_at: lastDay + 'T12:00:00Z',
      event_types: {focus_span: 32}, sources: {desktop: 32},
      analysis_plan: {date_chunks: chunks, more_dates_not_listed: true}
    }));
    assert.equal(h.modals, accepted ? 1 : 0, name);
    assert.equal(h.tickets.length, 0);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-rollover']);
    } else {
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
    }
  });

  const completeChunks = [
    {since: dayLabel(-4), until: dayLabel(-3), event_rows: 1},
    {since: dayLabel(-3), until: dayLabel(-2), event_rows: 1}
  ];
  for (const [name, last, flag, accepted] of [
    ['explicit full plan omits the latest selected date',
      dayLabel(-1) + 'T12:00:00Z', false, false],
    ['explicit full plan includes the latest observed date',
      dayLabel(-3) + 'T12:00:00Z', false, true],
    ['legacy plan with absent coverage flag remains unverified but reviewable',
      dayLabel(-1) + 'T12:00:00Z', undefined, true]
  ]) await t.test(name, async () => {
    const h = await openAllRetainedPreview(d => ({
      ...d, first_observed_at: dayLabel(-4) + 'T12:00:00Z',
      last_observed_at: last,
      analysis_plan: {date_chunks: completeChunks,
        ...(flag === undefined ? {} : {more_dates_not_listed: flag})}
    }));
    assert.equal(h.modals, accepted ? 1 : 0, name);
    assert.equal(h.tickets.length, 0);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-rollover']);
    } else {
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
    }
  });
});


test('explicit daily coverage refuses invented empty dates without breaking legacy previews', async t => {
  const day = 86400_000;
  const base = Date.parse('2026-01-01T00:00:00Z');
  const first = utc(base + 3600_000);
  const second = utc(base + day + 3600_000);
  const twoDays = [
    {since: utc(base), until: utc(base + day), event_rows: 0},
    {since: utc(base + day), until: utc(base + 2 * day), event_rows: 2}
  ];
  const clipped = Array.from({length: 31}, (_, i) => ({
    since: utc(base + i * day),
    until: utc(base + (i + 1) * day),
    event_rows: i === 1 ? 0 : i === 2 ? 2 : 1
  }));
  const cases = [
    ['explicit complete plan with fabricated empty first date', {
      canonical_rows: 2, retained_rows: 2, first_observed_at: first,
      last_observed_at: second, event_types: {focus_span: 2},
      sources: {desktop: 2},
      analysis_plan: {date_chunks: twoDays, more_dates_not_listed: false}
    }, false],
    ['explicit truncated plan with fabricated empty interior date', {
      canonical_rows: 32, retained_rows: 32, first_observed_at: first,
      last_observed_at: utc(base + 31 * day + 3600_000),
      event_types: {focus_span: 32}, sources: {desktop: 32},
      analysis_plan: {date_chunks: clipped, more_dates_not_listed: true}
    }, false],
    ['explicit zero-event plan cannot fabricate an empty date', {
      canonical_rows: 0, retained_rows: 2, first_observed_at: null,
      last_observed_at: null, event_types: {}, sources: {},
      analysis_plan: {date_chunks: [twoDays[0]], more_dates_not_listed: false}
    }, false],
    ['legacy partial plan may retain an empty date without a coverage claim', {
      canonical_rows: 2, retained_rows: 2, first_observed_at: first,
      last_observed_at: second, event_types: {focus_span: 2},
      sources: {desktop: 2}, analysis_plan: {date_chunks: twoDays}
    }, true],
    ['explicit complete positive-count date remains valid', {
      canonical_rows: 2, retained_rows: 2, first_observed_at: first,
      last_observed_at: utc(base + 2 * 3600_000),
      event_types: {focus_span: 2}, sources: {desktop: 2},
      analysis_plan: {date_chunks: [
        {since: utc(base), until: utc(base + day), event_rows: 2}
      ], more_dates_not_listed: false}
    }, true],
    ['explicit zero-event empty plan remains copy-only', {
      canonical_rows: 0, retained_rows: 2, first_observed_at: null,
      last_observed_at: null, event_types: {}, sources: {},
      analysis_plan: {date_chunks: [], more_dates_not_listed: false}
    }, true]
  ];
  for (const [name, variant, accepted] of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    h.bars[0].controls.select.value = 'all';
    h.bars[0].controls.select.onchange({target: h.bars[0].controls.select});
    await settle(); h.reply(h.reads[1], h.preview('all', 'all-selected')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[2], {...h.preview('all', 'daily-coverage'), ...variant});
    await opening;
    assert.equal(h.modals, accepted ? 1 : 0, name);
    assert.equal(h.tickets.length, 0, name);
    if (accepted) {
      await h.modal['#analysisCopy'].onclick();
      assert.deepEqual(h.copied, ['request-daily-coverage']);
      assert.equal(h.modal['#analysisDownload'].disabled, variant.canonical_rows === 0);
    } else {
      assert.deepEqual(h.copied, []);
      assert.match(h.summary.textContent, /incomplete or malformed/i);
    }
  });
});


test('handoff labels current-run and all-history scope without conflating them', async t => {
  const oneMinuteBeforeObserved = new Date(Date.parse(observedStart) - 60_000).toISOString();
  const variants = [
    {name: 'current run without start metadata', scope: 'current',
      expected: /Current recorder run \(start not supplied by preview\)/,
      forbidden: /all available history|All retained history/},
    {name: 'current run with explicit recorder start', scope: 'current',
      since: oneMinuteBeforeObserved,
      expected: /Current recorder run \(from /,
      forbidden: /all available history|All retained history/},
    {name: 'all retained history', scope: 'all',
      expected: /All retained history \(unbounded date selection\)/,
      forbidden: /Current recorder run/}
  ];
  for (const variant of variants) await t.test(variant.name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const select = h.bars[0].controls.select;
    select.value = variant.scope;
    select.onchange({target: select});
    await settle();
    const data = {...h.preview(variant.scope, 'reviewed'),
      ...(variant.since ? {window: {scope: variant.scope, since: variant.since}} : {})};
    const preserveWindow = !!variant.since;
    h.reply(h.reads[1], data, {preserveWindow});
    await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[2], data, {preserveWindow});
    await opening;
    assert.equal(h.modals, 1);
    assert.match(h.modalHtml, variant.expected);
    assert.doesNotMatch(h.modalHtml, variant.forbidden);
    if (variant.since) assert.match(h.modalHtml, new RegExp(variant.since.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-reviewed']);
    assert.equal(h.tickets.length, 0);
  });
});

test('missing daily plan and empty daily list are labelled as unknown, not zero evidence', async t => {
  const variants = [
    {name: 'absent daily plan', plan: undefined,
      expected: /No daily breakdown was provided; date coverage is unknown/},
    {name: 'empty daily plan', plan: {date_chunks: []},
      expected: /No daily chunks were listed; this does not prove complete recording or zero events/}
  ];
  for (const variant of variants) await t.test(variant.name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], {...h.preview('range', 'two-observed-events'),
      analysis_plan: variant.plan});
    await opening;
    assert.equal(h.modals, 1);
    assert.match(h.modalHtml, variant.expected);
    assert.match(h.modalHtml, /2 recorded events/);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-two-observed-events']);
    assert.equal(h.tickets.length, 0);
  });
});

test('latest-work disclosure distinguishes known, missing and health-only evidence', async t => {
  const cases = [
    {name: 'known latest work timestamp', patch: {
      work_evidence: {ordinary_event_rows: 2, health_event_rows: 0,
        last_work_observed_at: observedStart}},
    expected: /Latest work event: /, forbidden: /timestamp not supplied|No ordinary work events recorded/},
    {name: 'ordinary events but missing timestamp', patch: {
      work_evidence: {ordinary_event_rows: 2, health_event_rows: 0,
        last_work_observed_at: null}},
    expected: /Latest work event timestamp not supplied \(2 ordinary selected events\); this does not imply no work occurred/,
    forbidden: /Latest work event: none in this period|No ordinary work events recorded/},
    {name: 'legacy preview omits work metadata', patch: {work_evidence: undefined},
    expected: /Latest work event timestamp not supplied \(2 ordinary selected events\)/,
    forbidden: /Latest work event: none in this period|No ordinary work events recorded/},
    {name: 'health-only selected evidence', patch: {
      event_types: {capture_gap: 1, capture_health: 1},
      work_evidence: {ordinary_event_rows: 0, health_event_rows: 2,
        last_work_observed_at: null}},
    expected: /No ordinary work events recorded in this selection; this does not establish that no work occurred/,
    forbidden: /Latest work event: none in this period|timestamp not supplied/},
    {name: 'zero selected events', patch: {
      canonical_rows: 0, retained_rows: 2,
      first_observed_at: null, last_observed_at: null,
      event_types: {}, sources: {},
      work_evidence: {ordinary_event_rows: 0, health_event_rows: 0,
        last_work_observed_at: null}},
    expected: /No ordinary work events recorded in this selection; this does not establish that no work occurred/,
    forbidden: /Latest work event: none in this period|timestamp not supplied/}
  ];
  for (const {name, patch, expected, forbidden} of cases) await t.test(name, async () => {
    const h = harness();
    await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
    const opening = h.bars[0].controls.button.onclick();
    await settle(); h.reply(h.reads[1], {...h.preview('range', name), ...patch});
    await opening;
    assert.equal(h.modals, 1, name);
    assert.match(h.modalHtml, expected, name);
    assert.doesNotMatch(h.modalHtml, forbidden, name);
    assert.match(h.modalHtml, /do not confirm the recorder is running/, name);
    assert.equal(h.modal['#analysisDownload'].disabled, patch.canonical_rows === 0, name);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-' + name], name);
    assert.equal(h.tickets.length, 0, name);
  });
});

test('zero recorded events never become a claim of inactivity or recorder consent', async t => {
  const cases = [
    {name: 'selected period empty while retained history exists', retained: 2,
      expected: /No events recorded in this selection\. Other retained evidence exists; inactivity and capture completeness are unverified/},
    {name: 'no retained events reported', retained: 0,
      expected: /No retained events reported\. Recording and consent status are unverified; this does not establish inactivity/}
  ];
  for (const {name, retained, expected} of cases) await t.test(name, async () => {
    const h = harness();
    const empty = {
      ...h.preview('range', name),
      canonical_rows: 0, retained_rows: retained,
      first_observed_at: null, last_observed_at: null,
      event_types: {}, sources: {}, redacted_examples: [],
      analysis_plan: {date_chunks: []},
      work_evidence: {ordinary_event_rows: 0, health_event_rows: 0,
        last_work_observed_at: null}
    };
    await settle();
    h.reply(h.reads[0], empty);
    await settle();
    assert.match(h.summary.textContent, expected, name);
    assert.doesNotMatch(h.summary.textContent, /No activity in this selection|Work normally after enabling recording/i, name);
    const opening = h.bars[0].controls.button.onclick();
    await settle();
    h.reply(h.reads[1], empty);
    await opening;
    assert.equal(h.modals, 1, name);
    assert.match(h.summary.textContent, expected, name);
    assert.match(h.modalHtml, /No ordinary work events recorded in this selection; this does not establish that no work occurred/, name);
    assert.equal(h.modal['#analysisDownload'].disabled, true, name);
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-' + name], name);
    assert.equal(h.tickets.length, 0, name);
    assert.equal(h.downloads, 0, name);
  });
});
