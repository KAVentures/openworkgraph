/* Deadline-first review is browser-local over the already reviewed result. */
import '/deadline-schedule.js';
export function mount(host,{report,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const panel=el('details');panel.id='owgDeadlineSchedule';
  panel.append(el('summary','Optimize declared deadlines'),el('p','Keep the same reviewed opportunities, dependencies, durations and capacity designs. Minimize weighted missed deadlines first, then weighted late days, then makespan. Weight 1 treats a deadline equally with other weight-1 deadlines. A missed deadline in a heuristic schedule is not proof of infeasibility.'));
  const engine=globalThis.OWGDeadlineSchedule;
  const titles=new Map(report.selected_ideas.map(x=>[x.idea_id,x.title]));
  const tasks=report.reviewed_inputs.tasks.filter(t=>t.deadline_day!==null);
  if(!tasks.length){panel.append(el('p','Declare at least one deadline in the resource planner before using this review.'));host.replaceChildren(panel);return;}
  const weights=new Map(),fields=el('fieldset');fields.append(el('legend','Review relative deadline weights (1–1000)'));
  const consent=el('input');consent.type='checkbox';consent.id='owgDeadlineConfirm';
  const confirmLabel=el('label');confirmLabel.append(consent,el('span','I reviewed these relative deadline priorities and understand they are planning assumptions, not approved staffing or delivery commitments.'));
  const run=button('Evaluate deadline-first schedules'),save=button('Download deadline review JSON');run.id='owgDeadlineRun';save.id='owgDeadlineDownload';
  const status=el('p');status.id='owgDeadlineStatus';status.setAttribute('role','status');
  const output=el('div');output.id='owgDeadlineResult';
  let serial=0,result=null,busy=false;
  function controls(){run.disabled=busy||!consent.checked;save.disabled=busy||!result;}
  function invalidate(){serial++;busy=false;result=null;consent.checked=false;output.replaceChildren();status.textContent='Review and confirm the current deadline weights.';controls();}
  for(const t of tasks){const input=el('input');input.type='number';input.min='1';input.max='1000';input.step='1';input.value='1';
    const name=titles.get(t.idea_id)||t.idea_id;input.setAttribute('aria-label','Deadline weight for '+name);
    input.oninput=invalidate;weights.set(t.idea_id,input);
    const label=el('label');label.append(el('span',name+' · deadline day '+t.deadline_day+' · weight '),input);fields.append(label);}
  consent.onchange=()=>{if(!consent.checked){invalidate();}else controls();};
  function table(name,head,rows){
    const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
    const t=el('table');t.setAttribute('aria-label',name);const h=el('thead'),hr=el('tr'),b=el('tbody');
    for(const text of head){const th=el('th',text);th.scope='col';hr.append(th);}h.append(hr);
    for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const c=el(i?'td':'th',v??'—');if(!i)c.scope='row';tr.append(c);});b.append(tr);}t.append(h,b);wrap.append(t);return wrap;
  }
  function render(r){
    output.replaceChildren(el('h4','Deadline-first capacity comparison'),el('p','Objective triples are weighted misses / weighted late days / makespan days, in that exact priority order. Compare only under the declared assumptions.'),
      table('Deadline optimization summary',['Design','Original objective','Deadline-first objective','Lower bound (lexicographic)','Proof','All deadlines'],r.scenarios.map(s=>[
        s.label,s.original_objective?.join(' / '),s.objective?.join(' / '),s.lower_bound?.join(' / '),
        s.optimality_proven?'Proven optimum':s.schedule?'Feasible heuristic':'Demand exceeds capacity',
        s.all_declared_deadlines_met?'Met in returned schedule':s.all_deadlines_infeasible_proven?'Infeasible under assumptions':s.schedule?'Not met; feasibility unresolved':'No feasible schedule'
      ])));
    for(const s of r.scenarios){const section=el('details');section.append(el('summary',s.label+' deadline schedule'));
      if(s.schedule){section.append(el('p',s.method.replaceAll('_',' ')+' · '+s.search_nodes+' search nodes · '+s.pruned_prefixes+' pruned prefixes'),
        table(s.label+' deadline witness',['Opportunity','Pool','Units','Start','Finish exclusive','Deadline','Weight','Late days'],s.schedule.map(t=>[titles.get(t.idea_id)||t.idea_id,t.pool,t.units,t.start_day,t.finish_day_exclusive,t.deadline_day,t.deadline_weight,t.late_days])));
      }else section.append(el('p','Oversized tasks: '+s.oversized_task_ids.map(k=>titles.get(k)||k).join(', ')));
      output.append(section);}
    output.append(el('p',r.interpretation),el('p','Keep the original resource schedule report alongside this export to reproduce the input. No current-source refresh or portfolio mutation occurred.'));
  }
  run.onclick=async()=>{
    if(!isCurrent()||!consent.checked||busy)return;
    const n=++serial;busy=true;result=null;output.replaceChildren();controls();status.textContent='Evaluating deadline priorities locally…';
    try{const values=Object.fromEntries([...weights].map(([k,input])=>{if(!input.value||!input.checkValidity()||!Number.isSafeInteger(Number(input.value)))throw Error('Use whole-number weights from 1 to 1000.');return [k,Number(input.value)];}));
      // Yield once so the busy message can paint before bounded local search.
      await new Promise(resolve=>setTimeout(resolve,0));
      if(n!==serial||!isCurrent()||!host.isConnected)return;
      const r=engine.review(report,values);
      if(n!==serial||!isCurrent()||!host.isConnected)return;
      result=r;render(r);status.textContent='Deadline-first schedules reviewed. No saved plan changed.';
    }catch(error){if(n===serial&&host.isConnected&&isCurrent()){result=null;status.textContent=error.message;}}
    finally{if(n===serial&&host.isConnected&&isCurrent()){busy=false;controls();}}
  };
  save.onclick=()=>{if(!result||busy||!isCurrent())return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-deadline-first-schedule.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  panel.append(fields,confirmLabel,run,save,status,output);host.replaceChildren(panel);invalidate();
}
