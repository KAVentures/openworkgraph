import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const html = fs.readFileSync(path.resolve('dashboard/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

test('dashboard inline JavaScript parses', () => {
  assert.ok(scripts.length > 0, 'dashboard should contain inline JavaScript');
  for (const script of scripts) {
    // Parse without running browser-only globals.
    new Function(script);
  }
});

test('dashboard keeps rich export controls and MCP onboarding', () => {
  assert.match(html, /id="includeRawTop"/);
  assert.match(html, /id="includeRaw"/);
  assert.match(html, /function syncRawToggle\(source\)/);
  assert.match(html, /Connect your AI/);
  assert.match(html, /cursor:\/\/anysphere\.cursor-deeplink\/mcp\/install/);
  assert.match(html, /http:\/\/127\.0\.0\.1:8788\/mcp/);
});
