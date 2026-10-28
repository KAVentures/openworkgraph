import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {objectRule}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('each object rule template keeps only its own fields',()=>{
  assert.deepEqual(plain(objectRule({template:'event_cardinality',activity:'Pay',objectType:'invoice',relatedType:'order',min:'1',max:'1'})),
    {template:'event_cardinality',object_type:'invoice',activity:'Pay',min:1,max:1});
  assert.deepEqual(plain(objectRule({template:'object_cardinality',objectType:'order',relatedType:'item',via:'',min:'1',max:''})),
    {template:'object_cardinality',object_type:'order',related_type:'item',min:1});
  assert.deepEqual(plain(objectRule({template:'object_cardinality',objectType:'item',relatedType:'delivery',via:'Ship',min:'',max:'1'})),
    {template:'object_cardinality',object_type:'item',related_type:'delivery',via_activity:'Ship',min:0,max:1});
  assert.deepEqual(plain(objectRule({template:'synchronization',activity:'Ship',objectType:'order',relatedType:'item',required:'Pick item',min:'4'})),
    {template:'synchronization',object_type:'order',related_type:'item',activity:'Ship',required_activity:'Pick item'});
});

test('incomplete or contradictory rules are refused with a readable reason',()=>{
  assert.throws(()=>objectRule({template:'event_cardinality',activity:'Pay',objectType:'invoice',min:'',max:''}),/minimum above 0 or a maximum/);
  assert.throws(()=>objectRule({template:'event_cardinality',activity:'Pay',objectType:'invoice',min:'3',max:'1'}),/maximum at least the minimum/);
  assert.throws(()=>objectRule({template:'object_cardinality',objectType:'order',relatedType:'',min:'1'}),/related object type/);
  assert.throws(()=>objectRule({template:'synchronization',activity:'A',objectType:'t',relatedType:'t',required:'A'}),/different activity B/);
  assert.throws(()=>objectRule({template:'event_cardinality',activity:'',objectType:'invoice',min:'1'}),/activity A/);
});

test('supplied graph rules keep literal direction/qualifier and omit event semantics',()=>{
  const base={template:'supplied_object_cardinality',objectType:'order',relatedType:'item',min:'1',max:'2',activity:'Must not join',via:'Must not infer',required:'Must not synchronize'};
  assert.deepEqual(plain(objectRule(base)),{template:'supplied_object_cardinality',object_type:'order',related_type:'item',direction:'outgoing',min:1,max:2});
  for(const qualifier of ['',' ',' contains ','🐱'.repeat(80)]){
    const r=plain(objectRule({...base,direction:'incoming',exactQualifier:true,qualifier}));
    assert.equal(r.qualifier,qualifier);assert.equal(r.direction,'incoming');assert.equal('via_activity' in r,false);
  }
  assert.throws(()=>objectRule({...base,direction:'transitive'}),/directions/);
  assert.throws(()=>objectRule({...base,exactQualifier:true,qualifier:'🐱'.repeat(81)}),/80 characters/);
  assert.throws(()=>objectRule({...base,exactQualifier:true,qualifier:'\ud800'}),/80 characters/);
  assert.throws(()=>objectRule({...base,min:'1000001',max:''}),/1,000,000/);
});
