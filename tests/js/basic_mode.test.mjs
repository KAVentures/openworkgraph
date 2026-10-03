import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', '..');
const source = fs.readFileSync(path.join(root, 'dashboard', 'basic_mode.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'dashboard', 'index.html'), 'utf8');

test('basic mode script parses', () => {
  assert.doesNotThrow(() => new Function(source));
});

test('basic is the default and advanced is remembered locally', () => {
  assert.match(source, /owg_view_mode_v1/);
  assert.match(source, /=== 'advanced' \? 'advanced' : 'basic'/);
  assert.match(source, /body\.classList\.toggle\('mode-basic'/);
  assert.match(html, /body\.mode-basic \.owg-basic-hide\{display:none!important\}/);
});

test('basic hides by class only and adds the Privacy tab before the base wiring', () => {
  assert.doesNotMatch(source, /\.remove\(\)\s*;\s*\/\/ hide/);
  assert.match(source, /ensurePrivacyTab\(\);\n\n  function install/);
  assert.match(source, /data-tab|dataset\.tab = 'privacy'/);
});

test('privacy tab has every basic control', () => {
  for (const id of ['pvRetention', 'pvAiSwitch', 'pvHistorySwitch', 'pvRestartSwitch', 'pvRedactSwitch', 'pvBrowserRow', 'pvNeverRecord', 'pvDelete']) {
    assert.ok(source.includes(id), id);
  }
  // One retention choice also sets run memory, so "session only" keeps nothing.
  assert.match(source, /\/v1\/run-memory\/policy', send\('PUT', \{enabled: kind !== 'ephemeral'/);
  // Destructive choices ask first.
  assert.match(source, /confirm\('Keep nothing after each session\?/);
  assert.match(source, /Delete recorded activity\?/);
});

test('stale dashboard tabs explain how to recover', () => {
  assert.match(source, /owgAuthBanner/);
  assert.match(source, /earlier start of OpenWorkGraph/);
});
