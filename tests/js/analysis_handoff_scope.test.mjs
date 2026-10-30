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
