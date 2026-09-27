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

test('both Agents-tab scripts request the same history-filtered run list',()=>{
  const read=f=>fs.readFileSync(path.join(here,'..','..','dashboard',f),'utf8');
  for(const f of ['agent_observability_v090.js','v0571_polish.js']){
    const src=read(f);
    assert.match(src,/max_events_per_execution=100'\+agentHistoryParam\(\)/,f);
    assert.match(src,/owg_show_disconnected_agents/,f);
    assert.match(src,/hide_disconnected=true/,f);
  }
  assert.match(read('agent_control_plane.js'),/Show past runs from agents whose Observe is off/);
});

test('restart-needed and cloud-app guidance are shown',()=>{
  assert.match(source,/restart_needed/);
  assert.match(source,/Quit and reopen/);
  assert.match(source,/Lovable/);
});
