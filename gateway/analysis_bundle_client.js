/* Strict local review before paired company publication. The bundle itself is never posted. */
(()=>{'use strict';
const FORMAT='openworkgraph.analysis-handoff-bundle.v1';
function validate(value){
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Choose a reviewed OWG paired handoff.');
 const fields=['format','aggregate_snapshot','case_dataset','source_independently_verified',
               'case_attachment_authorized','organization_publication_authorized'];
 if(Object.keys(value).some(k=>!fields.includes(k))||fields.some(k=>!(k in value))||
    value.format!==FORMAT||value.source_independently_verified!==false||
    value.case_attachment_authorized!==false||value.organization_publication_authorized!==false)
   throw new Error('Paired handoff has unexpected fields or unsupported authority claims.');
 const aggregate_snapshot=window.owgAnalysisSnapshotClient.validate(value.aggregate_snapshot);
 const case_dataset=window.owgAnalysisCases.validate(value.case_dataset);
 if(aggregate_snapshot.level!=='cases'||
    aggregate_snapshot.source_content_sha256!==case_dataset.source_content_sha256||
    aggregate_snapshot.analysis_spec_sha256!==case_dataset.analysis_spec_sha256||
    aggregate_snapshot.matching_cases!==case_dataset.cases.length)
   throw new Error('The aggregate and case dataset do not describe the same selected source and analysis.');
 const out={format:FORMAT,aggregate_snapshot,case_dataset,
            source_independently_verified:false,case_attachment_authorized:false,
            organization_publication_authorized:false};
 if(new TextEncoder().encode(JSON.stringify(out)).length>640*1024)
   throw new Error('Paired handoff exceeds the 640 KiB limit.');
 return out;
}
window.owgAnalysisBundleClient={validate};
})();
