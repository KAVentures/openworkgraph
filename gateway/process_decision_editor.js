/* Offline table declarations over existing rules. No facts or source documents retained. */
(() => {
  'use strict';
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=(text,fn)=>{const n=el('button',text);n.type='button';n.className='secondary';n.onclick=fn;return n;};
  function summary(model,host){
    if(!model.decision_tables?.length)return;
    host.appendChild(el('h4','Declared decision tables'));
    host.appendChild(el('p','UNIQUE requires exactly one matching row and no unknown competitors. '+(model.decision_tables.some(t=>t.hit_policy==='FIRST')?'FIRST uses declared row order: an earlier unknown withholds later matches; later unknowns cannot displace an established first hit. ':'')+'Dates use an inclusive start and exclusive end. Supplied-case checks are not exhaustive policy validation. Agent projects require separate reviewed current-source bindings for every competing row and UTC effective dates.'));
    for(const table of model.decision_tables){
      const box=el('section');box.appendChild(el('h5',table.id+' · '+table.decision_activity_id+(table.hit_policy==='FIRST'?' · FIRST':'')+' · '+(table.user_confirmed?'user confirmed':'unconfirmed')));
      box.appendChild(el('p','Effective '+table.effective_from+' to '+(table.effective_until||'no declared end')+' · Source basis: '+table.source_basis.label+' · '+table.source_basis.document_sha256+' (human-declared digest, not independently verified)'));
      const list=el('ul');for(const [index,row] of table.rows.entries())list.appendChild(el('li',(table.hit_policy==='FIRST'?(index+1)+'. ':'')+row.id+' · rule '+row.rule_id+' → '+row.target_activity_id));box.appendChild(list);host.appendChild(box);
    }
  }
  function render(model,host,changed){
    const section=el('section');host.appendChild(section);
    const invalidate=()=>{for(const t of model.decision_tables||[])t.user_confirmed=false;changed(true);section.querySelectorAll('input[data-table-confirmation]').forEach(n=>{n.checked=false;});};
    function paint(){
      section.replaceChildren(el('h4','Decision tables'));
      section.appendChild(el('p','Use confirmed structured rules on each outgoing branch. UNIQUE requires one match with no unknown competitor. Explicit FIRST uses reviewed row order; an earlier unknown withholds a later match. There is no implicit default branch. Edit rules above, then add or synchronize rows. A source digest identifies your declared reference document and does not establish its authority.'));
      const decisions=model.activities.filter(a=>a.kind==='decision');
      const pick=el('select');pick.setAttribute('aria-label','Decision activity for new table');
      for(const d of decisions.filter(d=>!(model.decision_tables||[]).some(t=>t.decision_activity_id===d.id))){const o=el('option',d.id);o.value=d.id;pick.appendChild(o);}
      const notice=el('p');notice.setAttribute('role','status');
      const rowsFor=(id,previous=[])=>{
        const edges=model.transitions.filter(e=>e.source===id);
        if(edges.length<2||edges.some(e=>!e.rule_id||!model.business_rules.some(r=>r.id===e.rule_id&&r.predicate)))throw new Error('Each branch needs a structured rule before table rows can be synchronized.');
        if(new Set(edges.map(e=>e.rule_id)).size!==edges.length)throw new Error('Each branch needs its own structured rule.');
        if(!previous.length)return edges.map((e,i)=>({id:'row_'+(i+1),rule_id:e.rule_id,target_activity_id:e.target}));
        // FIRST synchronizes graph membership without silently replacing the
        // reviewed precedence of surviving rows. New branches append last.
        const result=previous.filter(r=>edges.some(e=>e.rule_id===r.rule_id&&e.target===r.target_activity_id)).map(r=>({...r}));
        const used=new Set(previous.map(r=>r.id));
        for(const edge of edges){
          if(result.some(r=>r.rule_id===edge.rule_id&&r.target_activity_id===edge.target))continue;
          let i=1;while(used.has('row_'+i))i++;used.add('row_'+i);
          result.push({id:'row_'+i,rule_id:edge.rule_id,target_activity_id:edge.target});
        }
        return result;
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
        const policyLabel=el('label','Hit policy '+table.id+' '),policy=el('select');policy.setAttribute('aria-label','Hit policy '+table.id);policy.dataset.tablePolicy=table.id;
        for(const [value,text] of [['UNIQUE','UNIQUE: exactly one established match'],['FIRST','FIRST: explicit reviewed row order']]){const option=el('option',text);option.value=value;policy.appendChild(option);}
        policy.value=table.hit_policy||'UNIQUE';
        policy.onchange=()=>{table.hit_policy=policy.value;invalidate();paint();[...section.querySelectorAll('select[data-table-policy]')].find(n=>n.dataset.tablePolicy===table.id)?.focus();};
        policyLabel.appendChild(policy);box.appendChild(policyLabel);
        const field=(label,value,apply,max=120)=>{
          const wrap=el('label',label+' '),input=el('input');input.value=value||'';input.maxLength=max;input.setAttribute('aria-label',label);
          input.oninput=()=>{apply(input.value);invalidate();};wrap.appendChild(input);box.appendChild(wrap);return input;
        };
        field('Effective start '+table.id,table.effective_from,v=>table.effective_from=v,10).placeholder='YYYY-MM-DD';
        field('Exclusive effective end '+table.id,table.effective_until,v=>table.effective_until=v||null,10).placeholder='YYYY-MM-DD or blank';
        field('Source document label '+table.id,table.source_basis.label,v=>table.source_basis.label=v);
        field('Source document SHA256 '+table.id,table.source_basis.document_sha256,v=>table.source_basis.document_sha256=v,64);
        const list=el('ul');
        table.rows.forEach((row,index)=>{
          const item=el('li',(table.hit_policy==='FIRST'?(index+1)+'. ':'')+row.id+' · '+row.rule_id+' → '+row.target_activity_id);
          if(table.hit_policy==='FIRST'){
            item.tabIndex=-1;item.dataset.orderRow=row.id;item.dataset.orderTable=table.id;
            const move=offset=>{const target=index+offset;if(target<0||target>=table.rows.length)return;[table.rows[index],table.rows[target]]=[table.rows[target],table.rows[index]];invalidate();paint();[...section.querySelectorAll('[data-order-row]')].find(n=>n.dataset.orderRow===row.id&&n.dataset.orderTable===table.id)?.focus();};
            const up=button('Move up',()=>move(-1)),down=button('Move down',()=>move(1));
            up.setAttribute('aria-label','Move row '+row.id+' up in '+table.id);down.setAttribute('aria-label','Move row '+row.id+' down in '+table.id);up.disabled=index===0;down.disabled=index===table.rows.length-1;item.append(up,down);
          }
          list.appendChild(item);
        });box.appendChild(list);
        box.appendChild(button('Synchronize rows '+table.id,()=>{try{table.rows=rowsFor(table.decision_activity_id,table.hit_policy==='FIRST'?table.rows:[]);invalidate();paint();}catch(e){notice.textContent=e.message;}}));
        const label=el('label',' I confirm this exact table, referenced rules, source basis and effective dates'+(table.hit_policy==='FIRST'?', and declared row precedence':'')+' '+table.id+' '),check=el('input');check.type='checkbox';check.dataset.tableConfirmation=table.id;check.checked=table.user_confirmed;
        check.onchange=()=>{table.user_confirmed=check.checked;changed(false);};label.appendChild(check);box.appendChild(label);
        box.appendChild(button('Remove table '+table.id,()=>{model.decision_tables.splice(index,1);if(!model.decision_tables.length)delete model.decision_tables;invalidate();paint();}));
      }
    }
    paint();return section;
  }
  window.owgProcessDecisionEditor=Object.freeze({render,summary});
})();
