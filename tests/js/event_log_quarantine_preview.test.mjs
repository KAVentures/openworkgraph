import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../dashboard/event_log_workspace.js', import.meta.url), 'utf8');
const start = source.indexOf('  function quarantineBlock(){');
const end = source.indexOf('\n  function ', start + 25);
assert.ok(start >= 0 && end > start, 'exercise the actual dashboard quarantine block');

function harness(previewJSON) {
  const buttons = new Map();
  const inputs = new Map();
  const requests = [];
  const messages = [];
  const state = {logId:'log_0123456789abcdef', generation:1};
  const node = (tag, value='') => ({
    tag, textContent:String(value ?? ''), style:{}, children:[],
    append(...items){this.children.push(...items);},
    setAttribute(){}, replaceChildren(...items){this.children=items;}
  });
  const sandbox = {
    state,
    el:(tag,value)=>node(tag,value),
    show:()=>{},
    getJSON:async()=>({rows_kept:2, decisions:[], rows:[], reasons:{}, note:'Local review'}),
    input:(id,kind,value='')=>{
      const x={id,kind,value,disabled:false,listeners:{},
        addEventListener(type,handler){this.listeners[type]=handler;}};
      inputs.set(id,x);
      return x;
    },
    button:(label)=>{
      const b={label,disabled:false,onclick:null};
      buttons.set(label,b);
      return b;
    },
    field:(label,control)=>node('field',label),
    table:()=>node('table'),
    reasonText:()=> 'none',
    REASON_LABELS:{},
    api:async(url,options)=>{
      requests.push({url,options,body:JSON.parse(options.body)});
      return {json:async()=>url.endsWith('/preview')
        ? (typeof previewJSON === 'function' ? previewJSON() : previewJSON)
        : {decision:{rows_released:1,rows_considered:2,rows_still_kept:1,
          events_added:1,naive_timestamp_offset:'+01:00'}}};
    },
    refreshLogs:async()=>{},
    analyse:async()=>{},
    status:text=>messages.push(text)
  };
  const build = runInNewContext(source.slice(start,end) + '\nquarantineBlock', sandbox);
  return {build,buttons,inputs,requests,messages,state};
}

const preview = {format:'openworkgraph.quarantine-release-preview.v1',
  naive_timestamp_offset:'+01:00',rows_considered:2,rows_eligible:1,
  rows_still_rejected:1,expected_preview_sha256:'a'.repeat(64)};
const settle = () => new Promise(resolve=>setImmediate(resolve));

test('dashboard requires a read-only preview before explicit guarded release', async()=>{
  const h=harness(preview);
  h.build();
  await settle();
  const release=h.buttons.get('Release rows');
  const check=h.buttons.get('Preview release');
  assert.equal(release.disabled,true);
  await check.onclick();
  assert.equal(h.requests.length,1);
  assert.match(h.requests[0].url,/\/quarantine\/preview$/);
  assert.deepEqual(h.requests[0].body,{naive_timestamp_offset:'+01:00'});
  assert.equal(release.disabled,false);
  await release.onclick();
  assert.equal(h.requests.length,2);
  assert.match(h.requests[1].url,/\/quarantine\/release$/);
  assert.deepEqual(h.requests[1].body,{naive_timestamp_offset:'+01:00',
    expected_preview_sha256:'a'.repeat(64)});
  assert.equal(release.disabled,true);
  assert.match(h.messages.at(-1),/Released 1 of 2/);
});

test('offset edits invalidate the preview and require a fresh request', async()=>{
  const h=harness(preview);
  h.build();
  await settle();
  const release=h.buttons.get('Release rows');
  await h.buttons.get('Preview release').onclick();
  assert.equal(release.disabled,false);
  const offset=h.inputs.get('owgQuarantineOffset');
  offset.value='+02:00';
  offset.listeners.input();
  assert.equal(release.disabled,true);
  await release.onclick();
  assert.equal(h.requests.length,1);
  await h.buttons.get('Preview release').onclick();
  assert.equal(release.disabled,true); // response for a different offset cannot authorize release
});

test('switching logs while a preview is pending refuses stale UI authorization', async()=>{
  let resolvePreview;
  const delayed=new Promise(resolve=>{resolvePreview=resolve;});
  const h=harness(()=>delayed);
  h.build();
  await settle();
  const pending=h.buttons.get('Preview release').onclick();
  h.state.generation++;
  resolvePreview(preview);
  await pending;
  assert.equal(h.buttons.get('Release rows').disabled,true);
  assert.equal(h.requests.length,1);
});

test('discard action invalidates the current release preview', async()=>{
  const h=harness(preview);
  h.build();
  await settle();
  await h.buttons.get('Preview release').onclick();
  assert.equal(h.buttons.get('Release rows').disabled,false);
  await h.buttons.get('Discard kept rows').onclick();
  assert.equal(h.buttons.get('Release rows').disabled,true);
  assert.match(h.requests.at(-1).url,/\/quarantine\/discard$/);
});
