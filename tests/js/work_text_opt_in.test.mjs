import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = p => fs.readFileSync(path.join(root,p), 'utf8');
const manifest = JSON.parse(read('browser_extension/manifest.json'));
const workText = read('browser_extension/work_text_capture.js');
const background = read('browser_extension/background.js');
const serverGuard = read('server/secure_app.py');
const privacyUi = read('dashboard/basic_mode.js');
const route = read('server/browser_signal_routes.py');

test('browser extension contains the separate work-text script and both scripts parse', () => {
  assert.ok(manifest.content_scripts[0].js.includes('work_text_capture.js'));
  assert.doesNotThrow(() => new Function(workText));
  assert.doesNotThrow(() => new Function(background));
  assert.doesNotThrow(() => new Function(privacyUi));
});

test('work text is off by default and only enabled from explicit local settings', () => {
  assert.match(workText, /let enabled = false/);
  assert.match(workText, /openworkgraph_work_text_capture_v1/);
  assert.match(workText, /document\.visibilityState/);
  assert.match(background, /fresh\?\.work_text_capture === true/);
  assert.match(privacyUi, /id="pvWorkTextSwitch"/);
  assert.match(privacyUi, /id="pvWorkTextAiSwitch"/);
  assert.match(privacyUi, /\/v1\/work-text\/policy/);
  assert.match(privacyUi, /confirm\('Enable automatic browser work-text capture/);
});

test('work text bypasses structural event queue and remote Gateway', () => {
  assert.match(background, /async function deliverWorkTextSnapshot/);
  assert.match(background, /postDirect\("\/v1\/work-text\/ingest"/);
  assert.ok(!workText.includes('workflow_observer_event'));
  assert.ok(!workText.includes('chrome.storage.local.set('));
  assert.ok(!workText.includes('openworkgraph_pending_browser_events'));
  assert.match(serverGuard, /\("POST", "\/v1\/work-text\/ingest"\)/);
  assert.ok(!route.includes('gateway.db'));
});

test('sensitive editing surfaces are excluded before sending', () => {
  assert.match(workText, /type="password"/);
  assert.match(workText, /closest\?\.\('form'\)/);
  assert.match(workText, /contenteditable/);
  assert.match(workText, /getComputedStyle/);
  assert.match(workText, /sensitiveSurface/);
});
