/* Human planning and reports. No spending, execution or persistent browser state. */
(() => {
 'use strict';
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
 const label=(text,node)=>{const n=el('label',text+' ');n.append(node);return n;};
 const button=(text,id,fn)=>{const n=el('button',text);if(id)n.id=id;n.type='button';n.onclick=fn;return n;};
 const options=(n,rows)=>n.replaceChildren(...rows.map(([id,text])=>{const o=el('option',text);o.value=id;return o;}));
 const pin=p=>({workflow_id:p.workflow_id||p.data.process.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
 const download=(r)=>{const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-implementation-phase-plan.json';a.click();URL.revokeObjectURL(url);};
 function create({detailHost,call,onDenied}){
  let epoch=0,busy=false,selected=null,view=null,baseline=null,reports=new Map(),baselineDirty=false,reportDirty=new Set(),sourceStale=false;
  const host=el('div');detailHost.append(host);let form=null,status=null,baselineConfirm=null,reportConfirm=null,freezeConfirm=null,save=null,saveReports=null,freeze=null,exportButton=null,phaseList=null,reportList=null,baselineGroup=null;
  const note=text=>{if(status)status.textContent=text;};
  function clear(){epoch++;busy=false;selected=view=baseline=null;reports.clear();reportDirty.clear();baselineDirty=false;sourceStale=false;host.replaceChildren();form=status=baselineConfirm=reportConfirm=freezeConfirm=save=saveReports=freeze=exportButton=phaseList=reportList=baselineGroup=null;}
  async function work(fn){if(busy)return;const e=epoch;busy=true;if(form)form.disabled=true;
   try{await fn(()=>epoch===e);}catch(error){if(epoch!==e)return;if([401,403].includes(error.status)){clear();onDenied();return;}note(error.message||'Implementation-plan request failed.');}
   finally{if(epoch===e){busy=false;if(form)form.disabled=sourceStale;}}
  }
  function select(data){clear();selected=data;status=el('p');status.id='implementationStatus';status.setAttribute('role','status');host.append(el('h3','Implementation phase baseline and reports'),el('p','Review a phase baseline over the existing opportunity, then separately report costs, effort and remaining estimates. Reports stay human declarations. Frozen plans grant no spending or execution permission and do not enter benefit totals.'),button('Load implementation plan','implementationLoad',()=>work(load)),status);}
  function writable(){return !sourceStale&&view?.permissions.edit&&!selected.viewing_historical_revision&&!view.viewing_historical_revision;}
  function update(){if(baselineGroup)baselineGroup.disabled=!writable()||reportDirty.size>0;if(reportList)reportList.disabled=!writable()||!view?.artifact||baselineDirty;if(save)save.disabled=!writable()||!baseline||reportDirty.size>0||!baselineConfirm.checked;if(saveReports)saveReports.disabled=!writable()||!view?.artifact||baselineDirty||!reportDirty.size||!reportConfirm.checked;if(freeze)freeze.disabled=sourceStale||!view?.can_freeze||baselineDirty||reportDirty.size>0||!freezeConfirm.checked;if(exportButton)exportButton.disabled=sourceStale||!view?.artifact||!view.integrity_ok||baselineDirty||reportDirty.size>0;}
  function changed(id){if(id)reportDirty.add(id);else baselineDirty=true;const history=host.querySelector('#implementationHistoryControls');if(history){history.remove();}for(const box of [baselineConfirm,reportConfirm,freezeConfirm])if(box)box.checked=false;update();note('Unsaved changes. Review the exact baseline or human report and confirm sharing.');}
  async function latestOpportunity(ok){const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity');if(!ok())return null;if(!r.integrity_ok||!r.artifact?.data.assessment)throw new Error('Save a current opportunity assessment before planning phases.');return r.artifact;}
  async function load(ok,revision){const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan'+(revision?'?revision='+revision:''));if(!ok())return;view=r;sourceStale=false;baseline=r.artifact?structuredClone(r.artifact.baseline):null;reports=new Map((r.artifact?.reports||[]).map(row=>[row.phase_id,structuredClone(row)]));baselineDirty=false;reportDirty.clear();render();note(!r.integrity_ok?'Plan integrity failed. An owner can erase its history.':r.artifact?'Opened implementation-plan revision '+r.artifact.revision+'.':'No phase baseline saved. Start from the current opportunity assessment.');}
  function input(value,type,change,max=120){const n=el('input');n.type=type;n.maxLength=max;n.value=value??'';n.disabled=!writable();n.oninput=()=>change(n.type==='checkbox'?n.checked:n.value);return n;}
  function check(id){const n=el('input');n.type='checkbox';n.id=id;n.onchange=update;return n;}
  const defaultReport=id=>({phase_id:id,status:'planned',cost_to_date:null,effort_hours_to_date:null,remaining_cost_estimate:null,remaining_effort_hours_estimate:null,started_on:null,finished_on:null,completion_self_report_confirmed:false,actual_values_independently_verified:false,business_outcome_verified:false});
  function drawPhases(){phaseList.replaceChildren();reportList.replaceChildren(el('legend','Human phase reports'));if(!baseline)return;
   for(const phase of baseline.phases){const row=el('fieldset');row.append(el('legend',phase.id));
    for(const [key,title,type] of [['name','Phase name','text'],['cost','Baseline cost','text'],['effort_hours','Baseline effort hours','text'],['starts_on','Planned start','date'],['finishes_on','Planned finish','date']]){
     const n=input(phase[key],type,value=>{phase[key]=value;changed();},key==='name'?120:40);n.setAttribute('aria-label',title+' '+phase.id);if(key==='name'&&reports.has(phase.id))n.disabled=true;row.append(label(title+' '+phase.id,n));}
    const remove=button('Remove phase '+phase.id,null,()=>{baseline.phases=baseline.phases.filter(p=>p.id!==phase.id);changed();drawPhases();});remove.disabled=!writable()||reports.has(phase.id);row.append(remove);phaseList.append(row);
    const report=reports.get(phase.id)||defaultReport(phase.id);const reportRow=el('fieldset');reportRow.append(el('legend','Human report: '+phase.name+' ('+phase.id+')'));
    const attribution=view.artifact?.report_attributions.find(r=>r.phase_id===phase.id);reportRow.append(el('p',attribution?'Reported by '+attribution.actor_id+' at '+attribution.at+' in plan r'+attribution.reported_in_revision+'. Values remain unverified.':'No report saved. Blank amounts mean unknown, not zero.'));
    const state=el('select');options(state,['planned','in_progress','on_hold','completed','cancelled'].map(v=>[v,v.replaceAll('_',' ')]));state.value=report.status;state.disabled=!writable();state.onchange=()=>{report.status=state.value;reports.set(phase.id,report);changed(phase.id);};reportRow.append(label('Reported phase status '+phase.id,state));
    for(const [key,title,type] of [['cost_to_date','Reported cost to date','text'],['effort_hours_to_date','Reported effort hours to date','text'],['remaining_cost_estimate','Remaining cost estimate','text'],['remaining_effort_hours_estimate','Remaining effort hours estimate','text'],['started_on','Reported start','date'],['finished_on','Reported finish','date']]){
     const n=input(report[key],type,value=>{report[key]=value===''?null:value;reports.set(phase.id,report);changed(phase.id);},40);n.setAttribute('aria-label',title+' '+phase.id);reportRow.append(label(title+' '+phase.id,n));}
    const completed=check('implementationCompleted_'+phase.id);completed.checked=report.completion_self_report_confirmed;completed.disabled=!writable();completed.onchange=()=>{report.completion_self_report_confirmed=completed.checked;reports.set(phase.id,report);changed(phase.id);};reportRow.append(label('I explicitly confirm this completion/cancellation self-report '+phase.id,completed));reportList.append(reportRow);
   }update();
  }
  function historyControls(){
   const panel=el('details');panel.id='implementationHistoryControls';panel.append(el('summary','Implementation forecast history and calendar controls'));
   const loadHistory=button('Load exact implementation history','implementationHistoryLoad',()=>work(async ok=>{
    reset();const r=await call(base+'/history');if(!ok()||!panel.isConnected)return;
    inventory=r;options(before,r.revisions.map(x=>[String(x.revision),'r'+x.revision+' · '+x.saved_at]));options(after,r.revisions.map(x=>[String(x.revision),'r'+x.revision+' · '+x.saved_at]));
    before.value=String(r.revisions[0].revision);after.value=String(r.latest_revision);
    review.disabled=r.revisions.length<2;message.textContent=r.revisions.length+' exact saved revisions. Select two ordered revisions and a review date.';
   }));
   const base='/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan';
   const before=el('select');before.id='implementationHistoryBefore';const after=el('select');after.id='implementationHistoryAfter';
   const day=el('input');day.id='implementationHistoryDate';day.type='date';day.value=new Date().toISOString().slice(0,10);day.max=day.value;
   const message=el('p');message.id='implementationHistoryStatus';message.setAttribute('role','status');
   const out=el('div');out.id='implementationHistoryOutput';
   let inventory=null,result=null;
   const review=button('Compare exact implementation revisions','implementationHistoryCompare',()=>work(async ok=>{
    clearReport();if(!inventory||!day.value||!day.checkValidity()||Number(before.value)>=Number(after.value)){message.textContent='Select ordered revisions and a valid UTC review date.';return;}
    const r=await call(base+'/history/compare',{before_revision:Number(before.value),after_revision:Number(after.value),as_of_date:day.value,expected_history_sha256:inventory.history_sha256});
    if(!ok()||!panel.isConnected)return;
    if(r.history_sha256!==inventory.history_sha256||r.root_workflow_id!==selected.process.workflow_id)throw Error('Unexpected history review basis.');
    result=r;draw(r);exportHistory.disabled=false;message.textContent='Exact saved history reviewed. Human reports remain unverified.';
   }));
   const exportHistory=button('Download implementation history review','implementationHistoryDownload',()=>{
    if(!result||!panel.isConnected||sourceStale||baselineDirty||reportDirty.size)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-implementation-history-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   });
   function clearReport(){result=null;exportHistory.disabled=true;out.replaceChildren();}
   function reset(){clearReport();inventory=null;review.disabled=true;before.replaceChildren();after.replaceChildren();}
   for(const n of [before,after,day])n.oninput=()=>{clearReport();message.textContent='Review the changed selection again.';};
   function grid(headers,rows){
    const wrapper=el('div');wrapper.style.cssText='max-width:100%;overflow-x:auto';wrapper.tabIndex=0;
    wrapper.setAttribute('role','region');wrapper.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
    const t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');for(const name of headers){const th=el('th',name);th.scope='col';hr.append(th);}h.append(hr);
    for(const row of rows){const tr=el('tr');for(const v of row)tr.append(el('td',v??'Unknown'));b.append(tr);}t.append(h,b);wrapper.append(t);return wrapper;
   }
   function draw(r){
    const c=r.comparison;
    out.replaceChildren(el('h4','Saved implementation forecast history'),el('p','Selected comparison r'+c.before.revision+' → r'+c.after.revision+'; latest r'+r.latest_revision+'.'),
     grid(['Revision','Currency','Baseline','Known actual lower bound','Known forecast lower bound','Complete forecast','Unknown forecast phases','Stored review','Reassessed'],
      r.timeline.map(x=>[x.revision,x.currency,x.cost.baseline.complete_total,x.cost.actual.nonnegative_known_lower_bound,x.cost.forecast.nonnegative_known_lower_bound,x.cost.forecast.complete_total,x.cost.forecast.unknown_phases,x.stored_independent_review?'Yes':'No',x.baseline_reassessed_since_previous?'Yes':'No'])),
     el('p','Stored review transition: '+c.stored_review_transition+' · baseline reassessed: '+c.baseline_reassessed+' · opportunity reference changed: '+c.opportunity_reference_changed+'.'));
    if(r.currency_warning)out.append(el('p',r.currency_warning));
    for(const [name,b] of [['Cost',c.cost_bridge],['Effort',c.effort_bridge]]){
     out.append(el('h4',name+' revision bridge'));
     if(!b.comparable){out.append(el('p','Not comparable: '+b.reason));continue;}
     out.append(grid(['Measure','Retained change','Added scope','Removed scope (signed)','Complete change','Unresolved phases'],
      Object.entries(b.amounts).map(([k,x])=>[k,x.retained_phase_change,x.added_phase_amount,x.removed_phase_amount_signed,x.complete_change,x.unresolved_phase_ids.join(', ')||'None'])));
    }
    const detail=el('details');detail.append(el('summary','Exact phase changes and attribution'));
    for(const p of c.phases){
     detail.append(el('h5',p.phase_id+' · '+p.membership),el('p','Baseline changes: '+(p.baseline_changed_fields.join(', ')||'none')+'; report changes: '+(p.report_changed_fields.join(', ')||'none')+'.'),
      el('p','Status: '+p.before_status+' → '+p.after_status+'; reported by '+(p.before_attribution?.actor_id??'none')+' → '+(p.after_attribution?.actor_id??'none')+'.'),
      el('p','Planned start shift: '+(p.planned_start_shift_days??'not comparable')+' days; finish shift: '+(p.planned_finish_shift_days??'not comparable')+' days.'),
      grid(['Metric','Measure','Before','After','Change'],Object.entries(p.metrics).flatMap(([name,x])=>Object.entries(x).map(([k,v])=>[name,k,v.before,v.after,v.change]))));
    }
    out.append(detail,el('h4','Calendar review at '+r.calendar.as_of_date),el('p',r.calendar.interpretation),
     grid(['Phase','Planned start','Planned finish','Reported status','Reported start','Reported finish','Days past finish','Flags'],
      r.calendar.phases.map(p=>[p.name,p.planned_start,p.planned_finish,p.reported_status,p.reported_start,p.reported_finish,p.days_past_planned_finish_without_terminal_report,p.flags.join(', ')||'None'])));
    const b=r.current_budget_review;
    if(b)out.append(el('h4','Current opportunity budget exposure'),grid(['Budget context','Known forecast lower bound','Complete forecast','Lower bound exceeds budget','Minimum excess'],
      [[b.budget_context,b.forecast_lower_bound,b.forecast_complete?'Yes':'No',b.known_lower_bound_exceeds_budget?'Yes':'No',b.minimum_excess_over_budget]]),
      el('p',b.headroom_not_established_if_unknown?'Unknown amounts prevent establishing budget headroom.':'Forecast amounts are complete human declarations.'),
      el('p','Current approved assessment: '+b.current_approved_assessment+'; blockers: '+(b.source_blockers.join(', ')||'none')));
    else out.append(el('p','Current budget comparison unavailable for this selection.'));
    out.append(el('p',r.interpretation),el('p','Review digest: '+r.review_sha256));
   }
   panel.append(el('p','Compare immutable saved revisions, including forecast completeness, scope changes, recorded independent reviews and planned dates. This is not a reconstruction of past knowledge.'),
    loadHistory,label('Earlier plan revision',before),label('Later plan revision',after),label('UTC calendar review date',day),review,exportHistory,message,out);
   reset();message.textContent='Load exact saved revisions first.';return panel;
  }
  function render(){if(form)form.remove();form=el('fieldset');form.id='implementationEditor';form.append(el('legend','Implementation planning'));
   if(!view.integrity_ok){const erase=button('Erase all implementation-plan history','implementationDelete',()=>work(remove));erase.disabled=!view.permissions.delete;form.append(erase);host.append(form);return;}
   if(!baseline){const start=button('Start phase baseline from opportunity','implementationStart',()=>work(async ok=>{const opportunity=await latestOpportunity(ok);if(!ok()||!opportunity)return;baseline={title:'Implementation phase plan',opportunity:pin(opportunity),currency:opportunity.data.assessment.budget_currency,phases:[{id:'phase_1',name:'Implementation',cost:'0.00',effort_hours:'0.00',starts_on:'',finishes_on:''}]};baselineDirty=true;render();note('Draft baseline created. Enter dates and assumptions, then review and share.');}));start.disabled=!writable();form.append(start);host.append(form);return;}
   baselineGroup=el('fieldset');baselineGroup.id='implementationBaseline';baselineGroup.append(el('legend','Phase baseline'));form.append(baselineGroup);
   baselineGroup.append(el('p','Currency: '+baseline.currency+' · pinned opportunity r'+baseline.opportunity.revision+' · '+baseline.opportunity.content_sha256));
   const title=input(baseline.title,'text',value=>{baseline.title=value;changed();});baselineGroup.append(label('Implementation plan title',title));
   const refresh=button('Refresh baseline opportunity reference','implementationRefresh',()=>work(async ok=>{const opportunity=await latestOpportunity(ok);if(!ok()||!opportunity)return;if(opportunity.data.assessment.budget_currency!==baseline.currency)throw new Error('A changed currency needs a separate explicitly reconciled plan; no conversion is performed.');baseline.opportunity=pin(opportunity);changed();render();note('Baseline reference refreshed in draft; saving requires a fresh independent review.');}));refresh.disabled=!writable();baselineGroup.append(refresh);
   phaseList=el('div');phaseList.id='implementationPhases';baselineGroup.append(phaseList);
   const add=button('Add implementation phase','implementationAdd',()=>{if(baseline.phases.length>=12){note('Use at most twelve phases.');return;}let i=1;while(baseline.phases.some(p=>p.id==='phase_'+i))i++;baseline.phases.push({id:'phase_'+i,name:'Phase '+i,cost:'0.00',effort_hours:'0.00',starts_on:'',finishes_on:''});changed();drawPhases();});add.disabled=!writable();baselineGroup.append(add);
   baselineConfirm=check('implementationBaselineConfirm');baselineConfirm.disabled=!writable();save=button('Save new baseline revision (fresh review required)','implementationSave',()=>work(async ok=>{await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan',{baseline:structuredClone(baseline),expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:baselineConfirm.checked,organization_sharing_confirmed:baselineConfirm.checked});if(ok()){await load(ok);if(ok())note('New phase baseline saved. Any previous review was cleared; phase reports were preserved.');}}));baselineGroup.append(label('I reviewed this exact phase baseline and confirm sharing its estimates, dates and opportunity reference',baselineConfirm),save);
   const summary=el('div');summary.id='implementationSummary';if(view.artifact){const a=view.artifact;summary.append(el('p','Saved plan r'+a.revision+' · status '+view.effective_state.replaceAll('_',' ')+' · baseline author '+a.baseline_author_actor_id+' · assessed in r'+a.assessed_in_revision+' · checked '+view.checked_at));if(a.review)summary.append(el('p','Baseline reviewed by '+a.review.actor_id+' at '+a.review.at+' · current reviewed baseline: '+view.current_reviewed_baseline));
    if(view.source)summary.append(el('p','Current opportunity budget context: '+(view.source.implementation_budget??'unknown')+' '+(view.source.currency??'')+' · '+view.source.opportunity_state.replaceAll('_',' ')),...view.source.blockers.map(code=>el('p','Review blocker: '+code.replaceAll('_',' '))));
    if(view.totals){const dl=el('dl');for(const [key,value] of Object.entries(view.totals))if(typeof value==='string'||typeof value==='number'||value===null)dl.append(el('dt',key.replaceAll('_',' ')),el('dd',value??'unknown; complete required phase coverage'));summary.append(dl);for(const [key,count] of Object.entries(view.totals.coverage))summary.append(el('p',key.replaceAll('_',' ')+': '+count.known_phases+' known / '+count.unknown_phases+' unknown phases.'));}}
   summary.append(el('p','Saved totals exclude unsaved entries. Reload to inspect remote changes. All costs, effort, dates and status are human declarations. Calendar span is not working time. No source-verified spending, business outcomes, realized savings or execution permission is established.'));form.append(summary);
   freezeConfirm=check('implementationFreezeConfirm');freeze=button('Independently freeze saved baseline','implementationFreeze',()=>work(async ok=>{await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan/freeze',{expected_approved_opportunity:view.source.opportunity_reference,expected_revision:view.latest_revision,expected_access_revision:view.access_revision,expected_baseline_sha256:view.artifact.baseline_sha256,user_confirmed:freezeConfirm.checked});if(ok()){await load(ok);if(ok())note('Named independent baseline review recorded. Spending and execution remain unauthorized.');}}));freezeConfirm.disabled=!view.can_freeze;form.append(label('I independently reviewed the exact saved baseline and its current opportunity budget context',freezeConfirm),freeze);
   reportList=el('fieldset');reportList.id='implementationReports';form.append(reportList);reportConfirm=check('implementationReportConfirm');reportConfirm.disabled=!writable()||!view.artifact;
   saveReports=button('Save changed human phase reports','implementationReportSave',()=>work(async ok=>{const changedReports=[...reportDirty].sort().map(id=>reports.get(id));await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan/reports',{reports:changedReports,expected_revision:view.latest_revision,expected_access_revision:view.access_revision,expected_baseline_sha256:view.artifact.baseline_sha256,user_confirmed:reportConfirm.checked,organization_sharing_confirmed:reportConfirm.checked});if(ok()){await load(ok);if(ok())note('Human reports saved. The frozen baseline, its author and reviewer were preserved.');}}));form.append(label('I confirm sharing these changed human-reported amounts, dates, statuses and remaining estimates; none are independently verified',reportConfirm),saveReports);
   exportButton=button('Download saved implementation plan','implementationExport',()=>work(async ok=>{if(!view.artifact||baselineDirty||reportDirty.size)return;const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan/export?revision='+view.artifact.revision);if(ok())download(r);}));
   const history=el('select');history.id='implementationRevision';options(history,Array.from({length:Math.min(50,view.latest_revision)},(_,i)=>[String(i+1),'Plan revision '+(i+1)]));if(view.artifact)history.value=String(view.artifact.revision);
   const erase=button('Erase all implementation-plan history','implementationDelete',()=>work(remove));erase.disabled=!view.permissions.delete||!view.latest_revision;
   if(view.artifact)form.append(historyControls());
   form.append(exportButton,label('Implementation plan history',history),button('Open implementation-plan revision','implementationHistory',()=>work(ok=>load(ok,Number(history.value)))),erase);host.append(form);drawPhases();if(busy)form.disabled=true;
  }
  async function remove(ok){if(!window.confirm('Erase all phase baselines, reports and review history? Source opportunity/process records remain.'))return;await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/implementation-plan/delete',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(ok()){await load(ok);if(ok())note('Implementation-plan history erased. Opportunity and process records remain.');}}
  window.addEventListener('owg-shared-opportunity-changed',event=>{if(!selected||event.detail?.workflow_id!==selected.process.workflow_id||!view)return;epoch++;busy=false;sourceStale=true;if(form)form.disabled=true;const summary=host.querySelector('#implementationSummary');if(summary)summary.replaceChildren(el('p','The opportunity changed after this snapshot. Current review and budget status are withheld until reload.'));update();note('Opportunity changed; reload the implementation plan before acting. Unsaved entries remain on this page until reload.');});
  return {clear,select};
 }
 window.owgImplementationPlans={create};
})();
