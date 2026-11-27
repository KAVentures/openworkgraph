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
      const [r,feeds,grants,sql]=await Promise.all([api(base+'?freshness_hours='+h),api(base+'/rest'),api('/v1/connector-oauth').catch(()=>({connections:[]})),api(base+'/sql/catalog').catch(()=>({sources:[],unavailable:true}))]);
      if(turn!==generation)return;
      latest=r;render(r);renderProfiles(feeds,r);renderGoogleGrants(grants);renderSfGrants(grants);renderSnGrants(grants);renderSqlCatalog(sql);$('download').disabled=false;
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
  const sfObjects=['AccountHistory','ContactHistory','LeadHistory','OpportunityFieldHistory','CaseHistory'];
  function renderSfGrants(grants){
    const select=$('sfConnection'),previous=select.value;
    select.replaceChildren();
    for(const grant of (grants.connections||[]).filter(g=>g.provider==='salesforce'&&!g.reauthorization_required&&g.has_refresh_token&&(g.scopes||[]).includes('api')&&(g.scopes||[]).some(x=>['id','openid','profile'].includes(x)))){
      const option=el('option',grant.connection_id+' · '+(grant.api_host||grant.host||'instance'));
      option.value=grant.connection_id;select.append(option);
    }
    if([...select.options].some(o=>o.value===previous))select.value=previous;
    $('sfCreateButton').disabled=busy||!select.options.length;
  }
  for(const name of sfObjects){
    const fieldset=el('fieldset');fieldset.append(el('legend',name));
    const selected=check('Include '+name);selected.box.id='sfSelect'+name;
    const fields=el('input');fields.id='sfFields'+name;fields.placeholder='Status,Owner';fields.maxLength=2500;
    const ids=el('input');ids.id='sfIds'+name;ids.placeholder='Optional comma-separated record IDs';ids.maxLength=2000;
    const fl=el('label','Tracked field API names (comma separated)');fl.append(fields);
    const il=el('label','Exact record IDs (comma separated)');il.append(ids);
    fields.disabled=ids.disabled=true;
    selected.box.onchange=()=>{fields.disabled=ids.disabled=!selected.box.checked;fields.required=selected.box.checked;};
    fieldset.append(selected.node,fl,il);$('sfSelections').append(fieldset);
  }
  const snTables=['incident','problem','change_request','change_task','sc_request','sc_req_item','sc_task','sysapproval_approver'];
  function renderSnGrants(grants){
    const select=$('snConnection'),previous=select.value;
    select.replaceChildren();
    for(const grant of (grants.connections||[]).filter(g=>g.provider==='servicenow'&&!g.reauthorization_required&&g.has_refresh_token&&(g.scopes||[]).length===1&&g.scopes.includes('useraccount'))){
      const option=el('option',grant.connection_id+' · '+(grant.host||'instance'));
      option.value=grant.connection_id;select.append(option);
    }
    if([...select.options].some(o=>o.value===previous))select.value=previous;
    $('snCreateButton').disabled=busy||!select.options.length;
  }
  for(const name of snTables){
    const fieldset=el('fieldset');fieldset.append(el('legend',name));
    const selected=check('Include '+name);selected.box.id='snSelect'+name;
    const created=check('Include record creation');created.box.id='snCreated'+name;
    const fields=el('input');fields.id='snFields'+name;fields.placeholder='state,assignment_group';fields.maxLength=2500;
    const ids=el('input');ids.id='snIds'+name;ids.placeholder='Optional comma-separated sys_ids';ids.maxLength=3400;
    const fl=el('label','Tracked audit field names (comma separated)');fl.append(fields);
    const il=el('label','Exact record IDs (comma separated)');il.append(ids);
    fields.disabled=ids.disabled=created.box.disabled=true;
    selected.box.onchange=()=>{fields.disabled=ids.disabled=created.box.disabled=!selected.box.checked;};
    fieldset.append(selected.node,fl,il,created.node);$('snSelections').append(fieldset);
  }
  let sqlSources=[];
  function renderSqlCatalog(catalog){
    sqlSources=catalog.sources||[];
    const select=$('sqlSource'),previous=select.value;
    select.replaceChildren();
    for(const source of sqlSources){
      const option=el('option',source.source_alias+' · '+source.engine);
      option.value=source.source_alias;option.disabled=!source.driver_available;select.append(option);
    }
    const available=sqlSources.filter(s=>s.driver_available);
    select.disabled=!available.length||busy;
    if(available.some(s=>s.source_alias===previous))select.value=previous;
    else if(available.length)select.value=available[0].source_alias;
    $('sqlConfirm').checked=false;
    renderSqlReview(catalog.unavailable);
  }
  function renderSqlReview(unavailable=false){
    const source=sqlSources.find(s=>s.source_alias===$('sqlSource').value&&s.driver_available);
    const review=$('sqlSourceReview');
    review.replaceChildren();
    $('sqlCreateButton').disabled=busy||!source;
    if(!source){review.append(el('p',unavailable?'SQL catalog unavailable.':'No configured SQL sources with an available driver.'));return;}
    review.append(table(['Reviewed source field','Approved mapping'],[
      ['Engine',source.engine],['Relation',[source.schema_name,source.relation].filter(Boolean).join('.')],
      ['Timestamp storage',source.timestamp_storage],...Object.entries(source.mapping).map(([k,v])=>[k,v||'Not retained']),
      ['Definition SHA-256',source.source_sha256]]));
  }
  $('sqlSource').onchange=()=>{$('sqlConfirm').checked=false;renderSqlReview();};
  async function action(fn){
    if(busy)return;busy=true;$('createButton').disabled=true;$('googleCreateButton').disabled=true;$('sqlCreateButton').disabled=true;$('sfCreateButton').disabled=true;$('snCreateButton').disabled=true;$('refresh').disabled=true;
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
        table(['Configuration','Value'],[...(p.provider==='google'?[['Provider','Google Workspace audit'],['Application',p.config.application],['Customer',p.config.customer_id],['User scope',p.config.user_key],['Case notion',p.config.case_basis],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='servicenow'?[['Provider','ServiceNow operations'],['Instance',p.config.instance_host],['Reviewed table/field/record scope',JSON.stringify(p.config.selections)],['Require repeat read',p.config.verify_repeat],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='salesforce'?[['Provider','Salesforce field history'],['Instance',p.config.instance_host],['Organization',p.config.organization_id],['User',p.config.user_id],['Reviewed object/field/record scope',JSON.stringify(p.config.selections)],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='slack'?[['Provider','Slack native'],['Mode',p.config.mode],['Enterprise',p.config.enterprise_id],['Workspace',p.config.team_id],['Channel',p.config.channel_id],['Case notion',p.config.case_basis],['Audit action filters',p.config.actions.join(', ')||'All'],['Audit actor filter',p.config.actor_id],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='sql'?[['Provider','Native SQL'],['Source alias',p.config.source_alias],['Engine',p.config.engine],['Reviewed source definition',p.config.source_sha256],['Retained schema pin',p.source_schema_sha256||'No successful snapshot yet']]:[['Fixed endpoint',p.config.endpoint]]),['Credential binding available',p.credential_binding_available],['Change window (hours)',p.config.window_hours],['Overlap (minutes)',p.config.overlap_minutes],['Last attempt',p.last_attempt_at],['Last successful retention',p.last_success_at],['Checkpoint',p.checkpoint],['Retained log',p.log_id],['Last result',p.last_result?JSON.stringify(p.last_result):null]]));
      const read=check(p.provider==='google'?'Authorize one bounded Google audit read for the exact customer, application and user scope.':p.provider==='servicenow'?'Authorize one bounded read of the exact approved ServiceNow scope.':p.provider==='salesforce'?'Authorize one bounded read of the exact approved Salesforce history scope.':p.provider==='slack'?'Authorize one bounded read from the exact selected Slack scope.':p.provider==='sql'?'Authorize one bounded read-only SQL snapshot from the exact approved source.':'Authorize one bounded read from the exact configured endpoint.');
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
  $('sqlCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const source=sqlSources.find(s=>s.source_alias===$('sqlSource').value&&s.driver_available);
      if(!source)throw Error('Review an available SQL source first.');
      await api(base+'/sql',{name:$('sqlName').value,source_alias:source.source_alias,
        expected_source_sha256:source.source_sha256,initial_since:$('sqlSince').value,
        window_hours:Number($('sqlWindow').value),overlap_minutes:Number($('sqlOverlap').value),
        page_size:Number($('sqlPage').value),confirm_configuration:$('sqlConfirm').checked,
        confirm_activity_labels:$('sqlRetention').checked});
      $('sqlConfirm').checked=$('sqlRetention').checked=false;
      return 'SQL profile saved. No source was read.';
    });
  };
  function slackMode(){
    const audit=$('slackMode').value==='enterprise_audit';
    const choices=audit?[['actor','Audit actor'],['entity','Audit entity'],['location','Audit location']]:[['channel','Channel'],['thread','Reported thread']];
    $('slackCase').replaceChildren(...choices.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));
    for(const id of ['slackTeam','slackChannel','slackTransient'])$(id).disabled=audit;
    for(const id of ['slackActions','slackActor','slackOrg'])$(id).disabled=!audit;
    $('slackEnterprise').required=audit;
    $('slackTeam').required=$('slackChannel').required=!audit;
  }
  $('slackMode').onchange=slackMode;
  slackMode();
  $('slackCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const audit=$('slackMode').value==='enterprise_audit';
      await api(base+'/slack',{name:$('slackName').value,mode:$('slackMode').value,
        credential_env:$('slackEnv').value,enterprise_id:$('slackEnterprise').value.trim()||null,
        team_id:audit?null:$('slackTeam').value.trim(),channel_id:audit?null:$('slackChannel').value.trim(),
        case_basis:$('slackCase').value,actions:audit?$('slackActions').value.split(',').map(s=>s.trim()).filter(Boolean):[],
        actor_id:audit?$('slackActor').value.trim()||null:null,initial_since:$('slackSince').value,
        window_hours:Number($('slackWindow').value),overlap_minutes:Number($('slackOverlap').value),
        ingestion_lag_minutes:Number($('slackLag').value),page_size:Number($('slackPage').value),
        confirm_configuration:$('slackConfirm').checked,confirm_retention:$('slackRetention').checked,
        confirm_org_audit:audit&&$('slackOrg').checked,confirm_transient_message_content:!audit&&$('slackTransient').checked});
      for(const id of ['slackConfirm','slackRetention','slackOrg','slackTransient'])$(id).checked=false;
      return 'Slack profile saved. No source was read.';
    });
  };
  $('sfCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const values=id=>$(id).value.split(',').map(x=>x.trim()).filter(Boolean);
      const selections=sfObjects.filter(n=>$('sfSelect'+n).checked).map(n=>({object:n,fields:values('sfFields'+n),record_ids:values('sfIds'+n)}));
      if(!selections.length)throw Error('Choose at least one history object.');
      await api(base+'/salesforce',{name:$('sfName').value,connection_id:$('sfConnection').value,instance_host:$('sfHost').value.trim(),organization_id:$('sfOrg').value.trim(),user_id:$('sfUser').value.trim(),selections,
        initial_since:$('sfSince').value,window_hours:Number($('sfWindow').value),overlap_minutes:Number($('sfOverlap').value),ingestion_lag_minutes:Number($('sfLag').value),page_size:Number($('sfPage').value),
        confirm_configuration:$('sfConfirm').checked,confirm_field_labels:$('sfLabels').checked,confirm_identity_metadata:$('sfIdentity').checked,confirm_unrestricted_records:$('sfAll').checked});
      for(const id of ['sfConfirm','sfLabels','sfIdentity','sfAll'])$(id).checked=false;
      return 'Salesforce profile saved. No source was read.';
    });
  };
  $('snCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const values=id=>$(id).value.split(',').map(x=>x.trim()).filter(Boolean);
      const selections=snTables.filter(n=>$('snSelect'+n).checked).map(n=>({table:n,fields:values('snFields'+n),record_ids:values('snIds'+n),include_created:$('snCreated'+n).checked}));
      if(!selections.length||selections.some(x=>!x.fields.length&&!x.include_created))throw Error('Choose audit fields or creation events for each selected table.');
      await api(base+'/servicenow',{name:$('snName').value,connection_id:$('snConnection').value,instance_host:$('snHost').value.trim(),selections,
        initial_since:$('snSince').value,window_hours:Number($('snWindow').value),overlap_minutes:Number($('snOverlap').value),ingestion_lag_minutes:Number($('snLag').value),page_size:Number($('snPage').value),
        verify_repeat:$('snRepeat').checked,confirm_configuration:$('snConfirm').checked,confirm_utc_account:$('snUTC').checked,confirm_audit_retention:$('snAudit').checked,
        confirm_creation_retention:$('snCreation').checked,confirm_unrestricted_records:$('snAll').checked});
      for(const id of ['snConfirm','snUTC','snAudit','snCreation','snAll'])$(id).checked=false;
      return 'ServiceNow profile saved. No source was read.';
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
