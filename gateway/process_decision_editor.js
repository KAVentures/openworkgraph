/* Offline table declarations over existing rules. No facts or source documents retained. */
(() => {
  'use strict';
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=(text,fn)=>{const n=el('button',text);n.type='button';n.className='secondary';n.onclick=fn;return n;};
  function summary(model,host){
    if(!model.decision_tables?.length)return;
    host.appendChild(el('h4','Declared decision tables'));
    host.appendChild(el('p','UNIQUE requires exactly one matching row and no unknown competitors. Dates use an inclusive start and exclusive end. Supplied-case checks are not exhaustive policy validation. Agent implementation export is unavailable for designs with tables.'));
    for(const table of model.decision_tables){
      const box=el('section');box.appendChild(el('h5',table.id+' · '+table.decision_activity_id+' · '+(table.user_confirmed?'user confirmed':'unconfirmed')));
      box.appendChild(el('p','Effective '+table.effective_from+' to '+(table.effective_until||'no declared end')+' · Source basis: '+table.source_basis.label+' · '+table.source_basis.document_sha256+' (human-declared digest, not independently verified)'));
      const list=el('ul');for(const row of table.rows)list.appendChild(el('li',row.id+' · rule '+row.rule_id+' → '+row.target_activity_id));box.appendChild(list);host.appendChild(box);
    }
  }
  function render(model,host,changed){
    const section=el('section');host.appendChild(section);
    const invalidate=()=>{for(const t of model.decision_tables||[])t.user_confirmed=false;changed(true);section.querySelectorAll('input[data-table-confirmation]').forEach(n=>{n.checked=false;});};
    function paint(){
      section.replaceChildren(el('h4','Decision tables'));
      section.appendChild(el('p','Use confirmed structured rules on each outgoing branch. Tables describe a single choice; no first-row precedence or default branch. Edit rules above, then add or synchronize rows. A source digest identifies your declared reference document and does not establish its authority.'));
      const decisions=model.activities.filter(a=>a.kind==='decision');
      const pick=el('select');pick.setAttribute('aria-label','Decision activity for new table');
      for(const d of decisions.filter(d=>!(model.decision_tables||[]).some(t=>t.decision_activity_id===d.id))){const o=el('option',d.id);o.value=d.id;pick.appendChild(o);}
      const notice=el('p');notice.setAttribute('role','status');
      const rowsFor=(id)=>{
        const edges=model.transitions.filter(e=>e.source===id);
        if(edges.length<2||edges.some(e=>!e.rule_id||!model.business_rules.some(r=>r.id===e.rule_id&&r.predicate)))throw new Error('Each branch needs a structured rule before table rows can be synchronized.');
        if(new Set(edges.map(e=>e.rule_id)).size!==edges.length)throw new Error('Each branch needs its own rule for a UNIQUE table.');
        return edges.map((e,i)=>({id:'row_'+(i+1),rule_id:e.rule_id,target_activity_id:e.target}));
      };
      const add=button('Add decision table',()=>{
        try{
          if(!pick.value||(model.decision_tables?.length||0)>=12)throw new Error('Choose an unused decision activity; maximum twelve tables.');
          const rows=rowsFor(pick.value);let i=1;while((model.decision_tables||[]).some(t=>t.id==='decision_table_'+i))i++;
          (model.decision_tables??=[]).push({id:'decision_table_'+i,decision_activity_id:pick.value,hit_policy:'UNIQUE',effective_from:'',effective_until:null,source_basis:{label:'',document_sha256:'',basis:'human_declared_document_digest',independently_verified:false},rows,user_confirmed:false,execution_authorized:false});
          invalidate();paint();
        }catch(e){notice.textContent=e.message;}
      });add.disabled=!pick.options.length;section.append(pick,add,notice);
      for(const [index,table] of (model.decision_tables||[]).entries()){
        const box=el('fieldset');box.appendChild(el('legend',table.id+' · '+table.decision_activity_id));section.appendChild(box);
        const field=(label,value,apply,max=120)=>{
          const wrap=el('label',label+' '),input=el('input');input.value=value||'';input.maxLength=max;input.setAttribute('aria-label',label);
          input.oninput=()=>{apply(input.value);invalidate();};wrap.appendChild(input);box.appendChild(wrap);return input;
        };
        field('Effective start '+table.id,table.effective_from,v=>table.effective_from=v,10).placeholder='YYYY-MM-DD';
        field('Exclusive effective end '+table.id,table.effective_until,v=>table.effective_until=v||null,10).placeholder='YYYY-MM-DD or blank';
        field('Source document label '+table.id,table.source_basis.label,v=>table.source_basis.label=v);
        field('Source document SHA256 '+table.id,table.source_basis.document_sha256,v=>table.source_basis.document_sha256=v,64);
        const list=el('ul');for(const row of table.rows)list.appendChild(el('li',row.id+' · '+row.rule_id+' → '+row.target_activity_id));box.appendChild(list);
        box.appendChild(button('Synchronize rows '+table.id,()=>{try{table.rows=rowsFor(table.decision_activity_id);invalidate();paint();}catch(e){notice.textContent=e.message;}}));
        const label=el('label',' I confirm this exact table, referenced rules, source basis and effective dates '+table.id+' '),check=el('input');check.type='checkbox';check.dataset.tableConfirmation=table.id;check.checked=table.user_confirmed;
        check.onchange=()=>{table.user_confirmed=check.checked;changed(false);};label.appendChild(check);box.appendChild(label);
        box.appendChild(button('Remove table '+table.id,()=>{model.decision_tables.splice(index,1);if(!model.decision_tables.length)delete model.decision_tables;invalidate();paint();}));
      }
    }
    paint();return section;
  }
  window.owgProcessDecisionEditor=Object.freeze({render,summary});
})();
