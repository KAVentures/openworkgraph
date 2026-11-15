import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {cohortSpec}=scope.window.__owgEventLogModel;
const plain=value=>JSON.parse(JSON.stringify(value));
test('numeric case attribute ranges are bounded and compose with other cohort criteria',()=>{
 assert.deepEqual(plain(cohortSpec({rangeName:'  amount 雪 ',rangeMin:'0',rangeMax:'2.5',resource:'team'})),{resource:'team',attribute_range:{name:'amount 雪',min:0,max:2.5}});
 assert.deepEqual(plain(cohortSpec({rangeName:'x',rangeMax:'-1e3'})),{attribute_range:{name:'x',max:-1000}});
 assert.deepEqual(plain(cohortSpec({rangeName:'x',rangeMin:0})),{attribute_range:{name:'x',min:0}});
 assert.deepEqual(plain(cohortSpec({rangeName:'\ufeffamount',rangeMin:0})),{attribute_range:{name:'\ufeffamount',min:0}});
 assert.deepEqual(plain(cohortSpec({})),{});
 for(const v of [{rangeMin:'2'},{rangeName:'x'},{rangeName:'x',rangeMin:'Infinity'},{rangeName:'x',rangeMin:'no'},{rangeName:'x',rangeMin:'2',rangeMax:'2'},{rangeName:'x',rangeMin:'3',rangeMax:'2'},{rangeName:'x'.repeat(201),rangeMin:'0'}])
  assert.throws(()=>cohortSpec(v));
});
