/* Explicit, non-executing decision/exception review ledger.
 * Conditions are labels for humans and future authorized implementers; OWG
 * never evaluates them, routes business cases or grants action permission.
 */
(() => {
  'use strict';
  const labels={
    pause_for_review:'Pause and request human review',
    request_source_recheck:'Request authoritative source recheck',
    escalate_to_owner:'Escalate to responsible process owner'
  };
  const elem=(tag,text)=>{
    const node=document.createElement(tag);
    if(text!==undefined&&text!==null)node.textContent=String(text);
    return node;
  };
  const option=(value,label)=>{
    const el=elem('option',label);el.value=value;return el;
  };
  function render(state,host,rerender) {
    if(!state||!host||typeof rerender!=='function')return;
    const section=elem('section');section.id='owgDecisionLedger';
    const title=elem('h4','Decisions and exceptions · manual-review rules');
    section.appendChild(title);
    section.appendChild(elem('p','Declare conditions an implementer must handle. These are human-authored labels, not evaluated rules, evidence of company policy, or authorization to run a tool.'));
    const existing=elem('ol');
    (state.rules||[]).forEach((rule,i)=>{
      const item=elem('li',
        'After step '+(rule.step_index+1)+' · '+String(rule.condition_label)+
        ' ('+String(rule.kind=== 'decision_point'?'decision':'exception')+') → '+
        (labels[rule.response]||'manual review')
      );
      const remove=elem('button','Remove');
      remove.type='button';remove.className='secondary';
      remove.addEventListener('click',()=>{
        state.rules.splice(i,1);
        state.reviewed=false;
        rerender();
      });
      item.appendChild(remove);existing.appendChild(item);
    });
    if(!state.rules?.length)
      section.appendChild(elem('p','No decision or exception review labels declared yet.'));
    else section.appendChild(existing);

    const form=elem('div');form.className='owg-decision-controls';
    const stepLabel=elem('label','After step');
    const step=elem('select');step.id='owgDecisionStep';
    state.target.forEach((name,i)=>step.appendChild(option(String(i),(i+1)+'. '+String(name))));
    stepLabel.appendChild(step);
    const kindLabel=elem('label','Condition type');
    const kind=elem('select');kind.id='owgDecisionKind';
    kind.append(option('decision_point','Decision point'),option('exception_condition','Exception'));
    kindLabel.appendChild(kind);
    const conditionLabel=elem('label','Condition label (human supplied)');
    const condition=elem('input');condition.type='text';condition.id='owgDecisionCondition';
    condition.maxLength=64;condition.placeholder='e.g. Missing approval';
    conditionLabel.appendChild(condition);
    const responseLabel=elem('label','Safe response');
    const response=elem('select');response.id='owgDecisionResponse';
    Object.entries(labels).forEach(([value,label])=>response.appendChild(option(value,label)));
    responseLabel.appendChild(response);
    const add=elem('button','Declare review condition');
    add.type='button';add.className='secondary';
    const notice=elem('p');notice.id='owgDecisionNotice';notice.setAttribute('role','status');
    add.addEventListener('click',()=>{
      const item={
        step_index:Number(step.value),
        kind:kind.value,
        condition_label:condition.value.trim(),
        response:response.value
      };
      try{
        // Validate the whole candidate before committing to state. Never turn
        // this user-provided text into executable JavaScript.
        window.__owgProcessDesignModel.validateRules(state.target,[...(state.rules||[]),item]);
        state.rules.push(item);
        state.reviewed=false;
        rerender();
      }catch(error){notice.textContent=String(error.message||'Invalid declared review condition.');}
    });
    form.append(stepLabel,kindLabel,conditionLabel,responseLabel,add);
    section.append(form,notice);
    const css=elem('style');css.textContent=[
      '#owgDecisionLedger{border-top:1px solid #d9dfd4;margin-top:14px;padding-top:12px}',
      '#owgDecisionLedger .owg-decision-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;align-items:end}',
      '#owgDecisionLedger .owg-decision-controls label{display:grid;gap:4px}',
      '#owgDecisionLedger select,#owgDecisionLedger input{max-width:100%;width:100%}',
      '#owgDecisionLedger li{padding:4px 0;overflow-wrap:anywhere}',
      '#owgDecisionLedger li button{margin-left:9px}'
    ].join('\n');
    section.prepend(css);
    host.appendChild(section);
  }
  window.owgDecisionLedger={render};
})();