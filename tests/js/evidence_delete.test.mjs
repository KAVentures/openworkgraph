import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','evidence_delete.js'),'utf8');

test('evidence deletion dashboard script parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('evidence deletion uses inline confirmation and authenticated local endpoint',()=>{
  assert.match(source,/Delete last 15 minutes/);
  assert.match(source,/Delete last 1 hour/);
  assert.match(source,/Custom range/);
  assert.match(source,/\/v1\/evidence\/delete/);
  assert.match(source,/await window\.__owgAuthReady/);
  assert.match(source,/already synchronized to an organization Gateway is not automatically recalled/);
  assert.doesNotMatch(source,/window\.confirm\(/);
});

test('v0.57 release label is applied by the final dashboard layer',()=>{
  assert.match(source,/versionLabel/);
  assert.match(source,/v0\.57\.0/);
});
