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
    const releaseButton=button('Release inventory','secondary');releaseButton.id='owgMonitorRelease';
    const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Attach reviewed design (workflow.spec.json)',design),toLog,channelButton,incidentButton,releaseButton,remove);
    const channelHost=el('div');channelHost.id='owgMonitorChannelPanel';const incidentHost=el('div');incidentHost.id='owgMonitorIncidentPanel';
    const releaseHost=el('div');releaseHost.id='owgMonitorReleasePanel';
    manage.append(mrow,arow,channelHost,incidentHost,releaseHost);show(manage,false);
    const portfolio=el('div');portfolio.id='owgMonitorPortfolio';
    const portfolioDays=input('owgMonitorPortfolioDays','number');portfolioDays.min='1';portfolioDays.max='365';portfolioDays.value='30';
    const portfolioButton=button('Review all deployment evidence','secondary');portfolioButton.id='owgMonitorPortfolioShow';
    const portfolioControls=el('div',null,'row');portfolioControls.style.alignItems='end';
    portfolioControls.append(field('Portfolio recent days',portfolioDays),portfolioButton);
    const rolloutButton=button('Review version rollout evidence','secondary');rolloutButton.id='owgMonitorRollout';
    const rolloutHost=el('div');rolloutHost.id='owgMonitorRolloutPanel';portfolioControls.append(rolloutButton);
    const results=el('div');results.id='owgMonitorResults';
    card.append(row,consentLabel,importButton,status,portfolioControls,portfolio,rolloutHost,manage,results);
    const anchor=document.getElementById('owgPddCard')||document.getElementById('owgCommsCard')||document.getElementById('owgEventLogCard');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let deployments=[],renderVersion=0,loadVersion=0,incidentVersion=0,releaseVersion=0,portfolioVersion=0;
    let incidentScope='selected',incidentStatus='',incidentSearch='';
    const clearResults=()=>{portfolioVersion++;portfolio.replaceChildren();rolloutHost.replaceChildren();renderVersion++;incidentVersion++;incidentScope='selected';incidentStatus='';incidentSearch='';results.replaceChildren();channelHost.replaceChildren();incidentHost.replaceChildren();releaseVersion++;releaseHost.replaceChildren();document.getElementById('owgMonitorRunEvidencePanel')?.replaceChildren();};
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
    function openRolloutReview(){
      const root=el('div');rolloutHost.replaceChildren(root);const active=()=>root.isConnected;
      if(deployments.length<2){root.append(el('p','Retain at least two deployments with distinct version claims first.','note'));return;}
      const selectField=(id,values)=>{const s=document.createElement('select');s.id=id;setOptions(s,values);return s;};
      const options=[['','Choose deployment'],...deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime])];
      const baseline=selectField('owgRolloutBaseline',options),candidate=selectField('owgRolloutCandidate',options);
      const baselineDigest=input('owgRolloutBaselineDigest','text','Exact 64-character SHA-256'),candidateDigest=input('owgRolloutCandidateDigest','text','Exact 64-character SHA-256');
      baselineDigest.maxLength=candidateDigest.maxLength=64;
      const start=input('owgRolloutStart','text','2026-01-01T00:00:00Z'),end=input('owgRolloutEnd','text','2026-02-01T00:00:00Z');
      const minimum=input('owgRolloutMinimum','number');minimum.min='1';minimum.max='50000';minimum.step='1';
      const share=input('owgRolloutShare','number'),decrease=input('owgRolloutDecrease','number'),unknown=input('owgRolloutUnknown','number');
      for(const n of [share,decrease,unknown]){n.min='0';n.max='100';n.step='any';}
      const review=button('Review exact version cohorts','secondary'),download=button('Download review JSON','secondary');download.disabled=true;
      const message=el('p',null,'muted');message.setAttribute('role','status');
      const summary=el('div'),list=el('div'),detail=el('div');
      const side=selectField('owgRolloutEvidenceSide',[['candidate','Candidate'],['baseline','Baseline']]);
      const attr=selectField('owgRolloutEvidenceAttribution',[['exact','Exact version'],['unknown','Unhashed'],['other','Other version'],['all','All']]);
      const outcome=selectField('owgRolloutEvidenceStatus',[['','Any status'],...['succeeded','failed','handed_to_human','cancelled','running'].map(v=>[v,v])]);
      const prev=button('Previous evidence page','secondary'),next=button('Next evidence page','secondary');
      const evidenceControls=el('div');evidenceControls.hidden=true;evidenceControls.append(field('Evidence cohort',side),field('Version attribution',attr),field('Reported status',outcome),prev,next);
      let basis=null,population=null,latest=null,serial=0,detailSerial=0;
      const invalidate=()=>{serial++;detailSerial++;basis=population=latest=null;download.disabled=true;review.disabled=false;evidenceControls.hidden=true;
        summary.replaceChildren();list.replaceChildren();detail.replaceChildren();prev.disabled=next.disabled=true;message.textContent='Review inputs and calculate again.';};
      for(const n of [baselineDigest,candidateDigest,start,end,minimum,share,decrease,unknown])n.oninput=invalidate;
      for(const [node,digest] of [[baseline,baselineDigest],[candidate,candidateDigest]])node.onchange=()=>{
        digest.value=deployments.find(d=>d.deployment_id===node.value)?.reported_spec_sha256||'';invalidate();};
      const range=b=>b?pct(b.lower)+' – '+pct(b.upper):'Unavailable';
      const post=body=>api('/v1/automation-monitoring/rollout-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      async function openDetail(ref){
        const ticket=++detailSerial;detail.replaceChildren(el('p','Reading pinned run report…'));
        try{
          const r=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(ref.deployment_id)+'/runs/detail?'+new URLSearchParams({run_id:ref.run_id,expected_sha256:ref.run_sha256}));
          if(!active()||ticket!==detailSerial)return;
          if(r.reference.run_sha256!==ref.run_sha256||r.reference.run_id!==ref.run_id||r.reference.deployment_id!==ref.deployment_id)throw Error('Unexpected run reference.');
          detail.replaceChildren(el('h4','Exact reported run '+r.run.run_id),el('p',r.run.status+' · '+ref.run_sha256),
            table(['Sequence','Step ID','Activity','Reported status','Reported time'],r.run.steps.map(x=>[x.seq,x.step_id,x.activity,x.status,x.at])),
            el('p','Reported evidence only; not independent outcome verification.','muted'));
        }catch(e){if(active()&&ticket===detailSerial)detail.replaceChildren(el('p',e.message,'note'));}
      }
      function draw(r){
        const entries=['baseline','candidate'].map(k=>[k,r.cohorts[k]]);
        summary.replaceChildren(el('h4','Version-attributed rollout review'),
          el('p',r.all_declared_gates_met?'All declared evidence gates met; not rollout approval.':'At least one declared evidence gate is unmet or unresolved.','note'),
          table(['Cohort','Deployment','All runs','Exact version','Other version','Unhashed','Attributed success bounds','Unhashed sensitivity bounds'],entries.map(([k,c])=>[k,c.name,c.all_retained_runs_in_window,c.attributed_runs,c.other_version_runs,c.unhashed_runs,range(c.attributed_eventual_success_share_bounds),range(c.possible_version_success_share_with_unhashed_bounds)])),
          el('p','Candidate minus baseline reported success share: '+range(r.candidate_minus_baseline_attributed_success_share_bounds)+'; sensitivity including possible unhashed attribution: '+range(r.candidate_minus_baseline_with_unhashed_sensitivity_bounds)+'.'),
          el('p','Same reported runtime: '+String(r.same_reported_runtime)+'. Exact selected spec matches current declaration: '+entries.map(([k,c])=>k+' '+String(c.selected_spec_matches_current_declaration)).join('; ')+'.'),
          table(['Cohort','Attribution','Succeeded','Failed','Handoff','Cancelled','Running'],entries.flatMap(([k,c])=>Object.entries(c.status_counts_by_attribution).map(([a,v])=>[k,a,v.succeeded,v.failed,v.handed_to_human,v.cancelled,v.running]))),
          table(['Declared gate','Status','Observed','Threshold'],r.review_gates.map(g=>[g.gate,g.status,typeof g.observed==='object'?range(g.observed):String(g.observed),String(g.threshold)])),
          el('p','Population SHA-256: '+r.population_sha256,'muted'));
        const notes=el('ul');r.interpretation.forEach(t=>notes.append(el('li',t)));summary.append(notes);
        const p=r.evidence_page;
        const grid=table(['Run ID','Start','Status','Attribution','Spec digest','Inspect'],p.rows.map(x=>[x.run_id,x.started_at,x.status,x.attribution,x.reported_spec_sha256||'Unknown','']));
        p.rows.forEach((x,i)=>{const b=button('Inspect exact report','secondary');b.onclick=()=>openDetail(x.reference);grid.querySelector('tbody').rows[i].cells[5].append(b);});
        list.replaceChildren(el('p',p.matching_runs+' matching evidence runs; showing '+p.rows.length+' at offset '+p.offset+'.'),grid);
        prev.disabled=p.offset===0;next.disabled=p.next_offset===null;
        prev.onclick=()=>loadPage(Math.max(0,p.offset-30));next.onclick=()=>{if(p.next_offset!==null)loadPage(p.next_offset);};
      }
      async function loadPage(offset=0){
        if(!basis)return;const ticket=++serial;detailSerial++;detail.replaceChildren();list.replaceChildren();review.disabled=true;download.disabled=true;prev.disabled=next.disabled=true;
        message.textContent='Reading exact retained version cohorts…';
        try{
          const r=await post({...basis,evidence_side:side.value,evidence_attribution:attr.value,evidence_status:outcome.value||null,
                             offset,limit:30,...(population?{expected_population_sha256:population}:{})});
          if(!active()||ticket!==serial)return;
          if(population&&r.population_sha256!==population)throw Error('Unexpected rollout population.');
          population=r.population_sha256;latest=r;draw(r);evidenceControls.hidden=false;download.disabled=false;message.textContent='Retained rollout evidence reviewed.';
        }catch(e){if(active()&&ticket===serial){invalidate();message.textContent=e.message;}}
        finally{if(active()&&ticket===serial)review.disabled=false;}
      }
      review.onclick=()=>{
        invalidate();const nums=[minimum,share,decrease,unknown];
        if(!baseline.value||!candidate.value||baseline.value===candidate.value||
          ![baselineDigest,candidateDigest].every(n=>/^[a-f0-9]{64}$/.test(n.value))||baselineDigest.value===candidateDigest.value||
          !start.value||!end.value||nums.some(n=>n.value===''||!n.checkValidity())){message.textContent='Select distinct deployments and digests, enter a common time window and every explicit threshold.';return;}
        basis={baseline:{deployment_id:baseline.value,spec_sha256:baselineDigest.value},candidate:{deployment_id:candidate.value,spec_sha256:candidateDigest.value},
          started_from:start.value,started_before:end.value,minimum_attributed_runs:Number(minimum.value),minimum_candidate_success_share:Number(share.value)/100,
          maximum_success_share_decrease:Number(decrease.value)/100,maximum_unknown_attribution_share:Number(unknown.value)/100};
        loadPage();
      };
      for(const n of [side,attr,outcome])n.onchange=()=>{if(basis&&population)loadPage();};
      download.onclick=()=>{if(!latest||!active())return;const url=URL.createObjectURL(new Blob([JSON.stringify(latest,null,2)],{type:'application/json'}));
        try{const a=el('a');a.href=url;a.download='owg-rollout-evidence-review.json';a.click();}finally{URL.revokeObjectURL(url);}};
      root.append(el('h3','Version rollout evidence review'),el('p','Compare distinct retained deployments and exact reported specification digests in a common start window. All inputs are explicit. Gates are not approval, and no runtime is contacted.','muted'),
        field('Baseline deployment',baseline),field('Baseline version SHA-256',baselineDigest),field('Candidate deployment',candidate),field('Candidate version SHA-256',candidateDigest),
        field('Started from (inclusive UTC)',start),field('Started before (exclusive UTC)',end),field('Minimum exact-version runs per cohort',minimum),
        field('Minimum candidate reported success (%)',share),field('Maximum candidate decrease (percentage points)',decrease),field('Maximum unhashed share (%)',unknown),
        review,download,message,summary,evidenceControls,list,detail);
    }
    rolloutButton.onclick=openRolloutReview;
    evidenceButton.onclick=openRunEvidence;
    portfolioButton.onclick=renderPortfolio;
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
