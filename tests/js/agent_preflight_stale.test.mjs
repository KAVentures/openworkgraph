import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const source=fs.readFileSync(path.join(root,'dashboard/agent_preflight.js'),'utf8');

test('agent preflight parses and keeps explicit authenticated local-only preflight',()=>{
  assert.doesNotThrow(()=>new Function(source));
  assert.match(source,/window\.__owgAuthReady/);
  assert.match(source,/\/v1\/process-design\/preflight/);
  assert.match(source,/No agent, tool, or business action was executed/);
});

test('in-flight preflight cannot overwrite a changed file selection',()=>{
  assert.match(source,/selectionGeneration\+\+/);
  assert.match(source,/if\(generation!==selectionGeneration\|\|file\.files\?\.\[0\]!==selected\)return;[\s\S]*?const response=await fetch/);
  assert.match(source,/generation!==selectionGeneration\|\|file\.files\?\.\[0\]!==selected/);
  assert.match(source,/if\(generation===selectionGeneration\)run\.disabled=false/);
  assert.match(source,/if\(generation===selectionGeneration\)[\s\S]*?status\.textContent='Preflight unavailable/);
});
