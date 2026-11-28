/* Explicit local evidence selection; candidate groups are navigation, not truth. */
const LIMIT = 2 * 1024 * 1024;
const RUN = /^execution:[a-f0-9]{16}$/;
const WINDOW = /^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,3})?)?(?:Z|[+-]\d\d:\d\d)$/;
const FORMAT = 'openworkgraph.workflow-evidence.v1';

export function evidenceWindow(since,until){
  if(!WINDOW.test(since)||!WINDOW.test(until))throw Error('Use explicit ISO 8601 timestamps with offsets.');
  const a=Date.parse(since),b=Date.parse(until);
  if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a||b-a>7*86400000)throw Error('Choose a positive window of at most seven days.');
  return {since:new Date(a).toISOString(),until:new Date(b).toISOString()};
}
export function selectedExecutionIds(text){
  const ids=text.trim().split(/[\s,]+/).filter(Boolean);
  if(ids.length<1||ids.length>12||ids.some(id=>!RUN.test(id))||new Set(ids).size!==ids.length)
    throw Error('Select one to twelve distinct exact execution IDs.');
  return ids;
}
function exactIds(values,ids){
  return Array.isArray(values)&&values.length===ids.length&&new Set(values).size===ids.length&&values.every(x=>ids.includes(x));
}
export function checkSelectedBundle(value,ids,window){
  const p=value?.provenance,s=value?.selector;
  if(value?.format!==FORMAT||value.canonical_evidence_included!==true||s?.selection_mode!=='explicit_executions'||
    s.family_selection_is_ground_truth!==false||s.selected_execution_count!==ids.length||!exactIds(s.execution_ids,ids)||
    p?.source!=='canonical_local_event_store'||p.requested_window?.since!==window.since||p.requested_window?.until!==window.until||
    !exactIds(p.selected_execution_ids,ids)||typeof p.source_limit_reached!=='boolean'||
    !Array.isArray(value.executions)||!exactIds(value.executions.map(r=>r?.execution_id),ids)||
    !Array.isArray(value.canonical_evidence)||!exactIds(value.canonical_evidence.map(r=>r?.execution_id),ids))
    throw Error('Returned evidence does not match every explicitly selected execution and window. Existing sources were not replaced.');
  let gaps=Number(p.source_limit_reached),events=0;
  for(const r of value.canonical_evidence){
    if(!Array.isArray(r.events)||r.events.length>100||!Number.isSafeInteger(r.observed_event_count)||
      r.observed_event_count<r.events.length||r.returned_event_count!==r.events.length||
      r.events_truncated!==(r.observed_event_count>r.events.length))throw Error('Unexpected canonical evidence coverage.');
    events+=r.events.length;
    gaps+=Number(r.events_truncated)+Number(r.event_membership_verified!==true)+Number(!r.events.length);
  }
  if(!events)throw Error('No canonical events returned; choose different executions.');
  return {events,gaps};
}
async function boundedJSON(path,query){
  await window.__owgAuthReady;
  const response=await fetch(path+'?'+new URLSearchParams(query),{cache:'no-store'});
  if(!response.ok)throw Error('Local evidence request refused ('+response.status+'). Narrow the window or check local access.');
  if(!response.body)throw Error('No local evidence response.');
  const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true});
  let size=0,text='';
  try{
    while(true){
      const {done,value}=await reader.read();if(done)break;
      size+=value.byteLength;
      if(size>LIMIT){await reader.cancel();throw Error('Local evidence exceeds 2 MiB; narrow the window or select fewer executions.');}
      text+=decoder.decode(value,{stream:true});
    }
    text+=decoder.decode();
  }finally{reader.releaseLock();}
  if(!size)throw Error('Empty local evidence response.');
  return {text,value:JSON.parse(text),size};
}

