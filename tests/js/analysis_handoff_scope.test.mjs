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
    location: {origin: 'http://localhost:8765'},
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

test('a valid partial date plan conserves counts without claiming complete coverage', async () => {
  const h = harness();
  await settle(); h.reply(h.reads[0], h.preview('range', 'initial')); await settle();
  const open = h.bars[0].controls.button.onclick();
  await settle(); h.reply(h.reads[1], {...h.preview('range', 'partial-plan'),
    analysis_plan: {date_chunks: [
      {since: dayLabel(0), until: dayLabel(1), event_rows: 1}
    ], more_dates_not_listed: true}});
  await open;
  assert.equal(h.modals, 1);
  assert.equal(h.modal['#analysisCopy'].disabled, false);
  assert.equal(h.modal['#analysisDownload'].disabled, false);
  await h.modal['#analysisCopy'].onclick();
  assert.deepEqual(h.copied, ['request-partial-plan']);
  assert.equal(h.tickets.length, 0);
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
      analysis_plan: {date_chunks: [chunk], more_dates_not_listed: true}});
    await opening;
    assert.equal(h.modals, 1, 'valid/partial plan should remain usable');
    await h.modal['#analysisCopy'].onclick();
    assert.deepEqual(h.copied, ['request-observed-intersection']);
    assert.equal(h.tickets.length, 0);
  });
});
