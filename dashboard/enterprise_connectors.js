/* Human-only local connector operations. No provider requests from the page. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const session=()=>{try{return sessionStorage.getItem('owg_dashboard_session_v1')||'';}catch(_){return '';}};
  let generation=0, busy=false, latest=null;
  async function api(path,body=null){
    const token=session();if(!token)throw Error('Local dashboard session missing. Reopen OWG from its launcher.');
    const headers={Authorization:'OWG-Session '+token};if(body!==null)headers['Content-Type']='application/json';
    const response=await fetch(path,{method:body===null?'GET':'POST',headers,body:body===null?undefined:JSON.stringify(body),credentials:'same-origin',cache:'no-store'});
    let value={};try{value=await response.json();}catch(_){}
    if(!response.ok)throw Error([401,403].includes(response.status)?'Request refused. Check dashboard session and explicit approvals.':String(typeof value.detail==='string'?value.detail:'Local connector request failed').slice(0,200));
    if(token!==session())throw Error('Local identity changed; response discarded.');
    return value;
  }
  const base='/v1/enterprise-connectors';
  const fmt=v=>v==null?'Unknown':typeof v==='boolean'?(v?'Yes':'No'):String(v);
  const flags=values=>values.map(v=>v.replaceAll('_',' ')).join('; ')||'No flagged local evidence';
  function table(headers,rows){
    const wrap=el('div');wrap.className='tablewrap';
    const t=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}
    head.append(hr);t.append(head);
    for(const values of rows){const row=el('tr');for(const v of values){const td=el('td');td.textContent=fmt(v);row.append(td);}body.append(row);}
    t.append(body);wrap.append(t);return wrap;
  }
  function button(label,fn,disabled=false){const b=el('button',label);b.type='button';b.disabled=disabled;b.onclick=fn;return b;}
  function check(label){const box=el('input');box.type='checkbox';const node=el('label');node.append(box,document.createTextNode(' '+label));return {node,box};}
  function download(value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));try{const a=el('a');a.href=url;a.download='owg-connector-operations.json';a.click();}finally{URL.revokeObjectURL(url);}}
  function threshold(){const n=Number($('freshness').value);if(!Number.isInteger(n)||n<1||n>720)throw Error('Choose a freshness threshold between 1 and 720 hours.');return n;}
  function render(r){
    $('inventory').replaceChildren(
      el('p',r.inventory_complete?'Local inventory sources available.':'Incomplete local inventory: '+r.inventory_sources_unavailable.join(', ')),
      el('p','Local scheduler running: '+fmt(r.scheduler_running)+'. Live provider health is unknown.'),
      table(['Provider','Implemented adapter scope','Stored OAuth sign-ins','Reconnect required','Retained profiles','Local configuration observed'],r.providers.map(p=>[p.label,p.implemented_adapter_scope,p.stored_oauth_connections,p.oauth_reconnect_required,p.retained_profiles_observed,p.local_configuration_observed])),
      el('h3','Operational worklist'),
      table(['Provider','Exact target','State','Retained events','Last success','Checkpoint','Scheduled jobs','Review flags'],r.profiles.map(p=>[p.provider,p.target_id,p.local_state,p.retained_event_count,p.last_retained_success_at,p.checkpoint_at,p.schedules.map(s=>s.schedule_id+' '+(s.enabled?'enabled':'paused')).join('; '),flags(p.review_flags)])),
      el('h3','Unmatched local schedules'),
      table(['Adapter','Target','Schedule IDs'],r.unmatched_schedules.map(s=>[s.kind,s.target_id,s.schedules.map(j=>j.schedule_id).join('; ')])),
      el('p',r.interpretation));
  }
  async function load(){
    const turn=++generation;latest=null;$('download').disabled=true;$('refresh').disabled=true;
    $('inventory').replaceChildren();$('profiles').replaceChildren();$('status').textContent='Reading local operational evidence…';
    try{
      const h=threshold();
      const [r,feeds,grants]=await Promise.all([api(base+'?freshness_hours='+h),api(base+'/rest'),api('/v1/connector-oauth').catch(()=>({connections:[]}))]);
      if(turn!==generation)return;
      latest=r;render(r);renderProfiles(feeds,r);renderGoogleGrants(grants);$('download').disabled=false;
      $('status').textContent='Local inventory refreshed. No provider reads performed.';
    }catch(error){if(turn===generation)$('status').textContent=error.message;}
    finally{if(turn===generation)$('refresh').disabled=busy;}
  }
  function renderGoogleGrants(grants){
    const select=$('googleConnection'),previous=select.value;
    select.replaceChildren();
    for(const grant of (grants.connections||[]).filter(g=>g.provider==='google'&&!g.reauthorization_required&&g.has_refresh_token)){
      const option=el('option',grant.connection_id);option.value=grant.connection_id;select.append(option);
    }
    if([...select.options].some(o=>o.value===previous))select.value=previous;
    $('googleCreateButton').disabled=!select.options.length||busy;
  }
  async function action(fn){
    if(busy)return;busy=true;$('createButton').disabled=true;$('googleCreateButton').disabled=true;$('refresh').disabled=true;
    const root=$('profiles');root.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{const r=await fn();$('restStatus').textContent=r;}
    catch(error){$('restStatus').textContent=error.message;}
    finally{busy=false;$('createButton').disabled=false;await load();}
  }
  function renderProfiles(feeds,r){
    const cards=[];
    for(const p of feeds.profiles){
      const card=el('article');card.className='profile';card.append(el('h3',p.config.name),
        el('p',p.profile_id+' · revision '+p.revision+' · '+p.status),
        table(['Configuration','Value'],[...(p.provider==='google'?[['Provider','Google Workspace audit'],['Application',p.config.application],['Customer',p.config.customer_id],['User scope',p.config.user_key],['Case notion',p.config.case_basis],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:[['Fixed endpoint',p.config.endpoint]]),['Credential binding available',p.credential_binding_available],['Change window (hours)',p.config.window_hours],['Overlap (minutes)',p.config.overlap_minutes],['Last attempt',p.last_attempt_at],['Last successful retention',p.last_success_at],['Checkpoint',p.checkpoint],['Retained log',p.log_id],['Last result',p.last_result?JSON.stringify(p.last_result):null]]));
      const read=check(p.provider==='google'?'Authorize one bounded Google audit read for the exact customer, application and user scope.':'Authorize one bounded read from the exact configured endpoint.');
      const retain=check('Authorize retention of the selected mapped events locally.');
      const run=button('Run next window',()=>action(async()=>{
        if(!read.box.checked||!retain.box.checked)throw Error('Confirm both read and retention.');
        $('restStatus').textContent='Reading one bounded change window…';
        const result=await api(base+'/rest/'+p.profile_id+'/run',{revision:p.revision,confirm_reads:true,confirm_retention:true});
        return 'Window committed: '+result.events_added+' added, '+result.duplicates_skipped+' duplicates skipped. Checkpoint '+result.checkpoint;
      }),true);
      const sync=()=>run.disabled=busy||!p.enabled||!p.credential_binding_available||!read.box.checked||!retain.box.checked;
      read.box.onchange=retain.box.onchange=sync;
      card.append(read.node,retain.node,run);
      const controls=el('div');controls.className='row';
      controls.append(button(p.enabled?'Disable feed':'Enable feed',()=>action(async()=>{
        if(!window.confirm('Change this exact feed revision? Existing recurring authorization will not follow a new revision.'))return 'Cancelled.';
        await api(base+'/rest/'+p.profile_id+'/state',{revision:p.revision,enabled:!p.enabled,confirm_change:true});return 'Feed revision changed.';
      }),!p.enabled&&p.status==='dataset_deleted'),button('Remove configuration',()=>action(async()=>{
        if(!window.confirm('Remove this feed configuration? Retained event log stays.'))return 'Cancelled.';
        await api(base+'/rest/'+p.profile_id+'/remove',{revision:p.revision,confirm_remove:true});return 'Feed removed; retained log not deleted.';
      })));
      card.append(controls);
      const jobs=r.profiles.find(q=>q.target_id===p.profile_id)?.schedules||[];
      const interval=el('input');interval.type='number';interval.min='15';interval.max='10080';interval.step='1';interval.value='60';
      const intervalLabel=el('label','Recurring interval (minutes)');intervalLabel.append(interval);
      const recurring=check('Authorize recurring reads and local retention for this exact feed revision while recording is enabled.');
      const schedule=button('Create recurring schedule',()=>action(async()=>{
        const n=Number(interval.value);if(!recurring.box.checked||!Number.isInteger(n)||n<15||n>10080)throw Error('Confirm recurring consent and a valid interval.');
        await api('/v1/schedules',{kind:'enterprise_rest_feed',target_id:p.profile_id,interval_minutes:n,confirm_scheduled_runs:true,confirm_rest_read_retention:true,expected_rest_profile_revision:p.revision});
        return 'Recurring schedule created for this profile revision.';
      }),true);
      recurring.box.onchange=()=>schedule.disabled=busy||!p.enabled||!p.credential_binding_available||jobs.length>0||!recurring.box.checked;
      card.append(el('h4','Recurring ingestion'),intervalLabel,recurring.node,schedule);
      for(const job of jobs){
        const line=el('div');line.className='row';
        line.append(el('span',job.schedule_id+' · '+(job.enabled?'enabled':'paused')+' · pinned revision '+job.profile_revision),
          button('Remove schedule',()=>action(async()=>{if(!window.confirm('Remove this recurring schedule?'))return 'Cancelled.';await api('/v1/schedules/delete',{schedule_id:job.schedule_id});return 'Schedule removed.';})));
        card.append(line);
      }
      cards.push(card);
    }
    $('profiles').replaceChildren(...cards.length?cards:[el('p','No configured REST feeds.')]);
  }
  $('create').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      await api(base+'/rest',{name:$('name').value,endpoint:$('endpoint').value,credential_env:$('credential').value,
        initial_since:$('since').value,window_hours:Number($('window').value),overlap_minutes:Number($('overlap').value),page_size:Number($('pageSize').value),
        mapping:{event_id:$('eventId').value,case_id:$('caseId').value,activity:$('activity').value,event_time:$('eventTime').value,
          change_time:$('changeTime').value,resource:$('resource').value||null},confirm_configuration:$('confirmConfig').checked,confirm_activity_labels:$('confirmLabels').checked});
      $('confirmConfig').checked=$('confirmLabels').checked=false;return 'Feed saved. No source was read.';
    });
  };
  $('googleCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      await api(base+'/google',{name:$('googleName').value,connection_id:$('googleConnection').value,customer_id:$('googleCustomer').value,user_key:$('googleUser').value,application:$('googleApplication').value,case_basis:$('googleCase').value,initial_since:$('googleSince').value,window_hours:Number($('googleWindow').value),overlap_minutes:Number($('googleOverlap').value),ingestion_lag_minutes:Number($('googleLag').value),page_size:Number($('googlePage').value),confirm_configuration:$('googleConfirm').checked,confirm_audit_retention:$('googleRetention').checked,confirm_domain_scope:$('googleDomain').checked,confirm_drive_parameters:$('googleParameters').checked});
      for(const id of ['googleConfirm','googleRetention','googleDomain','googleParameters'])$(id).checked=false;
      return 'Google audit profile saved. No source was read.';
    });
  };
  $('refresh').onclick=load;
  $('freshness').oninput=()=>{generation++;latest=null;$('download').disabled=true;$('refresh').disabled=busy;$('inventory').replaceChildren();$('profiles').replaceChildren();$('status').textContent='Threshold changed. Refresh local inventory.';};
  $('download').onclick=async()=>{
    if(!latest)return;const turn=generation;
    try{const r=await api(base+'?freshness_hours='+threshold());if(turn===generation)download(r);}
    catch(error){if(turn===generation)$('status').textContent=error.message;}
  };
  load();
})();
