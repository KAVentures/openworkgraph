/* Human-reviewed AS-IS / TO-BE workflow drafting over explicit local CSV observations.
 * No execution, policy inference, persistence, cloud sync, or extra permissions.
 */
(() => {
  'use strict';
  const FORMAT='openworkgraph.proposed-process-design.v1';
  const MAX_STEPS=40;
  let state=null;
  const id='owgProcessDesigner';

  function validStep(value) {
    return typeof value==='string' && /^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(value.trim());
  }
  function safePath(path) {
    if (!Array.isArray(path) || path.length<1 || path.length>MAX_STEPS ||
        !path.every(validStep)) throw new Error('A complete, structurally labeled path of 1–40 steps is required.');
    return path.map(step=>step.trim());
  }
  function diff(baseline, target) {
    // Multiset comparison preserves repeated activities; it does NOT pretend
    // to know whether a reordering changed the business meaning.
    const count=(xs)=>xs.reduce((map,item)=>map.set(item,(map.get(item)||0)+1),new Map());
    const before=count(baseline), after=count(target);
    const added=[], removed=[];
    for(const [step,n] of after)for(let i=0;i<Math.max(0,n-(before.get(step)||0));i++)added.push(step);
    for(const [step,n] of before)for(let i=0;i<Math.max(0,n-(after.get(step)||0));i++)removed.push(step);
    return {
      added, removed,
      order_changed: added.length===0 && removed.length===0 &&
        baseline.some((step,i)=>target[i]!==step),
      changes_are_owner_proposals_not_observed:true
    };
  }
  function packet(report,variant,target,goal,reviewed) {
    if(!reviewed)throw new Error('Explicit review is required before exporting a proposed process.');
    if(report?.format!=='openworkgraph.process-mining-csv-preview.v1' ||
       variant?.path_truncated || report?.input_rows<1 ||
       !Array.isArray(variant?.observed_path))throw new Error('Only a complete observed CSV variant can be used as a design baseline.');
    const baseline=safePath(variant.observed_path),proposal=safePath(target);
    if(typeof goal!=='string'||goal.trim().length<3||goal.trim().length>500)
      throw new Error('Describe the desired business outcome (3–500 characters).');
    return {
      format:FORMAT,
      status:'human_reviewed_draft_not_approved_policy',
      based_on:'explicit_local_csv_observed_variant',
      business_goal_declared_by_user:goal.trim(),
      as_is: {
        path:baseline,
        supporting_case_count:Number(variant.case_count)||0,
        total_case_count:Number(report.case_count)||0,
        source_observation_not_policy:true,
        customer_outcome_verified:false
      },
      to_be: {
        proposed_path:proposal,
        step_origins:proposal.map(step=>({step,source:'user_confirmed_design_entry'})),
        change_summary:diff(baseline,proposal),
        proposed_not_deployed:true
      },
      uncertainties:[
        'Decision rules and exceptions are not established by the CSV event log.',
        'Source-system identity and case completion have not been independently verified.',
        'The proposed process needs business-owner authorization and tool mapping before implementation.'
      ],
      source_case_ids_included:false,
      uploaded_data_retained_by_owg:false,
      approved_business_policy:false,
      authorized_agent_actions:false,
      agent_execution_triggered:false,
      user_reviewed_export:true
    };
  }
  const elem=(tag,value,cls)=>{
    const x=document.createElement(tag);
    if(value!==undefined&&value!==null)x.textContent=String(value);
    if(cls)x.className=cls;
    return x;
  };
  function btn(title,fn){
    const x=elem('button',title,'secondary');x.type='button';
    x.addEventListener('click',fn);return x;
  }
  function setMessage(message){
    const n=document.getElementById('owgDesignerStatus');
    if(n)n.textContent=String(message||'');
  }
  function reset(){
    state=null;
    document.getElementById(id)?.remove();
  }
  function selected(){
    const goal=document.getElementById('owgDesignerGoal');
    if(goal&&state)state.goal=goal.value;
    const review=document.getElementById('owgDesignerReviewed');
    if(review&&state)state.reviewed=review.checked;
  }
  function mutate(fn){
    if(!state)return;
    selected();
    try{
      fn(state.target);
      state.reviewed=false;
      render();
    }catch(error){setMessage(error.message||'Could not change step.');}
  }
  function render(){
    if(!state)return;
    let host=document.getElementById(id);
    if(!host){
      const parent=document.getElementById('owgProcessCSV');
      if(!parent)return;
      host=elem('section',null,'card');host.id=id;
      parent.appendChild(host);
    }
    host.replaceChildren();
    host.appendChild(elem('h3','Design the improved workflow · AS-IS → TO-BE'));
    host.appendChild(elem('p','The observed path is evidence, not company policy. Every target change here is a user proposal, not an inferred requirement or permission to run an AI agent.','muted'));
    const layout=elem('div',null,'owg-designer-layout');
    const observed=elem('section');
    observed.appendChild(elem('h4','AS-IS · observed variant'));
    const observedList=elem('ol');
    state.baseline.forEach(step=>observedList.appendChild(elem('li',step)));
    observed.appendChild(observedList);
    observed.appendChild(elem('p',state.variant.case_count+' cases with this exact observed path. The CSV does not establish a verified business outcome.','muted'));
    const target=elem('section');
    target.appendChild(elem('h4','TO-BE · editable proposal'));
    const targetList=elem('ol');
    state.target.forEach((step,index)=>{
      const li=elem('li');
      const label=elem('span',step);li.appendChild(label);
      const actions=elem('div',null,'owg-design-row-actions');
      const up=btn('↑',()=>mutate(steps=>{if(index>0)[steps[index-1],steps[index]]=[steps[index],steps[index-1]];}));
      up.setAttribute('aria-label','Move step '+(index+1)+' up');up.disabled=index===0;
      const down=btn('↓',()=>mutate(steps=>{if(index<steps.length-1)[steps[index],steps[index+1]]=[steps[index+1],steps[index]];}));
      down.setAttribute('aria-label','Move step '+(index+1)+' down');down.disabled=index===state.target.length-1;
      const remove=btn('Remove',()=>mutate(steps=>{if(steps.length>1)steps.splice(index,1);else throw new Error('At least one target step is required.');}));
      remove.disabled=state.target.length<=1;
      actions.append(up,down,remove);li.appendChild(actions);targetList.appendChild(li);
    });
    target.appendChild(targetList);
    const addRow=elem('div',null,'owg-design-add');
    const newStep=elem('input');newStep.id='owgDesignerNewStep';
    newStep.maxLength=64;newStep.placeholder='New step (structural label)';
    newStep.setAttribute('aria-label','New proposed activity label');
    const add=btn('Add step',()=>{
      const value=newStep.value.trim();
      if(!validStep(value)){setMessage('Use a structural activity label (1–64 characters, letters, numbers, spaces, dash or underscore).');return;}
      mutate(steps=>{
        if(steps.length>=MAX_STEPS)throw new Error('Maximum of 40 target steps.');
        steps.push(value);
      });
    });
    addRow.append(newStep,add);target.appendChild(addRow);
    layout.append(observed,target);host.appendChild(layout);
    const outcome=elem('label','Desired business outcome (user supplied)');
    outcome.htmlFor='owgDesignerGoal';
    host.appendChild(outcome);
    const goal=elem('textarea');goal.id='owgDesignerGoal';goal.rows=2;
    goal.maxLength=500;goal.value=state.goal;
    goal.placeholder='What should this process accomplish?';
    goal.addEventListener('input',()=>{state.goal=goal.value;state.reviewed=false;const check=document.getElementById('owgDesignerReviewed');if(check)check.checked=false;});
    host.appendChild(goal);
    const changes=diff(state.baseline,state.target);
    host.appendChild(elem('p','Proposed changes: '+changes.added.length+' added, '+changes.removed.length+' removed'+
      (changes.order_changed?' · order changed':'')+'. Review all process rules and exceptions separately.','muted'));
    const checkLabel=elem('label',null,'owg-design-confirm');
    const check=elem('input');check.type='checkbox';check.id='owgDesignerReviewed';check.checked=state.reviewed;
    check.addEventListener('change',()=>{state.reviewed=check.checked;});
    checkLabel.append(check,document.createTextNode(' I reviewed this proposed process. Export does not approve policy or authorize agent actions.'));
    host.appendChild(checkLabel);
    const buttons=elem('div',null,'owg-design-buttons');
    buttons.appendChild(btn('Export reviewed draft as JSON',exportDraft));
    buttons.appendChild(btn('Discard this draft',()=>{
      if(typeof window.confirm==='function'&& !window.confirm('Discard the unsaved proposed workflow?'))return;
      reset();
    }));
    host.appendChild(buttons);
    const status=elem('p','No data has been saved, sent to cloud AI, or executed.','muted');
    status.id='owgDesignerStatus';status.setAttribute('role','status');host.appendChild(status);
    const css=elem('style');css.textContent=[
      '#owgProcessDesigner .owg-designer-layout{display:grid;grid-template-columns:1fr 1fr;gap:16px}',
      '#owgProcessDesigner .owg-designer-layout section{border:1px solid #d9dfd4;border-radius:8px;padding:12px;min-width:0}',
      '#owgProcessDesigner .owg-designer-layout li{margin:6px 0;overflow-wrap:anywhere}',
      '#owgProcessDesigner .owg-design-row-actions{display:inline-flex;gap:4px;margin-left:10px;flex-wrap:wrap}',
      '#owgProcessDesigner .owg-design-row-actions button{padding:3px 7px;font-size:12px}',
      '#owgProcessDesigner .owg-design-add,#owgProcessDesigner .owg-design-buttons{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}',
      '#owgProcessDesigner .owg-design-confirm{display:block;margin:12px 0}',
      '#owgProcessDesigner #owgDesignerGoal{display:block;width:100%;max-width:680px}',
      '@media(max-width:720px){#owgProcessDesigner .owg-designer-layout{grid-template-columns:1fr}}'
    ].join('\n');
    host.prepend(css);
  }
  function exportDraft(){
    if(!state)return;
    selected();
    let output;
    try{
      output=packet(state.report,state.variant,state.target,state.goal,state.reviewed);
    }catch(error){setMessage(error.message||'Review the draft before exporting.');return;}
    output.exported_at=new Date().toISOString();
    const blob=new Blob([JSON.stringify(output,null,2)+'\n'],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    const anchor=elem('a');anchor.href=url;anchor.download='openworkgraph-proposed-process.json';
    anchor.hidden=true;document.body.appendChild(anchor);anchor.click();anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(url),3000);
    setMessage('Human-reviewed proposal exported locally; no process or agent was deployed.');
  }
  function open(report,variant) {
    try{
      if(!report||!variant||variant.path_truncated)throw new Error(
        'This observed path is incomplete. Select a complete variant before drafting.');
      const baseline=safePath(variant.observed_path);
      if(state){
        if(typeof window.confirm==='function'&&!window.confirm('Replace the unsaved proposed workflow?'))return;
      }
      state={report,variant,baseline,target:baseline.slice(),goal:'',reviewed:false};
      render();
      document.getElementById(id)?.scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){
      const status=document.getElementById('owgProcessStatus');
      if(status)status.textContent=String(error.message||'Could not open designer.');
    }
  }
  // The pure model is deliberately exported for regression tests, not agents.
  window.owgProcessDesigner={open,reset,hasDraft:()=>!!state};
  window.__owgProcessDesignModel={validStep,diff,packet};
})();