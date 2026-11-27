/* Local session-bound synchronized OCEL replay and pinned evidence browser. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const idOK=id=>/^log_[0-9a-f]{16}$/.test(id||'');
  let generation=0, reviewTurn=0, componentTurn=0, detailTurn=0, participantTurn=0;
  let model=null, report=null, component=null, event=null, componentPage=null, detailPage=null, participantPage=null;
  const session=()=>{try{return sessionStorage.getItem('owg_dashboard_session_v1')||'';}catch(_){return '';}};
  async function api(path,body=null){
    const token=session();if(!token)throw Error('Local dashboard session missing. Reopen OWG from the launcher.');
    const headers={Authorization:'OWG-Session '+token};if(body!==null)headers['Content-Type']='application/json';
    const response=await fetch(path,{method:body===null?'GET':'POST',headers,body:body===null?undefined:JSON.stringify(body),credentials:'same-origin',cache:'no-store'});
    let value={};try{value=await response.json();}catch(_){}
    if(!response.ok){if([401,403].includes(response.status))throw Error('Local session expired. Reopen OWG.');
      throw Error(String(value.detail||'Local analysis failed').slice(0,400));}
    if(token!==session())throw Error('Local identity changed; response discarded.');
    return value;
  }
  function base(){const id=$('log').value;if(!idOK(id))throw Error('Select a retained OCEL log.');return '/v1/event-logs/'+id+'/object-process-model';}
  function button(label,fn){const b=el('button',label);b.type='button';b.onclick=fn;return b;}
  function table(headers,rows){
    const wrap=el('div');wrap.className='tablewrap';
    const t=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}
    head.append(hr);t.append(head);
    for(const values of rows){const row=el('tr');for(const v of values){const td=el('td');if(v instanceof Node)td.append(v);else td.textContent=String(v??'—');row.append(td);}body.append(row);}
    t.append(body);wrap.append(t);return wrap;
  }
  function download(value,name){
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    try{const a=el('a');a.href=url;a.download=name;a.click();}finally{URL.revokeObjectURL(url);}
  }
  function clearParticipant(){
    participantTurn++;event=null;participantPage=null;$('participantDetail').hidden=true;
    $('participants').replaceChildren();$('participantStatus').textContent='';
    $('participantPrev').disabled=$('participantNext').disabled=true;
  }
  function clearDetail(){
    detailTurn++;component=null;detailPage=null;clearParticipant();
    $('componentDetail').hidden=true;$('detail').replaceChildren();$('detailStatus').textContent='';
    $('detailPrev').disabled=$('detailNext').disabled=$('downloadPage').disabled=true;
  }
  function clearReview(message=''){
    reviewTurn++;componentTurn++;report=null;componentPage=null;clearDetail();
    $('results').hidden=$('evidence').hidden=true;$('stats').replaceChildren();$('notes').replaceChildren();
    $('basis').textContent='';$('components').replaceChildren();$('componentStatus').textContent='';
    $('download').disabled=$('componentPrev').disabled=$('componentNext').disabled=true;
    $('status').textContent=message;$('run').disabled=!model||!idOK($('log').value);
  }
  function invalidateModel(message){
    generation++;model=null;$('modelSummary').replaceChildren();$('downloadModel').disabled=true;
    clearReview();$('setupStatus').textContent=message;
  }
  function renderModel(){
    const rows=model.types.map(t=>[t.object_type,t.process_model.activities.filter(a=>a.kind==='activity').length,
      model.arcs.filter(a=>a.object_type===t.object_type).length]);
    $('modelSummary').replaceChildren(el('h3','Selected lifecycle definitions'),
      table(['Object type','Visible model activities','Declared arcs'],rows),
      el('p','Model uses '+model.types.length+' selected types. Other types are outside the replay basis.'));
    $('downloadModel').disabled=$('run').disabled=false;
  }
  async function reload(){
    invalidateModel('Reading local retained logs…');const turn=generation;
    $('log').disabled=$('discover').disabled=true;$('log').replaceChildren();
    try{
      const data=await api('/v1/event-logs');if(turn!==generation)return;
      const logs=(data.logs||[]).filter(r=>idOK(r.log_id)&&['ocel','ocelxml'].includes(r.source_format));
      for(const r of logs){const o=el('option',(r.name||r.log_id)+' · '+r.events+' events');o.value=r.log_id;$('log').append(o);}
      $('log').disabled=$('discover').disabled=!logs.length;
      $('setupStatus').textContent=logs.length?'Discover or import an object process model.':'Import a retained OCEL log in the event-log workspace first.';
    }catch(error){if(turn===generation)$('setupStatus').textContent=error.message;}
  }
  $('reload').onclick=reload;
  $('log').onchange=()=>{invalidateModel('Log changed. Discover or import a model for this log.');$('discover').disabled=!idOK($('log').value);$('modelFile').value='';};
  $('discover').onclick=async()=>{
    invalidateModel('Discovering lifecycle models and arcs…');const turn=generation;
    $('discover').disabled=true;
    try{
      const found=await api(base()+'/discovery',{});if(turn!==generation)return;
      const checked=await api(base()+'/preview',found.model);if(turn!==generation)return;
      model=checked.model;renderModel();
      $('setupStatus').textContent='Discovered and validated draft. '+(found.types_not_discovered||[]).length+
        ' selected types not discovered; '+(found.types_omitted_over_limit||[]).length+' omitted over type limit.';
    }catch(error){if(turn===generation)$('setupStatus').textContent=error.message;}
    finally{if(turn===generation)$('discover').disabled=!idOK($('log').value);}
  };
  $('modelFile').onchange=async()=>{
    const file=$('modelFile').files[0];if(!file)return;
    invalidateModel('Checking model file against the selected log…');const turn=generation;
    $('discover').disabled=!idOK($('log').value);
    try{
      if(file.size>262144)throw Error('Model file exceeds 256 KiB.');
      const raw=JSON.parse(await file.text());if(turn!==generation)return;
      const checked=await api(base()+'/preview',raw);if(turn!==generation)return;
      model=checked.model;renderModel();$('setupStatus').textContent='Imported model validated. No replay or save performed.';
    }catch(error){if(turn===generation)$('setupStatus').textContent=error.message;}
    finally{if(turn===generation)$('modelFile').value='';}
  };
  $('downloadModel').onclick=()=>{if(model)download(model,'owg-object-process-model.json');};
  function showReport(r){
    const stats=[];
    for(const [key,value] of Object.entries(r.source_population))stats.push([key,value]);
    for(const [key,value] of Object.entries(r.component_status_counts))stats.push(['components '+key,value]);
    for(const [key,value] of Object.entries(r.event_status_counts))stats.push(['events '+key,value]);
    $('stats').replaceChildren(...stats.map(([name,value])=>{const box=el('div');box.className='stat';
      box.append(el('strong',value),el('span',name.replaceAll('_',' ')));return box;}));
    $('basis').textContent='Exact model '+r.model_sha256+' · reviewed population '+r.review_sha256+' · '+r.search_work+' search operations.';
    $('notes').replaceChildren(...r.interpretation.map(v=>el('li',v)));
    $('results').hidden=$('evidence').hidden=false;$('download').disabled=false;
  }
  async function pinned(spec){
    if(!model||!report)throw Error('Run the complete replay first.');
    const expected=report.review_sha256;
    const r=await api(base()+'/synchronized-replay',{model,expected_review_sha256:expected,...spec});
    if(r.review_sha256!==expected)throw Error('Unexpected reviewed source basis.');
    return r;
  }
  $('run').onclick=async()=>{
    if(!model)return;clearReview('Replaying exact recorded event memberships…');const turn=reviewTurn;
    $('run').disabled=true;
    try{
      const r=await api(base()+'/synchronized-replay',{model});if(turn!==reviewTurn)return;
      if(r.log_id!==$('log').value||r.format!=='openworkgraph.synchronized-object-event-replay.v1')throw Error('Unexpected review source.');
      report=r;showReport(r);$('status').textContent='Complete selected population classified. Unknown components are not passes.';
      await loadComponents(0);
    }catch(error){if(turn===reviewTurn)$('status').textContent=error.message;}
    finally{if(turn===reviewTurn)$('run').disabled=!model;}
  };
  $('clear').onclick=()=>clearReview('Review cleared.');
  $('download').onclick=async()=>{
    const turn=reviewTurn;try{const r=await pinned({view:'summary'});if(turn===reviewTurn)download(r,'owg-synchronized-object-replay-summary.json');}
    catch(error){if(turn===reviewTurn)clearReview(error.message);}
  };
  async function loadComponents(offset){
    if(!report)return;clearDetail();const turn=++componentTurn,review=reviewTurn;
    componentPage=null;$('components').replaceChildren();$('componentPrev').disabled=$('componentNext').disabled=true;
    $('componentStatus').textContent='Checking exact source and component evidence…';
    try{
      const r=await pinned({view:'components',status:$('componentFilter').value,offset,limit:30});
      if(turn!==componentTurn||review!==reviewTurn)return;
      componentPage=r.page;
      $('components').replaceChildren(table(['Component','Status','Reason','Objects','Events','First deviation','Evidence'],
        r.page.rows.map(c=>[c.component_id.slice(0,12),c.status,c.reason,c.objects,c.events,c.first_deviation_event_id,
          button('Inspect '+c.component_id.slice(0,8),()=>openComponent(c))])));
      $('componentStatus').textContent=r.page.total+' matching components; '+r.page.rows.length+' shown.';
      $('componentPrev').disabled=offset===0;$('componentNext').disabled=r.page.next_offset===null;
    }catch(error){if(turn===componentTurn&&review===reviewTurn)clearReview(error.message);}
  }
  $('componentFilter').onchange=()=>loadComponents(0);
  $('componentPrev').onclick=()=>{if(componentPage)loadComponents(Math.max(0,componentPage.offset-30));};
  $('componentNext').onclick=()=>{if(componentPage?.next_offset!=null)loadComponents(componentPage.next_offset);};
  function filters(){
    const values=$('view').value==='events'?['all','accepted','deviation','unknown','not_replayed']:
      ['all','terminal_possible','terminal_not_reachable','unknown','no_events'];
    $('detailFilter').replaceChildren(...values.map(v=>{const o=el('option',v.replaceAll('_',' '));o.value=v;return o;}));
  }
  function openComponent(c){
    clearDetail();component=c.component_id;$('componentDetail').hidden=false;
    $('componentTitle').textContent='Component '+component+' · '+c.status;
    $('view').value='events';filters();loadDetail(0);
  }
  function detailSpec(offset){
    return {view:$('view').value,component_id:component,status:$('detailFilter').value,offset,limit:30};
  }
  async function loadDetail(offset){
    if(!component||!report)return;clearParticipant();const turn=++detailTurn,review=reviewTurn;
    detailPage=null;$('detail').replaceChildren();$('detailPrev').disabled=$('detailNext').disabled=$('downloadPage').disabled=true;
    $('detailStatus').textContent='Loading exact evidence…';
    try{
      const r=await pinned(detailSpec(offset));if(turn!==detailTurn||review!==reviewTurn)return;
      detailPage=r.page;
      if(r.page.view==='events'){
        $('detail').replaceChildren(table(['Original event','Activity','UTC timestamp','Status','Participants','Shape issues','Blocked object examples','Inspect links'],
          r.page.rows.map(e=>[e.event_id,e.activity,e.timestamp,e.status,e.linked_modeled_object_count,
            e.issues.map(i=>i.reason+' '+(i.object_type||'')).join('; '),
            e.blocked_objects.map(o=>o.object_id+' ('+o.object_type+')').join('; ')+(e.blocked_examples_truncated?' …':''),
            button('Participants',()=>openParticipants(e))])));
      }else{
        $('detail').replaceChildren(table(['Object','Type','Terminal status','Reachable markings'],
          r.page.rows.map(o=>[o.object_id,o.object_type,o.status,o.reachable_markings])));
      }
      $('detailStatus').textContent=r.page.total+' matching evidence rows; '+r.page.rows.length+' shown.';
      $('detailPrev').disabled=offset===0;$('detailNext').disabled=r.page.next_offset===null;$('downloadPage').disabled=false;
    }catch(error){if(turn===detailTurn&&review===reviewTurn)clearReview(error.message);}
  }
  $('view').onchange=()=>{filters();loadDetail(0);};$('detailFilter').onchange=()=>loadDetail(0);
  $('detailPrev').onclick=()=>{if(detailPage)loadDetail(Math.max(0,detailPage.offset-30));};
  $('detailNext').onclick=()=>{if(detailPage?.next_offset!=null)loadDetail(detailPage.next_offset);};
  $('downloadPage').onclick=async()=>{
    if(!detailPage)return;const turn=detailTurn,review=reviewTurn;
    try{const r=await pinned(detailSpec(detailPage.offset));if(turn===detailTurn&&review===reviewTurn)download(r,'owg-synchronized-object-replay-evidence.json');}
    catch(error){if(turn===detailTurn&&review===reviewTurn)clearReview(error.message);}
  };
  function openParticipants(e){
    clearParticipant();event=e.event_id;$('participantDetail').hidden=false;
    $('participantTitle').textContent='Recorded modeled participants: '+event;loadParticipants(0);
  }
  async function loadParticipants(offset){
    if(!event||!component||!report)return;const turn=++participantTurn,review=reviewTurn;
    participantPage=null;$('participants').replaceChildren();$('participantPrev').disabled=$('participantNext').disabled=true;
    try{
      const r=await pinned({view:'participants',component_id:component,event_id:event,offset,limit:50});
      if(turn!==participantTurn||review!==reviewTurn)return;participantPage=r.page;
      $('participants').replaceChildren(table(['Exact object ID','Object type'],r.page.rows.map(o=>[o.object_id,o.object_type])));
      $('participantStatus').textContent=r.page.total+' retained distinct modeled participants.';
      $('participantPrev').disabled=offset===0;$('participantNext').disabled=r.page.next_offset===null;
    }catch(error){if(turn===participantTurn&&review===reviewTurn)clearReview(error.message);}
  }
  $('participantPrev').onclick=()=>{if(participantPage)loadParticipants(Math.max(0,participantPage.offset-50));};
  $('participantNext').onclick=()=>{if(participantPage?.next_offset!=null)loadParticipants(participantPage.next_offset);};
  reload();
})();
