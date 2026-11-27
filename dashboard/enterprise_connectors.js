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
      el('h3','Retained adapter capabilities'),
      table(['Provider','Recurring dispatch','Snapshot publication','Native crosswalk','Private file delivery','Reviewed rotation','Live acceptance verified'],(r.retained_adapter_capabilities||[]).map(p=>[p.provider,p.recurring_dispatch,p.exact_snapshot_publication,p.private_native_crosswalk,p.deployment_secret_file_delivery,p.reviewed_in_place_credential_rotation,p.live_provider_acceptance_verified])),
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
      const [r,feeds,grants,sql,sap,dv,hs,dbx,snow,ns,wd,ora,bq]=await Promise.all([api(base+'?freshness_hours='+h),api(base+'/rest'),api('/v1/connector-oauth').catch(()=>({connections:[]})),api(base+'/sql/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/sap/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/dataverse/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/hubspot/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/databricks/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/snowflake/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/netsuite/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/workday/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/oracle_fusion/catalog').catch(()=>({sources:[],unavailable:true})),api(base+'/bigquery/catalog').catch(()=>({sources:[],unavailable:true}))]);
      if(turn!==generation)return;
      latest=r;render(r);renderProfiles(feeds,r);renderGoogleGrants(grants);renderSfGrants(grants);renderZdGrants(grants);renderSnGrants(grants);renderSqlCatalog(sql);renderSapCatalog(sap);renderDvCatalog(dv);renderHsCatalog(hs);renderDbxCatalog(dbx);renderSnowCatalog(snow);renderNsCatalog(ns);renderWdCatalog(wd);renderOraCatalog(ora);renderBqCatalog(bq);$('download').disabled=false;
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
  function renderZdGrants(grants){
    const select=$('zdConnection'),previous=select.value;
    select.replaceChildren();
    for(const grant of (grants.connections||[]).filter(g=>g.provider==='zendesk'&&!g.reauthorization_required&&g.has_refresh_token&&(g.scopes||[]).length===1&&g.scopes[0]==='read')){
      const option=el('option',grant.connection_id+' · '+(grant.api_host||grant.host||'tenant'));option.value=grant.connection_id;select.append(option);
    }
    if([...select.options].some(o=>o.value===previous))select.value=previous;
    $('zdCreateButton').disabled=busy||!select.options.length;
  }
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
  const dvTables=['account','contact','lead','opportunity','incident','quote','salesorder','invoice','task','appointment'];
  const dvOps=[[1,'Create'],[2,'Update'],[3,'Delete'],[5,'Upsert'],[115,'Archive'],[116,'Retain'],[117,'Rollback retain'],[118,'Restore']];
  let dvSources=[];
  for(const [code,label] of dvOps){const c=check(label+' ('+code+')');c.box.id='dvOp'+code;c.box.checked=code<=3;$('dvOperations').append(c.node);}
  for(const name of dvTables){
    const fieldset=el('fieldset');fieldset.append(el('legend',name));
    const selected=check('Include '+name);selected.box.id='dvSelect'+name;
    const ids=el('input');ids.id='dvIds'+name;ids.placeholder='Optional comma-separated record GUIDs';ids.maxLength=3800;ids.disabled=true;
    const il=el('label','Exact record GUIDs');il.append(ids);
    selected.box.onchange=()=>{ids.disabled=!selected.box.checked;};
    fieldset.append(selected.node,il);$('dvTables').append(fieldset);
  }
  function renderDvCatalog(catalog){
    dvSources=catalog.sources||[];const select=$('dvSource'),previous=select.value;
    select.replaceChildren();
    for(const src of dvSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    const available=dvSources.filter(s=>s.credential_available);
    if(available.some(s=>s.source_alias===previous))select.value=previous;
    else if(available.length)select.value=available[0].source_alias;
    select.disabled=busy||!available.length;renderDvReview(catalog.unavailable);
  }
  function renderDvReview(unavailable=false){
    const src=dvSources.find(s=>s.source_alias===$('dvSource').value&&s.credential_available);
    $('dvReview').replaceChildren();$('dvConfirm').checked=false;$('dvCreateButton').disabled=busy||!src;
    if(!src){$('dvReview').append(el('p',unavailable?'Dataverse catalog unavailable.':'No configured Dataverse source with available credential.'));return;}
    $('dvReview').append(table(['Reviewed source','Value'],[['Environment',src.origin],['Tenant',src.tenant_id],['Application client',src.client_id],['Organization',src.organization_id],['Application user',src.application_user_id],['Definition SHA-256',src.source_sha256]]));
  }
  $('dvSource').onchange=()=>renderDvReview();
  const hsObjects=['contacts','companies','deals','tickets'];
  let dbxSources=[];
  function renderDbxCatalog(catalog){
    dbxSources=catalog.sources||[];const select=$('dbxSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved view'));
    select.firstChild.value='';
    for(const src of dbxSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(dbxSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderDbxReview(catalog.unavailable);
  }
  function renderDbxReview(unavailable=false){
    const src=dbxSources.find(s=>s.source_alias===$('dbxSource').value&&s.credential_available);
    $('dbxReview').replaceChildren();$('dbxConfirm').checked=false;$('dbxRead').checked=false;$('dbxRetention').checked=false;
    $('dbxCreateButton').disabled=busy||!src;
    if(src)$('dbxReview').append(table(['Approved field','Value'],[['View',src.relation],['Definition SHA-256',src.source_sha256],['Retained activity labels',src.activities.join(', ')],['Pseudonymous resource retained',src.resource_retained_as_pseudonym]]));
    else $('dbxReview').append(el('p',unavailable?'Databricks catalog unavailable.':'Choose an approved view with an available credential.'));
  }
  $('dbxSource').onchange=()=>renderDbxReview();
  let snowSources=[];
  function renderSnowCatalog(catalog){
    snowSources=catalog.sources||[];const select=$('snowSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved view'));
    select.firstChild.value='';
    for(const src of snowSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(snowSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderSnowReview(catalog.unavailable);
  }
  function renderSnowReview(unavailable=false){
    const src=snowSources.find(s=>s.source_alias===$('snowSource').value&&s.credential_available);
    $('snowReview').replaceChildren();$('snowConfirm').checked=false;$('snowRead').checked=false;$('snowRetention').checked=false;
    $('snowCreateButton').disabled=busy||!src;
    if(src)$('snowReview').append(table(['Approved field','Value'],[['View',src.relation],['Approved role',src.role],['Definition SHA-256',src.source_sha256],['Retained activity labels',src.activities.join(', ')],['Pseudonymous resource retained',src.resource_retained_as_pseudonym]]));
    else $('snowReview').append(el('p',unavailable?'Snowflake catalog unavailable.':'Choose an approved view with an available credential.'));
  }
  $('snowSource').onchange=()=>renderSnowReview();
  let nsSources=[];
  function renderNsCatalog(catalog){
    nsSources=catalog.sources||[];const select=$('nsSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved record'));
    select.firstChild.value='';
    for(const src of nsSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(nsSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderNsReview(catalog.unavailable);
  }
  function renderNsReview(unavailable=false){
    const src=nsSources.find(s=>s.source_alias===$('nsSource').value&&s.credential_available);
    $('nsReview').replaceChildren();$('nsConfirm').checked=false;$('nsRead').checked=false;$('nsRetention').checked=false;
    $('nsCreateButton').disabled=busy||!src;
    if(src)$('nsReview').append(table(['Approved field','Value'],[['Record',src.relation],['Account',src.account],['Definition SHA-256',src.source_sha256],['Retained activity labels',src.activities.join(', ')],['Pseudonymous resource retained',src.resource_retained_as_pseudonym]]));
    else $('nsReview').append(el('p',unavailable?'NetSuite catalog unavailable.':'Choose an approved record with an available credential.'));
  }
  $('nsSource').onchange=()=>renderNsReview();
  let wdSources=[];
  function renderWdCatalog(catalog){
    wdSources=catalog.sources||[];const select=$('wdSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved report pair'));
    select.firstChild.value='';
    for(const src of wdSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(wdSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderWdReview(catalog.unavailable);
  }
  function renderWdReview(unavailable=false){
    const src=wdSources.find(s=>s.source_alias===$('wdSource').value&&s.credential_available);
    $('wdReview').replaceChildren();$('wdConfirm').checked=false;$('wdRead').checked=false;$('wdRetention').checked=false;
    $('wdCreateButton').disabled=busy||!src;
    if(src)$('wdReview').append(table(['Approved field','Value'],[['Event report',src.relation],['Control report',src.control_report],['Tenant',src.tenant],['Definition SHA-256',src.source_sha256],['Retained activity labels',src.activities.join(', ')],['Pseudonymous resource retained',src.resource_retained_as_pseudonym]]));
    else $('wdReview').append(el('p',unavailable?'Workday catalog unavailable.':'Choose an approved report pair with an available credential.'));
  }
  $('wdSource').onchange=()=>renderWdReview();
  let oraSources=[];
  function renderOraCatalog(catalog){
    oraSources=catalog.sources||[];const select=$('oraSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved source'));select.firstChild.value='';
    for(const src of oraSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(oraSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderOraReview(catalog.unavailable);
  }
  function renderOraReview(unavailable=false){
    const src=oraSources.find(s=>s.source_alias===$('oraSource').value&&s.credential_available);
    $('oraReview').replaceChildren();for(const id of ['oraConfirm','oraRead','oraSnapshot','oraRetention'])$(id).checked=false;
    for(const [key,field] of [['Req','requisition_business_units'],['PO','purchase_order_business_units']]){
      const select=$('ora'+key+'BU'),previous=select.value;select.replaceChildren();
      for(const bu of src?.[field]||[]){const o=el('option',bu);o.value=bu;select.append(o);}
      if((src?.[field]||[]).includes(previous))select.value=previous;
      $('ora'+key).disabled=!(src?.[field]||[]).length;
      if($('ora'+key).disabled)$('ora'+key).checked=false;
    }
    $('oraCreateButton').disabled=busy||!src;
    if(src)$('oraReview').append(table(['Approved field','Value'],[['Definition SHA-256',src.source_sha256],['Requisition business units',src.requisition_business_units.join(', ')],['Purchase-order business units',src.purchase_order_business_units.join(', ')]]));
    else $('oraReview').append(el('p',unavailable?'Oracle Fusion catalog unavailable.':'Choose an approved source with an available credential.'));
  }
  $('oraSource').onchange=()=>renderOraReview();
  let bqSources=[];
  function renderBqCatalog(catalog){
    bqSources=catalog.sources||[];const select=$('bqSource'),previous=select.value;
    select.replaceChildren(el('option','Choose approved view'));
    select.firstChild.value='';
    for(const src of bqSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    if(bqSources.some(s=>s.source_alias===previous&&s.credential_available))select.value=previous;
    renderBqReview(catalog.unavailable);
  }
  function renderBqReview(unavailable=false){
    const src=bqSources.find(s=>s.source_alias===$('bqSource').value&&s.credential_available);
    $('bqReview').replaceChildren();$('bqConfirm').checked=false;$('bqRead').checked=false;$('bqRetention').checked=false;
    $('bqCreateButton').disabled=busy||!src;
    if(src)$('bqReview').append(table(['Approved field','Value'],[['View',src.relation],['Billing project',src.billing_project],['Location',src.location],['Maximum bytes billed per query',src.maximum_bytes_billed],['Definition SHA-256',src.source_sha256],['Retained activity labels',src.activities.join(', ')],['Pseudonymous resource retained',src.resource_retained_as_pseudonym]]));
    else $('bqReview').append(el('p',unavailable?'BigQuery catalog unavailable.':'Choose an approved view with an available credential.'));
  }
  $('bqSource').onchange=()=>renderBqReview();
  let hsSources=[];
  for(const name of hsObjects){
    const fieldset=el('fieldset');fieldset.append(el('legend',name));
    const selected=check('Include '+name);selected.box.id='hsSelect'+name;
    const ids=el('input');ids.id='hsIds'+name;ids.placeholder='Exact comma-separated record IDs';ids.maxLength=2200;ids.disabled=true;
    const props=el('input');props.id='hsProps'+name;props.placeholder='Selected internal property names';props.maxLength=1700;props.disabled=true;
    const il=el('label','Record IDs');il.append(ids);const pl=el('label','Property names');pl.append(props);
    selected.box.onchange=()=>{ids.disabled=props.disabled=!selected.box.checked;ids.required=props.required=selected.box.checked;};
    fieldset.append(selected.node,il,pl);$('hsObjects').append(fieldset);
  }
  function renderHsCatalog(catalog){
    hsSources=catalog.sources||[];const select=$('hsSource'),previous=select.value;
    select.replaceChildren();
    for(const src of hsSources){const o=el('option',src.source_alias);o.value=src.source_alias;o.disabled=!src.credential_available;select.append(o);}
    const available=hsSources.filter(s=>s.credential_available);
    if(available.some(s=>s.source_alias===previous))select.value=previous;
    else if(available.length)select.value=available[0].source_alias;
    select.disabled=busy||!available.length;renderHsReview(catalog.unavailable);
  }
  function renderHsReview(unavailable=false){
    const src=hsSources.find(s=>s.source_alias===$('hsSource').value&&s.credential_available);
    $('hsReview').replaceChildren();$('hsConfirm').checked=false;$('hsTicket').checked=false;$('hsCreateButton').disabled=busy||!src;
    if(!src){$('hsReview').append(el('p',unavailable?'HubSpot catalog unavailable.':'No configured HubSpot source with available credential.'));return;}
    $('hsReview').append(table(['Reviewed source','Value'],[['Account ID',src.hub_id],['Application ID',src.app_id],['Reviewed scopes',src.scopes.join(', ')],['Definition SHA-256',src.source_sha256]]));
  }
  $('hsSource').onchange=()=>renderHsReview();
  let sapSources=[];
  const sapStreams=[['sapPO','purchase_orders'],['sapMovement','material_movements'],['sapInvoice','supplier_invoices']];
  function renderSapCatalog(catalog){
    sapSources=catalog.sources||[];
    const select=$('sapSource'),previous=select.value;
    select.replaceChildren();
    for(const source of sapSources){
      const o=el('option',source.source_alias+' · SAP client '+source.sap_client);
      o.value=source.source_alias;o.disabled=!source.credential_available;select.append(o);
    }
    const available=sapSources.filter(s=>s.credential_available);
    if(available.some(s=>s.source_alias===previous))select.value=previous;
    else if(available.length)select.value=available[0].source_alias;
    select.disabled=!available.length||busy;
    $('sapConfirm').checked=false;renderSapReview(catalog.unavailable);
  }
  function renderSapReview(unavailable=false){
    const src=sapSources.find(s=>s.source_alias===$('sapSource').value&&s.credential_available);
    const review=$('sapReview');review.replaceChildren();
    $('sapCreateButton').disabled=busy||!src;
    $('sapConfirm').checked=false;
    for(const [id,stream] of sapStreams){const enabled=!!src&&src.streams.includes(stream);$(id).disabled=!enabled;if(!enabled)$(id).checked=false;}
    if(!src){review.append(el('p',unavailable?'SAP catalog unavailable.':'No configured SAP source with available credential.'));return;}
    review.append(table(['Reviewed source','Value'],[['Service root',src.service_root],['SAP client',src.sap_client],['Deployment definition SHA-256',src.source_sha256],['Metadata-pinned streams',src.streams.join(', ')]]));
  }
  $('sapSource').onchange=()=>renderSapReview();
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
    if(busy)return;busy=true;$('createButton').disabled=true;$('googleCreateButton').disabled=true;$('sqlCreateButton').disabled=true;$('sfCreateButton').disabled=true;$('snCreateButton').disabled=true;$('zdCreateButton').disabled=true;$('sapCreateButton').disabled=true;$('dvCreateButton').disabled=true;$('hsCreateButton').disabled=true;$('dbxCreateButton').disabled=true;$('snowCreateButton').disabled=true;$('nsCreateButton').disabled=true;$('wdCreateButton').disabled=true;$('oraCreateButton').disabled=true;$('bqCreateButton').disabled=true;$('refresh').disabled=true;
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
        table(['Configuration','Value'],[...(p.provider==='google'?[['Provider','Google Workspace audit'],['Application',p.config.application],['Customer',p.config.customer_id],['User scope',p.config.user_key],['Case notion',p.config.case_basis],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='oracle_fusion'?[['Provider','Oracle Fusion procurement headers'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Exact selected headers',JSON.stringify(p.config.selections)],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='bigquery'?[['Provider','BigQuery approved event view'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Reporting lag (minutes)',p.config.ingestion_lag_minutes],['Page size',p.config.page_size]]:p.provider==='workday'?[['Provider','Workday RaaS event and control reports'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='netsuite'?[['Provider','NetSuite curated SuiteQL event record'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Reporting lag (minutes)',p.config.ingestion_lag_minutes],['Page size',p.config.page_size]]:p.provider==='snowflake'?[['Provider','Snowflake approved event view'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='databricks'?[['Provider','Databricks approved event view'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='hubspot'?[['Provider','HubSpot property history'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Selected record and property scope',JSON.stringify(p.config.selections)],['Creation evidence',p.config.include_created],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='dataverse'?[['Provider','Dynamics 365 / Dataverse audit'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Selected table and record scope',JSON.stringify(p.config.selections)],['Operation codes',p.config.operations.join(', ')],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='sap'?[['Provider','SAP procurement day evidence'],['Approved source',p.config.source_alias],['Definition SHA-256',p.config.source_sha256],['Selected purchase orders',p.config.purchase_orders.join(', ')],['Streams',p.config.streams.join(', ')],['Completed-day lag',p.config.lag_days],['Time precision','Day only; midnight is a storage convention']]:p.provider==='zendesk'?[['Provider','Zendesk selected ticket audit'],['Tenant',p.config.instance_host],['Principal ID',p.config.principal_id],['Selected tickets',p.config.ticket_ids.join(', ')],['Fields',p.config.fields.join(', ')],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='servicenow'?[['Provider','ServiceNow operations'],['Instance',p.config.instance_host],['Reviewed table/field/record scope',JSON.stringify(p.config.selections)],['Require repeat read',p.config.verify_repeat],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='salesforce'?[['Provider','Salesforce field history'],['Instance',p.config.instance_host],['Organization',p.config.organization_id],['User',p.config.user_id],['Reviewed object/field/record scope',JSON.stringify(p.config.selections)],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='slack'?[['Provider','Slack native'],['Mode',p.config.mode],['Enterprise',p.config.enterprise_id],['Workspace',p.config.team_id],['Channel',p.config.channel_id],['Case notion',p.config.case_basis],['Audit action filters',p.config.actions.join(', ')||'All'],['Audit actor filter',p.config.actor_id],['Reporting lag (minutes)',p.config.ingestion_lag_minutes]]:p.provider==='sql'?[['Provider','Native SQL'],['Source alias',p.config.source_alias],['Engine',p.config.engine],['Reviewed source definition',p.config.source_sha256],['Retained schema pin',p.source_schema_sha256||'No successful snapshot yet']]:[['Fixed endpoint',p.config.endpoint]]),['Credential binding available',p.credential_binding_available],['Change window (hours)',p.config.window_hours],['Overlap (minutes)',p.config.overlap_minutes],['Last attempt',p.last_attempt_at],['Last successful retention',p.last_success_at],['Checkpoint',p.checkpoint],['Retained log',p.log_id],['Last result',p.last_result?JSON.stringify(p.last_result):null]]));
      const read=check(p.provider==='google'?'Authorize one bounded Google audit read for the exact customer, application and user scope.':p.provider==='oracle_fusion'?'Authorize two bounded reads of the exact approved Oracle procurement headers.':p.provider==='bigquery'?'Authorize bounded BigQuery read queries and result pagination with the reviewed billing cap.':p.provider==='workday'?'Authorize two bounded event-report reads and two population-control reads for the approved Workday source.':p.provider==='netsuite'?'Authorize certificate authentication and two bounded reads of the approved NetSuite record.':p.provider==='snowflake'?'Authorize a bounded read of the exact approved Snowflake view.':p.provider==='databricks'?'Authorize a bounded read of the exact approved Databricks view.':p.provider==='hubspot'?'Authorize receipt of selected HubSpot property histories, including transient values outside the window.':p.provider==='dataverse'?'Authorize one bounded read of the approved Dataverse audit scope.':p.provider==='sap'?'Authorize one bounded SAP business-date read for the approved source and selected purchase orders.':p.provider==='zendesk'?'Authorize full transient selected-ticket audit and agent identity reads, with repeated scans.':p.provider==='servicenow'?'Authorize one bounded read of the exact approved ServiceNow scope.':p.provider==='salesforce'?'Authorize one bounded read of the exact approved Salesforce history scope.':p.provider==='slack'?'Authorize one bounded read from the exact selected Slack scope.':p.provider==='sql'?'Authorize one bounded read-only SQL snapshot from the exact approved source.':'Authorize one bounded read from the exact configured endpoint.');
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
      }),!p.enabled&&(p.status==='dataset_deleted'||!!p.integrity_hold)),button('Remove configuration',()=>action(async()=>{
        if(!window.confirm('Remove this feed configuration? Retained event log stays.'))return 'Cancelled.';
        await api(base+'/rest/'+p.profile_id+'/remove',{revision:p.revision,confirm_remove:true});return 'Feed removed; retained log not deleted.';
      })));
      const cm=p.credential_management;
      if(cm){
        card.append(table(['Credential management','Value'],[['Delivery',cm.delivery],['Reviewed rotation supported',cm.reviewed_rotation_supported],['Last identity-verified rotation',cm.last_rotation?.at]]));
        if(cm.reviewed_rotation_supported&&!p.enabled&&p.status!=='dataset_deleted'){
          const rotate=button('Verify and bind replacement credential',()=>action(async()=>{
            if(!window.confirm('I stopped all local and Gateway workers, reconciled any active leases and outboxes, and installed the replacement credential through deployment management.'))return 'Cancelled.';
            if(!window.confirm('Authorize identity-only provider authentication and bind the replacement if the exact account/application identity matches? The feed stays disabled.'))return 'Cancelled.';
            await api(base+'/rest/'+p.profile_id+'/rotate-credential',{revision:p.revision,confirm_rotation:true,confirm_identity_probe:true,confirm_workers_stopped:true});
            return 'Replacement identity verified. Feed remains disabled; checkpoint preserved. Enable and reauthorize scheduling separately.';
          }));
          controls.append(rotate);
        }
      }
      card.append(controls);
      if(p.historical_reconciliation_supported){
        const h=el('details');h.append(el('summary','Historical window reconciliation'),
          el('p','Re-read one previously traversed change window with the same reviewed scope and exact event keys. This may recover late evidence, and applies current case exclusions, but does not establish complete source history. The live checkpoint does not move.'));
        const history=p.historical_receipts||[];
        if(history.length)h.append(table(['Window start','Window end','Outcome','Added','Duplicates','Excluded','Checkpoint','Error'],
          history.map(x=>[x.since,x.until,x.outcome,x.events_added,x.duplicates_skipped,x.excluded_case_events,x.checkpoint_after,x.error])));
        else h.append(el('p','No historical receipts yet.'));
        if(p.historical_active){
          const a=p.historical_active;
          h.append(el('p','Active historical intent: '+a.since+' — '+a.until+(a.lease_expired?' (expired; reconcile before another read)':' (running)')));
          if(a.lease_expired)h.append(button('Reconcile expired historical intent',()=>action(async()=>{
            if(!window.confirm('I stopped the relevant source workers, inspected the retained log and accept that the interrupted read outcome is uncertain. This will not read the provider.'))return 'Cancelled.';
            await api(base+'/rest/'+p.profile_id+'/historical-reconcile',{revision:p.revision,request_id:a.request_id,
                confirm_workers_stopped:true,confirm_uncertain_outcome:true});
            return 'Expired historical intent reconciled; checkpoint preserved.';
          })));
        }else if(p.enabled&&!p.integrity_hold&&p.checkpoint&&p.credential_binding_available){
          const since=el('input');since.type='text';since.value=p.config.initial_since;
          const until=el('input');until.type='text';until.value=p.checkpoint;
          const sl=el('label','Historical change window start (ISO timezone-qualified)');sl.append(since);
          const ul=el('label','Historical change window end (exclusive)');ul.append(until);
          const result=el('div'),read=check('Authorize one historical provider read of the exact reviewed window.'),
            retain=check('Retain recovered evidence using existing source identities; checkpoint stays fixed.'),
            workers=check('I stopped/reconciled external workers and paused or removed competing local schedules.');
          let reviewed=null,checking=false,requestId=null;
          const sync=()=>commit.disabled=busy||checking||!reviewed||!read.box.checked||!retain.box.checked||!workers.box.checked;
          const invalidate=()=>{reviewed=null;requestId=null;result.replaceChildren();read.box.checked=retain.box.checked=workers.box.checked=false;sync();};
          const preview=button('Preview historical window',async()=>{
            if(busy||checking)return;
            invalidate();checking=true;preview.disabled=true;since.disabled=until.disabled=true;sync();
            try{
              const plan=await api(base+'/rest/'+p.profile_id+'/historical-preview',{revision:p.revision,since:since.value,until:until.value});
              if(!h.isConnected)return;
              reviewed=plan;requestId=Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
              result.replaceChildren(table(['Exact historical plan','Value'],[['Window start',plan.since],['Window end',plan.until],
                ['Current checkpoint',plan.checkpoint],['Excluded cases in profile',plan.excluded_case_count],['Maximum projected rows',plan.max_rows],['Plan SHA-256',plan.plan_sha256],
                ['Provider read performed',plan.provider_reads_performed]]));
            }catch(e){if(h.isConnected)result.textContent=e.message;}
            finally{checking=false;preview.disabled=false;since.disabled=until.disabled=false;sync();}
          });
          const commit=button('Read and retain historical window',()=>action(async()=>{
            if(!reviewed||!read.box.checked||!retain.box.checked||!workers.box.checked)throw Error('Preview and explicitly approve the historical window.');
            const x=await api(base+'/rest/'+p.profile_id+'/historical-execute',{revision:p.revision,since:reviewed.since,until:reviewed.until,
                expected_plan_sha256:reviewed.plan_sha256,request_id:requestId,confirm_historical_read:true,
                confirm_retention:true,confirm_workers_reconciled:true});
            const receipt=x.receipt;
            return 'Historical '+receipt.outcome+': '+(receipt.events_added??'unknown')+' added, '+(receipt.duplicates_skipped??'unknown')+' duplicates. Checkpoint '+receipt.checkpoint_after;
          }),true);
          since.oninput=until.oninput=invalidate;
          read.box.onchange=retain.box.onchange=workers.box.onchange=sync;
          h.append(sl,ul,preview,result,read.node,retain.node,workers.node,commit);
        }else h.append(el('p','An enabled feed with a completed checkpoint and current credential binding is required.'));
        card.append(h);
      }
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
  $('zdCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const fields=['status','priority','type','assignee_id','group_id'].filter(x=>$('zdField'+x).checked);
      if(!fields.length)throw Error('Choose at least one approved audit field.');
      await api(base+'/zendesk',{name:$('zdName').value,connection_id:$('zdConnection').value,instance_host:$('zdHost').value.trim(),principal_id:$('zdPrincipal').value.trim(),
        ticket_ids:$('zdTickets').value.split(',').map(x=>x.trim()).filter(Boolean),fields,initial_since:$('zdSince').value,window_hours:Number($('zdWindow').value),
        overlap_minutes:Number($('zdOverlap').value),ingestion_lag_minutes:Number($('zdLag').value),confirm_configuration:$('zdConfirm').checked,
        confirm_transient_audit_payloads:$('zdTransient').checked,confirm_metadata_retention:$('zdRetention').checked});
      for(const id of ['zdConfirm','zdTransient','zdRetention'])$(id).checked=false;
      return 'Zendesk retained profile saved. No source was read.';
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
  $('sapCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=sapSources.find(s=>s.source_alias===$('sapSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved SAP source.');
      const streams=sapStreams.filter(([id])=>$(id).checked&&!$(id).disabled).map(([,stream])=>stream);
      if(!streams.length)throw Error('Choose at least one approved stream.');
      await api(base+'/sap',{name:$('sapName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,purchase_orders:$('sapOrders').value.split(',').map(x=>x.trim()).filter(Boolean),streams,
        initial_since:$('sapSince').value+'T00:00:00Z',window_days:Number($('sapWindow').value),overlap_days:Number($('sapOverlap').value),lag_days:Number($('sapLag').value),page_size:Number($('sapPage').value),
        confirm_configuration:$('sapConfirm').checked,confirm_day_precision:$('sapPrecision').checked,confirm_retention:$('sapRetention').checked,confirm_transient_unselected_references:$('sapTransient').checked});
      for(const id of ['sapConfirm','sapPrecision','sapRetention','sapTransient'])$(id).checked=false;
      return 'SAP profile saved. No source was read.';
    });
  };
  $('dvCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=dvSources.find(s=>s.source_alias===$('dvSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved Dataverse source.');
      const selections=dvTables.filter(n=>$('dvSelect'+n).checked).map(n=>({table:n,record_ids:$('dvIds'+n).value.split(',').map(x=>x.trim()).filter(Boolean)}));
      const operations=dvOps.filter(([n])=>$('dvOp'+n).checked).map(([n])=>n);
      if(!selections.length||!operations.length)throw Error('Choose at least one table and operation.');
      await api(base+'/dataverse',{name:$('dvName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,selections,operations,
        initial_since:$('dvSince').value,window_hours:Number($('dvWindow').value),overlap_minutes:Number($('dvOverlap').value),ingestion_lag_minutes:Number($('dvLag').value),page_size:Number($('dvPage').value),
        confirm_configuration:$('dvConfirm').checked,confirm_application_identity:$('dvIdentity').checked,confirm_audit_retention:$('dvRetention').checked,confirm_unrestricted_records:$('dvAll').checked});
      for(const id of ['dvConfirm','dvIdentity','dvRetention','dvAll'])$(id).checked=false;
      return 'Dataverse profile saved. No source was read.';
    });
  };
  $('hsCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=hsSources.find(s=>s.source_alias===$('hsSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved HubSpot source.');
      const values=id=>$(id).value.split(',').map(x=>x.trim()).filter(Boolean);
      const selections=hsObjects.filter(n=>$('hsSelect'+n).checked).map(n=>({object_type:n,record_ids:values('hsIds'+n),properties:values('hsProps'+n)}));
      if(!selections.length||selections.some(x=>!x.record_ids.length||!x.properties.length))throw Error('Choose exact records and properties for every selected object.');
      await api(base+'/hubspot',{name:$('hsName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,selections,
        initial_since:$('hsSince').value,window_hours:Number($('hsWindow').value),overlap_minutes:Number($('hsOverlap').value),ingestion_lag_minutes:Number($('hsLag').value),batch_size:Number($('hsBatch').value),include_created:$('hsCreated').checked,
        confirm_configuration:$('hsConfirm').checked,confirm_transient_history_values:$('hsTransient').checked,confirm_metadata_retention:$('hsRetention').checked,confirm_ticket_scope:$('hsTicket').checked});
      for(const id of ['hsConfirm','hsTransient','hsRetention','hsTicket'])$(id).checked=false;
      return 'HubSpot profile saved. No source was read.';
    });
  };
  $('dbxCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=dbxSources.find(s=>s.source_alias===$('dbxSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved Databricks view.');
      await api(base+'/databricks',{name:$('dbxName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,
        initial_since:$('dbxSince').value,window_hours:Number($('dbxWindow').value),overlap_minutes:Number($('dbxOverlap').value),ingestion_lag_minutes:Number($('dbxLag').value),
        confirm_configuration:$('dbxConfirm').checked,confirm_source_read:$('dbxRead').checked,confirm_metadata_retention:$('dbxRetention').checked});
      return 'Databricks profile saved. No source was read.';
    });
  };
  $('snowCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=snowSources.find(s=>s.source_alias===$('snowSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved Snowflake view.');
      await api(base+'/snowflake',{name:$('snowName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,
        initial_since:$('snowSince').value,window_hours:Number($('snowWindow').value),overlap_minutes:Number($('snowOverlap').value),ingestion_lag_minutes:Number($('snowLag').value),
        confirm_configuration:$('snowConfirm').checked,confirm_source_read:$('snowRead').checked,confirm_metadata_retention:$('snowRetention').checked});
      return 'Snowflake profile saved. No source was read.';
    });
  };
  $('nsCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=nsSources.find(s=>s.source_alias===$('nsSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved NetSuite view.');
      await api(base+'/netsuite',{name:$('nsName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,
        initial_since:$('nsSince').value,window_hours:Number($('nsWindow').value),overlap_minutes:Number($('nsOverlap').value),ingestion_lag_minutes:Number($('nsLag').value),page_size:Number($('nsPage').value),
        confirm_configuration:$('nsConfirm').checked,confirm_source_read:$('nsRead').checked,confirm_metadata_retention:$('nsRetention').checked});
      return 'NetSuite profile saved. No source was read.';
    });
  };
  $('wdCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=wdSources.find(s=>s.source_alias===$('wdSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved Workday report pair.');
      await api(base+'/workday',{name:$('wdName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,
        initial_since:$('wdSince').value,window_hours:Number($('wdWindow').value),overlap_minutes:Number($('wdOverlap').value),ingestion_lag_minutes:Number($('wdLag').value),
        confirm_configuration:$('wdConfirm').checked,confirm_source_read:$('wdRead').checked,confirm_metadata_retention:$('wdRetention').checked});
      return 'Workday profile saved. No source was read.';
    });
  };
  $('oraCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=oraSources.find(s=>s.source_alias===$('oraSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved Oracle Fusion source.');
      const selections=[];
      for(const [key,object_type] of [['Req','requisition'],['PO','purchase_order']]){
        if(!$('ora'+key).checked)continue;
        const header_ids=$('ora'+key+'IDs').value.split(',').map(x=>x.trim()).filter(Boolean);
        if(!header_ids.length||header_ids.some(x=>!/^[1-9][0-9]{0,18}$/.test(x))||new Set(header_ids).size!==header_ids.length)throw Error('Enter exact distinct positive header IDs.');
        selections.push({object_type,business_unit_id:$('ora'+key+'BU').value,header_ids,include_created:$('ora'+key+'Created').checked,include_milestone:$('ora'+key+'Milestone').checked,include_latest_update:$('ora'+key+'Update').checked});
      }
      if(!selections.length||selections.reduce((n,s)=>n+s.header_ids.length,0)>10)throw Error('Choose one to ten exact headers, at most nine per resource.');
      await api(base+'/oracle_fusion',{name:$('oraName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,selections,
        initial_since:$('oraSince').value,window_hours:Number($('oraWindow').value),overlap_minutes:Number($('oraOverlap').value),ingestion_lag_minutes:Number($('oraLag').value),
        confirm_configuration:$('oraConfirm').checked,confirm_source_read:$('oraRead').checked,confirm_snapshot_semantics:$('oraSnapshot').checked,confirm_metadata_retention:$('oraRetention').checked});
      return 'Oracle Fusion profile saved. No source was read.';
    });
  };
  $('bqCreate').onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const src=bqSources.find(s=>s.source_alias===$('bqSource').value&&s.credential_available);
      if(!src)throw Error('Choose an approved BigQuery view.');
      await api(base+'/bigquery',{name:$('bqName').value,source_alias:src.source_alias,source_sha256:src.source_sha256,
        initial_since:$('bqSince').value,window_hours:Number($('bqWindow').value),overlap_minutes:Number($('bqOverlap').value),ingestion_lag_minutes:Number($('bqLag').value),page_size:Number($('bqPage').value),
        confirm_configuration:$('bqConfirm').checked,confirm_source_read:$('bqRead').checked,confirm_metadata_retention:$('bqRetention').checked});
      return 'BigQuery profile saved. No source was read.';
    });
  };
  // Independent human-only review workspace. A change to any input invalidates
  // the displayed plan and requires a fresh server preview.
  let tuningGeneration=0,tuningBusy=false,tuningTargets=[],tuningPlan=null;
  const tuningLabels={window_hours:'Window (hours)',overlap_minutes:'Overlap (minutes)',
    ingestion_lag_minutes:'Reporting lag (minutes)',page_size:'Page size',lag_days:'Completed-day lag (days)'};
  function tuningInvalidate(){
    tuningGeneration++;tuningPlan=null;$('tuningPlan').replaceChildren();
    $('tuningPreview').disabled=tuningBusy||!tuningTargets.some(t=>t.selected.checked);
  }
  function tuningRender(data){
    tuningTargets=[];const cards=[];
    for(const p of data.profiles){
      const card=el('article');card.className='profile';
      const selected=check('Select '+p.name+' ('+p.provider+', revision '+p.revision+')');
      selected.box.disabled=p.unreconciled_run||p.status==='dataset_deleted';
      card.append(selected.node,el('p',p.profile_id+' · '+p.status+' · checkpoint '+fmt(p.checkpoint)));
      if(p.unreconciled_run)card.append(el('p','Reconcile the current local run before tuning.'));
      const fields=el('div');fields.className='grid';const inputs={};
      for(const [name,rules] of Object.entries(p.fields)){
        const label=el('label',tuningLabels[name]||name),input=el('input');
        input.type='number';input.min=String(rules.min);input.max=String(rules.max);
        input.step=String(rules.step);input.value=p.values[name]==null?'':String(p.values[name]);
        input.disabled=true;input.oninput=tuningInvalidate;label.append(input);fields.append(label);inputs[name]=input;
      }
      selected.box.onchange=()=>{for(const input of Object.values(inputs))input.disabled=!selected.box.checked;tuningInvalidate();};
      tuningTargets.push({profile:p,selected:selected.box,inputs});card.append(fields);cards.push(card);
    }
    $('tuningProfiles').replaceChildren(...cards.length?cards:[el('p','No retained feed profiles configured.')]);
    $('tuningHistory').replaceChildren(el('h3','Recent reviewed tuning receipts'),
      table(['Applied at','Receipt','Feeds changed','Reauthorization required'],data.receipts.map(r=>
        [r.applied_at,r.receipt_id,r.target_count,r.requires_separate_schedule_and_gateway_reauthorization])));
    tuningInvalidate();
  }
  function tuningSelection(){
    const out=[];
    for(const t of tuningTargets.filter(t=>t.selected.checked)){
      const row={profile_id:t.profile.profile_id,revision:t.profile.revision};
      for(const [name,input] of Object.entries(t.inputs)){
        if(input.value===''||!input.checkValidity()||!Number.isInteger(Number(input.value)))throw Error('Use valid integer values for '+t.profile.name+'.');
        if(Number(input.value)!==t.profile.values[name])row[name]=Number(input.value);
      }
      if(Object.keys(row).length===2)throw Error('Change at least one field for each selected feed.');
      out.push(row);
    }
    if(!out.length)throw Error('Select at least one feed.');
    return {targets:out};
  }
  function tuningLock(value){
    tuningBusy=value;$('tuningRefresh').disabled=value;$('tuningPreview').disabled=value||!tuningTargets.some(t=>t.selected.checked);
    $('tuningProfiles').querySelectorAll('input').forEach(input=>{
      if(value){input.dataset.tuningWasDisabled=String(input.disabled);input.disabled=true;}
      else if(input.dataset.tuningWasDisabled!==undefined){input.disabled=input.dataset.tuningWasDisabled==='true';delete input.dataset.tuningWasDisabled;}
    });
  }
  async function tuningLoad(){
    if(tuningBusy)return;tuningInvalidate();tuningLock(true);
    try{const data=await api(base+'/tuning');tuningLock(false);tuningRender(data);$('tuningStatus').textContent='Local tuning workspace loaded. No provider reads.';}
    catch(error){$('tuningStatus').textContent=error.message;}
    finally{if(tuningBusy)tuningLock(false);}
  }
  $('tuningRefresh').onclick=tuningLoad;
  $('tuningPreview').onclick=async()=>{
    if(tuningBusy)return;tuningInvalidate();const turn=tuningGeneration;
    try{
      const payload=tuningSelection();tuningLock(true);
      const plan=await api(base+'/tuning/preview',payload);
      if(turn!==tuningGeneration)return;
      tuningPlan={payload,plan};
      const root=$('tuningPlan');
      const rows=plan.targets.flatMap(p=>Object.keys(p.after).filter(k=>p.before[k]!==p.after[k]).map(k=>
        [p.name,p.provider,p.revision,p.next_revision,tuningLabels[k]||k,p.before[k],p.after[k],p.checkpoint,p.retained_events]));
      root.replaceChildren(el('h3','Exact proposed changes'),table(['Feed','Provider','Old revision','New revision','Setting','Before','After','Checkpoint preserved','Retained events'],rows),
        table(['Feed','Current next window (illustrative)','Proposed next window (illustrative)','Status'],plan.targets.map(p=>[p.name,
          p.before_next_window.since+' → '+fmt(p.before_next_window.until),
          p.after_next_window.since+' → '+fmt(p.after_next_window.until),p.after_next_window.status])),
        el('p','Plan SHA-256: '+plan.plan_sha256),el('p','No source scope, retained dataset or checkpoint changes. Existing published copies remain. Recurring local and Gateway authorization must be reviewed again.'));
      const exact=check('I reviewed these exact operational settings and accept the new revisions.');
      const dispatch=check('I understand existing schedule and Gateway job pins will be stale; I will separately reauthorize them.');
      const workers=check('I stopped or reconciled local and Gateway workers and their pending receipts.');
      const apply=button('Apply all reviewed changes',async()=>{
        if(tuningBusy||!tuningPlan||tuningPlan.plan!==plan||![exact,dispatch,workers].every(x=>x.box.checked))return;
        tuningLock(true);apply.disabled=true;
        try{
          const result=await api(base+'/tuning/apply',{...payload,plan_sha256:plan.plan_sha256,
            confirm_exact_changes:true,confirm_dispatch_reauthorization:true,confirm_workers_reconciled:true});
          tuningLock(false);await tuningLoad();
          $('tuningStatus').textContent=result.replayed?'Prior receipt confirmed; no changes reapplied.':
            result.target_count+' feeds updated atomically. Checkpoints and retained data preserved. Reauthorize dispatch separately.';
          if(!busy)await load();
        }catch(error){$('tuningStatus').textContent=error.message;tuningInvalidate();}
        finally{if(tuningBusy)tuningLock(false);}
      },true);
      for(const x of [exact,dispatch,workers])x.box.onchange=()=>apply.disabled=tuningBusy||!tuningPlan||![exact,dispatch,workers].every(v=>v.box.checked);
      root.append(exact.node,dispatch.node,workers.node,apply);
      $('tuningStatus').textContent='Preview ready. No provider reads or mutations.';
    }catch(error){$('tuningStatus').textContent=error.message;}
    finally{tuningLock(false);}
  };
  let historyGeneration=0,historyBusy=false;
  const historyFields=['historyProfile','historyDays','historyState','historyLimit'];
  function historyInvalidate(){
    historyGeneration++;$('historyDownload').disabled=true;$('historyResults').replaceChildren();
    $('historyStatus').textContent='Filters changed. Load local run history again.';
  }
  for(const id of historyFields)$(id).oninput=historyInvalidate;
  function historyPath(){
    const profile=$('historyProfile').value.trim(),days=Number($('historyDays').value),limit=Number($('historyLimit').value);
    if((profile&&!/^rest_[a-f0-9]{24}$/.test(profile))||!Number.isInteger(days)||days<1||days>90||
       !Number.isInteger(limit)||limit<1||limit>1000)throw Error('Choose a valid profile ID, lookback and row limit.');
    const params=new URLSearchParams({days:String(days),limit:String(limit)});
    if(profile)params.set('profile_id',profile);if($('historyState').value)params.set('state',$('historyState').value);
    return base+'/run-history?'+params;
  }
  function historyRender(data){
    const s=data.summary,fields=Object.keys(s.counts);
    const root=$('historyResults');
    root.replaceChildren(el('p',data.returned_runs+' of '+data.matching_retained_runs+' matching retained attempts returned. Summary uses returned rows only.'+
      (data.truncated?' Increase the limit or narrow filters.':'')),
      table(['Outcome','Returned attempts'],Object.entries(s.states)),
      table(['Reported successful-run measure','Sum','Known runs','Unknown runs'],fields.map(k=>
        [k.replaceAll('_',' '),s.counts[k].sum_of_reported,s.counts[k].reported_runs,s.counts[k].unknown_successful_runs])),
      table(['Profile','Attempts','Succeeded','Failed','Interrupted','Invalidated','Mean successful seconds'],data.profiles.map(p=>
        [p.profile_id,p.runs,p.successful_runs,p.states.failed,p.states.interrupted,p.states.invalidated,p.successful_elapsed_seconds_mean])),
      el('h3','Failure and interruption codes'),
      table(['Code','Attempts'],Object.entries(s.error_counts)),
      el('h3','Exact admitted attempts'),
      table(['Profile / provider','Revision','Admitted UTC','Change window','Outcome','Elapsed seconds','Checkpoint before','Checkpoint after','Added','Duplicates','Pages','Error / cooldown'],
        data.runs.map(r=>[r.profile_id+' / '+r.provider,r.revision,r.admitted_at,r.since_at+' → '+r.until_at,
          r.state+(r.local_intent_unreconciled?' (unreconciled)':''),r.elapsed_seconds,r.checkpoint_before,r.checkpoint_after,
          r.events_added,r.duplicates_skipped,r.pages,[r.error_code,r.retry_not_before].filter(Boolean).join(' / ')])),
      el('p','Counts describe locally committed evidence, not live provider health or complete source coverage. An expired or invalidated intent does not prove remote cancellation.'));
  }
  async function historyLoad(save=false){
    if(historyBusy)return;historyBusy=true;const turn=++historyGeneration;
    $('historyRefresh').disabled=true;$('historyDownload').disabled=true;
    try{
      const data=await api(historyPath());
      if(turn!==historyGeneration)return;
      historyRender(data);$('historyDownload').disabled=false;
      $('historyStatus').textContent='Local journal refreshed. No provider reads or credential renewal.';
      if(save){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
        try{const a=el('a');a.href=url;a.download='owg-ingestion-run-history.json';a.click();}finally{URL.revokeObjectURL(url);}}
    }catch(error){if(turn===historyGeneration){$('historyResults').replaceChildren();$('historyStatus').textContent=error.message;}}
    finally{historyBusy=false;$('historyRefresh').disabled=false;}
  }
  $('historyRefresh').onclick=()=>historyLoad(false);
  $('historyDownload').onclick=()=>historyLoad(true);
  // Local source onboarding is distinct from live provider health or dispatch.
  let onboardingData=null,onboardingBusy=false;
  function onboardingRender(){
    if(!onboardingData)return;
    const d=onboardingData,s=d.summary,provider=$('onboardingProvider').value,filter=$('onboardingFilter').value;
    const sources=d.sources.filter(x=>(!provider||x.provider===provider)&&
      (!filter||filter==='ready'&&x.local_credential_state==='locally_available'&&!x.profile_ids.length&&!x.definition_drift_profiles.length||
       filter==='blocked'&&x.local_credential_state==='blocked'||filter==='drift'&&x.definition_drift_profiles.length||
       filter==='shared'&&x.shared_credential_reference));
    const profiles=d.profiles.filter(x=>!provider||x.provider===provider);
    $('onboardingSummary').replaceChildren(
      el('h3','Provider onboarding coverage'),
      table(['Provider','Catalog','Approved sources','Locally available','Blocked','Ready without profile','Existing profiles','Profiles to review'],
        s.providers.filter(x=>!provider||x.provider===provider).map(x=>[x.provider,x.catalog_state,x.configured_sources,x.ready_local_sources,
          x.blocked_local_sources,x.unconfigured_ready_sources,x.retained_profiles,x.profiles_requiring_review])),
      el('h3','REST endpoint policy'),
      table(['Local policy measure','Value'],Object.entries(s.rest_endpoint_policy).map(([k,v])=>[k.replaceAll('_',' '),v])),
      el('h3','Managed secret-file inventory'),
      table(['Local measure','Value'],Object.entries(s.secret_file_inventory).map(([k,v])=>[k.replaceAll('_',' '),v])),
      el('p','Unreferenced mappings may belong to workers or other configured services. Do not remove them based on this report alone.'));
    $('onboardingResults').replaceChildren(
      el('h3','Catalog diagnostics'),
      table(['Provider','Setting present','State','Sources','Error'],d.catalogs.filter(x=>!provider||x.provider===provider).map(x=>[x.provider,x.deployment_setting_present,x.state,x.source_count,x.error_code])),
      el('h3','Approved source readiness'),
      table(['Provider / alias','Definition SHA-256','Local credential','Delivery','Matching profiles','Drifted profiles','Shared reference','Diagnostic'],
        sources.map(x=>[x.provider+' / '+x.source_alias,x.definition_sha256,x.local_credential_state,x.credential_delivery,
          x.profile_ids.join(', '),x.definition_drift_profiles.join(', '),x.shared_credential_reference,x.error_code])),
      el('h3','Existing profile binding and drift'),
      table(['Provider','Profile ID','Revision','Alias','Definition','Binding','Enabled','Diagnostic'],profiles.map(x=>[x.provider,x.profile_id,x.revision,x.source_alias,x.definition_state,x.binding_state,x.enabled,x.error_code])),
      el('p',d.interpretation));
  }
  async function onboardingLoad(support=false){
    if(onboardingBusy)return;onboardingBusy=true;
    $('onboardingRefresh').disabled=true;$('onboardingExport').disabled=true;
    try{
      const data=await api(base+'/onboarding'+(support?'?support_only=true':''));
      if(support){
        if(data.scope!=='counts_only_support'||'sources' in data||'profiles' in data||'catalogs' in data)throw Error('Support projection rejected.');
        const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
        try{const a=el('a');a.href=url;a.download='owg-connector-onboarding-support.json';a.click();}finally{URL.revokeObjectURL(url);}
        $('onboardingStatus').textContent='Fresh counts-only support report downloaded.';
      }else{
        onboardingData=data;
        const select=$('onboardingProvider'),previous=select.value;
        const all=el('option','All providers');all.value='';select.replaceChildren(all);
        for(const x of data.summary.providers){const opt=el('option',x.provider);opt.value=x.provider;select.append(opt);}
        select.value=data.summary.providers.some(x=>x.provider===previous)?previous:'';
        onboardingRender();$('onboardingStatus').textContent='Local inspection completed. Provider access and live health remain unknown.';
      }
    }catch(error){if(!support){onboardingData=null;$('onboardingSummary').replaceChildren();$('onboardingResults').replaceChildren();}$('onboardingStatus').textContent=error.message;}
    finally{onboardingBusy=false;$('onboardingRefresh').disabled=false;$('onboardingExport').disabled=!onboardingData;}
  }
  $('onboardingRefresh').onclick=()=>onboardingLoad(false);
  $('onboardingExport').onclick=()=>onboardingLoad(true);
  $('onboardingProvider').onchange=onboardingRender;
  $('onboardingFilter').onchange=onboardingRender;
  let integrityRows=[],integrityBusy=false,integrityGeneration=0;
  function integrityReset(){
    integrityGeneration++;$('integrityResults').replaceChildren();
    $('integrityPreview').disabled=integrityBusy||!integrityRows.some(x=>x.box.checked);
  }
  function integrityLock(value){
    integrityBusy=value;$('integrityRefresh').disabled=value;$('integrityAction').disabled=value;
    for(const x of integrityRows)x.box.disabled=value||!x.profile.log_id;
    $('integrityPreview').disabled=value||!integrityRows.some(x=>x.box.checked);
  }
  async function integrityLoad(){
    if(integrityBusy)return;integrityReset();integrityLock(true);
    try{
      const data=await api(base+'/integrity');integrityRows=[];
      const cards=data.profiles.map(p=>{
        const item=check(p.provider+' / '+p.profile_id+' · revision '+p.revision+
          (p.integrity_hold?' · integrity hold':'')+(!p.log_id?' · no retained log':''));
        item.box.disabled=!p.log_id;item.box.onchange=integrityReset;
        integrityRows.push({profile:p,box:item.box});return item.node;
      });
      $('integrityTargets').replaceChildren(...cards.length?cards:[el('p','No configured retained feeds.')]);
      $('integrityStatus').textContent='Profiles loaded. Structural checks are explicitly requested.';
    }catch(error){integrityRows=[];$('integrityTargets').replaceChildren();$('integrityStatus').textContent=error.message;}
    finally{integrityLock(false);}
  }
  $('integrityRefresh').onclick=integrityLoad;$('integrityAction').onchange=integrityReset;
  $('integrityPreview').onclick=async()=>{
    if(integrityBusy)return;integrityReset();const turn=integrityGeneration;
    const actionName=$('integrityAction').value;
    const targets=integrityRows.filter(x=>x.box.checked).map(x=>({profile_id:x.profile.profile_id,revision:x.profile.revision}));
    if(!targets.length||targets.length>5){$('integrityStatus').textContent='Select one to five profiles.';return;}
    integrityLock(true);
    try{
      const plan=await api(base+'/integrity/preview?action='+actionName,{targets});
      if(turn!==integrityGeneration)return;
      const root=$('integrityResults');
      root.replaceChildren(el('h3','Exact retained structural findings'),
        table(['Profile','Status','Events','Objects','Source keys','Eligible for action'],plan.targets.map(x=>
          [x.profile_id,x.status,x.counts.events,x.counts.objects,x.counts.event_source_keys,x.eligible])),
        table(['Profile','Violation','Affected population / difference'],plan.targets.flatMap(x=>
          Object.entries(x.violations).map(([k,v])=>[x.profile_id,k.replaceAll('_',' '),v]))),
        el('p','Plan SHA-256: '+plan.plan_sha256),el('p','This is a structural audit, not a proof of provider completeness or original content authenticity.'));
      if(plan.all_eligible){
        const exact=check('I reviewed the exact structural findings.');
        const local=check('I approve this local revision change and understand feeds remain disabled.');
        const remote=check('I will separately coordinate remote workers, publication copies and scheduling authorization.');
        const apply=button(actionName==='hold'?'Apply integrity holds':'Release reviewed integrity holds',async()=>{
          if(integrityBusy||turn!==integrityGeneration||![exact,local,remote].every(x=>x.box.checked))return;
          integrityLock(true);apply.disabled=true;
          try{
            const result=await api(base+'/integrity/apply?action='+actionName,{targets,plan_sha256:plan.plan_sha256,
              confirm_exact_findings:true,confirm_local_containment:true,confirm_remote_coordination:true});
            integrityLock(false);await integrityLoad();
            $('integrityStatus').textContent=result.changed.length+' profiles changed atomically; retained data unchanged. Separate reauthorization required.';
            if(!busy)await load();
          }catch(error){integrityReset();$('integrityStatus').textContent=error.message;}
          finally{integrityLock(false);}
        },true);
        for(const x of [exact,local,remote])x.box.onchange=()=>apply.disabled=integrityBusy||![exact,local,remote].every(y=>y.box.checked);
        root.append(exact.node,local.node,remote.node,apply);
      }else root.append(el('p','Not every selected profile is eligible. Narrow the selection or investigate the findings.'));
      $('integrityStatus').textContent='Structural review completed. No provider reads or mutations.';
    }catch(error){$('integrityStatus').textContent=error.message;}
    finally{integrityLock(false);}
  };
  let coverageRows=[],coverageBusy=false,coverageGeneration=0,coverageResult=null;
  const coverageKinds={committed_request_window:'Committed request window',before_configured_anchor:'Before configured anchor',
    unknown_prior_coverage:'Unknown prior coverage',not_yet_checkpointed:'Not yet checkpointed'};
  const coverageHours=us=>us==null?'Unknown':(us/3600000000).toFixed(3);
  function coverageReset(){
    coverageGeneration++;coverageResult=null;$('coverageResults').replaceChildren();$('coverageExport').disabled=true;
    $('coverageReview').disabled=coverageBusy||!coverageRows.some(x=>x.box.checked);
  }
  function coverageLock(value){
    coverageBusy=value;$('coverageRefresh').disabled=value;$('coverageSince').disabled=value;$('coverageUntil').disabled=value;
    for(const x of coverageRows)x.box.disabled=value;
    $('coverageReview').disabled=value||!coverageRows.some(x=>x.box.checked);
    $('coverageExport').disabled=value||!coverageResult;
  }
  $('coverageSince').oninput=coverageReset;$('coverageUntil').oninput=coverageReset;
  $('coverageRefresh').onclick=async()=>{
    if(coverageBusy)return;coverageReset();coverageLock(true);
    try{
      const data=await api(base+'/coverage');coverageRows=[];
      const cards=data.profiles.map(p=>{
        const item=check(p.provider+' / '+p.profile_id+' · revision '+p.revision);
        item.box.onchange=coverageReset;coverageRows.push({profile:p,box:item.box});return item.node;
      });
      $('coverageTargets').replaceChildren(...cards.length?cards:[el('p','No configured retained feeds.')]);
      if(!$('coverageSince').value||!$('coverageUntil').value){
        const now=new Date(),earlier=new Date(now.getTime()-30*86400000);
        $('coverageSince').value=earlier.toISOString();$('coverageUntil').value=now.toISOString();
      }
      $('coverageStatus').textContent='Select profiles and a UTC review interval up to 366 days.';
    }catch(error){coverageRows=[];$('coverageTargets').replaceChildren();$('coverageStatus').textContent=error.message;}
    finally{coverageLock(false);}
  };
  $('coverageReview').onclick=async()=>{
    if(coverageBusy)return;coverageReset();const turn=coverageGeneration;
    const targets=coverageRows.filter(x=>x.box.checked).map(x=>({profile_id:x.profile.profile_id,revision:x.profile.revision}));
    const since=$('coverageSince').value.trim(),until=$('coverageUntil').value.trim();
    if(!targets.length||targets.length>20||!since||!until){$('coverageStatus').textContent='Choose 1–20 profiles and a valid UTC interval.';return;}
    coverageLock(true);
    try{
      const result=await api(base+'/coverage/review',{targets,since,until});
      if(turn!==coverageGeneration)return;
      const s=result.summary,root=$('coverageResults');
      root.replaceChildren(el('h3','Exact committed request-window review'),
        el('p',s.included_profiles+' of '+s.selected_profiles+' profiles included; '+s.excluded_inconsistent_profiles+' excluded due to inconsistent coverage state.'),
        table(['Coverage classification','Profile-hours'],Object.entries(s.microseconds_by_kind).map(([k,v])=>[coverageKinds[k]||k,coverageHours(v)])),
        el('p','Committed request-window fraction: '+(s.committed_request_window_fraction===null?'Unknown':(100*s.committed_request_window_fraction).toFixed(2)+'%')),
        table(['Profile / provider','Index consistent','Recorded commits','Historical commits','Intervals retained','Checkpoint','Uncheckpointed planning hours','Estimated advancing windows'],result.profiles.map(p=>
          [p.profile_id+' / '+p.provider,p.coverage_index_consistent,p.recorded_commits,p.historical_commits,p.retained_interval_count,p.checkpoint,coverageHours(p.backlog?.uncheckpointed_microseconds),p.backlog?.estimated_advancing_windows])),
        el('h3','Exact interval partition'),
        table(['Profile','Classification','From UTC','Until UTC','Hours'],result.profiles.flatMap(p=>
          p.segments.map(x=>[p.profile_id,coverageKinds[x.kind]||x.kind,x.since,x.until,coverageHours(x.microseconds)]))),
        el('p',result.interpretation));
      coverageResult=result;$('coverageStatus').textContent='Local review completed. No provider reads or execution authorization.';
    }catch(error){$('coverageStatus').textContent=error.message;}
    finally{coverageLock(false);}
  };
  $('coverageExport').onclick=()=>{
    if(coverageBusy||!coverageResult)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(coverageResult,null,2)],{type:'application/json'}));
    try{const a=el('a');a.href=url;a.download='owg-committed-window-coverage.json';a.click();}finally{URL.revokeObjectURL(url);}
  };
  let exclusionProfiles=[],exclusionGeneration=0,exclusionBusy=false;
  function exclusionReset(){
    exclusionGeneration++;$('exclusionResults').replaceChildren();
    $('exclusionPreview').disabled=exclusionBusy||!$('exclusionProfile').value||!$('exclusionCases').value.trim();
  }
  function exclusionLock(value){
    exclusionBusy=value;$('exclusionRefresh').disabled=value;$('exclusionProfile').disabled=value;$('exclusionCases').disabled=value;
    $('exclusionPreview').disabled=value||!$('exclusionProfile').value||!$('exclusionCases').value.trim();
  }
  $('exclusionProfile').onchange=exclusionReset;$('exclusionCases').oninput=exclusionReset;
  async function exclusionLoad(){
    if(exclusionBusy)return;exclusionReset();exclusionLock(true);
    try{
      const data=await api(base+'/case-exclusions');
      exclusionProfiles=data.profiles.filter(p=>p.supported&&p.status!=='dataset_deleted'&&!p.integrity_hold&&!p.active_or_unreconciled_run);
      const select=$('exclusionProfile');select.replaceChildren();
      for(const p of exclusionProfiles){const opt=el('option',p.provider+' / '+p.profile_id+' · '+p.excluded_cases+' excluded · revision '+p.revision);opt.value=p.profile_id;select.append(opt);}
      $('exclusionStatus').textContent='Select an eligible profile. Use exact native IDs; typed sources require their reviewed native type.';
    }catch(error){exclusionProfiles=[];$('exclusionProfile').replaceChildren();$('exclusionStatus').textContent=error.message;}
    finally{exclusionLock(false);}
  }
  $('exclusionRefresh').onclick=exclusionLoad;
  $('exclusionPreview').onclick=async()=>{
    if(exclusionBusy)return;exclusionReset();const turn=exclusionGeneration;
    try{
      const p=exclusionProfiles.find(x=>x.profile_id===$('exclusionProfile').value);
      if(!p)throw Error('Load and select a profile.');
      const cases=$('exclusionCases').value.split(/\r?\n/).filter(x=>x.length).map(x=>{
        const parts=x.split('\t');if(parts.length===1)return {native_case_id:parts[0]};
        if(parts.length===2)return {native_type:parts[0],native_case_id:parts[1]};
        throw Error('Use an exact identifier or type and identifier separated by one tab.');
      });
      if(!cases.length||cases.length>100)throw Error('Select 1–100 cases.');
      const payload={profile_id:p.profile_id,revision:p.revision,cases};
      exclusionLock(true);
      const plan=await api(base+'/case-exclusions/preview',payload);
      if(turn!==exclusionGeneration)return;
      const root=$('exclusionResults');
      root.replaceChildren(el('h3','Exact prospective exclusion review'),
        table(['Measure','Value'],[['Requested cases',plan.requested_cases],['Already excluded',plan.already_excluded_cases],['New exclusions',plan.new_exclusions],
          ['Existing retained matches',plan.retained_matches],['Retained integrity',plan.retained_integrity_state],['Eligible',plan.eligible],['After apply',plan.total_exclusions_after_apply]]),
        el('p','Plan SHA-256: '+plan.plan_sha256),el('p','This does not erase source records or existing copies. The feed will be disabled and must be separately reauthorized.'));
      if(plan.eligible){
        const exact=check('I reviewed these exact native case exclusions.');
        const prospective=check('I understand these exclusions only prevent future retention in this profile, not source access or erasure.');
        const dispatch=check('I will separately review enablement and recurring authorization.');
        const apply=button('Apply prospective exclusions',async()=>{
          if(exclusionBusy||turn!==exclusionGeneration||![exact,prospective,dispatch].every(x=>x.box.checked))return;
          exclusionLock(true);apply.disabled=true;
          try{
            await api(base+'/case-exclusions/apply',{...payload,plan_sha256:plan.plan_sha256,
              confirm_exact_exclusions:true,confirm_prospective_only:true,confirm_dispatch_reauthorization:true});
            $('exclusionCases').value='';exclusionLock(false);await exclusionLoad();
            $('exclusionStatus').textContent='Exclusions saved. Feed disabled; retained evidence unchanged. Reauthorize separately.';
            if(!busy)await load();
          }catch(error){exclusionReset();$('exclusionStatus').textContent=error.message;}
          finally{exclusionLock(false);}
        },true);
        for(const x of [exact,prospective,dispatch])x.box.onchange=()=>apply.disabled=exclusionBusy||![exact,prospective,dispatch].every(y=>y.box.checked);
        root.append(exact.node,prospective.node,dispatch.node,apply);
      }else root.append(el('p','No changes authorized. Existing evidence, inconclusive integrity or an unchanged exclusion set blocks this operation.'));
      $('exclusionStatus').textContent='Review complete. No provider reads or mutations.';
    }catch(error){$('exclusionStatus').textContent=error.message;}
    finally{exclusionLock(false);}
  };
  let slaData=null,slaBusy=false;
  const slaFields=['slaProfile','slaInterval','slaSuccess','slaCheckpoint','slaFailures','slaHistoricalFailures','slaUnknownHours','slaLookback','slaExpired','slaHold','slaEnabled','slaConfirm'];
  const slaSelected=()=>slaData?.profiles.find(x=>x.profile_id===$('slaProfile').value);
  function slaLock(value){
    slaBusy=value;for(const id of slaFields)$(id).disabled=value||!slaData?.profiles.length;
    $('slaRefresh').disabled=value;$('slaEvaluate').disabled=value||!slaData?.profiles.some(p=>p.rule?.enabled);
    $('slaSave').disabled=value||!slaSelected();
    $('slaResults').querySelectorAll('button').forEach(b=>b.disabled=value);
  }
  function slaFill(){
    const p=slaSelected(),r=p?.rule;
    $('slaInterval').value=r?.interval_minutes??15;$('slaSuccess').value=r?r.success_age_hours??'':24;
    $('slaCheckpoint').value=r?r.checkpoint_age_hours??'':48;$('slaFailures').value=r?r.failure_streak??'':3;
    $('slaHistoricalFailures').value=r?.historical_failure_streak??'';$('slaUnknownHours').value=r?.unknown_coverage_hours??'';$('slaLookback').value=r?.coverage_lookback_days??30;
    $('slaExpired').checked=r?.watch_expired_intent??true;$('slaHold').checked=r?.watch_integrity_hold??true;
    $('slaEnabled').checked=r?.enabled??true;$('slaConfirm').checked=false;
  }
  function slaRender(){
    const d=slaData,root=$('slaResults');
    root.replaceChildren(el('p',d.active_alerts+' active alerts; '+d.unacknowledged_alerts+' unacknowledged.'),
      table(['Profile','Policy revision','Enabled','Last evaluated','Next due'],d.profiles.map(p=>[p.profile_id,p.rule?.revision,p.rule?.enabled,p.rule?.last_evaluated_at,p.rule?.next_due_at])),
      table(['Profile','Signal','Observation','Value','Threshold','Reason'],d.profiles.flatMap(p=>(p.rule?.last_observations||[]).map(o=>[p.profile_id,o.signal,o.state,o.signal==='unknown_prior_coverage'&&o.value!=null?(o.value/3600000000).toFixed(3)+' h':o.value,o.signal==='unknown_prior_coverage'&&o.threshold!=null?(o.threshold/3600000000)+' h':o.threshold,o.reason]))),
      el('h3','Alert episodes'),table(['Profile','Signal','Active','Opened','Last observation','Acknowledged','Closed','Close reason'],d.episodes.map(e=>[e.profile_id,e.signal,e.active,e.opened_at,e.observation.state,e.acknowledged_at,e.closed_at,e.close_reason])));
    for(const e of d.episodes.filter(e=>e.active&&!e.acknowledged_at)){
      const wrap=el('div');const approve=check('Acknowledge '+e.signal+' for '+e.profile_id+' (does not resolve the alert).');
      const b=button('Acknowledge',async()=>{
        if(slaBusy||!approve.box.checked)return;
        await slaAction(()=>api(base+'/service-levels/acknowledge',{episode_id:e.episode_id,version:e.version,confirm_acknowledgement:true}),'Alert acknowledged, not resolved.');
      },true);approve.box.onchange=()=>b.disabled=slaBusy||!approve.box.checked;wrap.append(approve.node,b);root.append(wrap);
    }
  }
  async function slaLoad(){
    const data=await api(base+'/service-levels'),previous=$('slaProfile').value;
    slaData=data;const select=$('slaProfile');select.replaceChildren();
    for(const p of data.profiles){const o=el('option',p.provider+' / '+p.profile_id+' · revision '+p.revision);o.value=p.profile_id;select.append(o);}
    if(data.profiles.some(p=>p.profile_id===previous))select.value=previous;
    slaFill();slaRender();
  }
  async function slaAction(fn,message){
    if(slaBusy)return;slaLock(true);
    try{if(fn)await fn();await slaLoad();$('slaStatus').textContent=message;}
    catch(error){slaData=null;$('slaResults').replaceChildren();$('slaStatus').textContent=error.message;}
    finally{slaLock(false);$('slaResults').querySelectorAll('button').forEach(b=>b.disabled=true);}
  }
  $('slaRefresh').onclick=()=>slaAction(null,'Local monitoring loaded. Provider health remains unknown.');
  $('slaProfile').onchange=slaFill;
  $('slaEvaluate').onclick=()=>slaAction(async()=>{
    const r=await api(base+'/service-levels/evaluate',{confirm_local_evaluation:true});
    if(r.errors.length)throw Error('Some local evaluations failed; reload to review committed results.');
  },'Local policies evaluated without provider reads.');
  $('slaForm').onsubmit=event=>{
    event.preventDefault();const p=slaSelected();if(!p||!$('slaConfirm').checked)return;
    const number=id=>$(id).value===''?null:Number($(id).value);
    const payload={profile_id:p.profile_id,profile_revision:p.revision,expected_rule_revision:p.rule?.revision??0,
      enabled:$('slaEnabled').checked,interval_minutes:number('slaInterval'),success_age_hours:number('slaSuccess'),
      checkpoint_age_hours:number('slaCheckpoint'),failure_streak:number('slaFailures'),
      historical_failure_streak:number('slaHistoricalFailures'),unknown_coverage_hours:number('slaUnknownHours'),coverage_lookback_days:number('slaLookback'),watch_expired_intent:$('slaExpired').checked,
      watch_integrity_hold:$('slaHold').checked,confirm_local_monitoring:true};
    slaAction(()=>api(base+'/service-levels/save',payload),'Reviewed local monitoring policy saved.');
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
