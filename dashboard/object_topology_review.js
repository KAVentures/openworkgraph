/* Pinned read-only topology workbench; never sends a mutation request. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),valid=id=>/^log_[0-9a-f]{16}$/.test(id);
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  let generation=0,pin=null,report=null,busy=false;
  const session=()=>{try{return sessionStorage.getItem('owg_dashboard_session_v1')||'';}catch{return '';}};
  async function api(path,body){
    const token=session();if(!token)throw Error('Open from your authenticated local OWG dashboard.');
    const response=await fetch(path,{cache:'no-store',credentials:'same-origin',method:body?'POST':'GET',
      headers:{Authorization:'OWG-Session '+token,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{})});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw Error([401,403].includes(response.status)?'Local session expired. Reopen OWG.':String(result.detail||'Request failed').slice(0,400));
    return result;
  }
  const exactPin=x=>({revision:x.revision,relationships_sha256:x.relationships_sha256,object_index_sha256:x.object_index_sha256});
  const scope=()=> $('exact').checked?$('qualifier').value:null;
  function controls(){
    $('run').disabled=busy||!pin||!$('confirm').checked;
    $('confirm').disabled=!pin||busy;
    $('previous').disabled=busy||!report||report.offset===0;
    $('next').disabled=busy||!report||report.next_offset===null;
    $('save').disabled=busy||!report;
    $('qualifier').disabled=!$('exact').checked;
  }
  function invalidate(message,resetPin=false,resetConsent=true){
    generation++;busy=false;report=null;$('results').hidden=true;$('rows').replaceChildren();$('counts').replaceChildren();
    $('notes').replaceChildren();$('page').textContent='';
    if(resetPin){pin=null;$('pin').textContent='';}
    if(resetConsent)$('confirm').checked=false;
    $('status').textContent=message;controls();
  }
  async function reload(){
    invalidate('Reading retained logs…',true);const n=generation;
    try{const r=await api('/v1/event-logs');if(n!==generation)return;
      const logs=(r.logs||[]).filter(l=>valid(l.log_id)&&['ocel','ocelxml'].includes(l.source_format));
      const blank=el('option','Choose a retained OCEL log');blank.value='';$('log').replaceChildren(blank);
      for(const l of logs){const option=el('option',l.name+' · '+l.log_id);option.value=l.log_id;$('log').append(option);}
      $('status').textContent=logs.length?'Select a log and inspect its exact snapshot.':'Import a retained OCEL log first.';
    }catch(error){if(n===generation)$('status').textContent=error.message;}
  }
  async function inspect(){
    invalidate('Reading retained snapshot fingerprints…',true);const n=generation,id=$('log').value;
    if(!valid(id)){$('status').textContent='Choose a retained OCEL log.';return;}
    try{const r=await api('/v1/event-logs/'+id+'/object-relationships/snapshot');
      if(n!==generation||id!==$('log').value)return;
      if(r.log_id!==id||!Number.isSafeInteger(r.revision)||!['relationships_sha256','object_index_sha256'].every(k=>/^[a-f0-9]{64}$/.test(r[k])))throw Error('Unexpected snapshot response.');
      $('pin').textContent=r.name+' · '+r.retained_objects+' objects · '+r.retained_relationships+' literal assertions\nRevision '+r.revision+'\nRelationships SHA-256: '+r.relationships_sha256+'\nObject index SHA-256: '+r.object_index_sha256;
      if(r.retained_objects>20000||r.retained_relationships>100000)throw Error('Complete review exceeds the graph budget. Select a smaller retained log.');
      pin=r;$('status').textContent='Review the exact snapshot and chosen qualifier scope, then confirm.';controls();
    }catch(error){if(n===generation){pin=null;$('status').textContent=error.message;controls();}}
  }
  const definitions={
    components:[['Component ID','component_id'],['Objects','objects'],['Projected pairs','projected_pairs'],['Assertions','literal_assertions'],['Self assertions','self_assertions'],['Articulation objects','articulation_objects'],['Bridge pairs','bridge_pairs']],
    objects:[['Object ID','object_id'],['Object type','object_type'],['Component ID','component_id'],['Component objects','component_objects'],['Remaining components','remaining_components'],['Part sizes (objects × multiplicity)','part_size_counts'],['Disconnected surviving pairs','disconnected_surviving_pairs'],['Distinct neighbors','degree']],
    bridges:[['Left ID','left_id'],['Right ID','right_id'],['Left type','left_type'],['Right type','right_type'],['Component ID','component_id'],['Component objects','component_objects'],['Part sizes','part_sizes'],['Disconnected pairs','disconnected_pairs'],['Assertions in pair','assertions_in_pair'],['Left → right','left_to_right'],['Right → left','right_to_left'],['Single assertion bridge','single_assertion_bridge']]
  };
  const display=(key,value)=>key==='part_size_counts'?value.map(x=>x.objects+' × '+x.count).join(', '):Array.isArray(value)?value.join(', '):typeof value==='boolean'?value?'Yes':'No':value;
  function render(r){
    $('counts').replaceChildren(...Object.entries(r.counts).map(([k,v])=>{const tr=el('tr'),th=el('th',k.replaceAll('_',' '));th.scope='row';tr.append(th,el('td',v));return tr;}));
    const cols=definitions[r.view];
    $('evidence').textContent=({components:'Weak components',objects:'Articulation objects',bridges:'Bridge endpoint pairs'})[r.view];
    $('headers').replaceChildren(...cols.map(([name])=>{const th=el('th',name);th.scope='col';return th;}));
    $('rows').replaceChildren(...r.rows.map(row=>{const tr=el('tr');cols.forEach(([,key],i)=>{const td=el(i?'td':'th',display(key,row[key]));if(!i)td.scope='row';tr.append(td);});return tr;}));
    $('page').textContent=r.matching_rows+' matching rows · offset '+r.offset+' · exact review '+r.review_sha256;
    $('notes').replaceChildren(...r.interpretation.map(text=>el('li',text)));
    $('results').hidden=false;
  }
  async function run(offset=0,expected=null){
    if(!pin||!$('confirm').checked||busy)return;
    const id=$('log').value,snapshot=exactPin(pin),qualifier=scope(),view=$('view').value,n=++generation;
    report=null;busy=true;$('results').hidden=true;controls();$('status').textContent='Reviewing complete selected retained graph…';
    try{const r=await api('/v1/event-logs/'+id+'/object-relationships/topology',{snapshot,confirm:true,qualifier,view,offset,limit:50,...(expected?{expected_review_sha256:expected}:{})});
      if(n!==generation||id!==$('log').value||qualifier!==scope()||view!==$('view').value)return;
      if(r.format!=='openworkgraph.ocel-topology-review.v1'||r.snapshot.log_id!==id||JSON.stringify(exactPin(r.snapshot))!==JSON.stringify(snapshot)||r.qualifier!==qualifier||r.view!==view||r.execution_authorized!==false||expected&&r.review_sha256!==expected)throw Error('Unexpected topology review basis.');
      report=r;render(r);$('status').textContent='Complete retained graph reviewed. No source or relationship was changed.';
    }catch(error){if(n===generation){report=null;$('status').textContent=error.message;}}
    finally{if(n===generation){busy=false;controls();}}
  }
  $('log').onchange=()=>invalidate('Log changed. Inspect the new snapshot.',true);
  $('exact').onchange=$('qualifier').oninput=()=>invalidate('Qualifier scope changed. Review and confirm again.');
  $('view').onchange=()=>invalidate('Evidence view changed. Run the new view.',false,false);
  $('confirm').onchange=()=>{if(!$('confirm').checked)invalidate('Confirmation cleared.');else controls();};
  $('refresh').onclick=reload;$('inspect').onclick=inspect;$('run').onclick=()=>run();
  $('previous').onclick=()=>{if(report)run(Math.max(0,report.offset-50),report.review_sha256);};
  $('next').onclick=()=>{if(report&&report.next_offset!==null)run(report.next_offset,report.review_sha256);};
  $('save').onclick=()=>{if(!report||busy)return;if(!window.confirm('Download this reviewed topology page? It includes literal local object identifiers.'))return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-ocel-topology-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  reload();
})();
