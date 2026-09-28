import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

test('Connections rows say where each agent telemetry signal stands', () => {
  const js = read('dashboard/connections.js');
  assert.doesNotThrow(() => new Function(js));
  for (const needle of ['/v1/agent-telemetry/diagnostics', 'claude_code_hooks', 'claude_code_otel_logs', 'codex_otel',
    'nothing received since OpenWorkGraph started', 'Hooks out of date']) assert.ok(js.includes(needle), needle);
});

test('the Recording pill does not hide a blind sensor', () => {
  const js = read('dashboard/gateway_panel.js');
  assert.doesNotThrow(() => new Function(js));
  assert.ok(js.includes('missing_permissions') && js.includes('permission missing') && js.includes("' · away'"));
});
