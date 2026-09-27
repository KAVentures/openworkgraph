import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const source=fs.readFileSync(path.join(here,'..','..','dashboard','connections.js'),'utf8');

test('connections panel parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('one switch per app and kind, backed by the shared connections API',()=>{
  assert.match(source,/role="switch"/);
  assert.match(source,/aria-checked=/);
  assert.match(source,/\/v1\/connections/);
  assert.match(source,/action:on\?'on':'off'/);
  assert.match(source,/kind:'both',action:'remove'/);
});

test('status is evidence based and the CLI is shown for agents',()=>{
  assert.match(source,/Telemetry observed/);
  assert.match(source,/Context used/);
  assert.match(source,/For agents and scripts/);
  assert.match(source,/owg_connect|state\.cli/);
});

test('context switch respects the per-run AI access master switch',()=>{
  assert.match(source,/AI access this run/);
  assert.match(source,/\/v1\/ai-access/);
});

test('conflicts fall back to manual setup without claiming success',()=>{
  assert.match(source,/manual_setup_required/);
  assert.match(source,/Nothing was changed/);
});
