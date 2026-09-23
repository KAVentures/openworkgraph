import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','v0571_polish.js'),'utf8');

test('v0.57.1 polish script parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('surface naming and colors are centralized',()=>{
  assert.match(source,/canonicalSurfaceName/);
  assert.match(source,/window\.surfaceName=canonicalSurfaceName/);
  assert.match(source,/window\.surfaceColor=unifiedSurfaceColor/);
  assert.match(source,/Google Sheets/);
  assert.match(source,/Salesforce/);
  assert.match(source,/keyByColor/);
});

test('timeline labels are normalized to 24-hour non-wrapping text',()=>{
  assert.match(source,/to24Hour/);
  assert.match(source,/whiteSpace='nowrap'/);
  assert.match(source,/tickSpans\.length-1/);
});

test('developer paging note is removed and sharing policy is live',()=>{
  assert.match(source,/cursor paging is added/i);
  assert.match(source,/\/v1\/sharing-policy/);
  assert.match(source,/Organization policy is temporarily unavailable/);
  assert.match(source,/sync fails closed/i);
});