export function mountImplementationEvidencePicker(host,{getBusy,lock,onSelected,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const since=el('input'),until=el('input'),ids=el('textarea');ids.rows=4;ids.maxLength=1024;
  since.placeholder='2026-11-28T08:00:00+01:00';until.placeholder='2026-11-28T12:00:00+01:00';
  const discover=button('Find local execution candidates'),load=button('Use selected executions (replace sources)'),save=button('Download last selected original evidence');
  const status=el('p','Set the explicit time window. No capture, query or sharing occurs automatically.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const candidates=el('div'),selection=el('p');let rows=[],last=null,epoch=0;
  function parsed(){try{return {window:evidenceWindow(since.value.trim(),until.value.trim()),ids:selectedExecutionIds(ids.value)};}catch(_){return null;}}
  function refresh(){
    const busy=getBusy();for(const n of [since,until,ids])n.disabled=busy;
    let validWindow=false;try{evidenceWindow(since.value.trim(),until.value.trim());validWindow=true;}catch(_){}
    discover.disabled=busy||!validWindow;load.disabled=busy||!parsed();save.disabled=busy||!last;
    let selected=[];try{selected=selectedExecutionIds(ids.value);}catch(_){}
    selection.textContent=selected.length?selected.length+' explicitly selected execution IDs.':'Enter or select one to twelve unique execution IDs.';
    for(const {node,id} of rows){node.disabled=busy||selected.includes(id)||selected.length>=12;}
  }
  function resetWindow(){epoch++;rows=[];candidates.replaceChildren();ids.value='';last=null;status.textContent='Window changed. Find candidates or enter exact IDs; existing handoff sources remain unchanged.';refresh();}
  for(const n of [since,until])n.addEventListener('input',resetWindow);
  ids.addEventListener('input',()=>{epoch++;last=null;refresh();});
  discover.onclick=async()=>{
    if(discover.disabled)return;
    const w=evidenceWindow(since.value.trim(),until.value.trim()),ticket=++epoch;
    rows=[];candidates.replaceChildren();lock(true);status.textContent='Reading bounded local navigation candidates.';
    try{
      const {value:v}=await boundedJSON('/v1/workflow-evidence/families',{...w,source_event_limit:'25000',min_runs:'1',limit:'20'});
      if(ticket!==epoch)return;
      if(v?.format!==FORMAT||v.authoritative!==false||v.family_grouping_is_navigation_only!==true||
        v.requested_window?.since!==w.since||v.requested_window?.until!==w.until||typeof v.source_limit_reached!=='boolean'||
        !Array.isArray(v.families)||v.families.length>20||v.families_returned!==v.families.length||
        !Number.isSafeInteger(v.families_total)||v.families_total<v.families.length)throw Error('Unexpected local navigation response.');
      const seen=new Set(),sections=[];
      for(const f of v.families){
        if(f?.family_is_ground_truth!==false||typeof f.family_key!=='string'||f.family_key.length>200||
          !Array.isArray(f.executions)||f.executions.length>12||!Number.isSafeInteger(f.execution_count)||f.execution_count<f.executions.length)
          throw Error('Unexpected candidate family.');
        const section=el('section');section.append(el('h4','Candidate family '+f.family_key+' — '+f.execution_count+' observed executions'));
        for(const r of f.executions){
          if(typeof r?.execution_id!=='string'||!RUN.test(r.execution_id))throw Error('Unexpected candidate execution ID.');
          if(seen.has(r.execution_id))continue;
          seen.add(r.execution_id);
          const node=button('Add '+r.execution_id);const id=r.execution_id;
          const label=el('p',(r.actor_kind==='human'?'Human':r.actor_kind==='agent'?'Agent':'Unknown actor')+' · '+String(r.started_at??'Unknown start').slice(0,100)+' · ');
          node.onclick=()=>{
            if(node.disabled||getBusy())return;
            let selected=[];if(ids.value.trim()){try{selected=selectedExecutionIds(ids.value);}catch(e){status.textContent=e.message;return;}}
            if(selected.includes(id)||selected.length>=12)return;
            ids.value=[...selected,id].join('\n');ids.dispatchEvent(new Event('input'));
          };
          rows.push({node,id});label.append(node);section.append(label);
        }
        sections.push(section);
      }
      candidates.append(...sections);
      status.textContent='Returned '+v.families_returned+' of '+v.families_total+' candidate families; at most 12 examples per family. '+(v.source_limit_reached?'Source row limit reached. ':'')+'These are navigation hints, not verified tasks or complete coverage. Select individual examples or enter an exact ID.';
    }catch(e){if(ticket===epoch){rows=[];candidates.replaceChildren();status.textContent=e.message;}}
    finally{lock(false);refresh();}
  };
  load.onclick=async()=>{
    const selected=parsed();if(load.disabled||!selected)return;
    const ticket=++epoch;lock(true);status.textContent='Retrieving the exact selected local execution evidence.';
    try{
      const r=await boundedJSON('/v1/workflow-evidence',{...selected.window,execution_ids:selected.ids.join(','),source_event_limit:'25000',max_runs:String(selected.ids.length),max_events_per_run:'100',include_canonical_evidence:'true'});
      if(ticket!==epoch)return;
      const counts=checkSelectedBundle(r.value,selected.ids,selected.window);
      const f=new File([r.text],'owg-selected-executions.json',{type:'application/json'});
      if(f.size!==r.size)throw Error('Evidence byte preservation failed.');
      onSelected(f);last=r.text;
      status.textContent='Selected '+selected.ids.length+' executions and '+counts.events+' canonical events; '+counts.gaps+' coverage flags. Original source loaded into the handoff below. No AI request or upload occurred.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}
    finally{lock(false);refresh();}
  };
  save.onclick=()=>{if(!getBusy()&&last)download('owg-selected-executions.json','application/json',last);};
  const details=el('details');details.id='owgImplementationEvidencePicker';
  details.append(el('summary','Select observed work directly from OWG'),
    el('p','Optional alternative to importing files. Search within seven days; derived families and limited examples are navigation only. Select the actual executions you want the external AI to interpret. Editing the picker does not replace existing handoff sources until you explicitly click Use selected executions. No automatic upload.'),
    field('Evidence window start (ISO 8601 with offset)',since),field('Evidence window end (ISO 8601 with offset)',until),discover,candidates,
    field('Exact execution IDs (one per line or comma separated)',ids),selection,load,status,save);
  host.append(details);refresh();return {refresh};
}
