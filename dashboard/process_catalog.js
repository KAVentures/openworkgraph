/* Explicit local process catalog. Same reviewed knowledge store, immutable revisions. */
(() => {
  'use strict';
  let record=null, model=null, reviewed=false, busy=false, after='', restorationDraft=null;
  let shadowGeneration=0,procurementGeneration=0;
  let importGeneration=0,importDraft=null;
  let ruleCheckGeneration=0,sourceRuleGeneration=0;
  let originGeneration=0,editGeneration=0,revisionCompareGeneration=0;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);return n;};
  const button=(text,fn)=>{const n=el('button',text);n.type='button';n.className='secondary';n.addEventListener('click',()=>Promise.resolve().then(fn).catch(error=>status(error.message)));return n;};
  const status=text=>{const n=document.getElementById('owgCatalogStatus');if(n)n.textContent=String(text);};
  const copy=value=>JSON.parse(JSON.stringify(value));
  function clearProcurementReview(){procurementGeneration++;document.getElementById('owgCatalogProcurementResult')?.replaceChildren();}
  function clearShadowReview(){shadowGeneration++;document.getElementById('owgCatalogShadowResult')?.replaceChildren();clearProcurementReview();}
  function clearRuleChecks(){ruleCheckGeneration++;document.getElementById('owgCatalogRuleCheckResult')?.replaceChildren();}
  function clearSourceRuleChecks(){sourceRuleGeneration++;document.getElementById('owgCatalogSourceRuleResult')?.replaceChildren();}
  function clearOrigins(){originGeneration++;document.getElementById('owgCatalogOriginResult')?.replaceChildren();}
  function clearRevisionComparison(){revisionCompareGeneration++;document.getElementById('owgCatalogRevisionCompare')?.replaceChildren();}
  function changed(invalidateTables=true){editGeneration++;if(invalidateTables&&model?.decision_tables?.length){for(const t of model.decision_tables)t.user_confirmed=false;document.querySelectorAll("input[data-table-confirmation]").forEach(n=>{n.checked=false;});}clearOrigins();clearRevisionComparison();clearSourceRuleChecks();reviewed=false;const check=document.getElementById('owgCatalogReviewed');if(check)check.checked=false;clearShadowReview();clearRuleChecks();}
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
      record=saved;model=copy(saved.process_model);restorationDraft=null;reviewed=false;renderEditor();
      status('Saved locally as revision '+saved.revision+'. Source CSV and native case IDs were not retained.');
      document.getElementById('owgProcessCatalog')?.scrollIntoView({block:'start'});
      return saved;
    });
  }
  function draftObserved(source){
    if(busy)throw new Error('Wait for the current process request.');
    const validPath=path=>Array.isArray(path)&&path.length>=1&&path.length<=40&&
      path.every(step=>typeof step==='string'&&!!step.trim()&&step.length<=240);
    if(!validPath(source.path))
      throw new Error('A bounded observed sequence of 1–40 labels is required.');
    // A second *observed* lane can be a human-proposed, editable TO-BE hypothesis.
    // It is never evidence that this alternative is policy, approved or executable.
    const hasAlternative=Object.prototype.hasOwnProperty.call(source,'proposedPath');
    if(hasAlternative&&(!validPath(source.proposedPath)||
      source.basis!=='selected_observed_variant'||
      JSON.stringify(source.path)===JSON.stringify(source.proposedPath)))
      throw new Error('A different complete observed alternative is required for a proposed TO-BE draft.');
    if(!replaceDraft())return false;
    const basis=['selected_observed_variant','selected_source_audit'].includes(source.basis)?source.basis:'user_declared';
    const activities=(hasAlternative?source.proposedPath:source.path).map((label,i)=>({id:'step_'+(i+1),
      label:/^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(label)?label:'Activity '+(i+1),kind:'activity',implementation:'unmapped'}));
    model={format:'openworkgraph.process-model.v1',goal:'',owner_label:'',baseline_path:copy(source.path),
      baseline_source:basis,baseline_coverage:source.partial?'partial_source_audit':'bounded_candidate_sequence',
      baseline_support_unit:basis==='selected_observed_variant'?'candidate_executions':'declared_cases',
      supporting_case_count:Number.isInteger(source.count)?Math.min(1000000,Math.max(0,source.count)):0,
      activities,transitions:activities.slice(1).map((activity,i)=>({source:activities[i].id,target:activity.id,rule_id:null})),
      business_rules:[],entry_activity_id:activities[0].id,terminal_activity_ids:[activities.at(-1).id],
      outcome_expectations:[],opportunity_scenario:null,observed_path_is_policy:false,execution_authorized:false,outcomes_verified:false};
    restorationDraft=null;
    record={workflow_id:null,revision:0,title:'Observed workflow proposal',process_model:copy(model),
      evidence_refs:(source.evidenceRefs||[]).filter(ref=>typeof ref==='string'&&/^execution:[A-Za-z0-9_-]+$/.test(ref)).slice(0,100)};
    reviewed=false;renderEditor();
    status(hasAlternative?
      'UNSAVED design comparison: AS-IS baseline is the selected observed lane; TO-BE activities are a DIFFERENT observed lane proposed by the user, not verified policy or an approved automation. Review and edit the business goal, every label, decisions and omissions before saving. Only AS-IS evidence refs are retained; alternative source lineage is not persisted. Unsupported labels use Activity placeholders.':
      'Unsaved proposal from a bounded observation. Add a business goal and review every proposed label. Unsupported labels use Activity placeholders; the AS-IS baseline keeps the supplied sequence.');
    document.querySelector('[role=tab][data-tab=evidence]')?.click();
    document.getElementById('owgProcessCatalog')?.scrollIntoView({block:'start'});
    return true;
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
      record=result;model=copy(result.process_model);restorationDraft=null;reviewed=false;renderEditor();
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
      record=result;model=copy(result.process_model);restorationDraft=null;reviewed=false;renderEditor();
      status('Saved revision '+record.revision+'. Earlier revisions remain available.');
    });
  }

  // Compare two exact saved snapshots. Never derive approval from structural equality.
  // Keys are stable model identities, not display labels; changed labels remain visible.
  function revisionDifferences(older,newer){
    const stable=value=>JSON.stringify(value,(_key,v)=>
      v&&typeof v==='object'&&!Array.isArray(v)?
        Object.fromEntries(Object.keys(v).sort().map(key=>[key,v[key]])):v);
    const format=value=>{
      const raw=typeof value==='string'?value:stable(value);
      return raw===undefined?'—':raw.length>180?raw.slice(0,180)+'… [truncated]':raw;
    };
    const rows=[];
    const add=(area,key,kind,before,after)=>rows.push({area,key,kind,before:format(before),after:format(after)});
    const old=older.process_model,now=newer.process_model;
    for(const key of ['title']){
      if(stable(older[key])!==stable(newer[key]))add('Process',key,'changed',older[key],newer[key]);
    }
    for(const key of ['goal','owner_label','baseline_path','baseline_source','baseline_coverage',
      'baseline_support_unit','supporting_case_count','entry_activity_id','terminal_activity_ids',
      'opportunity_scenario']){
      if(stable(old[key])!==stable(now[key]))add('Model',key,'changed',old[key],now[key]);
    }
    const collections=[
      ['Activities','activities',item=>item.id],
      ['Transitions','transitions',item=>item.source+' → '+item.target],
      ['Business rules','business_rules',item=>item.id],
      ['Manual review gates','manual_review_conditions',item=>item.activity_id+' · '+item.kind+' · '+item.condition_label],
      ['Outcome expectations','outcome_expectations',item=>item.terminal_activity_id+' · '+item.operation],
      ['Decision tables','decision_tables',(item,index)=>item.id||'table '+(index+1)]
    ];
    for(const [area,key,identify] of collections){
      const before=new Map((old[key]||[]).map((item,index)=>[identify(item,index),item]));
      const after=new Map((now[key]||[]).map((item,index)=>[identify(item,index),item]));
      for(const id of [...new Set([...before.keys(),...after.keys()])].sort()){
        if(!before.has(id))add(area,id,'added',undefined,after.get(id));
        else if(!after.has(id))add(area,id,'removed',before.get(id),undefined);
        else if(stable(before.get(id))!==stable(after.get(id)))add(area,id,'changed',before.get(id),after.get(id));
      }
    }
    return rows;
  }
  // Restoration is a new, unreviewed proposal, not a rollback of the saved record.
  // Inherited human confirmations and permission-like flags must never transfer.
  function clearHistoricalConfirmations(value){
    if(Array.isArray(value)){value.forEach(clearHistoricalConfirmations);return value;}
    if(value&&typeof value==='object'){
      for(const key of Object.keys(value)){
        if(['user_confirmed','execution_authorized','outcomes_verified','observed_path_is_policy',
            'automatic_branch_authorized'].includes(key))value[key]=false;
        else clearHistoricalConfirmations(value[key]);
      }
    }
    return value;
  }
  async function prepareHistoricalRestoration(previous,selected,generation,host){
    return locked(async()=>{
      if(generation!==revisionCompareGeneration||record!==selected||!host.isConnected)
        throw new Error('The comparison changed; compare the saved revisions again.');
      const id=selected.workflow_id;
      // Re-read BOTH immutable references. A previously displayed comparison cannot
      // resurrect a deleted revision or bypass a concurrent edit to the latest model.
      const [latest,historical]=await Promise.all([
        request('/v1/process-catalog/revision?'+new URLSearchParams({workflow_id:id})),
        request('/v1/process-catalog/revision?'+new URLSearchParams({
          workflow_id:id,revision:String(previous.revision)}))
      ]);
      if(generation!==revisionCompareGeneration||record!==selected||!host.isConnected)
        throw new Error('The comparison changed while restoring; start again.');
      if(latest.workflow_id!==id||latest.revision!==selected.revision||
         latest.content_sha256!==selected.content_sha256||
         historical.workflow_id!==id||historical.revision!==previous.revision||
         historical.content_sha256!==previous.content_sha256||
         historical.process_model?.format!=='openworkgraph.process-model.v1')
        throw new Error('The saved revision or latest process changed. Refresh and compare again.');
      if(!replaceDraft())return;
      // Keep the CURRENT optimistic-concurrency revision for a future reviewed save,
      // but never attach old/current evidence references to the restored model.
      record={...latest,evidence_refs:[]};
      model=clearHistoricalConfirmations(copy(historical.process_model));
      restorationDraft={from:historical.revision,base:latest.revision};
      changed();renderEditor();
      status('UNSAVED restoration draft from r'+historical.revision+
        ' against latest r'+latest.revision+'. Prior confirmations and evidence references were cleared. '+
        'Review and explicitly save a new revision; current source state remains unverified.');
      document.getElementById('owgCatalogEditor')?.scrollIntoView({block:'start'});
    });
  }

  async function compareSavedRevision(revision){
    return locked(async()=>{
      const selected=record,host=document.getElementById('owgCatalogRevisionCompare');
      if(!selected?.workflow_id||!Number.isInteger(selected.revision)||selected.revision<1||
         !Number.isInteger(revision)||revision<1||revision===selected.revision||!host)
        throw new Error('Open a saved revision and choose a different historical revision.');
      const generation=++revisionCompareGeneration;
      host.replaceChildren(el('p','Reading the selected immutable revision for comparison…'));
      try{
        const previous=await request('/v1/process-catalog/revision?'+new URLSearchParams({
          workflow_id:selected.workflow_id,revision:String(revision)}));
        if(generation!==revisionCompareGeneration||record!==selected||!host.isConnected)return;
        if(previous.workflow_id!==selected.workflow_id||previous.revision!==revision||
           !/^[0-9a-f]{64}$/.test(previous.content_sha256||'')||
           !/^[0-9a-f]{64}$/.test(selected.content_sha256||'')||
           previous.process_model?.format!=='openworkgraph.process-model.v1'||
           selected.process_model?.format!=='openworkgraph.process-model.v1')
          throw new Error('The compared revision or its immutable reference does not match.');
        const changes=revisionDifferences(previous,selected);
        const section=el('section');section.id='owgCatalogRevisionComparison';
        section.setAttribute('aria-label','Saved process revision differences');
        section.append(el('h4','Saved revision comparison · r'+revision+' → r'+selected.revision),
          el('p','Compared r'+revision+' SHA-256 '+previous.content_sha256),
          el('p','Opened saved r'+selected.revision+' SHA-256 '+selected.content_sha256),
          el('p','Only stored snapshots are compared. Unsaved edits, current source state and business outcomes are NOT evaluated. Equal structure is not policy approval, evidence validity or permission to run an agent.'));
        const summary=el('p',changes.length+' changed model entries. '+
          (changes.length>120?'Only the first 120 are displayed; comparison is incomplete.':
            'All detected stored-model differences are displayed.'));
        summary.setAttribute('role','status');section.appendChild(summary);
        if(changes.length){
          const wrap=el('div');wrap.className='table-wrap';
          const table=el('table'),thead=el('thead'),heading=el('tr');
          for(const title of ['Area','Identity','Change','Compared r'+revision,'Opened r'+selected.revision])
            heading.appendChild(el('th',title));
          thead.appendChild(heading);table.appendChild(thead);
          const body=el('tbody');
          for(const change of changes.slice(0,120)){
            const tr=el('tr');
            for(const value of [change.area,change.key,change.kind,change.before,change.after])
              tr.appendChild(el('td',value));
            body.appendChild(tr);
          }
          table.appendChild(body);wrap.appendChild(table);section.appendChild(wrap);
        }else section.appendChild(el('p','No stored-model difference detected. Source evidence and outcomes remain unverified.'));
        if(revision<selected.revision){
          section.appendChild(el('p','Restoration creates an UNSAVED draft from the historical model against the current latest revision. All earlier confirmations and evidence links are removed. A separate human review and new save are required.'));
          section.appendChild(button('Prepare historical r'+revision+' as unsaved restoration draft',
            ()=>prepareHistoricalRestoration(previous,selected,generation,host)));
        }
        section.appendChild(button('Open compared revision r'+revision,
          ()=>load(selected.workflow_id,revision,previous.content_sha256)));
        host.replaceChildren(section);
      }catch(error){
        if(generation===revisionCompareGeneration&&record===selected)
          host.replaceChildren(el('p','Comparison unavailable: '+error.message));
        throw error;
      }
    });
  }
  async function history(before){
    if(!record)return;
    return locked(async()=>{
      const workflowId=record.workflow_id;
      const params=new URLSearchParams({workflow_id:workflowId,limit:'10'});if(before)params.set('before_revision',String(before));
      const result=await request('/v1/process-catalog/history?'+params),host=document.getElementById('owgCatalogHistory');
      host.replaceChildren(el('h4','Revision history'));
      result.workflows.forEach(item=>{
        const row=el('p');row.appendChild(button('Open revision '+item.revision+' · '+item.saved_at,()=>load(workflowId,item.revision)));
        if(item.revision!==record.revision)row.appendChild(button('Compare r'+item.revision+' with opened r'+record.revision,()=>compareSavedRevision(item.revision)));
        host.appendChild(row);
      });
      const compare=el('div');compare.id='owgCatalogRevisionCompare';compare.setAttribute('aria-live','polite');host.appendChild(compare);
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
    const anchor=el('a');anchor.href=url;anchor.download='owg-process-r'+exportedRecord.revision+'.'+({pdd:'txt',json:'json',bpmn:'bpmn',dmn:'dmn','agent-project':'zip'}[format]);
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
      const rect=svg('rect',{x:p.x,y:p.y,width:175,height:50,rx:['decision','routing','parallel_split','parallel_join','inclusive_split','inclusive_join'].includes(activity.kind)?22:5,fill:'#f1f7f4',stroke:'#557e70'});
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

  // A structural comparison of exact display labels, not a claim that an
  // observed path is policy or that an alternative has the same case identity.
  // Ordered LCS avoids incorrectly treating repeated activities as a set.
  function designPathDifferences(baseline, activities) {
    if(!Array.isArray(baseline)||!Array.isArray(activities)||
       baseline.length>40||activities.length>40)return [];
    const left=baseline, right=activities.map(activity=>activity.label);
    const dp=Array.from({length:left.length+1},()=>Array(right.length+1).fill(0));
    for(let i=left.length-1;i>=0;i--)
      for(let j=right.length-1;j>=0;j--)
        dp[i][j]=left[i]===right[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1]);
    const differences=[];
    let i=0,j=0;
    while(i<left.length||j<right.length){
      if(i<left.length&&j<right.length&&left[i]===right[j]){i++;j++;}
      else if(i<left.length&&(j===right.length||dp[i+1][j]>=dp[i][j+1]))
        differences.push({kind:'as_is_only',label:left[i],position:i++ +1});
      else differences.push({kind:'to_be_only',label:right[j],position:j++ +1});
    }
    return differences;
  }
  function renderDifferenceReview(host) {
    const differences=designPathDifferences(model.baseline_path,model.activities);
    const section=el('section');section.id='owgCatalogDifferenceReview';
    section.setAttribute('aria-label','Review AS-IS and TO-BE differences');
    section.append(el('h4','Clarify differences between observed AS-IS and proposed TO-BE'),
      el('p','Exact displayed labels are aligned in order. An unmatched step can reflect a rename, reorder, missing observation or proposed change; this comparison cannot determine which. No source is read. A manual gate is a user-declared question, not evidence, policy, permission or executable logic.'));
    const summary=el('p',differences.length+' unmatched ordered steps. '+
      differences.filter(d=>d.kind==='as_is_only').length+' unmatched AS-IS and '+
      differences.filter(d=>d.kind==='to_be_only').length+' unmatched TO-BE.');
    section.appendChild(summary);
    if(!differences.length){section.appendChild(el('p','No unmatched labels. This does not establish that the business processes are equivalent.'));host.appendChild(section);return;}
    if(differences.length>12){
      section.appendChild(el('p','More than 12 unmatched steps. Batch clarification is unavailable; review the graph and add individual manual review conditions below. Nothing was omitted from an approved ledger.'));
      host.appendChild(section);return;
    }
    const snapshot=JSON.stringify({baseline:model.baseline_path,activities:model.activities.map(a=>[a.id,a.label]),
      conditions:model.manual_review_conditions||[]});
    const generation=editGeneration;
    const rows=[];
    const options=(control,values)=>{
      for(const [value,label] of values){const option=el('option',label);option.value=value;control.appendChild(option);}
    };
    for(const [index,difference] of differences.entries()){
      const row=el('fieldset'),legend=el('legend',(difference.kind==='as_is_only'?'Unmatched AS-IS':'Unmatched TO-BE')+
        ' step '+difference.position+': '+difference.label);
      row.appendChild(legend);
      const confirm=el('input');confirm.type='checkbox';confirm.id='owgCatalogDifferenceConfirm'+index;
      const confirmLabel=el('label',' Record a human-only clarification gate for this difference ');
      confirmLabel.htmlFor=confirm.id;row.append(confirmLabel,confirm);
      const control=(id,label,values)=>{
        const wrap=el('label',label+' '),select=el('select');select.id=id+index;
        options(select,values);wrap.htmlFor=select.id;wrap.appendChild(select);row.appendChild(wrap);return select;
      };
      const activity=control('owgCatalogDifferenceActivity','Review at proposed activity',
        [['','Choose a proposed activity'],...model.activities.map(a=>[a.id,a.id+' · '+a.label])]);
      const kind=control('owgCatalogDifferenceKind','Human review kind',
        [['','Choose kind'],['decision_point','Decision point'],['exception_condition','Exception condition']]);
      const response=control('owgCatalogDifferenceResponse','Required human response',
        [['','Choose response'],['pause_for_review','Pause for review'],
          ['request_source_recheck','Request source recheck'],['escalate_to_owner','Escalate to owner']]);
      const labelWrap=el('label','Editable clarification label ');
      const label=el('input');label.id='owgCatalogDifferenceLabel'+index;label.maxLength=64;
      label.value=('Review '+(difference.kind==='as_is_only'?'unmatched AS-IS ':'proposed TO-BE ')+
        String(difference.label).replace(/[^A-Za-z0-9 _-]/g,' ').replace(/\s+/g,' ').trim()).slice(0,64).trim();
      labelWrap.htmlFor=label.id;labelWrap.appendChild(label);row.appendChild(labelWrap);
      section.appendChild(row);
      rows.push({confirm,activity,kind,response,label});
    }
    const note=el('p','Select each difference you want to address, choose an activity and human response, then add gates. Saving remains a separate reviewed action.');
    note.id='owgCatalogDifferenceStatus';note.setAttribute('role','status');section.appendChild(note);
    section.appendChild(button('Add selected human review gates',()=>{
      const current=JSON.stringify({baseline:model?.baseline_path,
        activities:model?.activities?.map(a=>[a.id,a.label]),
        conditions:model?.manual_review_conditions||[]});
      if(document.getElementById('owgCatalogDifferenceReview')!==section||
         generation!==editGeneration||current!==snapshot){
        note.textContent='The process or review changed. Refresh the graph preview and choose again; no gates added.';
        return;
      }
      const selected=rows.filter(row=>row.confirm.checked);
      if(!selected.length){note.textContent='Choose at least one difference; nothing changed.';return;}
      const prior=model.manual_review_conditions||[];
      if(prior.length+selected.length>12){
        note.textContent='The process permits at most 12 manual review conditions. Nothing was added.';return;
      }
      const keys=new Set(prior.map(c=>[c.activity_id,c.kind,c.condition_label.toLowerCase()].join('|')));
      const additions=[];
      for(const row of selected){
        const name=row.label.value.trim();
        if(!model.activities.some(a=>a.id===row.activity.value)||
           !['decision_point','exception_condition'].includes(row.kind.value)||
           !['pause_for_review','request_source_recheck','escalate_to_owner'].includes(row.response.value)||
           !/^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(name)){
          note.textContent='Every selected gate needs an existing activity, explicit kind and response, and a valid 1–64 character label starting with a letter. Nothing was added.';
          return;
        }
        const key=[row.activity.value,row.kind.value,name.toLowerCase()].join('|');
        if(keys.has(key)){note.textContent='Duplicate clarification gate. Nothing was added.';return;}
        keys.add(key);
        additions.push({activity_id:row.activity.value,kind:row.kind.value,
          condition_label:name,response:row.response.value,source:'user_declared_unverified',
          evaluated_by_owg:false,automatic_branch_authorized:false,executable:false});
      }
      model.manual_review_conditions=[...prior,...additions];
      changed();renderEditor();
      status('Added '+additions.length+' unverified human-only clarification gate(s). Review and save the entire process separately; no source, agent or business action was performed.');
    }));
    host.appendChild(section);
  }
  function renderEditor(){
    clearRevisionComparison();
    clearOrigins();
    clearSourceRuleChecks();
    clearRuleChecks();
    clearShadowReview();
    const host=document.getElementById('owgCatalogEditor');if(!host||!model)return;
    host.replaceChildren(el('h3',record.revision?'Review process · revision '+record.revision:'Review process · unsaved observed proposal'));
    if(restorationDraft){
      const warning=el('p','UNSAVED restoration draft from historical r'+restorationDraft.from+
        ' against latest r'+restorationDraft.base+'. Earlier approvals and evidence references were cleared. ' +
        'Source state remains unverified. Review and explicitly save a new revision.');
      warning.id='owgCatalogRestorationDraft';warning.setAttribute('role','status');host.appendChild(warning);
    }
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
    if(model.activities.some(node=>['inclusive_split','inclusive_join'].includes(node.kind)))host.appendChild(el('p',
      'Inclusive splits run one or more branches: each branch has its own rule, and the join waits for exactly the branches that started. Rules stay unconfirmed until you confirm them. Generated projects check that a plan is a valid token flow and list the rules of the branches it chose; they do not evaluate those rules.'));
    host.appendChild(button('Refresh graph preview',()=>renderEditor()));
    renderDifferenceReview(host);
    const mutate=fn=>{fn();changed();renderEditor();};
    model.activities.forEach((activity,index)=>{
      const row=el('fieldset');row.appendChild(el('legend',activity.id));
      const label=field(row,'Activity '+activity.id,activity.label,value=>activity.label=value);label.id='owgCatalogActivity'+index;
      field(row,'Kind '+activity.id,activity.kind,value=>activity.kind=value,['activity','decision','routing','start','end','parallel_split','parallel_join','inclusive_split','inclusive_join']);
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
    ['json','bpmn','pdd',...(record.process_model?.decision_tables?.length?['dmn']:[])].forEach(format=>actions.appendChild(button('Export saved '+format.toUpperCase(),()=>download(format))));
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
      record=null;model=null;restorationDraft=null;reviewed=false;host.replaceChildren();status('Process revisions deleted locally. Downloaded exports are separate copies.');
    })));
    }
    host.appendChild(actions);
    if(record.revision){renderRuleChecks(host);renderSourceRuleChecks(host);renderShadowReview(host);renderProcurement(host);renderClarification(host);renderDMNImport(host);renderRuleLibrary(host);renderPrerequisites(host);}
    const historyHost=el('div');historyHost.id='owgCatalogHistory';host.appendChild(historyHost);
  }
  function renderPrerequisites(host){
    const selected=record,section=el('section');section.id='owgCatalogPrerequisites';
    const output=el('div'),message=el('p');message.setAttribute('role','status');
    const endpoint='/v1/process-catalog/prerequisites?'+new URLSearchParams({workflow_id:selected.workflow_id});
    const current=()=>record===selected&&section.isConnected;
    const pin=item=>({workflow_id:item.workflow_id,revision:item.revision,content_sha256:item.content_sha256});
    const stateLabel={current:'Current',superseded:'Newer revision available',unavailable:'Pinned revision unavailable'};
    const labelled=(label,control)=>{
      control.setAttribute('aria-label',label);
      const field=el('label',label+' ');field.append(control);return field;
    };
    function render(data){
      const sameRevision=selected.revision===data.root.revision&&selected.content_sha256===data.root.content_sha256;
      let links=data.links.map(link=>copy(link)),after='';
      const rows=el('div'),candidates=el('select');candidates.append(new Option('Choose a saved design',''));
      const choices=new Map();
      const confirm=el('input');confirm.type='checkbox';
      const saveLinks=button('Save prerequisites',()=>locked(async()=>{
        if(!confirm.checked)return;
        try{
          const result=await request(endpoint,{expected_revision:data.declaration?.revision||0,root_process:pin(selected),
            links:links.map(({id,prerequisite,relationship,reason})=>({id,prerequisite,relationship,reason})),
            user_confirmed:true});
          if(!current())return;
          render(result);message.textContent='Saved prerequisite revision '+result.declaration.revision+'.';
        }catch(error){if(current())message.textContent=error.message;}
      }));
      function updateSave(){
        saveLinks.disabled=!sameRevision||!confirm.checked||links.some(link=>!link.reason.trim());
      }
      function changedLinks(){
        confirm.checked=false;updateSave();
      }
      confirm.onchange=updateSave;
      function drawLinks(){
        rows.replaceChildren();
        for(const link of links){
          const id=link.prerequisite.workflow_id,resolution=link.resolution;
          const title=resolution.pinned?.title||resolution.latest?.title||id;
          const item=el('fieldset'),relation=el('select'),reason=el('input');
          item.append(el('legend',title+' · r'+link.prerequisite.revision));
          relation.append(new Option('Uses an output','uses_output'),new Option('Uses a policy','uses_policy'));
          relation.value=link.relationship;reason.maxLength=500;reason.value=link.reason;
          relation.disabled=reason.disabled=!sameRevision;
          relation.onchange=()=>{link.relationship=relation.value;changedLinks();};
          reason.oninput=()=>{link.reason=reason.value;changedLinks();};
          item.append(el('p',stateLabel[resolution.state]),labelled('Relationship for '+title,relation),labelled('Reason for '+title,reason));
          const latest=resolution.latest;
          if(sameRevision&&latest&&resolution.state!=='current'){
            item.append(button('Use latest '+title+' · r'+latest.revision,()=>{
              link.prerequisite=pin(latest);link.resolution={state:'current',pinned:latest,latest};
              changedLinks();drawLinks();
            }));
          }
          if(sameRevision)item.append(button('Remove '+title,()=>{
            links=links.filter(candidate=>candidate!==link);changedLinks();drawLinks();
          }));
          rows.append(item);
        }
        if(!links.length)rows.append(el('p','No prerequisites in this declaration.'));
      }
      const more=button('Load saved designs for prerequisites',()=>locked(async()=>{
        try{
          const page=await request('/v1/process-catalog?'+new URLSearchParams({after,limit:'50'}));
          if(!current()||!output.contains(candidates))return;
          for(const item of page.processes){
            if(item.workflow_id===selected.workflow_id||choices.has(item.workflow_id))continue;
            choices.set(item.workflow_id,item);
            candidates.append(new Option(item.title+' · r'+item.revision,item.workflow_id));
          }
          after=page.next_after||'';
          more.disabled=!page.has_more;more.textContent=page.has_more?'Load more saved designs':'Saved designs loaded';
        }catch(error){if(current())message.textContent=error.message;}
      }));
      const add=button('Add prerequisite',()=>{
        const item=choices.get(candidates.value);
        if(!item)return;
        if(links.some(link=>link.prerequisite.workflow_id===item.workflow_id)){
          message.textContent='This design is already a prerequisite.';return;
        }
        if(links.length>=20){message.textContent='A design can have up to 20 prerequisites.';return;}
        let number=1;
        while(links.some(link=>link.id==='local_'+number))number++;
        links.push({id:'local_'+number,prerequisite:pin(item),reason:'',relationship:'uses_output',
          resolution:{state:'current',pinned:item,latest:item}});
        candidates.value='';changedLinks();drawLinks();
      });
      output.replaceChildren(el('h5','Direct prerequisites'),
        el('p',data.declaration?'Declaration r'+data.declaration.revision+' · '+stateLabel[data.root_resolution.state]+' root basis.':
          'No prerequisite declaration has been saved for this design.'),rows);
      if(sameRevision)output.append(more,labelled('Prerequisite design',candidates),add,
        labelled('I reviewed these exact prerequisite revisions',confirm),saveLinks);
      else output.append(el('p','Open the latest saved revision to edit this design’s prerequisites.'));
      const consumers=el('ul');
      for(const item of data.potential_consumers){
        const title=item.pinned?.title||item.latest?.title||item.process.workflow_id;
        consumers.append(el('li',title+' · r'+item.process.revision+
          (item.distance===1?' · Pins this design at r'+item.direct_prerequisite_revision+
            (item.direct_prerequisite_current?'':' · Prerequisite needs review'):
            ' · Through '+item.distance+' links')+' · '+stateLabel[item.state]));
      }
      output.append(el('h5','Potential consumers'),data.potential_consumers.length?consumers:
        el('p','No saved declarations identify a consumer.'));
      drawLinks();updateSave();
    }
    const refresh=button('Review prerequisites',()=>locked(async()=>{
      message.textContent='Loading prerequisite declarations…';
      try{
        const data=await request(endpoint);
        if(current()){render(data);message.textContent='';}
      }catch(error){if(current())message.textContent=error.message;}
    }));
    section.append(el('h4','Process prerequisites'),
      el('p','Pin the saved designs whose outputs or policies this process uses.'),refresh,message,output);
    host.append(section);
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
      // A matched rule is not necessarily a selected route: UNIQUE tables may
      // overlap, FIRST tables may be blocked by earlier unknowns, and source
      // facts may be incomplete. Show selected-row coverage explicitly.
      for(const table of result.decision_table_test_coverage||[]){
        const detail=el('details');
        const rowCount=Number(table.selected_row_count||0),declaredCount=Number(table.declared_row_count||0);
        detail.appendChild(el('summary','Decision table '+table.table_id+' · '+table.hit_policy+
          ' · '+rowCount+'/'+declaredCount+' declared branches selected in these supplied cases'));
        detail.appendChild(el('p',Number(table.selected_case_count||0)+'/'+Number(table.cases_checked||0)+
          ' supplied cases selected an outgoing branch. '+Number(table.evaluated_case_count||0)+
          ' cases evaluated at least one table row; '+Number(table.not_evaluated_case_count||0)+
          ' did not. Coverage is descriptive for these supplied cases only, not exhaustive rule coverage.'));
        const statuses=Object.entries(table.status_counts||{});
        detail.appendChild(el('p',statuses.map(([key,count])=>key.replaceAll('_',' ')+': '+Number(count||0)).join(' · ')||
          'No decision outcomes were evaluated.'));
        const rowList=el('ul');
        for(const row of table.rows||[]){
          const n=Number(row.selected_cases||0),matched=Number(row.matched_cases_even_if_ambiguous||0);
          rowList.appendChild(el('li',row.row_id+' → '+row.target_activity_id+
            ' · selected '+n+' case'+(n===1?'':'s')+
            ' · matched '+matched+' case'+(matched===1?'':'s')+
            (n===0?' · NOT SELECTED in supplied examples':'')+
            '. A row can match without being selected if another rule overlaps or takes precedence.'));
        }
        detail.appendChild(rowList);
        output.appendChild(detail);
      }
      // Separate hypothetical probes from the independently supplied case batch.
      // Never imply sampled boundaries prove whole-domain safety or grant action rights.
      for(const probe of result.decision_table_boundary_probes||[]){
        const detail=el('details');detail.className='owgDecisionBoundaryProbes';
        const gap=Number(probe.status_counts?.gap||0),overlap=Number(probe.status_counts?.overlap||0);
        detail.appendChild(el('summary','Synthetic numeric boundaries · '+probe.table_id+
          (probe.status==='sampled'?' · '+Number(probe.sampled_numeric_points||0)+
            ' test points · '+gap+' gaps · '+overlap+' overlaps':' · not evaluated')));
        if(probe.status!=='sampled'){
          detail.appendChild(el('p','No synthetic numeric boundary check: '+
            String(probe.reason||'unsupported table shape').replaceAll('_',' ')+
            '. This is not evidence that the rules are complete.'));output.appendChild(detail);continue;
        }
        detail.appendChild(el('p','Artificial values for one shared declared numeric fact field. '+
          Number(probe.first_precedence_competition_count||0)+
          ' FIRST precedence point(s) matched multiple rows. '+Number(probe.unsampled_numeric_regions||0)+
          ' numeric region(s) could not be represented. These are sample checks, not proof of exhaustive domain coverage, real policy compliance or an authorized decision.'));
        for(const [key,label] of [
          ['gap_witnesses','No branch at'],['overlap_witnesses','Competing UNIQUE branches at'],
          ['first_precedence_competition_witnesses','FIRST precedence masks competing rows at']]){
          const values=probe[key]||[];
          if(!values.length)continue;
          const list=el('ul');
          for(const item of values)list.appendChild(el('li',label+' hypothetical value '+
            String(item.synthetic_value)+'. Review the declared rules.'));
          detail.appendChild(list);
        }
        output.appendChild(detail);
      }
      if(result.decision_table_test_coverage?.length)output.appendChild(el('p',
        'Decision evidence is from a selected local file, not verified actual operations. Rows without a selected example may still be valid; the test set does not prove completeness, fairness, correctness or execution authorization.'));
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
        for(const table of item.decision_tables||[])list.appendChild(el('li','Decision table '+table.table_id+' · '+table.status.replaceAll('_',' ')+(table.selected_target_activity_id?' → '+table.selected_target_activity_id:'')+(table.blocking_decision_table_id?' · waiting on '+table.blocking_decision_table_id:'')+(table.proposed_branch_agrees===false?' · proposed branch disagrees':'')));
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
  function renderRuleLibrary(host){
    const section=el('section');section.id='owgCatalogRuleLibrary';
    section.append(el('h4','Reusable rule library'),el('p','Keep reviewed rules in named, versioned rule sets and reuse them across designs. Linking copies the library rule into this design as an unconfirmed rule; it is not saved until you review and save. Drift lists saved designs whose linked rules are behind the library or were edited locally. OWG never evaluates or runs these rules.'));
    const sets=el('select');sets.setAttribute('aria-label','Rule set');
    const name=el('input');name.type='text';name.placeholder='Rule set name';name.setAttribute('aria-label','Rule set name');
    const source=el('input');source.type='text';source.placeholder='Source (e.g. Purchasing policy v3)';source.setAttribute('aria-label','Rule set source');
    const designRule=el('select');designRule.setAttribute('aria-label','Design rule to link');
    (record.process_model?.business_rules||[]).forEach(r=>{const o=el('option',r.id+': '+r.text.slice(0,60));o.value=r.id;designRule.appendChild(o);});
    const libraryRule=el('select');libraryRule.setAttribute('aria-label','Library rule');
    const output=el('div');output.id='owgCatalogRuleLibraryResult';output.setAttribute('role','status');
    let latest=null;
    const loadSet=async()=>{
      libraryRule.replaceChildren();latest=null;
      if(!sets.value)return;
      latest=await request('/v1/process-catalog/rule-library/set?'+new URLSearchParams({rule_set_id:sets.value}));
      latest.rules.forEach(r=>{const o=el('option',r.id+': '+r.text.slice(0,60));o.value=r.id;libraryRule.appendChild(o);});
    };
    const refresh=async(selectId)=>{
      const value=await request('/v1/process-catalog/rule-library');
      const fresh=el('option','New rule set');fresh.value='';sets.replaceChildren(fresh);
      value.rule_sets.forEach(s=>{const o=el('option',s.name+' (revision '+s.revision+', '+s.rule_count+' rules)');o.value=s.rule_set_id;sets.appendChild(o);});
      if(selectId)sets.value=selectId;
      await loadSet();
    };
    sets.addEventListener('change',()=>loadSet().catch(error=>status(error.message)));
    const blank=el('option','New rule set');blank.value='';sets.appendChild(blank);
    const saveSet=()=>locked(async()=>{
      const rules=(record.process_model?.business_rules||[]).map(r=>{const v={id:r.id,text:r.text};if(r.predicate)v.predicate=r.predicate;return v;});
      if(!rules.length)throw new Error('This saved design has no rules to keep.');
      const body={name:name.value.trim()||latest?.name||record.title||'Rule set',source_label:source.value.trim(),rules};
      if(sets.value){body.rule_set_id=sets.value;body.expected_revision=latest?.revision||0;}
      const saved=await request('/v1/process-catalog/rule-library',body);
      await refresh(saved.rule_set_id);
      status('Saved revision '+saved.revision+' of rule set '+saved.name+'. Earlier revisions stay readable.');
    });
    const linkRule=()=>locked(async()=>{
      const selected=record;
      if(!sets.value||!libraryRule.value||!designRule.value)throw new Error('Choose a design rule, a rule set and a library rule.');
      const value=await request('/v1/process-catalog/rule-library/link?'+new URLSearchParams({workflow_id:selected.workflow_id,revision:String(selected.revision)}),
        {rule_id:designRule.value,rule_set_id:sets.value,library_rule_id:libraryRule.value,library_revision:latest?.revision});
      if(record!==selected)return;
      if(model&&!window.confirm('Replace the editor contents with this linked-rule proposal?'))return;
      model=copy(value.proposed_process_model);reviewed=false;renderEditor();
      status('Linked rule '+designRule.value+' to the library as unsaved edits of revision '+selected.revision+'. Review and confirm the rule, then save a new revision.');
    });
    const showDrift=()=>locked(async()=>{
      const value=await request('/v1/process-catalog/rule-library/drift');
      const words={rule_set_missing:'rule set no longer in the library',edited_in_design:'edited in the design',removed_from_library:'removed from the latest library revision',library_rule_changed:'library has a newer version'};
      output.replaceChildren(el('p',value.findings.length+' drifted linked rules across '+value.designs_checked+' saved designs'));
      const list=el('ul');
      value.findings.forEach(f=>list.appendChild(el('li',f.workflow_id+' r'+f.revision+' · rule '+f.rule_id+' · linked to revision '+f.linked_revision+(f.latest_revision?' (latest '+f.latest_revision+')':'')+' · '+(words[f.status]||f.status))));
      output.appendChild(list);
    });
    const reviewUpgrades=()=>locked(async()=>{
      const selected=record;
      const url='/v1/process-catalog/rule-library/upgrade-review?'+new URLSearchParams({
        workflow_id:selected.workflow_id,revision:String(selected.revision)});
      const base={expected_content_sha256:selected.content_sha256};
      const preview=await request(url,base);
      if(record!==selected)return;
      output.replaceChildren(el('p',preview.linked_rules.length+' linked rules · '+preview.selectable_rule_count+
        ' available upgrades. Review exact source revisions and select rules to update together. Nothing is saved automatically.'));
      const choices=[];
      for(const row of preview.linked_rules){
        const item=el('fieldset');item.append(el('legend',row.rule_id+' · '+row.status.replaceAll('_',' ')));
        item.append(el('p','Linked revision '+row.linked_revision+' · latest '+(row.latest_revision??'unavailable')));
        if(row.latest_text!==null){
          item.append(el('p','Current rule: '+row.current_text),el('p','Library rule: '+row.latest_text));
          if(row.predicate_changed)item.append(el('p','Current predicate: '+JSON.stringify(row.current_predicate)),
            el('p','Library predicate: '+JSON.stringify(row.latest_predicate)));
        }
        if(row.affected_decision_table_ids.length)item.append(el('p','Decision tables requiring fresh review: '+row.affected_decision_table_ids.join(', ')));
        if(row.selectable){
          const label=el('label'),check=el('input');check.type='checkbox';
          check.setAttribute('aria-label','Upgrade linked rule '+row.rule_id);
          label.append(check,el('span','Include this exact upgrade'));item.append(label);
          choices.push({check,row});
        }else if(row.status!=='current')item.append(el('p','Cannot upgrade automatically; review the missing, edited or inconsistent reference separately.'));
        output.append(item);
      }
      if(choices.length)output.append(button('Open selected rule upgrades in editor',()=>locked(async()=>{
        if(record!==selected)throw new Error('Saved process changed; reopen the upgrade review.');
        const selections=choices.filter(c=>c.check.checked).map(({row})=>({
          rule_id:row.rule_id,library_revision:row.latest_revision,content_sha256:row.latest_content_sha256}));
        if(!selections.length)throw new Error('Select at least one linked rule to upgrade.');
        const value=await request(url,{...base,selections});
        if(record!==selected)return;
        if(model&&!window.confirm('Replace the editor contents with this exact rule upgrade proposal? Affected rule and decision confirmations will be cleared.'))return;
        model=copy(value.proposed_process_model);reviewed=false;renderEditor();
        status('Opened '+value.selected_rule_ids.length+' linked rule upgrades as unsaved edits; review the affected rules and decision tables before saving.');
      })));
    });
    section.append(button('Load rule sets',()=>locked(()=>refresh())),sets,name,source,button('Save design rules to library',saveSet),designRule,libraryRule,
      button('Link rule from library',linkRule),button('Show rule drift',showDrift),button('Review linked rule upgrades',reviewUpgrades),output);
    host.appendChild(section);
  }
  function renderDMNImport(host){
    const decisions=(record.process_model?.activities||[]).filter(activity=>activity.kind==='decision');
    if(!decisions.length)return;
    const section=el('section');section.id='owgCatalogDMN';
    section.append(el('h4','Import DMN decision tables'),
      el('p','Choose a DMN file, map its tables to decisions in this design, and preview them together. Imported tables and rules start unconfirmed.'));
    const from=el('input');from.type='date';
    const until=el('input');until.type='date';
    const file=el('input');file.type='file';file.accept='.dmn,.xml,application/xml';
    const labelled=(title,control)=>{
      const label=el('label',title+' ');control.setAttribute('aria-label',title);label.append(control);return label;
    };
    const settings=el('div');settings.className='row';
    const output=el('div');output.setAttribute('role','status');
    const mappings=el('div');
    const proposal=el('div');
    let inspection=null, choices=[], edit=0;
    const resetProposal=()=>{++edit;proposal.replaceChildren();output.textContent='';};
    file.onchange=()=>{
      inspection=null;choices=[];mappings.replaceChildren();resetProposal();
      preview.disabled=true;
    };
    from.oninput=until.oninput=resetProposal;
    async function send(suffix,parameters,source){
      await window.__owgAuthReady;
      const query=parameters?'?'+new URLSearchParams(parameters):'';
      const response=await fetch('/v1/process-catalog/import-dmn/'+suffix+query,
        {method:'POST',headers:{'Content-Type':'application/xml'},body:source,cache:'no-store'});
      const value=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'DMN request failed');
      return value;
    }
    const inspect=button('Inspect DMN file',()=>locked(async()=>{
      const selected=record,source=file.files?.[0];
      if(!source||source.size>256*1024){output.textContent='Choose a DMN file of at most 256 KiB.';return;}
      file.onchange();
      const current=()=>record===selected&&file.files?.[0]===source&&section.isConnected;
      output.textContent='Inspecting the DMN file…';
      try{
        const result=await send('inspect',null,source);
        if(!current())return;
        inspection=result;choices=[];
        for(const item of result.decisions){
          const row=el('div');row.className='row';
          const title='Table '+(item.index+1)+(item.name?' · '+item.name:'');
          row.append(el('span',title+' · '+item.hit_policy+' · '+item.rule_count+' rules'));
          if(item.unsupported_reason)row.append(el('span',item.unsupported_reason));
          else{
            const select=el('select');
            select.append(new Option('Skip this table',''));
            for(const decision of decisions)select.append(new Option(decision.label,decision.id));
            select.onchange=resetProposal;
            choices.push({item,select});
            row.append(labelled('Design decision for '+title,select));
          }
          mappings.append(row);
        }
        if(choices.length===1&&decisions.length===1)choices[0].select.value=decisions[0].id;
        preview.disabled=choices.length===0;
        output.textContent=result.decisions.length+' tables found.';
      }catch(error){if(current())output.textContent=error.message;}
    }));
    const preview=button('Preview selected DMN tables',()=>locked(async()=>{
      if(!inspection)return;
      const selected=record,source=file.files?.[0],ticket=++edit;
      const current=()=>record===selected&&file.files?.[0]===source&&ticket===edit&&section.isConnected;
      proposal.replaceChildren();
      const assignments=choices.filter(choice=>choice.select.value).map(({item,select})=>({
        index:item.index,decision_activity_id:select.value
      }));
      if(!from.value||assignments.length===0){output.textContent='Choose at least one design decision and enter the effective start date.';return;}
      if(new Set(assignments.map(item=>item.decision_activity_id)).size!==assignments.length){
        output.textContent='Choose a different design decision for each table.';return;
      }
      output.textContent='Checking the selected tables…';
      try{
        const result=await send('batch',{workflow_id:selected.workflow_id,revision:String(selected.revision),
          document_sha256:inspection.document_sha256,assignments:JSON.stringify(assignments),effective_from:from.value,
          ...(until.value?{effective_until:until.value}:{})},source);
        if(!current())return;
        output.textContent=result.proposals.length+' tables ready for review.';
        for(const item of result.proposals){
          const summary=el('details'),list=el('ul');
          const decision=decisions.find(activity=>activity.id===item.decision_activity_id);
          for(const rule of item.proposed_rules)list.append(el('li',rule.text));
          summary.append(el('summary',decision.label+' · '+item.hit_policy+' · '+item.proposed_rules.length+' unconfirmed rules'),list);
          proposal.append(summary);
        }
        proposal.append(button('Open proposal in editor',()=>{
          if(!current()||!replaceDraft())return;
          model=copy(result.proposed_process_model);reviewed=false;renderEditor();
          status('Opened the DMN tables as unsaved edits of revision '+selected.revision+'. Review and confirm the tables and rules before saving.');
        }));
      }catch(error){if(current())output.textContent=error.message;}
    }));
    preview.disabled=true;
    settings.append(labelled('Choose DMN file',file),inspect);
    section.append(settings,mappings,labelled('DMN effective from',from),labelled('DMN effective until (optional)',until),preview,output,proposal);
    host.appendChild(section);
  }
  function renderClarification(host){
    const section=el('section');section.id='owgCatalogClarification';
    section.append(el('h4','Clarify this design against multiple policy sources'),
      el('p','Review up to five separately supplied SOP or policy documents. Each citation identifies the source and character range. OWG does not store the document contents. Contradictory statements must be resolved by explicitly selecting the applicable evidence; no statement is trusted as an instruction or automatically approved. Only selected citation excerpts and your answers are retained in the local decision ledger; the full documents are not. Saving an answer does not change the process design.'));
    const sources=el('div');sources.id='owgCatalogClarifyDocuments';
    const output=el('div');output.id='owgCatalogClarifyResult';output.setAttribute('role','status');
    const add=button('Add another policy or SOP',()=>appendDocument());
    add.id='owgCatalogClarifyAddDocument';
    const rows=[];let sourceGeneration=0,serial=0;
    function refresh(){
      sources.replaceChildren(...rows.map((r,i)=>{r.legend.textContent='Document '+(i+1);return r.node;}));
      add.disabled=rows.length>=5;
    }
    function invalidate(){
      sourceGeneration++;
      output.replaceChildren(el('p','Documents changed. Run clarification again before recording any answer.'));
    }
    function appendDocument(values={}){
      if(rows.length>=5)throw new Error('Review at most five source documents in one bundle.');
      const node=el('fieldset');node.className='owgClarifyDocument';
      const legend=el('legend','Source document');
      const titleLabel=el('label','Document title '),title=el('input');
      title.type='text';title.maxLength=120;title.placeholder='Policy or SOP revision';
      title.dataset.clarifyTitle='true';title.setAttribute('aria-label','Source document title');
      title.value=values.title||'';titleLabel.append(title);
      const textLabel=el('label','Exact source text '),sourceText=el('textarea');
      sourceText.rows=5;sourceText.maxLength=60000;
      sourceText.dataset.clarifyText='true';sourceText.setAttribute('aria-label','Source document text');
      sourceText.value=values.text||'';textLabel.append(sourceText);
      const fileLabel=el('label','Load UTF-8 plain text or Markdown file '),file=el('input');
      file.type='file';file.accept='.txt,.md,text/plain,text/markdown';file.setAttribute('aria-label','Load source text file');
      fileLabel.append(file);
      const remove=button('Remove document',()=>{
        const idx=rows.findIndex(row=>row.node===node);if(idx<0)return;
        rows.splice(idx,1);refresh();invalidate();
      });
      const row={node,title,sourceText,legend};
      for(const input of [title,sourceText])input.addEventListener('input',invalidate);
      file.addEventListener('change',()=>locked(async()=>{
        const source=file.files?.[0];
        if(!source)return;
        try{
          if(source.size>65536||!/\.(txt|md)$/i.test(source.name))
            throw new Error('Choose a UTF-8 .txt or .md document of no more than 64 KiB.');
          const value=new TextDecoder('utf-8',{fatal:true}).decode(await source.arrayBuffer());
          if(!value.trim()||value.length>60000)throw new Error('Source text must have 1–60,000 characters.');
          title.value=source.name.slice(0,120);sourceText.value=value;invalidate();
        }finally{file.value='';}
      }));
      node.append(legend,titleLabel,textLabel,fileLabel,remove);
      rows.push(row);refresh();invalidate();
    }
    const docs=()=>rows.filter(row=>row.sourceText.value.trim()).map(row=>({
      title:row.title.value.trim()||'Untitled source',text:row.sourceText.value}));
    const queryFor=selected=>'?'+new URLSearchParams({workflow_id:selected.workflow_id,revision:selected.revision});
    const run=async()=>{
      const selected=record,generation=sourceGeneration,documents=docs();
      if(!selected?.workflow_id)throw new Error('Save a versioned process before reviewing source documents.');
      if(!documents.length)throw new Error('Enter at least one source document.');
      const query=queryFor(selected);
      const result=await request('/v1/process-catalog/clarify'+query,{documents});
      if(record!==selected||generation!==sourceGeneration)return;
      output.replaceChildren(el('p',result.questions.length+' open questions · '+result.answered_question_count+
        ' answered on this process revision and exact source bundle · '+result.statement_count+
        ' source statements. Full documents are not retained; selected citations and answers are recorded.'));
      const coverage=result.source_coverage;
      if(coverage){
        const omitted=coverage.documents.reduce((sum,d)=>sum+d.overlong_statements+d.omitted_by_budget,0);
        const incomplete=coverage.subjects.filter(s=>!s.conflict_review_complete).length;
        if(omitted||incomplete)output.append(el('p',
          'Source review incomplete: '+omitted+' statement(s) omitted and '+incomplete+
          ' subject(s) with incomplete conflict coverage. Narrow or split the sources before confirming affected questions.','note'));
        else output.append(el('p','All bounded source and conflict scans completed. Lexical matches are review prompts, not proof of policy accuracy.'));
      }
      if(result.instruction_like_statements.length)output.append(el('p',
        result.instruction_like_statements.length+' statement(s) look like instructions to the system and were excluded from evidence.'));
      for(const q of result.questions){
        const box=el('section');box.append(el('h5',q.prompt));
        if(q.source_coverage)box.append(el('p',q.source_coverage.matching_statements+
          ' matching statements · '+q.source_coverage.conflict_candidates_checked+
          ' checked for possible conflicts · '+q.source_coverage.displayed_conflict_count+' of '+
          q.source_coverage.possible_conflict_count+' possible conflicts displayed.','muted'));
        if(q.coverage_blocked)box.append(el('p','Confirmation is unavailable until source coverage is complete. Narrow the supplied documents or defer this question.','note'));
        if(q.previous_decision)box.append(el('p','Previous decision: '+q.previous_decision+'. This question remains unresolved.','muted'));
        const cites=el('ul');
        const citeText=c=>'“'+c.text+'” — '+c.document_title+' (source '+(Number(c.document_index)+1)+', characters '+c.start+'–'+c.end+')';
        for(const c of q.citations)cites.append(el('li',citeText(c)));
        box.append(cites);
        const disputed=new Map();
        if(q.contradictions.length){
          box.append(el('p','The documents disagree. To confirm or revise, choose ONE governing source statement and explain why. Nothing is chosen automatically; reject or defer if the conflict remains unresolved.'));
          for(const conflict of q.contradictions){
            box.append(el('p','Disagreement ('+conflict.kind.replaceAll('_',' ')+'): '+
              conflict.statements.map(c=>c.document_title+' at '+c.start).join(' versus ')));
            for(const c of conflict.statements)disputed.set(c.statement_id,c);
          }
          const group=el('fieldset');group.className='owgClarifyConflicts';
          group.append(el('legend','Select one governing statement'));
          for(const [statementId,c] of disputed){
            const label=el('label'),choiceInput=el('input');choiceInput.type='radio';
            choiceInput.name='owgClarifySource_'+q.question_id;
            choiceInput.value=statementId;
            choiceInput.setAttribute('aria-label','Governing statement from '+c.document_title+' at '+c.start);
            label.append(choiceInput,el('span',citeText(c)));group.append(label);
          }
          box.append(group);
        }
        const answer=el('input');answer.type='text';answer.maxLength=2000;
        answer.placeholder=q.kind==='contradiction'?'Explain which policy applies and why':'Your answer (optional)';
        answer.setAttribute('aria-label','Human explanation for: '+q.prompt);
        box.append(answer);
        for(const choice of q.allowed_decisions)box.append(button(choice[0].toUpperCase()+choice.slice(1),()=>locked(async()=>{
          if(record!==selected||generation!==sourceGeneration)
            throw new Error('Process or source documents changed; run clarification again.');
          let cited;
          if(q.kind==='contradiction'){
            const chosen=box.querySelector('.owgClarifyConflicts input[type=radio]:checked')?.value;
            cited=chosen?[chosen]:[];
            if(['confirm','revise'].includes(choice)){
              if(!chosen)throw new Error('Select one governing statement before resolving a conflict.');
              if(!answer.value.trim())throw new Error('Explain your resolution of the contradictory policy statements.');
            }
          }else cited=[...new Set(q.citations.map(c=>c.statement_id))].slice(0,5);
          const res=await request('/v1/process-catalog/clarify/decide'+query,{documents,
            decision:{question_id:q.question_id,decision:choice,answer:answer.value,
              cited_statement_ids:cited.slice(0,5),expected_documents_sha256:result.documents_sha256}});
          status(res.proposed_change?'Decision recorded. A proposed rule confirmation still needs an explicit new design revision.':
            'Human decision recorded for revision '+selected.revision+' and this exact source set.');
          await run();
        })));
        output.append(box);
      }
    };
    section.append(sources,add,button('Find open questions across sources',()=>locked(run)),output);
    host.append(section);
    appendDocument();
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

  function renderProcurement(host){
    const section=el('section');section.appendChild(el('h4','AI workforce procurement'));
    section.appendChild(el('p','Compare portable shadow evaluations, freeze a repeated trial, or review its full scheduled population. Files are inspected only on this computer and are not retained. Model identities, measurements and business outcomes remain unverified.'));
    const mode=el('select');mode.id='owgCatalogProcurementMode';mode.setAttribute('aria-label','Procurement operation');
    for(const [value,label] of [['compare','Compare completed shadow reports'],['freeze','Freeze a repeated trial plan'],['review','Review a frozen trial and captures'],['operating-cost','Review human and AI operating costs'],['router-freeze','Freeze a held-out router rehearsal'],['router-review','Review held-out routing predictions'],['router-change-freeze','Freeze router version comparison'],['router-change-assess','Assess paired router version change'],['router-change-decide','Record controlled router change referral'],['router-pilot-freeze','Freeze controlled router pilot'],['router-pilot-review','Review controlled pilot observations'],['router-pilot-decide','Record pilot expansion or rollback referral'],['router-monitor-freeze','Freeze multi-window router monitoring'],['router-monitor-review','Review cumulative monitoring snapshot'],['router-monitor-decide','Record monitoring or rollback referral'],['router-journal-list','List locally retained monitoring journals'],['router-journal-append','Retain a monitoring checkpoint locally'],['router-journal-refresh','Refresh monitoring using the local clock'],['router-journal-schedule','Set journal-specific scheduled review consent'],['router-journal-detail','Inspect retained monitoring history'],['router-journal-decide','Record retained monitoring decision'],['router-journal-forget','Remove retained monitoring journal'],['router-producer-grant','Issue scoped monitoring producer credential'],['router-producer-revoke','Revoke monitoring producer credential'],['router-finance-reconcile','Reconcile declared booked financial amounts'],['nominate','Nominate a candidate before holdout'],['assess','Assess the held-out trial'],['decide','Record a pinned rehearsal decision'],['outcome-review','Review controlled sandbox outcomes'],['rollout-review','Request separate rollout review'],['deployment-review','Review monitored deployment readiness'],['deployment-decide','Record deployment review referral']]){
      const option=el('option',label);option.value=value;mode.appendChild(option);
    }
    const modeLabel=el('label','Procurement operation ');modeLabel.htmlFor=mode.id;modeLabel.appendChild(mode);
    function chooser(id,title){
      const label=el('label',title+' '),file=el('input');file.id=id;file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label',title);
      label.htmlFor=id;label.appendChild(file);file.addEventListener('change',clearProcurementReview);return [label,file];
    }
    const [documentLabel,documentFile]=chooser('owgCatalogProcurementDocument','Procurement JSON document');
    const [planLabel,planFile]=chooser('owgCatalogProcurementPlan','Frozen trial plan JSON');
    planLabel.hidden=true;
    const hint=el('p','Choose an existing comparison request (see AGENT_PROCUREMENT.md).');
    mode.addEventListener('change',()=>{
      documentFile.value='';planFile.value='';planLabel.hidden=mode.value!=='review';
      hint.textContent=mode.value==='compare'?'Choose an existing comparison request (see AGENT_PROCUREMENT.md).':
        mode.value==='freeze'?'Choose a confirmed design JSON with exact reference report, candidates, gates and repeats (see AGENT_PROCUREMENT_TRIALS.md).':
        mode.value==='review'?'Choose the frozen plan and a capture ledger. Failed and missing scheduled slots remain in the denominator.':
        mode.value==='operating-cost'?'Choose the exact trial and human operating-cost bundle (see AGENT_PROCUREMENT_OPERATING_COST.md). Review and fallback time, missing evidence and fixed-cost assumptions remain visible.':
        mode.value.startsWith('router-producer-')?'Issue or revoke an explicitly confirmed scoped producer credential using the retained journal head. Credentials are shown once and never included in report downloads.':
        mode.value==='router-finance-reconcile'?'Submit an explicitly confirmed finance statement for a closed monitoring window, with current journal and report pins. See AGENT_PROCUREMENT_FINANCE.md.':
        mode.value.startsWith('router-journal-')?'Local retained monitoring journal. Append, decision and removal require explicit confirmation and a current head pin (see AGENT_PROCUREMENT_MONITOR_JOURNAL.md).':
        mode.value.startsWith('router-monitor-')?'Choose the exact monitoring bundle and retained prior snapshot pin (see AGENT_PROCUREMENT_ROUTER_MONITOR.md). No live telemetry or rollback is executed.':
        mode.value.startsWith('router-pilot-')?'Choose a pinned pilot evidence bundle (see AGENT_PROCUREMENT_ROUTER_PILOT.md). This reviews declared observations; it cannot execute or roll back a pilot.':
        mode.value.startsWith('router-change-')?'Choose the exact router version change bundle (see AGENT_PROCUREMENT_ROUTER_CHANGE.md). Referral is not production authorization.':
        ['router-freeze','router-review'].includes(mode.value)?'Choose the exact routing rehearsal bundle (see AGENT_PROCUREMENT_ROUTER_REHEARSAL.md). Predictions and paired outcomes are declarations, not observed live routing.':
        ['deployment-review','deployment-decide'].includes(mode.value)?'Choose the deployment readiness bundle (see AGENT_PROCUREMENT_DEPLOYMENT.md). Existing monitoring records are read locally; no release changes are made.':
        ['outcome-review','rollout-review'].includes(mode.value)?'Choose the outcome review bundle (see AGENT_PROCUREMENT_OUTCOMES.md). No rollout or execution permission will be granted.':
        'Choose the exact selection bundle (see AGENT_PROCUREMENT_SELECTION.md). Nomination and decision require a current saved revision and explicit confirmation in the document.';
      clearProcurementReview();
    });
    const output=el('div');output.id='owgCatalogProcurementResult';output.setAttribute('aria-live','polite');
    const run=button('Inspect procurement evidence against saved revision',()=>locked(async()=>{
      clearProcurementReview();
      const generation=procurementGeneration,selected=record,operation=mode.value;
      const file=documentFile.files?.[0],plan=planFile.files?.[0],cap=8*1024*1024;
      if(!file||file.size>cap||(operation==='review'&&(!plan||plan.size>cap)))throw new Error('Choose the required JSON file(s), at most 8 MiB each.');
      const payload={document:await file.text()};
      if(operation==='review')payload.plan=await plan.text();
      if(generation!==procurementGeneration||record!==selected||!output.isConnected)return;
      const query=new URLSearchParams({workflow_id:selected.workflow_id,revision:String(selected.revision)});
      const envelope=await request('/v1/process-catalog/procurement/'+operation+'?'+query,payload);
      if(generation!==procurementGeneration||record!==selected||!output.isConnected)return;
      const result=envelope.result;
      const selectionMode=['nominate','assess','decide'].includes(operation);
      const outcomeMode=['outcome-review','rollout-review'].includes(operation);
      const deploymentMode=['deployment-review','deployment-decide'].includes(operation);
      const economicsMode=operation==='operating-cost';
      const routerMode=['router-freeze','router-review'].includes(operation);
      const routerChangeMode=operation.startsWith('router-change-');
      const routerPilotMode=operation.startsWith('router-pilot-');
      const routerMonitorMode=operation.startsWith('router-monitor-');
      const routerJournalMode=operation.startsWith('router-journal-')||operation==='router-finance-reconcile';
      const routerProducerMode=operation.startsWith('router-producer-');
      const heading={'router-finance-reconcile':'Retained finance reconciliation','router-producer-grant':'Scoped monitoring producer credential','router-producer-revoke':'Monitoring producer revoked','router-journal-list':'Retained monitoring journals','router-journal-append':'Retained monitoring checkpoint','router-journal-refresh':'Clock-refreshed monitoring checkpoint','router-journal-schedule':'Scheduled review consent','router-journal-detail':'Retained monitoring history','router-journal-decide':'Retained monitoring decision','router-journal-forget':'Monitoring journal removal','router-monitor-freeze':'Frozen multi-window router monitoring','router-monitor-review':'Cumulative router monitoring review','router-monitor-decide':'Monitoring review referral receipt','router-pilot-freeze':'Frozen controlled router pilot','router-pilot-review':'Controlled router pilot review','router-pilot-decide':'Pilot review referral receipt','router-change-freeze':'Frozen router version comparison','router-change-assess':'Paired router version assessment','router-change-decide':'Controlled router change receipt','router-freeze':'Frozen router rehearsal plan','router-review':'Held-out router rehearsal review','operating-cost':'Human and AI operating cost comparison',nominate:'Frozen candidate nomination',assess:'Held-out selection assessment',decide:'Rehearsal decision receipt','outcome-review':'Controlled sandbox outcome review','rollout-review':'Separate rollout review receipt','deployment-review':'Monitored deployment readiness','deployment-decide':'Release-owner referral receipt'};
      output.appendChild(el('h5',(selectionMode||outcomeMode||deploymentMode||economicsMode||routerMode||routerChangeMode||routerPilotMode||routerMonitorMode||routerJournalMode||routerProducerMode)?heading[operation]:operation==='freeze'?'Frozen trial plan · '+result.scheduled_attempts+' attempts':'Candidate procurement comparison'));
      output.appendChild(el('p','Exact saved revision '+envelope.revision+'. '+(envelope.references_latest_revision_at_inspection?
        'Latest saved revision at inspection.':'Historical revision; current revision '+envelope.latest_revision_at_inspection+'. Evaluate the current design before substitution.')));
      if(operation==='freeze'){
        output.appendChild(el('p',result.basis.candidates.length+' candidates · '+result.basis.repeats+' repeats · '+result.basis.reference_report.case_count+' cases. Planned order only; no execution was authorized.'));
        const list=el('ul');
        for(const c of result.basis.candidates)list.appendChild(el('li',c.candidate_id+' · '+c.provider+' / '+c.model+' / '+c.runtime));
        output.appendChild(list);
      }else if(routerProducerMode){
        if(operation==='router-producer-grant'){
          output.append(el('p','Grant '+result.grant.grant_id+' · '+result.grant.producer_alias+' · '+result.grant.window_id+' / '+result.grant.arm),
            el('p','Expires '+new Date(result.grant.expires_ms).toISOString()),
            el('p','Copy this write-only bearer token to the authorized local producer now. It cannot be retrieved again. Do not include it in reports, URLs or logs.'));
          const secret=el('input');secret.type='password';secret.readOnly=true;secret.value=result.producer_token;secret.autocomplete='off';secret.setAttribute('aria-label','One-time monitoring producer token');
          const reveal=button('Show or hide producer token',()=>{secret.type=secret.type==='password'?'text':'password';});
          output.append(secret,reveal,button('Copy producer token',async()=>{await navigator.clipboard.writeText(secret.value);}),el('p','Current journal head: '+result.head_sha256));
        }else output.append(el('p','Grant '+result.grant_id+' revoked. Existing evidence is retained.'),el('p','Current journal head: '+result.head_sha256));
      }else if(routerJournalMode){
        if(operation==='router-journal-list'){
          const ul=el('ul');
          for(const r of result.journals){
            const item=el('li',r.monitor_id+' · '+r.entry_count+' audit entries · '+r.checkpoint_count+' checkpoints');
            item.append(el('p','Current head: '+r.head_sha256),el('p',r.eligible_for_continued_monitoring_review?'Eligible for continuation review':'Continuation blocked or incomplete'));
            ul.appendChild(item);
          }
          output.append(el('p',result.journals.length+' journals for this saved revision'),ul);
        }else if(operation==='router-journal-forget'){
          output.append(el('p','Removed local journal '+result.monitor_id+'. Existing exports and backups are not erased by this operation.'));
        }else{
          output.append(el('p',result.monitor_id+' · '+result.checkpoint_count+' checkpoints / '+result.entry_count+' audit entries'),
            el('p','Current head: '+result.head_sha256),
            el('p','Ordinary audit entries remaining: '+result.ordinary_entries_remaining+' · reserved disable entries: '+result.reserved_disable_entries),
            el('p',result.eligible_for_continued_monitoring_review?'Eligible for separate continuation review.':'Retained evidence blocks continuation or remains incomplete.'),
            el('p','Rollback referral previously recorded: '+(result.rollback_referral_recorded?'yes':'no')),
            el('p','Sticky rollback triggers: '+(result.sticky_rollback_triggers.map(x=>(x.window_id||'cumulative')+': '+x.reason).join(' · ')||'none recorded')),
            el('p','Historical overdue windows: '+(result.overdue_window_history.join(', ')||'none recorded')),
            el('p','Latest snapshot: '+result.latest_report.snapshot_sha256+' · '+new Date(result.latest_report.as_of_ms).toISOString()),
            el('p',(result.credential_authenticated_observations||0)+' observations submitted with scoped producer credentials; this does not verify their outcomes.'));
          output.appendChild(el('p','Scheduled deadline review consent: '+(result.scheduled_deadline_review_enabled?'enabled':'disabled')+'. The local scheduler must also be enabled.'));
          const grants=el('ul');for(const g of result.producer_grants||[])grants.appendChild(el('li',g.grant_id+' · '+g.producer_alias+' · '+g.window_id+' / '+g.arm+' · '+(g.revoked?'revoked':g.expires_ms<=Date.now()?'expired':'active')));output.append(el('h6','Producer grants'),grants);
          const money=(v,currency)=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n,r=(2n*a+d)/(2n*d);
            return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+currency;
          };
          const integerMoney=(v,currency)=>money({numerator:v,denominator:'1'},currency);
          output.appendChild(el('h6','Declared financial reconciliations'));
          if(!(result.finance_reconciliations||[]).length)output.appendChild(el('p','No retained finance statements.'));
          for(const f of result.finance_reconciliations||[]){
            const section=el('section');section.append(el('h6',f.window_id+' · '+f.accounting_basis+' basis'),
              el('p','Pinned monitoring report: '+f.monitor_report_sha256),
              el('p','Finance reviewer: '+f.reviewer_alias+' · statement '+f.reconciliation_sha256),
              el('p','Challenger minus incumbent declared net per enrolled case: '+money(f.challenger_minus_incumbent_booked_net_per_case,f.currency)+' (descriptive, not causal savings)'));
            for(const [name,a] of Object.entries(f.arms)){
              section.append(el('p',name+' · '+a.recorded_cases+'/'+a.scheduled_cases+' telemetry records · booked revenue '+integerMoney(a.declared_booked_revenue_microunits,f.currency)+' · booked cost '+integerMoney(a.declared_booked_cost_microunits,f.currency)+' · booked net '+integerMoney(a.declared_booked_net_microunits,f.currency)),
                el('p','Telemetry direct cost: '+money(a.telemetry_direct_cost.lower_microunits,f.currency)+' to '+money(a.telemetry_direct_cost.upper_microunits,f.currency)+'; not added to booked cost'),
                el('p','Diagnostic provider/external booking minus telemetry direct cost: '+money(a.booked_provider_external_minus_telemetry_direct.lower_microunits,f.currency)+' to '+money(a.booked_provider_external_minus_telemetry_direct.upper_microunits,f.currency)));
            }
            section.appendChild(el('p','Financial documents and reviewer identity are not independently verified. Historical reconciliation is not rewritten by later observations.'));
            output.appendChild(section);
          }
          const windows=el('ul');for(const w of result.latest_report.windows)windows.appendChild(el('li',w.window_id+' · '+w.state.replaceAll('_',' ')+' · '+w.recorded_cases+'/'+w.scheduled_cases+' recorded'));
          output.append(el('h6','Latest window results'),windows,el('h6','Chained local audit history'));
          const history=el('ul');for(const e of result.history){
            const item=el('li',e.sequence+' · '+e.kind+' · '+new Date(e.recorded_at_ms).toISOString());
            item.append(el('p','Entry: '+e.entry_sha256),el('p',e.kind==='decision'?e.details.disposition.replaceAll('_',' ')+' · '+e.details.reviewer_alias:e.kind==='producer_grant'||e.kind==='producer_revoke'?'Grant '+e.details.grant_id:e.kind==='producer_checkpoint'?'Producer '+e.details.producer_alias+' · '+e.details.accepted_observations+' accepted observations':e.kind==='finance_reconciliation'?'Finance statement '+e.details.window_id+' · '+e.details.reconciliation_sha256:e.kind==='schedule_consent'?'Scheduled deadline review '+(e.details.enabled?'enabled':'disabled'):e.kind==='outcome_corroboration'?'Separate review '+e.details.window_id+' · '+e.details.reviewed_cases+' cases':e.kind==='corroboration_required'?'Full corroboration required':e.kind==='source_outcome_bind'?'Source bindings '+e.details.bound_cases:e.kind==='source_outcome_check'?'Source checks '+e.details.confirmed_cases+'/'+e.details.checked_cases:'Snapshot '+e.details.snapshot_sha256+' · '+e.details.recorded_cases+' recorded cases'));
            history.appendChild(item);
          }
          output.append(history,el('p','Locally retained declarations, not authenticated production telemetry or a tamper-proof external audit log. No deployment or rollback performed.'));
          installJournalWorkbench(output,{workflow_id:selected.workflow_id,revision:selected.revision});
        }
      }else if(routerMonitorMode){
        if(operation==='router-monitor-freeze'){
          output.append(el('p',result.scheduled_cases+' distinct scheduled cases · '+result.windows.length+' frozen windows'),
            el('p','Retain the exact plan and approval reference before external monitoring. This is not a traffic assignment service.'),
            el('p','Rollback target: '+result.rollback_target_configuration_sha256));
          const list=el('ul');
          for(const w of result.windows)list.appendChild(el('li',w.window_id+' · '+w.plan.slots.length+' cases · '+new Date(w.plan.request.window_start_ms).toISOString()+' → '+new Date(w.plan.request.window_end_exclusive_ms).toISOString()+' (exclusive)'));
          output.appendChild(list);
        }else if(operation==='router-monitor-decide'){
          output.append(el('p','Disposition: '+result.request.disposition.replaceAll('_',' ')),
            el('p','Reviewer alias: '+result.request.reviewer_alias+' (identity not independently verified)'),
            el('p','Rollback target: '+result.rollback_target_configuration_sha256),
            el('p','No deployment changed or rollback executed.'));
        }else{
          const money=v=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n,r=(2n*a+d)/(2n*d);
            return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+result.currency;
          };
          const range=v=>money(v.lower_microunits)+' to '+money(v.upper_microunits);
          const counts=v=>v.lower+'–'+v.upper;
          output.append(el('p','Recommended review: '+result.recommended_review.replaceAll('_',' ')),
            el('p',result.recorded_cases+' recorded / '+result.started_window_cases+' started-window cases / '+result.scheduled_cases+' total scheduled cases'),
            el('p',result.completed_passing_windows+' of '+result.windows.length+' windows completed with passing checks'),
            el('p','Snapshot as of '+new Date(result.as_of_ms).toISOString()),
            el('p',result.prior_checkpoint_extension_verified?'Retained prior snapshot extension verified.':'No retained prior snapshot supplied.'),
            el('p','Current snapshot pin: '+result.snapshot_sha256),
            el('p','Previously overdue windows: '+(result.overdue_windows_at_previous_checkpoint.join(', ')||'none reported at supplied checkpoint')),
            el('p','Rollback review triggers: '+(result.rollback_review_triggers.map(x=>(x.window_id||'cumulative')+': '+x.reason).join(' · ')||'none established')),
            el('p','Prior checkpoint triggers: '+(result.rollback_triggers_at_previous_checkpoint.map(x=>(x.window_id||'cumulative')+': '+x.reason).join(' · ')||'none supplied')));
          const showArm=(name,a)=>el('p',name+': '+a.terminal+'/'+a.cases+' terminal · success '+counts(a.counts.success)+' · mean cost '+range(a.mean_variable_cost));
          output.appendChild(el('h6','Cumulative started-window results'));
          for(const [name,a] of Object.entries(result.arms))output.appendChild(showArm(name,a));
          for(const c of result.cumulative_category_reviews){
            const section=el('section');section.append(el('h6',c.category.replaceAll('_',' ')+' · '+c.status),showArm('Incumbent',c.incumbent),showArm('Challenger',c.challenger),el('p','Mean variable savings: '+range(c.mean_variable_cost_savings)));output.appendChild(section);
          }
          output.appendChild(el('h6','Closed-window cumulative prefixes'));
          const prefixes=el('ul');for(const p of result.closed_prefix_checks)prefixes.appendChild(el('li',p.through_window_id+' · '+p.status+' · '+Object.entries(p.category_status).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')));output.appendChild(prefixes);
          output.appendChild(el('h6','Individual monitoring windows'));
          for(const w of result.windows){
            const section=el('section');section.append(el('h6',w.window_id+' · '+w.state.replaceAll('_',' ')),
              el('p',w.recorded_cases+' / '+w.scheduled_cases+' recorded · '+new Date(w.start_ms).toISOString()+' → '+new Date(w.end_exclusive_ms).toISOString()));
            if(w.checks){
              const ul=el('ul');for(const [k,v] of Object.entries(w.checks))ul.appendChild(el('li',k.replaceAll('_',' ')+': '+v));section.appendChild(ul);
              for(const c of w.category_reviews)section.appendChild(el('p',c.category.replaceAll('_',' ')+': '+c.status+' · mean variable savings '+range(c.mean_variable_cost_savings)));
            }
            output.appendChild(section);
          }
          output.appendChild(el('p','Keep the snapshot input and its exported pin for the next checkpoint. No independently persisted history, authenticated telemetry or causal effect is established.'));
        }
      }else if(routerPilotMode){
        if(operation==='router-pilot-freeze'){
          output.append(el('p',result.slots.length+' allocated distinct cases · pilot '+result.request.pilot_id),
            el('p','Declared window (UTC): '+new Date(result.request.window_start_ms).toISOString()+' to '+new Date(result.request.window_end_exclusive_ms).toISOString()+' (exclusive)'),
            el('p','Pinned rollback target: '+result.request.rollback_target_configuration_sha256),
            el('p','External pilot approval reference is declared, not verified. Allocation is an offline artifact, not an instruction to execute.'));
          const list=el('ul');
          for(const category of ['normal','exception','missing_facts']){
            const group=result.slots.filter(s=>s.category===category);
            list.appendChild(el('li',category.replaceAll('_',' ')+': '+group.filter(s=>s.arm==='incumbent').length+' incumbent / '+group.filter(s=>s.arm==='challenger').length+' challenger'));
          }
          output.appendChild(list);
        }else if(operation==='router-pilot-decide'){
          output.append(el('p','Disposition: '+result.request.disposition.replaceAll('_',' ')),
            el('p','Reviewer alias: '+result.request.reviewer_alias+' (identity not independently verified)'),
            el('p','Pinned rollback target: '+result.rollback_target_configuration_sha256),
            el('p','No deployment changed or rollback executed.'));
        }else{
          const money=v=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n,r=(2n*a+d)/(2n*d);
            return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+result.currency;
          };
          const range=v=>money(v.lower_microunits)+' to '+money(v.upper_microunits);
          const counts=v=>v.lower+'–'+v.upper;
          output.append(el('p','Recommended review: '+result.recommended_review.replaceAll('_',' ')),
            el('p',result.recorded_cases+' of '+result.scheduled_cases+' distinct cases recorded'),
            el('p','Rollback review triggers: '+(result.rollback_review_triggers.join(', ')||'none established')),
            el('p','Pinned rollback target: '+result.rollback_target_configuration_sha256));
          const showArm=(name,a)=>el('p',name+': '+a.terminal+'/'+a.cases+' terminal · success '+counts(a.counts.success)+' · critical '+counts(a.counts.critical)+' · forbidden '+counts(a.counts.forbidden)+' · mean variable cost '+range(a.mean_variable_cost)+' · attribution '+a.attribution_counts.pass+' pass / '+a.attribution_counts.fail+' fail / '+a.attribution_counts.unknown+' unknown');
          for(const [name,a] of Object.entries(result.arms))output.appendChild(showArm(name,a));
          const gates=el('ul');for(const [k,v] of Object.entries(result.checks))gates.appendChild(el('li',k.replaceAll('_',' ')+': '+v));
          output.append(el('h6','Frozen pilot checks'),gates);
          for(const c of result.category_reviews){
            const section=el('section');section.append(el('h6',c.category.replaceAll('_',' ')+' · '+c.status),
              showArm('Incumbent',c.incumbent),showArm('Challenger',c.challenger),
              el('p','Mean variable savings: '+range(c.mean_variable_cost_savings)));
            const cg=el('ul');for(const [k,v] of Object.entries(c.checks))cg.appendChild(el('li',k.replaceAll('_',' ')+': '+v));
            section.appendChild(cg);output.appendChild(section);
          }
          const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show pilot problems or unresolved observations');
          const label=el('label',' Show pilot problems or unresolved observations');label.prepend(filter);
          const view=el('div');let offset=0;
          const draw=()=>{
            const rows=filter.checked?result.rows.filter(r=>r.attribution!=='pass'||r.status!=='completed'||r.success.lower!==1||r.critical.upper||r.forbidden.upper||r.manual_leakage.upper||r.route_deviation.upper):result.rows;
            const list=el('ul');
            for(const r of rows.slice(offset,offset+25)){
              const item=el('li',r.case_token_sha256+' · '+r.arm+' · '+r.category+' · '+r.status);
              item.append(el('p','Attribution: '+r.attribution+' · actual destination: '+(r.actual_candidate_id||r.actual_destination||'unknown')),
                el('p','Success '+counts(r.success)+' · critical '+counts(r.critical)+' · forbidden '+counts(r.forbidden)+' · variable cost '+range(r.variable_cost)),
                el('p','Source evidence reference: '+(r.source_evidence_sha256||'none')));
              list.appendChild(item);
            }
            const nav=el('div');if(offset)nav.appendChild(button('Previous pilot page',()=>{offset=Math.max(0,offset-25);draw();}));
            if(offset+25<rows.length)nav.appendChild(button('Next pilot page',()=>{offset+=25;draw();}));
            view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length+' pilot rows':'No matching pilot rows.'),list,nav);
          };
          filter.addEventListener('change',()=>{offset=0;draw();});output.append(el('h6','Declared pilot evidence'),label,view);draw();
          output.appendChild(el('p','Disjoint-arm comparisons are descriptive, not causal estimates. No independent execution, approval or source evidence verification is established.'));
        }
      }else if(routerChangeMode){
        if(operation==='router-change-freeze'){
          output.append(el('p',result.paired_attempts+' paired scheduled predictions · portfolio '+result.request.portfolio_id),
            el('p','Incumbent configuration: '+result.request.incumbent_configuration_sha256),
            el('p','Challenger configuration: '+result.request.challenger_configuration_sha256),
            el('p','Retain the frozen comparison before collecting either ledger. No independent chronology or production execution is established.'));
        }else if(operation==='router-change-decide'){
          output.append(el('p','Disposition: '+result.request.disposition.replaceAll('_',' ')),
            el('p','Reviewer alias: '+result.request.reviewer_alias+' (identity not independently verified)'),
            el('p','Referred configuration: '+(result.referred_configuration_sha256||'none')),
            el('p','No deployment changed. A referral is not rollout authorization.'));
        }else{
          const money=v=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n,r=(2n*a+d)/(2n*d);
            return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+result.currency;
          };
          const range=v=>money(v.lower_microunits)+' to '+money(v.upper_microunits);
          const counts=v=>v.lower+'–'+v.upper;
          output.append(el('p',result.eligible_for_controlled_change_review?'Eligible for separate controlled change review.':'Frozen checks block positive referral.'),
            el('p',result.paired_attempts+' paired predictions · incumbent gates '+result.incumbent_absolute_gate_status+' · challenger gates '+result.challenger_absolute_gate_status),
            el('p','Recurring switching savings: '+range(result.annual_recurring_switch_savings)),
            el('p','First-year switching savings: '+range(result.first_year_switch_savings)),
            el('p','Changed destinations: '+counts(result.observed_counts.changed_destination)+' · new outcome regressions: '+counts(result.observed_counts.new_outcome_regression)+' · new manual leakage: '+counts(result.observed_counts.new_manual_leakage)));
          const checks=el('ul');for(const [k,v] of Object.entries(result.checks))checks.appendChild(el('li',k.replaceAll('_',' ')+': '+v));
          output.append(el('h6','Frozen change checks'),checks);
          for(const c of result.category_reviews){
            const section=el('section');section.append(el('h6',c.category.replaceAll('_',' ')+' · '+c.status),
              el('p',c.distinct_cases+' distinct cases · '+c.attempts+' paired attempts'),
              el('p','Success change: '+counts(c.counts.success_change)+' · new regressions: '+counts(c.counts.new_outcome_regression)+' · new critical: '+counts(c.counts.new_critical)));
            const cg=el('ul');for(const [k,v] of Object.entries(c.checks))cg.appendChild(el('li',k.replaceAll('_',' ')+': '+v));
            section.appendChild(cg);output.appendChild(section);
          }
          const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show changed or potentially regressed routes');
          const label=el('label',' Show changed or potentially regressed routes');label.prepend(filter);
          const view=el('div');let offset=0;
          const draw=()=>{
            const rows=filter.checked?result.rows.filter(r=>r.changed_destination.upper||r.new_outcome_regression.upper||r.new_critical.upper||r.new_non_delivery.upper||r.new_manual_leakage.upper||r.new_fallback.upper):result.rows;
            const list=el('ul');
            for(const r of rows.slice(offset,offset+25)){
              const destination=(ids,manual)=>[...ids,...(manual?['manual']:[])].join(', ');
              const item=el('li',r.case_id+' · repeat '+r.repeat+' · '+r.incumbent_status+' → '+r.challenger_status);
              item.append(el('p','Possible incumbent paths: '+destination(r.incumbent_possible_candidate_ids,r.incumbent_manual_path_possible)),
                el('p','Possible challenger paths: '+destination(r.challenger_possible_candidate_ids,r.challenger_manual_path_possible)),
                el('p','Paired variable savings: '+range(r.paired_variable_cost_savings)+' · new regression: '+counts(r.new_outcome_regression)+' · new leakage: '+counts(r.new_manual_leakage)));
              list.appendChild(item);
            }
            const nav=el('div');
            if(offset)nav.appendChild(button('Previous router change page',()=>{offset=Math.max(0,offset-25);draw();}));
            if(offset+25<rows.length)nav.appendChild(button('Next router change page',()=>{offset+=25;draw();}));
            view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length+' paired change rows':'No matching change rows.'),list,nav);
          };
          filter.addEventListener('change',()=>{offset=0;draw();});
          output.append(el('h6','Paired change evidence'),label,view);draw();
          output.appendChild(el('p','Identical paths reuse shared counterfactual evidence. Outcomes are declarations, switching costs are assumptions, and no actual routed executions or reviewer identity are verified.'));
        }
      }else if(routerMode){
        if(operation==='router-freeze'){
          output.append(el('p',result.scheduled_attempts+' scheduled router predictions · portfolio '+result.request.portfolio_id),
            el('p','Retain the exact plan before obtaining predictions. The separate input-only challenge is available through the local CLI. The plan does not establish chronology.'));
          const routes=el('ul');
          for(const [category,candidate] of Object.entries(result.assignments))routes.appendChild(el('li',category.replaceAll('_',' ')+' → '+(candidate||'manual')));
          output.appendChild(routes);
        }else{
          const money=v=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n;
            const r=(2n*a+d)/(2n*d);
            return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+result.currency;
          };
          const range=v=>money(v.lower_microunits)+' to '+money(v.upper_microunits);
          const counts=v=>v.lower+'–'+v.upper;
          output.append(el('p','Portfolio '+result.portfolio_id+' · gate status: '+result.gate_status),
            el('p',result.recorded_attempts+' of '+result.scheduled_attempts+' predictions recorded · '+Object.entries(result.status_counts).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')),
            el('p','Category accuracy count: '+counts(result.observed_counts.accuracy)+' · destination agreement: '+counts(result.observed_counts.agreement)+' · manual leakage: '+counts(result.observed_counts.manual_leakage)),
            el('p','Declared outcome regressions: '+counts(result.observed_counts.regression)+' · shadow critical: '+counts(result.observed_counts.critical)+' · non-delivery: '+counts(result.observed_counts.non_delivery)),
            el('p','Conditional annual cost: '+range(result.annual_cost)+' · first-year savings: '+range(result.first_year_savings)),
            el('p','Annual signed variable cost difference from intended paths: '+range(result.annual_cost_difference_from_ideal_path)),
            el('p','Including router effort and direct charges: '+range(result.annual_cost_difference_from_ideal_including_router)+' (excludes fixed charges)'),
            el('p','Router p95 latency: '+result.router_latency.p95_lower_ms+' to '+(result.router_latency.p95_upper_ms==null?'unbounded':result.router_latency.p95_upper_ms)+' ms · all scheduled predictions'),
            el('p','Manual fixed costs retained. All configured agents charged. Financial evidence: '+(result.financial_evidence_complete?'complete for these assumptions':'incomplete')));
          const gates=el('ul');for(const [key,value] of Object.entries(result.checks))gates.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
          output.append(el('h6','Frozen policy checks'),gates);
          const matrix=el('ul');for(const [cat,values] of Object.entries(result.confusion_counts)){
            const item=el('li',cat.replaceAll('_',' ')+': '+Object.entries(values).filter(([,v])=>v).map(([k,v])=>k.replaceAll('_',' ')+' '+v).join(' · '));
            matrix.appendChild(item);
          }
          output.append(el('h6','Classification confusion counts (including failed and missing predictions)'),matrix);
          for(const c of result.category_reviews){
            const section=el('section');section.append(el('h6',c.category.replaceAll('_',' ')+' · '+c.status),
              el('p',c.distinct_cases+' distinct cases · '+c.attempts+' attempts · declared annual volume '+c.annual_cases),
              el('p','Category accuracy: '+counts(c.counts.accuracy)+' · manual leakage: '+counts(c.counts.manual_leakage)+' · outcome regressions: '+counts(c.counts.regression)));
            const cg=el('ul');for(const [key,value] of Object.entries(c.checks))cg.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
            section.appendChild(cg);output.appendChild(section);
          }
          const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show router problems or unresolved predictions');
          const label=el('label',' Show router problems or unresolved predictions');label.prepend(filter);
          const view=el('div');let offset=0;
          const draw=()=>{
            const rows=filter.checked?result.rows.filter(r=>r.route_unresolved||r.accuracy.lower!==1||r.manual_leakage.upper||r.regression.upper||r.critical.upper||r.non_delivery.upper):result.rows;
            const list=el('ul');
            for(const r of rows.slice(offset,offset+25)){
              const item=el('li',r.case_id+' · repeat '+r.repeat+' · '+r.status+' · expected '+r.true_category+' / predicted '+(r.predicted_category||'none'));
              item.append(el('p','Intended: '+(r.intended_candidate_id||'manual')+' · possible destinations: '+[...r.possible_candidate_ids,...(r.manual_path_possible?['manual']:[])].join(', ')),
                el('p','Router cost: '+range(r.router_cost)+' · paired savings: '+range(r.paired_savings)),
                el('p','Variable cost difference from intended path: '+range(r.cost_difference_from_ideal_path)),
                el('p','Manual leakage: '+counts(r.manual_leakage)+' · declared regression: '+counts(r.regression)+' · shadow critical: '+counts(r.critical)));
              list.appendChild(item);
            }
            const nav=el('div');
            if(offset)nav.appendChild(button('Previous router case page',()=>{offset=Math.max(0,offset-25);draw();}));
            if(offset+25<rows.length)nav.appendChild(button('Next router case page',()=>{offset+=25;draw();}));
            view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length+' routing rows':'No matching routing rows.'),list,nav);
          };
          filter.addEventListener('change',()=>{offset=0;draw();});
          output.append(el('h6','Paired routing evidence'),label,view);draw();
        }
        output.appendChild(el('p','No actual routed execution or pre-execution prediction provenance was verified. Known router failures use a declared manual fallback counterfactual; missing predictions are unresolved.'));
      }else if(economicsMode){
        // Exact amounts use decimal strings. Round only the displayed value,
        // never the downloaded report or the evidence hash.
        const money=v=>{
          if(v==null)return 'unbounded';
          const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n;
          const rounded=(a*2n+d)/(2n*d),whole=rounded/1000000n,part=String(rounded%1000000n).padStart(6,'0');
          return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(whole)+'.'+part+' '+result.currency;
        };
        const range=v=>money(v.lower_microunits)+' to '+money(v.upper_microunits);
        const human=(data)=>{
          const o=data.declared_business_outcomes,t=data.human_touched_attempts;
          return data.complete_cost_attempts+' of '+data.attempts+' attempts with complete costs · human-touched attempts '+t.lower+'–'+t.upper+
            ' · declared business outcomes: '+o.declared_success+' success, '+o.declared_failure+' failure, '+o.unresolved_or_missing+' unresolved/missing';
        };
        const manual=result.manual_baseline;
        output.append(el('h6','Current human-led baseline'),el('p','Total enrolled cost: '+range(manual.total_cost)),el('p',human(manual)),el('p','Cost per declared successful task: '+(manual.cost_per_declared_success.status==='no_possible_success'?'undefined (no possible success)':range(manual.cost_per_declared_success))));
        output.appendChild(el('p','Annual scenario: '+result.scenario.annual_cases+' cases. Conditional estimate, not verified realized savings or deployment approval.'));
        for(const c of result.candidates){
          const box=el('section');box.append(el('h6',c.candidate_id+' · trial gates: '+c.trial_gate_status),
            el('p','All-attempt operating cost: '+range(c.candidate.total_cost)),
            el('p','Paired savings versus manual: '+range(c.paired_trial_savings)),
            el('p','Full-replacement recurring savings (all manual fixed cost removed): '+range(c.annual_recurring_savings_scenario)),
            el('p','Full-replacement first-year savings: '+range(c.first_year_savings_scenario)),
            el('p',human(c.candidate)),el('p','Cost per declared successful task: '+(c.candidate.cost_per_declared_success.status==='no_possible_success'?'undefined (no possible success)':range(c.candidate.cost_per_declared_success))));
          const categories=el('ul');
          for(const [key,value] of Object.entries(c.candidate.human_time_by_category)){
            categories.appendChild(el('li',key+': '+value.lower_person_ms+' to '+(value.upper_person_ms==null?'unbounded':value.upper_person_ms)+' person-ms · '+value.known_attempts+' measured attempts'));
          }
          box.appendChild(categories);
          const quality=c.declared_outcome_review,adopt=c.conditional_adoption;
          box.append(el('h6','Declared outcome risk: '+quality.status),
            el('p','Paired outcome regressions: '+quality.regression_count.lower+'–'+quality.regression_count.upper+' of '+quality.paired_attempts+
              ' · success-count change: '+quality.success_count_change.lower+' to '+quality.success_count_change.upper),
            el('p','Declared outcome thresholds: minimum success '+(quality.minimum_declared_success_basis_points/100)+'%, maximum paired regression '+(quality.maximum_declared_regression_basis_points/100)+'%. Not independent outcome verification.'));
          const checks=el('ul');for(const [key,value] of Object.entries(quality.checks))checks.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
          box.appendChild(checks);
          const groups=el('ul');
          for(const g of c.category_reviews){
            const item=el('li',g.category.replaceAll('_',' ')+' · '+g.distinct_cases+' distinct cases · '+g.candidate.attempts+' enrolled attempts');
            item.append(el('p','Paired savings: '+range(g.paired_savings)),
              el('p','Declared outcome risk: '+g.declared_outcome_review.status+' · regressions '+g.declared_outcome_review.regression_count.lower+'–'+g.declared_outcome_review.regression_count.upper));
            groups.appendChild(item);
          }
          box.append(el('h6','Normal, exception and missing-facts economics'),groups);
          const volumeText=v=>v==null?'unknown':v.empty?'none':v.minimum_cases+'–'+v.maximum_cases+' cases';
          const yesNo=v=>v==null?'unknown':v?'yes':'no';
          const frontier=adopt.first_year_positive_volume;
          box.append(el('h6','Mixed-adoption economics'),
            el('p','Manual fixed costs remain during partial adoption. Only the declared avoidable amount is removed at full adoption.'),
            el('p','First-year positive lower-bound partial volume: '+volumeText(frontier.partial_guaranteed_positive)+
              ' · positive upper-bound partial volume: '+volumeText(frontier.partial_possible_positive)),
            el('p','Full adoption positive lower bound: '+yesNo(frontier.full_guaranteed_positive)+' · positive upper bound: '+yesNo(frontier.full_possible_positive)));
          const volumeList=el('ul');
          for(const row of adopt.rows){
            const item=el('li',row.adopted_cases+' adopted / '+row.manual_cases_remaining+' manual cases');
            item.append(el('p','Recurring savings: '+range(row.annual_recurring_savings)),
              el('p','First-year savings: '+range(row.first_year_savings)));
            const hours=v=>{
              if(v==null)return 'unbounded';
              // Exact report retains person-ms; display rounds to nearest whole person-hour.
              const n=BigInt(v.numerator),d=BigInt(v.denominator)*3600000n,a=n<0n?-n:n;
              return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String((a*2n+d)/(2n*d))+' h';
            };
            item.appendChild(el('p','Conditional released capacity: '+hours(row.annual_released_person_ms.lower)+' to '+hours(row.annual_released_person_ms.upper)+' (not cash savings)'));
            volumeList.appendChild(item);
          }
          box.appendChild(volumeList);output.appendChild(box);
        }
        if(result.conditional_portfolios){
          const p=result.conditional_portfolios;
          output.append(el('h5','Conditional multi-agent portfolios'),
            el('p','Declared annual category volumes: '+Object.entries(p.category_volumes).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')),
            el('p','All-manual annual cost: '+range(p.all_manual_annual_cost)),
            el('p','Retrospective case labels are not proven available before execution. No portfolio is automatically selected or authorized.'));
          const count=v=>{
            if(v==null)return 'unbounded';
            const n=BigInt(v.numerator),d=BigInt(v.denominator);
            return d===1n?String(n):String(n)+'/'+String(d);
          };
          const countRange=v=>count(v.lower)+' to '+count(v.upper);
          for(const plan of p.portfolios){
            const section=el('section');
            section.append(el('h6',plan.portfolio_id+' · declared quality gates: '+plan.declared_evidence_status),
              el('p',plan.ai_cases+' AI-assigned / '+plan.manual_cases+' manual cases · active agents: '+(plan.active_candidate_ids.join(', ')||'none')),
              el('p','Financial evidence: '+(plan.financial_evidence_complete?'complete for these assumptions':'incomplete')+' · capacity evidence: '+(plan.capacity_evidence_complete?'complete for these assumptions':'incomplete')),
              el('p','Annual cost: '+range(plan.annual_cost)),
              el('p','First-year cost: '+range(plan.first_year_cost)),
              el('p','Recurring savings: '+range(plan.recurring_savings)),
              el('p','First-year savings: '+range(plan.first_year_savings)),
              el('p','Conditional declared success count: '+countRange(plan.annual_declared_success_count)+' · regression count: '+countRange(plan.annual_declared_regression_count)),
              el('p','Annual released person-ms: '+countRange(plan.annual_released_person_ms)+' (not cash savings)'));
            const gates=el('ul');for(const [key,value] of Object.entries(plan.checks))gates.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
            const routes=el('ul');
            for(const r of plan.routes){
              const item=el('li',r.category.replaceAll('_',' ')+' · '+r.annual_cases+' cases → '+(r.candidate_id||'manual')+' · '+r.status);
              if(r.annual_cases)item.append(el('p',r.observed_distinct_cases+' observed cases / '+r.observed_attempts+' attempts · variable cost: '+range(r.annual_variable_cost)),
                el('p','Declared regression count: '+countRange(r.annual_declared_regression_count)));
              routes.appendChild(item);
            }
            section.append(gates,el('h6','Conditional assignments'),routes);output.appendChild(section);
          }
        }
        const picker=el('select');picker.setAttribute('aria-label','Operating cost candidate');
        for(const c of result.candidates){const option=el('option',c.candidate_id);option.value=c.candidate_id;picker.appendChild(option);}
        const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show incomplete operating cost evidence');
        const label=el('label',' Show incomplete operating cost evidence');label.prepend(filter);
        const view=el('div');let offset=0;
        const draw=()=>{
          const candidate=result.candidates.find(c=>c.candidate_id===picker.value);
          const rows=filter.checked?candidate.rows.filter(r=>r.manual_missing.length||r.candidate_missing.length):candidate.rows;
          const list=el('ul');
          for(const r of rows.slice(offset,offset+25)){
            const line=el('li',r.case_id+' · '+r.case_category.replaceAll('_',' ')+' · repeat '+r.repeat+' · '+r.trial_status+' · savings: '+range(r.paired_savings));
            line.appendChild(el('p','Manual missing: '+(r.manual_missing.join(', ')||'none')+' · AI missing: '+(r.candidate_missing.join(', ')||'none')));
            line.appendChild(el('p','Declared outcomes: '+(r.manual_declared_outcome||'unknown')+' → '+(r.candidate_declared_outcome||'unknown')+' · regression '+r.declared_outcome_comparison.regression.lower+'–'+r.declared_outcome_comparison.regression.upper));
            list.appendChild(line);
          }
          const nav=el('div');
          if(offset)nav.appendChild(button('Previous operating cost page',()=>{offset=Math.max(0,offset-25);draw();}));
          if(offset+25<rows.length)nav.appendChild(button('Next operating cost page',()=>{offset+=25;draw();}));
          view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length+' cost rows':'No matching cost rows.'),list,nav);
        };
        picker.addEventListener('change',()=>{offset=0;draw();});filter.addEventListener('change',()=>{offset=0;draw();});
        output.append(el('h6','Paired operating cost evidence'),picker,label,view);draw();
        output.appendChild(el('p','Cost, person-time, human baseline, and business outcome observations are operator declarations. Unknown costs are not zero; shadow matches do not establish realized business success.'));
      }else if(deploymentMode){
        if(operation==='deployment-decide'){
          output.appendChild(el('p','Disposition: '+result.request.decision.replaceAll('_',' ')+'. No production rollout authorized.'));
          output.appendChild(el('p','Pinned release revisions: baseline '+result.baseline_release_revision+' · candidate '+result.candidate_release_revision));
        }else{
          output.appendChild(el('p','Selected UTC window: '+result.window.from+' to '+result.window.until_exclusive+' (exclusive).'));
          for(const arm of ['baseline','candidate']){
            const data=result[arm];output.appendChild(el('h6',arm+' · '+data.reported_stage.replaceAll('_',' ')+' · revision '+data.release_revision));
            output.appendChild(el('p',data.runs+' retained runs · '+data.unattributed_runs+' without exact spec attribution · '+data.step_count+' retained steps'));
            output.appendChild(el('p',Object.entries(data.status_counts).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')));
            output.appendChild(el('p','p95 elapsed: '+(data.p95_elapsed_ms==null?'unknown':data.p95_elapsed_ms+' ms')));
          }
          const checks=el('ul');for(const [key,value] of Object.entries(result.checks))checks.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
          output.appendChild(checks);
          output.appendChild(el('p',result.eligible_for_release_owner_referral?'Eligible for separate release-owner referral.':'Monitoring evidence blocks a positive referral.'));
        }
        output.appendChild(el('p','Reported run states are not independently verified outcomes. Rollback targets are pinned, not tested; no release inventory is changed.'));
      }else if(outcomeMode){
        if(operation==='rollout-review'){
          output.appendChild(el('p','Disposition: '+result.request.decision.replaceAll('_',' ')+'. No production rollout authorized.'));
          output.appendChild(el('p','Rollback plan contents, reviewer identity and execution authority remain unverified.'));
        }else{
          for(const [arm,data] of Object.entries(result.arms)){
            const b=data.success_bounds;output.appendChild(el('h6',arm+' · '+b.lower+'–'+b.upper+' of '+b.total+' successful cases'));
            output.appendChild(el('p','Unknown outcomes: '+b.unknown+' · known forbidden actions: '+data.known_forbidden_actions+' · incomplete action capture: '+data.incomplete_action_capture));
          }
          const checks=el('ul');for(const [key,value] of Object.entries(result.checks))checks.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
          output.appendChild(checks);
          output.appendChild(el('p',result.eligible_to_request_separate_rollout_review?'Eligible to request separate rollout review.':'Outcome evidence blocks a rollout review request.'));
          const view=el('div');let offset=0;const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show failed or unresolved sandbox outcomes');
          const label=el('label',' Show failed or unresolved sandbox outcomes');label.prepend(filter);
          const draw=()=>{
            const rows=filter.checked?result.case_rows.filter(r=>r.success!==true):result.case_rows;
            const list=el('ul');for(const row of rows.slice(offset,offset+25)){
              const line=el('li',row.case_id+' · '+row.arm+' · '+(row.success===true?'pass':row.success===false?'fail':'unresolved')+' · '+row.status);
              if(row.forbidden_action_count!==null)line.appendChild(el('p','Forbidden actions: '+row.forbidden_action_count+' · state matches: '+String(row.state_matches_oracle)));
              for(const [key,delta] of [['Observed delta',row.actual_state_delta],['Expected delta',row.expected_state_delta]]){
                if(delta)line.appendChild(el('p',key+': added '+(delta.added.join(', ')||'none')+' · removed '+(delta.removed.join(', ')||'none')+' · changed '+(delta.changed.join(', ')||'none')));
              }
              list.appendChild(line);
            }
            const nav=el('div');
            if(offset)nav.appendChild(button('Previous sandbox case page',()=>{offset=Math.max(0,offset-25);draw();}));
            if(offset+25<rows.length)nav.appendChild(button('Next sandbox case page',()=>{offset+=25;draw();}));
            view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length:'No matching case rows.'),list,nav);
          };
          filter.addEventListener('change',()=>{offset=0;draw();});output.append(el('h6','Sandbox case evidence'),label,view);draw();
        }
        output.appendChild(el('p','Observer identity, sandbox isolation and real business outcomes are not independently verified.'));
      }else if(selectionMode){
        output.appendChild(el('p','Candidate: '+result.candidate.candidate_id+' · baseline: '+result.baseline_candidate_id));
        if(operation==='decide'){
          output.appendChild(el('p','Disposition: '+result.request.decision.replaceAll('_',' ')+'. No deployment changed.'));
          const prerequisites=el('ul');for(const item of result.remaining_prerequisites)prerequisites.appendChild(el('li',item));
          output.appendChild(el('h6','Remaining prerequisites'),prerequisites);
        }else{
          for(const [name,data] of [['Discovery',result.discovery],['Holdout',result.holdout]]){
            if(!data)continue;
            const box=el('section');box.appendChild(el('h6',name+' · '+(data.all_required_checks_pass?'all checks pass':'not eligible')));
            box.appendChild(el('p',data.distinct_cases+' distinct cases · '+data.repeats+' repeats · '+data.paired_attempts+' paired attempts'));
            const checks=el('ul');for(const [key,value] of Object.entries(data.checks))checks.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
            box.appendChild(checks);output.appendChild(box);
          }
          if(operation==='assess')output.appendChild(el('p',result.eligible_for_rehearsal_review?'Eligible for explicit rehearsal decision.':'Holdout did not meet every requirement; retain baseline or defer.'));
          else output.appendChild(el('p','Retain this exact nomination before inspecting holdout results. The artifact does not prove chronology.'));
        }
        output.appendChild(el('p','Reviewer aliases, independent sampling, provider identity and business outcomes are not independently verified.'));
      }else{
        const rate=v=>v==null?'unknown':(100*v).toFixed(1)+'%';
        for(const c of result.candidates){
          const box=el('section');box.appendChild(el('h6',c.candidate_id+' · '+c.gate_status));
          const match=c.match_rate_bounds?rate(c.match_rate_bounds.lower)+'–'+rate(c.match_rate_bounds.upper):rate(c.match_rate);
          const cost=c.cost.total_microunits==null?'unknown (known lower bound '+((c.cost.lower_bound_microunits??c.cost.known_cost_lower_bound_microunits)/1e6).toFixed(6)+' '+result.currency+')':
            (c.cost.total_microunits/1e6).toFixed(6)+' '+result.currency;
          box.appendChild(el('p','Expectation match: '+match+' · All-attempt cost: '+cost+' · p95 elapsed: '+(c.latency.p95_ms==null?'unknown':c.latency.p95_ms+' ms')));
          if(c.status_counts)box.appendChild(el('p',Object.entries(c.status_counts).map(([k,v])=>k+': '+v).join(' · ')));
          const gates=el('ul');for(const [key,value] of Object.entries(c.gates))gates.appendChild(el('li',key.replaceAll('_',' ')+': '+value));
          box.appendChild(gates);output.appendChild(box);
        }
        output.appendChild(el('p','Fully measured, gate-passing nondominated candidates: '+(result.pareto.candidate_ids.join(', ')||'none')+'. No automatic selection.'));
        const comparisons=result.paired_comparisons||result.comparisons;
        if(comparisons.length){
          const picker=el('select');picker.setAttribute('aria-label','Compare candidate against baseline');
          for(const c of comparisons){const option=el('option',c.candidate_id);option.value=c.candidate_id;picker.appendChild(option);}
          const filter=el('input');filter.type='checkbox';filter.setAttribute('aria-label','Show only possible regressions or critical cases');
          const filterLabel=el('label',' Show only possible regressions or critical cases');filterLabel.prepend(filter);
          const view=el('div');let offset=0;
          const draw=()=>{
            const comparison=comparisons.find(c=>c.candidate_id===picker.value);
            const all=comparison.rows||comparison.cases;
            const rows=filter.checked?all.filter(r=>r.regression_upper||r.new_critical_upper||r.change==='regressed'||r.new_critical):all;
            const list=el('ul');
            for(const r of rows.slice(offset,offset+25)){
              list.appendChild(el('li',r.case_id+(r.repeat?' · repeat '+r.repeat:'')+' · '+(r.baseline_reason||r.baseline_status)+' → '+(r.candidate_reason||r.candidate_status)+
                (r.change?' · '+r.change:' · possible regression: '+r.regression_upper+' · possible new critical: '+r.new_critical_upper)));
            }
            const nav=el('div');if(offset)nav.appendChild(button('Previous case page',()=>{offset=Math.max(0,offset-25);draw();}));
            if(offset+25<rows.length)nav.appendChild(button('Next case page',()=>{offset+=25;draw();}));
            view.replaceChildren(el('p',rows.length?'Showing '+(offset+1)+'–'+Math.min(rows.length,offset+25)+' of '+rows.length+' paired rows':'No matching paired rows.'),list,nav);
          };
          picker.addEventListener('change',()=>{offset=0;draw();});filter.addEventListener('change',()=>{offset=0;draw();});
          output.append(el('h5','Paired case evidence'),picker,filterLabel,view);draw();
        }
      }
      if(operation!=='router-producer-grant')output.appendChild(button('Download minimized procurement JSON',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)+'\n'],{type:'application/json'}));
        const link=el('a');link.href=url;link.download=(selectionMode||outcomeMode||deploymentMode||economicsMode||routerMode||routerChangeMode||routerPilotMode||routerMonitorMode||routerJournalMode||routerProducerMode)?'owg-procurement-'+operation+'.json':operation==='freeze'?'owg-procurement-plan.json':'owg-procurement-review.json';
        link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }));
      output.appendChild(el('p','No model or business tool was called; reported identity, measurements and outcomes remain unverified. This review grants no execution or substitution permission.'));
    }));
    section.append(modeLabel,hint,documentLabel,planLabel,run,
      button('Clear procurement review',()=>{documentFile.value='';planFile.value='';clearProcurementReview();}),output);
    host.appendChild(section);
  }
  function installJournalWorkbench(host,scope,initialId=''){
    const section=el('details');
    section.append(el('summary','Manage retained monitoring journals'),
      el('p','Exact saved revision '+scope.revision+'. Actions use the latest explicitly displayed audit head. No provider, deployment or business operation is performed.'));
    const output=el('div');output.setAttribute('aria-live','polite');
    let serial=0,working=false;
    const valid=attempt=>attempt===serial&&section.isConnected;
    const call=(op,document)=>request('/v1/process-catalog/procurement/'+op+'?'+new URLSearchParams({workflow_id:scope.workflow_id,revision:String(scope.revision)}),{document:JSON.stringify(document)}).then(e=>e.result);
    const perform=async(fn)=>{
      if(working||!section.isConnected)return;
      working=true;
      try{await fn();}catch(error){output.appendChild(el('p','Action failed: '+error.message+' · reload the journal before retrying.'));}
      finally{working=false;}
    };
    const download=(value,name)=>button('Download '+name,()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));
      const link=el('a');link.href=url;link.download=name+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    const loadList=async()=>{
      const attempt=++serial;
      const value=await call('router-journal-list',{});
      if(!valid(attempt))return;
      output.replaceChildren(el('p',value.journals.length+' retained journals for this revision.'));
      for(const row of value.journals){
        output.appendChild(button('Open journal '+row.monitor_id,()=>perform(()=>loadDetail(row.monitor_id))));
      }
    };
    const loadDetail=async(id)=>{
      const attempt=++serial;
      const value=await call('router-journal-detail',{monitor_id:id});
      if(!valid(attempt))return;
      draw(value);
    };
    const draw=value=>{
      output.replaceChildren(el('h5',value.monitor_id),el('p','Current audit head: '+value.head_sha256),
        el('p',value.entry_count+' audit entries · '+value.ordinary_entries_remaining+' ordinary entries remaining · '+value.reserved_disable_entries+' reserved for disable actions'),
        el('p',value.eligible_for_continued_monitoring_review?'Eligible for separate continuation review':'Continuation blocked or incomplete'),
        el('p','Latest monitoring cutoff: '+new Date(value.latest_report.as_of_ms).toISOString()),
        el('p','Recorded rollback referral: '+(value.rollback_referral_recorded?'yes':'no')+' · sticky incidents: '+value.sticky_rollback_triggers.length));
      const base={monitor_id:value.monitor_id,expected_head_sha256:value.head_sha256,confirmed:true};
      const change=(op,extra,message)=>perform(async()=>{
        if(!window.confirm(message))return;
        const attempt=++serial;
        await call(op,{...base,...extra});
        if(valid(attempt))await loadDetail(value.monitor_id);
      });
      output.append(button('Reload current journal',()=>perform(()=>loadDetail(value.monitor_id))),
        button('Refresh monitoring cutoff',()=>change('router-journal-refresh',{},'Reevaluate retained evidence using the current local clock?')),
        button(value.scheduled_deadline_review_enabled?'Withdraw scheduled review consent':'Enable journal scheduled review consent',()=>change('router-journal-schedule',{enabled:!value.scheduled_deadline_review_enabled},'Change scheduled review consent for this exact retained journal?')),
        download(value,'owg-monitor-journal'));
      const reviewer=el('input');reviewer.maxLength=80;reviewer.setAttribute('aria-label','Monitoring reviewer alias');
      const label=el('label','Distinct reviewer alias ');label.appendChild(reviewer);
      const disposition=el('select');disposition.setAttribute('aria-label','Monitoring review disposition');
      for(const [key,name] of [['hold','Hold'],['refer_for_rollback_review','Refer for rollback review'],['refer_for_continued_monitoring_review','Refer for continuation review']]){
        const opt=el('option',name);opt.value=key;if(key==='refer_for_continued_monitoring_review'&&!value.eligible_for_continued_monitoring_review)opt.disabled=true;disposition.appendChild(opt);
      }
      const dispositionLabel=el('label','Disposition ');dispositionLabel.appendChild(disposition);
      output.append(label,dispositionLabel,button('Record monitoring review decision',()=>change('router-journal-decide',{reviewer_alias:reviewer.value,disposition:disposition.value},'Record this review referral or hold? This does not execute a rollback or deployment.')));
      const corroboration=value.outcome_corroboration;
      const reviewSection=el('details');reviewSection.append(el('summary','Separate outcome corroboration'),
        el('p',corroboration.matched+'/'+corroboration.enrolled+' matched · '+corroboration.reviewed+' reviewed · '+corroboration.conflicts+' conflicts · '+corroboration.source_overlap+' source/alias overlaps'),
        el('p',corroboration.required?'Full corroboration required for continuation.':'Separate corroboration optional; any discovered conflict still blocks continuation.'),
        el('p','Separate declared evidence is not proof of reviewer identity or source-system accuracy.'));
      if(!corroboration.required)reviewSection.appendChild(button('Require complete outcome corroboration',()=>change('router-corroboration-require',{},'Require full outcome corroboration for continuation? This cannot be switched off within this journal.')));
      const reviewWindow=el('select');reviewWindow.setAttribute('aria-label','Closed corroboration window');
      for(const w of value.latest_report.windows){
        if(w.end_exclusive_ms<=value.latest_report.as_of_ms&&w.end_exclusive_ms<=Date.now()){
          const opt=el('option',w.window_id);opt.value=w.window_id;reviewWindow.appendChild(opt);
        }
      }
      const reviewLabel=el('label','Closed window ');reviewLabel.appendChild(reviewWindow);
      const reviewAlias=el('input');reviewAlias.maxLength=80;reviewAlias.setAttribute('aria-label','Separate outcome reviewer alias');
      const aliasLabel=el('label','Reviewer alias ');aliasLabel.appendChild(reviewAlias);
      const reviewFile=el('input');reviewFile.type='file';reviewFile.accept='.json,application/json';reviewFile.setAttribute('aria-label','Separate reviewer outcome rows JSON');
      const reviewPin={expected_report_sha256:value.latest_report.report_sha256};
      reviewSection.append(reviewLabel,button('Prepare blind corroboration manifest',()=>perform(async()=>{
        if(!reviewWindow.value)throw new Error('Choose a closed monitoring window.');
        if(!window.confirm('Export unreviewed opaque case and slot references without arm assignments or producer outcomes?'))return;
        const attempt=++serial;
        const manifest=await call('router-corroboration-manifest',{...base,...reviewPin,window_id:reviewWindow.value});
        if(valid(attempt))reviewSection.appendChild(download(manifest,'owg-corroboration-manifest'));
      })),aliasLabel,el('p','Upload a JSON array of resolved reviewer rows. See AGENT_PROCUREMENT_CORROBORATION.md. Unresolved cases may be omitted and reviewed later.'),reviewFile,
      button('Retain separate reviewer evidence',()=>perform(async()=>{
        if(!reviewWindow.value)throw new Error('Choose a closed monitoring window.');
        const file=reviewFile.files?.[0];if(!file||file.size>512*1024)throw new Error('Choose reviewer rows JSON of at most 512 KiB.');
        // Keep the original JSON array text: do not round or discard duplicate
        // fields in a browser parse before the server validates it.
        const raw=await file.text();
        if(!window.confirm('Retain separate reviewer declarations? Submitted slots cannot be rewritten. Conflicts block continuation.'))return;
        const attempt=++serial;
        const prefix=JSON.stringify({...base,...reviewPin,window_id:reviewWindow.value,reviewer_alias:reviewAlias.value,confirm_separate_source_review:true});
        const document=prefix.slice(0,-1)+',"rows":'+raw+'}';
        await request('/v1/process-catalog/procurement/router-corroboration-record?'+new URLSearchParams({workflow_id:scope.workflow_id,revision:String(scope.revision)}),{document});
        if(valid(attempt))await loadDetail(value.monitor_id);
      })));
      const reviewTriggers=el('ul');for(const x of corroboration.triggers)reviewTriggers.appendChild(el('li',x.window_id+' · '+x.reason+(x.fields?' · '+x.fields.join(', '):'')));
      reviewSection.appendChild(reviewTriggers);output.appendChild(reviewSection);
      const source=value.source_outcomes;
      const sourceSection=el('details');sourceSection.append(el('summary','Read-only source outcome checks'),
        el('p',source.confirmed+'/'+source.bound+' linked source predicates confirmed · '+source.unknown+' unknown · '+source.conflicts.length+' discrepancies'),
        el('p','Supports Stripe invoice payment, Jira issue resolution and GitHub pull request merge in the closed window. Each requires a separately enabled read-only source connection, retained record evidence and explicit case mapping. No source changes are made.'));
      const sourceWindow=el('select');sourceWindow.setAttribute('aria-label','Closed source outcome window');
      for(const w of value.latest_report.windows){
        if(w.end_exclusive_ms<=value.latest_report.as_of_ms&&w.end_exclusive_ms<=Date.now()){
          const opt=el('option',w.window_id);opt.value=w.window_id;sourceWindow.appendChild(opt);
        }
      }
      const sourceWindowLabel=el('label','Closed window ');sourceWindowLabel.appendChild(sourceWindow);
      const mappingFile=el('input');mappingFile.type='file';mappingFile.accept='.json,application/json';mappingFile.setAttribute('aria-label','Source outcome bindings JSON');
      sourceSection.append(sourceWindowLabel,el('p','Use the blind case manifest above to prepare an array of slot_id, case_token_sha256, observed opaque resource_ref and predicate (invoice_paid_in_window, jira_issue_resolved_in_window or github_pr_merged_in_window).'),mappingFile,
        button('Retain source outcome bindings',()=>perform(async()=>{
          const file=mappingFile.files?.[0];if(!sourceWindow.value||!file||file.size>128*1024)throw new Error('Choose a closed window and bindings JSON up to 128 KiB.');
          const raw=await file.text();
          if(!window.confirm('Retain immutable case-to-record mappings? Confirm each exact outcome definition is its selected source predicate within this window. This mapping is your declaration.'))return;
          const attempt=++serial;
          const prefix=JSON.stringify({...base,expected_report_sha256:value.latest_report.report_sha256,window_id:sourceWindow.value,confirm_operator_case_mapping:true,confirm_case_outcome_defined_by_predicate:true});
          await request('/v1/process-catalog/procurement/router-source-outcome-bind?'+new URLSearchParams({workflow_id:scope.workflow_id,revision:String(scope.revision)}),{document:prefix.slice(0,-1)+',"bindings":'+raw+'}'});
          if(valid(attempt))await loadDetail(value.monitor_id);
        })));
      for(const b of value.source_outcome_bindings){
        const result=[...value.source_outcome_checks].reverse().find(x=>x.slot_id===b.slot_id);
        const row=el('div');row.appendChild(el('p',b.window_id+' · '+b.slot_id.slice(0,12)+' · '+b.predicate+' · '+(result?result.status+' / '+result.reason:'not checked')));
        if(result?.status!=='confirmed')row.appendChild(button(b.predicate==='jira_issue_resolved_in_window'?'Check linked Jira issue':b.predicate==='github_pr_merged_in_window'?'Check linked GitHub pull request':'Check linked Stripe invoice',()=>change('router-source-outcome-check',{
          expected_report_sha256:value.latest_report.report_sha256,window_id:b.window_id,slot_ids:[b.slot_id],confirm_source_read:true
        },'Perform an explicitly authorized read-only check of the linked source record?')));
        sourceSection.appendChild(row);
      }
      output.appendChild(sourceSection);
      const sf=value.source_finance;
      const financeSection=el('details');financeSection.append(el('summary','Source invoice amount reconciliation'),
        el('p',sf.matched+' exact matches · '+sf.mismatched+' mismatches · '+sf.unknown+' unknown of '+sf.selected+' selected invoice lines'),
        el('p','Amounts below are original currency minor units. They are invoice amount_paid values, not verified booked revenue, cash settlement or ROI. Only selected, previously confirmed live invoice predicates can be checked.'));
      for(const g of sf.groups){
        financeSection.appendChild(el('p',g.window_id+' · '+g.arm+' · '+g.currency+' · '+g.matched+'/'+g.selected+' matched · '+g.unknown+' unknown · known declared '+g.known_declared_minor_units+' · known source '+g.known_source_minor_units+' · difference '+g.known_difference_minor_units+' minor units'));
      }
      for(const b of value.source_outcome_bindings){
        if(b.predicate!=='invoice_paid_in_window')continue;
        const predicate=value.source_outcome_checks.find(x=>x.slot_id===b.slot_id&&x.status==='confirmed');
        const statement=value.finance_reconciliations.find(x=>x.window_id===b.window_id);
        if(!predicate||!statement)continue;
        const latest=[...value.source_finance_checks].reverse().find(x=>x.slot_id===b.slot_id);
        const row=el('div');row.appendChild(el('p',b.window_id+' · '+b.slot_id.slice(0,12)+' · '+statement.currency+' · '+(latest?latest.status+' / '+latest.reason:'not compared')));
        if(latest&&latest.status!=='unknown'){
          row.appendChild(el('p','Declared '+latest.declared_amount_paid_minor_units+' · source '+latest.source_amount_paid_minor_units+' · difference '+latest.difference_minor_units+' minor units'));
        }else{
          const input=el('input');input.type='text';input.inputMode='numeric';input.maxLength=19;
          input.setAttribute('aria-label','Declared gross invoice paid amount in minor units');
          if(latest){input.value=latest.declared_amount_paid_minor_units;input.readOnly=true;}
          const label=el('label','Declared amount_paid ('+statement.currency+' minor units) ');label.appendChild(input);
          row.append(label,button(latest?'Retry unknown source amount':'Compare source invoice amount',()=>perform(async()=>{
            const raw=input.value.trim();
            if(!/^(0|[1-9][0-9]{0,18})$/.test(raw)||BigInt(raw)>1000000000000000000n)throw new Error('Provide an exact nonnegative integer of at most 10^18 minor units.');
            if(!window.confirm('Read the authorized live Stripe invoice amount and retain this immutable declaration? It is not booked revenue or cash settlement.'))return;
            const attempt=++serial;
            const prefix=JSON.stringify({...base,window_id:b.window_id,expected_report_sha256:value.latest_report.report_sha256,
              expected_reconciliation_sha256:statement.reconciliation_sha256,confirm_source_amount_read:true,confirm_gross_invoice_amount_not_booked_revenue:true});
            const document=prefix.slice(0,-1)+',"rows":[{"slot_id":'+JSON.stringify(b.slot_id)+',"declared_amount_paid_minor_units":'+raw+'}]}';
            await request('/v1/process-catalog/procurement/router-source-finance-check?'+new URLSearchParams({workflow_id:scope.workflow_id,revision:String(scope.revision)}),{document});
            if(valid(attempt))await loadDetail(value.monitor_id);
          })));
        }
        financeSection.appendChild(row);
      }
      output.appendChild(financeSection);
      output.appendChild(el('h6','Producer credential management'));
      for(const g of value.producer_grants){
        const row=el('div');row.appendChild(el('p',g.producer_alias+' · '+g.window_id+' / '+g.arm+' · '+(g.revoked?'revoked':g.expires_ms<=Date.now()?'expired':'active')));
        if(!g.revoked)row.appendChild(button('Revoke '+g.grant_id,()=>change('router-producer-revoke',{grant_id:g.grant_id},'Revoke this producer credential? Previously accepted evidence remains retained.')));
        if(!g.revoked&&g.expires_ms>Date.now())row.appendChild(button('Prepare assignment manifest for '+g.grant_id,()=>perform(async()=>{
          if(!window.confirm('Export the assigned opaque case and slot references for this producer?'))return;
          const attempt=++serial;
          const manifest=await call('router-producer-manifest',{...base,grant_id:g.grant_id});
          if(valid(attempt))row.appendChild(download(manifest,'owg-producer-assignment-'+g.grant_id));
        })));
        output.appendChild(row);
      }
      const name=el('input');name.maxLength=80;name.setAttribute('aria-label','Producer alias');
      const windowSelect=el('select');windowSelect.setAttribute('aria-label','Producer monitoring window');
      for(const w of value.latest_report.windows){const opt=el('option',w.window_id);opt.value=w.window_id;windowSelect.appendChild(opt);}
      const arm=el('select');arm.setAttribute('aria-label','Producer arm');
      for(const a of ['incumbent','challenger']){const opt=el('option',a);opt.value=a;arm.appendChild(opt);}
      const ttl=el('input');ttl.type='number';ttl.min='60';ttl.max='604800';ttl.step='1';ttl.value='3600';ttl.setAttribute('aria-label','Producer credential lifetime seconds');
      for(const [text,input] of [['Producer alias',name],['Window',windowSelect],['Arm',arm],['Credential lifetime seconds',ttl]]){const l=el('label',text+' ');l.appendChild(input);output.appendChild(l);}
      output.appendChild(button('Issue scoped producer credential',()=>perform(async()=>{
        const seconds=Number(ttl.value);
        if(!Number.isInteger(seconds)||seconds<60||seconds>604800)throw new Error('Choose a lifetime from 60 to 604800 seconds.');
        if(!window.confirm('Issue a write-only credential for this exact window and arm? It will be shown only once.'))return;
        const attempt=++serial;
        const grant=await call('router-producer-grant',{...base,producer_alias:name.value,window_id:windowSelect.value,arm:arm.value,ttl_seconds:seconds});
        if(!valid(attempt))return;
        // Never put this value in download state, storage, or a URL.
        output.replaceChildren(el('p','Credential issued. Copy it now; leaving this view discards the displayed secret.'));
        const input=el('input');input.type='password';input.readOnly=true;input.autocomplete='off';input.value=grant.producer_token;input.setAttribute('aria-label','One-time producer credential');
        output.append(input,button('Show or hide credential',()=>{input.type=input.type==='password'?'text':'password';}),button('Copy producer credential',()=>navigator.clipboard.writeText(input.value)),
          button('Dismiss credential and reload journal',()=>perform(()=>{input.value='';return loadDetail(value.monitor_id);})));
      })));
      output.appendChild(el('h6','Window review'));
      const windows=el('ul');for(const w of value.latest_report.windows)windows.appendChild(el('li',w.window_id+' · '+w.state+' · '+w.recorded_cases+'/'+w.scheduled_cases+' recorded'));output.appendChild(windows);
      output.appendChild(button('Remove retained journal',()=>perform(async()=>{
        if(!window.confirm('Permanently remove this local journal and its producer credentials? Existing backups and exports are not erased.'))return;
        const attempt=++serial;await call('router-journal-forget',base);
        if(valid(attempt))await loadList();
      })));
    };
    section.append(button(initialId?'Open journal '+initialId:'List retained journals',()=>perform(initialId?()=>loadDetail(initialId):loadList)),output);
    host.appendChild(section);

  }
  function installWorkforceExchange(host){
    const section=el('details');section.append(el('summary','Exchange minimized monitoring summaries across installations'),
      el('p','Explicit offline export or review. Use the same high-entropy correlation key at each source, and obtain packet pins separately from trusted operators. No automatic network transfer. Signed packets can be verified against separately obtained public keys.'));
    const alias=el('input');alias.setAttribute('aria-label','Installation alias');alias.maxLength=80;
    const secret=el('input');secret.type='password';secret.autocomplete='off';secret.maxLength=64;secret.setAttribute('aria-label','Shared 32-byte correlation key in hexadecimal');
    const aliasLabel=el('label','Installation alias ');aliasLabel.append(alias);
    const secretLabel=el('label','Shared correlation key (64 hex characters) ');secretLabel.append(secret);
    const file=el('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Pinned workforce exchange review bundle');
    const output=el('div');let generation=0;
    const download=(value,name)=>button('Download '+name,()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));
      const link=el('a');link.href=url;link.download=name+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    const signingMode=el('input');signingMode.type='checkbox';signingMode.checked=true;
    const signingLabel=el('label','Require signed export and review ');signingLabel.append(signingMode);
    const identity=button('Show installation public signing key',async()=>{
      if(!window.confirm('Create or read the local installation signing identity?'))return;
      const attempt=++generation;output.replaceChildren();
      try{
        const value=await request('/v1/process-catalog/procurement-workforce/exchange/identity',{document:JSON.stringify({confirmed:true})});
        if(attempt!==generation||!output.isConnected)return;
        output.append(el('p','Public key: '+value.public_key_hex),el('p','Fingerprint: '+value.public_key_sha256),download(value,'owg-workforce-public-identity'),el('p','Verify this fingerprint through a separate trusted channel. No private key is exported.'));
      }catch(error){if(attempt===generation)output.replaceChildren(el('p',error.message));}
    });
    const registered=el('input');registered.type='checkbox';registered.checked=true;
    const registeredLabel=el('label','Review against retained local trust registry ');registeredLabel.append(registered);
    const trustPanel=el('div');let trustRevision=null;
    const refreshTrust=async()=>{
      const value=await request('/v1/process-catalog/procurement-workforce/exchange/trust-status',{document:'{}'});
      if(!trustPanel.isConnected)return;
      trustRevision=value.revision;trustPanel.replaceChildren(el('p','Registry revision '+value.revision+' · '+value.entries.length+' enrolled or revoked identities'));
      const list=el('ul');for(const entry of value.entries){
        const item=el('li',entry.installation_alias+' · '+(entry.active?'active':'revoked')+' · namespace '+entry.correlation_namespace_sha256);
        item.append(el('p','Public key: '+entry.public_key_hex),el('p','Latest accepted packet: '+(entry.accepted_packet_sha256||'none')));list.appendChild(item);
      }trustPanel.appendChild(list);
      const ns=el('input');ns.setAttribute('aria-label','Trust correlation namespace');ns.maxLength=64;
      const name=el('input');name.setAttribute('aria-label','Trusted installation alias');name.maxLength=80;
      const key=el('input');key.setAttribute('aria-label','Separately verified public key');key.maxLength=64;
      const action=el('select');action.setAttribute('aria-label','Trust registry action');for(const v of ['enroll','rotate','revoke']){const opt=el('option',v);opt.value=v;action.appendChild(opt);}
      for(const [label,input] of [['Namespace',ns],['Installation alias',name],['Public key (ignored for revoke)',key],['Action',action]]){const l=el('label',label+' ');l.appendChild(input);trustPanel.appendChild(l);}
      trustPanel.appendChild(button('Apply trust registry change',async()=>{
        if(!window.confirm('Apply '+action.value+' for this exact alias and namespace? Verify key ownership separately.'))return;
        await request('/v1/process-catalog/procurement-workforce/exchange/trust-configure',{document:JSON.stringify({expected_revision:trustRevision,installation_alias:name.value,correlation_namespace_sha256:ns.value,action:action.value,public_key_hex:action.value==='revoke'?null:key.value,confirmed:true})});
        await refreshTrust();
      }));
    };
    const runExport=button('Create minimized exchange packet',async()=>{
      const attempt=++generation;output.replaceChildren();
      try{
        if(!window.confirm('Export minimized aggregate monitoring evidence with linkable correlation tags?'))return;
        if(!/^[a-f0-9]{64}$/.test(secret.value))throw new Error('Provide a randomly generated 32-byte key as lowercase hex.');
        const document=JSON.stringify({installation_alias:alias.value,correlation_key_hex:secret.value,confirmed:true});
        secret.value='';
        const signed=signingMode.checked;
        const result=await request('/v1/process-catalog/procurement-workforce/exchange/'+(signed?'signed-export':'export'),{document});
        if(attempt!==generation||!output.isConnected)return;
        const envelope=signed?result.signed_packet:{expected_packet_sha256:result.packet_sha256,packet:result.packet};
        output.append(el('p',envelope.packet.rows.length+' minimized journal rows · '+envelope.packet.unreadable_journals+' unreadable'),
          el('p','Packet SHA-256: '+envelope.expected_packet_sha256),
          el('p','Correlation namespace: '+envelope.packet.correlation_namespace_sha256),
          download(envelope,signed?'owg-workforce-signed-exchange-packet':'owg-workforce-exchange-packet'));
        if(signed)output.append(el('p','Signing public key fingerprint: '+result.public_key_sha256),el('p','Distribute the public key fingerprint separately; do not treat a packet as its own trust anchor.'));
      }catch(error){if(attempt===generation)output.replaceChildren(el('p',error.message));}
    });
    const runReview=button('Review pinned installation packets',async()=>{
      const attempt=++generation;output.replaceChildren();
      try{
        const selected=file.files?.[0];if(!selected||selected.size>8*1024*1024)throw new Error('Choose a review bundle of at most 8 MiB.');
        if(!window.confirm('Review these explicitly selected offline installation packets?'))return;
        const signed=signingMode.checked;
        const result=await request('/v1/process-catalog/procurement-workforce/exchange/'+(signed?(registered.checked?'registered-review':'signed-review'):'review'),{document:await selected.text()});
        if(attempt!==generation||!output.isConnected)return;
        output.append(el('h4','Multi-installation review'),el('p',result.installations.length+' declared installations · '+result.included_journals+' included journals · '+result.excluded_journals+' excluded · '+result.unreadable_journals+' unreadable'),
          el('p',result.totals_may_be_incomplete?'Totals may be incomplete.':'No exclusions identified in the supplied packets; completeness is not independently verified.'));
        output.appendChild(el('p',result.all_packet_signatures_verified_against_operator_pins?'All signatures verified against explicit operator-pinned keys.':'Unsigned content-digest review only.'));
        for(const pin of result.trusted_key_fingerprints||[])output.appendChild(el('p',pin.installation_alias+' · key fingerprint '+pin.public_key_sha256));
        if(result.local_monotonic_packet_pins_checked)output.appendChild(el('p','Registry revision '+result.registry_revision+' · '+result.accepted_new_packets+' new packets · '+result.exact_repeated_packets+' exact repeats'));
        const money=(v,c)=>{
          if(v==null)return 'unbounded';
          const n=BigInt(v.numerator),d=BigInt(v.denominator),r=(2n*n+d)/(2n*d);
          return String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+c+' (rounded)';
        };
        for(const g of result.currency_totals){
          const sub=el('section');sub.append(el('h5',g.currency),el('p',g.recorded_cases+' recorded / '+g.started_cases+' started / '+g.scheduled_cases+' scheduled'));
          for(const [name,a] of Object.entries(g.arms))sub.appendChild(el('p',name+': '+a.recorded+'/'+a.cases+' recorded · declared variable cost '+money(a.variable_cost.lower_microunits,g.currency)+' to '+money(a.variable_cost.upper_microunits,g.currency)));
          output.appendChild(sub);
        }
        const list=el('ul');for(const r of result.journals)list.appendChild(el('li',r.installation_alias+' · '+r.journal_tag.slice(0,12)+' · '+(r.rollback_review?'rollback review · ':r.attention?'attention · ':'')+(r.included_in_totals?'included':r.exclusion_reasons.join(', '))));
        output.append(el('h5','Incident and overlap queue'),list,download(result,'owg-workforce-exchange-review'),
          el('p','Source identity, actual business outcomes and financial benefits remain unverified. No deployment or spending authorization.'));
      }catch(error){if(attempt===generation)output.replaceChildren(el('p',error.message));}
    });
    file.addEventListener('change',()=>{generation++;output.replaceChildren();});
    signingMode.addEventListener('change',()=>{generation++;output.replaceChildren();});
    registered.addEventListener('change',()=>{generation++;output.replaceChildren();});
    section.append(signingLabel,registeredLabel,identity,button('Load local trust registry',refreshTrust),trustPanel,aliasLabel,secretLabel,runExport,el('p','Review bundle: combine downloaded packets with separately obtained pins, the expected correlation namespace and either the expected registry revision (registered review) or an explicit public-key trust list (stateless signed review) (see AGENT_PROCUREMENT_WORKFORCE_EXCHANGE.md).'),file,runReview,output);host.appendChild(section);
  }
  function installWorkforceSchedule(host){
    const section=el('details');section.append(el('summary','Schedule local workforce deadline reviews'),
      el('p','Only journals with separate explicit consent are reviewed, and only crossed frozen boundaries create a checkpoint. No external source reads, messages or deployment actions.'));
    const output=el('div');let generation=0;
    const show=async()=>{
      const attempt=++generation;
      const data=await request('/v1/schedules');
      if(attempt!==generation||!output.isConnected)return;
      output.replaceChildren(el('p',data.scheduler_running?'Local scheduler running':'Local scheduler not running'));
      const current=data.schedules.find(s=>s.kind==='procurement_monitor_deadlines'&&s.target_id==='local');
      if(!current){
        const interval=el('input');interval.type='number';interval.min='15';interval.max='10080';interval.step='1';interval.value='15';interval.setAttribute('aria-label','Monitoring review interval in minutes');
        const label=el('label','Interval in minutes ');label.append(interval);
        output.append(label,button('Enable local deadline schedule',async()=>{
          const minutes=Number(interval.value);if(!Number.isInteger(minutes)||minutes<15||minutes>10080)throw new Error('Choose an integer interval of 15 to 10080 minutes.');
          if(!window.confirm('Run local deadline reviews for explicitly opted-in monitoring journals at this interval?'))return;
          await request('/v1/schedules',{kind:'procurement_monitor_deadlines',target_id:'local',interval_minutes:minutes,confirm_scheduled_runs:true});await show();
        }));
      }else{
        output.append(el('p',(current.enabled?'Enabled':'Paused')+' · every '+current.interval_minutes+' minutes · next '+(current.next_run_at||'not scheduled')),
          el('p','Last result: '+(current.last_result||'not run')+' · pause reason '+(current.paused_reason||'none')),
          button(current.enabled?'Pause deadline schedule':'Resume deadline schedule',async()=>{
            if(!window.confirm((current.enabled?'Pause':'Resume')+' the local deadline schedule?'))return;
            await request('/v1/schedules/update',{schedule_id:current.schedule_id,enabled:!current.enabled});await show();
          }),
          button('Remove deadline schedule',async()=>{
            if(!window.confirm('Remove this schedule? Retained journals and their consent are unchanged.'))return;
            await request('/v1/schedules/delete',{schedule_id:current.schedule_id});await show();
          }));
      }
    };
    section.append(button('Load deadline schedule status',show),output);host.appendChild(section);
  }
  function installWorkforceAllocation(host){
    const section=el('details');section.append(el('summary','Optimize selected workflow AI budget scenarios'),
      el('p','Upload an exact allocation bundle (see AGENT_PROCUREMENT_WORKFORCE_ALLOCATION.md). This is a finite conditional comparison, not spending approval or an execution decision.'));
    const file=el('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Workforce allocation bundle');
    const output=el('div');let generation=0;
    const run=button('Evaluate workforce allocation',async()=>{
      const attempt=++generation;output.replaceChildren();
      try{
        const selected=file.files?.[0];
        if(!selected||selected.size>8*1024*1024)throw new Error('Choose a JSON bundle of at most 8 MiB.');
        const body={document:await selected.text()};
        const result=await request('/v1/process-catalog/procurement-workforce/allocation',body);
        if(attempt!==generation||!output.isConnected)return;
        const amount=(v,unit)=>{
          if(v==null)return 'unbounded';
          const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n;
          const scale=unit==='money'?1000000n:3600000n;
          const rounded=(2n*a*100n+d*scale)/(2n*d*scale);
          return (n<0n?'-':'')+String(rounded/100n)+'.'+String(rounded%100n).padStart(2,'0')+' '+(unit==='money'?result.currency:'person-hours')+' (rounded)';
        };
        output.append(el('h4','Exact finite allocation for review'),
          el('p',result.selected_workflows+' selected workflows · '+result.feasible_combinations+' feasible / '+result.evaluated_combinations+' combinations'),
          el('p','Lower-bound first-year scenario savings: '+amount(result.first_year_savings_lower_microunits,'money')),
          el('p','Upper gross AI cost: '+amount(result.first_year_gross_ai_cost_upper_microunits,'money')+' · remaining envelope '+amount(result.gross_ai_cost_headroom_microunits,'money')),
          el('p',result.annual_ai_cases+' annual AI cases · '+result.ai_case_headroom+' remaining case capacity'),
          el('p','Worst-case additional person-time: '+amount(result.annual_additional_person_ms_upper,'time')+' · remaining envelope '+amount(result.additional_person_ms_headroom,'time')));
        for(const row of result.rows){
          const group=el('section');group.append(el('h5',row.title+' · r'+row.revision),
            el('p','Selected for review: '+(row.selected_for_review.portfolio_id||'defer')),
            button('Open allocation process revision',()=>load(row.workflow_id,row.revision)));
          const ul=el('ul');
          for(const o of row.options)ul.appendChild(el('li',(o.portfolio_id||'defer')+' · '+(o.eligible?'eligible scenario; lower savings '+amount(o.first_year_savings_lower_microunits,'money')+'; upper gross cost '+amount(o.first_year_gross_ai_cost_upper_microunits,'money'):o.exclusion_reasons.join(', '))));
          group.appendChild(ul);output.appendChild(group);
        }
        output.append(el('p','No shared discounts, cross-workflow labor offsets, verified savings or purchase authority are assumed.'),
          button('Download allocation review JSON',()=>{
            const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)+'\n'],{type:'application/json'}));
            const link=el('a');link.href=url;link.download='owg-workforce-allocation.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
          }));
      }catch(error){if(attempt===generation)output.replaceChildren(el('p',error.message));}
    });
    file.addEventListener('change',()=>{generation++;output.replaceChildren();});
    section.append(file,run,output);host.appendChild(section);
  }
  async function workforceOverview(){
    const host=document.getElementById('owgWorkforceOverview');
    host.replaceChildren(el('p','Loading retained local monitoring journals…'));
    try{
      const data=await request('/v1/process-catalog/procurement-workforce/overview');
      host.replaceChildren(el('h3','AI workforce monitoring · local installation'),
        el('p',data.journal_count+' retained journals · '+data.included_journals+' included in totals · '+data.excluded_journals+' excluded · '+data.unreadable_journals.length+' unreadable'),
        el('p','Snapshot viewed '+new Date(data.as_of_server_ms).toISOString()+'. No production execution or independently verified savings.'));
      const money=(v,currency)=>{
        if(v==null)return 'unbounded';
        const n=BigInt(v.numerator),d=BigInt(v.denominator),a=n<0n?-n:n,r=(2n*a+d)/(2n*d);
        return (a%d!==0n?'≈ ':'')+(n<0n?'-':'')+String(r/1000000n)+'.'+String(r%1000000n).padStart(6,'0')+' '+currency;
      };
      const range=(v,currency)=>money(v.lower_microunits,currency)+' to '+money(v.upper_microunits,currency);
      const armLine=(name,a,currency)=>el('p',name+': '+a.recorded+'/'+a.cases+' recorded · success '+a.counts.success.lower+'–'+a.counts.success.upper+' · declared variable cost '+range(a.variable_cost,currency));
      if(data.totals_may_be_incomplete)host.appendChild(el('p','Totals exclude overlapping, historical, unreadable or otherwise ineligible journals. Inspect the exclusions before using these figures.'));
      for(const g of data.currency_totals){
        const section=el('section');section.append(el('h4',g.currency+' · '+g.journals+' included journals'),
          el('p',g.recorded_cases+' recorded / '+g.started_window_cases+' started / '+g.scheduled_cases+' scheduled · '+g.credential_authenticated_observations+' credential-attributed observations'));
        for(const [name,a] of Object.entries(g.arms))section.appendChild(armLine(name,a,g.currency));
        host.appendChild(section);
      }
      host.appendChild(el('h4','Incident and review queue'));
      const filter=el('select');filter.setAttribute('aria-label','Workforce review filter');
      for(const [value,label] of [['all','All journals'],['rollback_review','Rollback review'],['attention','Needs attention'],['excluded_review','Excluded review'],['monitoring','Monitoring'],['continuation_review','Continuation review']]){
        const o=el('option',label);o.value=value;filter.appendChild(o);
      }
      const list=el('div');const draw=()=>{
        list.replaceChildren();
        const rows=data.journals.filter(r=>filter.value==='all'||r.priority===filter.value);
        if(!rows.length)list.appendChild(el('p','No matching journals.'));
        for(const r of rows){
          const section=el('section');
          section.append(el('h5',r.title+' · r'+r.revision+' · '+r.monitor_id),
            el('p',r.priority.replaceAll('_',' ')+' · '+r.recorded_cases+'/'+r.started_window_cases+' recorded · '+(r.included_in_totals?'included in totals':'excluded: '+r.exclusion_reasons.join(', '))),
            el('p','Retained cutoff: '+new Date(r.as_of_ms).toISOString()+' · head '+r.head_sha256),
            el('p','Rollback triggers: '+(r.sticky_rollback_triggers.map(x=>(x.window_id||'cumulative')+': '+x.reason).join(' · ')||'none recorded')),
            el('p','Rollback referral: '+(r.rollback_referral_recorded?'recorded':'not recorded')+' · due for review: '+(r.due_for_review_windows.join(', ')||'none')),
            el('p','Source predicates: '+r.source_outcomes.confirmed+'/'+r.source_outcomes.bound+' confirmed'),
            el('p','Source invoice amounts: '+r.source_finance.matched+' matched · '+r.source_finance.mismatched+' discrepancies · '+r.source_finance.unknown+' unknown'),
            el('p','Outcome corroboration: '+r.outcome_corroboration.matched+'/'+r.outcome_corroboration.enrolled+' matched · '+r.outcome_corroboration.conflicts+' conflicts'),
            el('p','Declared finance statements: '+(r.finance_reconciled_windows.join(', ')||'none')+' · scheduled review consent '+(r.scheduled_deadline_review_enabled?'enabled':'disabled')),
            button('Open saved revision',()=>load(r.workflow_id,r.revision)));
          installJournalWorkbench(section,{workflow_id:r.workflow_id,revision:r.revision},r.monitor_id);
          for(const [name,a] of Object.entries(r.arms))section.appendChild(armLine(name,a,r.currency));
          list.appendChild(section);
        }
      };
      filter.addEventListener('change',draw);host.append(filter,list);draw();
      for(const r of data.unreadable_journals)host.appendChild(el('p','Unreadable journal: '+r.workflow_id+' · r'+r.revision+' · '+r.monitor_id));
      host.appendChild(el('p','Currencies are never combined. Disjoint arm costs are not realized savings. Source credentials do not verify business outcomes.'));
      host.appendChild(button('Download workforce monitoring overview',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'}));
        const link=el('a');link.href=url;link.download='owg-workforce-monitoring-overview.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }));
    }catch(error){host.replaceChildren(el('p','Workforce overview unavailable.'));status(error.message);}
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
    const section=el('details');section.id='owgBPMNImport';
    section.append(el('summary','Import a BPMN design as an unreviewed draft'),
      el('p','Choose a UTF-8 BPMN 2.0 file of at most 128 KiB. Preview a single-process file directly, or inspect the file and choose an independent process. Review and save the draft separately.'));
    const file=el('input');file.type='file';file.accept='.bpmn,.xml,application/xml,application/bpmn+xml';
    file.setAttribute('aria-label','Choose BPMN process design');
    const manifest=el('div'),message=el('p');message.setAttribute('role','status');
    const options=el('select');options.setAttribute('aria-label','BPMN process');
    const choice=el('label','Process to preview ');choice.append(options);choice.hidden=true;
    const output=el('div');output.id='owgBPMNImportResult';
    let sourceDigest=null;
    const preview=button('Preview selected BPMN',()=>previewBPMN(file,choice.hidden?null:{
      process_id:options.value,expected_source_sha256:sourceDigest
    }));preview.id='owgBPMNPreview';preview.disabled=true;
    const inspect=button('Inspect BPMN file',async()=>{
      const source=file.files?.[0];
      if(!source)return;
      if(source.size>128*1024){message.textContent='BPMN exceeds the 128 KiB limit.';return;}
      const attempt=++importGeneration;
      importDraft=null;sourceDigest=null;output.replaceChildren();manifest.replaceChildren();choice.hidden=true;
      preview.disabled=inspect.disabled=true;
      message.textContent='Reading the process list…';
      try{
        await window.__owgAuthReady;
        const response=await fetch('/v1/process-catalog/import-bpmn/inspect',{
          method:'POST',headers:{'Content-Type':'application/bpmn+xml'},body:source,cache:'no-store'
        });
        const result=await response.json().catch(()=>({}));
        if(attempt!==importGeneration)return;
        if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'BPMN inspection failed');
        sourceDigest=result.source_sha256;
        const placeholder=el('option','Choose a process');placeholder.value='';options.replaceChildren(placeholder);
        let eligible=0;
        result.processes.forEach((item,index)=>{
          const label=item.name||'Process '+(index+1);
          if(item.blocking_reason){manifest.append(el('p',label+' · '+item.blocking_reason));return;}
          if(!item.id)return;
          const option=el('option',label+' · '+item.id);option.value=item.id;options.append(option);eligible++;
        });
        choice.hidden=eligible===0;
        message.textContent=result.processes.length+' '+(result.processes.length===1?'process':'processes')+' found.';
        if(result.processes.length===1){
          if(eligible===1)options.selectedIndex=1;
          preview.disabled=false;
        }
      }catch(error){if(attempt===importGeneration)message.textContent=error.message;}
      finally{if(attempt===importGeneration)inspect.disabled=false;}
    });inspect.disabled=true;
    options.addEventListener('change',()=>{
      importGeneration++;importDraft=null;output.replaceChildren();preview.disabled=!options.value;inspect.disabled=false;
    });
    file.addEventListener('change',()=>{
      importGeneration++;importDraft=null;sourceDigest=null;choice.hidden=true;options.replaceChildren();
      preview.disabled=inspect.disabled=!file.files?.length;
      output.replaceChildren();manifest.replaceChildren();message.textContent='';
    });
    section.append(file,inspect,message,manifest,choice,preview,output);
    parent.appendChild(section);
  }
  async function previewBPMN(input,selection){
    const file=input.files?.[0];if(!file)return;
    if(file.size>128*1024)throw new Error('BPMN exceeds the 128 KiB limit.');
    const attempt=++importGeneration,control=document.getElementById('owgBPMNPreview');control.disabled=true;
    importDraft=null;const host=document.getElementById('owgBPMNImportResult');host.replaceChildren(el('p','Inspecting BPMN as data; nothing is saved…'));
    try{
      await window.__owgAuthReady;
      const query=selection?'?'+new URLSearchParams(selection):'';
      const response=await fetch('/v1/process-catalog/import-bpmn'+query,{
        method:'POST',headers:{'Content-Type':'application/bpmn+xml'},body:file,cache:'no-store'});
      const value=await response.json().catch(()=>({}));
      if(attempt!==importGeneration)return;
      if(!response.ok)throw new Error(String(value.detail||'BPMN import failed'));
      if(value.format!=='openworkgraph.bpmn-draft-import.v1')throw new Error('Unexpected BPMN preview response');
      importDraft=value;
      host.replaceChildren(el('h4','Unreviewed BPMN draft'),el('p',value.process_model.activities.length+' nodes · '+value.process_model.transitions.length+
        ' transitions · '+value.process_model.business_rules.length+' unconfirmed rules. Source SHA256 '+value.source_sha256));
      if(value.selected_process)host.appendChild(el('p','Selected process: '+(value.selected_process.name||value.selected_process.id)));
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
    restorationDraft=null;model=copy(value);
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
    restorationDraft=null;record={workflow_id:null,revision:0,title:value.selected_process?.name||'Imported BPMN design',evidence_refs:[]};
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
    host.appendChild(button('Review AI workforce monitoring',()=>workforceOverview()));
    const workforceHost=el('div');workforceHost.id='owgWorkforceOverview';host.appendChild(workforceHost);
    installWorkforceExchange(host);
    installWorkforceSchedule(host);
    installWorkforceAllocation(host);
    const message=el('p','No saved process has been loaded.');message.id='owgCatalogStatus';message.setAttribute('role','status');host.appendChild(message);
    const editor=el('div');editor.id='owgCatalogEditor';host.appendChild(editor);
    const style=el('style');style.textContent='#owgProcessCatalog label{display:block;margin:8px 0}#owgProcessCatalog fieldset{margin:12px 0;min-width:0}#owgProcessCatalog input:not([type=checkbox]){max-width:100%;width:32em}#owgProcessCatalog .owg-catalog-actions{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}';host.prepend(style);
    panel.appendChild(host);
  }
  window.owgProcessCatalog={saveDesigner,draftObserved,draftDiscovered,openSaved:ref=>load(ref.workflow_id,ref.revision,ref.content_sha256)};
  window.__owgProcessCatalogModel={linearModel,designPathDifferences,revisionDifferences};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
