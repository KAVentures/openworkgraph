import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createHmac, webcrypto} from 'node:crypto';

const source = fs.readFileSync('browser_extension/browser_auth.js', 'utf8');
const API = 'http://127.0.0.1:8787';

function makeContext({challengeProof}) {
  const secret = 'browser-test-secret';
  const store = {};
  const calls = [];
  const ext = {
    runtime: {getURL: p => `chrome-extension://test/${p}`},
    storage: {local: {
      async get(key) { return {[key]: store[key]}; },
      async set(value) { Object.assign(store, value); },
    }},
  };

  async function nativeFetch(url, init = {}) {
    const u = String(url);
    calls.push({url: u, init});
    if (u.startsWith('chrome-extension://test/pairing.json')) {
      return new Response(JSON.stringify({version: 1, secret}), {status: 200, headers: {'Content-Type':'application/json'}});
    }
    if (u === `${API}/v1/browser-challenge`) {
      const nonce = JSON.parse(String(init.body || '{}')).nonce;
      const proof = challengeProof === 'valid'
        ? createHmac('sha256', secret).update(`openworkgraph-server-proof:${nonce}`).digest('hex')
        : 'not-a-valid-openworkgraph-proof';
      return new Response(JSON.stringify({proof}), {status: 200, headers: {'Content-Type':'application/json'}});
    }
    if (u === `${API}/v1/browser-events`) {
      return new Response(JSON.stringify({status:'ok'}), {status: 200, headers: {'Content-Type':'application/json'}});
    }
    return new Response('', {status: 404});
  }

  const context = vm.createContext({
    browser: ext,
    chrome: undefined,
    fetch: nativeFetch,
    crypto: webcrypto,
    TextEncoder,
    Response,
    Headers,
    URL,
    console,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(source, context, {filename: 'browser_auth.js'});
  return {context, calls};
}

test('browser sensor does not send event body to a server that fails identity proof', async () => {
  const {context, calls} = makeContext({challengeProof: 'invalid'});
  const body = JSON.stringify({page:{hostname:'mail.google.com'},target:{label:'Project Falcon pricing'}});
  const response = await context.fetch(`${API}/v1/browser-events`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body,
  });
  assert.equal(response.status, 401);
  const eventCalls = calls.filter(x => x.url === `${API}/v1/browser-events`);
  assert.equal(eventCalls.length, 0, 'rich event body must never be sent after a failed server proof');
});

test('browser sensor sends an HMAC-authenticated request after valid server proof', async () => {
  const {context, calls} = makeContext({challengeProof: 'valid'});
  const body = JSON.stringify({page:{hostname:'mail.google.com'},target:{label:'Send'}});
  const response = await context.fetch(`${API}/v1/browser-events`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body,
  });
  assert.equal(response.status, 200);
  const eventCalls = calls.filter(x => x.url === `${API}/v1/browser-events`);
  assert.equal(eventCalls.length, 1);
  const headers = new Headers(eventCalls[0].init.headers);
  assert.match(headers.get('Authorization') || '', /^OWG-HMAC \d+\.[^.]+\.[0-9a-f]{64}$/);
  assert.equal(String(eventCalls[0].init.body), body);
});
