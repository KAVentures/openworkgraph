/* Explicit local process catalog. Same reviewed knowledge store, immutable revisions. */
(() => {
  'use strict';
  let record=null, model=null, reviewed=false, busy=false, after='';
  let shadowGeneration=0;
  let importGeneration=0,importDraft=null;
  let ruleCheckGeneration=0,sourceRuleGeneration=0;
  let originGeneration=0;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);return n;};
  const button=(text,fn)=>{const n=el('button',text);n.type='button';n.className='secondary';n.addEventListener('click',()=>Promise.resolve().then(fn).catch(error=>status(error.message)));return n;};
  const status=text=>{const n=document.getElementById('owgCatalogStatus');if(n)n.textContent=String(text);};
  const copy=value=>JSON.parse(JSON.stringify(value));
  function clearShadowReview(){shadowGeneration++;document.getElementById('owgCatalogShadowResult')?.replaceChildren();}
  function clearRuleChecks(){ruleCheckGeneration++;document.getElementById('owgCatalogRuleCheckResult')?.replaceChildren();}
  function clearSourceRuleChecks(){sourceRuleGeneration++;document.getElementById('owgCatalogSourceRuleResult')?.replaceChildren();}
  function clearOrigins(){originGeneration++;document.getElementById('owgCatalogOriginResult')?.replaceChildren();}
  function changed(invalidateTables=true){if(invalidateTables&&model?.decision_tables?.length){for(const t of model.decision_tables)t.user_confirmed=false;document.querySelectorAll("input[data-table-confirmation]").forEach(n=>{n.checked=false;});}clearOrigins();clearSourceRuleChecks();reviewed=false;const check=document.getElementById('owgCatalogReviewed');if(check)check.checked=false;clearShadowReview();clearRuleChecks();}
  function field(parent,title,value,apply,options){
    const label=el('label',title+' '),input=el(options?'select':'input');
    if(options)options.forEach(value=>{const option=el('option',value);option.value=value;input.appendChild(option);});
    input.value=value;input.setAttribute('aria-label',title);
    input.addEventListener(options?'change':'input',()=>{apply(input.value);changed();});
    label.appendChild(input);parent.appendChild(label);return input;
  }
  async function request(path,body){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(String(data.detail||'Local process request failed'));}
    return response.json();
  }
  async function locked(fn){
    if(busy)throw new Error('A process request is already in progress.');
    busy=true;
    try{return await fn();}finally{busy=false;}
  }
  function linearModel(draft){
    const activities=draft.to_be.proposed_path.map((label,i)=>({id:'step_'+(i+1),label,kind:'activity',implementation:'unmapped'}));
    return {format:'openworkgraph.process-model.v1',goal:draft.business_goal_declared_by_user,owner_label:'',
      baseline_path:copy(draft.as_is.path),baseline_source:'selected_csv_variant',supporting_case_count:draft.as_is.supporting_case_count,
      activities,transitions:activities.slice(1).map((activity,i)=>({source:activities[i].id,target:activity.id,rule_id:null})),
      business_rules:[],manual_review_conditions:(draft.to_be.decision_review_ledger?.entries||[]).map(condition=>({
        activity_id:activities[condition.step_index]?.id,kind:condition.kind,condition_label:condition.condition_label,
        response:condition.response,source:condition.source,evaluated_by_owg:condition.evaluated_by_owg,
        automatic_branch_authorized:condition.automatic_branch_authorized,executable:condition.executable
      })),entry_activity_id:activities[0].id,terminal_activity_ids:[activities.at(-1).id],
      observed_path_is_policy:false,execution_authorized:false,outcomes_verified:false};
  }
  async function saveDesigner(){
    return locked(async()=>{
      if(!replaceDraft())throw new Error('Existing catalog draft kept.');
      const draft=window.owgProcessDesigner.reviewedDraft();
      const saved=await request('/v1/process-catalog',{title:draft.business_goal_declared_by_user.slice(0,240),
        expected_revision:0,user_confirmed:true,process_model:linearModel(draft)});
      record=saved;model=copy(saved.process_model);reviewed=false;renderEditor();
      status('Saved locally as revision '+saved.revision+'. Source CSV and native case IDs were not retained.');
      document.getElementById('owgProcessCatalog')?.scrollIntoView({block:'start'});
      return saved;
    });
  }
  function draftObserved(source){
    if(busy)throw new Error('Wait for the current process request.');
    if(!replaceDraft())return;
    if(!Array.isArray(source.path)||source.path.length<1||source.path.length>40||
      source.path.some(step=>typeof step!=='string'||!step.trim()||step.length>240))
      throw new Error('A bounded observed sequence of 1–40 labels is required.');
    const basis=['selected_observed_variant','selected_source_audit'].includes(source.basis)?source.basis:'user_declared';
    const activities=source.path.map((label,i)=>({id:'step_'+(i+1),
      label:/^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(label)?label:'Activity '+(i+1),kind:'activity',implementation:'unmapped'}));
    model={format:'openworkgraph.process-model.v1',goal:'',owner_label:'',baseline_path:copy(source.path),
      baseline_source:basis,baseline_coverage:source.partial?'partial_source_audit':'bounded_candidate_sequence',
      baseline_support_unit:basis==='selected_observed_variant'?'candidate_executions':'declared_cases',
      supporting_case_count:Number.isInteger(source.count)?Math.min(1000000,Math.max(0,source.count)):0,
      activities,transitions:activities.slice(1).map((activity,i)=>({source:activities[i].id,target:activity.id,rule_id:null})),
      business_rules:[],entry_activity_id:activities[0].id,terminal_activity_ids:[activities.at(-1).id],
      outcome_expectations:[],opportunity_scenario:null,observed_path_is_policy:false,execution_authorized:false,outcomes_verified:false};
    record={workflow_id:null,revision:0,title:'Observed workflow proposal',process_model:copy(model),
      evidence_refs:(source.evidenceRefs||[]).filter(ref=>typeof ref==='string'&&/^execution:[A-Za-z0-9_-]+$/.test(ref)).slice(0,100)};
    reviewed=false;renderEditor();
    status('Unsaved proposal from a bounded observation. Add a business goal and review every proposed label. Unsupported labels use Activity placeholders; the AS-IS baseline keeps the supplied sequence.');
    document.querySelector('[role=tab][data-tab=evidence]')?.click();
    document.getElementById('owgProcessCatalog')?.scrollIntoView({block:'start'});
  }
  async function inventory(next=false){
    if(!next)after='';
    const page=await request('/v1/process-catalog?'+new URLSearchParams({after,limit:'20'}));
    const host=document.getElementById('owgCatalogList');host.replaceChildren();
    page.processes.forEach(item=>{
      const row=el('p');row.appendChild(button(item.title+' · r'+item.revision,()=>load(item.workflow_id)));
      row.appendChild(el('span',' · '+item.activity_count+' activities · '+item.unconfirmed_rule_count+' unconfirmed rules'));
      host.appendChild(row);
    });
    if(!page.processes.length)host.appendChild(el('p',page.has_more?'No process designs on this knowledge page. Continue to the next page.':'No saved process designs on this page.'));
    if(page.has_more)host.appendChild(button('Next designs',()=>{after=page.next_after;return inventory(true);}));
    status('Saved process inventory loaded from local reviewed knowledge.');
  }
  function replaceDraft(){return !model||window.confirm('Discard unsaved catalog edits and load this revision?');}
  async function load(id,revision,expectedHash){
    return locked(async()=>{
      if(!replaceDraft())return;
      const params=new URLSearchParams({workflow_id:id});if(revision)params.set('revision',String(revision));
      const result=await request('/v1/process-catalog/revision?'+params);
      if(expectedHash&&result.content_sha256!==expectedHash)throw new Error('Linked process revision no longer matches its saved reference.');
      record=result;model=copy(result.process_model);reviewed=false;renderEditor();
      status('Opened immutable revision '+record.revision+'. Saving requires review and the same latest revision.');
    });
  }
  async function save(fork=false){
    if(!model||!reviewed)throw new Error('Review this exact edited graph before saving.');
    return locked(async()=>{
      const submitted=copy(model),title=record.title;
      const result=await request('/v1/process-catalog',{...(fork?{}:{workflow_id:record.workflow_id}),
        title,expected_revision:fork?0:record.revision,user_confirmed:true,process_model:submitted,evidence_refs:record.evidence_refs||[]});
      // Never replace edits made while the server was saving an older snapshot.
      if(JSON.stringify(model)!==JSON.stringify(submitted)||record.title!==title){
        record={...result,title:record.title};changed();renderEditor();status('Snapshot saved; newer local edits remain unsaved.');return;
      }
      record=result;model=copy(result.process_model);reviewed=false;renderEditor();
      status('Saved revision '+record.revision+'. Earlier revisions remain available.');
    });
  }
  async function history(before){
    if(!record)return;
    return locked(async()=>{
      const workflowId=record.workflow_id;
      const params=new URLSearchParams({workflow_id:workflowId,limit:'10'});if(before)params.set('before_revision',String(before));
      const result=await request('/v1/process-catalog/history?'+params),host=document.getElementById('owgCatalogHistory');
      host.replaceChildren(el('h4','Revision history'));
      result.workflows.forEach(item=>host.appendChild(button('Open revision '+item.revision+' · '+item.saved_at,()=>load(workflowId,item.revision))));
      if(result.has_more)host.appendChild(button('Older revisions',()=>history(result.next_before_revision)));
    });
  }
  async function download(format){
    if(!record)return;
    const exportedRecord={workflow_id:record.workflow_id,revision:record.revision};
    await window.__owgAuthReady;
    const response=await fetch((format==='agent-project'?'/v1/process-catalog/agent-project?':'/v1/process-catalog/export?')+new URLSearchParams({workflow_id:exportedRecord.workflow_id,revision:String(exportedRecord.revision),format}),{cache:'no-store'});
    if(!response.ok)throw new Error('Could not export saved revision.');
    const url=URL.createObjectURL(await response.blob());
    const anchor=el('a');anchor.href=url;anchor.download='owg-process-r'+exportedRecord.revision+'.'+({pdd:'txt',json:'json',bpmn:'bpmn','agent-project':'zip'}[format]);
    document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);
    status('Exported saved revision '+exportedRecord.revision+'. Unsaved editor changes are excluded.');
  }
  function graph(){
    if(window.owgProcessGraph) return window.owgProcessGraph.draw(model, activity=>{
      const index=model.activities.findIndex(a=>a.id===activity.id);
      document.getElementById('owgCatalogActivity'+index)?.focus();
    });
    const ns='http://www.w3.org/2000/svg';
    const svg=(tag,attrs)=>{const node=document.createElementNS(ns,tag);Object.entries(attrs||{}).forEach(([k,v])=>node.setAttribute(k,String(v)));return node;};
    const wrap=el('div');wrap.style.overflow='auto';
    const drawing=svg('svg',{width:780,height:Math.max(120,Math.ceil(model.activities.length/4)*110),role:'group','aria-label':'Proposed process graph. Arrows represent designed transitions.'});
    const positions=new Map(model.activities.map((a,i)=>[a.id,{x:10+(i%4)*195,y:20+Math.floor(i/4)*110}]));
    model.transitions.forEach(edge=>{
      const from=positions.get(edge.source),to=positions.get(edge.target);if(!from||!to)return;
      const line=svg('path',{d:'M '+(from.x+85)+' '+(from.y+50)+' L '+(to.x+85)+' '+to.y,stroke:'#557e70',fill:'none'});
      const title=svg('title');title.textContent=edge.source+' → '+edge.target+(edge.rule_id?' · '+edge.rule_id:'');line.appendChild(title);drawing.appendChild(line);
      const tip=svg('path',{d:'M '+(to.x+80)+' '+(to.y-6)+' L '+(to.x+85)+' '+to.y+' L '+(to.x+90)+' '+(to.y-6),stroke:'#557e70',fill:'none'});drawing.appendChild(tip);
    });
    model.activities.forEach((activity,i)=>{
      const p=positions.get(activity.id),group=svg('g',{role:'button',tabindex:0,'aria-label':'Edit activity '+activity.id});
      const rect=svg('rect',{x:p.x,y:p.y,width:175,height:50,rx:['decision','routing','parallel_split','parallel_join'].includes(activity.kind)?22:5,fill:'#f1f7f4',stroke:'#557e70'});
      const text=svg('text',{x:p.x+8,y:p.y+20,'font-size':12});text.textContent=(activity.kind.startsWith('parallel_')?'+ ':'')+activity.label.slice(0,22);
      const sub=svg('text',{x:p.x+8,y:p.y+38,'font-size':10});sub.textContent=activity.responsibility_label?
        ('Responsibility: '+activity.responsibility_label).slice(0,27):activity.id+' · '+activity.kind;
      const title=svg('title');title.textContent=activity.label+(activity.responsibility_label?' · Declared responsibility: '+activity.responsibility_label:'');
      const focus=()=>document.getElementById('owgCatalogActivity'+i)?.focus();
      group.addEventListener('click',focus);group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();focus();}});
      group.append(rect,text,sub,title);drawing.appendChild(group);
    });
    wrap.appendChild(drawing);return wrap;
  }
  function renderEditor(){
    clearOrigins();
    clearSourceRuleChecks();
    clearRuleChecks();
    clearShadowReview();
    const host=document.getElementById('owgCatalogEditor');if(!host||!model)return;
    host.replaceChildren(el('h3',record.revision?'Review process · revision '+record.revision:'Review process · unsaved observed proposal'));
    host.appendChild(el('p','Edits create a new revision. These are proposed designs and declared rules; they do not authorize an agent or establish company policy.'));
    if((record.evidence_refs||[]).some(ref=>ref.startsWith('urn:openworkgraph:local-log-discovery:'))){
      const origin=el('div');origin.id='owgCatalogOriginResult';origin.style.overflowWrap='anywhere';
      host.appendChild(el('p','Local discovery snapshot reference retained. Source details stay in the imported-log store. Inspect the saved revision; unsaved graph edits have no verified relationship to it.'));
      if(record.workflow_id&&record.revision){
        const inspect=button('Inspect discovery source snapshots',async()=>{
          const attempt=++originGeneration;
          inspect.disabled=true;
          const progress=el('p','Checking retained source fingerprints… Large sources can take tens of seconds.');progress.setAttribute('role','status');origin.replaceChildren(progress);
          try{
          const result=await request('/v1/event-log-discovery/process-origin?'+new URLSearchParams({workflow_id:record.workflow_id,revision:String(record.revision)}));
          if(attempt!==originGeneration)return;
          origin.replaceChildren(el('h4','Saved revision source snapshots'));
          result.origins.forEach(item=>{
            const labels={current:'Source data unchanged',source_advanced:'Source data changed since this snapshot',unavailable:'Source snapshot unavailable',check_failed:'Source snapshot could not be checked',reference_mismatch:'Snapshot metadata no longer matches its reference',not_checked_work_budget:'Source not checked: fingerprint work budget exceeded'};
            origin.appendChild(el('p',labels[item.status]||'Source status unknown'));
            if(item.source_content_sha256){
              origin.appendChild(el('p','Recorded '+item.recorded_at+' · source SHA-256 '+item.source_content_sha256));
              if(item.source_fingerprint_checked_at)origin.appendChild(el('p','Source data checked at '+item.source_fingerprint_checked_at+'. Inspect again after changes.'));
              origin.appendChild(el('p','At recording: '+item.quality.cases_used+' of '+item.quality.cases_total+' cohort cases used; '+item.quality.checked_cases+' checked by draft replay. Coverage describes the supplied log.'));
              origin.appendChild(el('p',item.reviewed_graph_matches_discovery?'Saved graph matches the recorded generated graph. Business rules remain unverified.':'User-selected reference to an edited graph; derivation is not independently verified.'));
              const selection=el('pre',JSON.stringify(item.selection,null,2));selection.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';origin.appendChild(selection);
            }
          });
          }catch(error){if(attempt===originGeneration){origin.replaceChildren(el('p','Source fingerprints could not be checked.'));throw error;}}
          finally{inspect.disabled=false;}
        });host.appendChild(inspect);
      }else host.appendChild(el('p','The source snapshot is recorded; this process remains an unsaved draft.'));
      host.appendChild(origin);
    }
    field(host,'Process title',record.title,value=>record.title=value);
    field(host,'Business goal',model.goal,value=>model.goal=value);
    field(host,'Process owner',model.owner_label,value=>model.owner_label=value);
    host.appendChild(el('h4',model.baseline_coverage==='user_declared_design'?'Imported declared design · no observed baseline':'AS-IS observed baseline'));
    host.appendChild(el('p',model.baseline_path.join(' → ')));
    const sourceName={selected_csv_variant:'Selected CSV variant',selected_observed_variant:'Selected desktop candidate',selected_source_audit:'Selected source audit',user_declared:'User-declared baseline'}[model.baseline_source];
    const coverageName={complete_supplied_path:'Complete supplied path',bounded_candidate_sequence:'Bounded observed sequence',partial_source_audit:'Partial source audit',user_declared_design:'User-declared design without observed executions'}[model.baseline_coverage||'complete_supplied_path'];
    host.appendChild(el('p','Baseline: '+sourceName+' · '+coverageName+' · '+model.supporting_case_count+' '+
      (model.baseline_support_unit==='candidate_executions'?'candidate executions':'supplied cases')+'. Task boundaries and business case identity are unverified.'));
    host.appendChild(el('h4','TO-BE proposed graph'));host.appendChild(graph());
    host.appendChild(el('p','Responsibility labels describe who should handle a step. They are user declarations and grant no access, review or execution permissions.'));
    if(model.activities.some(node=>['parallel_split','parallel_join'].includes(node.kind)))host.appendChild(el('p',
      'Parallel splits run every branch and joins require every branch. This bounded design supports disjoint unconditional branch chains and complete nested paired blocks; conditional parallel regions are unsupported. SDK projects validate complete token-firing schedules and execute one customer-authorized activity at a time.'));
    host.appendChild(button('Refresh graph preview',()=>renderEditor()));
    const mutate=fn=>{fn();changed();renderEditor();};
    model.activities.forEach((activity,index)=>{
      const row=el('fieldset');row.appendChild(el('legend',activity.id));
      const label=field(row,'Activity '+activity.id,activity.label,value=>activity.label=value);label.id='owgCatalogActivity'+index;
      field(row,'Kind '+activity.id,activity.kind,value=>activity.kind=value,['activity','decision','routing','start','end','parallel_split','parallel_join']);
      field(row,'Implementation '+activity.id,activity.implementation,value=>activity.implementation=value,['unmapped','api','deterministic','ai_agent','computer_use','human']);
      const responsibility=field(row,'Responsibility '+activity.id,activity.responsibility_label||'',value=>activity.responsibility_label=value);
      responsibility.maxLength=120;
      row.appendChild(button('Remove '+activity.id,()=>mutate(()=>{
        model.activities.splice(index,1);model.transitions=model.transitions.filter(e=>e.source!==activity.id&&e.target!==activity.id);
        model.terminal_activity_ids=model.terminal_activity_ids.filter(id=>id!==activity.id);
        model.manual_review_conditions=(model.manual_review_conditions||[]).filter(c=>c.activity_id!==activity.id);
      })));host.appendChild(row);
    });
    host.appendChild(button('Add activity',()=>mutate(()=>{
      if(model.activities.length>=40)throw new Error('Maximum 40 activities.');
      let i=1;while(model.activities.some(a=>a.id==='step_'+i))i++;
      model.activities.push({id:'step_'+i,label:'New activity',kind:'activity',implementation:'unmapped'});
    })));
    field(host,'Entry activity ID',model.entry_activity_id,value=>model.entry_activity_id=value);
    field(host,'Terminal activity IDs (comma separated)',model.terminal_activity_ids.join(','),value=>model.terminal_activity_ids=value.split(',').map(s=>s.trim()).filter(Boolean));
    host.appendChild(el('h4','Transitions and decision branches'));
    model.transitions.forEach((edge,index)=>{
      const row=el('fieldset');row.appendChild(el('legend','Transition '+(index+1)));
      field(row,'From '+(index+1),edge.source,value=>edge.source=value,model.activities.map(a=>a.id));
      field(row,'To '+(index+1),edge.target,value=>edge.target=value,model.activities.map(a=>a.id));
      field(row,'Rule '+(index+1),edge.rule_id||'',value=>edge.rule_id=value||null,['',...model.business_rules.map(r=>r.id)]);
      row.appendChild(button('Remove transition '+(index+1),()=>mutate(()=>model.transitions.splice(index,1))));host.appendChild(row);
    });
    host.appendChild(button('Add transition',()=>mutate(()=>{
      if(model.transitions.length>=100)throw new Error('Maximum 100 transitions.');
      model.transitions.push({source:model.activities[0]?.id||'missing',target:model.activities.at(-1)?.id||'missing',rule_id:null});
    })));
    host.appendChild(el('h4','Business rules and exceptions'));
    model.business_rules.forEach((rule,index)=>{
      const row=el('fieldset');row.appendChild(el('legend',rule.id));
      field(row,'Rule text '+rule.id,rule.text,value=>{rule.text=value;rule.user_confirmed=false;check.checked=false;});
      const label=el('label',' User-confirmed rule '),check=el('input');check.type='checkbox';check.checked=rule.user_confirmed;
      check.addEventListener('change',()=>{rule.user_confirmed=check.checked;changed();});label.appendChild(check);row.appendChild(label);
      window.owgProcessRuleEditor?.render(rule,row,()=>{check.checked=false;changed();});
      row.appendChild(button('Remove rule '+rule.id,()=>mutate(()=>{
        model.business_rules.splice(index,1);model.transitions.forEach(e=>{if(e.rule_id===rule.id)e.rule_id=null;});
      })));host.appendChild(row);
    });
    host.appendChild(button('Add rule',()=>mutate(()=>{
      if(model.business_rules.length>=40)throw new Error('Maximum 40 rules.');
      let i=1;while(model.business_rules.some(r=>r.id==='rule_'+i))i++;
      model.business_rules.push({id:'rule_'+i,text:'Describe the condition or exception',user_confirmed:false});
    })));
    window.owgProcessDecisionEditor?.render(model,host,changed);
    host.appendChild(el('h4','Unverified manual review conditions'));
    host.appendChild(el('p','These declared triggers are not evaluated. They preserve the decision and exception ledger using activity IDs; they do not authorize an automatic branch.'));
    model.manual_review_conditions=model.manual_review_conditions||[];
    model.manual_review_conditions.forEach((condition,index)=>{
      const row=el('fieldset');row.appendChild(el('legend','Manual review '+(index+1)));
      field(row,'Review activity '+(index+1),condition.activity_id,value=>condition.activity_id=value,model.activities.map(a=>a.id));
      field(row,'Condition kind '+(index+1),condition.kind,value=>condition.kind=value,['decision_point','exception_condition']);
      field(row,'Condition label '+(index+1),condition.condition_label,value=>condition.condition_label=value);
      field(row,'Review response '+(index+1),condition.response,value=>condition.response=value,['pause_for_review','request_source_recheck','escalate_to_owner']);
      row.appendChild(button('Remove manual review '+(index+1),()=>mutate(()=>model.manual_review_conditions.splice(index,1))));host.appendChild(row);
    });
    host.appendChild(button('Add manual review condition',()=>mutate(()=>{
      if(model.manual_review_conditions.length>=12)throw new Error('Maximum 12 manual review conditions.');
      model.manual_review_conditions.push({activity_id:model.activities[0]?.id||'missing',kind:'exception_condition',
        condition_label:'Describe condition',response:'pause_for_review',source:'user_declared_unverified',
        evaluated_by_owg:false,automatic_branch_authorized:false,executable:false});
    })));
    host.appendChild(el('h4','Independent source-state expectations'));
    host.appendChild(el('p','Declare which provider operation should be checked for a terminal activity. Confirmation describes the expected state; it does not grant execution rights or prove an agent caused it.'));
    model.outcome_expectations=model.outcome_expectations||[];
    const operations=['github_pr_merged','stripe_invoice_paid','stripe_refund_processor_succeeded','jira_issue_resolved'];
    model.outcome_expectations.forEach((expectation,index)=>{
      const row=el('fieldset');row.appendChild(el('legend','Outcome expectation '+(index+1)));
      const clear=()=>{expectation.user_confirmed=false;confirmation.checked=false;};
      field(row,'Outcome terminal '+(index+1),expectation.terminal_activity_id,value=>{expectation.terminal_activity_id=value;clear();},model.terminal_activity_ids);
      field(row,'Expected source operation '+(index+1),expectation.operation,value=>{expectation.operation=value;clear();},operations);
      const label=el('label',' I confirm this declared source-state expectation '),confirmation=el('input');confirmation.type='checkbox';confirmation.checked=expectation.user_confirmed;
      confirmation.addEventListener('change',()=>{expectation.user_confirmed=confirmation.checked;changed();});label.appendChild(confirmation);row.appendChild(label);
      row.appendChild(button('Remove outcome expectation '+(index+1),()=>mutate(()=>model.outcome_expectations.splice(index,1))));host.appendChild(row);
    });
    host.appendChild(button('Add source-state expectation',()=>mutate(()=>{
      if(model.outcome_expectations.length>=40)throw new Error('Maximum 40 source-state expectations.');
      model.outcome_expectations.push({terminal_activity_id:model.terminal_activity_ids[0]||'missing',operation:'jira_issue_resolved',user_confirmed:false});
    })));
    const savedExpectations=(record.process_model.outcome_expectations||[]).filter(e=>e.user_confirmed);
    if(savedExpectations.length){
      let resource='';field(host,'Exact observed OWG record reference','',value=>resource=value.trim());
      savedExpectations.forEach(expectation=>host.appendChild(button('Check saved '+expectation.operation+' for '+expectation.terminal_activity_id,()=>locked(async()=>{
        if(!/^owg:r:[0-9a-f]{32}$/.test(resource))throw new Error('Enter an existing exact opaque owg:r: record reference.');
        if(!window.confirm('Read source status for the exact selected record '+resource+' using your separately enabled source connection?'))return;
        const output=await request('/v1/process-catalog/check-outcome',{workflow_id:record.workflow_id,revision:record.revision,
          terminal_activity_id:expectation.terminal_activity_id,expected_operation:expectation.operation,
          resource_ref:resource,source_reads_confirmed:true});
        status('Saved revision '+output.reference.revision+': '+(output.receipt.operation_verified?
          'source reports the expected provider operation.':'source operation not verified ('+output.receipt.reason+').')+
          (output.receipt.verified_at?' Checked at '+output.receipt.verified_at+'.':'')+
          ' Business objective and agent causality remain unverified. No result was persisted.');
      }))));
    }
    host.appendChild(el('h4','Automation opportunity · explicit assumptions'));
    host.appendChild(el('p','Enter a business scenario for comparison with other saved processes. Event gaps do not establish employee effort or savings. Sensitivity is a chosen range, not statistical confidence.'));
    if(model.opportunity_scenario){
      const scenario=model.opportunity_scenario;
      field(host,'Scenario currency',scenario.currency,value=>scenario.currency=value,['USD','EUR','GBP','SEK']);
      const names={monthly_cases:'Assumed monthly cases',minutes_saved_per_case:'Assumed minutes saved per case',
        eligible_fraction_percent:'Assumed eligible cases percent',loaded_hourly_cost:'Assumed loaded hourly cost',
        recurring_monthly_cost:'Assumed recurring monthly cost',one_time_implementation_cost:'Assumed implementation cost',
        sensitivity_percent:'Chosen sensitivity percent'};
      Object.entries(names).forEach(([key,title])=>field(host,title,scenario[key]??'',value=>scenario[key]=value.trim()===''?null:Number(value)));
      host.appendChild(button('Remove opportunity scenario',()=>mutate(()=>model.opportunity_scenario=null)));
    }else host.appendChild(button('Add explicit opportunity scenario',()=>mutate(()=>{
      model.opportunity_scenario={currency:'USD',monthly_cases:null,minutes_saved_per_case:null,
        eligible_fraction_percent:null,loaded_hourly_cost:null,recurring_monthly_cost:null,
        one_time_implementation_cost:null,sensitivity_percent:25};
    })));
    const reviewLabel=el('label',' I reviewed this exact process graph and declared rules. Save locally as reviewed knowledge. ');
    const check=el('input');check.id='owgCatalogReviewed';check.type='checkbox';check.checked=reviewed;
    check.addEventListener('change',()=>reviewed=check.checked);reviewLabel.appendChild(check);host.appendChild(reviewLabel);
    const actions=el('div');actions.className='owg-catalog-actions';
    actions.append(button('Save new revision',()=>save()),button('Save as a new process',()=>save(true)));
    if(record.revision){
    actions.appendChild(button('Load revision history',()=>history()));
    actions.appendChild(button('Add saved revision to automation pipeline',()=>locked(async()=>{
      const selected={workflow_id:record.workflow_id,revision:record.revision,content_sha256:record.content_sha256};
      const idea=await request('/v1/automation-pipeline/from-process',selected);
      window.dispatchEvent(new CustomEvent('owg-pipeline-open',{detail:{idea_id:idea.idea_id}}));
      document.getElementById('owgPipelineCard')?.scrollIntoView({block:'start'});
      status('Added saved revision '+selected.revision+' to the automation pipeline. Unsaved edits are excluded.');
    })));

    const project=button('Download OpenAI Agents project',()=>download('agent-project'));
    if(record.process_model?.decision_tables?.length)actions.appendChild(el('p','Decision-table projects need separately reviewed current-source '+(record.process_model.decision_tables.some(t=>t.hit_policy==='FIRST')?'UNIQUE/FIRST bindings, declared FIRST row precedence':'UNIQUE bindings')+' and UTC effective dates before execution.'));
    actions.appendChild(project);
    ['json','bpmn','pdd'].forEach(format=>actions.appendChild(button('Export saved '+format.toUpperCase(),()=>download(format))));
    actions.appendChild(button('Add screenshots to saved PDD',()=>{
      window.dispatchEvent(new CustomEvent('owg-process-pdd-source',{detail:{workflow_id:record.workflow_id,revision:record.revision,content_sha256:record.content_sha256}}));
    }));
    actions.appendChild(button('Compare next CSV with this saved revision',()=>{
      window.owgProcessMining?.setReference({workflow_id:record.workflow_id,revision:record.revision});
      document.getElementById('owgProcessCSV')?.scrollIntoView({block:'start'});
      status('Next CSV analysis will compare against saved revision '+record.revision+'. Unsaved edits are excluded.');
    }));
    actions.appendChild(button('Delete all revisions',()=>locked(async()=>{
      if(!window.confirm('Delete this saved process and ALL revisions from local reviewed knowledge?'))return;
      await request('/v1/process-catalog/forget',{workflow_id:record.workflow_id,user_confirmed:true});
      record=null;model=null;reviewed=false;host.replaceChildren();status('Process revisions deleted locally. Downloaded exports are separate copies.');
    })));
    }
    host.appendChild(actions);
    if(record.revision){renderRuleChecks(host);renderSourceRuleChecks(host);renderShadowReview(host);}
    const historyHost=el('div');historyHost.id='owgCatalogHistory';host.appendChild(historyHost);
  }
  function renderRuleChecks(host){
    const section=el('section');section.append(el('h4','Check declared conditions against case facts'),
      el('p','Choose an authorized JSON file containing cases with opaque labels (case_1 etc.) and facts. Confirmed structured predicates are checked against those supplied values only; missing facts stay unknown. Optional activity_ids describe a proposed route, not proof of observed execution. No data is sent to an AI or retained. Unsaved model edits are excluded.'));
    section.appendChild(el('p','For decision tables include an explicit as_of_date (YYYY-MM-DD) alongside cases in the JSON. Checks cover supplied cases only.'));
    const label=el('label','Choose authorized case-facts JSON '),file=el('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Choose authorized case-facts JSON');label.appendChild(file);
    const consentLabel=el('label',' I authorize local analysis of the case facts in this selected file. '),consent=el('input');consent.type='checkbox';consentLabel.appendChild(consent);
    const run=button('Check facts against saved rules',()=>locked(async()=>{
      if(!consent.checked)throw new Error('Confirm authorization for the selected case facts.');
      const source=file.files?.[0];if(!source||source.size>128*1024)throw new Error('Choose a case-facts JSON file of at most 128 KiB.');
      clearRuleChecks();const generation=ruleCheckGeneration,selected=record;
      await window.__owgAuthReady;
      const response=await fetch('/v1/process-catalog/check-rules?'+new URLSearchParams({workflow_id:selected.workflow_id,revision:selected.revision}),
        {method:'POST',headers:{'Content-Type':'application/json','X-OWG-Case-Facts-Authorized':'true'},body:source,cache:'no-store'});
      const result=await response.json().catch(()=>({}));if(generation!==ruleCheckGeneration||record!==selected)return;
      if(!response.ok)throw new Error(String(result.detail||'Case rule check unavailable'));
      if(result.format!=='openworkgraph.declared-rule-case-check.v1')throw new Error('Unexpected rule-check response');
      const counts=result.rule_result_counts||{};
      output.append(el('h4',result.case_count+' supplied case-fact reports'),
        el('p','Exact revision '+result.process_reference.revision+' · '+result.process_reference.content_sha256+'. '+
          (result.references_latest_revision_at_check?'This was the latest saved revision at check time.':'A newer revision '+result.latest_revision_at_check+' exists.')),
        el('p','Declared predicate results: '+Number(counts.matched||0)+' matched · '+Number(counts.not_matched||0)+' not matched · '+Number(counts.unknown||0)+' unknown. '+
          Number(counts.not_evaluated_unconfirmed||0)+' unconfirmed and '+Number(counts.not_evaluated_unstructured||0)+' unstructured definitions were not evaluated.'),
        el('p','Facts are caller-supplied and unverified. These are descriptive comparisons, not policy compliance, observed-case conformance, business outcomes or permission to execute. A structural match alone does not check business facts.'));
      const reasons={matched:'Matched declared comparison',not_matched:'Did not match declared comparison',unknown:'Unknown: missing or incompatible fact',
        not_on_proposed_route:'Not on proposed route',not_evaluated_unconfirmed:'Definition not confirmed',not_evaluated_unstructured:'No structured predicate',not_evaluated_invalid_route:'Proposed route outside graph'};
      for(const item of result.cases){
        const group=el('details');group.append(el('summary',item.case_id+' · '+(item.proposed_route_supplied?(item.proposed_route_structure_matches?'proposed route fits graph':'proposed route differs from graph'):'no route supplied')));
        if(item.manual_review_condition_count_on_proposed_route)group.appendChild(el('p',item.manual_review_condition_count_on_proposed_route+' manual review conditions on this route remain unevaluated. Predicate results do not remove those guards.'));
        const list=el('ul');for(const rule of item.rules){
          const row=el('li'),detail=el('details');detail.appendChild(el('summary',rule.rule_id+' · '+reasons[rule.status]));
          const clauses=el('ol');for(const condition of rule.conditions)clauses.appendChild(el('li',reasons[condition.status]+' · '+condition.reason.replaceAll('_',' ')));
          detail.appendChild(clauses);row.appendChild(detail);list.appendChild(row);
        }
        for(const table of item.decision_tables||[])list.appendChild(el('li','Decision table '+table.table_id+' · '+table.status.replaceAll('_',' ')+(table.selected_target_activity_id?' → '+table.selected_target_activity_id:'')+(table.proposed_branch_agrees===false?' · proposed branch disagrees':'')));
        group.appendChild(list);output.appendChild(group);
      }
      output.appendChild(button('Download minimized rule-check report',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)+'\n'],{type:'application/json'}));
        const anchor=el('a');anchor.href=url;anchor.download='owg-declared-rule-check-r'+selected.revision+'.json';
        document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);
      }));
      status('Declared rule check complete for saved revision '+selected.revision+'. No facts or report were retained and no model was changed.');
    }));run.disabled=true;
    const output=el('div');output.id='owgCatalogRuleCheckResult';output.setAttribute('role','status');
    file.addEventListener('change',()=>{clearRuleChecks();consent.checked=false;run.disabled=true;});
    consent.addEventListener('change',()=>{clearRuleChecks();run.disabled=!consent.checked||!file.files?.length;});
    section.append(label,consentLabel,run,output);host.appendChild(section);
  }
  function renderSourceRuleChecks(host){
    const section=el('section');section.append(el('h4','Check rules against a current source record'),
      el('p','Explicitly refresh one already observed record through its existing opted-in read-only adapter. Available fact paths are /source/provider, /source/record_kind and /source/state. Only confirmed structured conditions in the saved revision are evaluated; unsaved edits are excluded. Other paths or unavailable status remain unknown. Current status does not establish historical case facts, policy approval, business outcomes or permission to execute.'));
    const types=[['stripe_invoice','Stripe invoice'],['stripe_refund','Stripe refund'],['jira_issue','Jira issue'],['github_pull_request','GitHub pull request']];
    const typeLabel=el('label','Source record type '),type=el('select');type.setAttribute('aria-label','Source rule record type');type.style.maxWidth='100%';
    types.forEach(([value,text])=>{const option=el('option',text);option.value=value;type.appendChild(option);});type.value='stripe_invoice';typeLabel.appendChild(type);
    const refLabel=el('label','Observed record reference '),ref=el('input');ref.type='text';ref.maxLength=38;ref.placeholder='owg:r:…';ref.setAttribute('aria-label','Source rule observed record reference');refLabel.appendChild(ref);
    const choicesLabel=el('label','Observed source record choices '),choices=el('select');choices.setAttribute('aria-label','Observed source record choices');choices.style.maxWidth='100%';
    const placeholder=()=>{const option=el('option','Choose a loaded observed record');option.value='';choices.replaceChildren(option);};placeholder();choicesLabel.appendChild(choices);
    let observed=[];
    const notice=el('p','Record choices are a bounded local navigation sample; canonical identity and source permissions are checked again during refresh. Other already observed references may be entered manually.');
    const consentLabel=el('label',' I authorize refreshing this selected source record to check saved rules. '),consent=el('input');consent.type='checkbox';consentLabel.appendChild(consent);
    const output=el('div');output.id='owgCatalogSourceRuleResult';output.setAttribute('role','status');
    const invalidate=()=>{clearSourceRuleChecks();consent.checked=false;run.disabled=true;};
    const run=button('Refresh selected record and check rules',()=>locked(async()=>{
      if(!consent.checked||!/^owg:r:[0-9a-f]{32}$/.test(ref.value))throw new Error('Select and authorize one normalized observed record reference.');
      clearSourceRuleChecks();const generation=sourceRuleGeneration,selected=record;
      const body={resource_ref:ref.value,source_type:type.value};
      await window.__owgAuthReady;
      const response=await fetch('/v1/process-catalog/check-current-source-rules?'+new URLSearchParams({workflow_id:selected.workflow_id,revision:selected.revision}),{
        method:'POST',headers:{'Content-Type':'application/json','X-OWG-Source-Rule-Read-Authorized':'true'},body:JSON.stringify(body),cache:'no-store'});
      const report=await response.json().catch(()=>({}));if(generation!==sourceRuleGeneration||record!==selected)return;
      if(!response.ok)throw new Error(String(report.detail||'Selected source rule check unavailable.'));
      if(report.format!=='openworkgraph.current-source-rule-check.v1')throw new Error('Unexpected source rule-check response.');
      const counts=report.rule_result_counts||{};
      output.append(el('h4','Current-source conditions · saved revision '+report.process_reference.revision),
        el('p',report.facts_source_authoritativeness_verified?'Provider current-state facts checked at '+report.source_state_verified_at+'.':'No fresh authenticated source state was available for this selection: '+report.source_status_reason.replaceAll('_',' ')+'.'),
        el('p',Number(counts.matched||0)+' matched · '+Number(counts.not_matched||0)+' not matched · '+Number(counts.unknown||0)+' unknown. '+
          Number(counts.not_evaluated_unconfirmed||0)+' unconfirmed and '+Number(counts.not_evaluated_unstructured||0)+' text-only rules were not evaluated.'),
        el('p',report.references_latest_revision_at_check?'This was the latest saved revision at check time.':report.latest_revision_at_check?'A newer revision '+report.latest_revision_at_check+' exists.':'The saved process is no longer available.'),
        el('p','The selected record-to-case association is your declaration. This check does not establish historical conformance, remove manual review conditions or resolve the generated agent source-fact implementation guard. No report, source values or model changes were retained.'));
      const list=el('ul');for(const rule of report.cases[0].rules){
        const row=el('li'),detail=el('details');detail.appendChild(el('summary',rule.rule_id+' · '+rule.status.replaceAll('_',' ')));
        const clauses=el('ol');for(const condition of rule.conditions)clauses.appendChild(el('li',condition.status.replaceAll('_',' ')+' · '+condition.reason.replaceAll('_',' ')));
        detail.appendChild(clauses);row.appendChild(detail);list.appendChild(row);
      }output.appendChild(list);
      output.appendChild(button('Download minimized current-source rule report',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json'}));
        const anchor=el('a');anchor.href=url;anchor.download='owg-current-source-rules-r'+selected.revision+'.json';document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);
      }));status('Current-source rule check complete. Results grant no execution permission.');
    }));run.disabled=true;
    const load=button('Load observed source record choices',async()=>{
      invalidate();const generation=sourceRuleGeneration,selected=record;
      const value=await request('/v1/workflow-evidence/families?min_runs=2&limit=20');
      if(generation!==sourceRuleGeneration||record!==selected)return;
      observed=(value.record_case_lanes?.lanes||[]).filter(item=>/^owg:r:[0-9a-f]{32}$/.test(item.resource_ref||'')&&types.some(([key])=>key===item.provider+'_'+item.resource_kind));
      placeholder();observed.forEach((item,index)=>{const option=el('option',item.provider+' '+item.resource_kind+' · '+item.resource_ref);option.value=String(index);choices.appendChild(option);});
      notice.textContent=observed.length+' supported records in this bounded local navigation sample. Choosing one does not read its source. Canonical proof and source permissions are checked again during refresh.';
    });
    choices.addEventListener('change',()=>{invalidate();const item=choices.value===''?null:observed[Number(choices.value)];if(item){ref.value=item.resource_ref;type.value=item.provider+'_'+item.resource_kind;}});
    ref.addEventListener('input',()=>{choices.value='';invalidate();});type.addEventListener('change',invalidate);
    consent.addEventListener('change',()=>{clearSourceRuleChecks();run.disabled=!consent.checked||!/^owg:r:[0-9a-f]{32}$/.test(ref.value);});
    section.append(typeLabel,refLabel,load,choicesLabel,notice,consentLabel,run,
      button('Clear current-source rule check',()=>{invalidate();ref.value='';choices.value='';}),output);host.appendChild(section);
  }
  function renderShadowReview(host){
    const section=el('section');section.appendChild(el('h4','Review a shadow evaluation'));
    section.appendChild(el('p','Inspect a minimized report from this saved revision’s downloaded agent project. It stays local and temporary. Customer expectations and reported counts remain unverified declarations; this inspection grants no execution permission. Unsaved graph edits are excluded.'));
    const label=el('label','Choose shadow evaluation report '),file=el('input');
    file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Choose shadow evaluation report');
    file.addEventListener('change',clearShadowReview);label.appendChild(file);section.appendChild(label);
    const output=el('div');output.id='owgCatalogShadowResult';output.setAttribute('role','status');
    section.appendChild(button('Inspect report against saved revision',()=>locked(async()=>{
      clearShadowReview();const generation=shadowGeneration,selected=record,source=file.files?.[0];
      if(!source||source.size>128*1024)throw new Error('Choose a minimized shadow report JSON file of at most 128 KiB.');
      let report;
      try{report=JSON.parse(await source.text());}catch{throw new Error('The selected file is not a valid shadow report JSON document.');}
      if(generation!==shadowGeneration||record!==selected)return;
      const result=await request('/v1/process-catalog/shadow-review',{workflow_id:selected.workflow_id,revision:selected.revision,report});
      if(generation!==shadowGeneration||record!==selected)return;
      output.appendChild(el('h4',result.report.matching_case_count+' of '+result.report.case_count+' cases match customer expectations'));
      output.appendChild(el('p','Exact saved specification matched · revision '+result.process_reference.revision+'. '+
        (result.references_latest_revision_at_inspection?'This was the latest saved revision at inspection.':
          'A newer revision '+result.latest_revision_at_inspection+' exists. Re-export and evaluate that design before using its results.')));
      output.appendChild(el('p',result.report.review_required?'Mismatches require human review against source evidence and business rules.':'Selected expectations agree. Coverage, business truth and live outcomes remain unverified.'));
      const reasons={matches_reviewed_expectation:'Matches reviewed expectation',invalid_or_stale_plan:'Invalid or stale plan',
        invalid_route:'Route outside saved graph',unresolved_review_bypassed:'Unresolved review bypassed',
        proposal_has_unresolved_inputs:'Proposal contains unresolved inputs',expected_clarification:'Clarification was expected',
        unexpected_route:'Different route than expected',clarification_without_question:'Clarification has no question',
        unknown_activity_in_clarification:'Clarification references an unknown activity',unexpected_clarification:'Unexpected clarification'};
      const list=el('ul');for(const row of result.report.cases)list.appendChild(el('li',row.case_id+' · '+row.category+' · '+reasons[row.reason]));
      output.appendChild(list);
      output.appendChild(el('p','This is a customer-supplied report. Its authenticity and expected business results were not independently verified. No case values are imported, no process revision is changed and no report is retained.'));
      status('Shadow report inspected against saved revision '+result.process_reference.revision+'. Review is temporary and clears when the editor or file changes.');
    })));
    section.appendChild(button('Clear shadow report review',()=>{file.value='';clearShadowReview();}));
    section.appendChild(output);host.appendChild(section);
  }
  async function backlog(currency){
    const result=await request('/v1/process-catalog/opportunities?'+new URLSearchParams({currency}));
    const host=document.getElementById('owgCatalogBacklog');host.replaceChildren(el('h3','Improvement backlog · '+currency));
    host.appendChild(el('p','Ranked by declared net monthly value. These are scenarios, not verified savings or implementation recommendations. Costs and quality risks need separate review.'));
    result.opportunities.forEach(item=>{
      const row=el('section'),p=item.projection;
      row.appendChild(button(item.title+' · r'+item.revision,()=>load(item.workflow_id)));
      row.appendChild(el('p','Assumed net monthly value '+p.net_monthly_value_before_other_costs+' '+currency+
        ' · sensitivity '+p.sensitivity_net_monthly_low+' to '+p.sensitivity_net_monthly_high+
        ' · simple payback '+(p.simple_payback_months==null?'not positive':p.simple_payback_months+' months')));
      row.appendChild(el('p',item.unconfirmed_rule_count+' unconfirmed rules · '+item.unmapped_activity_count+
        ' unmapped activities · '+(item.manual_review_condition_count||0)+' unevaluated review conditions · owner: '+(item.declared_owner||'undeclared')));host.appendChild(row);
    });
    if(!result.opportunities.length)host.appendChild(el('p','No saved scenarios in this currency. Add assumptions to a process and save a reviewed revision.'));
    host.appendChild(el('p',result.processes_without_scenarios_in_scanned_records+' processes have no scenario. '+
      (result.catalog_scan_truncated||result.ranked_results_truncated?'This ranked view is bounded; additional records are omitted.':'All local reviewed-knowledge pages were considered.')));
  }
  function installBPMNImport(parent){
    const section=el('details');section.id='owgBPMNImport';section.appendChild(el('summary','Import a BPMN design as an unreviewed draft'));
    section.appendChild(el('p','Select an authorized UTF-8 BPMN 2.0 file (128 KiB maximum). Preview supports tasks, plain start/end events, exclusive choices/merges and paired parallel gateways with disjoint unconditional branches and complete nested paired blocks. Conditional parallel regions, inclusive gateways, subprocesses, event triggers, collaborations and executable extensions are rejected. XML never executes. No process is saved until separate graph review and Save.'));
    const file=el('input');file.type='file';file.accept='.bpmn,.xml,application/xml,application/bpmn+xml';
    file.setAttribute('aria-label','Choose BPMN process design');
    const preview=button('Preview selected BPMN',()=>previewBPMN(file));preview.id='owgBPMNPreview';preview.disabled=true;
    file.addEventListener('change',()=>{importGeneration++;importDraft=null;preview.disabled=!file.files?.length;
      document.getElementById('owgBPMNImportResult').replaceChildren();});
    const output=el('div');output.id='owgBPMNImportResult';section.append(file,preview,output);parent.appendChild(section);
  }
  async function previewBPMN(input){
    const file=input.files?.[0];if(!file)return;
    if(file.size>128*1024)throw new Error('BPMN exceeds the 128 KiB limit.');
    const attempt=++importGeneration,control=document.getElementById('owgBPMNPreview');control.disabled=true;
    importDraft=null;const host=document.getElementById('owgBPMNImportResult');host.replaceChildren(el('p','Inspecting BPMN as data; nothing is saved…'));
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/process-catalog/import-bpmn',{
        method:'POST',headers:{'Content-Type':'application/bpmn+xml'},body:file,cache:'no-store'});
      const value=await response.json().catch(()=>({}));
      if(attempt!==importGeneration)return;
      if(!response.ok)throw new Error(String(value.detail||'BPMN import failed'));
      if(value.format!=='openworkgraph.bpmn-draft-import.v1')throw new Error('Unexpected BPMN preview response');
      importDraft=value;
      host.replaceChildren(el('h4','Unreviewed BPMN draft'),el('p',value.process_model.activities.length+' nodes · '+value.process_model.transitions.length+
        ' transitions · '+value.process_model.business_rules.length+' unconfirmed rules. Source SHA256 '+value.source_sha256));
      host.appendChild(el('p','This is declared design structure, with no observed executions, source outcomes, approved policy or execution rights. Typed tasks have no imported tool mappings. All rule text is untrusted and unevaluated.'));
      const warnings=el('ul');value.warnings.forEach(text=>warnings.appendChild(el('li',text.replaceAll('_',' '))));host.appendChild(warnings);
      if(value.label_corrections.length){
        host.appendChild(el('h4','Labels requiring correction'));
        value.label_corrections.forEach(item=>host.appendChild(el('p',item.activity_id+': '+(item.source_label||'(missing label)')+
          (item.source_label_truncated?' [truncated]':'')+' → placeholder label. Review and rename in the editor.')));
      }
      if(value.documentation_notes.length){
        const notes=el('details');notes.appendChild(el('summary','Source documentation · not saved automatically'));
        value.documentation_notes.forEach(item=>notes.appendChild(el('p',(item.source_node_id||'Process')+': '+item.text+(item.text_truncated?' [truncated]':''))));host.appendChild(notes);
      }
      if(window.owgProcessGraph)host.appendChild(window.owgProcessGraph.draw(value.process_model));
      host.appendChild(button('Open imported draft in editor',()=>openImportedDraft(value)));
    }catch(error){if(attempt===importGeneration){host.replaceChildren();status(error.message);}}
    finally{if(attempt===importGeneration)control.disabled=!input.files?.length;}
  }
  function draftDiscovered(source){
    if(busy)throw new Error('Wait for the current process request.');
    const value=source&&source.model;
    if(!value||!Array.isArray(value.activities)||!value.activities.length||!Array.isArray(value.transitions))
      throw new Error('A discovered process model is required.');
    if(!replaceDraft())return false;
    model=copy(value);
    record={workflow_id:null,revision:0,title:String(source.logName?'Discovered from '+source.logName:'Discovered process draft').slice(0,240),
      process_model:copy(model),evidence_refs:typeof source.sourceReference==='string'&&/^urn:openworkgraph:local-log-discovery:[0-9a-f]{64}$/.test(source.sourceReference)?[source.sourceReference]:[]};
    reviewed=false;renderEditor();
    status('Unsaved draft discovered from an event log. Add a business goal, replace each observed-share rule with the real condition and review every step before saving. Saving never authorizes execution.');
    document.querySelector('[role=tab][data-tab=evidence]')?.click();
    document.getElementById('owgProcessCatalog')?.scrollIntoView({block:'start'});
    return true;
  }
  function openImportedDraft(value){
    if(busy)throw new Error('Wait for the current process request.');
    if(value!==importDraft)throw new Error('Preview the selected BPMN again before opening its draft.');
    if(!replaceDraft())return;
    record={workflow_id:null,revision:0,title:'Imported BPMN design',evidence_refs:[]};
    model=copy(value.process_model);record.process_model=copy(model);reviewed=false;renderEditor();
    status('Opened an unsaved declared BPMN design. Review its goal, labels, branch rules and source documentation before saving. No observations, permissions or executable implementations were imported.');
    document.getElementById('owgCatalogEditor')?.scrollIntoView({block:'start'});
  }
  function install(){
    const panel=document.getElementById('panel-evidence');if(!panel||document.getElementById('owgProcessCatalog'))return;
    const host=el('section');host.id='owgProcessCatalog';host.className='card';
    host.append(el('h2','Process catalog · reviewed designs'),el('p','Save a reviewed process from the designer, or reopen a saved revision. Source event logs remain separate.'));
    host.appendChild(button('Load saved designs',()=>inventory()));
    installBPMNImport(host);
    const list=el('div');list.id='owgCatalogList';host.appendChild(list);
    let currency='USD';field(host,'Backlog currency',currency,value=>currency=value,['USD','EUR','GBP','SEK']);
    host.appendChild(button('Review improvement backlog',()=>backlog(currency)));
    const backlogHost=el('div');backlogHost.id='owgCatalogBacklog';host.appendChild(backlogHost);
    const message=el('p','No saved process has been loaded.');message.id='owgCatalogStatus';message.setAttribute('role','status');host.appendChild(message);
    const editor=el('div');editor.id='owgCatalogEditor';host.appendChild(editor);
    const style=el('style');style.textContent='#owgProcessCatalog label{display:block;margin:8px 0}#owgProcessCatalog fieldset{margin:12px 0;min-width:0}#owgProcessCatalog input:not([type=checkbox]){max-width:100%;width:32em}#owgProcessCatalog .owg-catalog-actions{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}';host.prepend(style);
    panel.appendChild(host);
  }
  window.owgProcessCatalog={saveDesigner,draftObserved,draftDiscovered,openSaved:ref=>load(ref.workflow_id,ref.revision,ref.content_sha256)};
  window.__owgProcessCatalogModel={linearModel};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
