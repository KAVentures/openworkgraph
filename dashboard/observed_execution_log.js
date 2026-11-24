/* Explicit local snapshot creation from reviewed, selected execution evidence. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.observed-execution-log.v1';
  const el=(tag,text,cls)=>{
    const node=document.createElement(tag);
    if(text!==undefined&&text!==null)node.textContent=String(text);
    if(cls)node.className=cls;
    return node;
  };
  const button=label=>{const node=el('button',label,'secondary');node.type='button';return node;};
  const validId=id=>typeof id==='string'&&/^execution:[a-f0-9]{16}$/.test(id);
  const integer=n=>Number.isSafeInteger(n)&&n>=0;
  const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  function matchingPreview(report,spec){
    if(!report||report.format!==FORMAT||!sha(report.snapshot_sha256)||
      report.source_limit_reached!==false||report.business_case_identity_verified!==false||
      report.business_outcomes_verified!==false||report.capture_complete!==false||
      report.sensor_observations_not_deduplicated_actions!==true)return false;
    const ids=spec.execution_ids,returned=report.scope?.execution_ids;
    if(!Array.isArray(ids)||!ids.length||ids.length>12||!ids.every(validId)||new Set(ids).size!==ids.length||
      !Array.isArray(returned)||JSON.stringify(ids)!==JSON.stringify(returned))return false;
    for(const key of ['since','until']){
      const expected=spec[key],actual=report.scope?.[key];
      if(expected==null?actual!=null:(!Number.isFinite(Date.parse(expected))||Date.parse(expected)!==Date.parse(actual)))return false;
    }
    const cases=report.cases;
    return report.case_count===ids.length&&integer(report.event_count)&&report.event_count>0&&report.event_count<=10000&&
      Array.isArray(cases)&&cases.length===ids.length&&cases.every((row,i)=>row?.execution_id===ids[i]&&
        ['human','agent'].includes(row.actor_kind)&&integer(row.events)&&row.events>0&&integer(row.timestamp_tie_groups))&&
      cases.reduce((sum,row)=>sum+row.events,0)===report.event_count&&
      integer(report.timestamp_tie_groups)&&cases.reduce((sum,row)=>sum+row.timestamp_tie_groups,0)===report.timestamp_tie_groups;
  }

  function persistentNotice(message){
    const root=document.getElementById('owgWorkflowExplorer');
    if(!root)return;
    let status=document.getElementById('owgObservedSnapshotNotice');
    if(!status){status=el('p',null,'muted');status.id='owgObservedSnapshotNotice';status.setAttribute('role','status');root.appendChild(status);}
    status.textContent=message;
  }

  async function request(path,body,confirmed=false){
    await window.__owgAuthReady;
    const headers={'Content-Type':'application/json'};
    if(confirmed)headers['x-owg-local-storage-confirmed']='true';
    const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(body),cache:'no-store'});
    const payload=await response.json().catch(()=>null);
    if(!response.ok)throw Error(typeof payload?.detail==='string'?payload.detail:'Snapshot request failed ('+response.status+').');
    return payload;
  }

  function render(parent,matrix,scope,isCurrent){
    if(!matrix?.rows?.length||matrix.rows.some(row=>!validId(row.id)))return;
    const section=el('section');section.id='owgExplorerExecutionLog';
    section.appendChild(el('h5','Analyse recorded executions as a saved event log'));
    section.appendChild(el('p','Create a separate local structural snapshot for the event-log workspace. One inferred execution becomes one case; retained sensor observations become timed events. This is not an authoritative business log. Repeated sensor observations and timestamp ties may affect analytics.','muted'));
    if(matrix.sourceLimited){
      section.appendChild(el('p','Narrow the evidence window before creating a saved snapshot. The source limit or coverage status prevents this import.','muted'));
      parent.appendChild(section);return;
    }
    const fieldset=el('fieldset');fieldset.appendChild(el('legend','Executions to include'));
    const controls=[];
    matrix.rows.forEach((row,index)=>{
      const label=el('label');label.style.display='block';
      const check=el('input');check.type='checkbox';check.checked=true;
      label.append(check,document.createTextNode(' Run '+(index+1)+' · '+row.actor+' · '+row.id));
      fieldset.appendChild(label);controls.push({id:row.id,check});
    });
    const nameLabel=el('label','Snapshot name ');
    const name=el('input');name.type='text';name.maxLength=90;name.value='Workflow selection';nameLabel.appendChild(name);
    const preview=button('Preview execution event log');
    const summary=el('div');summary.setAttribute('role','status');
    const confirmLabel=el('label');const confirm=el('input');confirm.type='checkbox';
    confirmLabel.append(confirm,document.createTextNode(' Store this reviewed independent copy on this computer. It must be deleted separately from recorded history.'));
    const create=button('Create saved event log');
    const status=el('p');status.setAttribute('role','status');
    const open=button('Go to saved event logs');open.hidden=true;
    open.addEventListener('click',()=>{
      document.querySelector('[role=tab][data-tab=evidence]')?.click();
      document.getElementById('owgEventLogCard')?.scrollIntoView({block:'start'});
    });
    let generation=0,busy=false,review=null;
    const spec=()=>({execution_ids:controls.filter(row=>row.check.checked).map(row=>row.id),
      ...(scope.since?{since:scope.since}:{}),...(scope.until?{until:scope.until}:{})});
    const current=()=>section.isConnected&&isCurrent();
    const fresh=()=>review&&JSON.stringify(review.spec)===JSON.stringify(spec());
    const update=()=>{
      for(const row of controls)row.check.disabled=busy;
      name.disabled=busy;
      preview.disabled=busy||!spec().execution_ids.length||!current();
      confirm.disabled=busy||!fresh();
      create.disabled=busy||!current()||!fresh()||!confirm.checked||!name.value.trim();
    };
    const invalidate=()=>{generation++;review=null;confirm.checked=false;summary.replaceChildren();status.textContent='';open.hidden=true;update();};
    controls.forEach(row=>row.check.addEventListener('change',invalidate));
    name.addEventListener('input',()=>{confirm.checked=false;update();});
    confirm.addEventListener('change',update);
    preview.addEventListener('click',async()=>{
      invalidate();
      const selection=spec(),serial=++generation;
      if(!current()||!selection.execution_ids.length)return;
      busy=true;update();status.textContent='Reading selected structural observations…';
      try{
        const report=await request('/v1/workflow-evidence/event-log/preview',selection);
        if(!current()||serial!==generation)return;
        if(!matchingPreview(report,selection))throw Error('The preview did not match the exact selection.');
        review={spec:selection,report};
        summary.replaceChildren(el('p',report.case_count+' execution cases · '+report.event_count+' observations · '+report.timestamp_tie_groups+' tied timestamp groups.'));
        const list=el('ul');
        report.cases.forEach(row=>list.appendChild(el('li',row.execution_id+' · '+row.events+' observations · '+row.membership_basis)));
        summary.appendChild(list);
        status.textContent='Preview ready. Review the independent retention and confirm before creating the log.';
      }catch(error){if(current()&&serial===generation)status.textContent=String(error.message||error);}
      finally{busy=false;update();}
    });
    create.addEventListener('click',async()=>{
      if(busy||!current()||!fresh()||!confirm.checked||!name.value.trim())return;
      const chosen=review,serial=++generation;
      busy=true;confirm.checked=false;update();status.textContent='Creating the reviewed local snapshot. Changing the dashboard view does not cancel this save.';
      try{
        const result=await request('/v1/workflow-evidence/event-log/create',{
          selection:chosen.spec,expected_sha256:chosen.report.snapshot_sha256,name:name.value.trim()
        },true);
        if(!matchingPreview(result,chosen.spec)||result.snapshot_sha256!==chosen.report.snapshot_sha256||
          result.stored_independent_snapshot!==true||!/^log_[a-f0-9]{16}$/.test(result.log_id||''))throw Error('Save response could not be verified. Check saved logs before retrying.');
        review=null;
        persistentNotice('Created local event log: '+result.name+'. If not listed, reopen the dashboard after preserving unsaved edits.');
        if(current()&&serial===generation){
          status.textContent='Saved '+result.event_count+' observations as '+result.name+'. Go to the event-log workspace; reopen the dashboard if its list has not refreshed.';
          open.hidden=false;
        }
      }catch(error){
        const message=String(error.message||error);
        if(current()&&serial===generation){review=null;status.textContent=message;}
        else persistentNotice('Local snapshot result: '+message);
      }finally{busy=false;update();}
    });
    section.append(fieldset,nameLabel,preview,summary,confirmLabel,create,status,open);
    parent.appendChild(section);update();
  }
  window.owgObservedExecutionLog=Object.freeze({render});
  window.__owgObservedExecutionLogModel={matchingPreview};
})();
