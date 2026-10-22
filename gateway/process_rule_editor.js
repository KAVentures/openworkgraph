/* Shared, offline declared-condition editor. Facts, tools and policy never execute. */
(() => {
  'use strict';
  const styleNonce=document.currentScript?.nonce;
  const operators=['eq','ne','gt','gte','lt','lte','in','not_in','exists','not_exists'];
  const el=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node;};
  function scalar(value){return value===null||typeof value==='boolean'||
    (typeof value==='string'&&value.length<=240&&!/[\ud800-\udfff]/u.test(value))||
    (typeof value==='number'&&Number.isFinite(value)&&Math.abs(value)<=1e15);}
  function parseCondition(path,operator,literal){
    if(typeof path!=='string'||!path.startsWith('/')||path.length>240||path.split('/').length>9||
       /~(?![01])|[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(path))throw new Error('Use a JSON pointer with at most eight segments, such as /approval/status.');
    if(!operators.includes(operator))throw new Error('Choose a supported comparison.');
    let expected=null;
    if(!['exists','not_exists'].includes(operator)){
      try{expected=JSON.parse(literal);}catch{throw new Error('Enter a JSON scalar or membership list, such as 1000, "approved", true or ["a","b"].');}
    }
    if(['in','not_in'].includes(operator)){
      if(!Array.isArray(expected)||!expected.length||expected.length>20||!expected.every(scalar))throw new Error('Membership requires 1–20 bounded scalar values.');
    }else if(!scalar(expected))throw new Error('Choose a bounded scalar expected value.');
    if(['gt','gte','lt','lte'].includes(operator)&&typeof expected!=='number')throw new Error('Ordering requires a finite numeric expected value.');
    return {path,operator,expected};
  }
  function render(rule,host,onChange){
    const section=el('details');section.className='owg-rule-predicate';section.appendChild(el('summary','Structured conditions · '+rule.id));host.appendChild(section);
    function update(){rule.user_confirmed=false;onChange?.();paint();}
    function paint(){
      const summary=el('summary','Structured conditions · '+rule.id);section.replaceChildren(summary);
      section.appendChild(el('p','Optional declared comparisons over supplied case facts. No expression code, source reads or automatic branching. A change clears rule confirmation. Clauses take effect only after Add declared condition.'));
      const label=el('label','Combine conditions '),mode=el('select');mode.setAttribute('aria-label','Predicate combination '+rule.id);
      for(const [value,text] of [['all','All conditions'],['any','Any condition']]){const option=el('option',text);option.value=value;mode.appendChild(option);}
      mode.value=rule.predicate?.match||'all';label.appendChild(mode);section.appendChild(label);
      mode.addEventListener('change',()=>{if(rule.predicate){rule.predicate.match=mode.value;update();}});
      const list=el('ol');
      (rule.predicate?.conditions||[]).forEach((condition,index)=>{
        const item=el('li',condition.path+' '+condition.operator+(['exists','not_exists'].includes(condition.operator)?'':' '+JSON.stringify(condition.expected)));
        const remove=el('button','Remove condition '+(index+1)+' for '+rule.id);remove.type='button';remove.className='secondary';
        remove.addEventListener('click',()=>{rule.predicate.conditions.splice(index,1);if(!rule.predicate.conditions.length)delete rule.predicate;update();});item.appendChild(remove);list.appendChild(item);
      });section.appendChild(list);
      const form=el('div');form.className='owg-rule-clause-controls';
      const field=(title,node)=>{const label=el('label',title+' ');node.setAttribute('aria-label',title);label.appendChild(node);form.appendChild(label);return node;};
      const path=field('Predicate field '+rule.id,el('input'));path.type='text';path.maxLength=240;path.placeholder='/approval/status';
      const operator=field('Predicate operator '+rule.id,el('select'));
      for(const value of operators){const option=el('option',value);option.value=value;operator.appendChild(option);}operator.value='eq';
      const literal=field('Predicate expected value '+rule.id,el('input'));literal.type='text';literal.maxLength=8192;literal.value='null';
      operator.addEventListener('change',()=>{literal.disabled=['exists','not_exists'].includes(operator.value);});
      const notice=el('p');notice.setAttribute('role','status');
      const add=el('button','Add declared condition for '+rule.id);add.type='button';add.className='secondary';add.disabled=(rule.predicate?.conditions.length||0)>=8;
      add.addEventListener('click',()=>{
        try{
          const condition=parseCondition(path.value,operator.value,literal.value);
          if((rule.predicate?.conditions.length||0)>=8)throw new Error('Maximum eight conditions per rule.');
          rule.predicate={match:mode.value,conditions:[...(rule.predicate?.conditions||[]),condition]};update();section.open=true;
        }catch(error){notice.textContent=error.message;}
      });form.append(add,notice);section.appendChild(form);
      const style=el('style');if(styleNonce)style.nonce=styleNonce;style.textContent='.owg-rule-predicate label{display:block;margin:8px 0}.owg-rule-predicate input,.owg-rule-predicate select{width:100%;max-width:100%;min-width:0;box-sizing:border-box}.owg-rule-predicate li{overflow-wrap:anywhere}.owg-rule-predicate li button{margin-left:8px}';section.appendChild(style);
    }
    paint();return section;
  }
  window.owgProcessRuleEditor=Object.freeze({render,parseCondition});
})();
