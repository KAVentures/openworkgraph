import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','agent_control_plane.js'),'utf8');

test('agent control-plane JavaScript parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('connect page separates context access from agent observation',()=>{
  assert.match(source,/Give AI my context/);
  assert.match(source,/Observe an agent/);
  assert.match(source,/does <strong>not<\/strong> automatically let OpenWorkGraph observe/i);
  assert.match(source,/MCP context access and agent observation are separate connections/);
});

test('connection status is based on observed telemetry rather than config presence',()=>{
  assert.match(source,/Status means telemetry observed/);
  assert.match(source,/No telemetry observed/);
  assert.match(source,/Telemetry observed/);
  assert.match(source,/\/v1\/agent-execution-traces\?limit=50&evidence_limit=25000&max_events_per_execution=1/);
  assert.doesNotMatch(source,/config file exists.*connected/i);
});

test('dashboard exposes safe setup flows for supported agent surfaces',()=>{
  assert.match(source,/Claude Code/);
  assert.match(source,/Codex/);
  assert.match(source,/OpenAI Agents SDK/);
  assert.match(source,/OpenTelemetry \/ custom/);
  assert.match(source,/\/v1\/agent-setup/);
  assert.match(source,/Prompts not collected/);
  assert.match(source,/Reasoning not collected/);
  assert.match(source,/Tool arguments not collected/);
  assert.match(source,/Tool results not collected/);
});
