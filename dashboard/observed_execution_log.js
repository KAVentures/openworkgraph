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
  const validLogId=id=>typeof id==='string'&&/^log_[a-f0-9]{16}$/.test(id);
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
    const open=button('Open created event log');open.hidden=true;
    let createdLogId=null;
    open.addEventListener('click',async()=>{
      if(!validLogId(createdLogId))return;
      open.disabled=true;
      try{
        document.querySelector('[role=tab][data-tab=evidence]')?.click();
        if(typeof window.owgEventLogWorkspace?.openSaved!=='function')throw Error('Saved event-log workspace is unavailable. Reopen the dashboard.');
        await window.owgEventLogWorkspace.openSaved(createdLogId);
        document.getElementById('owgEventLogCard')?.scrollIntoView({block:'start'});
      }catch(error){persistentNotice('Saved log exists; '+String(error.message||error));}
      finally{open.disabled=false;}
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
    const invalidate=()=>{generation++;review=null;confirm.checked=false;summary.replaceChildren();status.textContent='';open.hidden=true;createdLogId=null;update();};
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
          result.stored_independent_snapshot!==true||!validLogId(result.log_id))throw Error('Save response could not be verified. Check saved logs before retrying.');
        review=null;
        persistentNotice('Created local event log: '+result.name+'. If not listed, reopen the dashboard after preserving unsaved edits.');
        if(current()&&serial===generation){
          createdLogId=result.log_id;
          status.textContent='Saved '+result.event_count+' observations as '+result.name+'. Open the saved log to use the existing process-mining tools.';
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
  function sourceManifestMatches(report,logId,metadata){
    return !!report&&report.log_id===logId&&validLogId(logId)&&
      report.stored_independent_snapshot===true&&report.source_history_rechecked===false&&
      metadata?.manifest_available===true&&report.snapshot_sha256===metadata.snapshot_sha256&&
      report.case_count===metadata.original_case_count&&report.event_count===metadata.original_event_count&&
      report.stored_event_count===report.event_count&&matchingPreview(report,report.scope||{});
  }

  function recheckMatches(report,logId,digest){
    if(!report||report.format!=='openworkgraph.observed-execution-log-recheck.v1'||
      report.log_id!==logId||report.original_snapshot_sha256!==digest||
      !Number.isFinite(Date.parse(report.checked_at))||report.stored_log_changed!==false||
      report.capture_complete!==false||report.business_case_identity_verified!==false||
      report.business_outcomes_verified!==false)return false;
    return report.status==='projection_matches'?report.current_projection_sha256===digest:
      report.status==='projection_changed'?sha(report.current_projection_sha256)&&report.current_projection_sha256!==digest:
      report.status==='source_unavailable'&&report.current_projection_sha256===null;
  }

  // Read the saved manifest on demand; a separate user action is required to
  // query retained history. The event log remains useful even after history
  // expires and the source cannot be re-read.
  function renderSource(parent,log,isCurrent){
    const metadata=log?.observed_execution_snapshot;
    if(!metadata||!validLogId(log.log_id))return;
    const section=el('section');section.id='owgObservedSnapshotSource';
    section.appendChild(el('h4','Original observed execution snapshot'));
    if(metadata.manifest_available!==true||!sha(metadata.snapshot_sha256)){
      section.appendChild(el('p','The original source manifest is unavailable. This independent saved dataset remains analysable; its original rows cannot be appended to. No source identity or outcome claim is available.','muted'));
      parent.appendChild(section);return;
    }
    section.appendChild(el('p',metadata.original_case_count+' inferred executions · '+metadata.original_event_count+
      ' original structural observations · '+metadata.timestamp_tie_groups+' timestamp tie groups. This independent saved log is not a verified business record. Its original event rows cannot be appended to.','muted'));
    const load=button('Show original execution references'),check=button('Recheck retained source');
    check.disabled=true;
    const details=el('div'),status=el('p');status.setAttribute('role','status');
    const current=()=>section.isConnected&&isCurrent();
    let report=null,busy=false;
    const update=()=>{load.disabled=busy||!current();check.disabled=busy||!report||!current();};
    load.addEventListener('click',async()=>{
      if(busy||!current())return;
      busy=true;report=null;update();details.replaceChildren();
      status.textContent='Reading saved snapshot provenance…';
      try{
        await window.__owgAuthReady;
        const response=await fetch('/v1/workflow-evidence/event-log/source?'+new URLSearchParams({log_id:log.log_id}),{cache:'no-store'});
        if(!response.ok)throw Error('Original snapshot unavailable ('+response.status+').');
        const result=await response.json();
        if(!current())return;
        if(!sourceManifestMatches(result,log.log_id,metadata))throw Error('Saved source manifest does not match this log.');
        report=result;
        details.appendChild(el('p','Original scope: '+(report.scope.since||'All retained history')+' to '+(report.scope.until||'unbounded end')+'.'));
        details.appendChild(el('p','Snapshot SHA-256: '+report.snapshot_sha256,'muted'));
        const list=el('ul');
        report.cases.forEach(row=>{
          const item=el('li',row.execution_id+' · '+row.actor_kind+' · '+row.events+' observations ');
          const open=button('Inspect retained source execution');
          open.addEventListener('click',async()=>{
            if(!current()||busy||report!==result)return;
            open.disabled=true;
            try{
              if(typeof window.owgWorkflowExplorer?.inspectSnapshotExecution!=='function')throw Error('Workflow explorer unavailable.');
              document.querySelector('[role=tab][data-tab=overview]')?.click();
              await window.owgWorkflowExplorer.inspectSnapshotExecution({executionId:row.execution_id,
                window:{since:result.scope.since,until:result.scope.until}});
              document.getElementById('owgWorkflowExplorer')?.scrollIntoView({block:'start'});
            }catch(error){if(current())status.textContent=String(error.message||error);}
            finally{open.disabled=false;}
          });
          item.appendChild(open);list.appendChild(item);
        });
        details.appendChild(list);
        status.textContent='Original manifest loaded. Retained source history has not been rechecked.';
      }catch(error){if(current())status.textContent=String(error.message||error);}
      finally{busy=false;update();}
    });
    check.addEventListener('click',async()=>{
      if(busy||!current()||!report)return;
      const selected=report;busy=true;update();status.textContent='Comparing the original scope with currently retained structural evidence…';
      try{
        const result=await request('/v1/workflow-evidence/event-log/recheck',{log_id:log.log_id});
        if(!current()||selected!==report)return;
        if(!recheckMatches(result,log.log_id,selected.snapshot_sha256))throw Error('Source recheck response could not be verified.');
        const message={
          projection_matches:'Retained structural projection matches the original snapshot.',
          projection_changed:'Retained structural projection changed. The saved snapshot remains unchanged.',
          source_unavailable:'Original source is unavailable or outside the retained bounded scope. The saved snapshot remains unchanged.'
        }[result.status];
        status.textContent=message+' Checked '+result.checked_at+'. This does not verify business outcomes or capture completeness.';
      }catch(error){if(current())status.textContent=String(error.message||error);}
      finally{busy=false;update();}
    });
    section.append(load,check,details,status);
    parent.appendChild(section);update();
  }

  window.owgObservedExecutionLog=Object.freeze({render,renderSource});
  window.__owgObservedExecutionLogModel={matchingPreview,sourceManifestMatches,recheckMatches};
})();
