/* Explicit multi-window evidence selection. Counts are bundle-scoped, not unique work. */
const MAX_FILES=8,MAX_BYTES=8*1024*1024;
const RUN=/^execution:[a-f0-9]{16}$/,EVENT=/^event:[a-f0-9]{16}$/;
function require(ok,message){if(!ok)throw Error(message);}
function limits(files){
  require(Array.isArray(files)&&files.length<=MAX_FILES,'Choose at most eight original evidence sources.');
  require(files.every(f=>f&&Number.isSafeInteger(f.size)&&f.size>0&&typeof f.arrayBuffer==='function'),'Choose nonempty original evidence files.');
  require(files.reduce((n,f)=>n+f.size,0)<=MAX_BYTES,'Selected original sources exceed 8 MiB.');
}
async function scan(files){
  limits(files);const seen=new Set(),rows=[];
  for(const [i,file] of files.entries()){
    const bytes=await file.arrayBuffer();require(bytes.byteLength===file.size,'Evidence file size changed.');
    const raw=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
    require(new TextEncoder().encode(raw).length===file.size,'Evidence bytes could not be preserved.');
    require(!seen.has(raw),'This exact original evidence file is already selected.');seen.add(raw);
    const v=JSON.parse(raw),p=v?.provenance;
    require(v?.format==='openworkgraph.workflow-evidence.v1'&&v.canonical_evidence_included===true&&typeof p?.source_limit_reached==='boolean'&&Array.isArray(v.canonical_evidence)&&v.canonical_evidence.length>=1&&v.canonical_evidence.length<=25&&Array.isArray(v.executions)&&v.executions.length===v.canonical_evidence.length,'Expected an original canonical workflow evidence source.');
    const runIds=new Set(),occurrences=new Set(),summaryIds=new Set(v.executions.map(r=>r?.execution_id));
    let truncated=0,unverified=0,empty=0;
    require(summaryIds.size===v.executions.length&&[...summaryIds].every(x=>typeof x==='string'&&RUN.test(x)),'Invalid evidence summary population.');
    for(const r of v.canonical_evidence){
      require(typeof r?.execution_id==='string'&&RUN.test(r.execution_id)&&summaryIds.has(r.execution_id)&&!runIds.has(r.execution_id)&&Array.isArray(r.events)&&r.events.length<=160&&Number.isSafeInteger(r.observed_event_count)&&r.returned_event_count===r.events.length&&r.observed_event_count>=r.events.length&&r.events_truncated===(r.observed_event_count>r.events.length),'Invalid canonical execution coverage.');
      runIds.add(r.execution_id);truncated+=Number(r.events_truncated);unverified+=Number(r.event_membership_verified!==true);empty+=Number(!r.events.length);
      for(const e of r.events){
        require(typeof e?.event_ref==='string'&&EVENT.test(e.event_ref),'Invalid canonical event reference.');
        const key=r.execution_id+'/'+e.event_ref;require(!occurrences.has(key),'Duplicate event within a selected execution.');occurrences.add(key);
      }
    }
    rows.push({index:i+1,name:String(file.name??'Original evidence').slice(0,240),bytes:file.size,runIds,occurrences,sourceLimitReached:p.source_limit_reached,truncated,unverified,empty});
  }
  return rows;
}
export async function inspectEvidenceSources(files){
  const rows=await scan(files),overlaps=[];
  for(let i=0;i<rows.length;i++)for(let j=i+1;j<rows.length;j++){
    const a=rows[i],b=rows[j],runs=[...a.runIds].filter(x=>b.runIds.has(x)).length,occurrences=[...a.occurrences].filter(x=>b.occurrences.has(x)).length;
    if(runs||occurrences)overlaps.push({left:a.index,right:b.index,repeatedExecutionIds:runs,repeatedRunEventRefs:occurrences});
  }
  return {sourceCount:rows.length,totalBytes:rows.reduce((n,r)=>n+r.bytes,0),executionOccurrences:rows.reduce((n,r)=>n+r.runIds.size,0),eventOccurrences:rows.reduce((n,r)=>n+r.occurrences.size,0),sources:rows.map(({runIds,occurrences,...r})=>({...r,executionOccurrences:runIds.size,eventOccurrences:occurrences.size})),overlaps,representativeCoverageVerified:false,sourceAuthenticityVerified:false,crossBundleMembershipVerified:false};
}
export async function appendEvidenceSource(files,file){
  const next=[...files,file];await inspectEvidenceSources(next);return next;
}
export function mountSourceInventory(host,{getFiles}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const section=el('section');section.id='owgImplementationSourceInventory';
  const status=el('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');section.append(el('h4','Selected work sample coverage'),el('p','Counts describe the selected bundle-scoped occurrences, not unique work, a representative department sample, or verified source membership. Repeated references may overlap; inspect them before handing evidence to AI.'),status,output);host.append(section);
  let previous=null,epoch=0;
  async function refresh(){
    const files=getFiles().slice();if(previous&&files.length===previous.length&&files.every((f,i)=>f===previous[i]))return;
    previous=files;const ticket=++epoch;output.replaceChildren();status.textContent=files.length?'Inspecting the selected original files locally.':'No original sources selected.';
    if(!files.length)return;
    try{
      const report=await inspectEvidenceSources(files);if(ticket!==epoch)return;
      status.textContent=report.sourceCount+' sources; '+report.executionOccurrences+' execution occurrences; '+report.eventOccurrences+' event occurrences; '+report.totalBytes+' original bytes.';
      for(const r of report.sources)output.append(el('p','Source '+r.index+' — '+r.name+': '+r.executionOccurrences+' executions, '+r.eventOccurrences+' events; '+r.truncated+' truncated, '+r.unverified+' unverified membership, '+r.empty+' empty; source limit '+(r.sourceLimitReached?'reached':'not reported reached')+'.'));
      for(const p of report.overlaps)output.append(el('p','Possible overlap: sources '+p.left+' and '+p.right+' share '+p.repeatedExecutionIds+' execution IDs and '+p.repeatedRunEventRefs+' run/event references.'));
    }catch(e){if(ticket===epoch)status.textContent='Inventory unavailable: '+e.message+' Existing selections were not changed.';}
  }
  refresh();return {refresh};
}
