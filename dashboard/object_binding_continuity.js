/* Session-only local OCEL joint-event binding explorer.
 * All imported labels/IDs are displayed via textContent; no HTML interpolation.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let activeLog = '', relationships = [], generation = 0, queryGeneration = 0, activityGeneration = 0;
  const idOK = id => /^log_[0-9a-f]{16}$/.test(String(id || ''));
  const number = value => Number(value || 0).toLocaleString();
  const seconds = value => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Unknown';
    if (value >= 86400) return (value / 86400).toFixed(2) + ' days';
    if (value >= 3600) return (value / 3600).toFixed(2) + ' h';
    if (value >= 60) return (value / 60).toFixed(1) + ' min';
    return Number(value).toFixed(1) + ' s';
  };
  function session() {
    try { return sessionStorage.getItem('owg_dashboard_session_v1') || ''; }
    catch (_) { return ''; }
  }
  async function api(path, body = null) {
    const token = session();
    if (!token) throw Error('Local dashboard session missing; reopen from the OWG launcher in this tab.');
    const headers = {'Authorization': 'OWG-Session ' + token};
    if (body !== null) headers['Content-Type'] = 'application/json';
    const response = await fetch(path,{method:body === null ? 'GET' : 'POST',
      headers,body:body===null?undefined:JSON.stringify(body),
      credentials:'same-origin',cache:'no-store'});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      if ([401,403].includes(response.status)) throw Error('Your local session expired. Reopen OWG from its launcher.');
      throw Error(String(data.detail || data.error || 'Local analysis could not complete.').slice(0,400));
    }
    return data;
  }
  function options(id, values, empty) {
    const select = $(id); select.replaceChildren();
    const zero = document.createElement('option');
    zero.value='';zero.textContent=empty;select.appendChild(zero);
    for (const item of values) {
      const node=document.createElement('option');
      node.value=String(item.value);node.textContent=String(item.label);select.appendChild(node);
    }
    select.disabled = values.length === 0;
    if(values.length) select.value=values[0].value;
  }
  function datalist(id, values) {
    const node=$(id);node.replaceChildren();
    for(const value of [...new Set(values)].sort().slice(0,500)) {
      const el=document.createElement('option');el.value=String(value);node.appendChild(el);
    }
  }
  function invalidate(reason) {
    queryGeneration++;
    $('analyze').disabled=false;
    $('results').hidden=true;
    $('rows').replaceChildren();$('metrics').replaceChildren();
    $('provenance').textContent='';
    if(reason) $('status').textContent=reason;
  }
  async function reload() {
    const current=++generation;
    activeLog='';relationships=[];
    invalidate('Reading locally retained object-centric logs…');
    options('logSelect',[],'Loading logs…');
    options('relationship',[],'Choose log first');
    try {
      const data=await api('/v1/event-logs');
      if(current!==generation)return;
      const logs=(data.logs||[]).filter(row=>row&&idOK(row.log_id)&&['ocel','ocelxml'].includes(row.source_format));
      options('logSelect',logs.map(r=>({value:r.log_id,label:String(r.name||r.log_id)+' · '+number(r.events)+' events'})),'No saved OCEL log');
      if(!logs.length) {
        $('status').textContent='No retained OCEL log. Import one from the OWG event-log workspace.';
        return;
      }
      await selectLog(current);
    }catch(error){if(current===generation)$('status').textContent=error.message;}
  }
  async function selectLog(generationValue=++generation) {
    activeLog=idOK($('logSelect').value)?$('logSelect').value:'';
    relationships=[];invalidate('Reading saved OCEL relationships and activities…');
    options('relationship',[],'Loading supplied relationships…');
    if(!activeLog)return;
    const id=activeLog;
    try{
      const [groups,types] = await Promise.all([
        api('/v1/event-logs/'+id+'/object-relationships'),
        api('/v1/event-logs/'+id+'/object-types')
      ]);
      if(generationValue!==generation||activeLog!==id)return;
      relationships=(groups.groups||[]).filter(r=>r&&typeof r.source_type==='string'&&typeof r.target_type==='string');
      options('relationship',relationships.map((r,i)=>({value:String(i+1),
        label:r.source_type+' → '+r.target_type+' · '+(r.qualifier||'(empty)')+' · '+number(r.relationships)+' supplied assertions'})),
        'No caller-supplied O2O links');
      datalist('objectTypes',(types.object_types||[]).map(r=>String(r.object_type||'')).filter(Boolean));
      datalist('knownQualifiers',relationships.map(r=>String(r.qualifier||'')).filter(Boolean));
      $('coverage').textContent=number(groups.retained_relationships)+' caller-supplied assertions; not independently verified. '+
        (groups.groups_truncated?'List limited to 200 groups; exact types/qualifier can be entered manually.':'');
      if(relationships.length)selectRelationship();
      else $('status').textContent='No supplied object links exist in this OCEL log.';
    }catch(error){if(generationValue===generation)$('status').textContent=error.message;}
  }
  function selectRelationship() {
    const item=relationships[Number($('relationship').value)-1];
    if(!item)return;
    $('sourceType').value=item.source_type;
    $('targetType').value=item.target_type;
    $('qualifierMode').value=item.qualifier?'literal':'empty';
    $('qualifier').value=item.qualifier||'';
    $('qualifierLabel').hidden=$('qualifierMode').value!=='literal';
    invalidate('Select two distinct jointly attended activities, then analyze.');
    loadActivities();
  }
  async function loadActivities() {
    const n=++activityGeneration;
    const id=activeLog,types=[$('sourceType').value,$('targetType').value];
    if(!id||!types[0].trim()||!types[1].trim())return;
    try {
      const responses=await Promise.all(types.map(type=>api('/v1/event-logs/'+id+'/activities?case_type='+encodeURIComponent(type))));
      if(n!==activityGeneration||id!==activeLog||types[0]!==$('sourceType').value||types[1]!==$('targetType').value)return;
      const names=[...new Set(responses.flatMap(r=>(r.activities||[]).map(x=>String(x.activity||'')).filter(Boolean)))];
      datalist('knownActivities',names);
      if(!names.includes($('firstActivity').value))$('firstActivity').value=names[0]||'';
      if(!names.includes($('thenActivity').value))$('thenActivity').value=names.find(x=>x!==$('firstActivity').value)||'';
    }catch(_){if(n===activityGeneration)datalist('knownActivities',[]);}
  }
  function stat(name,value) {
    const el=document.createElement('div');el.className='stat';
    const val=document.createElement('strong');val.textContent=String(value);
    const label=document.createElement('span');label.textContent=String(name);
    el.append(val,label);return el;
  }
  function row(values) {
    const tr=document.createElement('tr');
    values.forEach(value=>{const td=document.createElement('td');
      td.textContent=String(value??'—');tr.appendChild(td);});
    return tr;
  }
  function render(r) {
    const stats=[
      ['Directed supplied pairs',r.directed_pairs],
      ['Distinct source objects',r.distinct_source_objects],
      ['Distinct target objects',r.distinct_target_objects],
      ['Both in observed order',r.forward_pairs],
      ['Observed reverse order',r.reversed_pairs],
      ['Same timestamp (uncertain)',r.simultaneous_timestamp_pairs],
      ['Only first shared event',r.first_only_pairs],
      ['Only next shared event',r.then_only_pairs],
      ['Neither shared event',r.neither_joint_event_pairs],
      ['Repeated joint activity',r.pairs_with_repeated_joint_activity],
      ['Original joint event witnesses',r.joint_pair_event_witnesses],
      ['Median forward elapsed',seconds((r.forward_elapsed_seconds||{}).p50)]
    ];
    $('metrics').replaceChildren(...stats.map(([name,value])=>stat(name,typeof value==='number'&&name!=='Median forward elapsed'?number(value):value)));
    $('basis').textContent='Exact O2O pair-level audit · saved revision '+r.relationship_revision+
      ' · '+number(r.selected_distinct_event_object_links)+' distinct selected event/object associations examined';
    $('caveats').textContent='Two activities only count as jointly attended when the same original event ID links both supplied objects. '+
      'Equal timestamps do not establish order. Imported relationship validity at either event is unknown; the result '+
      'is not full object-centric Petri-net conformance, proven compliance or a business handoff.';
    const examples=Array.isArray(r.samples)?r.samples:[];
    $('rows').replaceChildren(...examples.map(x=>row([
      x.source_object_id,x.target_object_id,x.status,x.first_joint_at,x.then_joint_at,seconds(x.forward_seconds)
    ])));
    $('sampleStatus').textContent=number(examples.length)+' example pairs displayed'+
      (r.samples_truncated?'; remaining pairs counted in the aggregates.':'.');
    $('provenance').textContent='Relationship SHA-256: '+String(r.relationship_digest||'unknown')+
      '\nObject index SHA-256: '+String(r.object_index_digest||'unknown')+
      '\nHistorical completeness verified: '+r.source_data_completeness_verified+
      '\nRelationship validity at event times verified: '+r.supplied_relationship_valid_at_event_time_verified+
      '\nFull OCPN binding replay: '+r.full_object_centric_petri_net_binding_replay+
      '\nExecution authorized: '+r.execution_authorized;
    $('results').hidden=false;
  }
  async function submit(event) {
    event.preventDefault();
    const id=activeLog,limit=Number($('samples').value);
    if(!idOK(id)){$('status').textContent='Select an imported OCEL log.';return;}
    if(!Number.isInteger(limit)||limit<0||limit>50){$('status').textContent='Examples must be 0–50.';return;}
    if(!$('firstActivity').value||!$('thenActivity').value||$('firstActivity').value===$('thenActivity').value){
      $('status').textContent='Select two different joint event activities.';return;
    }
    const settings={source_type:$('sourceType').value,target_type:$('targetType').value,
      first_activity:$('firstActivity').value,then_activity:$('thenActivity').value,sample_limit:limit};
    if(['empty','literal'].includes($('qualifierMode').value))
      settings.qualifier=$('qualifierMode').value==='empty'?'':$('qualifier').value;
    const n=++queryGeneration;
    $('analyze').disabled=true;$('results').hidden=true;
    $('status').textContent='Reading exact pair bindings locally…';
    try{
      const result=await api('/v1/event-logs/'+id+'/object-bindings/continuity',settings);
      if(n!==queryGeneration||id!==activeLog)return;
      render(result);
      $('status').textContent='Completed descriptive analysis. No source-system outcome or business rule was verified.';
    }catch(error){if(n===queryGeneration)$('status').textContent=error.message;}
    finally{if(n===queryGeneration)$('analyze').disabled=false;}
  }
  function init(){
    $('reload').addEventListener('click',reload);
    $('logSelect').addEventListener('change',()=>selectLog());
    $('relationship').addEventListener('change',selectRelationship);
    $('qualifierMode').addEventListener('change',()=>{$('qualifierLabel').hidden=$('qualifierMode').value!=='literal';invalidate('Selection changed; rerun analysis.');});
    for(const id of ['sourceType','targetType','firstActivity','thenActivity','qualifier','samples']){
      $(id).addEventListener('input',()=>invalidate('Selection changed; rerun analysis.'));
    }
    $('sourceType').addEventListener('change',loadActivities);
    $('targetType').addEventListener('change',loadActivities);
    $('query').addEventListener('submit',submit);
    $('clear').addEventListener('click',()=>invalidate('Results cleared; saved logs remain unchanged.'));
    if(!session()){$('status').textContent='Open from the same tab as your authenticated OWG dashboard.';return;}
    reload();
  }
  if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',init);
  if(typeof window!=='undefined')window.__owgObjectBindingContinuity={idOK,seconds};
})();
