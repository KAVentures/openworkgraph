import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {simArrivals}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('seasonal arrival patterns are built from form values',()=>{
  assert.equal(simArrivals({mode:'',timezone:'UTC'}),null);
  assert.deepEqual(plain(simArrivals({mode:'from_log',timezone:' Europe/Berlin '})),{from_log:true,timezone:'Europe/Berlin'});
  assert.deepEqual(plain(simArrivals({mode:'from_log',timezone:''})),{from_log:true});
  const office='0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 0 0 0 0 0 0 0';
  assert.deepEqual(plain(simArrivals({mode:'weights',timezone:'UTC',hourly:office,daily:'1,1,1,1,1,0,0'})),
    {hourly:office.split(' ').map(Number),daily:[1,1,1,1,1,0,0],timezone:'UTC'});
  assert.deepEqual(plain(simArrivals({mode:'weights',daily:'2; 1 1 1 1 0 0'})),{daily:[2,1,1,1,1,0,0]});
});

test('invalid arrival weights give readable errors',()=>{
  assert.throws(()=>simArrivals({mode:'weights'}),/hourly or daily/);
  assert.throws(()=>simArrivals({mode:'weights',hourly:'1 2 3'}),/24 non-negative hourly/);
  assert.throws(()=>simArrivals({mode:'weights',daily:'0 0 0 0 0 0 0'}),/not all zero/);
  assert.throws(()=>simArrivals({mode:'weights',daily:'1 1 1 1 1 -1 0'}),/7 non-negative/);
  assert.throws(()=>simArrivals({mode:'weights',daily:'1 1 1 1 1 x 0'}),/7 non-negative/);
  assert.throws(()=>simArrivals({mode:'monthly'}),/how cases arrive/);
});
