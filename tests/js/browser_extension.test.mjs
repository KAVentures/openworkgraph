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
      getManifest: () => ({version: '1.8.1', version_name: 'test'}),
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
      async query() {
        return [{
          id: 1,
          windowId: 1,
          url: 'https://chatgpt.com/c/123456789?session=SECRET#fragment',
          title: 'Enterprise Task Mining Software',
        }];
      },
      async get(id) {
        return {id, windowId: 1, url: 'https://chatgpt.com/', title: 'ChatGPT'};
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

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('browser sanitizer executes and strips query/fragment/token paths', async () => {
  const {context} = makeContext();
  const source = fs.readFileSync(new URL('../../browser_extension/background.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);

  const safe = plain(vm.runInContext(
    "safeUrl('https://example.com/reset/0123456789abcdef0123456789abcdef?session=SECRET#confirm')",
    context,
  ));
  assert.deepEqual(safe, {
    origin: 'https://example.com',
    hostname: 'example.com',
    pathname: '/reset/:token',
  });

  const event = plain(vm.runInContext(`
    sanitizeBrowserEvent({
      page: {
        origin: 'https://example.com',
        hostname: 'example.com',
        pathname: '/reset/0123456789abcdef0123456789abcdef?session=SECRET#confirm',
        title: 'Reset'
      },
      metadata: {frame_url: 'https://example.com/callback/SECRETSECRET?token=SECRET#x'}
    })
  `, context));
  const blob = JSON.stringify(event);
  assert.equal(blob.includes('session=SECRET'), false);
  assert.equal(blob.includes('#confirm'), false);
  assert.equal(blob.includes('0123456789abcdef0123456789abcdef'), false);
});

test('heartbeat sends sanitized active-tab identity, not the full URL', async () => {
  const {context, requests} = makeContext();
  const source = fs.readFileSync(new URL('../../browser_extension/background.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);
  requests.length = 0;

  await vm.runInContext("heartbeat('test')", context);
  const hb = requests.filter(r => r.url.endsWith('/v1/browser-heartbeat')).at(-1);
  assert.ok(hb);
  assert.equal(hb.body.page.hostname, 'chatgpt.com');
  assert.equal(hb.body.page.pathname, '/c/:id');
  assert.equal(JSON.stringify(hb.body).includes('session=SECRET'), false);
  assert.equal(JSON.stringify(hb.body).includes('#fragment'), false);
});
