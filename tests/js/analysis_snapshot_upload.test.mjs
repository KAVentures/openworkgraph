import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const scope={window:{},TextEncoder};
vm.runInNewContext(fs.readFileSync(new URL('../../gateway/analysis_snapshot_client.js',import.meta.url),'utf8'),scope);
const validate=scope.window.owgAnalysisSnapshotClient.validate;
const doc=()=>({format:'openworkgraph.aggregate-analysis-snapshot.v1',generated_at:'2026-01-01T00:00:00Z',level:'cases',columns:[{heading:'Group 1',kind:'group',unit:null,aggregation:null,value_basis:null,metric_slot:null},{heading:'Measure 1',kind:'number',unit:'cases',aggregation:'count',value_basis:null,metric_slot:null}],rows:[['G1_1',4]],matching_cases:4,matching_rows:4,rows_truncated:false,non_finite_results_withheld:0,metric_coverage:[],source_content_sha256:'a'.repeat(64),analysis_spec_sha256:'b'.repeat(64),fingerprint_algorithm:'openworkgraph.discovery-source.v1',group_labels_included:false,source_independently_verified:false,execution_authorized:false});
test('only the complete bounded reviewed contract is accepted before upload',()=>{
 const input=doc(),out=validate(input);assert.deepEqual(JSON.parse(JSON.stringify(out)),input);assert.notEqual(out,input);
 for(const extra of [{raw_events:['secret']},{case_ids:['secret']},{cohort:{resource:'secret'}},{execution_authorized:true},{rows:[['private-person',4]]},{rows:[['G1_1',Infinity]]},{rows:[['G1_1',true]]},{rows:[['G1_1',2**60]]},{rows:[['G1_1',4,5]]},{rows:input.rows.concat(Array(100).fill(input.rows[0]))}])assert.throws(()=>validate({...input,...extra}),/cannot be uploaded/);
 for(const change of [{column:{private:1}},{coverage:{private:1}}]){
  const d=doc();if(change.column)Object.assign(d.columns[0],change.column);else d.metric_coverage=[{slot:1,with_value:4,without_value:0,missing_operand:0,zero_divisor:0,non_finite:0,...change.coverage}];assert.throws(()=>validate(d));
 }
});
test('coverage, units and timestamps have explicit bounds',()=>{
 const d=doc();d.metric_coverage=[{slot:1,with_value:3,without_value:1,missing_operand:1,zero_divisor:0,non_finite:0}];assert.equal(validate(d).metric_coverage[0].with_value,3);
 for(const alter of [x=>x.metric_coverage[0].with_value=4,x=>x.metric_coverage[0].zero_divisor=2,x=>x.generated_at='2026-01-01T00:00:00',x=>x.columns[0].unit='person',x=>x.columns[1].unit='x'.repeat(257)]){const copy=JSON.parse(JSON.stringify(d));alter(copy);assert.throws(()=>validate(copy));}
});

test('unit limits count Unicode scalars and refuse unpaired surrogates before transfer',()=>{const d=doc();d.columns[1].unit='🦉'.repeat(256);assert.equal(validate(d).columns[1].unit,d.columns[1].unit);d.columns[1].unit='\ud800';assert.throws(()=>validate(d));});
