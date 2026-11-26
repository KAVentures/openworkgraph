/* Explicit local comparison; imported strings are rendered as text only. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), valid=id=>/^log_[0-9a-f]{16}$/.test(id);
  let pins=null,generation=0,offset=0,selection=null;
  const token=()=>{try{return sessionStorage.getItem('owg_dashboard_session_v1')||'';}catch{return '';}};
  async function api(path,body){
    const session=token();if(!session)throw Error('Open from your authenticated local OWG dashboard.');
    const response=await fetch(path,{cache:'no-store',credentials:'same-origin',
      method:body?'POST':'GET',headers:{Authorization:'OWG-Session '+session,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw Error([401,403].includes(response.status)?'Local session expired. Reopen OWG.':String(data.detail||'Request failed').slice(0,400));
    return data;
  }
  function clear(message='',resetPins=false){
    generation++;selection=null;offset=0;$('results').hidden=true;$('rows').replaceChildren();
    for(const id of ['summary','counts','groups','page'])$(id).textContent='';
    $('previous').disabled=$('next').disabled=true;$('status').textContent=message;
    if(resetPins){pins=null;$('pins').textContent='';$('confirm').checked=false;}
    $('run').disabled=!pins||!$('confirm').checked;
  }
  function syncMode(){
    const pair=$('mode').value==='pairs';
    for(const id of ['qualifier','exact','statusFilter'])$(id).disabled=pair;
    $('pairFilterLabel').hidden=!pair;
  }
  function selected(){return [$('left').value,$('right').value];}
  function current(n,ids){return n===generation&&ids.every((id,i)=>id===selected()[i]);}
  function fill(id,logs){
    const select=$(id);select.replaceChildren();const blank=document.createElement('option');blank.value='';blank.textContent='Choose retained OCEL log';select.append(blank);
    for(const log of logs){const option=document.createElement('option');option.value=log.log_id;option.textContent=log.name+' · '+log.events+' events · '+log.log_id;select.append(option);}
  }
  async function reload(){
    clear('Loading logs…',true);const n=generation;
    try{const data=await api('/v1/event-logs');if(n!==generation)return;
      const logs=(data.logs||[]).filter(l=>valid(l.log_id)&&['ocel','ocelxml'].includes(l.source_format));
      fill('left',logs);fill('right',logs);$('status').textContent=logs.length<2?'Import two OCEL logs first.':'Select two distinct retained logs.';
    }catch(e){if(n===generation)$('status').textContent=e.message;}
  }
  const pinOf=x=>({revision:x.revision,relationships_sha256:x.relationships_sha256,object_index_sha256:x.object_index_sha256});
  async function inspect(){
    clear('Reading exact retained fingerprints…',true);const n=generation,ids=selected();
    if(!ids.every(valid)||ids[0]===ids[1]){$('status').textContent='Select two distinct OCEL logs.';return;}
    try{const pair=await Promise.all(ids.map(id=>api('/v1/event-logs/'+id+'/object-relationships/snapshot')));
      if(!current(n,ids))return;
      if(pair.some((p,i)=>p.log_id!==ids[i]))throw Error('Unexpected snapshot response.');
      pins=pair;$('pins').textContent=pair.map((p,i)=>(i?'Right':'Left')+': '+p.name+'\nRevision '+p.revision+' · '+p.retained_relationships+' assertions · '+p.retained_objects+' objects\nRelationships SHA-256: '+p.relationships_sha256+'\nObjects SHA-256: '+p.object_index_sha256).join('\n\n');
      $('status').textContent='Review the two fingerprints and confirm the common namespace.';
    }catch(e){if(current(n,ids))$('status').textContent=e.message;}
  }
  async function run(pageOffset=0){
    if(!pins||!$('confirm').checked)return;
    const ids=selected(),n=++generation;
    const spec={other_log_id:ids[1],same_namespace_declared:true,left:pinOf(pins[0]),right:pinOf(pins[1]),offset:pageOffset,limit:50};
    spec.mode=$('mode').value;
    for(const [id,key] of [['source','source_type'],['target','target_type'],['sourceId','source_id'],['targetId','target_id']])if($(id).value)spec[key]=$(id).value;
    if(spec.mode==='pairs'){if($('pairFilter').value)spec.pair_status=$('pairFilter').value;}
    else{if($('statusFilter').value)spec.status=$('statusFilter').value;if($('exact').checked)spec.qualifier=$('qualifier').value;}
    $('run').disabled=true;$('previous').disabled=$('next').disabled=true;$('results').hidden=true;$('status').textContent='Comparing pinned retained assertion sets…';
    try{
      const r=await api('/v1/event-logs/'+ids[0]+'/object-relationships/compare',spec);
      if(!current(n,ids))return;
      if(r.left.log_id!==ids[0]||r.right.log_id!==ids[1]||
        [r.left,r.right].some((p,i)=>JSON.stringify(pinOf(p))!==JSON.stringify(pinOf(pins[i]))))throw Error('Snapshot response does not match reviewed pins.');
      if(pageOffset&&selection!==r.selection_sha256)throw Error('Comparison selection changed; restart at page one.');
      selection=r.selection_sha256;offset=r.offset;
      const pair=spec.mode==='pairs';
      $('groupDetails').hidden=pair;
      $('evidenceTitle').textContent=pair?'Exact directed pair evidence':'Exact assertion evidence';
      $('evidenceHeaders').replaceChildren(...(pair?['Source ID','Target ID','Left types','Right types','Left qualifiers','Right qualifiers','Common qualifiers','Left only','Right only','Status','Inspect']:['Source ID','Target ID','Qualifier','Left types','Right types','Status']).map(label=>{const th=document.createElement('th');th.scope='col';th.textContent=label;return th;}));
      if(pair){
        const pct=x=>x===null?'unavailable':(100*x).toFixed(1)+'%';
        $('summary').textContent=r.filtered_pair_union_count+' directed pairs; '+r.comparable_pairs+' comparable endpoints and '+r.uncertain_pairs+' uncertain. Comparable-pair overlap '+pct(r.comparable_pair_overlap)+'; equal qualifier sets among shared comparable pairs '+pct(r.equal_qualifier_set_share_of_common_comparable_pairs)+'. These are retained-set comparisons, not verified changes.';
        $('counts').textContent='Pair statuses\n'+JSON.stringify(r.pair_status_counts,null,2)+'\n\nLiteral assertion counts\n'+JSON.stringify(r.literal_assertion_counts,null,2)+'\n\nWhole object index\n'+JSON.stringify(r.whole_object_index_counts,null,2);
        $('groups').textContent='';
        $('rows').replaceChildren(...r.rows.map(row=>{
          const tr=document.createElement('tr');
          for(const v of [row.source_id,row.target_id,(row.left_source_type??'missing')+' → '+(row.left_target_type??'missing'),(row.right_source_type??'missing')+' → '+(row.right_target_type??'missing'),row.left_qualifiers,row.right_qualifiers,row.common_qualifiers,row.left_only_qualifiers,row.right_only_qualifiers,row.status]){
            const td=document.createElement('td');td.textContent=String(v);tr.append(td);
          }
          const td=document.createElement('td'),b=document.createElement('button');b.textContent='Inspect assertions';
          b.setAttribute('aria-label','Inspect assertions for '+row.source_id+' to '+row.target_id);
          b.onclick=()=>{if(!current(n,ids))return;$('mode').value='assertions';$('sourceId').value=row.source_id;$('targetId').value=row.target_id;$('exact').checked=false;$('statusFilter').value='';syncMode();clear('Exact pair selected.');run();};
          td.append(b);tr.append(td);return tr;
        }));
      }else{
      $('summary').textContent=r.filtered_union_count+' retained assertions across the selected type/qualifier scope. Exact evidence page contains '+r.page_count+' matching assertions in total.';
      $('counts').textContent='Assertion statuses\n'+JSON.stringify(r.assertion_status_counts,null,2)+'\n\nWhole object index\n'+JSON.stringify(r.whole_object_index_counts,null,2);
      $('groups').textContent=(r.groups_truncated?'More than 100 groups; showing first 100.\n':'')+JSON.stringify(r.group_rows,null,2);
      $('page').textContent=(r.rows.length?String(offset+1)+'–'+String(offset+r.rows.length):'0')+' of '+r.page_count;
      $('rows').replaceChildren(...r.rows.map(row=>{
        const tr=document.createElement('tr');
        for(const value of [row.source_id,row.target_id,row.qualifier===''?'(empty)':row.qualifier,
          (row.left_source_type??'missing')+' → '+(row.left_target_type??'missing'),
          (row.right_source_type??'missing')+' → '+(row.right_target_type??'missing'),row.status]){
          const td=document.createElement('td');td.textContent=value;tr.append(td);}
        return tr;
      }));
      }
      $('page').textContent=(r.rows.length?String(offset+1)+'–'+String(offset+r.rows.length):'0')+' of '+r.page_count;
      $('results').hidden=false;$('previous').disabled=offset===0;$('next').disabled=r.next_offset===null;
      $('status').textContent='Completed descriptive comparison; no source-system relationship change was verified.';
    }catch(e){if(current(n,ids)){clear(e.message,true);}}
    finally{if(current(n,ids))$('run').disabled=!pins||!$('confirm').checked;}
  }
  document.addEventListener('DOMContentLoaded',()=>{
    $('reload').onclick=reload;$('pin').onclick=inspect;$('run').onclick=()=>run();
    $('previous').onclick=()=>run(Math.max(0,offset-50));$('next').onclick=()=>run(offset+50);
    $('clear').onclick=()=>clear('Results cleared.',true);
    for(const id of ['left','right'])$(id).onchange=()=>clear('Selection changed; inspect both snapshots again.',true);
    $('confirm').onchange=()=>clear('Confirmation changed; compare again.');
    $('mode').onchange=()=>{syncMode();clear('Comparison unit changed; compare again.');};syncMode();
    for(const id of ['source','target','sourceId','targetId','qualifier','exact','statusFilter','pairFilter'])$(id).addEventListener('input',()=>clear('Filter changed; compare again.'));
    reload();
  });
})();
