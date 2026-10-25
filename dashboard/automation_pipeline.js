/* Automation pipeline: review candidate automations locally. Approval records a decision; it never runs anything. */
(() => {
  'use strict';
  const QUESTIONS=[['rules_based','Decisions follow clear rules'],['structured_inputs','Inputs are structured'],['process_stability','The process rarely changes'],['digital_inputs','Inputs arrive digitally'],['low_exception_rate','Exceptions are rare']];
  const STATE_LABELS={idea:'Idea',assessing:'Assessing',approved:'Approved',rejected:'Rejected',on_hold:'On hold',implemented:'Implemented'};
  const QUADRANTS={quick_win:'Quick win',major_project:'Major project',fill_in:'Fill-in',deprioritize:'Deprioritize'};
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};
  const select=(id,options)=>{const node=document.createElement('select');node.id=id;options.forEach(([v,l])=>{const o=el('option',l);o.value=v;node.appendChild(o);});return node;};

  // Pure helper, exercised by tests: builds an assessment body or throws a readable error.
  function assessmentBody(values,revision){
    const ease={};
    for(const [key] of QUESTIONS){const n=Number(values[key]);if(!(n>=1&&n<=5))throw new Error('Answer every ease question from 1 to 5.');ease[key]=n;}
    const cases=Number(values.cases),minutes=Number(values.minutes),share=Number(values.share);
    if(!(cases>=0)||values.cases==='')throw new Error('Enter cases per month.');
    if(!(minutes>=0)||values.minutes==='')throw new Error('Enter minutes per case.');
    if(!(share>=0&&share<=100)||values.share==='')throw new Error('Enter the eligible share from 0 to 100%.');
    return {ease,cases_per_month:cases,minutes_per_case:minutes,eligible_share:share/100,compliance_review_needed:values.compliance===true,expected_revision:revision};
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Pipeline request failed ('+response.status+').');}
    return response;
  }
  const post=async(path,body)=>(await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgPipelineCard'))return;
    const card=el('div',null,'card');card.id='owgPipelineCard';
    card.appendChild(el('h2','Automation pipeline'));
    card.appendChild(el('div','Collect automation ideas, assess how easy and valuable they look, and record decisions. Approving an idea only records your decision here: nothing is run, built or sent, and modeled hours come from the numbers you enter, not measured savings.','muted'));
    const title=input('owgPipelineTitle','text','For example: Copy approved prices into renewals');title.maxLength=200;
    const description=input('owgPipelineDescription','text','Optional details');description.maxLength=2000;
    const source=select('owgPipelineSource',[['manual','Entered by hand'],['process_catalog','Saved process'],['event_log','Event log'],['repeated_workflow','Repeated workflow'],['communications','Communications mining']]);
    const reference=input('owgPipelineReference','text','Optional reference');reference.maxLength=200;
    const add=button('Add idea');add.id='owgPipelineAdd';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Idea',title),field('Details',description),field('Found in',source),field('Reference',reference),add);
    const filter=select('owgPipelineFilter',[['','All states'],...Object.entries(STATE_LABELS)]);
    const exportLink=button('Download CSV','secondary');exportLink.id='owgPipelineCsv';
    const tools=el('div',null,'row');tools.style.alignItems='end';tools.append(field('Show',filter),exportLink);
    const status=el('div',null,'muted');status.id='owgPipelineStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const counts=el('div',null,'muted');counts.id='owgPipelineCounts';
    const list=el('div');list.id='owgPipelineList';
    const detail=el('div');detail.id='owgPipelineDetail';
    card.append(row,tools,status,counts,list,detail);
    panel.appendChild(card);

    async function refresh(){
      const q=filter.value?'?state='+encodeURIComponent(filter.value):'';
      const r=await (await api('/v1/automation-pipeline'+q)).json();
      counts.textContent=Object.entries(r.counts).filter(([,n])=>n).map(([s,n])=>STATE_LABELS[s]+': '+n).join(' · ')||'No ideas yet.';
      const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('tr');
      ['Idea','State','Ease','Modeled hours / month','Priority',''].forEach(h=>head.appendChild(el('th',h)));
      const thead=el('thead');thead.appendChild(head);const body=el('tbody');
      r.ideas.forEach(idea=>{
        const tr=el('tr');const open=button('Open','secondary');open.setAttribute('aria-label','Open idea '+idea.title);open.onclick=()=>show(idea.idea_id).catch(e=>{status.textContent=e.message;});
        [idea.title,STATE_LABELS[idea.state],idea.score?idea.score.ease_score:'—',idea.score?idea.score.modeled_hours_per_month:'—',idea.score?QUADRANTS[idea.score.quadrant]:'Not assessed'].forEach(v=>tr.appendChild(el('td',v)));
        const cell=el('td');cell.appendChild(open);tr.appendChild(cell);body.appendChild(tr);
      });
      t.append(thead,body);wrap.appendChild(t);list.replaceChildren(r.ideas.length?wrap:el('div','No ideas in this view.','muted'));
    }

    let detailGeneration=0;
    async function show(ideaId){
      const generation=++detailGeneration;
      const idea=await (await api('/v1/automation-pipeline/'+encodeURIComponent(ideaId))).json();
      if(generation!==detailGeneration)return;
      const box=el('div');box.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin-top:8px';
      box.appendChild(el('h3',idea.title));
      if(idea.description)box.appendChild(el('div',idea.description));
      box.appendChild(el('div','State: '+STATE_LABELS[idea.state]+' · revision '+idea.revision+(idea.source.reference?' · from '+idea.source.reference:''),'muted'));
      if(idea.process_reference){
        const ref=idea.process_reference;
        const source=el('div');source.id='owgPipelineProcessStatus';
        const labels={current:'Current saved revision',changed:'Process changed since this idea was created',
          unavailable:'Linked revision is unavailable',reference_mismatch:'Linked reference no longer matches',
          check_failed:'Process status could not be checked'};
        source.appendChild(el('div','Linked process: '+ref.workflow_id+' · revision '+ref.revision));
        const message=el('div','Checking linked process…','muted');source.appendChild(message);
        const check=button('Refresh process status','secondary');
        const open=button('Open linked saved process','secondary');open.disabled=true;
        open.onclick=()=>Promise.resolve(window.owgProcessCatalog?.openSaved(ref)).then(()=>{
          document.getElementById('owgCatalogEditor')?.scrollIntoView({block:'start'});
        }).catch(e=>{status.textContent=e.message;});
        const update=async()=>{
          check.disabled=true;
          try{
            const result=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/process-status')).json();
            if(generation!==detailGeneration)return;
            message.textContent=(labels[result.status]||'Process status is unknown')+(result.latest_revision?' · latest revision '+result.latest_revision:'')+'. Checked now; decisions recheck the source.';
            open.disabled=!['current','changed'].includes(result.status);
          }catch(e){if(generation===detailGeneration){message.textContent='Process status could not be checked.';open.disabled=true;}}
          finally{check.disabled=false;}
        };
        check.onclick=()=>update();source.append(check,open);box.appendChild(source);update();
      }else if(idea.source.kind==='process_catalog'){
        box.appendChild(el('div','This source is a descriptive reference only; no saved process revision is linked.','muted'));
      }
      const journeyButton=button('View evidence journey','secondary');journeyButton.id='owgPipelineJourney';
      const journeyHost=el('div');journeyHost.id='owgPipelineJourneyResult';journeyHost.style.overflowWrap='anywhere';
      journeyButton.onclick=async()=>{
        journeyButton.disabled=true;journeyHost.replaceChildren(el('div','Checking local references…','muted'));
        try{
          const data=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/journey')).json();
          if(generation!==detailGeneration)return;
          journeyHost.replaceChildren(el('h4','Evidence journey'));
          const stages=el('ol');
          stages.appendChild(el('li','Dataset and cohort → process: origin is not linked.'));
          const process=data.process_status,ref=process.process_reference;
          const sourceLabels={current:'current',changed:'changed since this idea',unavailable:'unavailable',reference_mismatch:'reference no longer matches',check_failed:'could not be checked'};
          stages.appendChild(el('li',ref?'Saved process '+ref.workflow_id+' · revision '+ref.revision+' · '+(sourceLabels[process.status]||'unknown status')+' · SHA-256 '+ref.content_sha256:'Saved process: no exact revision linked.'));
          stages.appendChild(el('li','Opportunity revision '+data.idea.revision+' · '+(STATE_LABELS[data.idea.state]||data.idea.state)+'. Review state does not grant execution authority.'));
          journeyHost.append(stages,el('div','Checked local references now, across separate stores. Refresh after changes. Reported specifications do not prove which artifact ran; source-check results are not retained here.','muted'));
          if(!data.projects.length){journeyHost.appendChild(el('p','No project artifacts recorded.'));return;}
          const choice=select('owgPipelineJourneyProject',data.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at]));
          const artifact=el('div');artifact.id='owgPipelineJourneyArtifact';
          const render=()=>{
            const p=data.projects.find(item=>item.project_id===choice.value);if(!p)return;
            artifact.replaceChildren();const list=el('dl');
            const add=(label,value)=>list.append(el('dt',label),el('dd',value));
            add('Project artifact',p.project_id+' · '+(p.idea_revision_is_current?'current idea revision':'historical idea revision'));
            add('Specification SHA-256',p.spec_sha256);add('ZIP SHA-256',p.archive_sha256);
            artifact.appendChild(list);
            const linked=el('ul');
            const labels={matching_reported_specification:'Matching reported specification',unavailable:'Reported deployment unavailable',specification_mismatch:'Reported specification changed',unbound_specification:'Reported specification unbound',check_failed:'Reported specification could not be checked'};
            p.reported_specifications.forEach(r=>linked.appendChild(el('li',r.deployment_id+' · '+(labels[r.status]||'Unknown report status')+(r.reported_run_count===null?'':' · '+r.reported_run_count+' reported runs')+'. Artifact execution and individual run/case attribution are unverified.')));
            artifact.append(el('h5','Reported specifications'),p.reported_specifications.length?linked:el('p','No reported specifications linked.'));
            const checks=p.source_check_exports;
            artifact.append(el('h5','Source-check exports'),el('p',checks.count?checks.count+' recorded · latest '+checks.last_export_at+' · document SHA-256 '+checks.last_document_sha256+'. Receipt and result are not retained; no outcome can be inferred from this record.':'No source-check exports recorded.'));
            artifact.appendChild(el('p','Business outcomes and agent causality remain unverified.'));
          };
          choice.onchange=render;journeyHost.append(field('Recorded project',choice),artifact);render();
          if(data.projects_truncated)journeyHost.appendChild(el('p','Only the first '+data.max_projects+' projects are shown.'));
        }catch(e){if(generation===detailGeneration)journeyHost.replaceChildren(el('div',e.message,'muted'));}
        finally{journeyButton.disabled=false;journeyButton.textContent='Refresh evidence journey';}
      };
      box.append(journeyButton,journeyHost);
      if(idea.process_reference){
        const projects=el('div');projects.id='owgPipelineProjects';
        projects.appendChild(el('h4','Agent project exports'));
        projects.appendChild(el('div','Download a starter for this exact idea and saved design. Tool mappings and execution remain disabled. Export records show what was generated, not a deployment or verified outcome.','muted'));
        const exports=el('div');const message=el('div',null,'muted');message.setAttribute('role','status');
        const refreshProjects=async()=>{
          const result=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects')).json();
          if(generation!==detailGeneration)return;
          const list=el('ul');
          result.projects.forEach(p=>list.appendChild(el('li','Idea revision '+p.idea_revision+' · '+p.created_at+' · '+(p.idea_revision_is_current?'current idea revision':'historical idea revision')+' · specification '+p.spec_sha256.slice(0,12)+'…')));
          exports.replaceChildren(result.projects.length?list:el('div','No project exports recorded.','muted'));
        };
        const download=button('Download tracked agent project','secondary');download.id='owgPipelineProjectDownload';
        download.disabled=!['approved','implemented'].includes(idea.state);
        download.onclick=async()=>{
          download.disabled=true;
          try{
            const response=await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/agent-project',{
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:idea.revision})});
            const blob=await response.blob();const url=URL.createObjectURL(blob);const link=el('a');
            link.href=url;link.download='owg-opportunity-project.zip';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            if(generation===detailGeneration){message.textContent='Project downloaded. Its specification and archive hashes are recorded; execution remains disabled.';await refreshProjects();}
          }catch(e){if(generation===detailGeneration)message.textContent=e.message;}
          finally{download.disabled=!['approved','implemented'].includes(idea.state);}
        };
        projects.append(download,message,exports);
        const reviewReports=button('Review imported run reports','secondary');reviewReports.id='owgPipelineReviewReports';
        const reports=el('div');reports.id='owgPipelineReportLinks';let reportVersion=0;
        const renderReports=async()=>{
          const version=++reportVersion;
          const data=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports')).json();
          if(generation!==detailGeneration||version!==reportVersion)return;
          const note=el('div');note.setAttribute('role','status');
          const help=el('div','Link a selected imported report only when its reported specification and attached design both match this exported project. These remain customer reports: individual runs, execution and outcomes are not independently verified.','muted');
          const artifact=select('owgPipelineReportProject',data.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at+' · '+p.spec_sha256.slice(0,12)+'…']));
          const deployment=select('owgPipelineReportDeployment',data.deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime+' · '+d.reported_run_count+' reported runs']));
          const add=button('Link selected report','secondary');add.id='owgPipelineLinkReport';
          add.disabled=!data.monitoring_metadata_available||!data.projects.length||!data.deployments.length;
          add.onclick=async()=>{
            add.disabled=true;
            try{
              await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports',{
                project_id:artifact.value,deployment_id:deployment.value,expected_revision:idea.revision});
              if(generation===detailGeneration){await renderReports();status.textContent='Report link saved. Matching hashes are reported metadata, not independently verified execution or outcomes.';}
            }catch(e){if(generation===detailGeneration)note.textContent=e.message;}
            finally{add.disabled=!data.monitoring_metadata_available||!data.projects.length||!data.deployments.length;}
          };
          const rows=el('ul');
          const labels={matching_reported_specification:'Matching reported specification',unbound_specification:'Specification is not bound',
            specification_mismatch:'Specification changed or does not match',unavailable:'Reported deployment unavailable',check_failed:'Report source could not be checked'};
          data.links.forEach(r=>{
            const source=data.deployments.find(d=>d.deployment_id===r.deployment_id);
            const item=el('li',(source?source.name:r.deployment_id)+' · '+(labels[r.status]||'Unknown status'));
            const remove=button('Remove report link','secondary');remove.setAttribute('aria-label','Remove report link '+r.deployment_id);
            remove.onclick=async()=>{
              try{await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports/unlink',{
                project_id:r.project_id,deployment_id:r.deployment_id,expected_revision:idea.revision});
                if(generation===detailGeneration)await renderReports();
              }catch(e){if(generation===detailGeneration)note.textContent=e.message;}
            };
            item.appendChild(remove);rows.appendChild(item);
          });
          if(!data.monitoring_metadata_available)note.textContent='Monitoring metadata is unavailable; report links cannot be validated.';
          reports.replaceChildren(el('h4','Imported report links'),help,field('Exported project',artifact),field('Imported report',deployment),add,note,
            data.links.length?rows:el('div','No report links recorded.','muted'));
        };
        reviewReports.onclick=()=>renderReports().catch(e=>{if(generation===detailGeneration)status.textContent=e.message;});
        projects.append(reviewReports,reports);
        const sourceChecks=button('Check selected source state','secondary');sourceChecks.id='owgPipelineSourceChecks';
        const checkHost=el('div');checkHost.id='owgPipelineSourceCheckForm';let sourceView=0;
        sourceChecks.onclick=async()=>{
          const view=++sourceView;
          try{
            const saved=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects')).json();
            if(generation!==detailGeneration||view!==sourceView)return;
            const selected=select('owgPipelineSourceProject',saved.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at]));
            const expectation=select('owgPipelineSourceExpectation',[]);
            const sourceRef=input('owgPipelineSourceRecord','text','owg:r:…');sourceRef.maxLength=38;sourceRef.autocomplete='off';sourceRef.spellcheck=false;
            const confirmed=input('owgPipelineSourceConsent','checkbox');const consentText=el('label');consentText.append(confirmed,document.createTextNode(' Read this exact observed record and download the minimized source-check document. Existing provider opt-ins still apply.'));
            const run=button('Read source and download check','secondary');run.id='owgPipelineSourceExport';run.disabled=true;
            const note=el('div',null,'muted');note.setAttribute('role','status');const history=el('div');
            let entries=[],formVersion=0,optionsVersion=0,pending=false;
            const ready=()=>{run.disabled=pending||!entries.length||!confirmed.checked||!/^owg:r:[0-9a-f]{32}$/.test(sourceRef.value.trim());confirmed.disabled=pending||!entries.length;};
            const invalidate=()=>{formVersion++;confirmed.checked=false;ready();};
            sourceRef.oninput=invalidate;expectation.onchange=invalidate;confirmed.onchange=ready;
            const base=()=>'/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects/'+encodeURIComponent(selected.value);
            const loadOptions=async()=>{
              const version=++optionsVersion;invalidate();entries=[];expectation.replaceChildren();ready();
              if(!selected.value){note.textContent='Export a linked project before checking source state.';return;}
              const path=base();
              const results=await Promise.allSettled([api(path+'/source-check-options').then(r=>r.json()),api(path+'/source-check-exports').then(r=>r.json())]);
              if(generation!==detailGeneration||view!==sourceView||version!==optionsVersion)return;
              const options=results[0],records=results[1];
              if(options.status==='fulfilled'&&options.value.current_idea_revision===idea.revision){
                entries=options.value.expectations;
                entries.forEach((e,i)=>{const opt=el('option',e.activity_label+' · '+e.operation.replaceAll('_',' '));opt.value=String(i);expectation.appendChild(opt);});
                note.textContent=entries.length?'Select the exact observed record. Source state does not prove agent causality, run/case identity or customer outcomes.':'This saved process has no confirmed source-state expectation. Review one in a new saved revision before creating its idea/project.';
              }else note.textContent=options.status==='rejected'?options.reason.message:'The idea changed; reopen it before checking a source.';
              if(records.status==='fulfilled'){
                const list=el('ul');records.value.exports.forEach(r=>list.appendChild(el('li','Check document exported '+r.created_at+' · hash '+r.document_sha256.slice(0,12)+'…')));
                history.replaceChildren(el('div','Only export hashes and times are retained here; receipt bodies and observed-record references are not stored.','muted'),records.value.exports.length?list:el('div','No source-check exports recorded.','muted'));
              }
              ready();
            };
            selected.onchange=()=>{sourceRef.value='';loadOptions().catch(e=>{if(generation===detailGeneration)note.textContent=e.message;});};
            run.onclick=async()=>{
              if(run.disabled)return;
              const entry=entries[Number(expectation.value)],path=base(),version=formVersion;
              const payload={expected_revision:idea.revision,terminal_activity_id:entry.terminal_activity_id,expected_operation:entry.operation,
                resource_ref:sourceRef.value.trim(),source_read_and_export_confirmed:confirmed.checked};
              confirmed.checked=false;pending=true;ready();
              try{
                const response=await api(path+'/source-check-export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const blob=await response.blob();
                if(generation!==detailGeneration||view!==sourceView||version!==formVersion)return;
                const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='owg-project-source-check.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
                sourceRef.value='';await loadOptions();note.textContent='Source-check document downloaded. Unknown results remain unknown; no agent causality or business outcome is established.';
              }catch(e){if(generation===detailGeneration&&view===sourceView&&version===formVersion)note.textContent=e.message;}
              finally{pending=false;ready();}
            };
            checkHost.replaceChildren(el('h4','Selected source-state check'),el('div','This performs an explicitly authorized read through existing source adapters. The receipt is downloaded only; OWG retains a checksum, not the receipt or a verified-state value.','muted'),
              field('Exported project for source check',selected),field('Confirmed source-state expectation',expectation),field('Observed record reference (owg:r:…)',sourceRef),consentText,run,note,history);
            await loadOptions();
          }catch(e){if(generation===detailGeneration&&view===sourceView)status.textContent=e.message;}
        };
        projects.append(sourceChecks,checkHost);box.appendChild(projects);
        refreshProjects().catch(e=>{if(generation===detailGeneration)message.textContent=e.message;});
      }
      if(idea.score)box.appendChild(el('div','Ease '+idea.score.ease_score+'/100 · '+idea.score.modeled_hours_per_month+' modeled hours per month · '+QUADRANTS[idea.score.quadrant]+(idea.assessment.compliance_review_needed?' · needs compliance review':''),'muted'));
      if(!['implemented','rejected'].includes(idea.state)){
        const answers={};const qrow=el('div',null,'row');qrow.style.alignItems='end';
        QUESTIONS.forEach(([key,label])=>{const s=select('owgPipelineQ_'+key,[['','—'],['1','1 · not at all'],['2','2'],['3','3'],['4','4'],['5','5 · fully']]);if(idea.assessment)s.value=String(idea.assessment.ease[key]);answers[key]=s;qrow.appendChild(field(label,s));});
        const cases=input('owgPipelineCases','number');cases.min='0';const minutes=input('owgPipelineMinutes','number');minutes.min='0';const share=input('owgPipelineShare','number');share.min='0';share.max='100';
        const compliance=input('owgPipelineCompliance','checkbox');const complianceLabel=el('label');complianceLabel.style.cssText='display:flex;gap:6px;align-items:center';complianceLabel.append(compliance,document.createTextNode(' Needs compliance review'));
        if(idea.assessment){cases.value=idea.assessment.cases_per_month;minutes.value=idea.assessment.minutes_per_case;share.value=Math.round(idea.assessment.eligible_share*100);compliance.checked=idea.assessment.compliance_review_needed;}
        const save=button('Save assessment','secondary');save.id='owgPipelineAssess';
        const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Cases per month (your estimate)',cases),field('Minutes per case',minutes),field('Eligible share (%)',share),complianceLabel,save);
        save.onclick=async()=>{try{
          const values={cases:cases.value,minutes:minutes.value,share:share.value,compliance:compliance.checked};
          for(const [key] of QUESTIONS)values[key]=answers[key].value;
          const saved=await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/assess',assessmentBody(values,idea.revision));
          await refresh();await show(idea.idea_id);status.textContent=idea.state==='approved'&&saved.state==='assessing'?'Assessment saved. Approval cleared; review the new assessment.':'Assessment saved.';
        }catch(e){status.textContent=e.message;}};
        box.append(el('h4','Assessment'),qrow,arow);
        if(idea.state==='approved')box.appendChild(el('div','Saving an assessment clears this approval and returns the idea to Assessing for a fresh review.','muted'));
      }
      if(idea.allowed_transitions.length){
        const note=input('owgPipelineNote','text','Reason or note (optional)');note.maxLength=1000;
        const moves=el('div',null,'row');moves.style.alignItems='end';moves.appendChild(field('Note',note));
        idea.allowed_transitions.forEach(to=>{const b=button('Move to '+STATE_LABELS[to],'secondary');b.dataset.state=to;
          b.onclick=async()=>{try{await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/transition',{to,note:note.value,expected_revision:idea.revision});await refresh();await show(idea.idea_id);status.textContent='Moved to '+STATE_LABELS[to]+'.';}catch(e){status.textContent=e.message;}};
          moves.appendChild(b);});
        box.append(el('h4','Decision'),moves);
      }
      const history=el('ol');idea.history.forEach(h=>{
        const item=el('li',(h.action==='assessment_saved'?'Assessment saved · ':'')+STATE_LABELS[h.state]+' · '+h.at+(h.note?' · '+h.note:''));
        if(h.assessment){
          const details=el('details');details.appendChild(el('summary','Assessment at this decision'));
          const appendAssessment=(a,label)=>{
            details.appendChild(el('div',label+': '+a.cases_per_month+' cases/month × '+a.minutes_per_case+' minutes × '+Number((a.eligible_share*100).toFixed(4))+'% eligible.'));
            const answers=QUESTIONS.map(([key,text])=>text+': '+a.ease[key]+'/5').join(' · ');
            details.appendChild(el('div',answers,'muted'));
            details.appendChild(el('div','Compliance review needed: '+(a.compliance_review_needed?'Yes':'No')));
            if(a.notes)details.appendChild(el('div',a.notes));
          };
          appendAssessment(h.assessment,'Saved assessment');
          if(h.previous_assessment)appendAssessment(h.previous_assessment,'Previous assessment');
          item.appendChild(details);
        }
        history.appendChild(item);
      });
      const remove=button('Delete idea','secondary');remove.id='owgPipelineDelete';
      remove.onclick=async()=>{if(!window.confirm('Delete the idea "'+idea.title+'"? This cannot be undone.'))return;try{await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/delete',{method:'POST'});detail.replaceChildren();await refresh();status.textContent='Idea deleted.';}catch(e){status.textContent=e.message;}};
      box.append(el('h4','History'),history,remove);
      detail.replaceChildren(box);
    }

    window.addEventListener('owg-pipeline-open',event=>{
      const id=event.detail?.idea_id;if(typeof id!=='string')return;
      refresh().then(()=>show(id)).catch(e=>{status.textContent=e.message;});
    });

    add.onclick=async()=>{try{
      if(!title.value.trim())throw new Error('Name the idea.');
      const idea=await post('/v1/automation-pipeline',{title:title.value.trim(),description:description.value.trim(),source:{kind:source.value,reference:reference.value.trim()}});
      title.value='';description.value='';reference.value='';await refresh();await show(idea.idea_id);status.textContent='Idea added.';
    }catch(e){status.textContent=e.message;}};
    filter.onchange=()=>refresh().catch(e=>{status.textContent=e.message;});
    exportLink.onclick=async()=>{try{
      const blob=await (await api('/v1/automation-pipeline/export.csv'+(filter.value?'?state='+encodeURIComponent(filter.value):''))).blob();
      const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='automation-pipeline.csv';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){status.textContent=e.message;}};
    refresh().catch(e=>{status.textContent=e.message;});
  }

  window.__owgPipelineModel={assessmentBody};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
