import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function dashboard() {
  const nodes = new Map();
  for (const id of ['#personalLinkMessage', '#personalLinkStart', '#personalLinkCancel']) {
    nodes.set(id, {isConnected:true, textContent:'', disabled:false});
  }
  const requests = [], modals = [];
  const popup = {opener:{}, close(){this.closed=true;}};
  const window = {__owgAuthReady:Promise.resolve(), open:() => popup, esc:s => s};
  const context = vm.createContext({window, Date, setTimeout, clearTimeout,
    document:{addEventListener(){}, querySelector:s => nodes.get(s) || null},
    openModal:(...args) => modals.push(args), closeModal(){},
    fetch:async (url, options) => {
      requests.push([url, options]);
      const value = url.endsWith('/start') ? {authorization_url:'https://issuer.example/authorize', expires_in:600}
        : url.endsWith('/status') ? {status:'ready'}
        : {connected:true, mcp_url:'https://mcp.owg.kinvectum.com/mcp'};
      return {ok:true, json:async () => value};
    },
  });
  vm.runInContext(fs.readFileSync(new URL('../../dashboard/gateway_panel.js', import.meta.url), 'utf8'), context);
  return {window, nodes, requests, modals, popup};
}

test('personal sign-in requires a separate sharing click', async () => {
  const d = dashboard();
  d.window.openChatGPTPersonalLink();
  assert.equal(d.requests.length, 0);
  await d.nodes.get('#personalLinkStart').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(d.popup.opener, null);
  assert.equal(d.popup.location, 'https://issuer.example/authorize');
  assert.deepEqual(d.requests.map(r => r[0]), ['/v1/chatgpt-link/start', '/v1/chatgpt-link/status']);
  assert.equal(d.nodes.get('#personalLinkStart').textContent, 'Link and share new evidence');
  await d.nodes.get('#personalLinkStart').onclick();
  assert.ok(d.requests.some(r => r[0] === '/v1/chatgpt-link/complete'));
  assert.match(d.modals.at(-1)[2], /https:\/\/mcp\.owg\.kinvectum\.com\/mcp/);
});

test('cancel does not enroll or change capture preferences', async () => {
  const d = dashboard();
  d.window.openChatGPTPersonalLink();
  await d.nodes.get('#personalLinkCancel').onclick();
  assert.deepEqual(d.requests.map(r => r[0]), ['/v1/chatgpt-link/cancel']);
});
