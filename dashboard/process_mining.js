/* Local-only process mining: no default upload, retention or external service. */
(() => {
  'use strict';
  const eventBrowserGenerations=new WeakMap();
  const SVG_NS='http://www.w3.org/2000/svg';
  const MAX_UPLOAD=1024*1024;
  let report=null;
  let reference=null;
  let activeVariant=0;
  let activeActivity=null;
  let analyzedFile=null, retainedDataset=null, generation=0, inventoryGeneration=0;
  let driftGeneration=0,cohortGeneration=0,cohortComparisonGeneration=0,cohortBaseline=null,retentionGeneration=0;
  let mappingGeneration=0,mappingInspection=null,mappedFile=null,mappingRequired=false;
  let mappedParticipantLabels=false,analyzedParticipantLabels=false,mappedCaseLinks=false,analyzedCaseLinks=false,retainedLabelChoices={};
  let zendeskGeneration=0;
  let observedExpanded=false, observedSelection=null, observedTransitionSelection=null,caseBrowserGeneration=0;
  const make=(tag,text,className)=>{
    const el=document.createElement(tag);
    if(text!==undefined&&text!==null)el.textContent=String(text);
    if(className)el.className=className;
    return el;
  };
  const safe=(v)=>Array.isArray(v)?v:[];
  const seconds=(s)=>{
    const n=Number(s||0);
    if(n>=3600)return (n/3600).toFixed(1)+' h';
    if(n>=60)return (n/60).toFixed(1)+' min';
    return n.toFixed(1)+' s';
  };
  const svg=(tag,attrs)=>{
    const node=document.createElementNS(SVG_NS,tag);
    for(const [key,value] of Object.entries(attrs||{}))node.setAttribute(key,String(value));
    return node;
  };
  const replace=(host,...children)=>{host.replaceChildren(...children);};
  const button=(title,fn)=>{
    const b=make('button',title,'secondary');b.type='button';b.addEventListener('click',fn);return b;
  };
  function install(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgProcessCSV'))return;
    const card=make('section',null,'card');card.id='owgProcessCSV';
    card.appendChild(make('h2','Process mining · event logs'));
    card.appendChild(make('p','Import a small CSV log or open an explicitly selected source projection to inspect supplied paths, transitions, elapsed durations and repeated activities. Preview stays local; nothing is saved or sent to an AI.','muted'));
    const p=make('p','CSV header: case_id,activity,timestamp (optional source). Timestamps must include a UTC offset, e.g. 2026-10-20T10:00:00Z. Only use files you are authorized to analyze. Use column mapping below for optional participant/team labels. Case IDs are replaced with temporary labels; activity names are displayed as supplied.','muted');
    card.appendChild(p);
    const wrap=make('div',null,'owg-process-controls');
    const file=make('input');file.type='file';file.accept='.csv,text/csv';file.id='owgProcessFile';file.setAttribute('aria-label','Choose business event log CSV');
    const run=button('Analyze selected CSV',analyze);run.id='owgProcessAnalyze';run.disabled=true;
    file.addEventListener('change',()=>{
      if(window.owgProcessDesigner?.hasDraft?.() && !window.confirm(
        'Discard your unsaved proposed process before selecting a different CSV?')) {
        file.value='';
        run.disabled=true;
        return;
      }
      report=null;
      resetMapping();
      generation++;analyzedFile=null;retainedDataset=null;retentionState(true);
      run.disabled=!file.files?.length;
      clearReport();
    });
    wrap.append(file,run);card.appendChild(wrap);
    installMapping(card);
    window.owgProcessCaseJoin?.install?.(card);
    installZendesk(card);
    const referenceLabel=make('p','No saved reference selected.','muted');referenceLabel.id='owgProcessReference';card.appendChild(referenceLabel);
    card.appendChild(button('Clear reference model',()=>setReference(null)));
    const status=make('p','No CSV selected. This feature is opt-in and does not change desktop recording.','muted');
    status.id='owgProcessStatus';status.setAttribute('role','status');card.appendChild(status);
    const output=make('div');output.id='owgProcessResults';card.appendChild(output);
    const retention=make('details');retention.appendChild(make('summary','Retain or reopen a local event-log snapshot'));
    retention.appendChild(make('p','Saving is separate from preview. It retains structural activities, timestamps, case ordering and any explicitly selected participant/team labels in this local OWG database. Native case IDs and source values are discarded. No AI access or synchronization is enabled. Snapshots expire after the selected period; you can delete them here.','muted'));
    const consentLabel=make('label');const consent=make('input');consent.type='checkbox';consent.id='owgLogConsent';
    consent.addEventListener('change',()=>{retentionGeneration++;retentionState();});
    consentLabel.append(consent,make('span',' I authorize retaining this selected event log locally.'));
    const days=make('select');days.id='owgLogDays';days.setAttribute('aria-label','Local event-log retention period');
    for(const value of [1,7,30]){const option=make('option',value+' days');option.value=String(value);days.appendChild(option);}
    const save=button('Save analyzed log locally',saveLog);save.id='owgLogSave';save.disabled=true;
    retention.append(consentLabel,days,save,button('List saved local logs',listLogs));
    const inventory=make('div');inventory.id='owgLogInventory';retention.appendChild(inventory);
    installCohort(retention);
    installDrift(retention);
    const cases=make('div');cases.id='owgLogCases';retention.appendChild(cases);card.appendChild(retention);
    const style=make('style');style.textContent=[
      '#owgProcessCSV .owg-process-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0}',
      '#owgCSVMapping .owg-process-controls label{display:grid;grid-template-columns:minmax(0,1fr);min-width:0;max-width:100%;flex:1 1 180px}',
      '#owgCSVMapping select{width:100%;min-width:0;max-width:100%;box-sizing:border-box}',
      '#owgLogDrift select{max-width:100%;width:100%}',
      '#owgLogDrift .owg-drift-controls{display:grid;gap:10px;margin:12px 0}',
      '#owgProcessCSV .owg-pmetrics{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}',
      '#owgProcessCSV .owg-pmetric{border:1px solid #d9dfd4;border-radius:8px;padding:10px;min-width:130px;flex:1}',
      '#owgProcessCSV .owg-pmetric strong{display:block;font-size:21px}',
      '#owgProcessCSV .owg-variants{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}',
      '#owgProcessCSV .owg-variants button[aria-pressed=true]{outline:2px solid #3b7462}',
      '#owgProcessCSV .owg-process-map{overflow:auto;max-width:100%;border:1px solid #d9dfd4;border-radius:8px;margin:8px 0}',
      '#owgProcessCSV .owg-process-map svg{display:block;max-height:110px}',
      '#owgProcessCSV .owg-process-table{width:100%;overflow:auto}',
      '#owgProcessCSV .owg-process-table table{min-width:300px;width:100%}'
    ].join('\n');
    card.prepend(style);
    const browse=panel.querySelector('#savedDayCard');
    if(browse)browse.insertAdjacentElement('afterend',card);else panel.appendChild(card);
  }
  function clearReport(preserveDraft=false){
    observedExpanded=false;observedSelection=null;observedTransitionSelection=null;caseBrowserGeneration++;
    const host=document.getElementById('owgProcessResults');if(host)host.replaceChildren();
    document.getElementById('owgLogCases')?.replaceChildren();
    if(!preserveDraft){window.owgProcessDesigner?.reset?.();window.owgOpportunityReview?.reset?.();}
  }
  function setStatus(message){
    const s=document.getElementById('owgProcessStatus');if(s)s.textContent=String(message||'');
  }
  async function analyze(){
    const file=mappedFile||document.getElementById('owgProcessFile')?.files?.[0];
    const control=document.getElementById('owgProcessAnalyze');
    if(!file)return;
    if(mappingRequired&&!mappedFile){setStatus('Review and apply the selected columns before analyzing this file.');return;}
    if(file.size>MAX_UPLOAD){setStatus('File exceeds the 1 MiB upload limit.');return;}
    if(!mappedFile){
      const checkedGeneration=generation;
      try{
        if(!window.owgProcessCSVMapping?.inspectFile)throw new Error('Column inspection unavailable. Reopen the local dashboard.');
        const inspected=await window.owgProcessCSVMapping.inspectFile(file,',');
        if(checkedGeneration!==generation)return;
        const names=inspected.headers.map(value=>value.trim().toLowerCase()),allowed=['case_id','activity','timestamp','source'];
        if(new Set(names).size!==names.length||!['case_id','activity','timestamp'].every(key=>names.includes(key))||names.some(key=>!allowed.includes(key))){
          setStatus('Use column mapping before uploading this export. Only the standard structural columns can be analyzed directly; participant/team and other extra columns require reviewed mapping.');return;
        }
      }catch(error){if(checkedGeneration===generation)setStatus('Unable to inspect selected CSV: '+error.message);return;}
    }
    const participantLabelsAuthorized=!!(mappedFile&&mappedParticipantLabels),caseLinksAuthorized=!!(mappedFile&&mappedCaseLinks);
    const attempt=++generation;analyzedFile=null;analyzedParticipantLabels=false;analyzedCaseLinks=false;retainedDataset=null;retentionState(true);
    report=null;clearReport();control.disabled=true;
    const mappedAnalyze=document.getElementById('owgCSVMapAnalyze');if(mappedAnalyze)mappedAnalyze.disabled=true;
    setStatus('Analyzing selected CSV on this local OWG service…');
    try{
      await window.__owgAuthReady;
      if(attempt!==generation)return;
      const response=await fetch('/v1/process-mining/preview'+(reference?'?'+new URLSearchParams(reference):''),{
        method:'POST',headers:{'Content-Type':'text/csv',...(participantLabelsAuthorized?{'X-OWG-Participant-Labels-Authorized':'true'}:{}),...(caseLinksAuthorized?{'X-OWG-Shared-Case-Keys-Reviewed':'true'}:{})},body:file,cache:'no-store'
      });
      if(!response.ok){
        const error=await response.json().catch(()=>({}));
        throw new Error(String(error?.detail||('Local import failed ('+response.status+')')));
      }
      const result=await response.json();
      if(attempt!==generation)return;
      if(result.format!=='openworkgraph.process-mining-csv-preview.v1')
        throw new Error('Unexpected process-mining response.');
      report=result;analyzedFile=file;analyzedParticipantLabels=participantLabelsAuthorized;analyzedCaseLinks=caseLinksAuthorized;activeVariant=0;activeActivity=null;render();retentionState();
      setStatus('Local analysis complete. Case labels are temporary; nothing was stored in OWG. Refresh or leave this page to discard this preview.');
    }catch(error){
      if(attempt===generation)setStatus('Unable to analyze: '+String(error?.message||'Unknown error'));
    }finally{if(attempt===generation){control.disabled=!!(mappingRequired&&!mappedFile);
      const mappedAnalyze=document.getElementById('owgCSVMapAnalyze');if(mappedAnalyze)mappedAnalyze.disabled=!mappedFile;}}
  }
  function installMapping(parent){
    const section=make('details');section.id='owgCSVMapping';section.appendChild(make('summary','Map columns from another event-log export'));
    section.appendChild(make('p','Inspect headers on this device, then choose the case, activity and timestamp columns. Applying the mapping creates a minimized CSV in browser memory. Those three columns are sent to local OWG. Optional participant/team columns can be selected separately; native identities in them are replaced with import-scoped labels before upload. Other columns are excluded. Activity labels and timestamps must still meet the documented format. For a file already using the OWG schema, skip this step.','muted'));
    const delimiter=make('select');delimiter.id='owgCSVDelimiter';delimiter.setAttribute('aria-label','Event-log delimiter');
    for(const [value,label] of [[',','Comma'],[';','Semicolon'],['\t','Tab']]){const option=make('option',label);option.value=value;delimiter.appendChild(option);}
    delimiter.value=',';delimiter.addEventListener('change',()=>{
      invalidateMappedPreview();resetMapping();mappingRequired=true;document.getElementById('owgProcessAnalyze').disabled=true;
    });
    const inspect=button('Inspect columns on this device',inspectColumns);inspect.id='owgCSVInspect';
    const fields=make('div');fields.id='owgCSVMappingFields';
    const status=make('p','No column mapping applied.','muted');status.id='owgCSVMappingStatus';status.setAttribute('role','status');
    section.append(delimiter,inspect,status,fields);parent.appendChild(section);
  }
  function installZendesk(parent){
    const section=make('details');section.id='owgZendeskProcess';section.appendChild(make('summary','Read selected Zendesk ticket status audits'));
    section.appendChild(make('p','Requires an administrator-configured, opted-in Zendesk OAuth connector. Only the ticket IDs you choose are read. OWG requests Create/Change audit event types, verifies each audit belongs to its selected ticket, and analyzes standard status history. Provider responses may include other metadata; comments, actors, custom fields and credentials are discarded and never included in the process report. No ticket inventory scan, writes or retained import.','muted'));
    const label=make('label','Selected Zendesk ticket IDs (comma separated)'),input=make('input');input.id='owgZendeskTicketIDs';input.type='text';input.maxLength=160;input.setAttribute('aria-label','Selected Zendesk ticket IDs (comma separated)');label.appendChild(input);
    const consentLabel=make('label'),consent=make('input');consent.id='owgZendeskConsent';consent.type='checkbox';
    consentLabel.append(consent,make('span',' I authorize reading the selected ticket audits from the configured Zendesk account.'));
    const run=button('Read selected Zendesk audits',readZendesk);run.id='owgZendeskRead';run.disabled=true;
    const status=make('p','No Zendesk source reads requested.','muted');status.id='owgZendeskStatus';status.setAttribute('role','status');
    const output=make('div');output.id='owgZendeskResults';
    const changed=()=>{zendeskGeneration++;consent.checked=false;run.disabled=true;output.replaceChildren();status.textContent='Selection changed. Review and confirm these source reads.';};
    input.addEventListener('input',changed);
    consent.addEventListener('change',()=>{zendeskGeneration++;output.replaceChildren();run.disabled=!consent.checked||!input.value.trim();});
    section.append(label,consentLabel,run,status,output);parent.appendChild(section);
  }
  async function readZendesk(){
    const input=document.getElementById('owgZendeskTicketIDs'),consent=document.getElementById('owgZendeskConsent');
    if(!consent.checked)return;
    const ids=input.value.split(',').map(value=>value.trim()),status=document.getElementById('owgZendeskStatus');
    if(!ids.length||ids.length>8||new Set(ids).size!==ids.length||ids.some(value=>! /^[1-9][0-9]{0,17}$/.test(value))){status.textContent='Choose 1 to 8 distinct numeric ticket IDs.';return;}
    const attempt=++zendeskGeneration,control=document.getElementById('owgZendeskRead'),host=document.getElementById('owgZendeskResults');control.disabled=true;
    host.replaceChildren();status.textContent='Reading bounded status audits for only the selected tickets…';
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/process-mining/zendesk-selected-cases',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ticket_ids:ids,confirm_source_reads:true}),cache:'no-store'});
      const value=await response.json().catch(()=>({}));if(attempt!==zendeskGeneration)return;
      if(!response.ok)throw new Error(String(value.detail||'Selected Zendesk request failed'));
      if(value.format!=='openworkgraph.zendesk-selected-cases-process.v1')throw new Error('Unexpected Zendesk projection response');
      for(const item of safe(value.selections))host.appendChild(make('p','Selection '+item.selection_number+' → '+(item.case_label||'no projected case')+
        ' · '+item.projected_event_count+' status events · '+String(item.availability).replaceAll('_',' ')+
        (item.provider_pagination_complete?' · source audit pages exhausted':' · source audit coverage incomplete or unknown')));
      const quality=value.projection_diagnostics||{};
      if(value.projection_diagnostics)host.appendChild(make('p','Projection diagnostics: '+Number(quality.invalid_audit_count||0)+' invalid/unsupported status audits, '+
        Number(quality.ambiguous_status_audit_count||0)+' ambiguous status audits, '+Number(quality.conflicting_audit_count||0)+' conflicting audit IDs omitted; '+
        Number(quality.duplicate_audit_count||0)+' repeated audit IDs encountered. Distinct audits with equal timestamps remain separate.','muted'));
      if(!value.process){status.textContent='Zendesk process projection unavailable: '+String(value.availability).replaceAll('_',' ')+'. No process was saved.';return;}
      status.textContent='Projected '+value.projected_ticket_count+' of '+value.requested_ticket_count+' selected tickets. Status history is partial workflow evidence; no outcomes or business rules were verified.';
      host.appendChild(button('Explore selected Zendesk status history',()=>{
        if(attempt!==zendeskGeneration)return;showSourcePreview(value.process);
      }));
    }catch(error){if(attempt===zendeskGeneration)status.textContent='Unable to read selected Zendesk audits: '+error.message;}
    finally{if(attempt===zendeskGeneration)control.disabled=!consent.checked;}
  }
  function resetMapping(preserveSharedCases=false){
    if(!preserveSharedCases)window.owgProcessCaseJoin?.reset?.();
    mappingGeneration++;mappingInspection=null;mappedFile=null;mappedParticipantLabels=false;mappedCaseLinks=false;analyzedCaseLinks=false;mappingRequired=false;
    document.getElementById('owgCSVMappingFields')?.replaceChildren();
    const status=document.getElementById('owgCSVMappingStatus');if(status)status.textContent='No column mapping applied.';
    const inspect=document.getElementById('owgCSVInspect');if(inspect)inspect.disabled=false;
  }
  function invalidateMappedPreview(){
    document.getElementById('owgCSVLocalLookup')?.remove();
    generation++;mappedFile=null;mappedParticipantLabels=false;mappedCaseLinks=false;analyzedFile=null;analyzedParticipantLabels=false;analyzedCaseLinks=false;
    report=null;retainedDataset=null;clearReport(true);retentionState(true);mappingRequired=true;
    for(const id of ['owgCSVMapUse','owgProcessAnalyze','owgCSVMapAnalyze']){const control=document.getElementById(id);if(control)control.disabled=true;}
  }
  async function inspectColumns(){
    const file=document.getElementById('owgProcessFile')?.files?.[0];
    if(!file){document.getElementById('owgCSVMappingStatus').textContent='Choose a CSV event log first.';return;}
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before mapping this event log?'))return;
    generation++;report=null;analyzedFile=null;retainedDataset=null;clearReport();retentionState(true);
    resetMapping();mappingRequired=true;const attempt=mappingGeneration;
    document.getElementById('owgProcessAnalyze').disabled=true;
    const control=document.getElementById('owgCSVInspect'),status=document.getElementById('owgCSVMappingStatus');control.disabled=true;
    status.textContent='Reading CSV headers and structure on this device…';
    try{
      if(!window.owgProcessCSVMapping?.inspectFile)throw new Error('Column mapper unavailable. Reopen the local dashboard.');
      const value=await window.owgProcessCSVMapping.inspectFile(file,document.getElementById('owgCSVDelimiter').value);
      if(attempt!==mappingGeneration)return;
      mappingInspection=value;
      const fields=document.getElementById('owgCSVMappingFields'),controls=make('div',null,'owg-process-controls');fields.replaceChildren();
      for(const [key,title] of [['case_id','Case identifier column'],['activity','Activity label column'],['timestamp','Event timestamp column'],['participant','Participant identifier column (optional)'],['team','Team identifier column (optional)']]){
        const label=make('label',title),select=make('select');select.id='owgCSVMap_'+key;select.setAttribute('aria-label',title);
        const empty=make('option',['participant','team'].includes(key)?'Exclude this column':'Choose column');empty.value='';select.appendChild(empty);
        value.headers.forEach((header,index)=>{const option=make('option',(index+1)+'. '+String(header).replace(/[\u0000-\u001f\u007f-\u009f]/g,' ').slice(0,80));option.value=String(index);select.appendChild(option);});
        const matches=value.headers.map((header,index)=>({header,index})).filter(item=>item.header.trim().toLowerCase()===key);
        select.value=!['participant','team'].includes(key)&&matches.length===1?String(matches[0].index):'';
        select.addEventListener('change',()=>{
          invalidateMappedPreview();
          document.getElementById('owgCSVMapConsent').checked=false;
          const selected=['participant','team'].some(key=>document.getElementById('owgCSVMap_'+key)?.value);
          consentText.textContent=selected?' I reviewed these columns and authorize local analysis of participant/team labels. Replace identifiers in these columns before upload and exclude other columns.':' I reviewed these three columns; exclude every other column from local analysis.';
          document.getElementById('owgCSVMapUse').disabled=true;document.getElementById('owgProcessAnalyze').disabled=true;
          document.getElementById('owgCSVMapAnalyze').disabled=true;
          status.textContent='Column choices changed. Confirm and apply them before the next analysis.';
        });label.appendChild(select);controls.appendChild(label);
      }
      const consentLabel=make('label'),consent=make('input');consent.type='checkbox';consent.id='owgCSVMapConsent';
      const consentText=make('span',' I reviewed these three columns; exclude every other column from local analysis.');consentLabel.append(consent,consentText);
      const use=button('Use mapped columns for next analysis',useMappedColumns);use.id='owgCSVMapUse';use.disabled=true;
      const analyzeMapped=button('Analyze mapped log',analyze);analyzeMapped.id='owgCSVMapAnalyze';analyzeMapped.disabled=true;
      consent.addEventListener('change',()=>{if(!consent.checked&&mappedParticipantLabels)invalidateMappedPreview();use.disabled=!consent.checked;});
      fields.append(controls,consentLabel,use,analyzeMapped);
      status.textContent='Inspected '+value.row_count+' event rows and '+value.headers.length+' columns on this device. No file content or header was sent for inspection.';
    }catch(error){if(attempt===mappingGeneration)status.textContent='Unable to inspect columns: '+error.message;}
    finally{if(attempt===mappingGeneration)control.disabled=false;}
  }
  function useMappedColumns(){
    if(!mappingInspection||!document.getElementById('owgCSVMapConsent')?.checked)return;
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before applying these columns?'))return;
    try{
      const mapping={};for(const key of ['case_id','activity','timestamp']){
        const value=document.getElementById('owgCSVMap_'+key).value;
        if(!/^[0-9]{1,3}$/.test(value))throw new Error('Choose each of the three columns.');mapping[key]=Number(value);
      }
      for(const key of ['participant','team']){const value=document.getElementById('owgCSVMap_'+key).value;
        if(value!==''){if(!/^[0-9]{1,3}$/.test(value))throw new Error('Choose existing optional columns.');mapping[key]=Number(value);}}
      const labelsAuthorized=['participant','team'].some(key=>Object.hasOwn(mapping,key));
      const file=mappingInspection.createMappedFile(mapping,labelsAuthorized);

      generation++;mappedFile=file;mappedParticipantLabels=labelsAuthorized;mappedCaseLinks=false;report=null;analyzedFile=null;retainedDataset=null;clearReport();retentionState(true);
      document.getElementById('owgCSVLocalLookup')?.remove();
      if(labelsAuthorized){
        const lookup=make('div');lookup.id='owgCSVLocalLookup';
        lookup.appendChild(make('p','Optional lookup on this device: identify import labels using the selected file. Names are kept in browser memory and excluded from uploads and saved snapshots. This lookup clears when the mapping changes or the page reloads.','muted'));
        const values=make('div');let visible=false;
        const show=button('Show local participant/team lookup',()=>{
          visible=!visible;values.replaceChildren();show.textContent=visible?'Hide local participant/team lookup':'Show local participant/team lookup';
          if(!visible)return;
          const list=make('ul');for(const rows of Object.values(file.localParticipantLookup||{})){
            for(const row of rows)list.appendChild(make('li',aliasLabel(row.label)+' · '+row.source));
          }values.appendChild(list);
        });lookup.append(show,values);document.getElementById('owgCSVMappingFields').appendChild(lookup);
      }
      document.getElementById('owgProcessAnalyze').disabled=false;
      document.getElementById('owgCSVMapAnalyze').disabled=false;
      setStatus('Mapped log is ready. Select Analyze mapped log to analyze the reviewed columns'+(labelsAuthorized?' and import-scoped participant/team labels.':'.'));
      document.getElementById('owgCSVMappingStatus').textContent='Mapped '+mappingInspection.row_count+' event rows. '+(labelsAuthorized?'Participant/team identities were replaced in browser memory with labels scoped to this import. ':'Only case_id, activity and timestamp were included. ')+'Select Analyze mapped log to send the minimized log to local OWG.';
    }catch(error){document.getElementById('owgCSVMappingStatus').textContent='Unable to apply mapping: '+error.message;}
  }
  function retentionState(reset=false){
    const consent=document.getElementById('owgLogConsent'),save=document.getElementById('owgLogSave');
    if(reset&&consent){retentionGeneration++;consent.checked=false;}
    if(save)save.disabled=!(report&&analyzedFile&&!retainedDataset&&consent?.checked);
    cohortControls(reset);
  }
  async function logRequest(path,options,isCurrent){
    await window.__owgAuthReady;
    if(isCurrent&&!isCurrent())return null;
    const response=await fetch('/v1/process-mining/datasets'+path,{...options,cache:'no-store'});
    const value=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(String(value.detail||'Local log request failed'));
    return value;
  }
  async function saveLog(){
    if(!report||!analyzedFile||retainedDataset||!document.getElementById('owgLogConsent')?.checked)return;
    const attempt=generation,saveAttempt=++retentionGeneration,file=analyzedFile,save=document.getElementById('owgLogSave');save.disabled=true;
    try{
      const value=await logRequest('?'+new URLSearchParams({retention_days:document.getElementById('owgLogDays').value}),{
        method:'POST',headers:{'Content-Type':'text/csv','X-OWG-Retention-Confirmed':'true',...(analyzedParticipantLabels?{'X-OWG-Participant-Labels-Authorized':'true'}:{}),...(analyzedCaseLinks?{'X-OWG-Shared-Case-Keys-Reviewed':'true'}:{})},body:file},
        ()=>attempt===generation&&saveAttempt===retentionGeneration&&file===analyzedFile&&document.getElementById('owgLogConsent')?.checked);
      if(!value||attempt!==generation||saveAttempt!==retentionGeneration)return;
      retainedDataset=value.dataset_id;report.import_retained=true;report.dataset_id=value.dataset_id;report.expires_at=value.expires_at;render();retentionState(true);
      setStatus('Saved a minimized local snapshot. Expires '+value.expires_at+'. Native case IDs and source values were discarded.');
      await listLogs();
    }catch(error){if(attempt===generation&&saveAttempt===retentionGeneration){retentionState();setStatus('Unable to save log: '+error.message);}}
  }
  async function listLogs(){
    const attempt=++inventoryGeneration,host=document.getElementById('owgLogInventory');
    invalidateDrift();
    try{
      const value=await logRequest('');if(attempt!==inventoryGeneration)return;
      driftChoices(safe(value.datasets));
      host.replaceChildren();
      if(!safe(value.datasets).length)host.appendChild(make('p','No retained local logs.'));
      for(const dataset of safe(value.datasets)){
        const row=make('div');row.append(make('p',dataset.dataset_id+' · '+dataset.case_count+' cases · '+dataset.event_count+' events · expires '+dataset.expires_at),
          button('Open '+dataset.dataset_id,()=>openLog(dataset.dataset_id)),
          button('Delete '+dataset.dataset_id,()=>deleteLog(dataset.dataset_id)));host.appendChild(row);
      }
    }catch(error){if(attempt===inventoryGeneration)setStatus('Unable to list logs: '+error.message);}
  }
  async function openLog(id){
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before opening this saved log?'))return;
    const attempt=++generation;resetMapping();analyzedFile=null;retainedDataset=null;report=null;clearReport();retentionState(true);
    document.getElementById('owgProcessFile').value='';document.getElementById('owgProcessAnalyze').disabled=true;
    try{
      const value=await logRequest('/report?'+new URLSearchParams({dataset_id:id,...(reference||{})}));if(attempt!==generation)return;
      if(value.format!=='openworkgraph.process-mining-csv-preview.v1')throw new Error('Unexpected saved-log response');
      report=value;retainedDataset=id;activeVariant=0;activeActivity=null;render();cohortControls(true);
      setStatus('Opened retained local snapshot. Expires '+value.expires_at+'. Diagram examples remain sampled; browse all cases below.');
      await browseCases(id,0,attempt);
    }catch(error){if(attempt===generation)setStatus('Unable to open log: '+error.message);}
  }
  async function browseCases(id,after=0,attempt=generation,activity=null,transition=null){
    const caseAttempt=++caseBrowserGeneration;
    try{
      const filters=report?.dataset_id===id?report.cohort?.filters||{}:{};
      const value=await logRequest('/cases?'+new URLSearchParams({dataset_id:id,after,limit:25,...filters,...(activity?{activity}:{}),...(transition?{transition_from:transition.from,transition_to:transition.to}:{})}),undefined,
        ()=>attempt===generation&&caseAttempt===caseBrowserGeneration);if(attempt!==generation||caseAttempt!==caseBrowserGeneration)return;
      const host=document.getElementById('owgLogCases');host.replaceChildren(make('h3',transition?'Retained cases containing adjacent '+transition.from+' → '+transition.to:activity?'Retained cases containing '+activity:report?.cohort?'Selected retained cases · dataset ordinals':'All retained cases · dataset ordinals'));
      for(const entry of safe(value.cases)){
        const row=make('details');row.appendChild(make('summary','Case '+entry.case_number+' · '+entry.event_count+' events · '+entry.variant_id+(entry.case_key_alias?' · '+entry.case_key_alias:'')));
        const events=make('div');row.appendChild(events);let loaded=false;
        row.addEventListener('toggle',()=>{if(row.open&&!loaded){loaded=true;browseEvents(id,entry.case_number,events,0,attempt,caseAttempt);}});host.appendChild(row);
      }
      if(after)host.appendChild(button('First cases',()=>browseCases(id,0,attempt,activity,transition)));
      if(value.has_more)host.appendChild(button('Next cases',()=>browseCases(id,value.next_after,attempt,activity,transition)));
    }catch(error){if(attempt===generation&&caseAttempt===caseBrowserGeneration)setStatus('Unable to browse cases: '+error.message);}
  }
  async function browseEvents(id,number,host,after,attempt,caseAttempt){
    const eventAttempt=(eventBrowserGenerations.get(host)||0)+1;eventBrowserGenerations.set(host,eventAttempt);
    const current=()=>attempt===generation&&caseAttempt===caseBrowserGeneration&&eventBrowserGenerations.get(host)===eventAttempt;
    try{
      const value=await logRequest('/events?'+new URLSearchParams({dataset_id:id,case_number:number,after,limit:100}),undefined,
        current);if(!current())return;
      host.replaceChildren();
      const list=make('ol');for(const event of safe(value.events)){
        const row=make('li',event.position+'. '+event.activity+' · '+event.observed_at+' · preceding gap '+seconds(event.elapsed_since_previous_seconds)+(eventLabelSuffix(event)?' · '+eventLabelSuffix(event):''));list.appendChild(row);
      }host.appendChild(list);
      if(after)host.appendChild(button('First events',()=>browseEvents(id,number,host,0,attempt,caseAttempt)));
      if(value.has_more)host.appendChild(button('Next events',()=>browseEvents(id,number,host,value.next_after,attempt,caseAttempt)));
    }catch(error){if(current())host.replaceChildren(make('p','Unable to read events: '+error.message));}
  }
  async function deleteLog(id){
    if(!window.confirm('Delete this retained local log, including all of its case timelines? Saved process designs and independent downloads remain separate.'))return;
    invalidateDrift();
    try{
      await logRequest('/forget?'+new URLSearchParams({dataset_id:id}),{method:'POST',headers:{'X-OWG-Deletion-Confirmed':'true'}});
      if(retainedDataset===id){generation++;report=null;analyzedFile=null;retainedDataset=null;clearReport();retentionState(true);document.getElementById('owgLogCases').replaceChildren();}
      await listLogs();setStatus('Deleted local event-log snapshot.');
    }catch(error){setStatus('Unable to delete log: '+error.message);}
  }
  const cohortInputs=[['owgCohortSince','Case first event from (inclusive, ISO timestamp)','started_since'],
    ['owgCohortBefore','Case first event before (exclusive, ISO timestamp)','started_before'],
    ['owgCohortActivity','Case contains exact activity','contains_activity'],
    ['owgCohortVariant','Retained variant ID (optional)','variant_id'],
    ['owgCohortParticipant','Cases with participant label','contains_participant','participant'],
    ['owgCohortTeam','Cases with team label','contains_team','team']];
  function installCohort(parent){
    const section=make('section');section.id='owgLogCohort';section.append(make('h3','Filter complete retained cases'),
      make('p','Open or save a retained log first. Date filters select cases by their first retained event; every event of a selected case is included. Use offset-aware timestamps such as 2026-10-20T00:00:00Z. These are log boundaries, not verified business start or completion. Filters apply only after Apply case filters. Participant/team filters match supplied event labels; when both are selected they must occur on the same event. Complete case timelines remain intact.','muted'));
    const controls=make('div',null,'owg-process-controls');
    for(const [id,title,,dimension] of cohortInputs){
      const label=make('label',title),input=make(dimension?'select':'input');input.id=id;if(!dimension){input.type='text';input.maxLength=64;}input.disabled=true;
      input.setAttribute('aria-label',title);
      input.addEventListener(dimension?'change':'input',()=>{
        cohortGeneration++;invalidateCohortComparison();
        document.getElementById('owgCohortStatus').textContent='Filter inputs changed. Apply to update the results.';
        document.getElementById('owgCohortApply').disabled=!retainedDataset;
      });label.appendChild(input);controls.appendChild(label);
    }
    const apply=button('Apply case filters',applyCohort);apply.id='owgCohortApply';apply.disabled=true;
    const clear=button('Clear case filters',()=>{if(retainedDataset)openLog(retainedDataset);});clear.id='owgCohortClear';clear.disabled=true;
    const status=make('p','Open a retained snapshot to filter complete cases.','muted');status.id='owgCohortStatus';status.setAttribute('role','status');
    controls.append(apply,clear);section.append(controls,status);
    const comparison=make('details');comparison.appendChild(make('summary','Compare two complete-case selections'));
    comparison.appendChild(make('p','Choose baseline filters above and keep them as a comparison baseline. Then change the filters for the current selection. Both populations come from this saved log; all events of each case are used, including events outside date filters. Cases can appear in both selections.','muted'));
    const keep=button('Keep filters as comparison baseline',()=>{
      if(!retainedDataset)return;
      cohortBaseline={dataset_id:retainedDataset,filters:readCohortFilters()};invalidateCohortComparison();
      document.getElementById('owgCohortBaseline').textContent='Baseline filters: '+JSON.stringify(cohortBaseline.filters)+'. Empty filters include every case.';
    });keep.id='owgCohortKeep';keep.disabled=true;
    const baseline=make('p','No baseline selected.','muted');baseline.id='owgCohortBaseline';
    const label=make('label'),consent=make('input');consent.type='checkbox';consent.id='owgCohortCompareConsent';
    label.append(consent,make('span',' I reviewed these baseline and current case selections for comparison.'));
    consent.addEventListener('change',()=>invalidateCohortComparison(false));
    const compare=button('Compare case selections',compareCohorts);compare.id='owgCohortCompare';compare.disabled=true;
    const resultStatus=make('p','No comparison requested.','muted');resultStatus.id='owgCohortCompareStatus';resultStatus.setAttribute('role','status');
    const output=make('div');output.id='owgCohortCompareResults';
    comparison.append(keep,baseline,label,compare,resultStatus,output);section.appendChild(comparison);parent.appendChild(section);
  }
  function cohortControls(reset=false){
    if(reset){
      cohortGeneration++;cohortBaseline=null;invalidateCohortComparison();
      const text=document.getElementById('owgCohortBaseline');if(text)text.textContent='No baseline selected.';
    }
    if(!retainedDataset)retainedLabelChoices={};
    else if(report&&!report.cohort)retainedLabelChoices=report.participant_labels?.dimensions_summary||{};
    for(const [id,,,dimension] of cohortInputs){const input=document.getElementById(id);if(input){
      if(dimension){const previous=reset?'':input.value,labels=safe(retainedLabelChoices[dimension]?.labels);
        const empty=make('option','Any '+dimension+' label');empty.value='';input.replaceChildren(empty);
        labels.forEach(row=>{const option=make('option',aliasLabel(row.label));option.value=row.label;input.appendChild(option);});
        input.value=labels.some(row=>row.label===previous)?previous:'';input.disabled=!retainedDataset||!labels.length;
      }else{input.disabled=!retainedDataset;if(reset)input.value='';}
    }}
    for(const id of ['owgCohortApply','owgCohortClear','owgCohortKeep']){const control=document.getElementById(id);if(control)control.disabled=!retainedDataset;}
    if(reset){const status=document.getElementById('owgCohortStatus');if(status)status.textContent=retainedDataset?'Full retained log selected. Case filters are optional.':'Open a retained snapshot to filter complete cases.';}
  }
  function readCohortFilters(){
    const filters={};
    for(const [element,,key] of cohortInputs){const value=document.getElementById(element).value.trim();if(value)filters[key]=value;}
    return filters;
  }
  function cohortComparisonEnabled(){
    return !!(retainedDataset&&cohortBaseline?.dataset_id===retainedDataset&&document.getElementById('owgCohortCompareConsent')?.checked);
  }
  function invalidateCohortComparison(resetConsent=true){
    cohortComparisonGeneration++;
    if(resetConsent){const consent=document.getElementById('owgCohortCompareConsent');if(consent)consent.checked=false;}
    document.getElementById('owgCohortCompareResults')?.replaceChildren();
    const status=document.getElementById('owgCohortCompareStatus');if(status)status.textContent='No comparison requested for these selections.';
    const control=document.getElementById('owgCohortCompare');if(control)control.disabled=!cohortComparisonEnabled();
  }
  async function compareCohorts(){
    if(!cohortComparisonEnabled())return;
    const attempt=++cohortComparisonGeneration,selectedGeneration=generation;
    const control=document.getElementById('owgCohortCompare'),status=document.getElementById('owgCohortCompareStatus');control.disabled=true;
    const body={dataset_id:retainedDataset,baseline_filters:{...cohortBaseline.filters},current_filters:readCohortFilters(),
      comparability_confirmed:true,...(reference?{workflow_id:reference.workflow_id,revision:Number(reference.revision)}:{})};
    document.getElementById('owgCohortCompareResults').replaceChildren();status.textContent='Comparing complete retained case populations…';
    try{
      const value=await logRequest('/cohorts/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},
        ()=>attempt===cohortComparisonGeneration&&selectedGeneration===generation&&cohortComparisonEnabled());
      if(attempt!==cohortComparisonGeneration||selectedGeneration!==generation)return;
      if(value.format!=='openworkgraph.process-cohort-comparison.v1')throw new Error('Unexpected complete-case comparison response');
      renderDrift(value,'owgCohortCompareResults');status.textContent='Case comparison complete. '+value.cohort_overlap.shared_case_count+' retained cases occur in both selections. No saved process was changed.';
    }catch(error){if(attempt===cohortComparisonGeneration&&selectedGeneration===generation)status.textContent='Unable to compare case selections: '+error.message;}
    finally{if(attempt===cohortComparisonGeneration&&selectedGeneration===generation)control.disabled=!cohortComparisonEnabled();}
  }
  function inspectComparedCohort(filters){
    for(const [id,,key] of cohortInputs)document.getElementById(id).value=filters[key]||'';
    invalidateCohortComparison();applyCohort();
  }
  async function applyCohort(){
    if(!retainedDataset)return;
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before filtering this retained log?'))return;
    const id=retainedDataset,attempt=++generation,cohortAttempt=++cohortGeneration;
    const query={dataset_id:id,...(reference||{}),...readCohortFilters()};
    invalidateCohortComparison();
    report=null;analyzedFile=null;clearReport();
    const control=document.getElementById('owgCohortApply'),status=document.getElementById('owgCohortStatus');control.disabled=true;
    status.textContent='Analyzing all events of matching retained cases…';
    try{
      const value=await logRequest('/cohort?'+new URLSearchParams(query));
      if(attempt!==generation||cohortAttempt!==cohortGeneration)return;
      if(value.format!=='openworkgraph.process-log-cohort.v1')throw new Error('Unexpected retained-case selection response');
      if(!value.report){status.textContent='No retained cases match these filters. No process model was changed.';return;}
      report=value.report;activeVariant=0;activeActivity=null;render();
      status.textContent='Selected '+value.selection.selected_case_count+' of '+value.selection.dataset_case_count+' retained cases, including all '+value.selection.selected_event_count+' events. No events were clipped to the date window.';
      await browseCases(id,0,attempt);
    }catch(error){if(attempt===generation&&cohortAttempt===cohortGeneration)status.textContent='Unable to filter retained cases: '+error.message;}
    finally{if(attempt===generation&&cohortAttempt===cohortGeneration)control.disabled=!retainedDataset;}
  }
  function installDrift(parent){
    const section=make('section');section.id='owgLogDrift';
    section.append(make('h3','Review changes between retained logs'),make('p',
      'Compare two complete local snapshots using literal activity labels and ordered paths. First list saved logs, then choose a baseline and a current log. No case identities are joined and no saved process is updated.','muted'));
    const controls=make('div',null,'owg-drift-controls');
    for(const [id,title] of [['owgDriftBaseline','Baseline event-log snapshot'],['owgDriftCurrent','Current event-log snapshot']]){
      const label=make('label',title),select=make('select');select.id=id;select.setAttribute('aria-label',title);
      const empty=make('option','List saved logs to choose a snapshot');empty.value='';select.appendChild(empty);
      select.addEventListener('change',()=>invalidateDrift());label.appendChild(select);controls.appendChild(label);
    }
    const label=make('label'),consent=make('input');consent.type='checkbox';consent.id='owgDriftConsent';
    label.append(consent,make('span',' These logs describe the same process with compatible activity labels.'));
    consent.addEventListener('change',()=>invalidateDrift(false));
    const compare=button('Compare retained logs',compareLogs);compare.id='owgDriftCompare';compare.disabled=true;
    const status=make('p','No comparison requested.','muted');status.id='owgDriftStatus';status.setAttribute('role','status');
    const output=make('div');output.id='owgDriftResults';
    controls.append(label,compare);section.append(controls,status,output);parent.appendChild(section);
  }
  function driftEnabled(){
    const a=document.getElementById('owgDriftBaseline')?.value,b=document.getElementById('owgDriftCurrent')?.value;
    return !!(a&&b&&a!==b&&document.getElementById('owgDriftConsent')?.checked);
  }
  function invalidateDrift(resetConsent=true){
    driftGeneration++;
    if(resetConsent){const consent=document.getElementById('owgDriftConsent');if(consent)consent.checked=false;}
    document.getElementById('owgDriftResults')?.replaceChildren();
    const status=document.getElementById('owgDriftStatus');if(status)status.textContent='No comparison requested for these selections.';
    const control=document.getElementById('owgDriftCompare');if(control)control.disabled=!driftEnabled();
  }
  function driftChoices(datasets){
    for(const id of ['owgDriftBaseline','owgDriftCurrent']){
      const select=document.getElementById(id),previous=select.value;select.replaceChildren();
      const empty=make('option','Choose a retained snapshot');empty.value='';select.appendChild(empty);
      for(const dataset of datasets){
        const option=make('option',dataset.dataset_id+' · '+dataset.case_count+' cases · saved '+dataset.created_at);
        option.value=dataset.dataset_id;select.appendChild(option);
      }
      select.value=datasets.some(dataset=>dataset.dataset_id===previous)?previous:'';
    }
    invalidateDrift();
  }
  async function compareLogs(){
    if(!driftEnabled())return;
    const attempt=++driftGeneration,control=document.getElementById('owgDriftCompare');control.disabled=true;
    const status=document.getElementById('owgDriftStatus');
    document.getElementById('owgDriftResults').replaceChildren();status.textContent='Comparing every retained case and full path…';
    try{
      const query={baseline_id:document.getElementById('owgDriftBaseline').value,
        current_id:document.getElementById('owgDriftCurrent').value,comparability_confirmed:true,...(reference||{})};
      const value=await logRequest('/compare?'+new URLSearchParams(query));if(attempt!==driftGeneration)return;
      if(value.format!=='openworkgraph.process-log-drift.v1')throw new Error('Unexpected log comparison response');
      renderDrift(value);status.textContent='Comparison complete. Observed changes require human review; no process model was updated.';
    }catch(error){if(attempt===driftGeneration)status.textContent='Unable to compare retained logs: '+error.message;}
    finally{if(attempt===driftGeneration)control.disabled=!driftEnabled();}
  }
  function renderDrift(value,hostId='owgDriftResults'){
    const cohort=!!value.cohort_overlap,host=document.getElementById(hostId);host.replaceChildren(make('h3',cohort?'Observed differences between case selections':'Observed log changes'));
    if(cohort){
      host.appendChild(make('p','Baseline filters: '+JSON.stringify(value.baseline.cohort_filters)+'; current filters: '+JSON.stringify(value.current.cohort_filters)+'. '+value.cohort_overlap.shared_case_count+' cases belong to both populations; '+value.cohort_overlap.baseline_only_case_count+' only to baseline; '+value.cohort_overlap.current_only_case_count+' only to current. Cases are identified by their unchanged ordinal in this one snapshot. These are not verified independent samples.','muted'));
    }
    host.appendChild(make('p','Baseline '+value.baseline.dataset_id+' ('+value.baseline.case_count+' cases) → current '+
      value.current.dataset_id+' ('+value.current.case_count+' cases). '+(cohort?'All events and full paths of the selected cases':'All retained events and full paths')+' contribute to the metrics.','muted'));
    const metrics=make('div',null,'owg-pmetrics'),s=value.summary;
    metrics.append(metric('Observed path-mix distance · 0 identical, 1 disjoint',s.path_mix_total_variation_distance),
      metric(cohort?'Paths seen only in current selection':'Paths seen only in current log',s.current_only_path_count),metric('Paths seen only in baseline',s.baseline_only_path_count),
      metric('Baseline → current median case elapsed',seconds(s.baseline_median_elapsed_seconds)+' → '+seconds(s.current_median_elapsed_seconds)),
      metric('Baseline → current p90 case elapsed',seconds(s.baseline_p90_case_elapsed_seconds)+' → '+seconds(s.current_p90_case_elapsed_seconds)));
    host.appendChild(metrics);
    host.appendChild(make('p','This is a descriptive comparison, not a significance test or a verified change in business behavior. Case counts and time windows can differ; mix changes may explain overall timing changes. Within-path medians below compare the same complete activity sequence. Elapsed time is not active labor or verified waiting.','muted'));
    const warningText={observed_event_windows_overlap:cohort?'Event windows overlap. Exact shared-case counts are shown above; event overlap does not establish employee effort or causal dependencies.':'Event windows overlap. Duplicate or overlapping cases cannot be ruled out because native case IDs were discarded.',
      fewer_than_twenty_cases_in_at_least_one_snapshot:cohort?'At least one selection has fewer than 20 cases; this advisory is not a significance threshold.':'At least one snapshot has fewer than 20 cases; this advisory is not a significance threshold.',
      timestamp_ties_have_unverified_order:'Some events share timestamps; their supplied row order is unverified.'};
    for(const code of safe(value.warnings)){if(warningText[code])host.appendChild(make('p',warningText[code],'muted'));}
    for(const [title,entry] of [['Baseline',value.baseline],['Current',value.current]]){
      host.appendChild(make('p',title+' event window: '+entry.event_window.first_event_at+' to '+entry.event_window.last_event_at+
        '. Snapshot expires '+entry.expires_at+'.','muted'));
    }
    if(value.conformance){
      const c=value.conformance,r=c.reference;
      host.append(make('h4','Same-revision structural comparison'),make('p','Process '+r.workflow_id+' revision '+r.revision+' · '+r.content_sha256),
        make('p','Exact structural matches: baseline '+c.baseline_exact_structural_match_cases+' / '+value.baseline.case_count+
        '; current '+c.current_exact_structural_match_cases+' / '+value.current.case_count+'. Match-share change '+
        signed(c.exact_match_share_change_percentage_points)+' percentage points. Business rules and conditions were not evaluated.'));
    }
    driftTable(host,'Path mix and within-path timing',[
      'Observed path · full-path fingerprint','Presence','Baseline cases','Current cases','Case-share change (pp)','Baseline median elapsed','Current median elapsed'],
      safe(value.paths).map(row=>[row.observed_path.join(' → ')+(row.path_truncated?' … ('+row.path_event_count+' events)':'')+' · '+row.path_sha256,
        row.presence,row.baseline_case_count,row.current_case_count,signed(row.case_share_change_percentage_points),
        optionalSeconds(row.baseline_median_elapsed_seconds),optionalSeconds(row.current_median_elapsed_seconds)]));
    if(value.coverage.path_rows_truncated)host.appendChild(make('p','Showing '+value.coverage.path_rows_returned+' of '+value.coverage.path_rows_total+' full paths, ranked by absolute case-share change. Summary metrics use every path.','muted'));
    driftTable(host,'Activity incidence', ['Activity','Baseline events','Current events','Baseline cases','Current cases','Case-share change (pp)'],
      safe(value.activities).map(row=>[row.activity,row.baseline_event_count,row.current_event_count,row.baseline_case_count,row.current_case_count,signed(row.case_share_change_percentage_points)]));
    driftTable(host,'Observed transitions and elapsed gaps',['From → to','Baseline transitions','Current transitions','Case-share change (pp)','Baseline median gap','Current median gap'],
      safe(value.transitions).map(row=>[row.from+' → '+row.to,row.baseline_transition_count,row.current_transition_count,
        signed(row.case_share_change_percentage_points),optionalSeconds(row.baseline_median_elapsed_seconds),optionalSeconds(row.current_median_elapsed_seconds)]));
    if(value.coverage.transition_rows_truncated)host.appendChild(make('p','Showing '+value.coverage.transition_rows_returned+' of '+value.coverage.transition_rows_total+' transition types.','muted'));
    if(cohort){
      host.append(button('Inspect baseline case selection',()=>inspectComparedCohort(value.baseline.cohort_filters)),
        button('Inspect current case selection',()=>inspectComparedCohort(value.current.cohort_filters)));return;
    }
    host.append(make('p','Inspect a snapshot to review its paths and supporting cases. Drafting and saving any improved process requires a separate explicit review. No business outcomes or execution authority are inferred.','muted'),
      button('Inspect baseline snapshot',()=>openLog(value.baseline.dataset_id)),
      button('Inspect current snapshot',()=>openLog(value.current.dataset_id)));
  }
  const signed=value=>(Number(value)>0?'+':'')+String(value);
  const optionalSeconds=value=>value===null||value===undefined?'Not observed':seconds(value);
  function driftTable(host,title,columns,rows){
    host.appendChild(make('h4',title));
    const wrap=make('div',null,'owg-process-table'),table=make('table'),head=make('thead'),header=make('tr'),body=make('tbody');
    for(const title of columns){const cell=make('th',title);cell.setAttribute('scope','col');header.appendChild(cell);}
    head.appendChild(header);
    for(const values of rows){const row=make('tr');for(const value of values)row.appendChild(make('td',value));body.appendChild(row);}
    table.append(head,body);wrap.appendChild(table);host.appendChild(wrap);
  }
  function aliasLabel(value){return String(value).replace(/^participant_/, 'Participant ').replace(/^team_/, 'Team ');}
  function eventLabelSuffix(event){return [...safe(report?.participant_labels?.dimensions).map(key=>
    event[key]?aliasLabel(event[key]):'Unknown '+key),...(event.origin?[String(event.origin).replace(/^source_/,'Source ')]:[])].join(' · ');}
  function renderCaseCorrelation(host){
    const data=report?.case_correlation;if(!data)return;
    host.append(make('h3','Reviewed shared case keys'),make('p',data.multi_source_case_count+' cases contain events from more than one of '+data.source_count+' supplied source aliases. Matching uses the caller-reviewed literal case key, not timing or activity similarity. The source namespace and case identity are caller declarations, not independently verified associations. Native case keys and source names were replaced before upload.','muted'));
    const list=make('ul');for(const source of safe(data.sources))list.appendChild(make('li',String(source.origin).replace(/^source_/,'Source ')+' · '+source.event_count+' events across '+source.case_count+' cases'));
    host.appendChild(list);
  }
  function renderActivityLabelSupport(host,activity){
    const support=activity?.label_support;if(!support)return;
    const titles={participant:'Participant labels',team:'Team labels',origin:'Source aliases'};
    host.append(make('h4','Labels on this activity'),make('p','Counts use every occurrence of this activity in the current population. A case can contain several labels, so case support across labels is not additive. Labels do not verify people, membership, source identity, effort or human handoffs.','muted'));
    for(const [dimension,data] of Object.entries(support)){
      if(!titles[dimension])continue;
      driftTable(host,titles[dimension],['Supplied label','Activity events','Distinct cases'],
        safe(data.labels).map(row=>[dimension==='origin'?String(row.label).replace(/^source_/,'Source '):aliasLabel(row.label),row.event_count,row.case_count]));
      host.appendChild(make('p',data.missing_label_event_count+' activity events across '+data.missing_label_case_count+
        ' cases have no '+(dimension==='origin'?'source':dimension)+' label; those values remain unknown.','muted'));
      if(data.rows_truncated)host.appendChild(make('p','Showing '+data.rows_returned+' of '+data.label_count+' labels, ranked by event support. Totals include all labels and all selected cases.','muted'));
    }
  }
  function renderParticipantLabels(host){
    const metadata=report?.participant_labels;if(!metadata)return;
    host.append(make('h3','Participant and team labels'),make('p','Labels describe this import only. Counts and adjacent label changes do not verify people, team membership or human handoffs. Selected participant/team identifiers were replaced before upload.','muted'));
    for(const key of safe(metadata.dimensions)){
      const data=metadata.dimensions_summary?.[key];if(!data)continue;
      const section=make('details');section.appendChild(make('summary',(key==='participant'?'Participant':'Team')+' labels · '+data.label_count+' distinct · '+data.adjacent_label_change_count+' adjacent changes'));
      section.appendChild(make('p',data.missing_label_events+' events lack this label; '+data.ambiguous_tied_timestamp_changes+' changes touching tied-time groups were excluded from change counts. Whole-case cohorts are available after explicit local retention.','muted'));
      const wrapper=make('div',null,'owg-process-table'),table=make('table'),head=make('thead'),hr=make('tr');
      ['Import-scoped label','Events','Cases'].forEach(title=>hr.appendChild(make('th',title)));head.appendChild(hr);
      const body=make('tbody');safe(data.labels).slice(0,100).forEach(row=>{const tr=make('tr');
        [aliasLabel(row.label),row.event_count,row.case_count].forEach(value=>tr.appendChild(make('td',value)));body.appendChild(tr);});
      table.append(head,body);wrapper.appendChild(table);section.appendChild(wrapper);
      if(data.label_count>100)section.appendChild(make('p','Showing 100 labels here; retained-case filters include every label.','muted'));
      const changes=make('ul');safe(data.changes).forEach(row=>changes.appendChild(make('li',aliasLabel(row.from)+' → '+aliasLabel(row.to)+' · '+row.change_count+' changes in '+row.case_count+' cases')));
      section.appendChild(changes);if(data.changes_truncated)section.appendChild(make('p','Only the 50 most frequent change types are shown.','muted'));
      host.appendChild(section);
    }
  }
  function metric(label,value){
    const host=make('div',null,'owg-pmetric');
    host.append(make('strong',value),make('span',label));return host;
  }
  function selectActivity(activity){
    activeActivity=activity;render();
    document.getElementById('owgProcessActivityDetails')?.focus();
  }
  function graph(path){
    const scroller=make('div',null,'owg-process-map');
    scroller.setAttribute('aria-label','Observed event sequence; the arrows represent recorded order, not causal dependencies');
    const steps=safe(path).slice(0,24), width=Math.max(300,steps.length*184+35);
    const drawing=svg('svg',{role:'group',width,height:96,viewBox:'0 0 '+width+' 96',
      'aria-label':'Observed events in order, not a confirmed workflow'});
    steps.forEach((step,i)=>{
      const x=12+i*184;
      if(i){
        const link=svg('path',{d:'M '+(x-19)+' 43 H '+(x-5),stroke:'#607c71','stroke-width':2,fill:'none'});
        drawing.appendChild(link);
        const tip=svg('path',{d:'M '+(x-10)+' 38 L '+(x-5)+' 43 L '+(x-10)+' 48',
          stroke:'#607c71','stroke-width':2,fill:'none'});drawing.appendChild(tip);
      }
      const rect=svg('rect',{x,y:13,width:161,height:61,rx:9,stroke:'#557e70',fill:'#f1f7f4'});
      const name=svg('text',{x:x+9,y:39,fill:'#234236','font-size':12});
      const value=String(step);
      name.textContent=value.length>22?value.slice(0,21)+'…':value;
      const stepNo=svg('text',{x:x+9,y:57,fill:'#546e62','font-size':10});
      stepNo.textContent='Event '+(i+1);
      const title=svg('title');title.textContent=value;
      const node=svg('g',{role:'button',tabindex:0,
        'aria-label':'Inspect event '+(i+1)+': '+value,
        'aria-pressed':String(activeActivity===value)});
      if(activeActivity===value)rect.setAttribute('stroke-width','3');
      node.append(rect,name,stepNo,title);
      node.addEventListener('click',()=>selectActivity(value));
      node.addEventListener('keydown',event=>{
        if(event.key==='Enter'||event.key===' '){event.preventDefault();selectActivity(value);}
      });
      drawing.appendChild(node);
    });
    scroller.appendChild(drawing);return scroller;
  }
  function render(){
    const host=document.getElementById('owgProcessResults');if(!host||!report)return;
    host.replaceChildren();
    host.appendChild(make('h3',report.source_preview?'Full supplied source projection':report.cohort?'Selected complete-case cohort':'Full imported log'));
    if(report.cohort)host.appendChild(make('p',report.cohort.selected_case_count+' of '+report.cohort.dataset_case_count+
      ' retained cases selected by '+JSON.stringify(report.cohort.filters)+'. Every event of these cases is included. Case ordinals and variant IDs remain those of the original snapshot.','muted'));
    if(report.source_preview)host.appendChild(make('p','Bounded provider projection: supported source events may omit earlier work, other activities or later state changes. Cases label selected references, not inferred cross-record business cases. This source preview cannot be retained as an imported CSV.','muted'));
    const stats=make('div',null,'owg-pmetrics');
    stats.append(metric('Cases',report.case_count),metric('Events',report.input_rows),
      metric('Activities',report.activity_count),metric('Observed variants',report.variant_count),
      metric('Median case elapsed',seconds(report.metrics?.median_case_elapsed_seconds)),
      metric('Median inter-event gap',report.metrics?.median_inter_event_gap_seconds==null?
        '—':seconds(report.metrics.median_inter_event_gap_seconds)));
    host.appendChild(stats);renderParticipantLabels(host);renderCaseCorrelation(host);
    host.appendChild(make('p','Elapsed time spans first to last logged event; gaps are not proven waiting, and repeated activities are not necessarily rework. No case outcome or automation authority is established.','muted'));
    if(report.data_quality){
      const quality=report.data_quality;
      host.appendChild(make('p','Data quality: '+quality.cases_with_tied_timestamps+' cases have timestamp ties; '+
        quality.same_case_activity_timestamp_repeats+' repeated case/activity/timestamp entries. Ties follow supplied row order; repeats are retained. Source completeness and authoritative case identity are not verified.','muted'));
    }
    if(report.conformance){
      const comparison=report.conformance,ref=comparison.reference;
      host.appendChild(make('h3','Structural comparison · saved revision '+ref.revision));
      host.appendChild(make('p',comparison.exact_structural_match_cases+' exact path matches · '+
        comparison.structurally_deviating_cases+' differing cases · '+comparison.possible_incomplete_prefix_cases+
        ' possible incomplete prefixes. All cases and full variants are compared.'));
      host.appendChild(make('p','Routing decisions are traversed structurally; rule conditions, business compliance, source completeness and successful outcomes are not evaluated.','muted'));
    }
    renderObservedMap(host);
    host.appendChild(make('h3','Observed process variants'));
    const variants=safe(report.variants);
    const controls=make('div',null,'owg-variants');
    const visible=variants.slice(0,20);
    visible.forEach((item,index)=>{
      const variantNumber=/^variant_[0-9]+$/.test(item.variant_id||'')?item.variant_id.slice(8):index+1;
      const b=button('Variant '+variantNumber+' · '+item.case_count+' cases',()=>{
        activeVariant=index;activeActivity=null;render();
      });
      b.setAttribute('aria-pressed',String(activeVariant===index));controls.appendChild(b);
    });
    host.appendChild(controls);
    if(variants.length){
      const selected=variants[Math.min(activeVariant,variants.length-1)];
      const edit=button('Design improved TO-BE workflow',()=>{
        if(report.source_preview){
          try{
            if(!window.owgProcessCatalog?.draftObserved)throw new Error('Process catalog unavailable. Reopen the local dashboard.');
            window.owgProcessCatalog.draftObserved({path:selected.observed_path,count:selected.case_count,
              basis:'selected_source_audit',partial:true,evidenceRefs:[]});
          }catch(error){setStatus(error.message);}
          return;
        }
        if(!window.owgProcessDesigner?.open) {
          setStatus('Process designer unavailable. Reopen the local dashboard.');
          return;
        }
        window.owgProcessDesigner.open(report,selected);
      });
      edit.disabled=!!selected.path_truncated;
      if(selected.path_truncated)edit.title='This observed path is truncated; design requires a complete observed variant.';
      host.appendChild(edit);
    }
    if(variants.length){
      const variant=variants[Math.min(activeVariant,variants.length-1)];
      host.appendChild(graph(variant.observed_path));
      host.appendChild(make('p',variant.path_truncated?
        'This variant contains '+variant.path_event_count+' events. Only the first 24 are shown.':
        'Each arrow means next recorded activity in the same supplied case, not a causal or required step.','muted'));
    }
    if(report.variants_truncated)host.appendChild(make('p','Additional variants are omitted from this bounded preview.','muted'));
    const variant=variants[Math.min(activeVariant,variants.length-1)];
    if(variant){
      if(variant.conformance){
        const c=variant.conformance;
        host.appendChild(make('h4',c.exact_structural_match?'Selected variant matches the reference path':'Selected variant differs from the reference path'));
        host.appendChild(make('p','Minimum activity edits: '+c.minimum_activity_edit_count+
          (c.could_be_incomplete_log_prefix?' · this could be an incomplete source-log prefix.':'.')));
        const alignment=make('details');alignment.appendChild(make('summary','Inspect one minimum-edit structural alignment'));
        const list=make('ol');safe(c.alignment).forEach(item=>list.appendChild(make('li',item.type+' · '+item.activity+
          (item.log_position?' · log event '+item.log_position:'')+(item.model_activity_id?' · model '+item.model_activity_id:''))));
        alignment.appendChild(list);if(c.alignment_truncated)alignment.appendChild(make('p','Alignment is truncated to 80 entries.'));
        host.appendChild(alignment);
      }
      if(variant.ambiguous_order_case_count)host.appendChild(make('p',variant.ambiguous_order_case_count+
        ' cases in this variant have timestamp ties. This path and its structural comparison depend on the supplied row order.','muted'));
      host.appendChild(make('h3','Selected variant · activity inspection'));
      host.appendChild(make('p',variant.case_count+' cases · median case elapsed '+
        seconds(variant.median_case_elapsed_seconds)+'. Counts below cover every case in this exact variant.','muted'));
      const activities=make('div',null,'owg-variants');
      const all=button('All activities',()=>{activeActivity=null;render();});
      all.setAttribute('aria-pressed',String(activeActivity===null));
      activities.appendChild(all);
      safe(variant.activities).forEach(activity=>{
        const pick=button(activity.activity+' · '+activity.event_count+' events',()=>selectActivity(activity.activity));
        pick.setAttribute('aria-pressed',String(activeActivity===activity.activity));
        activities.appendChild(pick);
      });
      host.appendChild(activities);
      if(activeActivity){
        const details=make('section');details.id='owgProcessActivityDetails';details.tabIndex=-1;
        const activity=safe(variant.activities).find(a=>a.activity===activeActivity);
        details.appendChild(make('h4',activeActivity));
        details.appendChild(make('p',activity.event_count+' recorded events across '+activity.case_count+
          ' cases in this variant. Repeated positions count as separate events.'));
        renderActivityLabelSupport(details,activity);
        details.appendChild(make('p','Select an example below to inspect its recorded chronology. Times describe source events, not active labor or verified waiting.','muted'));
        host.appendChild(details);
      }
      host.appendChild(make('h3','Selected variant · case examples'));
      safe(variant.case_examples).forEach(example=>{
        const disclosure=make('details');
        disclosure.appendChild(make('summary',example.case+' · '+example.event_count+' events · '+seconds(example.duration_seconds)+(example.case_key_alias?' · '+example.case_key_alias:'')));
        const events=make('ol');
        safe(example.events).forEach(event=>{
          const item=make('li',event.activity+' · '+event.observed_at+
            (event.elapsed_since_previous_seconds==null?'':' · gap '+seconds(event.elapsed_since_previous_seconds))+(eventLabelSuffix(event)?' · '+eventLabelSuffix(event):''));
          item.value=event.position;
          if(event.activity===activeActivity)item.appendChild(make('strong',' · selected activity'));
          events.appendChild(item);
        });
        disclosure.appendChild(events);
        if(example.path_truncated)disclosure.appendChild(make('p','Only the first 24 recorded events are shown.','muted'));
        host.appendChild(disclosure);
      });
      host.appendChild(make('p','Showing '+safe(variant.case_examples).length+' of '+variant.case_count+
        ' cases in this variant. Case labels apply only within this dataset or preview.'+
        (variant.case_examples_truncated?' Additional case examples are omitted.':''),'muted'));
    }
    host.appendChild(make('h3',activeActivity?'Selected activity · adjacent transitions in this variant':'Selected variant · observed transitions'));
    const wrapper=make('div',null,'owg-process-table');
    const table=make('table'),head=make('thead'),hr=make('tr');
    ['From','To','Transitions','Cases','Median gap','Maximum gap'].forEach(t=>hr.appendChild(make('th',t)));head.appendChild(hr);
    const body=make('tbody');
    const edges=safe(variant?.transitions).filter(edge=>!activeActivity||edge.from===activeActivity||edge.to===activeActivity);
    edges.slice(0,64).forEach(edge=>{
      const row=make('tr');
      [edge.from,edge.to,edge.transition_count,edge.case_count,seconds(edge.median_elapsed_gap_seconds),seconds(edge.max_elapsed_gap_seconds)].forEach(v=>row.appendChild(make('td',v)));
      body.appendChild(row);
    });
    table.append(head,body);wrapper.appendChild(table);host.appendChild(wrapper);
    if(edges.length>64)host.appendChild(make('p','Only the first 64 transition types are shown.','muted'));
    host.appendChild(make('p','Transition gaps use all matching events in this variant. Native case IDs and source values are never returned. '+(report.import_retained?'This snapshot was explicitly retained locally.':report.source_preview?'This partial source projection is not saved.':'This CSV preview is not saved.'),'muted'));
    if(!report.source_preview)window.owgOpportunityReview?.render?.(report);
  }
  function renderObservedMap(host){
    if(!window.owgProcessGraph?.drawObserved)return;
    const control=button(observedExpanded?'Hide combined transition map':'Explore all observed transitions',()=>{
      observedExpanded=!observedExpanded;render();
    });
    control.setAttribute('aria-expanded',String(observedExpanded));host.appendChild(control);
    if(!observedExpanded)return;
    host.appendChild(make('h3','All observed activity transitions'));
    host.appendChild(make('p','Counts cover every '+(report.cohort?'selected complete case in this cohort':'case in this imported log')+'. Arrows show observed adjacent pairs, not causal dependencies or approved routes. A route through this combined map may never occur in one case; inspect exact variants below for complete paths.','muted'));
    try{
      const drawing=window.owgProcessGraph.drawObserved(report,activity=>{
        observedSelection=activity.label;observedTransitionSelection=null;render();document.getElementById('owgObservedActivityDetails')?.focus();
      },edge=>{
        observedSelection=null;observedTransitionSelection={from:edge.from,to:edge.to};render();
        document.getElementById('owgObservedEdgeDetails')?.focus();
      });
      drawing.id='owgObservedTransitionMap';
      host.appendChild(drawing);
    }catch{
      host.appendChild(make('p','Combined graph unavailable for this response. Variant paths and statistics remain available.','muted'));
    }
    const edge=safe(report.transitions).find(row=>row.from===observedTransitionSelection?.from&&row.to===observedTransitionSelection?.to);
    if(edge){
      const details=make('section');details.id='owgObservedEdgeDetails';details.tabIndex=-1;
      details.append(make('h4',edge.from+' → '+edge.to+' · '+(report.cohort?'selected complete-case cohort':'full supplied log')),
        make('p',edge.transition_count+' recorded adjacent transitions across '+edge.case_count+' distinct cases. Repeated transitions remain separate.'),
        make('p','Median elapsed gap '+optionalSeconds(edge.median_elapsed_gap_seconds)+'; maximum '+optionalSeconds(edge.max_elapsed_gap_seconds)+'. Gaps are not verified waiting or active labor.','muted'),
        make('p','Adjacency follows timestamps, then supplied row order for ties. Tied-time order is unverified; an arrow does not establish causality, policy or a human handoff.','muted'));
      const variants=safe(report.variants).map((variant,index)=>({variant,index})).filter(({variant})=>safe(variant.transitions).some(row=>row.from===edge.from&&row.to===edge.to));
      for(const {variant,index} of variants)details.appendChild(button('Inspect supporting variant '+(index+1)+' · '+variant.case_count+' cases',()=>{
        activeVariant=index;activeActivity=null;render();document.getElementById('owgObservedEdgeDetails')?.focus();
      }));
      if(!variants.length)details.appendChild(make('p','No displayed variant contains this transition. The bounded variant sample may omit supporting full paths.','muted'));
      if(report.dataset_id)details.appendChild(button('Browse retained cases for '+edge.from+' → '+edge.to,()=>browseCases(report.dataset_id,0,generation,null,{from:edge.from,to:edge.to})));
      host.appendChild(details);
    }
    const selected=safe(report.activities).find(row=>row.activity===observedSelection);
    if(!selected)return;
    const details=make('section');details.id='owgObservedActivityDetails';details.tabIndex=-1;
    details.append(make('h4',selected.activity+' · '+(report.cohort?'selected complete-case cohort':'full imported log')),
      make('p',selected.event_count+' recorded events across '+selected.case_count+' cases. Repeated occurrences remain separate.'));
    renderActivityLabelSupport(details,selected);
    const variants=safe(report.variants).map((variant,index)=>({variant,index})).filter(({variant})=>safe(variant.activities).some(row=>row.activity===selected.activity));
    if(variants.length){
      details.appendChild(make('p','Inspect a displayed supporting variant:'));
      for(const {variant,index} of variants)details.appendChild(button('Inspect variant '+(index+1)+' · '+variant.case_count+' cases',()=>{
        activeVariant=index;activeActivity=selected.activity;render();document.getElementById('owgProcessActivityDetails')?.focus();
      }));
    }else details.appendChild(make('p','No displayed variant contains this activity. The bounded variant sample may omit supporting paths.'));
    if(report.dataset_id)details.appendChild(button('Browse retained cases for '+selected.activity,()=>browseCases(report.dataset_id,0,generation,selected.activity)));
    const table=make('table'),head=make('thead'),header=make('tr'),body=make('tbody');
    for(const title of ['From','To','Transitions','Cases','Median elapsed gap'])header.appendChild(make('th',title));head.appendChild(header);
    for(const edge of safe(report.transitions).filter(edge=>edge.from===selected.activity||edge.to===selected.activity)){
      const row=make('tr');for(const value of [edge.from,edge.to,edge.transition_count,edge.case_count,seconds(edge.median_elapsed_gap_seconds)])row.appendChild(make('td',value));body.appendChild(row);
    }
    table.append(head,body);const scroll=make('div',null,'owg-process-table');scroll.appendChild(table);details.appendChild(scroll);
    details.appendChild(make('p','Elapsed gaps are not verified waiting or active labor. The first and last events are not independently verified case start or completion.','muted'));
    host.appendChild(details);
  }
  function setReference(value){
    invalidateDrift();invalidateCohortComparison();
    cohortGeneration++;cohortControls();
    reference=value?{workflow_id:value.workflow_id,revision:String(value.revision)}:null;
    const label=document.getElementById('owgProcessReference');
    if(label)label.textContent=reference?'Next analysis compares with saved process '+reference.workflow_id+' revision '+reference.revision+'. Existing results are unchanged until Analyze.':'No saved reference selected.';
  }
  function showSourcePreview(value){
    if(!value||value.format!=='openworkgraph.process-mining-csv-preview.v1'||!Array.isArray(value.variants))
      throw new Error('A bounded analyzed source projection is required.');
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before opening this source projection?'))return false;
    generation++;resetMapping();analyzedFile=null;retainedDataset=null;report=null;clearReport();retentionState(true);
    const file=document.getElementById('owgProcessFile');if(file)file.value='';
    const control=document.getElementById('owgProcessAnalyze');if(control)control.disabled=true;
    report={...value,source_preview:true};activeVariant=0;activeActivity=null;render();retentionState(true);
    setStatus('Opened a bounded source projection. Coverage is partial; no CSV, source records or report were retained.');
    document.querySelector('[role=tab][data-tab=evidence]')?.click();
    document.getElementById('owgProcessCSV')?.scrollIntoView({block:'start'});
    return true;
  }
  function useSharedCaseImport(file){
    if(!file||typeof file.arrayBuffer!=='function'||file.size<1||file.size>MAX_UPLOAD)return false;
    if(window.owgProcessDesigner?.hasDraft?.()&&!window.confirm('Discard the unsaved proposed process before using this reviewed shared-key import?'))return false;
    generation++;resetMapping(true);mappedFile=file;mappedCaseLinks=true;analyzedFile=null;analyzedCaseLinks=false;
    report=null;retainedDataset=null;clearReport();retentionState(true);
    const input=document.getElementById('owgProcessFile');if(input)input.value='';
    document.getElementById('owgProcessAnalyze').disabled=false;
    setStatus('Reviewed shared-key import ready. Analyze to send only structural events and import-scoped case/source aliases to local OWG. Nothing is retained automatically.');
    return true;
  }
  function withdrawSharedCaseImport(){if(mappedCaseLinks||analyzedCaseLinks)invalidateMappedPreview();}
  window.owgProcessMining={setReference,showSourcePreview,useSharedCaseImport,withdrawSharedCaseImport,
    analyzeSharedCaseImport:()=>mappedCaseLinks?analyze():undefined};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
