import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','v0571_polish.js'),'utf8');

test('agent observability dashboard JavaScript parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('dashboard adds a dedicated accessible Agents tab and panel before DOM ready',()=>{
  assert.match(source,/id='tab-agents'/);
  assert.match(source,/tab\.dataset\.tab='agents'/);
  assert.match(source,/id='panel-agents'/);
  assert.match(source,/panel\.dataset\.panel='agents'/);
  assert.match(source,/ensureAgentDashboardSurface\(\);/);
});

test('agent dashboard reads only privacy-safe structural execution traces',()=>{
  assert.match(source,/\/v1\/agent-execution-traces\?limit=50&evidence_limit=25000&max_events_per_execution=100/);
  assert.match(source,/Recent agent runs/);
  assert.match(source,/Agent reports/);
  assert.match(source,/View trace/);
  assert.match(source,/model calls, tool calls, handoffs, approvals, errors/i);
});

test('coverage wording preserves the observation boundary',()=>{
  assert.match(source,/missing signal means <em>not observed<\/em>/i);
  assert.match(source,/not proof of non-occurrence/i);
  assert.match(source,/Hidden reasoning observed: no/);
});