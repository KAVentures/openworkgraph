import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','evidence_paging.js'),'utf8');

test('Evidence paging script parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('Evidence uses 100-row cursor paging and server-side filters',()=>{
  assert.match(source,/const PAGE_SIZE=100/);
  assert.match(source,/\/v1\/evidence\?/);
  assert.match(source,/params\.set\('cursor',cursor\)/);
  assert.match(source,/params\.set\('q',q\)/);
  assert.match(source,/params\.set\('surface',selectedSurface\)/);
  assert.doesNotMatch(source,/recent_evidence/);
});

test('Evidence pager preserves cursor history and supports previous/next',()=>{
  assert.match(source,/cursors=\[null\]/);
  assert.match(source,/pageIndex-=1/);
  assert.match(source,/cursors\.push\(next\)/);
  assert.match(source,/Showing \$\{Number\(payload\.position_start/);
  assert.match(source,/Previous/);
  assert.match(source,/Next/);
});

test('Evidence search is debounced and diagnostic views remain summary-based',()=>{
  assert.match(source,/schedulePageLoad\(250\)/);
  assert.match(source,/frequent_sequences/);
  assert.match(source,/transitions/);
  assert.match(source,/data-evidence-view/);
});
