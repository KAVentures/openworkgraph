import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('server/secure_app.py', 'utf8');
const scripts = [...source.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)].map(m => m[1]);

test('runtime-injected dashboard security JavaScript parses', () => {
  assert.ok(scripts.length >= 2, 'secure_app should inject bootstrap and connection scripts');
  for (const script of scripts) new Function(script);
});

test('dashboard bootstrap uses a port-scoped session capability, not cookies or the API token', () => {
  assert.match(source, /\/v1\/dashboard-session/);
  assert.match(source, /sessionStorage/);
  assert.match(source, /OWG-Session/);
  assert.match(source, /history\.replaceState/);
  assert.doesNotMatch(source, /set_cookie\(/);
  assert.doesNotMatch(source, /DASHBOARD_COOKIE/);
  assert.doesNotMatch(source, /__API_TOKEN__/);
});

test('dashboard exports use short-lived server tickets rather than cookie navigation', () => {
  assert.match(source, /\/v1\/export-ticket/);
  assert.match(source, /issue_export_ticket/);
  assert.match(source, /consume_export_ticket/);
  const override = source.split('window.downloadExport', 2)[1].split('async function connectionConfig', 1)[0];
  assert.match(override, /export-ticket/);
  assert.doesNotMatch(override, /location\.href/);
});

test('local AI connectors use compact authenticated stdio while HTTP bearer is on demand only', () => {
  assert.match(source, /\/v1\/mcp-connection-config/);
  assert.match(source, /mcp_server\.compact_stdio/);
  assert.match(source, /cursor:\/\/anysphere\.cursor-deeplink\/mcp\/install/);
  const cursorBlock = source.split('window.connectCursor', 2)[1].split('window.showBrowserPairingCode', 1)[0];
  assert.doesNotMatch(cursorBlock, /Bearer/);
  assert.match(source, /httpMcp\('start'\)/);
  assert.match(source, /Bearer \$\{c\.token\}/);
});
