/* Automation monitoring: import run reports from customer-run automations; show reported rates, drift and step hotspots. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.automation-run-report.v1';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};
  const setOptions=(node,options)=>node.replaceChildren(...options.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));
  const show=(node,visible)=>{node.hidden=!visible;node.style.display=visible?'':'none';};
  function table(headers,rows){
    const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');
    headers.forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
    const body=el('tbody');rows.forEach(r=>{const tr=el('tr');r.forEach(v=>tr.appendChild(el('td',v)));body.appendChild(tr);});
    t.append(head,body);wrap.appendChild(t);return wrap;
  }
  // Pure helpers, exercised by tests.
  const pct=v=>v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%';
  const rate=r=>r?pct(r.rate)+' ['+pct(r.ci95[0])+', '+pct(r.ci95[1])+'] of '+Number(r.of).toLocaleString():'—';
  const change=d=>d?(d.difference>0?'+':'')+(d.difference*100).toFixed(1)+' pts ['+(d.ci95[0]*100).toFixed(1)+', '+(d.ci95[1]*100).toFixed(1)+']':'not enough runs';
  function checkReport(doc){
    if(!doc||doc.format!==FORMAT)throw new Error('Choose a run report in the '+FORMAT+' format.');
    if(!Array.isArray(doc.runs)||!doc.runs.length)throw new Error('The report has no runs.');
    return doc.runs.length;
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Monitoring request failed ('+response.status+').');}
    return response.json();
  }

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgMonitorCard'))return;
    const card=el('div',null,'card');card.id='owgMonitorCard';
    card.appendChild(el('h2','Automation monitoring'));
    card.appendChild(el('div','Import run reports exported by automations you run (n8n, the AI SDK or Agents SDK starters, or any runtime that writes the run report format). Rates, drift and step problems are shown as the automation reported them; OpenWorkGraph does not verify outcomes or run anything.','muted'));
    const file=input('owgMonitorReport','file');file.accept='.json,application/json';
    const target=document.createElement('select');target.id='owgMonitorTarget';setOptions(target,[['','New deployment']]);
    const consent=input('owgMonitorConsent','checkbox');const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    consentLabel.append(consent,document.createTextNode(' Store these runs on this computer until I delete the deployment'));
    const importButton=button('Import run report');importButton.id='owgMonitorImport';importButton.disabled=true;
    const status=el('div',null,'muted');status.id='owgMonitorStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Run report (JSON)',file),field('Add to',target));
    const pick=document.createElement('select');pick.id='owgMonitorPick';
    const recent=input('owgMonitorRecent','number');recent.min='1';recent.max='90';recent.value='7';
    const baseline=input('owgMonitorBaseline','number');baseline.min='1';baseline.max='365';baseline.value='28';
    const view=button('Show monitoring','secondary');view.id='owgMonitorShow';
    const design=input('owgMonitorDesign','file');design.accept='.json,application/json';
    const toLog=button('Analyse as event log','secondary');toLog.id='owgMonitorToLog';
    const remove=button('Delete deployment','secondary');remove.id='owgMonitorDelete';
    const manage=el('div');manage.id='owgMonitorManage';
    const mrow=el('div',null,'row');mrow.style.cssText='align-items:end;margin-top:12px';
    mrow.append(field('Deployment',pick),field('Recent days',recent),field('Baseline days',baseline),view);
    const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Attach reviewed design (workflow.spec.json)',design),toLog,remove);
    manage.append(mrow,arow);show(manage,false);
    const results=el('div');results.id='owgMonitorResults';
    card.append(row,consentLabel,importButton,status,manage,results);
    const anchor=document.getElementById('owgPddCard')||document.getElementById('owgCommsCard')||document.getElementById('owgEventLogCard');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let deployments=[];
    const refresh=()=>{importButton.disabled=!(file.files&&file.files[0]&&consent.checked);};
    file.onchange=refresh;consent.onchange=refresh;
    async function load(select){
      deployments=(await api('/v1/automation-monitoring/deployments')).deployments||[];
      const options=deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime+' · '+Number(d.runs).toLocaleString()+' runs']);
      setOptions(pick,options);setOptions(target,[['','New deployment'],...options]);
      show(manage,deployments.length>0);
      if(select&&deployments.some(d=>d.deployment_id===select))pick.value=select;
      if(!deployments.length)results.replaceChildren();
    }
    importButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      importButton.disabled=true;status.textContent='Importing '+chosen.name+'…';
      try{
        const text=await chosen.text();let doc;
        try{doc=JSON.parse(text);}catch(_){throw new Error('That file is not valid JSON.');}
        checkReport(doc);
        const q=target.value?'?deployment_id='+encodeURIComponent(target.value):'';
        const r=await api('/v1/automation-monitoring/reports'+q,{method:'POST',body:text,headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'}});
        status.textContent='Imported '+Number(r.runs_imported).toLocaleString()+' runs ('+Number(r.steps_imported).toLocaleString()+' steps) into '+r.deployment.name+'.';
        file.value='';consent.checked=false;await load(r.deployment.deployment_id);await render();
      }catch(error){status.textContent=error.message;}finally{refresh();}
    };
    design.onchange=async()=>{
      const chosen=design.files&&design.files[0];if(!chosen||!pick.value)return;design.value='';
      try{await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(pick.value)+'/design',{method:'PUT',headers:{'Content-Type':'application/json'},body:await chosen.text()});
        status.textContent='Reviewed design attached.';await load(pick.value);await render();}
      catch(error){status.textContent=error.message;}
    };
    view.onclick=()=>render().catch(error=>{status.textContent=error.message;});
    pick.onchange=()=>results.replaceChildren();
    toLog.onclick=async()=>{
      if(!window.confirm('Store this deployment\'s reported steps as an event log on this computer?'))return;
      try{const log=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(pick.value)+'/event-log',{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}});
        status.textContent='Saved the event log "'+log.name+'" with '+Number(log.events).toLocaleString()+' steps. Open it under Large event logs.';}
      catch(error){status.textContent=error.message;}
    };
    remove.onclick=async()=>{
      const chosen=deployments.find(d=>d.deployment_id===pick.value);if(!chosen)return;
      if(!window.confirm('Delete the deployment "'+chosen.name+'" and all its runs from this computer?'))return;
      try{await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(pick.value)+'/delete',{method:'POST'});await load();status.textContent='Deployment deleted.';}
      catch(error){status.textContent=error.message;}
    };
    async function render(){
      if(!pick.value)return;
      const m=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(pick.value)+'/monitor?recent_days='+encodeURIComponent(recent.value||7)+'&baseline_days='+encodeURIComponent(baseline.value||28));
      const parts=[];
      if(m.alerts.length){const box=el('div',null,'note');box.setAttribute('role','alert');box.appendChild(el('strong','Changes in the recent window'));
        const list=el('ul');m.alerts.forEach(a=>list.appendChild(el('li',a.message+(a.code==='duration_up'?'':' ('+change(a)+')'))));box.appendChild(list);parts.push(box);}
      else parts.push(el('div','No change in success, handoff, failure or duration rates that the intervals can distinguish.','muted'));
      const o=m.overall;
      parts.push(el('h3','Reported results'),table(['Window','Runs','Succeeded','Handed to a person','Failed'],[
        ['All',Number(o.runs).toLocaleString(),rate(o.success),rate(o.handed_to_human),rate(o.failure)],
        ['Recent',Number(m.recent.runs).toLocaleString(),rate(m.recent.success),rate(m.recent.handed_to_human),rate(m.recent.failure)],
        ['Baseline',Number(m.baseline.runs).toLocaleString(),rate(m.baseline.success),rate(m.baseline.handed_to_human),rate(m.baseline.failure)]]),
        el('div','Rates are among finished runs with 95% Wilson intervals. Outcome verified as reported by the automation: '+rate(o.outcome_reported_verified)+' of succeeded runs that reported it.','muted'),
        table(['Recent minus baseline','Change (95% CI)'],[['Success rate',change(m.drift.success_rate)],['Handoff rate',change(m.drift.handoff_rate)],['Failure rate',change(m.drift.failure_rate)]]));
      if(m.steps.length)parts.push(el('h3','Steps'),table(['Step','Attempts','Failed','Handed to a person','Median time since previous step'],m.steps.slice(0,20).map(s=>[s.step_id+' '+s.activity,Number(s.attempts).toLocaleString(),pct(s.failure_rate),pct(s.handoff_rate),s.median_seconds_since_previous_step===null?'—':Math.round(s.median_seconds_since_previous_step)+' s'])));
      const c=m.design_conformance;
      if(c&&c.kind==='runtime_graph')parts.push(el('h3','Reported activity structure against the saved graph'),
        table(['Runs compared / total','Exact structural matches','Mean minimum activity edits among compared runs','Activities never reported completed','Unexpected step IDs'],[[
          Number(c.runs_compared).toLocaleString()+' / '+Number(c.total_runs).toLocaleString(),Number(c.runs_exact_structural_match).toLocaleString(),
          c.mean_minimum_activity_edits_among_compared_runs===null?'—':Number(c.mean_minimum_activity_edits_among_compared_runs).toFixed(3),
          c.activity_ids_never_reported_completed.join(', ')||'none',c.unexpected_step_ids.map(s=>s.step_id+' ('+s.runs+')').join(', ')||'none']]),
        el('div','Comparison coverage: '+pct(c.covered_run_share)+'. Not compared: '+Number(c.runs_not_compared).toLocaleString()+
          ' (no completed steps: '+c.not_compared_by_reason.no_completed_steps+', distinct-path limit: '+c.not_compared_by_reason.distinct_path_limit+
          ', alignment-state limit: '+c.not_compared_by_reason.alignment_state_limit+'). Missing activities mean missing reported evidence, not proven process deviations. '+
          'Rules, human work, current catalog approval and business outcomes are not verified.','muted'));
      else if(c)parts.push(el('h3','Compared with the reviewed design'),table(['Runs with steps','Completed every step in order','Mean fitness','Design steps never completed','Unexpected steps'],[[Number(c.runs_with_steps).toLocaleString(),Number(c.runs_completing_every_design_step_in_order).toLocaleString(),c.mean_fitness===null?'—':Number(c.mean_fitness).toFixed(3),c.design_steps_never_completed.map(s=>s.id).join(', ')||'none',c.unexpected_step_ids.map(s=>s.step_id+' ('+s.runs+')').join(', ')||'none']]));
      else parts.push(el('div','Attach the starter\'s or SDK project\'s workflow.spec.json to compare reported steps with its design.','muted'));
      if(m.weekly.length)parts.push(el('h3','Runs per week'),table(['Week','Succeeded','Handed to a person','Failed','Cancelled','Running'],m.weekly.slice(-12).map(w=>[w.week,w.succeeded,w.handed_to_human,w.failed,w.cancelled,w.running])));
      results.replaceChildren(...parts);
    }
    refresh();load().catch(()=>{});
  }

  window.__owgMonitorModel={rate,change,checkReport};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
