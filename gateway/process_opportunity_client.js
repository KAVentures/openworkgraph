/* Human assessment and named stage decisions. No persistence, spending or business tools. */
(() => {
 'use strict';
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
 const button=(text,id,fn)=>{const n=el('button',text);n.type='button';n.id=id;n.onclick=fn;return n;};
 const label=(text,n)=>{const l=el('label',text+' ');l.append(n);return l;};
 const input=(id,type='text')=>{const n=el('input');n.id=id;n.type=type;return n;};
 const select=(id,rows)=>{const n=el('select');n.id=id;for(const [v,t] of rows){const o=el('option',t);o.value=v;n.append(o);}return n;};
 const ref=p=>({workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
 const words=s=>String(s).replaceAll('_',' ');
 const questions=[['rules_based','Rules are clear'],['structured_inputs','Inputs are structured'],['process_stability','Process is stable'],['digital_inputs','Inputs arrive digitally'],['low_exception_rate','Exceptions are rare']];
 function create({inventoryHost,detailHost,call,open,onDenied}){
  let epoch=0,busy=false,selected=null,view=null,draft=null,dirty=false,page=[],after='';
  const inventory=el('div'),list=el('div'),filter=input('opportunityFilter'),inventoryStatus=el('p');inventoryStatus.id='opportunityInventoryStatus';inventoryStatus.setAttribute('role','status');
  const next=button('Next opportunity page','opportunityNext',()=>work(ok=>loadInventory(ok,after)));next.disabled=true;
  inventory.append(el('h2','Shared opportunity review'),el('p','Per-process assessments and named decisions. Scores use declared scenario assumptions; stages do not authorize execution or spending. Filters cover this loaded page only.'),
   button('Load shared opportunity pipeline','opportunityInventory',()=>work(ok=>loadInventory(ok))),label('Filter loaded opportunities by title, team, owner or stage',filter),inventoryStatus,list,next);inventoryHost.append(inventory);
  const host=el('div');detailHost.append(host);let form=null,status=null,confirm=null,save=null,exportButton=null,decisionForm=null;
  const message=text=>{inventoryStatus.textContent=text;if(status)status.textContent=text;};
  async function work(fn){if(busy)return;const e=epoch;busy=true;if(form)form.disabled=true;if(decisionForm)decisionForm.disabled=true;
   try{await fn(()=>e===epoch);}catch(error){if(e!==epoch)return;if([401,403].includes(error.status)){clear();onDenied();return;}message(error.message||'Opportunity request failed.');}
   finally{if(e===epoch){busy=false;if(form)form.disabled=false;if(decisionForm)decisionForm.disabled=dirty;}}
  }
  function clear(){epoch++;busy=false;selected=view=draft=null;dirty=false;page=[];after='';host.replaceChildren();list.replaceChildren();filter.value='';inventoryStatus.textContent='';next.disabled=true;form=decisionForm=status=null;}
  function selectRoot(data){epoch++;busy=false;selected=data;view=draft=null;dirty=false;host.replaceChildren();form=decisionForm=null;
   status=el('p');status.id='opportunityStatus';status.setAttribute('role','status');host.append(el('h3','Shared opportunity for this process'),el('p','The current process owner controls access. Coordinator/team labels are descriptive and grant no role. Assessment edits always clear opportunity approval, including after a human-reported implementation.'),button('Load root opportunity','opportunityLoad',()=>work(load)),status);
  }
  function filtered(){const q=filter.value.toLocaleLowerCase();list.replaceChildren(...page.filter(r=>[r.title,r.team_label,r.owner_actor_id,r.effective_state,r.recorded_state].join(' ').toLocaleLowerCase().includes(q)).map(r=>{
   const item=el('p',r.title+' · opportunity r'+r.revision+' · process r'+(r.process_revision||'unknown')+' · source '+words(r.source_state||'unavailable')+' · recorded '+words(r.recorded_state||'unknown')+' · current '+words(r.effective_state)+(r.team_label?' · '+r.team_label:''));
   if(r.score)item.append(el('span',' · ease '+r.score.ease_score+' · '+r.score.modeled_hours_per_month+' modeled h/month · '+words(r.score.quadrant)));
   item.append(button('Open opportunity '+r.workflow_id,'opportunityOpen_'+r.workflow_id,()=>work(()=>open(r.workflow_id))));return item;
  }));}
  async function loadInventory(ok,cursor=''){const r=await call('/v1/human/process-opportunity-pipeline?limit=50&after='+encodeURIComponent(cursor));if(!ok())return;page=r.opportunities;after=r.next_after||'';next.disabled=!r.has_more;filtered();inventoryStatus.textContent=r.opportunities.length+' opportunities in this page at load time. Refresh after changes; no company savings total or verified outcomes are implied.';}
  filter.oninput=filtered;
  function initial(){return {process:ref(selected.process),title:'Process opportunity',description:'',coordinator_label:'',team_label:'',assessment:null};}
  async function load(ok,revision){const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity'+(revision?'?revision='+revision:''));if(!ok())return;view=r;draft=structuredClone(r.artifact?.data||initial());dirty=false;render();message(!r.integrity_ok?'Opportunity integrity failed; an owner can erase history.':r.artifact?'Opened opportunity revision '+r.artifact.revision+'.':'No shared opportunity saved. Review the draft and explicitly share it.');}
  const writable=()=>view?.permissions.edit&&selected.permissions.edit&&(!view.artifact||view.artifact.revision===view.latest_revision)&&view.integrity_ok;
  function invalidateInventory(){page=[];after='';list.replaceChildren();next.disabled=true;window.dispatchEvent(new CustomEvent('owg-shared-opportunity-changed',{detail:{workflow_id:selected?.process.workflow_id}}));}
  function changed(){dirty=true;if(confirm)confirm.checked=false;if(decisionForm)decisionForm.disabled=true;update();}
  function update(){if(save)save.disabled=!writable()||!confirm.checked;if(exportButton)exportButton.disabled=dirty||!view?.artifact||!view.integrity_ok;}
  function download(value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-shared-opportunity.json';a.click();URL.revokeObjectURL(url);}
  function render(){
   if(form)form.remove();if(decisionForm)decisionForm.remove();form=el('fieldset');form.id='opportunityEditor';form.append(el('legend','Opportunity assessment draft'));
   if(!view.integrity_ok){const erase=button('Erase all opportunity history','opportunityDelete',()=>work(remove));erase.disabled=!view.permissions.delete;form.append(erase);host.append(form);return;}
   const field=(name,id,value,apply,max=120)=>{const n=input(id);n.maxLength=max;n.value=value||'';n.disabled=!writable();n.oninput=()=>{apply(n.value);changed();};form.append(label(name,n));return n;};
   field('Opportunity title','opportunityTitle',draft.title,v=>draft.title=v);
   field('Opportunity description','opportunityDescription',draft.description,v=>draft.description=v,2000);
   field('Coordinator label (no access grant)','opportunityCoordinator',draft.coordinator_label,v=>draft.coordinator_label=v);
   field('Team label (no access grant)','opportunityTeam',draft.team_label,v=>draft.team_label=v);
   form.append(el('p','Current root owner: '+view.owner_actor_id+' · pinned model r'+draft.process.revision+' · '+draft.process.content_sha256));
   if(writable())form.append(button('Refresh opportunity process pin','opportunityRefresh',()=>work(async ok=>{const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id));if(!ok())return;if(r.archived)throw new Error('Restore this process before reassessing.');draft.process=ref(r.process);changed();render();})));
   if(draft.assessment){
    form.append(el('p','Ease answers:1 means low agreement;5 means high agreement. Impact and implementation-cost assumptions come from the pinned process scenario.'));
    for(const [key,text] of questions){const n=select('opportunityEase_'+key,[1,2,3,4,5].map(i=>[String(i),String(i)]));n.value=String(draft.assessment.ease[key]);n.disabled=!writable();n.onchange=()=>{draft.assessment.ease[key]=Number(n.value);changed();};form.append(label(text,n));}
    const money=select('opportunityBudgetCurrency',['USD','EUR','GBP','SEK'].map(v=>[v,v]));money.value=draft.assessment.budget_currency;money.disabled=!writable();money.onchange=()=>{draft.assessment.budget_currency=money.value;changed();};form.append(label('Implementation budget currency (context only)',money));
    field('Implementation budget (context only, no spending grant)','opportunityBudget',draft.assessment.implementation_budget,v=>draft.assessment.implementation_budget=v,13);
    const compliance=input('opportunityCompliance','checkbox');compliance.checked=draft.assessment.compliance_review_required;compliance.disabled=!writable();compliance.onchange=()=>{draft.assessment.compliance_review_required=compliance.checked;changed();};form.append(label('Require a reviewer declaration that compliance review is complete',compliance));
    if(writable())form.append(button('Remove draft assessment','opportunityRemoveAssessment',()=>{draft.assessment=null;changed();render();}));
   }else if(writable())form.append(button('Add opportunity assessment','opportunityAddAssessment',()=>{draft.assessment={ease:Object.fromEntries(questions.map(([k])=>[k,1])),budget_currency:selected.process.process_model.opportunity_scenario?.currency||'USD',implementation_budget:'0.00',compliance_review_required:false};changed();render();}));
   confirm=input('opportunityConfirm','checkbox');confirm.disabled=!writable();confirm.onchange=update;
   save=button('Save opportunity assessment snapshot','opportunitySave',()=>work(async ok=>{await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity',{data:structuredClone(draft),expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:confirm.checked,organization_sharing_confirmed:confirm.checked});if(!ok())return;invalidateInventory();await load(ok);if(ok())message('Opportunity snapshot saved. Previous approval does not approve this assessment.');}));
   exportButton=button('Download saved opportunity manifest','opportunityExport',()=>work(async ok=>{if(dirty||!view.artifact)return;const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity/export?revision='+view.artifact.revision);if(ok())download(r);}));
   form.append(label('I reviewed this exact draft and explicitly confirm sharing its labels, assessment, budget context and pinned process reference with organization process readers',confirm),save,exportButton);
   const history=select('opportunityRevision',Array.from({length:view.latest_revision},(_,i)=>[String(i+1),'Opportunity revision '+(i+1)]));if(view.artifact)history.value=String(view.artifact.revision);
   const erase=button('Erase all opportunity history','opportunityDelete',()=>work(remove));erase.disabled=!view.permissions.delete||!view.latest_revision;
   form.append(label('Opportunity history',history),button('Open opportunity revision','opportunityHistory',()=>work(ok=>load(ok,Number(history.value)))),erase);
   host.append(form);
   decisionForm=el('fieldset');decisionForm.id='opportunityDecision';decisionForm.append(el('legend','Saved assessment and stage decisions'));
   if(view.artifact){const a=view.artifact;decisionForm.append(el('p','Saved opportunity r'+a.revision+' only; unsaved draft edits are excluded. Recorded stage: '+words(a.state)+' · current status: '+words(view.effective_state)+' · checked '+view.checked_at),el('p','Assessment basis: '+(a.assessment_sha256||'none')+' · assessment author: '+(a.assessment_author_actor_id||'none')));
    if(view.score)decisionForm.append(el('p','Ease '+view.score.ease_score+' /100 · '+view.score.modeled_hours_per_month+' modeled hours/month · '+words(view.score.quadrant)+'. Thresholds: ease60 and unrounded20 modeled hours/month. These are entered assumptions.'));
    for(const reason of view.approval_blockers)decisionForm.append(el('p','Approval blocked: '+words(reason)));
    decisionForm.append(button('Open pinned opportunity process','opportunityOpenSource',()=>work(()=>open(a.data.process.workflow_id,a.data.process.revision))));
    if(a.approval)decisionForm.append(el('p','Historical approval by '+a.approval.actor_id+' at '+a.approval.at+' · current approved assessment: '+view.current_approved_assessment+'. Reviewer permission is historical, not a current grant.'));
    if(a.last_decision)decisionForm.append(el('p','Last named decision: '+a.last_decision.actor_id+' → '+words(a.last_decision.target)+' · '+a.last_decision.note));
    const transition=select('opportunityTarget',view.allowed_transitions.map(v=>[v,words(v)]));const note=input('opportunityDecisionNote');note.maxLength=1000;
    const ack=input('opportunityComplianceAck','checkbox'),implemented=input('opportunityImplementation','checkbox'),reviewed=input('opportunityDecisionConfirm','checkbox');
    const decide=button('Record reviewed opportunity decision','opportunityTransition',()=>work(async ok=>{if(dirty||!reviewed.checked)throw new Error('Review the unchanged saved assessment before recording a decision.');await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity/transition',{target:transition.value,expected_revision:view.latest_revision,expected_access_revision:view.access_revision,expected_assessment_sha256:a.assessment_sha256,note:note.value,compliance_review_declared_complete:ack.checked,implementation_self_report_confirmed:implemented.checked,user_confirmed:reviewed.checked});if(!ok())return;invalidateInventory();await load(ok);if(ok())message('Named stage decision recorded. Execution, spending and outcome verification are unchanged.');}));decide.disabled=!view.allowed_transitions.length;
    for(const n of [transition,note,ack,implemented])n.onchange=n.oninput=()=>{reviewed.checked=false;};
    decisionForm.append(label('Available target stage',transition),label('Decision note',note),label('I declare the required compliance review complete; OWG has not verified its evidence',ack),label('I explicitly report implementation by a human; this is not a verified outcome',implemented),label('I reviewed this exact saved assessment, source status and decision',reviewed),decide);
   }
   decisionForm.append(el('p','Approval records a named opportunity decision only. Budget is context, not permission to spend. Implemented is a human self-report; no business outcome or realized savings is verified. Models, benefit plans, local pipeline and agents are unchanged.'));
   host.append(decisionForm);update();decisionForm.disabled=dirty||busy;if(busy)form.disabled=true;
  }
  async function remove(ok){if(!window.confirm('Erase all shared opportunity assessments and decisions for this root? Process designs remain.'))return;await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/opportunity/delete',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(ok()){invalidateInventory();await load(ok);if(ok())message('Opportunity history erased. Process models remain.');}}
  return {clear,select:selectRoot};
 }
 window.owgSharedOpportunity=Object.freeze({create});
})();
