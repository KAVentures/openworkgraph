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
    const channelButton=button('Signed report channel','secondary');channelButton.id='owgMonitorChannel';
    const incidentButton=button('Show incidents','secondary');incidentButton.id='owgMonitorIncidents';
    const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Attach reviewed design (workflow.spec.json)',design),toLog,channelButton,incidentButton,remove);
    const channelHost=el('div');channelHost.id='owgMonitorChannelPanel';const incidentHost=el('div');incidentHost.id='owgMonitorIncidentPanel';
    manage.append(mrow,arow,channelHost,incidentHost);show(manage,false);
    const results=el('div');results.id='owgMonitorResults';
    card.append(row,consentLabel,importButton,status,manage,results);
    const anchor=document.getElementById('owgPddCard')||document.getElementById('owgCommsCard')||document.getElementById('owgEventLogCard');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let deployments=[],renderVersion=0,loadVersion=0,incidentVersion=0;
    let incidentScope='selected',incidentStatus='',incidentSearch='';
    const clearResults=()=>{renderVersion++;incidentVersion++;incidentScope='selected';incidentStatus='';incidentSearch='';results.replaceChildren();channelHost.replaceChildren();incidentHost.replaceChildren();};
    const refresh=()=>{importButton.disabled=!(file.files&&file.files[0]&&consent.checked);};
    file.onchange=refresh;consent.onchange=refresh;
    async function load(select){
      const version=++loadVersion;
      const loaded=(await api('/v1/automation-monitoring/deployments')).deployments||[];
      if(version!==loadVersion)return;
      const previous=pick.value;
      clearResults();deployments=loaded;
      const options=deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime+' · '+Number(d.runs).toLocaleString()+' runs']);
      setOptions(pick,options);setOptions(target,[['','New deployment'],...options]);
      show(manage,deployments.length>0);
      if(select&&deployments.some(d=>d.deployment_id===select))pick.value=select;
      else if(deployments.some(d=>d.deployment_id===previous))pick.value=previous;
      if(!deployments.length)results.replaceChildren();
    }
    importButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      const selectedTarget=target.value;
      importButton.disabled=true;status.textContent='Importing '+chosen.name+'…';
      try{
        const text=await chosen.text();let doc;
        try{doc=JSON.parse(text);}catch(_){throw new Error('That file is not valid JSON.');}
        checkReport(doc);
        const q=selectedTarget?'?deployment_id='+encodeURIComponent(selectedTarget):'';
        const r=await api('/v1/automation-monitoring/reports'+q,{method:'POST',body:text,headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'}});
        status.textContent='Imported '+Number(r.runs_imported).toLocaleString()+' runs ('+Number(r.steps_imported).toLocaleString()+' steps) into '+r.deployment.name+'.';
        file.value='';consent.checked=false;await load(r.deployment.deployment_id);await render();
      }catch(error){status.textContent=error.message;}finally{refresh();}
    };
    design.onchange=async()=>{
      const chosen=design.files&&design.files[0];if(!chosen||!pick.value)return;design.value='';
      const selected=pick.value;
      try{await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/design',{method:'PUT',headers:{'Content-Type':'application/json'},body:await chosen.text()});
        if(pick.value!==selected)return;
        status.textContent='Reviewed design attached.';await load(selected);await render();}
      catch(error){if(pick.value===selected)status.textContent=error.message;}
    };
    view.onclick=()=>render().catch(error=>{status.textContent=error.message;});
    const secretBox=(secret)=>{const box=input('owgMonitorChannelSecret','text');box.readOnly=true;box.value=secret;box.style.width='100%';
      return [field('Channel secret (shown only now; store it in your automation\'s secret store)',box)];};
    async function renderChannel(shown){
      const selected=pick.value;if(!selected)return;
      const data=await api('/v1/automation-monitoring/channels?deployment_id='+encodeURIComponent(selected));
      if(pick.value!==selected)return;
      const live=data.channels.find(c=>!c.revoked_at);
      const parts=[el('h3','Signed report channel'),el('div','Your automation can POST run reports here itself, with the local API token and an HMAC-SHA256 signature over the timestamp, a sequence number and the body (see shared/automation_report_signing.py). OWG refuses bad signatures and stale timestamps, ignores exact resends, refuses reused sequence numbers and reports skipped ones as lost. A report for a different workflow spec is refused as version drift. Reports are still what the automation says happened.','muted')];
      if(!live){
        const create=button('Create report channel','secondary');create.id='owgMonitorChannelCreate';
        create.onclick=async()=>{try{const made=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/channel',{method:'POST'});await renderChannel(made.secret);}catch(error){status.textContent=error.message;}};
        parts.push(create);
      }else{
        parts.push(el('div','Endpoint: POST /v1/automation-monitoring/channels/'+live.channel_id+'/reports · body format openworkgraph.signed-run-report.v1 · clock skew up to '+data.max_clock_skew_seconds+' s.','muted'));
        if(shown)parts.push(...secretBox(shown));
        parts.push(table(['Accepted','Refused','Highest sequence','Missing (retained)'],[[String(live.accepted_reports),String(live.refused_reports),String(live.highest_sequence),live.missing_count?live.missing_sequences.join(', ')+(live.missing_count>live.missing_sequences.length?' …':''):'none']]));
        if(live.missing_truncated)parts.push(el('p','Earlier missing sequence numbers were truncated from the 1,000-ID recovery window. The retained list cannot prove that all reports arrived, even when empty.','note'));
        if(live.versions.length)parts.push(el('h4','Versions seen'),table(['Version','Workflow spec','First seen','Last seen','Reports','Runs'],live.versions.map(v=>[v.version_label||'(none)',v.workflow_spec_sha256?v.workflow_spec_sha256.slice(0,12)+'…':'(none)',v.first_seen,v.last_seen,String(v.reports),String(v.runs)])));
        const rotate=button('Rotate secret','secondary');rotate.id='owgMonitorChannelRotate';
        rotate.onclick=async()=>{if(!window.confirm('Replace the channel secret? The old one stops working immediately.'))return;try{const r=await api('/v1/automation-monitoring/channels/'+encodeURIComponent(live.channel_id)+'/rotate',{method:'POST'});await renderChannel(r.secret);}catch(error){status.textContent=error.message;}};
        const revoke=button('Revoke channel','secondary');revoke.id='owgMonitorChannelRevoke';
        revoke.onclick=async()=>{if(!window.confirm('Revoke this channel? The automation can no longer send reports with it.'))return;try{await api('/v1/automation-monitoring/channels/'+encodeURIComponent(live.channel_id)+'/revoke',{method:'POST'});await renderChannel();}catch(error){status.textContent=error.message;}};
        parts.push(el('div',null,'row'),rotate,revoke);
      }
      channelHost.replaceChildren(...parts);
    }
    const KIND={run_failures:'Failed runs',run_handoffs:'Runs handed to a person',signature_rejected:'Rejected signatures',replay_conflict:'Reused sequence numbers',lost_reports:'Lost reports',version_drift:'Version drift'};
    async function renderIncidents(){
      const selected=pick.value;if(!selected)return;
      const version=++incidentVersion,requestedScope=incidentScope;
      const suffix=requestedScope==='all'?'':'?deployment_id='+encodeURIComponent(selected);
      let data;
      try{data=await api('/v1/automation-monitoring/incidents'+suffix);}
      catch(error){if(version===incidentVersion&&pick.value===selected)status.textContent=error.message;return;}
      if(version!==incidentVersion||pick.value!==selected||incidentScope!==requestedScope)return;
      const scope=document.createElement('select');scope.id='owgMonitorIncidentScope';
      setOptions(scope,[['selected','Selected deployment'],['all','All local deployments']]);scope.value=incidentScope;
      const filter=document.createElement('select');filter.id='owgMonitorIncidentStatus';
      setOptions(filter,[['','All statuses'],['open','Open'],['acknowledged','Acknowledged'],['resolved','Resolved']]);filter.value=incidentStatus;
      const search=input('owgMonitorIncidentSearch','search','Deployment, kind or step');search.value=incidentSearch;
      const controls=el('div',null,'row');controls.style.alignItems='end';
      controls.append(field('Incident scope',scope),field('Incident status',filter),field('Search incidents',search));
      const refreshList=()=>renderIncidents();
      scope.onchange=()=>{incidentScope=scope.value;draw();refreshList();};
      filter.onchange=()=>{incidentStatus=filter.value;draw();};
      search.oninput=()=>{incidentSearch=search.value;draw();};
      const body=el('div');
      function draw(){
      if(incidentScope!==requestedScope){body.replaceChildren(el('div','Loading incidents for the selected scope…','muted'));return;}
      const query=search.value.trim().toLocaleLowerCase();
      const visible=data.incidents.filter(i=>{
        const deployment=deployments.find(d=>d.deployment_id===i.deployment_id);
        const text=[deployment?.name||'',KIND[i.kind]||i.kind,i.fingerprint].join(' ').toLocaleLowerCase();
        return (!filter.value||i.status===filter.value)&&(!query||text.includes(query));
      });
      const list=el('div');
      visible.forEach(i=>{
        const box=el('fieldset');box.appendChild(el('legend',(KIND[i.kind]||i.kind)+' · '+i.status));
        if(scope.value==='all'){const deployment=deployments.find(d=>d.deployment_id===i.deployment_id);box.appendChild(el('div','Deployment: '+(deployment?deployment.name:'Unavailable deployment'),'muted'));}
        box.appendChild(el('div',i.fingerprint.replace(/^step:/,'Step: ')+' · '+i.occurrences+(i.count_basis==='distinct_retained_run_ids'?' distinct retained runs':' occurrences')+' · first '+i.first_seen+' · last '+i.last_seen+(i.reopened_count?' · reopened '+i.reopened_count+'×':'')+(i.sample_refs.length?' · runs '+i.sample_refs.join(', '):''),'muted'));
        i.notes.forEach(n=>box.appendChild(el('div',n.at+' · '+n.status+' · '+n.note)));
        const note=input('owgMonitorIncidentNote_'+i.incident_id,'text','Note (optional)');note.maxLength=500;note.setAttribute('aria-label','Note for incident '+i.incident_id);box.appendChild(note);
        for(const [to,label] of [['acknowledged','Acknowledge'],['resolved','Resolve'],['open','Reopen']]){
          if(to===i.status)continue;
          const b=button(label,'secondary');b.setAttribute('aria-label',label+' incident '+i.incident_id);
          b.onclick=async()=>{b.disabled=true;try{await api('/v1/automation-monitoring/incidents/'+encodeURIComponent(i.incident_id)+'/triage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:to,note:note.value,expected_last_seen:i.last_seen,expected_review_revision:i.review_revision})});if(version===incidentVersion&&pick.value===selected)await renderIncidents();}catch(error){if(version===incidentVersion&&pick.value===selected)status.textContent=error.message;b.disabled=false;}};
          box.appendChild(b);
        }
        list.appendChild(box);
      });
      body.replaceChildren(el('div','Showing '+visible.length+' of '+data.incidents.length+' incidents in scope · '+data.counts.open+' open · '+data.counts.acknowledged+' acknowledged · '+data.counts.resolved+' resolved. Counts reflect the selected scope before filters. Triage is version-checked; nothing is re-run or replayed.','muted'),visible.length?list:el('div','No incidents match these filters.','muted'));
      }
      const reload=button('Refresh incidents','secondary');reload.id='owgMonitorIncidentRefresh';reload.onclick=refreshList;
      incidentHost.replaceChildren(el('h3','Incidents'),controls,reload,body);draw();
    }
    channelButton.onclick=()=>renderChannel().catch(error=>{status.textContent=error.message;});
    incidentButton.onclick=()=>{incidentScope='selected';incidentStatus='';incidentSearch='';renderIncidents();};
    pick.onchange=clearResults;recent.oninput=clearResults;baseline.oninput=clearResults;
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
      const selected=pick.value,version=++renderVersion;
      let m;
      try{m=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/monitor?recent_days='+encodeURIComponent(recent.value||7)+'&baseline_days='+encodeURIComponent(baseline.value||28));}
      catch(error){if(version!==renderVersion||pick.value!==selected)return;throw error;}
      if(version!==renderVersion||pick.value!==selected)return;
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
