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
    // A scrollable table must be reachable by keyboard (WCAG 2.1.1).
    wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
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
    const objectiveButton=button('Service objectives','secondary');objectiveButton.id='owgMonitorObjectives';
    const releaseButton=button('Release inventory','secondary');releaseButton.id='owgMonitorRelease';
    const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Attach reviewed design (workflow.spec.json)',design),toLog,channelButton,incidentButton,releaseButton,objectiveButton,remove);
    const channelHost=el('div');channelHost.id='owgMonitorChannelPanel';const incidentHost=el('div');incidentHost.id='owgMonitorIncidentPanel';
    const releaseHost=el('div');releaseHost.id='owgMonitorReleasePanel';
    const objectiveHost=el('div');objectiveHost.id='owgMonitorObjectivePanel';
    manage.append(mrow,arow,channelHost,incidentHost,releaseHost,objectiveHost);show(manage,false);
    const portfolio=el('div');portfolio.id='owgMonitorPortfolio';
    const portfolioDays=input('owgMonitorPortfolioDays','number');portfolioDays.min='1';portfolioDays.max='365';portfolioDays.value='30';
    const portfolioButton=button('Review all deployment evidence','secondary');portfolioButton.id='owgMonitorPortfolioShow';
    const portfolioControls=el('div',null,'row');portfolioControls.style.alignItems='end';
    portfolioControls.append(field('Portfolio recent days',portfolioDays),portfolioButton);
    const results=el('div');results.id='owgMonitorResults';
    card.append(row,consentLabel,importButton,status,portfolioControls,portfolio,manage,results);
    const anchor=document.getElementById('owgPddCard')||document.getElementById('owgCommsCard')||document.getElementById('owgEventLogCard');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let deployments=[],renderVersion=0,loadVersion=0,incidentVersion=0,releaseVersion=0,portfolioVersion=0;
    let incidentScope='selected',incidentStatus='',incidentSearch='';
    const clearResults=()=>{portfolioVersion++;portfolio.replaceChildren();renderVersion++;incidentVersion++;incidentScope='selected';incidentStatus='';incidentSearch='';results.replaceChildren();channelHost.replaceChildren();incidentHost.replaceChildren();releaseVersion++;releaseHost.replaceChildren();objectiveHost.replaceChildren();document.getElementById('owgMonitorRunEvidencePanel')?.replaceChildren();};
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
        const receipts=live.recent_sequence_receipts||[];
        if(receipts.length){
          const disposition={imported:'Accepted',refused_invalid:'Invalid report',refused_version_drift:'Specification mismatch',
            refused_run_revision_conflict:'Conflicting run revision'};
          parts.push(el('h4','Recent signed sequence dispositions'),
            el('p','Highest '+receipts.length+' retained sequences, not execution or arrival order. Original refusal status is preserved even on an exact resend.','muted'),
            table(['Sequence','Received','Disposition','Original HTTP status'],receipts.map(r=>[
              r.sequence,r.received_at,disposition[r.outcome]||r.outcome,String(r.original_status)])));
          if(live.receipt_list_truncated)parts.push(el('p',live.retained_receipt_count+' receipts retained; only the highest 50 are displayed.','muted'));
        }
        const rotate=button('Rotate secret','secondary');rotate.id='owgMonitorChannelRotate';
        rotate.onclick=async()=>{if(!window.confirm('Replace the channel secret? The old one stops working immediately.'))return;try{const r=await api('/v1/automation-monitoring/channels/'+encodeURIComponent(live.channel_id)+'/rotate',{method:'POST'});await renderChannel(r.secret);}catch(error){status.textContent=error.message;}};
        const revoke=button('Revoke channel','secondary');revoke.id='owgMonitorChannelRevoke';
        revoke.onclick=async()=>{if(!window.confirm('Revoke this channel? The automation can no longer send reports with it.'))return;try{await api('/v1/automation-monitoring/channels/'+encodeURIComponent(live.channel_id)+'/revoke',{method:'POST'});await renderChannel();}catch(error){status.textContent=error.message;}};
        parts.push(el('div',null,'row'),rotate,revoke);
      }
      channelHost.replaceChildren(...parts);
    }
    const KIND={run_failures:'Failed runs',run_handoffs:'Runs handed to a person',signature_rejected:'Rejected signatures',replay_conflict:'Reused sequence numbers',lost_reports:'Lost reports',version_drift:'Version drift',run_revision_conflict:'Conflicting run revisions'};
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
        let inspectedPopulation=null,evidenceBusy=false;
        if(i.count_basis==='distinct_retained_run_ids'&&i.run_membership_sha256){
          const evidenceHost=el('div');
          evidenceHost.setAttribute('aria-live','polite');
          const inspect=button('Inspect retained run evidence','secondary');
          inspect.setAttribute('aria-label','Inspect retained run evidence for incident '+i.incident_id);
          let evidenceVersion=0;
          const current=v=>v===evidenceVersion&&version===incidentVersion&&pick.value===selected&&box.isConnected;
          async function evidencePage(offset,population){
            const v=++evidenceVersion;inspect.disabled=true;evidenceBusy=true;
            evidenceHost.replaceChildren(el('div','Loading retained run evidence…','muted'));
            try{
              const result=await api('/v1/automation-monitoring/incidents/'+encodeURIComponent(i.incident_id)+'/evidence',{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
                  expected_last_seen:i.last_seen,expected_review_revision:i.review_revision,
                  expected_membership_sha256:i.run_membership_sha256,expected_population_sha256:population,
                  offset,limit:20})});
              if(!current(v))return;
              inspectedPopulation=result.population_sha256;
              const parts=[el('p',result.matching_runs+' matching retained runs · '+result.matching_steps+' reported steps. Current reports, not verified outcomes. A triage decision from this view will require this exact population to remain current.','muted')];
              const runList=el('div');
              for(const r of result.rows){
                const details=el('details');
                details.appendChild(el('summary',r.run_id+' · '+r.status+' · '+r.started_at));
                const issue=r.first_reported_issue;
                details.appendChild(el('div',issue?'First reported issue: '+issue.activity+' · '+issue.status+' · step '+issue.step_id:'No matching problem step reported','muted'));
                const load=button('Read exact retained report','secondary');
                const exact=el('div');exact.setAttribute('aria-live','polite');
                load.onclick=async()=>{
                  load.disabled=true;
                  try{
                    const d=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(r.reference.deployment_id)+'/runs/detail?run_id='+encodeURIComponent(r.run_id)+'&expected_sha256='+encodeURIComponent(r.reference.run_sha256));
                    if(!current(v)||!details.isConnected)return;
                    exact.replaceChildren(el('p','Reported spec: '+(d.run.reported_spec_sha256||'Unknown')+' · '+d.attribution,'muted'),
                      d.run.steps.length?table(['Position','Activity','Status','Reported time'],d.run.steps.map(s=>[s.seq+1,s.activity,s.status,s.at])):el('p','No steps reported.'));
                  }catch(error){if(current(v)&&details.isConnected)exact.replaceChildren(el('p',error.message,'note'));}
                  finally{if(current(v)&&details.isConnected)load.disabled=false;}
                };
                details.append(load,exact);runList.appendChild(details);
              }
              parts.push(runList);
              const nav=el('div',null,'row');
              if(offset>0){const previous=button('Previous evidence page','secondary');previous.onclick=()=>evidencePage(Math.max(0,offset-20),result.population_sha256);nav.appendChild(previous);}
              if(result.next_offset!==null){const next=button('Next evidence page','secondary');next.onclick=()=>evidencePage(result.next_offset,result.population_sha256);nav.appendChild(next);}
              parts.push(el('div',result.rows.length?'Showing '+(offset+1)+'–'+(offset+result.rows.length)+' of '+result.matching_runs:'No currently retained matching runs. Historical reviews are not erased.','muted'),nav);
              evidenceHost.replaceChildren(...parts);
            }catch(error){if(current(v))evidenceHost.replaceChildren(el('p',error.message,'note'));}
            finally{if(current(v)){inspect.disabled=false;evidenceBusy=false;}}
          }
          inspect.onclick=()=>evidencePage(0,null);
          box.append(inspect,evidenceHost);
        }

        const note=input('owgMonitorIncidentNote_'+i.incident_id,'text','Note (optional)');note.maxLength=500;note.setAttribute('aria-label','Note for incident '+i.incident_id);box.appendChild(note);
        for(const [to,label] of [['acknowledged','Acknowledge'],['resolved','Resolve'],['open','Reopen']]){
          if(to===i.status)continue;
          const b=button(label,'secondary');b.setAttribute('aria-label',label+' incident '+i.incident_id);
          b.onclick=async()=>{
            if(evidenceBusy){status.textContent='Wait for the incident evidence to finish loading before triage.';return;}
            b.disabled=true;
            const payload={status:to,note:note.value,expected_last_seen:i.last_seen,expected_review_revision:i.review_revision};
            const pinned=inspectedPopulation!==null;
            if(pinned){payload.expected_membership_sha256=i.run_membership_sha256;payload.expected_population_sha256=inspectedPopulation;}
            try{
              await api('/v1/automation-monitoring/incidents/'+encodeURIComponent(i.incident_id)+(pinned?'/evidence/triage':'/triage'),{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
              if(version===incidentVersion&&pick.value===selected&&box.isConnected)await renderIncidents();
            }catch(error){if(version===incidentVersion&&pick.value===selected&&box.isConnected)status.textContent=error.message;b.disabled=false;}
          };
          box.appendChild(b);
        }
        list.appendChild(box);
      });
      body.replaceChildren(el('div','Showing '+visible.length+' of '+data.incidents.length+' incidents in scope · '+data.counts.open+' open · '+data.counts.acknowledged+' acknowledged · '+data.counts.resolved+' resolved. Counts reflect the selected scope before filters. Triage is version-checked; nothing is re-run or replayed.','muted'),visible.length?list:el('div','No incidents match these filters.','muted'));
      }
      const reload=button('Refresh incidents','secondary');reload.id='owgMonitorIncidentRefresh';reload.onclick=refreshList;
      incidentHost.replaceChildren(el('h3','Incidents'),controls,reload,body);draw();
    }
    async function renderRelease(){
      const selected=pick.value;if(!selected)return;
      const version=++releaseVersion;
      const data=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/release');
      if(version!==releaseVersion||pick.value!==selected)return;
      const parts=[el('h3','Deployment release inventory'),
        el('div','Human-declared stages only: changes here do not activate, pause, retire or roll back any remote automation.','muted')];
      parts.push(el('div',data.current?
        'Latest declaration: '+data.current.stage+' · '+data.current.release_label+' · revision '+data.revision:
        'No release review has been recorded.','muted'));
      parts.push(el('div','Retained runs: '+data.runs.retained+'; runs claiming this pinned spec: '+
        data.runs.declaring_pinned_spec+'. Neither a reported spec nor a declared stage verifies the executable artifact.','muted'));
      const stage=document.createElement('select');stage.id='owgMonitorReleaseStage';
      setOptions(stage,[['planned','Planned'],['shadow_reported','Shadow (declared)'],
        ['pilot_reported','Pilot (declared)'],['production_reported','Production (declared)'],
        ['paused_reported','Paused (declared only)'],['retired_reported','Retired (declared only)']]);
      stage.value=data.current?.stage||'planned';
      const label=input('owgMonitorReleaseLabel','text','v1.0.0');label.maxLength=64;label.value=data.current?.release_label||'v1';
      const digest=input('owgMonitorReleaseArtifact','text','Artifact SHA-256');digest.maxLength=64;digest.value=data.current?.artifact_sha256||'';
      const note=input('owgMonitorReleaseNote','text','No secrets');note.maxLength=500;
      const confirmation=input('owgMonitorReleaseConfirm','checkbox');
      const confirmLabel=el('label');confirmLabel.style.cssText='display:flex;gap:8px;align-items:center';
      confirmLabel.append(confirmation,document.createTextNode(' I confirm this is an unverified operator declaration, not a remote deployment command'));
      const save=button('Record release review','secondary');save.id='owgMonitorReleaseSave';save.disabled=true;
      confirmation.onchange=()=>{save.disabled=!confirmation.checked;};
      const feedback=el('p',null,'muted');feedback.id='owgMonitorReleaseFeedback';feedback.setAttribute('role','status');
      const line=el('div',null,'row');line.style.alignItems='end';
      line.append(field('Declared stage',stage),field('Release label',label),
        field('Artifact SHA-256 (required for pilot/production)',digest),field('Reviewer note',note),save);
      save.onclick=async()=>{
        if(version!==releaseVersion||pick.value!==selected||!confirmation.checked)return;
        save.disabled=true;
        try{
          await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/release',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
              expected_revision:data.revision,
              expected_reported_spec_sha256:data.expected_reported_spec_sha256,
              expected_design_sha256:data.expected_design_sha256,
              stage:stage.value,release_label:label.value,artifact_sha256:digest.value||null,
              reviewer_note:note.value,user_confirmed:true
            })
          });
          if(version===releaseVersion&&pick.value===selected){
            status.textContent='Release review saved locally.';portfolioVersion++;portfolio.replaceChildren();await renderRelease();
          }
        }catch(error){
          if(version===releaseVersion&&pick.value===selected){feedback.textContent=error.message;save.disabled=!confirmation.checked;}
        }
      };
      parts.push(line,confirmLabel,feedback);
      if(data.history.length)parts.push(el('h4','Immutable local history'),table(
        ['Revision','Declared at','Stage','Label','Artifact SHA-256','Reported spec SHA-256','Note'],
        data.history.map(row=>[String(row.revision),row.declared_at,row.stage,row.release_label,
          row.artifact_sha256||'unknown',row.reported_spec_sha256||'unknown',row.reviewer_note||'—'])));
      parts.push(el('p','An attached design or matching hash does not establish what executed or what a provider returned. Deleting the deployment also removes this inventory.','muted'));
      releaseHost.replaceChildren(...parts);
    }
    const flagText={
      no_release_declaration:'No release declaration',release_pins_missing_or_changed:'Release pins missing or changed',
      reported_spec_missing:'Reported specification missing',reviewed_design_missing:'Reviewed design missing',
      reported_spec_design_mismatch:'Reported specification and design differ',artifact_digest_missing:'Artifact digest missing',
      no_runs_claiming_current_spec:'No retained runs claim current specification',runs_claiming_other_spec:'Retained runs claim another specification',
      runs_without_comparable_spec:'Retained runs lack comparable specification',no_recent_retained_runs:'No recent retained runs',
      no_recent_runs_claiming_current_spec:'No recent runs claim current specification',recent_runs_claiming_other_spec:'Recent runs claim another specification',
      recent_runs_without_comparable_spec:'Recent runs lack comparable specification'
    };
    portfolioDays.oninput=()=>{portfolioVersion++;portfolio.replaceChildren();};
    async function renderPortfolio(){
      const version=++portfolioVersion;
      const days=Number(portfolioDays.value);
      if(!Number.isInteger(days)||days<1||days>365){portfolio.replaceChildren(el('p','Choose 1–365 recent days.','note'));return;}
      portfolio.replaceChildren(el('p','Loading retained deployment evidence…','muted'));
      let data;
      try{data=await api('/v1/automation-monitoring/portfolio?recent_days='+encodeURIComponent(days));}
      catch(error){if(version===portfolioVersion)portfolio.replaceChildren(el('p',error.message,'note'));return;}
      if(version!==portfolioVersion)return;
      const parts=[el('h3','Deployment portfolio evidence'),
        el('p',data.deployment_count+' retained deployments; '+data.deployments_with_review_flags+
          ' with listed evidence gaps. Snapshot '+data.as_of+'. Recent window: '+data.recent_window.days+' days.','muted'),
        el('p',data.interpretation,'note')];
      if(data.deployments.length){
        parts.push(table(['Deployment','Runtime','Declared stage','Current spec claims / all','Other / unknown','Recent claims / all','Most recent retained run start','Review flags'],
          data.deployments.map(d=>[d.name,d.runtime,d.current_declaration?d.current_declaration.stage:'Undeclared',
            d.runs.claiming_current_spec+' / '+d.runs.retained,d.runs.claiming_other_spec+' / '+d.runs.without_comparable_spec,
            d.recent_runs.claiming_current_spec+' / '+d.recent_runs.retained,d.latest_retained_run_start||'None',
            d.review_flags.map(f=>flagText[f]||f).join('; ')||'No listed evidence gaps'])));
        const selected=document.createElement('select');selected.id='owgMonitorPortfolioPick';
        setOptions(selected,data.deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime]));
        const open=button('Open selected release inventory','secondary');
        open.onclick=async()=>{
          const ident=selected.value;
          try{
            await load(ident);
            if(pick.value!==ident)throw new Error('Deployment no longer retained; refresh portfolio.');
            await renderRelease();
          }catch(error){status.textContent=error.message;}
        };
        const controls=el('div',null,'row');controls.append(field('Inspect deployment',selected),open);
        parts.push(controls);
      }else parts.push(el('p','No deployments retained.','muted'));
      portfolio.replaceChildren(...parts);
    }
    // Review exact report evidence without invoking the customer runtime.
    const evidenceButton=button('Explore retained runs','secondary');evidenceButton.id='owgMonitorRunEvidence';
    const evidenceHost=el('div');evidenceHost.id='owgMonitorRunEvidencePanel';arow.appendChild(evidenceButton);manage.appendChild(evidenceHost);
    let evidenceGeneration=0;
    function openRunEvidence(){
      const dep=pick.value;if(!dep)return;
      const generation=++evidenceGeneration;
      const root=el('div');evidenceHost.replaceChildren(root);
      const active=()=>root.isConnected&&pick.value===dep&&generation===evidenceGeneration;
      const path='/v1/automation-monitoring/deployments/'+encodeURIComponent(dep)+'/runs';
      const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const selectField=(id,values)=>{const s=document.createElement('select');s.id=id;setOptions(s,values);return s;};
      const runStatus=selectField('owgRunEvidenceStatus',[['','Any status'],...['succeeded','failed','handed_to_human','cancelled','running'].map(v=>[v,v])]);
      const stepStatus=selectField('owgRunEvidenceStepStatus',[['','Any step status'],...['completed','failed','skipped','handed_to_human'].map(v=>[v,v])]);
      const attribution=selectField('owgRunEvidenceAttribution',[['any','Any attribution'],['attached_design','Matches attached design digest'],['declared_other','Different declared digest'],['unknown','No declared digest']]);
      const runId=input('owgRunEvidenceId','text','Exact run ID');runId.maxLength=128;
      const stepId=input('owgRunEvidenceStep','text','Exact step ID');stepId.maxLength=128;
      const from=input('owgRunEvidenceFrom','text','2026-01-01T00:00:00Z');
      const before=input('owgRunEvidenceBefore','text','2026-02-01T00:00:00Z');
      const searchButton=button('Search retained runs','secondary'),prev=button('Previous page','secondary'),next=button('Next page','secondary');
      const message=el('p',null,'muted');message.setAttribute('role','status');
      const list=el('div'),detailHost=el('div'),compareHost=el('div');
      let serial=0,detailSerial=0,compareSerial=0,population=null,offset=0,nextOffset=null,left=null,right=null,selectedFilters=null;
      const compareButton=button('Compare two pinned runs','secondary');compareButton.disabled=true;
      const clearPins=button('Clear compared runs','secondary');
      const pinsLabel=el('p','No comparison runs pinned.','muted');
      function renderPins(){
        pinsLabel.textContent='Left: '+(left?left.run_id:'none')+' · Right: '+(right?right.run_id:'none');
        compareButton.disabled=!(left&&right&&(left.deployment_id!==right.deployment_id||left.run_id!==right.run_id));
        compareSerial++;compareHost.replaceChildren();
      }
      clearPins.onclick=()=>{left=right=null;renderPins();};
      function reset(){
        serial++;detailSerial++;population=null;offset=0;nextOffset=null;selectedFilters=null;
        prev.disabled=next.disabled=true;list.replaceChildren();detailHost.replaceChildren();left=right=null;renderPins();
      }
      function filterValues(){
        const values={attribution:attribution.value};
        for(const [key,node] of [['status',runStatus],['step_status',stepStatus],['run_id',runId],['step_id',stepId],['started_from',from],['started_before',before]])
          if(node.value)values[key]=node.value;
        return values;
      }
      for(const node of [runStatus,stepStatus,attribution,runId,stepId,from,before])node.addEventListener('input',()=>{reset();message.textContent='Filters changed; search again.';});
      async function openDetail(ref){
        const n=++detailSerial;detailHost.replaceChildren(el('p','Loading exact report…'));
        try{
          const data=await api(path+'/detail?'+new URLSearchParams({run_id:ref.run_id,expected_sha256:ref.run_sha256}));
          if(!active()||n!==detailSerial)return;
          if(data.reference.run_sha256!==ref.run_sha256||data.reference.deployment_id!==dep||data.reference.run_id!==ref.run_id)throw Error('Unexpected run evidence reference.');
          const run=data.run,pinLeft=button('Use as left run','secondary'),pinRight=button('Use as right run','secondary');
          pinLeft.onclick=()=>{left=data.reference;renderPins();};pinRight.onclick=()=>{right=data.reference;renderPins();};
          detailHost.replaceChildren(el('h4','Reported run '+run.run_id),
            el('p',run.status+' · '+data.attribution+' · SHA-256 '+data.reference.run_sha256),
            el('p','Reported outcome verification: '+String(run.outcome_reported_verified)+' · Reported source: '+(run.outcome_source||'not supplied')),
            el('p','Reported spec: '+(run.reported_spec_sha256||'unknown')+' · Attached design: '+(data.attached_design_sha256||'none')),
            table(['Sequence','Step ID','Activity','Reported status','Reported time (UTC)'],run.steps.map(s=>[s.seq,s.step_id,s.activity,s.status,s.at])),
            pinLeft,pinRight,el('p','No outcome or source-system state is independently verified.','muted'));
        }catch(error){if(active()&&n===detailSerial)detailHost.replaceChildren(el('p',error.message,'note'));}
      }
      async function searchPage(page=0){
        const n=++serial;detailSerial++;detailHost.replaceChildren();list.replaceChildren();prev.disabled=next.disabled=true;
        message.textContent='Reading matching retained evidence…';
        if(page===0){population=null;selectedFilters=filterValues();left=right=null;renderPins();}
        try{
          const data=await post(path+'/search',{...selectedFilters,offset:page,limit:30,...(population?{expected_population_sha256:population}:{})});
          if(!active()||n!==serial)return;
          if(data.deployment_id!==dep||(population&&data.population_sha256!==population))throw Error('Unexpected evidence population.');
          population=data.population_sha256;offset=data.offset;nextOffset=data.next_offset;
          message.textContent=data.matching_runs+' matching runs · '+data.matching_steps+' reported steps · snapshot '+population;
          const summary=el('p',Object.entries(data.status_counts).map(([k,v])=>k+': '+v).join(' · '));
          const grid=table(['Run ID','Start (UTC)','Status','Spec attribution','First reported issue','Steps','Evidence'],
            data.rows.map(row=>[row.run_id,row.started_at,row.status,row.attribution,
              row.first_reported_issue?row.first_reported_issue.step_id+' · '+row.first_reported_issue.status:'none reported',row.step_count,'']));
          const body=grid.querySelector('tbody');
          data.rows.forEach((row,i)=>{const open=button('Inspect','secondary');open.onclick=()=>openDetail(row.reference);body.rows[i].cells[6].appendChild(open);});
          list.replaceChildren(summary,grid);prev.disabled=offset===0;next.disabled=nextOffset===null;
        }catch(error){if(active()&&n===serial){reset();message.textContent=error.message;}}
      }
      searchButton.onclick=()=>searchPage(0);prev.onclick=()=>searchPage(Math.max(0,offset-30));next.onclick=()=>{if(nextOffset!==null)searchPage(nextOffset);};
      compareButton.onclick=async()=>{
        if(!left||!right)return;
        const n=++compareSerial;compareHost.replaceChildren(el('p','Comparing exact reported step sequences…'));
        try{
          const data=await post('/v1/automation-monitoring/runs/compare',{left,right});
          if(!active()||n!==compareSerial)return;
          if(data.left.reference.run_sha256!==left.run_sha256||data.right.reference.run_sha256!==right.run_sha256)throw Error('Unexpected pinned comparison.');
          const describe=step=>step?step.seq+' · '+step.step_id+' · '+step.activity+' · '+step.status+' · '+step.at:'—';
          compareHost.replaceChildren(el('h4','Paired run evidence'),
            el('p','Same declared spec: '+String(data.same_reported_spec)+' · Minimum step ID edits: '+data.minimum_step_id_edits+
              ' · Matched status changes: '+data.matched_status_changes+' · Matched label changes: '+data.matched_activity_label_changes),
            table(['Alignment','Left reported step','Right reported step','Status changed'],
              data.alignment.map(a=>[a.kind,describe(a.left),describe(a.right),a.status_changed?'yes':''])),
            el('p','One minimum insertion/deletion alignment on reported IDs; equal-cost alternatives may exist. This is not verified business-case equivalence.','muted'));
        }catch(error){if(active()&&n===compareSerial)compareHost.replaceChildren(el('p',error.message,'note'));}
      };
      root.append(el('h3','Retained run evidence'),el('p','Filter exact imported reports. Dates are timezone-qualified, with an exclusive upper bound. Nothing is executed.','muted'),
        field('Run status',runStatus),field('Exact run ID',runId),field('Exact step ID',stepId),field('Step status',stepStatus),
        field('Started from',from),field('Started before',before),field('Spec attribution',attribution),
        searchButton,prev,next,message,list,detailHost,pinsLabel,compareButton,clearPins,compareHost);
      prev.disabled=next.disabled=true;
    }
    const concurrencyButton=button('Review retained run concurrency','secondary');concurrencyButton.id='owgMonitorConcurrency';arow.appendChild(concurrencyButton);
    concurrencyButton.onclick=()=>{const dep=pick.value;if(!dep)return;const n=++evidenceGeneration,host=el('div');host.id='owgRunConcurrency';evidenceHost.replaceChildren(host);
      const active=()=>host.isConnected&&pick.value===dep&&n===evidenceGeneration;
      import('/automation-run-concurrency.js').then(m=>{if(active())m.mount(host,{api,dep,isCurrent:active});}).catch(()=>{if(active())host.textContent='Concurrency review unavailable; existing monitoring remains available.';});};
    const recoveryButton=button('Review issue sequences and later completions','secondary');
    recoveryButton.id='owgMonitorRecoveryPatterns';arow.appendChild(recoveryButton);
    recoveryButton.onclick=()=>{
      const dep=pick.value;if(!dep)return;
      const generation=++evidenceGeneration,root=el('div');evidenceHost.replaceChildren(root);root.id='owgRecoveryPatterns';
      const active=()=>root.isConnected&&pick.value===dep&&generation===evidenceGeneration;
      const start=input('owgRecoveryStart','date'),end=input('owgRecoveryEnd','date');
      const hash=input('owgRecoverySpec','text','Exact reported specification SHA-256');hash.maxLength=64;
      const unknown=input('owgRecoveryUnknown','checkbox');
      const view=el('select');view.id='owgRecoveryView';setOptions(view,[['patterns','Issue sequence patterns'],['steps','Step issue episodes'],['transitions','Consecutive issue transitions'],['runs','Exact retained runs']]);
      const runFilter=el('select');runFilter.id='owgRecoveryFilter';setOptions(runFilter,[['all','All runs'],['has_issue','Reported issues'],['no_issue','No reported issue'],['later_completion','Later same-step completion'],['unresolved_episode','Episode without later completion'],['succeeded_with_issue','Reported success with issue']]);
      const filterField=field('Run filter',runFilter);show(filterField,false);
      const run=button('Review exact issue population','secondary');run.id='owgRecoveryRun';
      const message=el('p');message.id='owgRecoveryStatus';message.setAttribute('role','status');
      const out=el('div');out.id='owgRecoveryOutput';
      let serial=0,basis=null,report=null,focus={};
      const clear=()=>{serial++;basis=report=null;focus={};out.replaceChildren();run.disabled=false;message.textContent='Review exact source settings.';};
      for(const node of [start,end,hash,unknown])node.addEventListener('input',clear);
      unknown.onchange=()=>{hash.disabled=unknown.checked;clear();};
      const save=(value,name)=>{const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      const issue=x=>x[0]+' ('+x[1]+')';
      function inspect(group){
        focus=group;view.value='runs';runFilter.value='all';show(filterField,true);load(0);
      }
      function drill(label,group){
        const b=button(label,'secondary');b.onclick=()=>inspect(group);return b;
      }
      function grid(headers,rows){
        const wrap=el('div',null,'table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
        const t=el('table'),h=el('thead'),hr=el('tr'),body=el('tbody');
        for(const x of headers){const th=el('th',x);th.scope='col';hr.append(th);}h.append(hr);
        for(const row of rows){const tr=el('tr');for(const x of row){const td=el('td');if(x instanceof Node)td.append(x);else td.textContent=String(x??'—');tr.append(td);}body.append(tr);}
        t.append(h,body);wrap.append(t);return wrap;
      }
      async function load(offset=0){
        if(!basis||!active())return;
        const n=++serial,prior=basis;report=null;out.replaceChildren();run.disabled=true;message.textContent='Reading exact retained issue evidence…';
        try{
          const payload={...prior.settings,view:view.value,run_filter:view.value==='runs'?runFilter.value:'all',
            ...(view.value==='runs'?focus:{}),offset,limit:25,...(prior.pin?{expected_population_sha256:prior.pin}:{})};
          const data=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(dep)+'/recovery-patterns',
            {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
          if(!active()||n!==serial)return;
          if(prior.pin&&prior.pin!==data.population_sha256)throw Error('Unexpected retained population.');
          basis={...prior,pin:data.population_sha256};report=data;
          const s=data.summary,p=data.page;
          message.textContent=s.runs+' retained runs · '+s.run_flag_counts.has_issue+' with issues · '+s.episode_summary.episodes+' issue episodes.';
          out.append(el('h4','Issue and later-completion intelligence'),
            grid(['Measure','Count'],[['Reported steps',s.reported_steps],['Runs with no reported steps',s.runs_without_reported_steps],
              ['Runs with later completion',s.run_flag_counts.later_completion],['Runs with unresolved episode',s.run_flag_counts.unresolved_episode],
              ['Reported success with issue',s.run_flag_counts.succeeded_with_issue],['Distinct issue sequences',s.patterns],
              ['Distinct issue steps',s.distinct_issue_steps],['Consecutive issue transitions',s.distinct_issue_transitions],
              ...Object.entries(s.episode_summary.episode_states)]),
            el('p',p.matching+' matching rows; '+p.rows.length+' shown at offset '+p.offset+'.'));
          if(p.view==='patterns')out.append(grid(['Exact ordered issue sequence','Runs','Later completion runs','Unresolved runs','Evidence'],
            p.rows.map(r=>[r.issue_sequence.map(issue).join(' → ')||'No reported issues',r.runs,r.runs_with_later_completion,r.runs_with_unresolved_episode,
              drill('Inspect pattern runs',{pattern_sha256:r.pattern_sha256})])));
          else if(p.view==='steps')out.append(grid(['Step','Runs with issue','Episodes','Later completions','Terminal unresolved','Running unresolved','Median reported elapsed seconds','Evidence'],
            p.rows.map(r=>[r.step_id,r.runs_with_issue,r.episodes,r.episode_states.later_completion,r.episode_states.terminal_without_later_completion,
              r.episode_states.running_without_later_completion,r.median_first_issue_to_later_completion_seconds,drill('Inspect step episode runs',{step_id:r.step_id})])));
          else if(p.view==='transitions')out.append(grid(['Previous reported issue','Next reported issue','Distinct runs','Occurrences','Evidence'],
            p.rows.map(r=>[issue(r.from_issue),issue(r.to_issue),r.runs,r.occurrences,drill('Inspect transition runs',{transition_sha256:r.transition_sha256})])));
          else{
            if(Object.keys(focus).length){const reset=button('Clear exact issue group','secondary');reset.onclick=()=>{focus={};load(0);};out.append(reset);}
            for(const r of p.rows){
              const d=el('details');d.append(el('summary',r.reference.run_id+' · '+r.status+' · '+r.episodes.length+' episodes'),
                el('p','Exact run digest '+r.reference.run_sha256),el('p','Issue sequence: '+(r.issue_sequence.map(issue).join(' → ')||'none')),
                grid(['Step','First issue sequence','Last issue sequence','Failure count','Handoff count','Episode state','Completion sequence','Elapsed seconds'],
                  r.episodes.map(x=>[x.step_id,x.first_issue_seq,x.last_issue_seq,x.failure_occurrences,x.handoff_occurrences,
                    x.state,x.completion_seq,x.first_issue_to_completion_seconds])));
              const inspectRun=button('Inspect exact retained run','secondary');
              const evidence=el('div');
              inspectRun.onclick=async()=>{try{const item=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(dep)+'/runs/detail?'+new URLSearchParams({run_id:r.reference.run_id,expected_sha256:r.reference.run_sha256}));
                if(!active()||report!==data)return;evidence.replaceChildren(grid(['Sequence','Step ID','Reported activity','Status','Timestamp'],item.run.steps.map(x=>[x.seq,x.step_id,x.activity,x.status,x.at])));
              }catch(error){if(active()&&report===data)evidence.replaceChildren(el('p',error.message));}};
              d.append(inspectRun,evidence);out.append(d);
            }
          }
          const prev=button('Previous issue evidence page','secondary'),next=button('Next issue evidence page','secondary');
          prev.disabled=p.offset===0;next.disabled=p.next_offset===null;prev.onclick=()=>load(Math.max(0,p.offset-25));next.onclick=()=>load(p.next_offset);
          const download=button('Download recovery evidence page','secondary');download.onclick=()=>{if(report===data&&active())save(data,'owg-recovery-evidence.json');};
          out.append(prev,next,download,el('p',data.interpretation,'muted'),el('p','Population digest: '+data.population_sha256,'muted'));
        }catch(error){if(active()&&n===serial){basis=null;message.textContent=error.message;}}
        finally{if(active()&&n===serial)run.disabled=false;}
      }
      view.onchange=()=>{focus={};runFilter.value='all';show(filterField,view.value==='runs');if(basis)load(0);};
      runFilter.onchange=()=>{if(basis)load(0);};
      run.onclick=()=>{clear();if(!start.value||!end.value||(!unknown.checked&&!/^[a-f0-9]{64}$/.test(hash.value))){message.textContent='Choose valid UTC dates and exact specification hash or unknown attribution.';return;}
        basis={settings:{start_date:start.value,end_date:end.value,reported_spec_sha256:unknown.checked?null:hash.value},pin:null};load(0);};
      root.append(el('h3','Reported issue sequences and later completions'),el('p','Review exact retained reports for one declared version or explicitly unknown attribution. Later same-step completion is not verified recovery or a retry.','muted'),
        field('UTC start date',start),field('UTC end date exclusive',end),field('Reported specification hash',hash),field('Select unknown attribution only',unknown),field('Issue evidence view',view),filterField,run,message,out);
    };
    const reliabilityButton=button('Review step reliability','secondary');
    reliabilityButton.id='owgMonitorStepReliability';arow.appendChild(reliabilityButton);
    reliabilityButton.onclick=()=>{
      const dep=pick.value;if(!dep)return;
      const generation=++evidenceGeneration,root=el('div');evidenceHost.replaceChildren(root);
      const active=()=>root.isConnected&&pick.value===dep&&generation===evidenceGeneration;
      const start=input('owgStepReliabilityStart','date'),end=input('owgStepReliabilityEnd','date');
      const spec=input('owgStepReliabilitySpec','text','Exact reported specification SHA-256');spec.maxLength=64;
      const unknown=input('owgStepReliabilityUnknown','checkbox');
      const run=button('Review retained step population','secondary'),message=el('p',null,'muted'),out=el('div');
      message.setAttribute('role','status');
      let turn=0,basis=null;
      function clear(){turn++;basis=null;out.replaceChildren();run.disabled=false;message.textContent='Review the selected inputs.';}
      for(const c of [start,end,spec,unknown])c.addEventListener('input',clear);
      unknown.onchange=()=>{spec.disabled=unknown.checked;clear();};
      const endpoint='/v1/automation-monitoring/deployments/'+encodeURIComponent(dep)+'/step-reliability';
      async function load(offset=0,step=null,runFilter='all'){
        const t=++turn,current=()=>active()&&t===turn,previous=basis;
        if(!previous)return;
        run.disabled=true;out.replaceChildren();message.textContent='Reviewing exact retained run and step evidence…';
        try{
          const r=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
            ...previous.settings,offset,limit:30,...(step!==null?{step_id:step,run_filter:runFilter}:{}),
            ...(previous.pin?{expected_population_sha256:previous.pin}:{})})});
          if(!current())return;
          if(previous.pin&&r.population_sha256!==previous.pin)throw Error('Unexpected evidence population.');
          basis={...previous,pin:r.population_sha256};
          message.textContent=r.matching_runs+' retained runs · '+r.matching_steps+' steps · '+r.distinct_step_ids+' step IDs · '+r.runs_without_reported_steps+' runs with no reported steps.';
          const p=r.page,parts=[el('p','Population SHA-256: '+r.population_sha256,'muted'),el('p',r.runs_with_reported_issue_step+' runs with a reported issue step.')];
          if(p.kind==='step_groups'){
            const grid=table(['Step ID','Label count','Runs containing step','Occurrences','Failed runs','Handoff runs','Issue share among containing runs','First issue runs','Repeated runs','Mixed-status runs','Evidence'],
              p.rows.map(x=>[x.step_id,x.distinct_reported_activity_labels,x.runs_with_step,x.occurrences,x.runs_with_status.failed,
                x.runs_with_status.handed_to_human,pct(x.reported_issue_share_among_runs_with_step),x.runs_where_first_reported_issue_is_step,x.runs_with_repeated_step_id,x.runs_with_mixed_statuses,'']));
            p.rows.forEach((x,i)=>{const b=button('Review runs','secondary');b.onclick=()=>load(0,x.step_id);grid.querySelector('tbody').rows[i].cells[10].appendChild(b);});
            parts.push(grid);
          }else{
            const back=button('Return to step groups','secondary');back.onclick=()=>load(0,null);
            const filter=el('select');filter.id='owgStepReliabilityRunFilter';
            setOptions(filter,[['all','All containing runs'],['issue','Failed or handed off'],['failed','Failed occurrence'],
              ['handoff','Human handoff'],['first_issue','First reported issue'],['mixed_status','Mixed reported statuses']]);
            filter.value=p.run_filter;filter.onchange=()=>load(0,step,filter.value);
            parts.push(back,el('h4','Exact step ID: '+step),field('Filter matching runs',filter),
              el('p',r.selected_step.runs_with_failed_and_completed_occurrences+' runs contain both failed and completed occurrences.'));
            for(const row of p.rows){const detail=el('details');detail.append(el('summary',row.reference.run_id+' · '+row.run_status+' · '+row.started_at),
                el('p','Exact report SHA-256 '+row.reference.run_sha256),
                table(['Sequence','Activity','Reported status','UTC'],row.matching_step_occurrences.map(x=>[x.seq,x.activity,x.status,x.at])));parts.push(detail);}
          }
          const prev=button('Previous evidence page','secondary'),next=button('Next evidence page','secondary');
          prev.disabled=p.offset===0;next.disabled=p.next_offset===null;
          prev.onclick=()=>load(Math.max(0,p.offset-30),step,runFilter);next.onclick=()=>load(p.next_offset,step,runFilter);
          const download=button('Download current review page JSON','secondary');download.onclick=()=>{
            if(!current())return;
            const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));
            const a=el('a');a.href=url;a.download='owg-step-reliability.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
          };
          out.replaceChildren(...parts,el('p',p.matching+' matching entries · offset '+p.offset),prev,next,download,...r.interpretation.map(x=>el('p',x,'muted')));
        }catch(error){if(current()){basis=null;message.textContent=error.message;}}
        finally{if(current())run.disabled=false;}
      }
      run.onclick=()=>{
        clear();
        if(!start.value||!end.value||(!unknown.checked&&!/^[a-f0-9]{64}$/.test(spec.value))){message.textContent='Choose UTC dates and an exact reported hash, or explicitly select missing attribution.';return;}
        basis={settings:{start_date:start.value,end_date:end.value,reported_spec_sha256:unknown.checked?null:spec.value},pin:null};load();
      };
      root.append(el('h3','Version-scoped retained step reliability'),el('p','Compare reported issue frequency and repeated steps within one exact declared hash. Unknown attribution may mix versions. This is not a live runtime check.','muted'),
        field('UTC start date',start),field('UTC end date (exclusive)',end),field('Reported specification SHA-256',spec),
        field('Explicitly review runs without a reported specification',unknown),run,message,out);
    };
    evidenceButton.onclick=openRunEvidence;
    portfolioButton.onclick=renderPortfolio;
    objectiveButton.onclick=()=>{
      const selected=pick.value;if(!selected)return;
      const root=el('section');objectiveHost.replaceChildren(root);
      let generation=0,report=null,settings=null,offset=0;
      const active=()=>root.isConnected&&pick.value===selected;
      const start=input('owgObjectiveStart','datetime-local'),end=input('owgObjectiveEnd','datetime-local'),asOf=input('owgObjectiveAsOf','datetime-local');
      const localStamp=d=>{const t=new Date(d.getTime()-d.getTimezoneOffset()*60000);return t.toISOString().slice(0,16);};
      const now=new Date();end.value=asOf.value=localStamp(now);start.value=localStamp(new Date(now.getTime()-7*86400000));
      const deadline=input('owgObjectiveDeadline','number');deadline.min='1';deadline.max='2592000';deadline.value='3600';
      const target=input('owgObjectiveTarget','number');target.min='1';target.max='10000';target.value='9500';
      const verified=input('owgObjectiveVerified','checkbox');const digest=input('owgObjectiveSpec','text','Optional exact specification SHA-256');digest.maxLength=64;
      const filter=el('select');filter.id='owgObjectiveFilter';setOptions(filter,[['','All runs'],['met','Met'],['missed','Missed'],['pending','Pending'],['unknown','Unknown']]);
      const run=button('Evaluate service objective','secondary'),prev=button('Previous page','secondary'),next=button('Next page','secondary'),download=button('Download current evidence page','secondary');
      const note=el('p');note.setAttribute('role','status');const output=el('div');
      const grid=el('div',null,'row');grid.append(field('Cohort start (local time)',start),field('Cohort end (exclusive)',end),field('Observation cutoff (local time)',asOf),
        field('Deadline in seconds',deadline),field('Target in basis points (9500 = 95%)',target),field('Require reported verified outcome',verified),field('Exact spec digest',digest),field('Evidence filter',filter));
      const reset=()=>{generation++;report=null;settings=null;offset=0;output.replaceChildren();prev.disabled=next.disabled=download.disabled=true;run.disabled=false;note.textContent='Settings changed; evaluate again.';};
      grid.addEventListener('input',reset);grid.addEventListener('change',reset);
      async function page(n){
        const k=++generation;run.disabled=prev.disabled=next.disabled=download.disabled=true;note.textContent='Reading the retained run cohort…';
        try{
          const result=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(selected)+'/service-objectives',
            {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...settings,offset:n,limit:30,...(report?{expected_population_sha256:report.population_sha256}:{})})});
          if(!active()||k!==generation)return;
          if(result.deployment_id!==selected||(report&&result.population_sha256!==report.population_sha256))throw Error('Retained cohort changed.');
          report=result;offset=n;const a=result.summary;
          output.replaceChildren(el('h4','Retained cohort objective'),el('p',a.verdict.replaceAll('_',' ')),
            el('p','Objective attainment bounds: '+pct(a.lower_met_fraction)+' to '+pct(a.upper_met_fraction)+' · '+a.runs+' retained starts.'),
            table(['Met','Missed','Pending','Unknown','Required met','Permitted misses','Remaining budget (known misses)','Remaining budget (worst case)'],
              [[a.counts.met,a.counts.missed,a.counts.pending,a.counts.unknown,a.required_met_runs,a.permitted_misses,a.remaining_error_budget_after_known_misses,a.remaining_error_budget_if_unresolved_miss]]),
            el('h4','Daily start cohorts'),
            table(['Start (UTC)','End (UTC)','Runs','Met','Missed','Pending','Unknown','Attainment bounds','Verdict'],result.daily_start_cohorts.map(d=>[d.start,d.end,d.runs,d.counts.met,d.counts.missed,d.counts.pending,d.counts.unknown,pct(d.lower_met_fraction)+' – '+pct(d.upper_met_fraction),d.verdict])),
            el('h4','Run evidence · '+result.matching_runs+' matching rows'),
            table(['Run ID','Start','Finish','Reported status','Reported verified outcome','Objective','Reason'],result.rows.map(r=>[r.run_id,r.started_at,r.finished_at??'Unknown',r.reported_status,r.reported_outcome_verified===null?'Unknown':String(r.reported_outcome_verified),r.objective_status,r.reason])),
            el('p',result.interpretation,'muted'));
          note.textContent='Exact retained population digest: '+result.population_sha256;
          prev.disabled=n===0;next.disabled=result.next_offset===null;download.disabled=false;
        }catch(error){if(active()&&k===generation){reset();note.textContent=error.message;}}
        finally{if(active()&&k===generation)run.disabled=false;}
      }
      run.onclick=()=>{
        reset();
        if(!start.value||!end.value||!asOf.value||!deadline.value||!target.value||!deadline.checkValidity()||!target.checkValidity()||(digest.value&&!/^[a-f0-9]{64}$/.test(digest.value))){
          note.textContent='Enter valid dates, integer deadline and target, and an optional lowercase SHA-256.';return;
        }
        settings={start:new Date(start.value).toISOString(),end:new Date(end.value).toISOString(),as_of:new Date(asOf.value).toISOString(),
          deadline_seconds:Number(deadline.value),target_basis_points:Number(target.value),require_reported_verified_outcome:verified.checked};
        if(digest.value)settings.exact_spec_sha256=digest.value;if(filter.value)settings.status=filter.value;
        page(0);
      };
      prev.onclick=()=>page(Math.max(0,offset-30));next.onclick=()=>{if(report?.next_offset!==null&&report?.next_offset!==undefined)page(report.next_offset);};
      download.onclick=()=>{if(!report||!active())return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-service-objective-page-'+offset+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      prev.disabled=next.disabled=download.disabled=true;
      root.append(el('h3','Reported automation service objectives'),el('p','This is a retrospective review of currently retained reports, not a live service guarantee or reconstructed historical reporting state. Unknown and pending runs remain in the denominator.','muted'),grid,run,note,output,prev,next,download);
    };
    releaseButton.onclick=()=>renderRelease().catch(error=>{status.textContent=error.message;});
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
