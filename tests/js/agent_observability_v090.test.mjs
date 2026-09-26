import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../../dashboard/agent_observability_v090.js', import.meta.url), 'utf8');

assert.match(source, /signal_capabilities/);
assert.match(source, /observed_operation_counts/);
assert.match(source, /usage_totals/);
assert.match(source, /models_observed/);
assert.match(source, /0 = observed zero/);
assert.match(source, /— = this integration cannot observe that signal/);
assert.match(source, /model_call/);
assert.match(source, /human_approval_requested/);

// Syntax-only compile. The script is an IIFE and expects a browser DOM, so do
// not execute it in Node; compilation still catches malformed template strings
// and accidental syntax regressions.
new vm.Script(source, {filename: 'agent_observability_v090.js'});
