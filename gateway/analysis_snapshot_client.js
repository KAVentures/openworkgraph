/* Strict pre-upload contract: reject unreviewed fields before any transfer. */
(()=>{'use strict';
const FORMAT='openworkgraph.aggregate-analysis-snapshot.v1',MAX=Number.MAX_SAFE_INTEGER;
function fail(){throw new Error('Choose a bounded OWG aggregate snapshot; unexpected fields or invalid values cannot be uploaded.');}
function object(value,keys){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!keys.includes(k)))fail();}
function count(v){if(!Number.isSafeInteger(v)||v<0||v>1e12)fail();return v;}
function validate(doc){
 object(doc,['format','generated_at','level','columns','rows','matching_cases','matching_rows','rows_truncated','non_finite_results_withheld','metric_coverage','source_content_sha256','analysis_spec_sha256','fingerprint_algorithm','group_labels_included','source_independently_verified','execution_authorized']);
 if(doc.format!==FORMAT||typeof doc.generated_at!=='string'||!/(Z|[+-]\d\d:\d\d)$/.test(doc.generated_at)||!Number.isFinite(Date.parse(doc.generated_at))||!['cases','events'].includes(doc.level)||typeof doc.rows_truncated!=='boolean')fail();
 if(doc.fingerprint_algorithm!=='openworkgraph.discovery-source.v1'||doc.group_labels_included!==false||doc.source_independently_verified!==false||doc.execution_authorized!==false)fail();
 for(const key of ['source_content_sha256','analysis_spec_sha256'])if(typeof doc[key]!=='string'||!/^[a-f0-9]{64}$/.test(doc[key]))fail();
 if(!Array.isArray(doc.columns)||!doc.columns.length||doc.columns.length>6||!Array.isArray(doc.rows)||doc.rows.length>100)fail();
 const columns=doc.columns.map(c=>{
  object(c,['heading','kind','unit','aggregation','value_basis','metric_slot']);
  if(typeof c.heading!=='string'||!/^(Group|Measure) [1-6]$/.test(c.heading)||!['group','number'].includes(c.kind)||(c.heading.startsWith('Group')!==(c.kind==='group')))fail();
  if(c.unit!==null&&(typeof c.unit!=='string'||!c.unit.length||c.unit.length>256))fail();
  if(c.kind==='group'&&[c.unit,c.aggregation,c.value_basis,c.metric_slot].some(v=>v!==null))fail();
  if(c.kind==='number'&&(!['count','cases','share','median','p90','avg','min','max','sum'].includes(c.aggregation)||(['count','cases','share'].includes(c.aggregation)!==(c.value_basis===null))))fail();
  if(c.value_basis!==null&&!['duration_seconds','events','distinct_activities','case_metric'].includes(c.value_basis))fail();
  if((c.value_basis==='case_metric')!==(c.metric_slot!==null)||c.metric_slot!==null&&(!Number.isSafeInteger(c.metric_slot)||c.metric_slot<1||c.metric_slot>4))fail();
  return {heading:c.heading,kind:c.kind,unit:c.unit,aggregation:c.aggregation,value_basis:c.value_basis,metric_slot:c.metric_slot};
 });
 if(new Set(columns.map(c=>c.heading)).size!==columns.length||columns.filter(c=>c.kind==='group').length>2||!columns.some(c=>c.kind==='number'))fail();
 const rows=doc.rows.map(row=>{
  if(!Array.isArray(row)||row.length!==columns.length)fail();
  return row.map((v,i)=>{
   if(v===null)return null;
   if(columns[i].kind==='group'){if(typeof v!=='string'||!/^G[12]_[0-9]{1,3}$/.test(v))fail();}
   else if(typeof v!=='number'||!Number.isFinite(v)||Math.abs(v)>MAX)fail();
   return v;
  });
 });
 if(!Array.isArray(doc.metric_coverage)||doc.metric_coverage.length>4)fail();
 const coverage=doc.metric_coverage.map(c=>{
  object(c,['slot','with_value','without_value','missing_operand','zero_divisor','non_finite']);
  if(!Number.isSafeInteger(c.slot)||c.slot<1||c.slot>4)fail();
  const out={slot:c.slot};for(const key of ['with_value','without_value','missing_operand','zero_divisor','non_finite'])out[key]=count(c[key]);
  if(out.with_value+out.without_value!==doc.matching_cases||[out.missing_operand,out.zero_divisor,out.non_finite].some(v=>v>out.without_value))fail();return out;
 });
 if(new Set(coverage.map(c=>c.slot)).size!==coverage.length||columns.some(c=>c.metric_slot!==null&&!coverage.some(v=>v.slot===c.metric_slot)))fail();
 const result={format:FORMAT,generated_at:doc.generated_at,level:doc.level,columns,rows,matching_cases:count(doc.matching_cases),matching_rows:count(doc.matching_rows),rows_truncated:doc.rows_truncated,non_finite_results_withheld:count(doc.non_finite_results_withheld),metric_coverage:coverage,source_content_sha256:doc.source_content_sha256,analysis_spec_sha256:doc.analysis_spec_sha256,fingerprint_algorithm:doc.fingerprint_algorithm,group_labels_included:false,source_independently_verified:false,execution_authorized:false};
 if(new TextEncoder().encode(JSON.stringify(result)).length>128*1024)fail();return result;
}
window.owgAnalysisSnapshotClient={validate};
})();
