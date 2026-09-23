import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const html = fs.readFileSync(path.resolve('dashboard/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

test('dashboard inline JavaScript parses', () => {
  assert.ok(scripts.length > 0, 'dashboard should contain inline JavaScript');
  for (const script of scripts) new Function(script);
});

test('dashboard is data-first with persistent accessible tabs', () => {
  for (const name of ['overview', 'evidence', 'connect', 'organization', 'export']) {
    assert.match(html, new RegExp(`role="tab"[^>]+data-tab="${name}"`));
    assert.match(html, new RegExp(`role="tabpanel"[^>]+data-panel="${name}"`));
  }
  assert.match(html, /sessionStorage\.setItem\(TAB_KEY/);
  assert.match(html, /ArrowRight/);
  assert.match(html, /aria-selected/);
  assert.doesNotMatch(html, /See how work actually happens/);
});

test('dashboard keeps one export surface and existing MCP onboarding hooks', () => {
  assert.equal((html.match(/id="includeRaw"/g) || []).length, 1);
  assert.equal((html.match(/id="includeRawTop"/g) || []).length, 0);
  assert.match(html, /data-panel="export"/);
  assert.match(html, /downloadExport\('xlsx'\)/);
  assert.match(html, /cursor:\/\/anysphere\.cursor-deeplink\/mcp\/install/);
  assert.match(html, /http:\/\/127\.0\.0\.1:8788\/mcp/);
});

test('dashboard exposes stable secure mount points and accessible evidence search', () => {
  assert.match(html, /id="aiAccessPanel"/);
  assert.match(html, /id="gatewayPanel"/);
  assert.match(html, /id="aiStatusChip"/);
  assert.match(html, /id="gatewayStatusChip"/);
  assert.match(html, /<label for="evidenceSearch">/);
  assert.match(html, /id="evidenceSearch"/);
});

test('surface rendering removes browser-container prefixes when a real surface is known', () => {
  assert.match(html, /function surfaceName\(value\)/);
  assert.match(html, /Google Chrome/);
  assert.match(html, /replace\(\/\^Chrome/);
});

test('overview hides navigation diagnostics and transitions under Evidence', () => {
  const overview = html.split('id="panel-overview"', 2)[1].split('id="panel-evidence"', 1)[0];
  assert.doesNotMatch(overview, /Navigation fragments/);
  assert.doesNotMatch(overview, /Common workflow transitions/);
  const evidence = html.split('id="panel-evidence"', 2)[1].split('id="panel-connect"', 1)[0];
  assert.match(evidence, /Navigation loops/);
  assert.match(evidence, /Transitions/);
});
