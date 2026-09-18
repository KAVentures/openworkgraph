import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('server/secure_app.py', 'utf8');
const scripts = [...source.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)].map(m => m[1]);

test('runtime-injected dashboard security JavaScript parses', () => {
  assert.ok(scripts.length >= 2, 'secure_app should inject bootstrap and connection scripts');
  for (const script of scripts) new Function(script);
});

test('dashboard bootstrap does not embed the installation API token', () => {
  assert.match(source, /\/v1\/dashboard-session/);
  assert.match(source, /httponly=True/);
  assert.match(source, /history\.replaceState/);
  assert.doesNotMatch(source, /__API_TOKEN__/);
});

test('AI connector setup requests authenticated MCP connection details', () => {
  assert.match(source, /\/v1\/mcp-connection-config/);
  assert.match(source, /Authorization:`Bearer \$\{c\.token\}`/);
  assert.match(source, /mcp_server\.secure_stdio/);
});
