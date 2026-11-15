import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8');

test('signal history is available beside the local review inbox, not in public feeds',()=>{
  assert.match(source,/owgSignalTransitions/);
  assert.match(source,/owgSignalTransitionRefresh/);
  assert.match(source,/\/signal-history/);
  assert.match(source,/getJSON\(transitionBase\(\)\+'\?limit=100'\)/);
  assert.match(source,/generation!==state\.generation\|\|requestId!==transitionRequestId/);
});

test('signal lifecycle history is explicitly non-executing and bounded in UI',()=>{
  assert.match(source,/Locally recorded triggered and cleared transitions/);
  assert.match(source,/not live source monitoring or a notification feed/i);
  assert.match(source,/Up to 5,000 transitions are retained per log/);
  assert.match(source,/r\.truncated\?' \(limited to recent results\)'/);
});
