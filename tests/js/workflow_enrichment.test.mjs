import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { randomUUID } from 'node:crypto';

function listener() { return { addListener() {} }; }

function makeContext() {
  const storage = {};
  const requests = [];
  const chrome = {
    runtime: {
      getManifest: () => ({version: '1.10.0', version_name: 'test'}),
      onMessage: listener(),
      onInstalled: listener(),
      onStartup: listener(),
    },
    storage: {
      local: {
        async get(key) {
          if (typeof key === 'string') return {[key]: storage[key]};
          return {...storage};
        },
        async set(value) { Object.assign(storage, value); },
      },
    },
    tabs: {
      async query() { return []; },
      async get(id) {
        return {id, windowId: 1, url: 'https://example.com/work', title: 'Work'};
      },
      onActivated: listener(),
      onUpdated: listener(),
    },
    webNavigation: {
      onBeforeNavigate: listener(),
      onCommitted: listener(),
      onCompleted: listener(),
      onHistoryStateUpdated: listener(),
    },
    alarms: {create() {}, onAlarm: listener()},
  };

  const context = vm.createContext({
    chrome,
    browser: undefined,
    URL,
    Map,
    Set,
    Promise,
    Date,
    Math,
    JSON,
    String,
    Array,
    Object,
    RegExp,
    Number,
    console,
    crypto: {randomUUID},
    fetch: async (url, options = {}) => {
      const method = options.method || 'GET';
      if (String(url).endsWith('/v1/browser-context')) {
        return {ok: true, async json() {
          return {organization_id: '', actor_id: '', device_id: 'd1', work_session_id: 's1'};
        }};
      }
      requests.push({
        url: String(url),
        method,
        body: options.body ? JSON.parse(options.body) : null,
      });
      return {ok: true, async json() { return {}; }};
    },
  });
  return {context, requests};
}

function loadScripts(context) {
  const background = fs.readFileSync(new URL('../../browser_extension/background.js', import.meta.url), 'utf8');
  const enrichment = fs.readFileSync(new URL('../../browser_extension/workflow_enrichment.js', import.meta.url), 'utf8');
  vm.runInContext(background, context);
  vm.runInContext(enrichment, context);
}

test('semantic actions are conservative and tab context is runtime-stable', () => {
  const {context} = makeContext();
  loadScripts(context);

  const send = JSON.parse(JSON.stringify(vm.runInContext(
    "workflowSemanticAction('click', {label: 'Send (⌘Enter)'})",
    context,
  )));
  assert.deepEqual(send, {name: 'send', confidence: 'label_heuristic'});

  const submit = JSON.parse(JSON.stringify(vm.runInContext(
    "workflowSemanticAction('form_submit', {})",
    context,
  )));
  assert.deepEqual(submit, {name: 'submit', confidence: 'direct_event'});

  const one = vm.runInContext("workflowTabContextId(7)", context);
  const two = vm.runInContext("workflowTabContextId(7)", context);
  const other = vm.runInContext("workflowTabContextId(8)", context);
  assert.equal(one, two);
  assert.notEqual(one, other);
  assert.ok(one.endsWith(':7'));
});

test('copy and paste share a transfer id without clipboard contents', async () => {
  const {context, requests} = makeContext();
  loadScripts(context);
  requests.length = 0;

  await vm.runInContext(`sendBrowserEvent({
    observed_at: new Date().toISOString(),
    action: 'copy',
    page: {origin: 'https://source.example', hostname: 'source.example', pathname: '/doc', title: 'Source'},
    target: {tag: 'button', label: 'Copy'},
    metadata: {tab_id: 11, window_id: 1}
  })`, context);

  await vm.runInContext(`sendBrowserEvent({
    observed_at: new Date().toISOString(),
    action: 'paste',
    page: {origin: 'https://dest.example', hostname: 'dest.example', pathname: '/compose', title: 'Destination'},
    target: {tag: 'textarea', role: 'textbox'},
    metadata: {tab_id: 12, window_id: 1}
  })`, context);

  const events = requests.filter(r => r.url.endsWith('/v1/browser-events')).map(r => r.body);
  assert.equal(events.length, 2);
  const [copy, paste] = events;

  assert.ok(copy.metadata.clipboard_transfer_id);
  assert.equal(paste.metadata.clipboard_transfer_id, copy.metadata.clipboard_transfer_id);
  assert.equal(copy.metadata.clipboard_contents_captured, false);
  assert.equal(paste.metadata.clipboard_contents_captured, false);
  assert.equal(copy.metadata.semantic_action, 'copy');
  assert.equal(paste.metadata.semantic_action, 'paste');
  assert.equal(copy.metadata.semantic_action_confidence, 'direct_event');
  assert.equal(paste.metadata.clipboard_source_observed, true);
  assert.ok(copy.metadata.tab_context_id.endsWith(':11'));
  assert.ok(paste.metadata.tab_context_id.endsWith(':12'));
  assert.equal(JSON.stringify(events).includes('clipboard_text'), false);
});
