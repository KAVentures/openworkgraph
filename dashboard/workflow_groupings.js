/* Explicit local classifications. No capture, family rewrite, automatic save or AI call. */
(() => {
  'use strict';
  let current=null,groups=[],ids=[],windowFilter={},busy=false,generation=0;
  const $=id=>document.getElementById(id);
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=String(text);return node;};
  const note=text=>{$('owgGroupStatus').textContent=String(text);};
  const button=(text,action)=>{const node=make('button',text);node.type='button';node.addEventListener('click',()=>run(action));return node;};
  async function run(action){
    if(busy){note('Wait for the current grouping request.');return;}
    busy=true;$('owgGroupEditor').disabled=true;
    try{await action();}catch(error){note(error.message||'Grouping request failed.');}
    finally{busy=false;$('owgGroupEditor').disabled=false;}
  }
  async function call(path,body){
    await window.__owgAuthReady;
    const response=await fetch('/v1/workflow-groupings'+path,{cache:'no-store',
      ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Invalid grouping request');
    return result;
  }
  function resetReview(){$('owgGroupConfirmed').checked=false;}
  function checked(){return [...$('owgGroupMembers').querySelectorAll('input:checked')].map(node=>node.value);}
  function confirm(){if(!$('owgGroupConfirmed').checked)throw new Error('Review the exact classification and selected executions first.');}
  function install(){
    const overview=$('panel-overview');if(!overview||$('owgWorkflowGroups'))return;
    const card=make('section');card.className='card';card.id='owgWorkflowGroups';
    card.append(make('h2','Reviewed workflow groups'),make('p','Name selected work, separate incorrectly grouped examples, or combine reviewed groups. These are your classifications of exact inferred executions, not verified case joins. Original history and automatic discovery remain unchanged.'));
    card.appendChild(button('Load reviewed workflow groups',load));
    const inventory=make('div');inventory.id='owgGroupInventory';card.appendChild(inventory);
    const status=make('p','Use the workflow explorer to select examples, then name or regroup them. Nothing is saved automatically.');status.id='owgGroupStatus';status.setAttribute('role','status');card.appendChild(status);
    const style=make('style','#owgWorkflowGroups label{display:block;overflow-wrap:anywhere;margin:10px 0} #owgWorkflowGroups p{overflow-wrap:anywhere} #owgWorkflowGroups fieldset{min-width:0} #owgWorkflowGroups button{max-width:100%;white-space:normal;overflow-wrap:anywhere} #owgWorkflowGroups input:not([type=checkbox]),#owgWorkflowGroups select{width:100%;max-width:100%;box-sizing:border-box}');card.appendChild(style);
    const editor=make('fieldset');editor.id='owgGroupEditor';editor.hidden=true;editor.appendChild(make('legend','Review an exact classification'));
    const label=make('label','Group name '),title=make('input');title.id='owgGroupTitle';title.maxLength=120;title.setAttribute('aria-label','Reviewed workflow group name');title.addEventListener('input',resetReview);label.appendChild(title);editor.appendChild(label);
    editor.appendChild(make('p','Checked executions are included when saving this group. Split or move acts only on checked executions. References may become unavailable after source history or derivation changes; this group retains no original event content.'));
    const members=make('div');members.id='owgGroupMembers';editor.appendChild(members);
    const consent=make('label'),checkbox=make('input');checkbox.id='owgGroupConfirmed';checkbox.type='checkbox';
    consent.append(checkbox,make('span',' I reviewed this exact name, selected executions and any split or target group.'));editor.appendChild(consent);
    editor.appendChild(button('Save reviewed group',save));
    const splitLabel=make('label','New group name for checked executions '),split=make('input');split.id='owgGroupSplitTitle';split.maxLength=120;split.setAttribute('aria-label','Split workflow group name');split.addEventListener('input',resetReview);splitLabel.appendChild(split);editor.appendChild(splitLabel);
    const splitButton=button('Split checked executions into a new group',()=>move(true));splitButton.id='owgGroupSplit';editor.appendChild(splitButton);
    const targetLabel=make('label','Move checked executions to '),target=make('select');target.id='owgGroupTarget';target.setAttribute('aria-label','Target reviewed workflow group');
    target.addEventListener('change',()=>{resetReview();targetPreview();});targetLabel.appendChild(target);editor.appendChild(targetLabel);
    const targetInfo=make('p');targetInfo.id='owgGroupTargetInfo';editor.appendChild(targetInfo);
    const moveButton=button('Move checked executions to target group',()=>move(false));moveButton.id='owgGroupMove';editor.appendChild(moveButton);
    const historyButton=button('Inspect group revision history',history);historyButton.id='owgGroupHistoryButton';editor.appendChild(historyButton);
    const remove=button('Delete this reviewed group',forget);remove.id='owgGroupForget';editor.appendChild(remove);
    const historyOutput=make('div');historyOutput.id='owgGroupHistory';editor.appendChild(historyOutput);card.appendChild(editor);overview.appendChild(card);
  }
  function targetPreview(){
    const target=groups.find(group=>group.workflow_id===$('owgGroupTarget').value);
    $('owgGroupTargetInfo').textContent=target?'Target: '+target.title+' · revision '+target.revision+' · existing references: '+target.execution_grouping.execution_ids.join(', '):'Select a target. Moving all checked members combines groups; an emptied source keeps its revision history.';
  }
  function renderMembers(executions=[]){
    const host=$('owgGroupMembers');host.replaceChildren();
    for(const [index,id] of ids.entries()){
      const execution=executions.find(item=>item.execution_id===id),label=make('label'),check=make('input');
      check.type='checkbox';check.checked=true;check.value=id;check.setAttribute('aria-label','Include grouping execution '+(index+1));check.addEventListener('change',resetReview);
      label.append(check,make('span',id+(execution?' · '+(execution.started_at||'time not observed')+' · '+(Number.isFinite(execution.duration_seconds)?Math.round(execution.duration_seconds)+' s':'duration not observed')+' · '+(execution.actor_kind||'actor type not observed'):' · source details not currently resolved')));
      if(Array.isArray(execution?.steps))label.appendChild(make('p',execution.steps.slice(0,12).join(' → ')+(execution.steps.length>12?' · additional steps omitted':'')));
      host.appendChild(label);
    }
    if(!ids.length)host.appendChild(make('p','This group is empty. Its earlier classifications remain in revision history.'));
  }
  function renderEditor(){
    $('owgGroupEditor').hidden=false;$('owgGroupTitle').value=current?.title||$('owgGroupTitle').value;
    $('owgGroupSplitTitle').value='';$('owgGroupHistory').replaceChildren();resetReview();renderMembers();
    for(const id of ['owgGroupSplit','owgGroupMove','owgGroupForget','owgGroupHistoryButton'])$(id).disabled=!current;
    renderTargets(true);
  }
  function renderTargets(reset=false){
    const target=$('owgGroupTarget'),previous=reset?'':target.value;target.replaceChildren();const empty=make('option','Choose a target group');empty.value='';target.appendChild(empty);
    for(const group of groups.filter(item=>item.workflow_id!==current?.workflow_id)){
      const option=make('option',group.title+' · r'+group.revision+' · '+group.execution_grouping.execution_ids.length+' executions');option.value=group.workflow_id;target.appendChild(option);
    }
    target.value=groups.some(group=>group.workflow_id===previous&&group.workflow_id!==current?.workflow_id)?previous:'';targetPreview();resetReview();
  }
  async function sourceDetails(attempt){
    if(!ids.length)return;
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/workflow-evidence?'+new URLSearchParams({execution_ids:ids.join(','),max_runs:ids.length,source_event_limit:25000,max_events_per_run:1,include_canonical_evidence:false,...windowFilter}),{cache:'no-store'});
      if(!response.ok)throw new Error('Source execution details are unavailable in this bounded history window. Stored classifications remain separate from retained evidence.');
      const result=await response.json();if(attempt!==generation)return;
      renderMembers(result.executions||[]);
      if(current)window.owgWorkflowExplorer?.showReviewedGroup?.({bundle:result,label:current.title,workflow_id:current.workflow_id,revision:current.revision,window:windowFilter});
    }catch(error){if(attempt===generation)note(error.message);}
  }
  async function load(){
    const result=await call('');groups=result.groups;
    if(!$('owgGroupEditor').hidden)renderTargets();
    const host=$('owgGroupInventory');host.replaceChildren();
    for(const group of groups)host.appendChild(button(group.title+' · r'+group.revision+' · '+group.execution_grouping.execution_ids.length+' executions',()=>open(group.workflow_id)));
    if(!groups.length)host.appendChild(make('p','No reviewed groups saved.'));
    note('Loaded reviewed classifications. Automatic workflow families and original executions are unchanged.');
  }
  async function open(id){
    const attempt=++generation;current=await call('/revision?'+new URLSearchParams({workflow_id:id}));
    ids=[...current.execution_grouping.execution_ids];windowFilter={};renderEditor();window.owgWorkflowExplorer?.clearReviewed?.(current.title);await sourceDetails(attempt);
  }
  async function stage(selection){
    if(!Array.isArray(selection.execution_ids)||!selection.execution_ids.length||selection.execution_ids.length>25)throw new Error('Select between one and 25 exact execution references.');
    if(ids.length&&!window.confirm('Replace the current unsaved grouping selection?'))return;
    const attempt=++generation;current=null;ids=[...selection.execution_ids];
    windowFilter={...(selection.since?{since:selection.since}:{}),...(selection.until?{until:selection.until}:{})};
    $('owgGroupTitle').value=String(selection.title||'').slice(0,120);renderEditor();$('owgWorkflowGroups').scrollIntoView({behavior:'smooth',block:'start'});
    note('Review the selected inferred executions and enter your intended group name. This does not label future executions or establish common business cases.');
    await sourceDetails(attempt);
  }
  async function save(){
    confirm();const value=await call('',{title:$('owgGroupTitle').value,execution_ids:checked(),
      ...(current?{workflow_id:current.workflow_id,expected_revision:current.revision}:{}),...windowFilter,user_confirmed:true});
    await load();await open(value.workflow_id);note('Saved reviewed group revision '+value.revision+'. Original evidence and inferred families are unchanged.');
  }
  async function move(split){
    if(!current)throw new Error('Save the initial grouping before splitting or combining.');confirm();
    if($('owgGroupTitle').value.trim()!==current.title)throw new Error('Save the label change before moving or splitting executions.');
    const selected=checked();if(!selected.length)throw new Error('Check at least one execution to move.');
    const target=groups.find(group=>group.workflow_id===$('owgGroupTarget').value);
    if(!split&&!target)throw new Error('Choose a reviewed target group first.');
    const value=await call('/move',{source_id:current.workflow_id,source_revision:current.revision,execution_ids:selected,
      ...(split?{new_title:$('owgGroupSplitTitle').value}:{target_id:target.workflow_id,target_revision:target.revision}),user_confirmed:true});
    await load();await open(value.target.workflow_id);note('Moved only the checked classifications. Both group revisions were saved together; source histories are unchanged.');
  }
  async function history(before){
    if(!current)return;const page=await call('/history?'+new URLSearchParams({workflow_id:current.workflow_id,...(before?{before_revision:before}:{})}));
    const host=$('owgGroupHistory');host.replaceChildren();
    for(const group of page.groups)host.appendChild(make('p','Revision '+group.revision+' · '+group.title+' · '+group.execution_grouping.execution_ids.join(', ')+' · '+group.saved_at));
    if(before)host.appendChild(button('Latest classifications',()=>history()));
    if(page.has_more)host.appendChild(button('Older classifications',()=>history(page.next_before_revision)));
  }
  async function forget(){
    if(!current)return;confirm();if(!window.confirm('Delete all saved revisions of this classification? Original work history remains unchanged.'))return;
    await call('/forget',{workflow_id:current.workflow_id,expected_revision:current.revision,user_confirmed:true});
    generation++;current=null;ids=[];$('owgGroupEditor').hidden=true;window.owgWorkflowExplorer?.clearReviewed?.('');await load();note('Deleted the reviewed classification and its versions. Original executions remain unchanged.');
  }
  window.owgWorkflowGroups=Object.freeze({stage:selection=>run(()=>stage(selection))});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
