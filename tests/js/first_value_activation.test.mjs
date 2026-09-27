import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve('dashboard/first_value_activation.js'), 'utf8');

test('first-value activation JavaScript parses standalone', () => {
  assert.doesNotThrow(() => new Function(source));
});

test('first-value activation is read-only and evidence driven', () => {
  assert.match(source, /\/v1\/summary\?scope=current&limit=500/);
  assert.match(source, /\/v1\/agent-execution-traces\?/);
  assert.match(source, /\/v1\/ai-access/);
  assert.match(source, /state\.ready/);
  assert.match(source, /Observed evidence only/);
  assert.doesNotMatch(source, /method:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/i);
});

test('first-value activation never captures content or hidden reasoning', () => {
  for (const forbidden of [
    'getDisplayMedia(', 'getUserMedia(', 'clipboard.read(', 'clipboard.readText(',
    '/agent-ingest/', '/v1/events', '/v1/context-events'
  ]) assert.equal(source.includes(forbidden), false, forbidden);
  assert.match(source, /does not infer intent or read hidden reasoning/);
});

test('first-value guide is dismissible and does not trap advanced navigation', () => {
  assert.match(source, /owg_first_value_dismissed_v1/);
  assert.match(source, /window\.activateTab\?\.\('evidence'\)/);
  assert.match(source, /window\.activateTab\?\.\('connect'\)/);
  assert.match(source, /window\.activateTab\?\.\('organization'\)/);
});
