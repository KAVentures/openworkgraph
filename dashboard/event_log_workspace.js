/* Local event-log workspace: import CSV/XES/OCEL logs, analyse, compare with a reference model, export. */
(() => {
  'use strict';
  const MODEL_FORMAT='openworkgraph.reference-model.v1';
  const state={logs:[],logId:'',caseType:'',generation:0,refresh:0,follows:[],attr:{},caseActivity:'',objectTypes:[]};
  // The global case filter: follower rules plus an optional case-attribute value and/or numeric range.
  // More case filters (Disco-style timeframe, performance and variation filters): end time, events per case,
  // how many cases share the case's path, and rework.
  const MORE_FILTER_KEYS=['ended_from','ended_before','events','path_cases','rework','tag','without_tag','without_activity'];
  const countText=(r,what)=>what+' '+(r.min!==undefined&&r.max!==undefined?(r.min===r.max?'= '+r.min:r.min+'–'+r.max):r.min!==undefined?'\u2265 '+r.min:'\u2264 '+r.max);
  const moreFilterText=a=>[a.ended_from?'ended from '+a.ended_from.slice(0,10):'',a.ended_before?'ended before '+a.ended_before.slice(0,10):'',a.events?countText(a.events,'events per case'):'',a.path_cases?countText(a.path_cases,'cases on the same path'):'',a.rework===true?'repeats an activity':a.rework===false?'repeats no activity':'',a.tag?'tagged '+JSON.stringify(a.tag):'',a.without_tag?'not tagged '+JSON.stringify(a.without_tag):'',a.without_activity?'never does '+JSON.stringify(a.without_activity):''].filter(Boolean).join('; ');
  const filterOn=()=>state.follows.length>0||!!state.attr.attribute||!!state.attr.attribute_range||!!state.attr.starts_with||!!state.attr.ends_with||!!state.attr.duration||MORE_FILTER_KEYS.some(k=>state.attr[k]!==undefined)||!!state.caseActivity;
  const globalCohort=()=>({...(state.follows.length?{follows:state.follows}:{}),...state.attr,...(state.caseActivity?{activity:state.caseActivity}:{})});
  const rangeText=r=>(r.min!==undefined?r.min+' ≤ ':'')+r.name+(r.max!==undefined?' < '+r.max:'');
  const hoursText=s=>Number((s/3600).toFixed(3))+' h';
  const durationText=d=>'case duration '+(d.min!==undefined?'\u2265 '+hoursText(d.min):'')+(d.min!==undefined&&d.max!==undefined?' and ':'')+(d.max!==undefined?'< '+hoursText(d.max):'');
  const attrText=()=>[state.attr.attribute,state.attr.attribute_range?rangeText(state.attr.attribute_range):'',state.attr.starts_with?'starts with '+JSON.stringify(state.attr.starts_with):'',state.attr.ends_with?'ends with '+JSON.stringify(state.attr.ends_with):'',state.attr.duration?durationText(state.attr.duration):'',moreFilterText(state.attr)].filter(Boolean).join('; ');
  let activityLookupBusy=false;
  // Views that honour the workspace case filter; every other section uses all cases.
  const FILTERED=new Set(['automation-candidates','summary','variants','variants/preview','variant-coverage','duration-distribution','activities','kpis','process-map','playback','dotted-chart','performance-spectrum','waiting-time','drift','model-discovery','organization','report.html','decision-points','decision-rules']);
  FILTERED.add('roles');
  FILTERED.add('anonymized-export');
  FILTERED.add('anonymized-export.csv');
  FILTERED.add('model-discovery.pnml');
  FILTERED.add('temporal-profile');
  FILTERED.add('heuristics-net');
  FILTERED.add('footprints');
  FILTERED.add('model-quality');
  FILTERED.add('similar-cases');
  FILTERED.add('model-change');
  FILTERED.add('model-comparison');
  FILTERED.add('funnel');
  FILTERED.add('variants.csv');
  FILTERED.add('unusual-cases');
  FILTERED.add('due-dates');
  FILTERED.add('story');
  FILTERED.add('co-occurrence');
  FILTERED.add('activity-profile');
  FILTERED.add('quality/timestamp-ties');
  FILTERED.add('breakdown');
  FILTERED.add('breakdown.csv');
  FILTERED.add('activities.csv');
  FILTERED.add('resources.csv');
  FILTERED.add('next-steps');
  FILTERED.add('petri-net-replay');
  FILTERED.add('petri-net-alignments');
  FILTERED.add('variant.pnml');
  FILTERED.add('petri-net-deviation-causes');
  FILTERED.add('petri-net-alignments/cases.csv');
  FILTERED.add('petri-net-alignments/tag');

  const el=(tag,text,cls)=>{
    const node=document.createElement(tag);
    if(text!==undefined&&text!==null)node.textContent=String(text);
    if(cls)node.className=cls;
    return node;
  };
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const select=(id,options)=>{const node=document.createElement('select');node.id=id;setOptions(node,options);return node;};
  function setOptions(node,options){node.replaceChildren(...options.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));}
  // The dashboard's .row class sets display, which would override the hidden attribute.
  const show=(node,visible)=>{node.hidden=!visible;node.style.display=visible?'':'none';};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};

  // Pure helpers, exercised by the browser test.
  // A gzip-compressed file (log.xes.gz, events.csv.gz) is read like the file inside it: the browser
  // decompresses the first bytes for the column read and the server decompresses the upload.
  const isGzip=file=>/\.gz$/i.test(String(file&&file.name||''));
  async function headText(file,bytes){
    if(!isGzip(file))return file.slice(0,bytes).text();
    const reader=file.stream().pipeThrough(new DecompressionStream('gzip')).getReader();
    const parts=[];let size=0;
    try{while(size<bytes){const {done,value}=await reader.read();if(done)break;parts.push(value);size+=value.length;}}
    finally{reader.cancel().catch(()=>{});}
    return new Blob(parts).slice(0,bytes).text();
  }
  function detectFormat(name){
    const lower=String(name||'').toLowerCase().replace(/\.gz$/,'');
    if(lower.endsWith('.xes'))return 'xes';
    if(lower.endsWith('.xmlocel'))return 'ocelxml';
    if(lower.endsWith('.jsonocel')||lower.endsWith('.json'))return 'ocel';
    return 'csv';
  }
  function csvHeader(text,delimiter){
    const line=String(text||'').replace(/^﻿/,'');
    const out=[];let cell='',quoted=false;
    for(let i=0;i<line.length;i++){
      const c=line[i];
      if(quoted){if(c==='"'&&line[i+1]==='"'){cell+='"';i++;}else if(c==='"')quoted=false;else cell+=c;}
      else if(c==='"')quoted=true;
      else if(c===delimiter){out.push(cell);cell='';}
      else if(c==='\r'||c==='\n')break;
      else cell+=c;
    }
    out.push(cell);
    return out.slice(0,200);
  }
  function guess(columns,words){return columns.find(c=>words.some(w=>c.toLowerCase().includes(w)))||'';}
  // One accepted path per line, activities separated by ">" or "→".
  function parsePaths(text){
    if(String(text||'').trimStart().startsWith('{'))return JSON.parse(text);
    const paths=String(text||'').split(/\r?\n/).map(line=>line.split(/>|→/).map(s=>s.trim()).filter(Boolean)).filter(p=>p.length);
    if(!paths.length)throw new Error('Enter at least one reference path, for example: Receive > Review > Close');
    if(paths.length>10)throw new Error('Use at most 10 reference paths.');
    return {format:MODEL_FORMAT,paths};
  }
  function duration(seconds){
    if(seconds===null||seconds===undefined||!Number.isFinite(Number(seconds)))return '—';
    const s=Number(seconds);
    if(s<120)return Math.round(s)+' s';
    if(s<7200)return Math.round(s/60)+' min';
    if(s<172800)return (s/3600).toFixed(1)+' h';
    return (s/86400).toFixed(1)+' d';
  }
  const pct=v=>v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%';
  const REASON_LABELS={missing_case:'missing case',missing_activity:'missing activity',missing_timestamp:'missing time',
    timestamp_without_utc_offset:'time without a UTC offset',unparseable_timestamp:'unreadable time'};
  // Plain-language rejection reasons from a quality report; reasons can overlap.
  function reasonText(reasons){
    if(!reasons)return 'missing case, activity or timezone-qualified time';
    const parts=Object.entries(reasons).filter(([,n])=>n>0).map(([k,n])=>Number(n).toLocaleString()+' '+(REASON_LABELS[k]||k));
    return parts.length?parts.join(', '):'reason not recorded';
  }
  const keptText=q=>(q&&q.timestamps_with_assumed_offset?' '+Number(q.timestamps_with_assumed_offset).toLocaleString()+' times without an offset were read with the chosen offset.':'')
    +(q&&q.rejected_rows_kept_for_review?' '+Number(q.rejected_rows_kept_for_review).toLocaleString()+' rejected rows were kept for review under Data quality.':'');
  const tieText=q=>q&&q.events_sharing_a_timestamp_in_a_case?' '+Number(q.events_sharing_a_timestamp_in_a_case).toLocaleString()+' events share a timestamp with another event of the same case; their order falls back to the event ID.':'';

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Event-log request failed ('+response.status+').');}
    return response;
  }
  const getJSON=async path=>(await api(path)).json();
  const logPath=(suffix,params)=>{
    const q=new URLSearchParams(),args=FILTERED.has(suffix)?mergeActiveCohort(params||{},globalCohort()):{...(params||{})};
    for(const [key,value] of Object.entries(args)){
      if(key==='attribute_range'||key==='follows')q.set(key,JSON.stringify(value));
      else if(key==='duration'){if(value.min!==undefined)q.set('duration_min',String(value.min));if(value.max!==undefined)q.set('duration_max',String(value.max));}
      else if(key==='events'||key==='path_cases'){if(value.min!==undefined)q.set(key+'_min',String(value.min));if(value.max!==undefined)q.set(key+'_max',String(value.max));}
      else if(Array.isArray(value))value.forEach(v=>q.append(key,v));
      else q.set(key,value!==null&&typeof value==='object'?JSON.stringify(value):value);
    }
    if(state.caseType)q.set('case_type',state.caseType);
    const search=q.toString();return '/v1/event-logs/'+encodeURIComponent(state.logId)+'/'+suffix+(search?'?'+search:'');
  };

  function table(headers,rows){
    const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');
    // A scrollable table must be reachable by keyboard (WCAG 2.1.1).
    wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
    headers.forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
    const body=el('tbody');rows.forEach(r=>{const tr=el('tr');r.forEach(v=>{const cell=el('td');cell.style.whiteSpace='pre-wrap';if(v instanceof window.Node)cell.appendChild(v);else cell.textContent=v===null||v===undefined?'':String(v);tr.appendChild(cell);});body.appendChild(tr);});
    t.append(head,body);wrap.appendChild(t);return wrap;
  }
  const status=text=>{const s=document.getElementById('owgEventLogStatus');if(s)s.textContent=String(text||'');};
  const section=(title)=>{const box=el('div');box.style.marginTop='12px';box.appendChild(el('h3',title));return box;};

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgEventLogCard'))return;
    const card=el('div',null,'card');card.id='owgEventLogCard';
    card.appendChild(el('h2','Large event logs'));
    card.appendChild(el('div','Import a CSV, XES or OCEL 2.0 event log exported from your own systems and analyse it on this computer. Imported logs are stored locally until you delete them and are never readable by connected AI apps. Results describe the log; they are not verified business policy or causes.','muted'));

    const file=input('owgEventLogFile','file');file.accept='.csv,.tsv,.txt,.xlsx,.sqlite,.sqlite3,.db,.jsonl,.ndjson,.parquet,.xes,.json,.jsonocel,.xmlocel,.xml';
    const format=select('owgEventLogFormat',[['csv','CSV'],['xes','XES'],['ocel','OCEL 2.0 JSON'],['ocelxml','OCEL 2.0 XML']]);
    const name=input('owgEventLogName','text','Log name');name.maxLength=120;
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('File',file),field('Format',format),field('Name',name));
    const mapping=el('div',null,'row');mapping.id='owgEventLogMapping';mapping.style.alignItems='end';
    const cols={case:select('owgEventLogCase',[]),activity:select('owgEventLogActivity',[]),time:select('owgEventLogTime',[]),resource:select('owgEventLogResource',[])};
    const delimiter=select('owgEventLogDelimiter',[[',','Comma'],[';','Semicolon'],['t','Tab'],['|','Pipe']]);
    const extras=select('owgEventLogAttributes',[]);extras.multiple=true;extras.size=3;
    extras.title='Only the columns you choose are stored, for example a lifecycle (start/complete) or end-time column.';
    // Times written without an offset (always the case for Excel dates) are read with this offset.
    const naiveOffset=input('owgEventLogNaiveOffset','text','none: reject them');naiveOffset.maxLength=6;naiveOffset.size=8;naiveOffset.pattern='[+-][0-9]{2}:[0-9]{2}';
    naiveOffset.title='For timestamps without a time zone, e.g. +01:00 or -05:00. Leave empty to reject such rows.';
    // Times such as 03/04/2024 14:05 need a declared format; the dashboard suggests one from the first rows.
    const timeFormat=select('owgEventLogTimeFormat',[['','ISO 8601 (2024-03-04T14:05:00Z)']]);
    mapping.append(field('Case column',cols.case),field('Activity column',cols.activity),field('Timestamp column (with timezone)',cols.time),field('Timestamp format',timeFormat),field('Resource column',cols.resource),field('Extra columns to keep (optional)',extras),field('Delimiter',delimiter),field('Offset for times without one',naiveOffset));
    const identity=el('fieldset');identity.id='owgEventLogIdentity';identity.append(el('legend','Supplied source-event IDs (optional)'));
    const useIdentity=input('owgEventLogUseIdentity','checkbox');
    const identityFields=el('div',null,'row');identityFields.style.alignItems='end';
    const eventId=select('owgEventLogEventId',[]),sourceColumn=select('owgEventLogSourceColumn',[]),caseColumn=select('owgEventLogCaseNamespaceColumn',[]),jsonColumn=select('owgEventLogAttributesJson',[]);
    const sourceNamespace=input('owgEventLogSourceNamespace','text','e.g. crm'),caseNamespace=input('owgEventLogCaseNamespace','text','Defaults to source namespace');
    sourceNamespace.maxLength=40;caseNamespace.maxLength=40;
    identityFields.append(field('Event ID column',eventId),field('Source namespace',sourceNamespace),field('Or source namespace column',sourceColumn),field('Case namespace (optional)',caseNamespace),field('Or case namespace column',caseColumn),field('Attribute JSON column (optional; replaces extra columns)',jsonColumn));
    identity.append(field('Deduplicate using supplied source-event IDs',useIdentity),identityFields,el('p','Namespaces are your declarations, not verified provider identities. Different source IDs keep otherwise identical events; conflicting reuse refuses the entire file. A shared case namespace joins exact case keys across sources. Every row must be valid; rejected-row retention is incompatible. Use Source-ID CSV to preserve these keys on export.'));
    file.accept+=',.gz';      // gzip-compressed logs, e.g. BPI Challenge .xes.gz files
    const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    const consent=input('owgEventLogConsent','checkbox');consentLabel.append(consent,document.createTextNode(' Store this log on this computer until I delete it'));
    const keepLabel=el('label');keepLabel.style.cssText='display:flex;gap:6px;align-items:center';
    const keep=input('owgEventLogKeepRejected','checkbox');keepLabel.append(keep,document.createTextNode(' Also keep rejected CSV rows with the log so I can review, release or discard them'));
    const importButton=button('Import log');importButton.id='owgEventLogImport';importButton.disabled=true;
    const statusLine=el('div',null,'muted');statusLine.id='owgEventLogStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const appendButton=button('Add file to selected log','secondary');appendButton.id='owgEventLogAppend';appendButton.disabled=true;
    appendButton.title='Adds events from a newer export to the stored log chosen below; events it already holds are skipped.';
    const sampleButton=button('Try with a sample log','secondary');sampleButton.id='owgEventLogSample';
    sampleButton.title='Stores a synthetic order-to-cash log (no real data) on this computer and analyses it';
    const importRow=el('div',null,'row');importRow.append(importButton,appendButton,sampleButton);
    // Storage used by the saved logs on this computer, largest first.
    const storageButton=button('Storage used','secondary');storageButton.id='owgEventLogStorage';storageButton.setAttribute('aria-expanded','false');storageButton.setAttribute('aria-controls','owgEventLogStorageList');
    const storageList=el('div');storageList.id='owgEventLogStorageList';storageList.setAttribute('role','region');storageList.setAttribute('aria-label','Storage used by saved logs');show(storageList,false);
    const bytes=n=>n===null||n===undefined?'unknown':n<1048576?Math.max(1,Math.round(n/1024))+' KB':n<1073741824?(Math.round(n/104857.6)/10)+' MB':(Math.round(n/107374182.4)/10)+' GB';
    storageButton.onclick=async()=>{
      const open=storageButton.getAttribute('aria-expanded')!=='true';storageButton.setAttribute('aria-expanded',String(open));show(storageList,open);
      if(!open)return;storageList.replaceChildren(el('div','Measuring storage\u2026','muted'));
      try{const r=await getJSON('/v1/event-logs/storage');
        const head=el('div',r.log_count+' saved log'+(r.log_count===1?' uses ':'s use ')+bytes(r.database_bytes)+' on this computer'+(r.write_ahead_log_bytes?' (plus '+bytes(r.write_ahead_log_bytes)+' not yet compacted)':'')+'; '+bytes(r.free_disk_bytes)+' free on that disk.','muted');
        if(!r.logs.length){storageList.replaceChildren(head);return;}
        const t=table(['Log','Events','Objects','Imported','Estimated size','Share'],r.logs.map(l=>[l.name,Number(l.events).toLocaleString(),Number(l.objects).toLocaleString(),l.imported_at.slice(0,10),bytes(l.estimated_bytes),Math.round(l.share*1000)/10+'%']));t.id='owgEventLogStorageTable';
        storageList.replaceChildren(head,t,el('div',r.estimate,'muted'));}
      catch(error){storageList.replaceChildren(el('div',error.message));}};
    const storageRow=el('div',null,'row');storageRow.append(storageButton);
    card.append(row,mapping,identity,consentLabel,keepLabel,importRow,statusLine,storageRow,storageList);

    const pick=select('owgEventLogPick',[]);const caseType=select('owgEventLogCaseType',[]);
    const exportFormat=select('owgEventLogExportFormat',[['csv','CSV (exact interchange)'],['csv-review','CSV for spreadsheet review'],['sourcecsv','Source-ID CSV (exact interchange)'],['sourcecsv-review','Source-ID CSV for spreadsheet review'],['xes','XES'],['xes-attributes','XES with retained attributes'],['ocel','OCEL 2.0 JSON'],['ocelxml','OCEL 2.0 XML']]);
    const analyze=button('Analyse');analyze.id='owgEventLogAnalyze';
    const exportButton=button('Export','secondary');exportButton.id='owgEventLogExport';
    const remove=button('Delete log','secondary');remove.id='owgEventLogDelete';
    const manage=el('div',null,'row');manage.id='owgEventLogManage';manage.style.cssText='align-items:end;margin-top:12px';show(manage,false);
    const reportButton=button('Download report','secondary');reportButton.id='owgEventLogReport';reportButton.title='A self-contained HTML report: overview, KPIs, top variants and activities';
    const savedButton=button('Saved copies','secondary');savedButton.id='owgEventLogSavedReports';savedButton.setAttribute('aria-expanded','false');savedButton.setAttribute('aria-controls','owgEventLogSavedList');
    const renameButton=button('Rename log','secondary');renameButton.id='owgEventLogRename';
    manage.append(field('Stored log',pick),field('Case notion',caseType),analyze,field('Export as',exportFormat),exportButton,reportButton,savedButton,renameButton,remove);
    const savedList=el('div');savedList.id='owgEventLogSavedList';savedList.setAttribute('role','region');savedList.setAttribute('aria-label','Saved report copies');show(savedList,false);
    const results=el('div');results.id='owgEventLogResults';
    let salesforceStagedFile=null;
    const unstageSalesforce=()=>{
      if(salesforceStagedFile&&file.files?.[0]===salesforceStagedFile){
        file.value='';consent.checked=false;refreshImport();
        status('Earlier Salesforce selection cleared from the import form.');
      }
      salesforceStagedFile=null;
    };
    card.append(manage,savedList,results,salesforceSourceSection((csv,namespace,isCurrent)=>{
      if(!isCurrent())return;
      // Invalidate any in-flight column read for the previous import file.
      columnReadSequence++;readingColumns=false;columnsReady=true;
      const transfer=new DataTransfer();transfer.items.add(new File([csv],'selected-salesforce-cases.csv',{type:'text/csv'}));
      file.files=transfer.files;salesforceStagedFile=file.files[0];
      name.value='Selected Salesforce cases';format.value='csv';delimiter.value=',';
      // The projection has a fixed, reviewed four-column schema; use it directly
      // so a delayed column read cannot overwrite another chosen file's mapping.
      const options=['case_id','activity','timestamp','source_event_id'].map(x=>[x,x]);
      for(const column of [cols.case,cols.activity,cols.time])setOptions(column,options);
      setOptions(cols.resource,[['','(none)'],...options]);setOptions(extras,options);
      setOptions(eventId,[['','Choose a column'],...options]);
      for(const column of [sourceColumn,caseColumn,jsonColumn])setOptions(column,[['','(none)'],...options]);
      cols.case.value='case_id';cols.activity.value='activity';cols.time.value='timestamp';cols.resource.value='';
      useIdentity.checked=true;eventId.value='source_event_id';sourceNamespace.value=namespace;
      sourceColumn.value='';caseNamespace.value='';caseColumn.value='';jsonColumn.value='';
      keep.checked=false;consent.checked=false;refreshImport();
      status('Salesforce events staged. Review the mapping and separately confirm local storage.');
      file.focus();
    },unstageSalesforce),jiraSourceSection());

    const browse=document.getElementById('owgProcessCSV');
    if(browse)browse.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let readingColumns=false,columnsReady=false,columnReadSequence=0;
    const refreshImport=()=>{const csv=format.value==='csv',active=csv&&useIdentity.checked;
      const valid=!active||(eventId.value&&!!sourceNamespace.value!==!!sourceColumn.value&&!(caseNamespace.value&&caseColumn.value)&&!keep.checked);
      const ready=!readingColumns&&(!csv||columnsReady)&&file.files&&file.files[0]&&consent.checked&&valid;importButton.disabled=!(ready&&name.value.trim());appendButton.disabled=!(ready&&state.logId);show(mapping,csv);show(identity,csv);show(identityFields,active);};
    for(const control of [useIdentity,eventId,sourceNamespace,sourceColumn,caseNamespace,caseColumn,jsonColumn]){
      control.addEventListener('change',()=>{consent.checked=false;refreshImport();});
      if(control.tagName==='INPUT'&&control.type==='text')control.addEventListener('input',()=>{consent.checked=false;refreshImport();});
    }
    keep.addEventListener('change',refreshImport);
    function csvMapping(q){
      q.set('case_column',cols.case.value);q.set('activity_column',cols.activity.value);q.set('timestamp_column',cols.time.value);q.set('resource_column',cols.resource.value);
      if(!(useIdentity.checked&&jsonColumn.value))[...extras.selectedOptions].forEach(o=>{if(![cols.case.value,cols.activity.value,cols.time.value,cols.resource.value].includes(o.value))q.append('attribute_column',o.value);});
      q.set('delimiter',delimiter.value);if(keep.checked)q.set('keep_rejected','true');
      if(naiveOffset.value.trim())q.set('naive_timestamp_offset',naiveOffset.value.trim());
      if(timeFormat.value)q.set('timestamp_format',timeFormat.value);
      if(useIdentity.checked)for(const [key,value] of Object.entries({event_id_column:eventId.value,source_namespace:sourceNamespace.value,source_namespace_column:sourceColumn.value,case_namespace:caseNamespace.value,case_namespace_column:caseColumn.value,attributes_json_column:jsonColumn.value}))if(value)q.set(key,value);
    }
    async function readColumns(){
      const sequence=++columnReadSequence;
      const staleOffer=document.getElementById('owgEventLogReuse');if(staleOffer){staleOffer.hidden=true;staleOffer.replaceChildren();}
      const chosen=file.files&&file.files[0];
      columnsReady=false;
      if(!chosen||format.value!=='csv'){
        // Non-CSV imports have no column read, but still need the same ready
        // status as before. Incrementing the sequence above prevents a stale
        // CSV read from changing this status or the current file's readiness.
        readingColumns=false;
        status(chosen?'File mapping ready. Review the selected columns before storing.':'');
        refreshImport();return;
      }
      const sep=delimiter.value==='t'?'\t':delimiter.value;
      readingColumns=true;status('Reading file columns…');refreshImport();
      try{
        const columns=csvHeader(await headText(chosen,65536),sep);
        if(sequence!==columnReadSequence||file.files?.[0]!==chosen||format.value!=='csv'||(delimiter.value==='t'?'\t':delimiter.value)!==sep)return;
        const options=columns.map(c=>[c,c]);
        setOptions(cols.case,options);setOptions(cols.activity,options);setOptions(cols.time,options);setOptions(cols.resource,[['','(none)'],...options]);
        cols.case.value=guess(columns,['case','order','ticket','id'])||columns[0]||'';
        cols.activity.value=guess(columns,['activity','event','step','action'])||columns[1]||'';
        cols.time.value=guess(columns,['time','date','ts'])||columns[2]||'';
        cols.resource.value=guess(columns,['resource','user','who','agent','owner']);
        setOptions(extras,options);[...extras.options].forEach(o=>{o.selected=/lifecycle/i.test(o.value);});
        setOptions(eventId,[['','Choose a column'],...options]);
        for(const control of [sourceColumn,caseColumn,jsonColumn])setOptions(control,[['','(none)'],...options]);
        eventId.value=columns.includes('source_event_id')?'source_event_id':'';
        sourceColumn.value=columns.includes('source_namespace')?'source_namespace':'';
        caseColumn.value=columns.includes('case_namespace')?'case_namespace':'';
        jsonColumn.value=columns.includes('attributes')?'attributes':'';
        columnsReady=!!(cols.case.value&&cols.activity.value&&cols.time.value);
        status(columnsReady?'File mapping ready. Review the selected columns before storing.':'Choose a CSV with case, activity and time columns.');
        if(columnsReady)detectTimeFormat().then(()=>findReusableMapping(columns));
      }catch(_){
        if(sequence===columnReadSequence)status('Could not read the column names.');
      }finally{
        if(sequence===columnReadSequence){readingColumns=false;refreshImport();}
      }
    }
    // An Excel workbook is converted to CSV on this computer (nothing is stored), then imported like any CSV.
    const localOffset=()=>{const m=-new Date().getTimezoneOffset(),a=Math.abs(m);return (m<0?'-':'+')+String(Math.floor(a/60)).padStart(2,'0')+':'+String(a%60).padStart(2,'0');};
    // A workbook with several sheets: choose which one to import (the first by default).
    const sheetSelect=select('owgEventLogSheet',[]),sheetField=field('Worksheet',sheetSelect);sheetField.hidden=true;mapping.prepend(sheetField);
    let workbook=null;
    const isDatabase=n=>/\.(sqlite3?|db)$/i.test(n),isJsonLines=n=>/\.(jsonl|ndjson)$/i.test(n),isParquet=n=>/\.parquet$/i.test(n);
    async function convertWorkbook(chosen,sheet){
      workbook=chosen;
      // A SQLite database file is read like a workbook: its tables take the place of worksheets.
      const database=isDatabase(chosen.name),unit=database?'table':'worksheet';
      columnReadSequence++;readingColumns=true;refreshImport();status(isParquet(chosen.name)?'Reading the Parquet file '+chosen.name+'…':isJsonLines(chosen.name)?'Reading the JSON lines of '+chosen.name+'…':'Reading '+(sheet?unit+' \u201c'+sheet+'\u201d':'the first '+unit)+' of '+chosen.name+'…');
      try{
        const url=isParquet(chosen.name)?'/v1/event-logs/parquet-to-csv':isJsonLines(chosen.name)?'/v1/event-logs/jsonl-to-csv':database?'/v1/event-logs/sqlite-to-csv'+(sheet?'?table='+encodeURIComponent(sheet):''):'/v1/event-logs/xlsx-to-csv'+(sheet?'?sheet='+encodeURIComponent(sheet):'');
        const r=await (await api(url,{method:'POST',body:chosen,headers:{'Content-Type':'application/octet-stream'}})).json();
        setOptions(sheetSelect,r.sheets.map(n=>[n,n]));sheetSelect.value=r.sheet;sheetField.hidden=r.sheets.length<2;
        const transfer=new DataTransfer();transfer.items.add(new File([r.csv],chosen.name.replace(/\.(xlsx|sqlite3?|db|jsonl|ndjson|parquet)$/i,'')+(database?'-'+r.sheet:'')+'.csv',{type:'text/csv'}));
        file.files=transfer.files;format.value='csv';delimiter.value=',';
        if(r.dates_have_no_offset&&!naiveOffset.value.trim())naiveOffset.value=localOffset();
        readingColumns=false;await readColumns();
        if(r.kind==='parquet'){status('Read '+Number(r.rows).toLocaleString()+(r.rows===1?' row':' rows')+' with '+r.columns+' columns from '+chosen.name+'.'+(r.binary_cells_left_empty?' '+Number(r.binary_cells_left_empty).toLocaleString()+' binary values were left empty.':'')+(r.dates_have_no_offset?' Some times have no time zone; they will be read with offset '+naiveOffset.value+' (change it under Offset for times without one).':''));return;}
        if(r.kind==='lines'){status('Read '+Number(r.rows).toLocaleString()+(r.rows===1?' JSON line':' JSON lines')+' with '+r.columns+' columns from '+chosen.name+'; nested fields are named with dots (customer.id).');return;}
        status('Read '+unit+' “'+r.sheet+'”'+(r.sheets.length>1?' ('+(r.sheets.indexOf(r.sheet)+1)+' of '+r.sheets.length+'; choose another under Worksheet)':'')+': '+Number(r.rows).toLocaleString()+(r.rows===1?' row.':' rows.')+(r.binary_cells_left_empty?' '+Number(r.binary_cells_left_empty).toLocaleString()+' binary values were left empty.':'')+(r.dates_have_no_offset?' '+(database?'SQLite':'Excel')+' dates have no time zone; they will be read with offset '+naiveOffset.value+' (change it under Offset for times without one).':''));
      }catch(error){readingColumns=false;file.value='';refreshImport();status(error.message);}
    }
    file.onchange=()=>{const chosen=file.files&&file.files[0];if(chosen){if(!name.value)name.value=chosen.name.replace(/\.[^.]+$/,'').slice(0,120);if(chosen.name.toLowerCase().endsWith('.xlsx')||isDatabase(chosen.name)||isJsonLines(chosen.name)||isParquet(chosen.name)){consent.checked=false;convertWorkbook(chosen);return;}workbook=null;sheetField.hidden=true;format.value=detectFormat(chosen.name);if(chosen.name.toLowerCase().endsWith('.tsv'))delimiter.value='t';}consent.checked=false;readColumns();};
    format.onchange=()=>{consent.checked=false;readColumns();};
    sheetSelect.onchange=()=>{if(workbook){consent.checked=false;convertWorkbook(workbook,sheetSelect.value);}};
    delimiter.onchange=()=>{consent.checked=false;readColumns();};
    // Suggest a timestamp format from the first rows of the chosen time column (sent to this computer only).
    let formatSequence=0;
    async function detectTimeFormat(){
      const sequence=++formatSequence,chosen=file.files&&file.files[0],column=cols.time.value;
      setOptions(timeFormat,[['','ISO 8601 (2024-03-04T14:05:00Z)']]);timeFormat.value='';
      if(!chosen||format.value!=='csv'||!column)return;
      try{
        const q=new URLSearchParams({column,delimiter:delimiter.value});
        const r=await (await api('/v1/event-logs/timestamp-formats?'+q,{method:'POST',body:await headText(chosen,256*1024),headers:{'Content-Type':'text/plain'}})).json();
        if(sequence!==formatSequence)return;
        setOptions(timeFormat,[['','ISO 8601 (2024-03-04T14:05:00Z)'],...r.candidates.map(c=>[c.pattern,c.pattern+' \u2014 '+c.order+': '+c.first_value+' reads as '+c.first_value_reads_as])]);
        if(r.suggested){
          timeFormat.value=r.suggested;
          const pick=r.candidates.find(c=>c.pattern===r.suggested);
          if(!pick.has_offset&&!naiveOffset.value.trim())naiveOffset.value=localOffset();
          status('Times look like '+pick.first_value+', read as '+pick.first_value_reads_as+' with format '+r.suggested+(pick.has_offset?'.':' at offset '+naiveOffset.value+'. Change the format or offset if that is wrong.'));
        }else if(r.ambiguous)status('Times such as '+r.candidates[0].first_value+' fit both day-first and month-first formats. Choose the one your system uses under Timestamp format.');
        else if(r.samples&&!r.candidates.length&&r.iso_with_offset+r.iso_without_offset<r.samples)status('Some times in the first rows are not ISO 8601 and match no known format; those rows will be rejected.');
      }catch(_){/* detection is a convenience; the import still validates every row */}
    }
    cols.time.addEventListener('change',detectTimeFormat);
    // Offer the mapping recorded with an earlier import whose columns this file also has.
    const reuseRow=el('div',null,'row');reuseRow.id='owgEventLogReuse';reuseRow.hidden=true;mapping.appendChild(reuseRow);
    let reuseSequence=0;
    function applyMapping(m){
      cols.case.value=m.case_column;cols.activity.value=m.activity_column;cols.time.value=m.timestamp_column;cols.resource.value=m.resource_column||'';
      [...extras.options].forEach(o=>{o.selected=(m.attribute_columns||[]).includes(o.value);});
      naiveOffset.value=m.naive_timestamp_offset||'';
      if(m.timestamp_format&&![...timeFormat.options].some(o=>o.value===m.timestamp_format))timeFormat.appendChild(new Option(m.timestamp_format,m.timestamp_format));
      timeFormat.value=m.timestamp_format||'';
      columnsReady=true;consent.checked=false;refreshImport();
    }
    async function findReusableMapping(columns){
      const sequence=++reuseSequence;reuseRow.hidden=true;reuseRow.replaceChildren();
      try{
        const r=await (await api('/v1/event-logs/mapping-matches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({columns,delimiter:delimiter.value})})).json();
        if(sequence!==reuseSequence||!r.matches.length)return;
        // Appending to the selected log: its own mapping first.
        const match=r.matches.find(x=>x.log_id===state.logId)||r.matches[0];
        const use=button('Use the mapping from \u201c'+match.name+'\u201d');use.onclick=()=>{applyMapping(match.mapping);reuseRow.replaceChildren(el('span','Mapping from \u201c'+match.name+'\u201d applied. '+r.note+(match.missing_attribute_columns.length?' Not in this file: '+match.missing_attribute_columns.join(', ')+'.':''),'muted'));status('Mapping applied. Review the selected columns before storing.');};
        use.id='owgEventLogReuseMapping';
        reuseRow.append(use,el('span',' This file has the columns mapped when \u201c'+match.name+'\u201d was imported.','muted'));reuseRow.hidden=false;
      }catch(_){/* a convenience only */}
    }
    timeFormat.onchange=()=>{const fmt=timeFormat.value;if(fmt&&!fmt.includes('%z')&&!naiveOffset.value.trim())naiveOffset.value=localOffset();};
    consent.onchange=refreshImport;name.oninput=refreshImport;
    card.appendChild(agentRunsSection());
    importButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      const q=new URLSearchParams({format:format.value,name:name.value.trim()});
      if(format.value==='csv')csvMapping(q);
      importButton.disabled=true;status('Importing '+chosen.name+'…');
      try{
        const log=await (await api('/v1/event-logs/import?'+q,{method:'POST',body:chosen,headers:{'Content-Type':'application/octet-stream','X-OWG-Local-Storage-Confirmed':'true'}})).json();
        const done='Imported '+Number(log.events).toLocaleString()+' events'+(log.rejected_events?' ('+Number(log.rejected_events).toLocaleString()+' rejected: '+reasonText(log.quality&&log.quality.rejected_by_reason)+')':'')+'.'+relationshipText(log.object_relationships)+identityText(log.source_identity)+tieText(log.quality)+keptText(log.quality)+sameSourceText(log.same_source_as);
        consent.checked=false;keep.checked=false;file.value='';name.value='';
        // Report the import only once the new log is selected. Selecting it starts a new generation and
        // clears the results, so an analysis started before that would be dropped and never shown.
        await refreshLogs(log.log_id);
        status(done);
      }catch(error){status(error.message);}finally{refreshImport();}
    };
    sampleButton.onclick=async()=>{
      sampleButton.disabled=true;status('Creating the synthetic sample log…');
      try{
        const r=await (await api('/v1/event-logs/sample',{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}})).json();
        await refreshLogs(r.log.log_id);
        const caseType=document.getElementById('owgEventLogCaseType');
        if(r.suggested_case_notion&&[...caseType.options].some(o=>o.value===r.suggested_case_notion)){caseType.value=r.suggested_case_notion;state.caseType=r.suggested_case_notion;}
        else if([...caseType.options].some(o=>o.value==='order')){caseType.value='order';state.caseType='order';}
        await analyse();
        status((r.already_present?'Opened the sample log you already have. ':'Created the sample log: ')+'synthetic order-to-cash data with no real customers or people, analysed by order.');
      }catch(error){status(error.message);}finally{sampleButton.disabled=false;}
    };
    appendButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];const target=state.logs.find(l=>l.log_id===state.logId);if(!chosen||!consent.checked||!target)return;
      const q=new URLSearchParams({format:format.value});
      if(format.value==='csv')csvMapping(q);
      appendButton.disabled=true;status('Adding '+chosen.name+' to '+target.name+'…');
      try{
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(target.log_id)+'/append?'+q,{method:'POST',body:chosen,headers:{'Content-Type':'application/octet-stream','X-OWG-Local-Storage-Confirmed':'true'}})).json();
        consent.checked=false;file.value='';
        await refreshLogs(target.log_id);
        status('Added '+Number(r.added_events).toLocaleString()+' new events to '+target.name+'; skipped '+Number(r.duplicate_events_skipped).toLocaleString()+' already in the log'+(r.rejected_events?' and rejected '+Number(r.rejected_events).toLocaleString()+' ('+reasonText(r.quality&&r.quality.rejected_by_reason)+')':'')+'.'+relationshipText(r.log&&r.log.object_relationships)+identityText(r.source_identity)+tieText(r.quality)+keptText(r.quality)+sameSourceText(r.same_source_as));keep.checked=false;
      }catch(error){status(error.message);}finally{refreshImport();}
    };
    pick.onchange=()=>{selectLog(pick.value);refreshImport();};
    caseType.onchange=()=>{state.generation++;state.caseType=caseType.value;state.follows=[];state.attr={};state.caseActivity='';results.replaceChildren();};
    analyze.onclick=()=>analyse().catch(error=>status(error.message));
    const relationshipExportNote=()=>{const log=state.logs.find(l=>l.log_id===state.logId);return log&&log.object_relationships&&!['ocel','ocelxml'].includes(exportFormat.value)?' This format omits supplied object relationships; OCEL JSON/XML preserve the retained assertions.':'';};
    exportFormat.onchange=()=>{const note=relationshipExportNote();const review=exportFormat.value.endsWith('-review')?'Review CSV changes formula-like text and column names; use the regular export for re-import.':'';const attributes=exportFormat.value==='xes-attributes'?'Includes retained trace and event attribute values in the download.':'';if(review||attributes||note)status((review+attributes+note).trim());};
    exportButton.onclick=async()=>{
      try{
        status('Preparing export…');
        const response=await api(logPath('export',{format:exportFormat.value}));
        const url=URL.createObjectURL(await response.blob());
        const link=el('a');link.href=url;link.download=state.logId+'.'+({sourcecsv:'csv','csv-review':'review.csv','sourcecsv-review':'review.csv','xes-attributes':'xes',ocel:'jsonocel',ocelxml:'xmlocel'}[exportFormat.value]||exportFormat.value);
        document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        status('Export ready.'+relationshipExportNote());
      }catch(error){status(error.message);}
    };
    // Copies of the report saved on this computer, by hand or by a local schedule (Basic mode › Scheduled jobs).
    const savedBase=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/saved-reports';
    function savedChanges(c){
      if(!c||!c.available)return el('div','Save two copies to see what changed between them.','muted');
      const f=c.figures,sign=v=>v>0?'+':v<0?'−':'';
      const fmt={cases:v=>Number(v).toLocaleString(),events:v=>Number(v).toLocaleString(),activities:v=>Number(v).toLocaleString(),distinct_variants:v=>Number(v).toLocaleString(),
        median_case_seconds:duration,p90_case_seconds:duration,events_per_case:v=>Number(v).toFixed(2),repeat_rate:v=>(v*100).toFixed(1)+'%'};
      const label={cases:'Cases',events:'Events',activities:'Activities',distinct_variants:'Variants',median_case_seconds:'Median case time',p90_case_seconds:'90th percentile case time',events_per_case:'Events per case',repeat_rate:'Cases repeating an activity'};
      const rows=Object.keys(label).filter(k=>f[k]).map(k=>{const x=f[k],d=x.change;
        const change=d===null?'—':d===0?'no change':sign(d)+(k==='repeat_rate'?(Math.abs(d)*100).toFixed(1)+' pts':fmt[k](Math.abs(d)));
        return [label[k],x.older===null?'—':fmt[k](x.older),x.newer===null?'—':fmt[k](x.newer),change];});
      if(c.cost){const d=c.cost.change;rows.push(['Total cost ('+c.cost.currency+')',Number(c.cost.older).toLocaleString(undefined,{maximumFractionDigits:2}),Number(c.cost.newer).toLocaleString(undefined,{maximumFractionDigits:2}),d===0?'no change':sign(d)+Math.abs(d).toLocaleString(undefined,{maximumFractionDigits:2})]);}
      const box=el('div');box.id='owgEventLogSavedChanges';
      box.append(el('h4','Changes since the previous copy'),
        el('div','From '+String(c.older.generated_at).replace('T',' ').replace('Z',' UTC')+' to '+String(c.newer.generated_at).replace('T',' ').replace('Z',' UTC')+'.','muted'),
        table(['Figure','Previous','Newest','Change'],rows));
      const notes=[];
      if(c.comparison_scope==='different')notes.push('These copies use different case notions or case filters.');
      if(c.comparison_scope==='unknown')notes.push('An older copy has no saved case selection.');
      if(c.activities_added.length)notes.push('New activities: '+c.activities_added.slice(0,10).join(', ')+'.');
      if(c.activities_gone.length)notes.push('No longer seen: '+c.activities_gone.slice(0,10).join(', ')+'.');
      if(!c.top_variant.same_path&&c.top_variant.newer)notes.push('The most common variant is now '+c.top_variant.newer.path.join(' → ')+'.');
      if(c.largest_activity_changes.length)notes.push('Largest changes in cases per activity: '+c.largest_activity_changes.slice(0,5).map(a=>a.activity+' '+sign(a.change)+Math.abs(a.change)).join(', ')+'.');
      notes.forEach(n=>box.appendChild(el('div',n)));
      box.appendChild(el('div',c.interpretation,'muted'));
      return box;
    }
    async function showSaved(note){
      const logId=state.logId,base=savedBase();
      const r=await getJSON(base);
      let changes=null;try{changes=await getJSON('/v1/event-logs/'+encodeURIComponent(logId)+'/saved-report-changes');}catch(_){}
      if(state.logId!==logId)return;
      const saveNow=button('Save a copy now','secondary');saveNow.id='owgEventLogSaveCopy';
      saveNow.onclick=async()=>{
        if(state.logId!==logId)return;
        const scope={cohort:globalCohort()};
        if(state.caseType)scope.case_type=state.caseType;
        saveNow.disabled=true;
        try{
          const result=await (await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scope)})).json();
          if(state.logId===logId)await showSaved('Saved '+result.saved.name+'.');
        }catch(error){if(state.logId===logId)status(error.message);}
        finally{saveNow.disabled=false;}
      };
      const rows=r.reports.map(c=>{const get=button('Download','secondary');get.setAttribute('aria-label','Download saved report '+c.name);
        get.onclick=async()=>{try{const response=await api(savedBase()+'/'+encodeURIComponent(c.name));const url=URL.createObjectURL(await response.blob());const link=el('a');link.href=url;link.download=state.logId+'-'+c.name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){status(error.message);}};
        return [String(c.saved_at).replace('T',' ').replace('Z',' UTC'),Math.max(1,Math.round(c.bytes/1024))+' KB',get];});
      savedList.replaceChildren(el('h3','Saved report copies'),
        el('div','The newest '+r.keep+' copies of this log’s report are kept on this computer. Save a copy now uses the current case selection. To save one regularly, add an “Event log report copy” job under Scheduled jobs with this log’s ID: '+state.logId+'.','muted'),
        ...(note?[el('div',note)]:[]),
        rows.length?table(['Saved','Size',''],rows):el('div','No saved copies yet.','muted'),saveNow,savedChanges(changes));
      show(savedList,true);savedButton.setAttribute('aria-expanded','true');
    }
    savedButton.onclick=()=>{if(savedButton.getAttribute('aria-expanded')==='true'){show(savedList,false);savedButton.setAttribute('aria-expanded','false');return;}
      showSaved().catch(error=>status(error.message));};
    reportButton.onclick=async()=>{
      if(!state.logId)return;
      try{
        status('Preparing report…');
        const response=await api(logPath('report.html'));
        const url=URL.createObjectURL(await response.blob());
        const link=el('a');link.href=url;link.download=state.logId+'-report.html';
        document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        status('Report ready: a single HTML file with no scripts that opens offline.');
      }catch(error){status(error.message);}
    };
    renameButton.onclick=async()=>{
      const chosen=state.logs.find(l=>l.log_id===state.logId);if(!chosen)return;
      const name=window.prompt('New name for the event log "'+chosen.name+'":',chosen.name);
      if(name===null||name.trim()===chosen.name)return;
      try{const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
        await refreshLogs(state.logId);status('Renamed the event log to "'+r.name+'".');}
      catch(error){status(error.message);}
    };
    remove.onclick=async()=>{
      const chosen=state.logs.find(l=>l.log_id===state.logId);if(!chosen)return;
      if(!window.confirm('Delete the event log "'+chosen.name+'" from this computer? This cannot be undone.'))return;
      try{state.generation++;results.replaceChildren();await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/delete',{method:'POST'});await refreshLogs('');document.dispatchEvent(new CustomEvent('owg:eventlog-deleted'));status('Event log deleted.');}
      catch(error){status(error.message);}
    };
    refreshImport();
    refreshLogs('').catch(()=>{});
  }

  function salesforceSourceSection(stage,unstage){
    const box=section('Salesforce CaseHistory export');box.id='owgSalesforceSource';
    box.append(el('p','Prepare creation and status events from an authorized CSV export on this computer. Preparing does not store a log or contact Salesforce. Selected IDs and status labels are processed locally; history completeness is unknown.','muted'));
    const file=input('owgSalesforceFile','file');file.accept='.csv';
    const account=input('owgSalesforceAccount','text','Stable account namespace');account.maxLength=128;
    const cases=el('textarea');cases.id='owgSalesforceCases';cases.rows=3;cases.maxLength=700;cases.placeholder='One exact CaseId per line (up to 32)';
    const consent=input('owgSalesforceConsent','checkbox');
    const prepare=button('Prepare selected cases','secondary');prepare.id='owgSalesforcePrepare';prepare.disabled=true;
    const out=el('div');out.id='owgSalesforceReview';out.setAttribute('role','status');out.setAttribute('aria-live','polite');
    let generation=0;
    const invalidate=()=>{generation++;out.replaceChildren();consent.checked=false;unstage();refresh();};
    function refresh(){prepare.disabled=!(file.files?.[0]&&account.value.trim()&&cases.value.trim()&&consent.checked);}
    file.onchange=invalidate;account.oninput=invalidate;cases.oninput=invalidate;
    consent.onchange=()=>{if(!consent.checked){generation++;out.replaceChildren();unstage();}refresh();};
    prepare.onclick=async()=>{
      const selected=file.files?.[0];if(!selected||!consent.checked)return;
      const current=++generation;prepare.disabled=true;
      try{
        if(selected.size>16*1024*1024)throw new Error('Choose an export no larger than 16 MiB.');
        let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(await selected.arrayBuffer());}
        catch(_){throw new Error('The export must be valid UTF-8.');}
        const spec={account:account.value.trim(),cases:cases.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),csv:text,confirm_processing:true};
        if(current!==generation)return;
        const result=await (await api('/v1/event-logs/prepare-salesforce',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(current!==generation)return;
        const r=result.report;
        const reviewed=input('owgSalesforceReviewed','checkbox');
        const use=button('Use reviewed events in import form','secondary');use.id='owgSalesforceStage';use.disabled=true;
        reviewed.onchange=()=>{use.disabled=!reviewed.checked;};
        use.onclick=async()=>{
          if(current!==generation||!reviewed.checked)return;
          use.disabled=true;
          try{await stage(result.csv,result.namespace,()=>current===generation);}
          catch(error){if(current===generation)out.appendChild(el('p',error.message,'muted'));}
        };
        out.replaceChildren(table(['Export review','Count'],[['Input rows',r.input_rows],['Selected cases',r.selected_cases],['Represented cases',r.represented_cases],['Projected events',r.events],['Unselected rows',r.unselected_rows],['Excluded fields',r.excluded_fields],['Duplicate rows',r.duplicate_rows],['Additional events at tied timestamps',r.tied_timestamp_events]]),
          el('p','Showing '+result.preview.length+' of '+r.events+' projected events. Case keys are derived from your declared account and are not anonymization. Tied timestamps use deterministic ID order, not confirmed business order.','muted'),
          table(['Derived case key','Activity','Timestamp'],result.preview.map(x=>[x.case_id,x.activity,x.timestamp])),
          el('p','Only creation and status labels are included. Missing history is unknown. The source namespace is based on your account declaration; use the same declaration for later manual exports. Live connector-managed logs use a different scope. No events have been stored.','muted'),
          field('I reviewed this projection and want to stage it for import',reviewed),use);
      }catch(error){if(current===generation)out.replaceChildren(el('p',error.message,'muted'));}
      finally{if(current===generation)refresh();}
    };
    box.append(field('CaseHistory CSV export',file),field('Account namespace',account),field('Selected case IDs',cases),field('Process this selected export locally',consent),prepare,out);
    return box;
  }

  function jiraSourceSection(){
    const box=section('Retained Jira histories');box.id='owgJiraSource';box.style.overflowWrap='anywhere';
    box.append(el('p','Manually read 1–8 selected Jira issues into a local event log. Use observed references, or explicitly authorized issue keys without desktop capture. Each run rescans the bounded history. Status labels, native issue IDs and source event identities are retained; authors, comments, descriptions and assignee values are excluded. Pagination is not proof of complete history. Scheduled reads are optional and require separate confirmation; they pause when recording is paused. No source writes.','muted'));
    const basis=select('owgJiraSourceBasis',[['observed_refs','Observed Jira references'],['native_issue_keys','Selected Jira issue keys (separate admin opt-in)']]);
    const refs=el('textarea');refs.id='owgJiraSourceRefs';refs.rows=3;refs.maxLength=400;refs.placeholder='One owg:r: reference per line';refs.style.maxWidth='100%';
    const read=input('owgJiraSourceRead','checkbox'),retain=input('owgJiraSourceRetain','checkbox'),labels=input('owgJiraSourceLabels','checkbox');
    const create=button('Create selected Jira profile');create.id='owgJiraSourceCreate';create.disabled=true;
    const refresh=button('Refresh Jira source status','secondary');refresh.id='owgJiraSourceRefresh';
    const message=el('p');message.id='owgJiraSourceStatus';message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgJiraSourceProfiles';
    const scopeField=field('Observed Jira references',refs);
    box.append(field('Selection basis',basis),scopeField,el('p','Profile selection identifiers remain until disconnect. Imported events and their checkpoint are erased when you delete the log.','muted'),field('I authorize these selected source reads',read),field('I confirm local retention until I delete the log',retain),field('I confirm retention of business status labels and source identities',labels),create,refresh,message,out);
    let version=0,capabilityVersion=0,creating=false,nativeReady=false;
    const changed=()=>{version++;read.checked=false;retain.checked=false;labels.checked=false;sync();};
    const sync=()=>{create.disabled=creating||!refs.value.trim()||!read.checked||!retain.checked||!labels.checked||(basis.value==='native_issue_keys'&&!nativeReady);};
    basis.addEventListener('change',()=>{refs.value='';scopeField.querySelector('label').textContent=basis.value==='native_issue_keys'?'Exact Jira issue keys':'Observed Jira references';refs.placeholder=basis.value==='native_issue_keys'?'One exact key per line, e.g. OWG-123':'One owg:r: reference per line';changed();if(basis.value==='native_issue_keys'&&!nativeReady)message.textContent='An administrator must separately authorize native issue-key ingestion.';});
    refs.addEventListener('input',changed);for(const control of [read,retain,labels])control.addEventListener('change',sync);
    const credentialError=value=>({
      jira_configuration_changed_recreate_profile:'The configured Jira credential changed. Verify the renewed credential to keep this profile; a different account or tenant requires a new profile.',
      jira_credential_account_mismatch:'The configured credential could not be matched to this profile’s previously confirmed Jira account. The existing history and binding were preserved.',
      jira_credential_tenant_change_refused:'The configured Jira tenant changed. Create a separate profile for that tenant.',
      jira_profile_revision_conflict:'This profile changed. Refresh its status before checking the credential again.',
      jira_prior_account_and_dataset_required:'Retain a history with the original credential before renewing this profile.'
    })[value]||value;
    const endpoint='/v1/event-log-sources/jira';
    const send=async(path,body,method='POST')=>(await api(endpoint+path,{method,...(body===null?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})})).json();
    async function load(){
      const request=++version,capability=++capabilityVersion;
      try{
        const result=await getJSON(endpoint);
        if(capability===capabilityVersion){nativeReady=result.native_case_selection_configured===true;sync();}
        if(request!==version)return;
        message.textContent=result.configured&&basis.value==='native_issue_keys'&&!nativeReady?'An administrator must separately authorize native issue-key ingestion.':result.configured?'Profiles can be read manually or scheduled separately. A successful read is historical coverage, not verified completeness.':'An administrator must configure the separate retained Jira opt-in and OAuth read scopes. No credentials are entered here.';
        out.replaceChildren();
        for(const profile of result.profiles){
          const item=el('div',null,'card');item.dataset.profileId=profile.profile_id;
          item.append(el('h4',profile.host+' · '+(profile.selected_case_count??profile.resource_refs.length)+' selected issues'),el('p','Status: '+profile.status+(profile.error?' · '+profile.error:'')+'. Last successful read: '+(profile.last_success_at?new Date(profile.last_success_at*1000).toISOString():'none')+'. Source completeness unverified.'));
          item.append(el('p',profile.selection_basis==='native_issue_keys'?'Selection: exact Jira keys · '+profile.selected_issue_keys.join(', '):'Selection: observed references · '+profile.resource_refs.join(', ')));
          item.append(el('p','Profile ID: '+profile.profile_id+'. '+(profile.scheduled_runs_enabled?'An enabled schedule exists.':'No enabled schedule. A paused schedule may still exist.')+' Manage schedules in Privacy → Scheduled jobs.','muted'));
          if(profile.checkpoint)item.append(el('p',profile.checkpoint.events+' retained projected events; '+profile.checkpoint.histories+' histories in the last successful bounded rescan.'));
          const row=el('div',null,'row');
          const run=button('Read and retain selected histories');run.disabled=!profile.enabled||['running','checking_credentials'].includes(profile.status)||Date.now()<profile.retry_after*1000||(profile.selection_basis==='native_issue_keys'&&!nativeReady);
          const toggle=button(profile.enabled?'Disable profile':'Enable profile','secondary'),disconnect=button('Disconnect profile','secondary'),verify=button('Verify renewed credential','secondary'),schedule=button('Schedule daily reads','secondary');
          schedule.disabled=!profile.enabled||profile.scheduled_runs_enabled||profile.status==='dataset_deleted';
          verify.disabled=!profile.log_id||profile.status==='dataset_deleted'||profile.status==='checking_credentials'||Date.now()<profile.retry_after*1000||(profile.selection_basis==='native_issue_keys'&&!nativeReady);
          if(profile.credential_check_status)item.append(el('p','Credential check: '+profile.credential_check_status+(profile.credential_check_error?' · '+credentialError(profile.credential_check_error):'')+'. Last verified: '+(profile.credential_verified_at?new Date(profile.credential_verified_at*1000).toISOString():'none')+'. History freshness and profile enablement are unchanged.'));
          // Lifecycle controls stay available while reading; generation fencing
          // prevents a held old run response from replacing newer disable state.
          const action=async(path,body,method,selectRetainedLog=true)=>{
            const request=++version;run.disabled=true;verify.disabled=true;
            try{const result=await send(path,body,method);if(request!==version)return;
              if(selectRetainedLog&&result.log_id)await refreshLogs(result.log_id);
              if(request!==version)return;
              await load();
            }catch(error){if(request===version){const refreshed=version+1;await load();if(version===refreshed)message.textContent=credentialError(error.message);}}
          };
          run.onclick=()=>action('/'+profile.profile_id+'/run',{confirm_reads:true});
          toggle.onclick=()=>action('/'+profile.profile_id+'/enabled',{enabled:!profile.enabled,confirmed:true});
          disconnect.onclick=()=>{if(window.confirm('Disconnect this Jira profile? Its imported log stays on this computer until you delete it.'))action('/'+profile.profile_id,null,'DELETE');};
          verify.onclick=()=>{if(window.confirm('Verify the configured Jira credential against this profile’s previously confirmed account and tenant? This checks account permissions, preserves the selected cases and history, and does not enable a paused profile.'))action('/'+profile.profile_id+'/credential',{revision:profile.revision,confirm_account_check:true},undefined,false);};
          schedule.onclick=async()=>{
            if(!window.confirm('Schedule daily reads of this profile’s selected Jira histories while OpenWorkGraph is running and recording? Reads pause when recording is paused. You can pause or remove the schedule under Privacy → Scheduled jobs.'))return;
            const request=++version;schedule.disabled=true;
            try{
              await api('/v1/schedules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'retained_jira_profile',target_id:profile.profile_id,interval_minutes:1440,confirm_scheduled_runs:true})});
              if(request!==version)return;await load();
            }catch(error){
              if(request!==version)return;
              schedule.disabled=false;message.textContent=error.message.includes('already has a schedule')?'A schedule already exists for this profile. Manage or resume it under Privacy → Scheduled jobs.':error.message;
            }
          };
          toggle.disabled=profile.status==='dataset_deleted';
          row.append(run,verify,schedule,toggle,disconnect);
          if(profile.log_id&&profile.status!=='dataset_deleted'){const choose=button('Open retained log','secondary');choose.onclick=()=>refreshLogs(profile.log_id).catch(error=>{message.textContent=error.message;});row.append(choose);}
          item.append(row);out.append(item);
        }
      }catch(error){if(request===version)message.textContent=error.message;}
    }
    create.onclick=async()=>{
      const request=++version;creating=true;sync();
      try{const selected=refs.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);await send('',{[basis.value==='native_issue_keys'?'issue_keys':'resource_refs']:selected,confirm_reads:read.checked,confirm_retention:retain.checked,confirm_status_labels:labels.checked});
        if(request!==version)return;read.checked=false;retain.checked=false;labels.checked=false;await load();
      }catch(error){if(request===version)message.textContent=error.message;}
      finally{creating=false;sync();}
    };
    document.addEventListener('owg:eventlog-deleted',()=>load());
    refresh.onclick=()=>load();load();return box;
  }

  // Agent mining: observed AI agent runs (structure only) become an event log like any other.
  function agentRunsSection(){
    const box=section('AI agent runs as a log');box.id='owgAgentRuns';
    box.appendChild(el('div','Analyse how your AI agents work, like any process: each observed agent run becomes a case and each step (tool calls by tool name, failures, approvals, handoffs) an event. Outcome, agent, model and token totals become case attributes. No prompt, tool argument, result, file path or message text is included.','muted'));
    const period=select('owgAgentRunsPeriod',[['7','Last 7 days'],['30','Last 30 days'],['90','Last 90 days'],['','All retained runs']]);period.value='30';
    const runName=input('owgAgentRunsName','text');runName.value='AI agent runs';runName.maxLength=120;
    const models=input('owgAgentRunsModelCalls','checkbox');
    const modelsLabel=el('label',' Include model calls as steps');modelsLabel.prepend(models);
    const store=input('owgAgentRunsConsent','checkbox');
    const storeLabel=el('label',' Store this log on this computer until I delete it');storeLabel.prepend(store);
    const go=button('Import AI agent runs','secondary');go.id='owgAgentRunsImport';go.disabled=true;
    const out=el('div');out.id='owgAgentRunsStatus';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Runs from',period),field('Log name',runName));
    box.append(row,modelsLabel,storeLabel,go,out);
    const ready=()=>{go.disabled=!(store.checked&&runName.value.trim());};
    store.addEventListener('change',ready);runName.addEventListener('input',ready);
    go.onclick=async()=>{
      const spec={name:runName.value.trim(),include_model_calls:models.checked};
      if(period.value)spec.since=new Date(Date.now()-Number(period.value)*86400000).toISOString();
      go.disabled=true;out.textContent='Reading observed agent runs\u2026';
      try{
        const r=await (await api('/v1/event-logs/import-agent-runs',{method:'POST',headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'},body:JSON.stringify(spec)})).json();
        out.textContent='Imported '+Number(r.runs).toLocaleString()+' agent runs with '+Number(r.events).toLocaleString()+' steps as \u201c'+r.name+'\u201d.'+(r.runs_with_steps_truncated?' '+r.runs_with_steps_truncated+' long run(s) were cut at '+r.max_steps_per_run+' steps.':'');
        store.checked=false;
        await refreshLogs(r.log_id);
      }catch(error){out.textContent=error.message;}finally{ready();}
    };
    return box;
  }

  async function refreshLogs(selectId, preferredCaseType){
    const refresh=++state.refresh;
    const data=await getJSON('/v1/event-logs');
    if(refresh!==state.refresh)return;
    state.logs=Array.isArray(data.logs)?data.logs:[];
    const pick=document.getElementById('owgEventLogPick');
    setOptions(pick,state.logs.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']));
    show(document.getElementById('owgEventLogManage'),state.logs.length>0);
    const next=state.logs.some(l=>l.log_id===selectId)?selectId:(state.logs[0]?state.logs[0].log_id:'');
    if(next){pick.value=next;await selectLog(next,next===selectId?preferredCaseType:undefined);}else{state.logId='';state.objectTypes=[];}
  }

  // About this log: the owner's own description, kept with the log.
  function descriptionSection(){
    const box=section('About this log');box.id='owgLogDescription';
    const text=document.createElement('textarea');text.id='owgLogDescriptionText';text.rows=3;text.maxLength=2000;
    text.placeholder='What is this log, where did it come from and what is it for?';
    const save=button('Save description','secondary');save.id='owgLogDescriptionSave';
    const statusLine=el('div',null,'muted');statusLine.id='owgLogDescriptionStatus';statusLine.setAttribute('role','status');
    box.append(field('Description',text),save,statusLine);
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/description',generation=state.generation;
    const show=r=>{if(generation!==state.generation)return;text.value=r.description;
      statusLine.textContent=r.updated_at?'Saved '+r.updated_at.slice(0,16).replace('T',' ')+' UTC. Only you see it; it never changes the events.':'No description yet. Only you see it; it never changes the events.';};
    save.onclick=async()=>{save.disabled=true;
      try{show(await (await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description:text.value})})).json());}
      catch(error){statusLine.textContent=error.message;}
      finally{save.disabled=false;}};
    getJSON(base).then(show).catch(error=>{statusLine.textContent=error.message;});
    return box;
  }

  // Save the cases matching a filter as a new saved log, to analyse or compare a slice on its own.
  function deriveSection(activityNames){
    const box=section('Save or export matching cases');box.id='owgEventLogDerive';
    box.appendChild(el('div','Save matching cases as a new log or download their original events in CSV or XES. Saved copies start without the source log’s activity mapping, saved filters, signals and quality reports.','muted'));
    const filter=cohortFields('owgDeriveCohort','Cases to include',activityNames);
    const name=input('owgDeriveName','text');name.maxLength=120;name.autocomplete='off';
    const current=state.logs.find(l=>l.log_id===state.logId);name.value=((current&&current.name)||'Log')+' (subset)';
    const run=button('Save as new log','secondary');run.id='owgDeriveRun';
    const formats=select('owgDeriveExportFormat',[['csv','CSV (exact interchange)'],['csv-review','CSV for spreadsheet review'],
      ['xes','XES'],['xes-attributes','XES with retained attributes']]);
    const download=button('Download matching cases','secondary');download.id='owgDeriveExport';
    const formatNote=el('p',null,'muted');
    function describeFormat(){
      formatNote.textContent={
        'csv':'Includes case IDs, activity names, timestamps and resources.',
        'csv-review':'Changes formula-like cells for spreadsheet review. Use exact CSV for re-import.',
        'xes':'Includes one trace per matching case with activity names, timestamps and resources.',
        'xes-attributes':'Also includes retained trace and event attribute values. Use OCEL for timed object attributes and relationships.'
      }[formats.value];
    }
    formats.onchange=describeFormat;describeFormat();
    const out=el('div');out.id='owgDeriveResult';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('New log name',name),run);
    // Optional time window: keep only the events inside it (trim to timeframe).
    const windowFrom=input('owgDeriveEventsFrom','datetime-local'),windowBefore=input('owgDeriveEventsBefore','datetime-local');
    const windowRow=el('div',null,'row');windowRow.style.alignItems='end';
    windowRow.append(field('Only events from (optional, local time)',windowFrom),field('Only events before (optional)',windowBefore));
    const exportRow=el('div',null,'row');exportRow.style.alignItems='end';
    exportRow.append(field('Matching case export format',formats),download);
    box.append(filter.node,windowRow,row,exportRow,formatNote,out);
    const generation=state.generation;
    function selectedCases(missingFilter){
      const cohort=filter.read();delete cohort.label;
      if(filterOn())applyActiveCohort(cohort);
      if(!Object.keys(cohort).length)throw new Error(missingFilter);
      return state.caseType?{cohort,case_type:state.caseType}:{cohort};
    }
    download.onclick=async()=>{
      const logId=state.logId,format=formats.value;
      const extension=format==='csv-review'?'review.csv':format.startsWith('xes')?'xes':'csv';
      download.disabled=true;
      try{
        const spec=selectedCases('Choose which cases to export.');
        out.textContent='Preparing matching cases…';
        const response=await api('/v1/event-logs/'+encodeURIComponent(logId)+'/export/matching-cases?'+new URLSearchParams({format}),{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});
        const blob=await response.blob();
        if(generation!==state.generation||!box.isConnected)return;
        const url=URL.createObjectURL(blob);
        const link=el('a');link.href=url;link.download=logId+'-matching-cases.'+extension;
        document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
        out.textContent='Matching cases exported.';
      }catch(error){if(generation===state.generation)out.textContent=error.message;}
      finally{download.disabled=false;}
    };
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const window={};
        if(windowFrom.value)window.events_from=new Date(windowFrom.value).toISOString();
        if(windowBefore.value)window.events_before=new Date(windowBefore.value).toISOString();
        let cases;
        try{cases=selectedCases('Choose which cases to copy. Or set a time window.');}
        catch(error){if(!Object.keys(window).length)throw error;cases=state.caseType?{cohort:{},case_type:state.caseType}:{cohort:{}};}
        const spec={...cases,...window,name:name.value.trim()};
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/derive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        // Add the new log to the picker without leaving the current analysis.
        const data=await getJSON('/v1/event-logs');state.logs=Array.isArray(data.logs)?data.logs:state.logs;
        const pick=document.getElementById('owgEventLogPick');setOptions(pick,state.logs.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']));pick.value=state.logId;
        const open=button('Open '+r.log.name,'secondary');open.id='owgDeriveOpen';
        open.onclick=()=>refreshLogs(r.log.log_id,r.derived_from.case_notion).then(()=>status('Opened '+r.log.name+'. Select Analyse to explore it.')).catch(error=>status(error.message));
        const trimmed=r.time_window?' Only events in the time window were kept: '+Number(r.time_window.cases_with_events_in_window).toLocaleString()+' cases have events in it; '+Number(r.time_window.events_outside_left_out).toLocaleString()+' events outside it were left out.':'';
        out.replaceChildren(el('div','Saved '+r.log.name+': '+Number(r.derived_from.cases_matched).toLocaleString()+' of '+Number(r.derived_from.cases_in_source).toLocaleString()+' cases, '+Number(r.copied.events).toLocaleString()+' events.'+trimmed),open);
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  // Combine this log with other saved logs into a new log (one export per region, or one system per step).
  function combineSection(){
    const box=section('Combine with other saved logs');box.id='owgCombine';
    const others=state.logs.filter(l=>l.log_id!==state.logId);
    const current=state.logs.find(l=>l.log_id===state.logId);
    box.appendChild(el('div','Copies this log and the logs you tick into a new saved log. The logs themselves are unchanged. Every event gets a source_log attribute naming the log it came from.','muted'));
    if(!others.length){box.appendChild(el('div','Import another log to combine it with this one.','muted'));return box;}
    const list=el('fieldset');list.id='owgCombineLogs';list.append(el('legend','Logs to add (up to 9)'));
    const boxes=others.map((l,i)=>{const c=input('owgCombineLog'+i,'checkbox');c.value=l.log_id;const label=el('label');label.style.cssText='display:flex;gap:6px;align-items:center';
      label.append(c,document.createTextNode(' '+l.name+' · '+Number(l.events).toLocaleString()+' events'));list.append(label);return c;});
    const cases=select('owgCombineCases',[['shared','Same ID is the same case (join systems of one process)'],['separate','Keep each log’s cases apart (one export per region or period)']]);
    const name=input('owgCombineName','text');name.maxLength=120;name.autocomplete='off';name.value=((current&&current.name)||'Log')+' (combined)';
    const run=button('Save combined log','secondary');run.id='owgCombineRun';
    const out=el('div');out.id='owgCombineResult';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Case IDs',cases),field('New log name',name),run);
    box.append(list,row,out);
    const generation=state.generation;
    run.onclick=async()=>{
      const chosen=boxes.filter(c=>c.checked).map(c=>c.value);
      if(!chosen.length){out.replaceChildren(el('div','Tick at least one log to add.','muted'));return;}
      run.disabled=true;
      try{
        const spec={name:name.value.trim(),log_ids:[state.logId,...chosen],cases:cases.value};
        const r=await (await api('/v1/event-logs/combine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const data=await getJSON('/v1/event-logs');state.logs=Array.isArray(data.logs)?data.logs:state.logs;
        const pick=document.getElementById('owgEventLogPick');setOptions(pick,state.logs.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']));pick.value=state.logId;
        const open=button('Open '+r.log.name,'secondary');open.id='owgCombineOpen';
        open.onclick=()=>refreshLogs(r.log.log_id).then(()=>status('Opened '+r.log.name+'. Select Analyse to explore it.')).catch(error=>status(error.message));
        const shared=r.cases==='shared'&&r.ids_in_several_sources?' '+Number(r.ids_in_several_sources).toLocaleString()+' IDs appear in more than one log and were joined'+(r.object_type_conflicts?' ('+r.object_type_conflicts+' with different types; the first log’s type was kept)':'')+'.':'';
        out.replaceChildren(el('div','Saved '+r.log.name+': '+Number(r.copied.events).toLocaleString()+' events from '+r.combined_from.length+' logs.'+shared),open);
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  async function selectLog(logId,preferredCaseType){
    const savedPanel=document.getElementById('owgEventLogSavedList');if(savedPanel){show(savedPanel,false);document.getElementById('owgEventLogSavedReports')?.setAttribute('aria-expanded','false');}
    const generation=++state.generation;state.logId=logId;state.caseType='';state.follows=[];state.attr={};state.caseActivity='';state.objectTypes=[];
    document.getElementById('owgEventLogResults').replaceChildren();
    const types=await getJSON('/v1/event-logs/'+encodeURIComponent(logId)+'/object-types');
    if(generation!==state.generation)return;
    state.objectTypes=types.object_types||[];
    const options=(types.object_types||[]).map(t=>[t.object_type,t.object_type+' ('+Number(t.objects).toLocaleString()+')']);
    const caseType=document.getElementById('owgEventLogCaseType');
    setOptions(caseType,options);
    const available=options.map(([type])=>type);
    state.caseType=available.includes(preferredCaseType)?preferredCaseType:
      (available.includes('case')?'case':(available[0]||''));
    caseType.value=state.caseType;
  }

  function anonymizedExportSection(){
    const box=section('Anonymized export');box.id='owgAnonymized';
    box.appendChild(el('div','Before sharing a log, check which cases and fields the CSV will contain. Rare paths are left out, case IDs are replaced, and attributes are removed.','muted'));
    const minPick=select('owgAnonymizedMin',[['5','At least 5 cases per path'],['2','At least 2'],['10','At least 10'],['20','At least 20'],['50','At least 50']]);
    const timePick=select('owgAnonymizedTime',[['relative','Start of the original month, gaps kept'],['elapsed','Same synthetic start for every case, gaps kept'],['sequence','Event order only, times one second apart'],['day','Date of each event only'],['exact','Exact timestamps']]);
    const resourcePick=select('owgAnonymizedResources',[['drop','Leave resources out'],['pseudonymize','Replace resources with Resource 01, 02, …']]);
    const activityPick=select('owgAnonymizedActivities',[['keep','Keep activity names'],['pseudonymize','Replace activity names with codes']]);
    const check=button('Check what is kept','secondary');check.id='owgAnonymizedCheck';
    const get=button('Download anonymized CSV','secondary');get.id='owgAnonymizedDownload';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Smallest group',minPick),field('Time',timePick),field('Resources',resourcePick),field('Activities',activityPick),check,get);
    const statusLine=el('div',null,'muted');statusLine.id='owgAnonymizedStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgAnonymizedResults';
    box.append(row,statusLine,out);
    let revision=0;
    const generation=state.generation;
    const logId=state.logId;
    const params=()=>({min_cases:minPick.value,time_mode:timePick.value,resource_mode:resourcePick.value,activity_mode:activityPick.value});
    const setBusy=busy=>{check.disabled=busy;get.disabled=busy;};
    for(const control of [minPick,timePick,resourcePick,activityPick])control.addEventListener('change',()=>{
      revision++;statusLine.textContent='';out.replaceChildren();setBusy(false);
    });
    async function withSelection(message,work){
      const ticket=++revision;
      const current=()=>ticket===revision&&generation===state.generation&&box.isConnected;
      setBusy(true);statusLine.textContent=message;
      try{await work(current);}
      catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())setBusy(false);}
    }
    check.onclick=()=>withSelection('Checking…',async current=>{
      out.replaceChildren();
      const r=await getJSON(logPath('anonymized-export',params()));
      if(!current())return;
      statusLine.textContent=Number(r.cases_kept).toLocaleString()+' of '+Number(r.cases_total).toLocaleString()+' cases would be exported ('+Math.round(r.cases_kept_share*1000)/10+'%); '+Number(r.cases_suppressed).toLocaleString()+' cases on '+Number(r.variants_suppressed).toLocaleString()+' rare paths are left out.';
      const rows=[['Paths',Number(r.variants_kept).toLocaleString()+' of '+Number(r.variants_total).toLocaleString()],
        ['Events',Number(r.events_kept).toLocaleString()],['Columns',r.columns.join(', ')],
        ['Activity names',r.activity_mode==='pseudonymize'?'Replaced with export-local codes':'Kept'],
        ['Activities left out',r.activities_removed_total?r.activities_removed.join(', ')+(r.activities_removed_total>r.activities_removed.length?' …':''):'none']];
      if(r.synthetic_start)rows.push(['Synthetic case start',r.synthetic_start]);
      out.append(table(['Kept','Value'],rows),el('div',r.caveat,'muted'));
    });
    get.onclick=()=>withSelection('Preparing download…',async current=>{
      const response=await api(logPath('anonymized-export.csv',params()));
      if(!current())return;
      const blob=await response.blob();
      if(!current())return;
      const link=document.createElement('a');link.href=URL.createObjectURL(blob);
      link.download=logId+'-anonymized.csv';document.body.appendChild(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(link.href),1000);
      statusLine.textContent='Downloaded '+(response.headers.get('X-OWG-Anonymized')||'').replace(/; /g,', ')+'. Review the file before sharing it.';
    });
    return box;
  }

  function activityMappingSection(){
    const box=section('Group activities');box.id='owgActivityMappings';
    box.appendChild(el('div','Group low-level activities under one higher-level name for every analysis of this log. The stored events are never changed, and saved signals and object views keep the original activities.','muted'));
    const host=el('div');const groups=[];const draft=el('ul');draft.id='owgActivityMappingDraft';
    const name=input('owgActivityMappingName','text','For example: Data entry steps');name.maxLength=200;
    const label=input('owgActivityGroupLabel','text','Higher-level name');label.maxLength=200;
    const pick=document.createElement('select');pick.id='owgActivityGroupPick';pick.multiple=true;pick.size=6;
    const collapse=input('owgActivityMappingCollapse','checkbox');
    const addGroup=button('Add group','secondary');addGroup.id='owgActivityGroupAdd';
    const save=button('Save mapping','secondary');save.id='owgActivityMappingSave';
    const msg=el('div',null,'muted');msg.id='owgActivityMappingStatus';msg.setAttribute('role','status');msg.setAttribute('aria-live','polite');
    const generation=state.generation;
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/activity-mappings';
    const post=async(path,body)=>(await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    const drawDraft=()=>draft.replaceChildren(...groups.map((g,i)=>{const li=el('li',g.label+' ← '+g.activities.join(', ')+' ');const rm=button('Remove','secondary');rm.setAttribute('aria-label','Remove group '+g.label);rm.onclick=()=>{groups.splice(i,1);drawDraft();};li.appendChild(rm);return li;}));
    const render=r=>{
      if(generation!==state.generation)return;
      pick.replaceChildren(...r.original_activities.map(a=>{const o=el('option',a.activity+' ('+Number(a.events).toLocaleString()+')');o.value=a.activity;return o;}));
      const list=el('ul');list.id='owgActivityMappingList';
      r.mappings.forEach(m=>{
        const li=el('li',m.name+' · '+m.groups.length+' groups · '+m.mapped_activities+' activities'+(m.collapse_repeats?' · repeats collapsed':'')+(m.applied?' · applied ':' '));
        const toggle=button(m.applied?'Stop applying':'Apply to analyses','secondary');toggle.setAttribute('aria-label',(m.applied?'Stop applying ':'Apply ')+m.name);
        toggle.onclick=async()=>{try{await post(base()+'/applied',{mapping_id:m.applied?null:m.mapping_id});await analyse();}catch(e){msg.textContent=e.message;}};
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete mapping '+m.name);
        del.onclick=async()=>{try{await post(base()+'/'+encodeURIComponent(m.mapping_id)+'/delete',{});if(m.applied)await analyse();else render({...r,mappings:(await (await api(base())).json()).mappings});}catch(e){msg.textContent=e.message;}};
        li.append(toggle,del);list.appendChild(li);
      });
      host.replaceChildren(r.mappings.length?list:el('div','No saved mappings.','muted'));
    };
    addGroup.onclick=()=>{
      const chosen=[...pick.selectedOptions].map(o=>o.value);
      if(!label.value.trim()||!chosen.length){msg.textContent='Name the group and choose at least one activity.';return;}
      groups.push({label:label.value.trim(),activities:chosen});label.value='';pick.selectedIndex=-1;drawDraft();msg.textContent='';
    };
    save.onclick=async()=>{
      if(!groups.length){msg.textContent='Add at least one group.';return;}
      try{const m=await post(base(),{name:name.value.trim()||'Activity mapping',groups,collapse_repeats:collapse.checked});
        groups.length=0;drawDraft();msg.textContent='Saved “'+m.name+'”. Apply it to see it in every analysis.'+(m.activities_not_in_log.length?' Not in this log: '+m.activities_not_in_log.join(', ')+'.':'');
        render(await (await api(base())).json());}
      catch(e){msg.textContent=e.message;}
    };
    const row=el('div',null,'row');row.style.alignItems='end';
    const collapseWrap=el('div');const collapseLabel=el('label',' Collapse consecutive repeats into the first');collapseLabel.htmlFor=collapse.id;collapseWrap.append(collapse,collapseLabel);
    row.append(field('Mapping name',name),field('Group name',label),field('Activities in the group',pick),addGroup);
    // Suggested groups: spellings of probably the same activity (case, spacing, punctuation, typos).
    const suggest=button('Suggest groups of similar labels','secondary');suggest.id='owgActivitySuggest';
    const suggestions=el('div');suggestions.id='owgActivitySuggestions';suggestions.setAttribute('role','status');suggestions.setAttribute('aria-live','polite');
    suggest.onclick=async()=>{
      suggest.disabled=true;suggestions.replaceChildren(el('div','Comparing activity names…','muted'));
      try{
        const r=await getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/activity-label-suggestions');
        if(generation!==state.generation)return;
        if(!r.groups.length){suggestions.replaceChildren(el('div','No activity names look like spellings of the same activity.','muted'));return;}
        const list=el('ul');
        r.groups.forEach(g=>list.appendChild(el('li',g.suggested_label+' ← '+g.labels.map(l=>l.activity+' ('+Number(l.events).toLocaleString()+')').join(', ')+' · '+g.reason)));
        const use=button('Add these groups to the mapping','secondary');use.id='owgActivitySuggestUse';
        use.onclick=()=>{
          const taken=new Set(groups.flatMap(g=>g.activities));
          let added=0;
          r.groups.forEach(g=>{const acts=g.labels.map(l=>l.activity).filter(a=>!taken.has(a));if(acts.length<2)return;
            acts.forEach(a=>taken.add(a));groups.push({label:g.suggested_label,activities:acts});added++;});
          if(!name.value.trim())name.value='Similar labels';
          drawDraft();msg.textContent=added?'Added '+added+' groups to the draft. Review them, then save the mapping.':'Every suggested label is already in the draft.';
        };
        suggestions.replaceChildren(el('div',r.groups.length+' groups of names that may be the same activity'+(r.groups_total>r.groups.length?' (showing '+r.groups.length+' of '+r.groups_total+')':'')+'. '+r.caveat,'muted'),list,use);
      }catch(e){suggestions.replaceChildren(el('div',e.message,'muted'));}
      finally{suggest.disabled=false;}};
    box.append(host,row,suggest,suggestions,draft,collapseWrap,save,msg);
    api(base()).then(r=>r.json()).then(render).catch(e=>{msg.textContent=e.message;});
    return box;
  }

  // Leave noise activities (heartbeats, comments, status syncs) out of every case-centric analysis.
  function hiddenActivitiesSection(){
    const box=section('Hide activities');box.id='owgHiddenActivities';
    box.appendChild(el('div','Leave chosen activities out of variants, maps, KPIs and the other case analyses of this log. The stored events are never changed, and saved signals and object views keep every event.','muted'));
    const pick=document.createElement('select');pick.id='owgHiddenActivitiesPick';pick.multiple=true;pick.size=6;
    const save=button('Hide selected','secondary');save.id='owgHiddenActivitiesSave';
    const clear=button('Show all activities','secondary');clear.id='owgHiddenActivitiesClear';
    const msg=el('div',null,'muted');msg.id='owgHiddenActivitiesStatus';msg.setAttribute('role','status');msg.setAttribute('aria-live','polite');
    const generation=state.generation;
    const url=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/hidden-activities';
    const render=r=>{
      if(generation!==state.generation)return;
      pick.replaceChildren(...r.activities.map(a=>{const o=el('option',a.activity+' ('+Number(a.events).toLocaleString()+' events)');o.value=a.activity;o.selected=a.hidden;return o;}));
      clear.disabled=!r.hidden.length;
      msg.textContent=r.hidden.length?'Hidden: '+r.hidden.map(h=>h.activity).join(', ')+' · '+Number(r.events_hidden).toLocaleString()+' of '+Number(r.events_total).toLocaleString()+' events left out.':'No activities are hidden.';
    };
    const store=async names=>{
      save.disabled=clear.disabled=true;
      try{await api(url(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({activities:names})});
        if(generation===state.generation)await analyse();}
      catch(e){msg.textContent=e.message;save.disabled=false;clear.disabled=false;}
    };
    save.onclick=()=>{const names=[...pick.selectedOptions].map(o=>o.value);if(!names.length){msg.textContent='Choose at least one activity to hide, or use Show all activities.';return;}store(names);};
    clear.onclick=()=>store([]);
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities to hide (stored names)',pick),save,clear);
    box.append(row,msg);
    getJSON(url()).then(render).catch(e=>{msg.textContent=e.message;});
    return box;
  }

  async function analyse(){
    const generation=++state.generation;
    const results=document.getElementById('owgEventLogResults');results.replaceChildren();
    status('Analysing…');
    const [summary,variants,activities]=await Promise.all([getJSON(logPath('summary')),getJSON(logPath('variants/preview',{limit:10})),getJSON(logPath('activities'))]);
    if(generation!==state.generation)return;
    const names=(activities.activities||[]).map(a=>a.activity);
    const inspection=caseInspector(names);
    const overview=section('Overview');
    const d=summary.case_duration_seconds||{};
    overview.appendChild(table(['Cases','Events','Activities','Median case time','90th percentile'],[[Number(summary.cases).toLocaleString(),Number(summary.events).toLocaleString(),summary.activities,duration(d.median),duration(d.p90)]]));
    if(summary.cases_before_filter!==undefined){const note=el('div','Filtered: '+Number(summary.cases).toLocaleString()+' of '+Number(summary.cases_before_filter).toLocaleString()+' cases match the case filter.','muted');note.id='owgCaseFilterCount';overview.appendChild(note);}
    if(summary.activity_mapping){const m=summary.activity_mapping;const note=el('div','Activities are grouped by the mapping “'+m.name+'”: '+Number(m.events_relabeled).toLocaleString()+' events relabelled'+(m.repeats_collapsed?', '+Number(m.repeats_collapsed).toLocaleString()+' consecutive repeats collapsed':'')+'. The stored events are unchanged; remove the mapping under Group activities to see the original activities.','note');note.id='owgActivityMappingNotice';overview.appendChild(note);}
    if(summary.hidden_activities){const h=summary.hidden_activities;const note=el('div','Hidden activities left out: '+h.activities.join(', ')+' ('+Number(h.events_hidden).toLocaleString()+' events'+(h.cases_with_only_hidden_events?'; '+Number(h.cases_with_only_hidden_events).toLocaleString()+' cases had only hidden events and are not shown':'')+'). The stored events are unchanged; use Show all activities under Hide activities to include them.','note');note.id='owgHiddenActivitiesNotice';overview.appendChild(note);}
    results.appendChild(overview);
    results.appendChild(insightSection(inspection));
    results.appendChild(activityMappingSection());
    results.appendChild(hiddenActivitiesSection());
    results.appendChild(qualitySection());
    results.appendChild(attributeProfileSection(inspection));
    results.appendChild(caseFilterSection(names));
    results.appendChild(cohortMetricSection(names));

    const variantBox=section('Most common variants');
    variantBox.appendChild(table(['Path','Cases','Share','Median time','Evidence'],(variants.variants||[]).map(v=>{
      const open=button('Inspect cases','secondary');open.disabled=!!v.path_truncated;
      if(v.path_truncated)open.title='This path is shortened; browse cases by activity instead.';
      else open.onclick=()=>inspection.path(v.path);
      return [v.path.join(' → ')+(v.path_truncated?' … ('+Number(v.path_activity_count).toLocaleString()+' activities; shortened)':''),Number(v.cases).toLocaleString(),pct(v.share),duration(v.median_duration_seconds),open];})));
    variantBox.appendChild(el('div',Number(variants.distinct_variants).toLocaleString()+' distinct variants.','muted'));
    // Every variant (not just the top ones) for review in a spreadsheet, with running share and durations.
    const variantsCsv=button('Download all variants (CSV)','secondary');variantsCsv.id='owgVariantsCsv';
    const variantsCsvStatus=el('span',null,'muted');variantsCsvStatus.id='owgVariantsCsvStatus';variantsCsvStatus.setAttribute('role','status');
    variantsCsv.onclick=async()=>{
      variantsCsv.disabled=true;
      try{const response=await api(logPath('variants.csv',{}));const blob=await response.blob();
        downloadBlob(blob,(state.logId||'log')+'-variants.csv');
        const info=response.headers.get('X-OWG-Variants')||'';
        variantsCsvStatus.textContent=' Downloaded '+(/written=(\d+)/.exec(info)||[,'the'])[1]+' variants'+(/truncated=true/.test(info)?' (the most common; the list was cut)':'')+'.';}
      catch(error){variantsCsvStatus.textContent=' '+error.message;}
      finally{variantsCsv.disabled=false;}};
    const csvRow=el('div',null,'row');csvRow.append(variantsCsv,variantsCsvStatus);variantBox.appendChild(csvRow);
    results.appendChild(variantBox);
    results.appendChild(variantCoverageSection(names));
    results.appendChild(reworkSection());
    results.appendChild(statisticsExportSection());
    results.appendChild(objectMapSection());
    if(summary.log&&summary.log.object_relationships)results.appendChild(objectRelationshipSection());
    results.appendChild(processMapSection(inspection));

    const activityBox=section('Activities');
    activityBox.appendChild(table(['Activity','Events','Cases','Cases repeating activity','Evidence'],(activities.activities||[]).map(a=>{const open=button('Inspect cases','secondary');open.setAttribute('aria-label','Inspect cases containing '+a.activity);open.onclick=()=>inspection.activity(a.activity);return [a.activity,a.events,a.cases,a.repeated_in_cases,open];})));
    results.append(activityBox,inspection.node);
    results.appendChild(kpiSection(names));
    results.appendChild(workInProgressSection(inspection));
    results.appendChild(durationDistributionSection(inspection));
    results.appendChild(batchingSection(inspection));
    results.appendChild(benchmarkSection(names));
    results.appendChild(timePatternSection(names,inspection));
    results.appendChild(impactSection(names,inspection));
    results.appendChild(stuckSection(inspection));
    results.appendChild(savedGraphSection(names,inspection));
    results.appendChild(simulationSection());
    results.appendChild(conformanceSection((variants.variants||[])[0]));
    results.appendChild(clusterSection(inspection));
    results.appendChild(caseTagSection(names));
    results.appendChild(caseAttributeSection());
    results.appendChild(caseTableExportSection());
    results.appendChild(caseExplainSection());
    results.appendChild(caseNotesSection(inspection));
    results.appendChild(discoverySection());
    results.appendChild(driftSection(inspection));
    results.appendChild(automationSection());
    results.appendChild(decisionSection(inspection));
    results.appendChild(descriptionSection());
    results.appendChild(agentRunsRefreshSection());
    results.appendChild(folderFeedSection());
    results.appendChild(agentShareSection());
    results.appendChild(expirySection());
    results.appendChild(deriveSection(names));
    results.appendChild(anonymizedExportSection());
    results.appendChild(combineSection());
    results.appendChild(dottedSection(inspection));
    results.appendChild(spectrumSection(inspection));
    results.appendChild(heuristicsSection(inspection));
    results.appendChild(glossarySection());
    results.appendChild(activityProfileSection(names));
    results.appendChild(breakdownSection(names,inspection));
    results.appendChild(pathExplorerSection(inspection));
    results.appendChild(cooccurrenceSection(inspection));
    results.appendChild(dueDatesSection(names,inspection));
    results.appendChild(unusualCasesSection());
    results.appendChild(funnelSection(names));
    results.appendChild(modelQualitySection());
    results.appendChild(modelChangeSection());
    results.appendChild(footprintsSection(inspection));
    results.appendChild(petriReplaySection(inspection));
    results.appendChild(temporalProfileSection(inspection));
    results.appendChild(waitingSection(inspection));
    results.appendChild(rootCauseSection((activities.activities||[]).map(a=>a.activity),inspection));
    results.appendChild(organizationSection(inspection));
    results.appendChild(privateMapSection());
    results.appendChild(logComparisonSection());
    results.appendChild(analysisSection(names));
    results.appendChild(signalSection((activities.activities||[]).map(a=>a.activity)));
    results.appendChild(predictionSection(names,inspection));
    results.appendChild(nextActivitySection(names,inspection));
    results.appendChild(complianceSection((activities.activities||[]).map(a=>a.activity),inspection));
    results.appendChild(domainPackSection());
    results.appendChild(costSection(names,inspection));
    results.prepend(analysisNavigator(results));
    results.prepend(storySection());
    attachCaseAttributeSuggestions(generation);
    status('');
  }

  // A searchable list of the analysis sections below, so a long results page can be navigated by name.
  // Buttons rather than links: the page hash carries the dashboard session.
  // The log in plain words, on request: each sentence comes from a computed figure and links to its section.
  function storySection(){
    const box=document.createElement('section');box.id='owgStory';box.style.marginTop='12px';box.setAttribute('aria-label','Summary in plain words');
    const run=button('Summarize in plain words','secondary');run.id='owgStoryRun';
    const out=el('div');out.id='owgStoryText';out.setAttribute('role','status');out.setAttribute('aria-live','polite');
    box.append(run,out);
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;out.replaceChildren(el('div','Summarizing…','muted'));
      try{
        const r=await getJSON(logPath('story',{}));
        if(generation!==state.generation)return;
        const list=el('ul');list.style.margin='6px 0';
        r.sentences.forEach(sn=>{const li=el('li',sn.text);
          const target=sn.section&&document.getElementById(sn.section);
          if(target){const go=button('Show','secondary');go.style.marginLeft='6px';go.setAttribute('aria-label','Show the analysis: '+sn.text.slice(0,60));
            go.onclick=()=>{target.scrollIntoView({block:'start'});const h=target.querySelector('h3');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}};li.appendChild(go);}
          list.appendChild(li);});
        out.replaceChildren(list,el('div',r.caveat,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}};
    return box;
  }

  function analysisNavigator(results){
    const nav=document.createElement('nav');nav.id='owgAnalysisNav';nav.setAttribute('aria-label','Analyses');nav.style.marginTop='12px';
    const sections=[...results.children].filter(box=>box.firstElementChild&&box.firstElementChild.tagName==='H3');
    const search=input('owgAnalysisNavSearch','search','e.g. conformance, funnel, cost');search.autocomplete='off';
    const count=el('div',null,'muted');count.id='owgAnalysisNavCount';count.setAttribute('role','status');count.setAttribute('aria-live','polite');
    const list=el('ul');list.id='owgAnalysisNavList';list.style.cssText='display:flex;flex-wrap:wrap;gap:4px 8px;list-style:none;padding:0;margin:6px 0 0';
    const items=sections.map((box,i)=>{
      const heading=box.firstElementChild,title=heading.textContent.trim();
      if(!heading.id)heading.id='owgAnalysisHeading'+i;heading.tabIndex=-1;
      const go=button(title,'secondary');go.style.fontSize='12px';
      go.onclick=()=>{box.scrollIntoView({block:'start'});heading.focus({preventScroll:true});};
      const li=el('li');li.appendChild(go);list.appendChild(li);
      return {li,text:(title+' '+(box.querySelector('.muted')?.textContent||'')).toLowerCase()};});
    const show=()=>{const words=search.value.toLowerCase().split(/\s+/).filter(Boolean);let shown=0;
      items.forEach(item=>{const on=words.every(w=>item.text.includes(w));item.li.hidden=!on;shown+=on?1:0;});
      count.textContent=words.length?shown+' of '+items.length+' analyses match.':items.length+' analyses on this page.';};
    search.oninput=show;
    nav.append(field('Find an analysis',search),count,list);show();
    return nav;
  }

  // Suggest case attributes (from the import or a joined case table) in every case-attribute input:
  // name=value inputs get the common values, numeric-range inputs the numeric attribute names.
  async function attachCaseAttributeSuggestions(generation){
    try{
      const q=state.caseType?'?case_type='+encodeURIComponent(state.caseType):'';
      const r=await getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/case-attribute-values'+q);
      if(generation!==state.generation||!r.attributes.length)return;
      const list=(id,options)=>{let node=document.getElementById(id);if(!node){node=document.createElement('datalist');node.id=id;document.body.appendChild(node);}
        node.replaceChildren(...options.map(([value,label])=>{const o=document.createElement('option');o.value=value;if(label)o.label=label;return o;}));};
      list('owgCaseAttributeValueSuggestions',r.attributes.filter(a=>!a.numeric).flatMap(a=>a.top_values.map(v=>[a.name+'='+v.value,v.cases+' cases'])));
      list('owgCaseAttributeNumericSuggestions',r.attributes.filter(a=>a.numeric).map(a=>[a.name,a.min+' to '+a.max]));
      for(const node of document.querySelectorAll('#owgCaseFilterAttribute,input[id$="CohortAttribute"],input[id$="FilterAttribute"],input[id$="CohortAAttribute"],input[id$="CohortBAttribute"]'))node.setAttribute('list','owgCaseAttributeValueSuggestions');
      for(const node of document.querySelectorAll('input[id$="RangeName"]'))node.setAttribute('list','owgCaseAttributeNumericSuggestions');
    }catch(_){/* suggestions are a convenience */}
  }

  function caseAttributeSearch(attrValue,rangeName,rangeMin,rangeMax){
    const panel=el('details');
    panel.appendChild(el('summary','Find a case attribute'));
    const query=input('owgCaseAttributeSearch','search','Part of an attribute name');query.maxLength=200;
    const search=button('Search attributes','secondary');search.id='owgCaseAttributeSearchRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Attribute name to find',query),search);
    const message=el('p');message.id='owgCaseAttributeSearchStatus';message.setAttribute('role','status');
    const results=el('div');results.id='owgCaseAttributeSearchResults';
    panel.append(row,message,results);
    const generation=state.generation;
    let request=0;
    query.oninput=()=>{request++;search.disabled=false;results.replaceChildren();message.textContent='';};
    search.onclick=async()=>{
      const term=query.value.trim();
      if(!term){message.textContent='Enter part of an attribute name.';query.focus();return;}
      const ticket=++request;
      search.disabled=true;message.textContent='Searching case attributes…';results.replaceChildren();
      try{
        const result=await getJSON(logPath('case-attribute-values',{search:term}));
        if(generation!==state.generation||ticket!==request)return;
        message.textContent=result.names_total
          ? 'Showing '+result.attributes.length+' of '+result.names_total+' matching attributes.'+
            (result.names_truncated?' Enter a more specific name to narrow the results.':'')
          : 'No case attribute names match this search.';
        for(const [index,attribute] of result.attributes.entries()){
          const heading=el('h4',attribute.name);
          const info=el('p',Number(attribute.cases).toLocaleString()+' cases have this attribute.'+
            (attribute.numeric?' Numeric values range from '+attribute.min+' to '+attribute.max+'.':''),'muted');
          const actions=el('div',null,'row');
          if(attribute.numeric){
            const useRange=button('Use for numeric range','secondary');
            useRange.setAttribute('aria-label','Use '+attribute.name+' for numeric range');
            useRange.onclick=()=>{
              if(rangeName.value!==attribute.name){rangeMin.value='';rangeMax.value='';}
              rangeName.value=attribute.name;rangeMin.focus();
            };
            actions.appendChild(useRange);
          }
          const choices=attribute.top_values.filter(item=>attribute.name.trim()===attribute.name&&
            !attribute.name.includes('=')&&item.value.trim()===item.value&&
            Array.from(attribute.name+'='+item.value).length<=200);
          if(choices.length){
            const values=select('owgCaseAttributeSearchValue'+index,choices.map(item=>[
              item.value,item.value+' ('+item.cases+' cases)'
            ]));
            const useValue=button('Use value','secondary');
            useValue.setAttribute('aria-label','Use value for '+attribute.name);
            useValue.onclick=()=>{attrValue.value=attribute.name+'='+values.value;attrValue.focus();};
            actions.append(field('Common values for '+attribute.name,values),useValue);
          }
          results.append(heading,info,actions);
        }
      }catch(error){if(generation===state.generation&&ticket===request)message.textContent=error.message;}
      finally{if(ticket===request)search.disabled=false;}
    };
    query.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();if(!search.disabled)search.click();}};
    return panel;
  }

  // Process cost from the user's own cost model: a price per activity execution and an optional hourly cost per open case.
  function costSection(activityNames,inspection){
    const box=section('Process cost');box.id='owgEventLogCosts';
    box.appendChild(el('div','Enter what one execution of each activity costs, and optionally what each hour a case stays open costs. The log does not record what work cost: these figures are your model applied to the observed events.','muted'));
    const currency=input('owgCostCurrency','text');currency.value='EUR';currency.maxLength=3;currency.size=4;currency.autocomplete='off';
    const hourly=input('owgCostHourly','number');hourly.min='0';hourly.step='any';hourly.value='0';
    const fallback=input('owgCostDefault','number','none');fallback.min='0';fallback.step='any';
    const controls=el('div',null,'row');controls.style.alignItems='end';
    controls.append(field('Currency (ISO code)',currency),field('Cost per hour a case is open',hourly),field('Cost of activities left blank',fallback));
    const shown=activityNames.slice(0,200);
    const prices=shown.map((name,i)=>{const node=input('owgCostActivity'+i,'number');node.min='0';node.step='any';node.setAttribute('aria-label','Cost of one '+name);node.dataset.activity=name;node.style.width='8em';return node;});
    const priceTable=table(['Activity','Cost per execution'],shown.map((name,i)=>[name,prices[i]]));
    const run=button('Calculate cost','secondary');run.id='owgCostRun';
    const out=el('div');out.id='owgCostResults';out.setAttribute('role','status');
    box.append(controls,priceTable);
    if(activityNames.length>shown.length)box.appendChild(el('div','Only the first '+shown.length+' activities are listed; give the rest a cost with “Cost of activities left blank”.','muted'));
    const saveModel=button('Save prices','secondary');saveModel.id='owgCostSaveModel';saveModel.title='Keep these prices with the log; the HTML report then includes a cost section';
    const forget=button('Forget saved prices','secondary');forget.id='owgCostForgetModel';forget.disabled=true;
    const modelStatus=el('div',null,'muted');modelStatus.id='owgCostModelStatus';modelStatus.setAttribute('role','status');
    const actions=el('div',null,'row');actions.append(run,saveModel,forget);
    const evidence=el('div');evidence.id='owgCostCaseReview';
    box.append(actions,modelStatus,out,evidence);
    const generation=state.generation;
    const modelPath=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/cost-model';
    const readModel=()=>{
      const activity_costs=Object.create(null);
      for(const node of prices){if(node.value.trim()==='')continue;const v=Number(node.value);if(!(v>=0))throw new Error('Costs must be zero or more ('+node.dataset.activity+').');activity_costs[node.dataset.activity]=v;}
      const model={currency:currency.value.trim().toUpperCase(),activity_costs,hourly_open_case_cost:Number(hourly.value||0)};
      if(fallback.value.trim()!=='')model.default_activity_cost=Number(fallback.value);
      return model;
    };
    getJSON(modelPath()).then(r=>{
      if(generation!==state.generation||!r.saved)return;
      const m=r.saved.model;currency.value=m.currency;hourly.value=String(m.hourly_open_case_cost||0);
      fallback.value=m.default_activity_cost===null||m.default_activity_cost===undefined?'':String(m.default_activity_cost);
      for(const node of prices){const v=m.activity_costs[node.dataset.activity];node.value=v===undefined?'':String(v);}
      forget.disabled=false;modelStatus.textContent='Prices saved '+String(r.saved.saved_at).slice(0,16).replace('T',' ')+' UTC are filled in. The HTML report includes a cost section.';
    }).catch(()=>{});
    saveModel.onclick=async()=>{
      saveModel.disabled=true;
      try{const r=await (await api(modelPath(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readModel())})).json();
        forget.disabled=false;modelStatus.textContent='Prices saved. The HTML report and its saved copies now include a cost section.';}
      catch(error){modelStatus.textContent=error.message;}finally{saveModel.disabled=false;}
    };
    forget.onclick=async()=>{
      try{await api(modelPath()+'/delete',{method:'POST'});forget.disabled=true;modelStatus.textContent='Saved prices forgotten. The report no longer includes a cost section.';}
      catch(error){modelStatus.textContent=error.message;}
    };
    let revision=0;
    function invalidate(){
      revision++;run.disabled=false;out.replaceChildren();evidence.replaceChildren();
    }
    for(const control of [currency,hourly,fallback,...prices])control.addEventListener('input',invalidate);
    run.onclick=async()=>{
      invalidate();
      const ticket=revision;
      const current=()=>box.isConnected&&generation===state.generation&&ticket===revision;
      run.disabled=true;
      try{
        const spec=readModel();
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/costs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(!current())return;
        let money;try{money=new Intl.NumberFormat(undefined,{style:'currency',currency:r.currency});}catch(_){money={format:v=>r.currency+' '+Number(v).toFixed(2)};}
        const m=v=>v===null||v===undefined?'—':money.format(v);
        const cpc=r.cost_per_case;
        const summary=table(['Total','Activities','Time cases are open','Repeated executions','Per case: mean','median','90th percentile'],
          [[m(r.total_cost),m(r.activity_cost),m(r.holding_cost),m(r.repeated_execution_cost)+' ('+pct(r.repeated_execution_share)+')',m(cpc.mean),m(cpc.median),m(cpc.p90)]]);
        const acts=table(['Activity','Executions','Unit cost','Cost','Share','Repeated executions','Their cost'],r.activities.map(a=>[a.activity,Number(a.events).toLocaleString(),a.unit_cost===null?'no price':m(a.unit_cost)+(a.price_source==='default'?' (default)':''),m(a.cost),pct(a.share_of_activity_cost),Number(a.repeated_executions).toLocaleString(),m(a.repeated_execution_cost)]));
        function review(label,variant){
          const panel=el('div'),heading=el('h4',label);heading.tabIndex=-1;
          const message=el('div',null,'muted');message.setAttribute('role','status');
          const list=el('div'),first=button('First case page','secondary'),next=button('Next case page','secondary');
          panel.append(heading,message,list,first,next);evidence.replaceChildren(panel);heading.focus();
          let cursor=null;
          async function load(offset){
            first.disabled=next.disabled=true;
            message.textContent='Loading case costs…';
            try{
              const path='/v1/event-logs/'+encodeURIComponent(state.logId)+'/costs/cases';
              const result=await (await api(path,{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({model:spec,variant_id:variant,offset})})).json();
              if(!current()||!panel.isConnected)return;
              cursor=result.next_offset;
              message.textContent=Number(result.selected_cases).toLocaleString()+' matching cases, highest cost first.';
              list.replaceChildren(table(['Case','Total','Activity cost','Elapsed-time cost','Repeat cost (included in activity cost)','Unpriced executions'],
                result.cases.map(c=>{
                  const open=button(c.case_id,'secondary');
                  open.setAttribute('aria-label','Open case '+c.case_id);
                  open.onclick=()=>inspection.open(c.case_id);
                  return [open,m(c.total_cost),m(c.activity_cost),m(c.holding_cost),m(c.repeated_execution_cost),c.unpriced_events];
                })));
              first.disabled=offset===0;next.disabled=cursor===null;
            }catch(error){
              if(current()&&panel.isConnected){
                message.textContent=error.message;
                const retry=button('Retry','secondary');retry.onclick=()=>load(offset);list.replaceChildren(retry);
              }
            }
          }
          first.onclick=()=>load(0);next.onclick=()=>load(cursor);load(0);
        }
        const browse=button('Review case costs','secondary');browse.onclick=()=>review('Case costs');
        const variants=table(['Variant','Cases','Cost per case','Total','Repeated executions per case'],r.costliest_variants.map(v=>{
          const label=v.path.slice(0,20).join(' → ')+(v.path.length>20?' … ('+v.path.length+' steps)':'');
          const open=button(Number(v.cases).toLocaleString(),'secondary');
          open.setAttribute('aria-label','Review costs for '+label);open.onclick=()=>review('Case costs for '+label,v.variant_id);
          return [label,open,m(v.cost_per_case),m(v.total_cost),m(v.repeated_execution_cost_per_case)];
        }));
        const nodes=[el('div',Number(r.cases).toLocaleString()+' cases, '+Number(r.events).toLocaleString()+' events.','muted'),summary,browse];
        if(r.unpriced_events)nodes.push(el('div','No price for '+r.unpriced_activities.map(a=>a.activity).join(', ')+': '+Number(r.unpriced_events).toLocaleString()+' executions are left out of the totals.','muted'));
        nodes.push(el('h4','Cost by activity'),acts,el('h4','Costliest variants'),variants,el('div',r.interpretation,'muted'));
        out.replaceChildren(...nodes);
      }catch(error){if(current())out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  // R9: read-only, bounded metrics for selected saved logs.
  function cohortMetricSection(activityNames){
    const box=section('Cohort metrics');box.id='owgCohortMetricSection';
    box.appendChild(el('p','Calculate descriptive metrics or combine two approved case metrics across matching cases. Division by zero and missing values are excluded from numeric summaries. Aggregate results only; maximum 1,000 cases.','muted'));
    const metric=select('owgCohortMetricField',[
      ['duration_seconds','Case duration (seconds)'],['event_count','Events per case'],
      ['resource_count','Distinct resources per case'],['duration_hours','Case duration (hours)']
    ]);
    // Only approved, non-identifying numeric fields can enter these expressions.
    // The authenticated server independently validates every expression.
    const operation=select('owgCohortMetricOperation',[
      ['single','Single metric'],['add','Add'],['subtract','Subtract'],
      ['multiply','Multiply'],['divide','Divide (zero denominator is missing)']
    ]);
    const other=select('owgCohortMetricOther',[
      ['duration_seconds','Case duration (seconds)'],['event_count','Events per case'],
      ['resource_count','Distinct resources per case']
    ]);
    const otherWrap=field('Second metric',other);
    const updateOperation=()=>show(otherWrap,operation.value!=='single');
    operation.addEventListener('change',updateOperation);
    updateOperation();
    const savedPicker=select('owgCohortMetricSaved',[['','Choose saved expression']]);
    const savedName=input('owgCohortMetricSaveName','text','Metric name');savedName.maxLength=120;
    const saveButton=button('Save expression','secondary');saveButton.id='owgCohortMetricSave';
    const deleteButton=button('Delete saved expression','secondary');deleteButton.id='owgCohortMetricDelete';
    const savedStatus=el('div');savedStatus.setAttribute('role','status');
    let savedEntries=[];
    let savedLoadSequence=0;
    const savedPath=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/cohort-metric-definitions';
    const currentDefinition=()=>{
      const first=metric.value==='duration_hours'
        ?{divide:[{field:'duration_seconds'},{literal:3600}]}:{field:metric.value};
      return {version:1,expression:operation.value==='single'
        ?first:{[operation.value]:[first,{field:other.value}]}};
    };
    const loadSaved=async()=>{
      const log=state.logId,generation=state.generation,sequence=++savedLoadSequence;
      if(!log)return;
      const response=await api(savedPath());
      const data=await response.json();
      if(log!==state.logId||generation!==state.generation||sequence!==savedLoadSequence)return;
      savedEntries=data.definitions||[];
      savedPicker.replaceChildren(new Option('Choose saved expression',''),
        ...savedEntries.map(item=>new Option(item.name,item.metric_id)));
    };
    savedPicker.addEventListener('change',()=>{
      const item=savedEntries.find(entry=>entry.metric_id===savedPicker.value);
      if(!item)return;
      const expression=item.definition.expression;
      const isHours=JSON.stringify(expression)===JSON.stringify({divide:[{field:'duration_seconds'},{literal:3600}]});
      if(expression.field||isHours){
        metric.value=isHours?'duration_hours':expression.field;operation.value='single';
      }else{
        const op=Object.keys(expression)[0],parts=expression[op];
        if(!['add','subtract','multiply','divide'].includes(op)||!Array.isArray(parts)||parts.length!==2)return;
        const first=parts[0],second=parts[1];
        const hours=JSON.stringify(first)===JSON.stringify({divide:[{field:'duration_seconds'},{literal:3600}]});
        if(!second.field||(!first.field&&!hours))return;
        metric.value=hours?'duration_hours':first.field;other.value=second.field;operation.value=op;
      }
      updateOperation();savedStatus.textContent='Loaded '+item.name+'. Calculate to apply current cohort filters.';
    });
    saveButton.onclick=async()=>{
      const log=state.logId,generation=state.generation,path=savedPath();
      const isCurrent=()=>log===state.logId&&generation===state.generation;
      saveButton.disabled=true;
      try{
        const response=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name:savedName.value,definition:currentDefinition()})});
        const item=await response.json();
        if(!isCurrent())return;
        await loadSaved();
        if(!isCurrent())return;
        savedPicker.value=item.metric_id;
        savedStatus.textContent='Saved '+item.name+' for this log.';
      }catch(error){if(isCurrent())savedStatus.textContent=error.message;}
      finally{saveButton.disabled=false;}
    };
    deleteButton.onclick=async()=>{
      const id=savedPicker.value;if(!id)return;
      const log=state.logId,generation=state.generation,path=savedPath();
      const isCurrent=()=>log===state.logId&&generation===state.generation;
      deleteButton.disabled=true;
      try{
        await api(path+'/'+encodeURIComponent(id)+'/delete',{method:'POST'});
        if(!isCurrent())return;
        await loadSaved();
        if(isCurrent())savedStatus.textContent='Saved expression deleted.';
      }catch(error){if(isCurrent())savedStatus.textContent=error.message;}
      finally{deleteButton.disabled=false;}
    };
    // A manually edited expression is no longer the selected saved revision.
    for(const control of [metric,operation,other]){
      control.addEventListener('change',()=>{savedPicker.value='';});
    }
    const initialLog=state.logId,initialGeneration=state.generation;
    loadSaved().catch(error=>{
      if(initialLog===state.logId&&initialGeneration===state.generation)savedStatus.textContent=error.message;
    });
    const filters=cohortFields('owgCohortMetricFilter','Choose matching cases',activityNames);
    const run=button('Calculate cohort metric','secondary');run.id='owgCohortMetricRun';
    const out=el('div');out.id='owgCohortMetricResult';out.setAttribute('role','status');out.setAttribute('aria-live','polite');
    const controls=el('div',null,'row');controls.append(field('Metric',metric),field('Calculation',operation),otherWrap,run);
    box.append(controls,field('Saved expression',savedPicker),field('Name to save',savedName),saveButton,deleteButton,savedStatus,filters.node,out);
    let turn=0;
    run.onclick=async()=>{
      const t=++turn,generation=state.generation,logId=state.logId,caseType=state.caseType;
      run.disabled=true;out.replaceChildren(el('div','Calculating…','muted'));
      try{
        const cohort=filters.read();
        if(filterOn())applyActiveCohort(cohort);
        const first=metric.value==='duration_hours'
          ?{divide:[{field:'duration_seconds'},{literal:3600}]}
          :{field:metric.value};
        const expression=operation.value==='single'
          ?first:{[operation.value]:[first,{field:other.value}]};
        const savedId=savedPicker.value;
        const endpoint='/v1/event-logs/'+encodeURIComponent(logId)+(savedId
          ?'/cohort-metric-definitions/'+encodeURIComponent(savedId)+'/evaluate':'/cohort-metric');
        const url=new URL(endpoint,window.location.origin);
        if(caseType)url.searchParams.set('case_type',caseType);
        const payload=savedId?{cohort}:{definition:{version:1,expression},cohort};
        const response=await api(url.pathname+url.search,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const data=await response.json();
        if(t!==turn||generation!==state.generation||logId!==state.logId||caseType!==state.caseType)return;
        const selected=savedId?savedEntries.find(entry=>entry.metric_id===savedId):null;
        // Never present a result as the selected saved revision if it changed
        // between the picker load and evaluation. Re-select and recalculate.
        if(savedId&&(!selected||!data.saved_metric||
           data.saved_metric.metric_id!==savedId||
           data.saved_metric.definition_sha256!==selected.definition_sha256)){
          out.replaceChildren(el('div','Saved metric revision changed. Reload saved expressions and calculate again.','muted'));
          await loadSaved();
          return;
        }
        const m=data.metric||{};
        out.replaceChildren(
          el('div',String(m.rows??0)+' matching cases; '+String(m.null??0)+' missing values.','muted'),
          table(['Average','Minimum','Maximum','Sum','Values'],[[m.mean??'—',m.min??'—',m.max??'—',m.sum??'—',String(m.non_null??0)]]),
          el('div','Complete cohort: '+(data.complete_cohort?'yes':'no')+'. No raw cases returned.','muted'),
          ...(selected?[el('div','Saved definition SHA-256: '+selected.definition_sha256,'muted')]:[])
        );
      }catch(error){
        if(t===turn&&generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));
      }finally{if(t===turn)run.disabled=false;}
    };
    return box;
  }

  function caseInspector(activityNames,options={}){
    const prefix=options.prefix||'owgEventLogCases';
    const box=section('Case evidence');box.id=prefix;
    const generation=state.generation;
    const current=()=>box.isConnected&&generation===state.generation;
    const queryPath=(suffix,caseType=options.caseType)=>{
      const url=new URL(logPath(suffix),window.location.origin);
      if(caseType!==undefined)url.searchParams.set('case_type',caseType);
      return url.pathname+url.search;
    };
    const filters=cohortFields(prefix+'Filter','Select complete cases',activityNames);
    const exactActivity=input(prefix+'ExactActivity','text','Exact activity label (optional)');exactActivity.maxLength=400;
    const activityPicker=filters.node.querySelector('#'+prefix+'FilterActivity');
    const activityLookup=activityLabelLookup(exactActivity,{id:part=>prefix+'Activity'+part,
      subject:prefix==='owgVariantCoverageCases'?'variant coverage case activity':
        prefix==='owgEventLogObjectCases'?'object case activity':'case evidence activity',caseType:options.caseType});
    exactActivity.addEventListener('input',activityLookup.reset);
    activityPicker.addEventListener('change',()=>{exactActivity.value='';activityLookup.reset();});
    const browse=button('Browse cases','secondary');browse.id=prefix+'Browse';
    const first=button('First case page','secondary'),next=button('Next case page','secondary');
    first.disabled=true;next.disabled=true;
    const note=el('div','Case keys and labels are supplied by this log. They do not verify business identity, people or outcomes.','muted');
    const chosen=el('div','All paths.','muted');chosen.id=prefix+'Path';
    const reset=button('All paths','secondary');reset.onclick=()=>{association=false;path=null;transition=null;chosen.textContent='All paths.';};
    const message=el('div');message.id=prefix+'Status';message.setAttribute('role','status');
    const list=el('div');list.id=prefix+'List';
    const detail=el('div');detail.id=prefix==='owgEventLogCases'?'owgEventLogCaseEvents':prefix+'Events';
    const controls=el('div',null,'row');controls.append(browse,reset,first,next);
    box.append(note,filters.node,el('div','Suggestions include up to 500 frequent activities. An exact label overrides the suggestion and is matched literally.','muted'),field('Exact activity to inspect',exactActivity),activityLookup.wrap,chosen,controls,message,list,detail);
    let path=null,transition=null,spec=null,nextAfter=null,listing=0,opening=0,association=false,filterApplied=false;
    let listEndpoint='cases/query';
    async function load(after,endpoint='cases/query'){
      listEndpoint=endpoint;
      const ticket=++listing;first.disabled=true;next.disabled=true;browse.disabled=true;
      message.textContent='Loading cases…';
      try{
        const payload={...spec};if(after!==null)payload.after=after;
        const result=await (await api(queryPath(endpoint),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
        if(!current()||ticket!==listing)return;
        nextAfter=result.next_after;
        message.textContent=Number(result.selected_cases).toLocaleString()+' matching cases'+(filterApplied?' (case filter applied)':'')+'. Showing '+result.cases.length+' on this page.';
        const headers=['Supplied case key','Events','Elapsed time','Equal-timestamp events'];
        if(endpoint==='attribute-profile/cases')headers.push('Events with matching values');
        list.replaceChildren(table(headers,result.cases.map(c=>{
          const open=button(c.case_id,'secondary');open.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%';
          open.setAttribute('aria-label','Open case '+c.case_id);open.onclick=()=>openCase(c.case_id);
          const cells=[open,c.events,duration(c.elapsed_seconds),c.timestamp_ties];
          if(endpoint==='attribute-profile/cases')cells.push(c.matching_events);
          return cells;
        })));
        first.disabled=after===null;next.disabled=nextAfter===null;
      }catch(error){if(current()&&ticket===listing){message.textContent=error.message;list.replaceChildren();}}
      finally{if(current()&&ticket===listing)browse.disabled=false;}
    }
    function start(){
      try{const cohort=filters.read();
        if(exactActivity.value){if(Array.from(exactActivity.value).length>200)throw new Error('Activity labels must have at most 200 characters.');cohort.activity=exactActivity.value;}
        if(options.caseType===undefined){applyActiveCohort(cohort);filterApplied=filterOn();}else filterApplied=false;
        if(association){chosen.textContent='All paths.';association=false;}
        spec={cohort,limit:30};if(path)spec.path=path;if(transition)spec.transition=transition;detail.replaceChildren();opening++;load(null);}
      catch(error){message.textContent=error.message;}
    }
    browse.onclick=start;first.onclick=()=>load(null,listEndpoint);next.onclick=()=>load(nextAfter,listEndpoint);
    async function openCase(caseId,caseType){
      const ticket=++opening;
      const heading=el('h4','Case '+caseId);heading.tabIndex=-1;
      const progress=el('div','Loading events…');progress.setAttribute('role','status');
      const rows=el('div'),more=button('More case events','secondary');more.id=prefix==='owgEventLogCases'?'owgEventLogCaseEventsMore':prefix+'EventsMore';more.disabled=true;
      const notes=document.createElement('details');notes.id=prefix+'InlineNotes';
      notes.append(el('summary','Case notes'));
      notes.ontoggle=()=>{
        if(notes.open&&notes.childElementCount===1&&current()&&ticket===opening){
          notes.append(caseNotesPanel(prefix+'InlineNote',caseId,null));
        }
      };
      detail.replaceChildren(heading,progress,rows,more,notes);
      heading.focus();let offset=0,shown=0;
      async function page(){
        more.disabled=true;
        try{
          const result=await (await api(queryPath('case-events/query',caseType),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:caseId,offset,limit:100})})).json();
          if(!current()||ticket!==opening)return;
          shown+=result.events.length;offset=result.next_offset;
          progress.textContent='Showing '+shown+' of '+result.total_events+' events. '+result.timestamp_ties+' equal-timestamp events. '+result.ordering;
          rows.appendChild(table(['Activity','Timestamp (UTC)','Resource label','Event ID'],result.events.map(e=>[e.activity,e.timestamp,e.resource||'(missing)',e.event_id])));
          more.disabled=offset===null;show(more,offset!==null);
        }catch(error){if(current()&&ticket===opening)progress.textContent=error.message;}
      }
      more.onclick=page;await page();
    }
    return {node:box,open:openCase,path(value,comparisonCohort){
      if(value.length>500){message.textContent='This path exceeds the 500-step case selector limit. Browse by activity or cohort instead.';box.scrollIntoView({block:'nearest'});return;}
      association=false;path=value.slice();transition=null;chosen.textContent='Exact path: '+JSON.stringify(path);
      if(comparisonCohort!==undefined){
        chosen.append(' · Saved process comparison cohort. Browse cases uses the fields above.');
        filterApplied=true;spec={cohort:comparisonCohort,path,limit:30};
        detail.replaceChildren();opening++;load(null);
      }else start();
      box.scrollIntoView({block:'nearest'});
    },activity(value){
      const picker=document.getElementById(prefix+'FilterActivity');
      if(!Array.from(picker.options).some(option=>option.value===value))picker.add(new Option(value,value));
      association=false;exactActivity.value='';activityLookup.reset();path=null;transition=null;chosen.textContent='All paths.';picker.value=value;start();box.scrollIntoView({block:'nearest'});
    },variant(value,rank,cohort,description){
      path=null;transition=null;association=true;filterApplied=Object.keys(cohort).length>0;
      spec={cohort,variant_id:value,limit:30};
      chosen.textContent=description||'Exact path for variant '+rank+'.';
      detail.replaceChildren();opening++;load(null);box.scrollIntoView({block:'nearest'});
    },transition(value){
      association=false;exactActivity.value='';activityLookup.reset();path=null;transition=value.slice();document.getElementById(prefix+'FilterActivity').value='';
      chosen.textContent='Adjacent transition: '+JSON.stringify(transition)+'. Timestamp ties do not verify order.';
      start();box.scrollIntoView({block:'nearest'});
    },activityPresence(required,excluded,cohort){
      path=null;transition=null;association=true;filterApplied=Object.keys(cohort).length>0;
      spec={cohort,required_activities:required,...(excluded.length?{excluded_activities:excluded}:{}),limit:30};
      const names=values=>values.map(name=>JSON.stringify(name)).join(' and ');
      chosen.textContent='Cases with '+names(required)+(excluded.length?' and without '+names(excluded):'')+'.';
      detail.replaceChildren();opening++;load(null);box.scrollIntoView({block:'nearest'});
    },attributeMatch(cohort,attribute,selection,description){
      path=null;transition=null;association=true;filterApplied=Object.keys(cohort).length>0;
      spec={cohort,attribute,...selection,limit:30};
      chosen.textContent=description;
      detail.replaceChildren();opening++;load(null,'attribute-profile/cases');box.scrollIntoView({block:'nearest'});
    },cohort(value,description){
      path=null;transition=null;association=true;filterApplied=Boolean(description)&&filterOn();spec={cohort:value,limit:30};
      chosen.textContent=description||'Association cases: '+JSON.stringify(value)+'. This selection uses all cases in the current case notion. Browse cases uses the fields above instead.';
      detail.replaceChildren();opening++;load(null);box.scrollIntoView({block:'nearest'});
    }};
  }

  // Cohort fields; dates are whole UTC days on the case's first event.
  // Case-filter rules from editor rows; throws a readable error.
  const FOLLOW_MODES={eventual:['eventual',false],direct:['direct',false],'not-eventual':['eventual',true],'not-direct':['direct',true]};
  function followSpec(rows){
    const rules=(rows||[]).map(r=>{
      const from=String(r.from||''),to=String(r.to||'');const mode=FOLLOW_MODES[r.mode];
      if(!from||!to)throw new Error('Choose both activities for every rule.');
      if(Array.from(from).length>200||Array.from(to).length>200)throw new Error('Activity labels must have at most 200 characters.');
      if(!mode)throw new Error('Choose how the activities follow each other.');
      const spec={from,to,mode:mode[0],negate:mode[1]};
      // Optional time between the two steps, in hours: at least min_hours, less than max_hours.
      for(const key of ['min_hours','max_hours']){
        const raw=r[key];if(raw===undefined||raw===null||raw==='')continue;
        const value=Number(raw);if(!Number.isFinite(value)||value<0)throw new Error('Time limits are zero or more hours.');
        spec[key]=value;}
      if((spec.min_hours!==undefined||spec.max_hours!==undefined)&&spec.negate)throw new Error('A time limit applies only to a rule where one step follows the other.');
      if(spec.min_hours!==undefined&&spec.max_hours!==undefined&&!(spec.min_hours<spec.max_hours))throw new Error('The shortest time must be below the longest.');
      return spec;
    });
    if(rules.length>5)throw new Error('Use at most 5 rules.');
    return rules;
  }
  // "Where to look first": ranked, descriptive pointers computed automatically after Analyse.
  function insightSection(inspection){
    const box=section('Where to look first');box.id='owgEventLogInsights';
    const out=el('div');out.id='owgInsightResults';out.setAttribute('role','status');out.setAttribute('aria-live','polite');
    out.appendChild(el('div','Looking for where case time and repetition concentrate…','muted'));
    box.appendChild(out);
    const generation=state.generation;
    const spec={limit:6};if(state.caseType)spec.case_type=state.caseType;if(filterOn())spec.cohort=globalCohort();
    api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/insights',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})
      .then(r=>r.json()).then(r=>{
        if(generation!==state.generation)return;
        if(r.too_few_cases){out.replaceChildren(el('div','At least '+r.minimum_cases+' cases are needed for automatic insights.','muted'));return;}
        if(!r.findings.length){out.replaceChildren(el('div','Nothing stands out: no step, loop or detour holds a large share of case time.','muted'));return;}
        const list=el('ol');list.style.paddingLeft='1.2em';
        r.findings.forEach(f=>{
          const item=el('li');item.style.marginBottom='8px';
          const title=el('strong',f.title);
          const meta=el('div',Number(f.cases).toLocaleString()+' cases ('+pct(f.case_share)+')'+(f.impact_seconds?' · about '+duration(f.impact_seconds)+' of case time involved':'')+'. '+f.caveat,'muted');
          item.append(title,meta);
          const finding={...f.cohort,...(f.follows?.length?{follows:f.follows}:{})};
          const scoped=Object.keys(r.cohort).length>0;
          const selection=scoped?{all_of:[r.cohort,finding]}:finding;
          if(Object.keys(finding).length){
            const review=button('Review cases','secondary');
            review.setAttribute('aria-label','Review cases for '+f.title);
            review.onclick=()=>inspection.cohort(selection,f.title);
            item.appendChild(review);
          }
          if(f.follows&&f.follows.length&&!scoped){
            const go=button('Filter to these cases','secondary');go.setAttribute('aria-label','Filter to these cases: '+f.title);
            go.onclick=()=>{state.follows=f.follows;analyse().catch(error=>status(error.message));};
            item.appendChild(go);
          }else if(f.cohort&&(f.cohort.attribute||f.cohort.attribute_range)){
            // Case-attribute groups: list the slowest of exactly these cases, or save them as a log.
            const casesOut=el('div');casesOut.setAttribute('role','status');
            const base='/v1/event-logs/'+encodeURIComponent(state.logId);
            const post=(path,body)=>{if(state.caseType)body.case_type=state.caseType;return api(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(x=>x.json());};
            const slowest=button('Slowest cases','secondary');slowest.setAttribute('aria-label','Slowest cases: '+f.title);
            const save=button('Save as log','secondary');save.setAttribute('aria-label','Save these cases as a new log: '+f.title);
            slowest.onclick=async()=>{try{const r2=await post('/cases/slowest',{cohort:selection,limit:10});if(generation!==state.generation)return;
              const row=el('div',null,'row');r2.cases.forEach(c=>{const b=button(c.case_id+' ('+duration(c.duration_seconds)+')','secondary');b.setAttribute('aria-label','Explain case '+c.case_id);
                b.onclick=()=>{const id=document.getElementById('owgCaseExplainId'),run=document.getElementById('owgCaseExplainRun');if(!id||!run)return;id.value=c.case_id;run.click();document.getElementById('owgCaseExplain').scrollIntoView({block:'start'});};row.appendChild(b);});
              casesOut.replaceChildren(row);}catch(error){casesOut.replaceChildren(el('div',error.message,'muted'));}};
            save.onclick=async()=>{const name=(window.prompt('Name for a new log with these '+f.cases+' cases',(f.numbers.segment||'segment').slice(0,120))||'').trim();if(!name)return;
              try{const r2=await post('/derive',{name:name.slice(0,120),cohort:selection});casesOut.replaceChildren(el('div','Saved '+Number(r2.copied.events).toLocaleString()+' events of '+Number(r2.derived_from.cases_matched).toLocaleString()+' cases as “'+r2.log.name+'”. This log is unchanged.'));}
              catch(error){casesOut.replaceChildren(el('div',error.message,'muted'));}};
            const row=el('div',null,'row');
            // Make the group the global case filter, so every filtered view shows only these cases.
            if(!scoped){const go=button('Filter to these cases','secondary');go.setAttribute('aria-label','Filter to these cases: '+f.title);
              go.onclick=()=>{state.attr={...f.cohort};analyse().catch(error=>status(error.message));};row.appendChild(go);}
            row.append(slowest,save);item.append(row,casesOut);
          }
          list.appendChild(item);
        });
        out.replaceChildren(list,el('div',r.interpretation,'muted'));
      }).catch(error=>{if(generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));});
    return box;
  }

  function followText(rule){
    const h=x=>Number(x)+' h';
    const time=rule.min_hours!==undefined&&rule.max_hours!==undefined?' after '+h(rule.min_hours)+' to '+h(rule.max_hours):rule.min_hours!==undefined?' after at least '+h(rule.min_hours):rule.max_hours!==undefined?' within '+h(rule.max_hours):'';
    return rule.from+(rule.negate?' is never ':' is ')+(rule.mode==='direct'?'directly':'eventually')+' followed by '+rule.to+time;
  }
  const followRow=rule=>({from:rule.from,to:rule.to,mode:(rule.negate?'not-':'')+rule.mode,
    ...(rule.min_hours!==undefined?{min_hours:rule.min_hours}:{}),...(rule.max_hours!==undefined?{max_hours:rule.max_hours}:{})});

  // Preserve all selected conditions. Incompatible numeric ranges fail closed
  // instead of silently dropping either the operation or workspace constraint.
  function mergeActiveCohort(cohort,active){
    const result={...cohort};
    if(active.follows)result.follows=active.follows;
    if(active.activity){
      if(result.activity&&result.activity!==active.activity)throw new Error('This operation selects a different activity from the active case filter. Clear one activity to continue.');
      result.activity=active.activity;
    }
    if(active.attribute){
      if(result.attribute&&result.attribute!==active.attribute)throw new Error('This operation selects a different case attribute from the active filter. Change one attribute to continue.');
      result.attribute=active.attribute;
    }
    if(active.attribute_range){
      const base=active.attribute_range,extra=result.attribute_range;
      if(extra&&extra.name!==base.name)throw new Error('This operation has a different numeric attribute range from the active case filter. Clear one range to continue.');
      const range={...base};
      if(extra){
        if(extra.min!==undefined)range.min=range.min===undefined?extra.min:Math.max(range.min,extra.min);
        if(extra.max!==undefined)range.max=range.max===undefined?extra.max:Math.min(range.max,extra.max);
      }
      if(range.min!==undefined&&range.max!==undefined&&range.min>=range.max)throw new Error('The numeric ranges do not overlap. Change one range to continue.');
      result.attribute_range=range;
    }
    for(const [key,what] of [['starts_with','first activity'],['ends_with','last activity']]){
      if(!active[key])continue;
      if(result[key]&&result[key]!==active[key])throw new Error('This operation selects a different '+what+' from the active case filter. Clear one to continue.');
      result[key]=active[key];
    }
    if(active.duration){
      const d={...active.duration},extra=result.duration;
      if(extra){
        if(extra.min!==undefined)d.min=d.min===undefined?extra.min:Math.max(d.min,extra.min);
        if(extra.max!==undefined)d.max=d.max===undefined?extra.max:Math.min(d.max,extra.max);
      }
      if(d.min!==undefined&&d.max!==undefined&&d.min>=d.max)throw new Error('The case duration ranges do not overlap. Change one to continue.');
      result.duration=d;
    }
    for(const key of MORE_FILTER_KEYS){
      if(active[key]===undefined)continue;
      if(result[key]!==undefined&&JSON.stringify(result[key])!==JSON.stringify(active[key]))throw new Error('This operation uses a different '+key.replace('_',' ')+' filter from the active case filter. Clear one to continue.');
      result[key]=active[key];
    }
    return result;
  }
  function applyActiveCohort(cohort){Object.assign(cohort,mergeActiveCohort(cohort,globalCohort()));}

  function activityLabelLookup(exact,options){
    const lookupGeneration=state.generation,lookupLog=state.logId,activeCase=state.caseType;
    const lookupCase=options.caseType===undefined?activeCase:options.caseType;
    const wrap=el('div'),find=button('Find labels','secondary'),more=button('More matches','secondary'),use=button('Use label','secondary');
    find.id=options.id('Find');more.id=options.id('More');use.id=options.id('Use');
    const subject=options.subject;
    find.setAttribute('aria-label','Find matching '+subject);
    more.setAttribute('aria-label','More matches for '+subject);
    use.setAttribute('aria-label','Use selected '+subject);
    const choices=select(options.id('Matches'),[]),choiceField=field('Matches for '+subject,choices);
    const status=el('div',null,'muted');status.setAttribute('role','status');status.id=options.id('LookupStatus');
    show(choiceField,false);show(use,false);show(more,false);let turn=0,next=null;
    const reset=()=>{turn++;next=null;choices.replaceChildren();show(choiceField,false);show(use,false);show(more,false);status.textContent='';};
    const current=t=>wrap.isConnected&&lookupGeneration===state.generation&&lookupLog===state.logId&&activeCase===state.caseType&&t===turn;
    async function load(after){
      const search=exact.value;
      if(!search||Array.from(search).length>200){status.textContent='Enter 1 to 200 characters to search.';return;}
      if(activityLookupBusy){status.textContent='Wait for the current activity lookup to finish.';return;}
      const ticket=++turn;activityLookupBusy=true;find.disabled=true;more.disabled=true;use.disabled=true;status.textContent='Searching the current case notion…';
      try{
        const result=await (await api('/v1/event-logs/'+encodeURIComponent(lookupLog)+'/activity-labels/search',{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({search,after,case_type:lookupCase||null})})).json();
        if(!current(ticket)||exact.value!==search)return;
        const labels=Array.isArray(result.labels)?result.labels:[];
        setOptions(choices,labels.map(label=>[label,label]));next=result.next_after??null;
        show(choiceField,labels.length>0);show(use,labels.length>0);show(more,next!==null);
        status.textContent=labels.length+' matching labels on this page. Search is case-sensitive; results can change between pages.';
      }catch(error){if(current(ticket))status.textContent=error.message;}
      finally{activityLookupBusy=false;if(wrap.isConnected&&lookupGeneration===state.generation&&lookupLog===state.logId&&activeCase===state.caseType){find.disabled=false;more.disabled=false;use.disabled=false;}}
    }
    find.onclick=()=>load('');more.onclick=()=>{if(next!==null)load(next);};
    use.onclick=()=>{if(!choices.value)return;exact.value=choices.value;exact.dispatchEvent(new Event('input',{bubbles:true}));exact.focus();};
    wrap.append(find,choiceField,use,more,status);return {wrap,reset};
  }


  function caseFilterSection(activityNames){
    const box=section('Case filter');box.id='owgCaseFilter';
    box.appendChild(el('div','Keep only cases where one activity is (or is never) followed by another, directly or later, or that start or end with an activity or last a given time. Every rule must hold. The filter applies to the overview, variants, activities, KPIs, process map and playback, dotted chart, waiting time, drift, discovery and organization; other sections use every case. Matching describes recorded order only.','muted'));
    box.appendChild(el('div','Suggestions show up to 500 frequent activities. Enter an exact label to override a suggestion; matching is literal and an unknown label may match no cases.','muted'));
    const draft=state.follows.map(followRow);
    const rows=el('div');rows.id='owgCaseFilterRules';
    const add=button('Add rule','secondary');add.id='owgCaseFilterAdd';
    const apply=button('Apply filter');apply.id='owgCaseFilterApply';
    const clear=button('Clear filter','secondary');clear.id='owgCaseFilterClear';
    const contains=input('owgCaseFilterContainsActivity','text','Exact activity label (optional)');contains.maxLength=400;contains.value=state.caseActivity;
    const containsLookup=activityLabelLookup(contains,{id:part=>'owgCaseFilterContainsActivity'+part,subject:'case filter activity'});contains.addEventListener('input',containsLookup.reset);
    const activeText=[...state.follows.map(followText),...(state.caseActivity?['contains activity '+JSON.stringify(state.caseActivity)]:[]),attrText()].filter(Boolean).join('; ');
    const message=el('div',filterOn()?'Active: '+activeText+'.':'No case filter: every case is included.','muted');
    // Case attributes (from the import or a joined case table): one value, and/or a numeric range.
    const attrValue=input('owgCaseFilterAttribute','text','for example Region=EU');attrValue.maxLength=200;attrValue.value=state.attr.attribute||'';
    const rangeName=input('owgCaseFilterRangeName','text','for example amount');rangeName.maxLength=200;
    const rangeMin=input('owgCaseFilterRangeMin','number','from (inclusive)');rangeMin.step='any';
    const rangeMax=input('owgCaseFilterRangeMax','number','below (exclusive)');rangeMax.step='any';
    if(state.attr.attribute_range){const r=state.attr.attribute_range;rangeName.value=r.name;if(r.min!==undefined)rangeMin.value=r.min;if(r.max!==undefined)rangeMax.value=r.max;}
    const attrRow=el('div',null,'row');attrRow.style.alignItems='end';
    attrRow.append(field('Case attribute equals (name=value)',attrValue),field('Numeric case attribute',rangeName),field('From',rangeMin),field('Below',rangeMax));
    // Endpoints and performance filters (Disco/Celonis): first or last activity, and case duration.
    const startsWith=input('owgCaseFilterStartsWith','text','exact first activity');startsWith.maxLength=400;startsWith.value=state.attr.starts_with||'';
    const endsWith=input('owgCaseFilterEndsWith','text','exact last activity');endsWith.maxLength=400;endsWith.value=state.attr.ends_with||'';
    const durMin=input('owgCaseFilterDurationMin','number','hours');durMin.step='any';durMin.min='0';
    const durMax=input('owgCaseFilterDurationMax','number','hours');durMax.step='any';durMax.min='0';
    if(state.attr.duration){if(state.attr.duration.min!==undefined)durMin.value=state.attr.duration.min/3600;if(state.attr.duration.max!==undefined)durMax.value=state.attr.duration.max/3600;}
    const endRow=el('div',null,'row');endRow.style.alignItems='end';
    endRow.append(field('Case starts with',startsWith),field('Case ends with',endsWith),field('Case duration at least (hours)',durMin),field('Case duration below (hours)',durMax));
    // Timeframe by end, events per case, path frequency (variation) and rework.
    const endedFrom=input('owgCaseFilterEndedFrom','date',''),endedBefore=input('owgCaseFilterEndedBefore','date','');
    const evMin=input('owgCaseFilterEventsMin','number','events'),evMax=input('owgCaseFilterEventsMax','number','events');
    const pathMin=input('owgCaseFilterPathMin','number','cases'),pathMax=input('owgCaseFilterPathMax','number','cases');
    for(const n of [evMin,evMax,pathMin,pathMax]){n.step='1';n.min='1';}
    const reworkPick=select('owgCaseFilterRework',[['','Any'],['true','Repeats an activity'],['false','Repeats no activity']]);
    // Case tags (yours, or added from unusual cases, alignments or root causes): keep tagged or untagged cases.
    const tagName=input('owgCaseFilterTag','text','tag');tagName.maxLength=60;tagName.autocomplete='off';
    const tagMode=select('owgCaseFilterTagMode',[['tag','Has the tag'],['without_tag','Does not have the tag']]);
    // Cases that never do an activity (the opposite of "contains activity").
    const withoutActivity=input('owgCaseFilterWithoutActivity','text','Exact activity label');withoutActivity.maxLength=200;withoutActivity.autocomplete='off';
    const a0=state.attr;
    if(a0.without_activity)withoutActivity.value=a0.without_activity;
    if(a0.ended_from)endedFrom.value=a0.ended_from.slice(0,10);if(a0.ended_before)endedBefore.value=a0.ended_before.slice(0,10);
    if(a0.events){if(a0.events.min!==undefined)evMin.value=a0.events.min;if(a0.events.max!==undefined)evMax.value=a0.events.max;}
    if(a0.path_cases){if(a0.path_cases.min!==undefined)pathMin.value=a0.path_cases.min;if(a0.path_cases.max!==undefined)pathMax.value=a0.path_cases.max;}
    if(a0.rework!==undefined)reworkPick.value=String(a0.rework);
    if(a0.tag){tagName.value=a0.tag;tagMode.value='tag';}else if(a0.without_tag){tagName.value=a0.without_tag;tagMode.value='without_tag';}
    const moreRow=el('div',null,'row');moreRow.style.alignItems='end';
    moreRow.append(field('Case ended on or after',endedFrom),field('Case ended before',endedBefore),field('Events per case at least',evMin),field('Events per case at most',evMax),
      field('Path followed by at least (cases)',pathMin),field('Path followed by at most (cases)',pathMax),field('Rework',reworkPick),
      field('Case tag',tagName),field('Tag condition',tagMode),field('Case never does activity',withoutActivity));
    function readCount(lo,hi,what){
      if(lo.value===''&&hi.value==='')return undefined;
      const r={};if(lo.value!=='')r.min=Number(lo.value);if(hi.value!=='')r.max=Number(hi.value);
      if([r.min,r.max].some(v=>v!==undefined&&!(Number.isInteger(v)&&v>=1)))throw new Error(what+' bounds must be whole numbers of 1 or more.');
      if(r.min!==undefined&&r.max!==undefined&&r.min>r.max)throw new Error(what+': the lower bound must not be above the upper bound.');
      return r;
    }
    function readAttr(){
      const out={},value=attrValue.value.trim();
      if(value){if(!value.includes('=')||!value.split('=')[0].trim())throw new Error('Write the case attribute as name=value.');out.attribute=value;}
      const name=rangeName.value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu,'');
      if(name||rangeMin.value||rangeMax.value){
        if(!name)throw new Error('Name the numeric case attribute.');
        const r={name};if(rangeMin.value!=='')r.min=Number(rangeMin.value);if(rangeMax.value!=='')r.max=Number(rangeMax.value);
        if(r.min===undefined&&r.max===undefined)throw new Error('Give a lower bound, an upper bound or both.');
        if((r.min!==undefined&&!Number.isFinite(r.min))||(r.max!==undefined&&!Number.isFinite(r.max)))throw new Error('Numeric range bounds must be finite numbers.');
        if(r.min!==undefined&&r.max!==undefined&&!(r.min<r.max))throw new Error('The lower bound must be below the upper bound.');
        out.attribute_range=r;}
      for(const [key,node,what] of [['starts_with',startsWith,'first'],['ends_with',endsWith,'last']]){
        const v=node.value;if(!v)continue;
        if(/^[\p{White_Space}\u001c-\u001f]*$/u.test(v)||Array.from(v).length>200)throw new Error('Enter a '+what+' activity label of at most 200 characters.');
        out[key]=v;}
      if(durMin.value!==''||durMax.value!==''){
        const d={};if(durMin.value!=='')d.min=Number(durMin.value)*3600;if(durMax.value!=='')d.max=Number(durMax.value)*3600;
        if([d.min,d.max].some(v=>v!==undefined&&!(Number.isFinite(v)&&v>=0)))throw new Error('Case duration bounds must be zero or more hours.');
        if(d.min!==undefined&&d.max!==undefined&&!(d.min<d.max))throw new Error('The shortest case duration must be below the longest.');
        out.duration=d;}
      if(endedFrom.value)out.ended_from=endedFrom.value+'T00:00:00Z';
      if(endedBefore.value)out.ended_before=endedBefore.value+'T00:00:00Z';
      if(out.ended_from&&out.ended_before&&!(out.ended_from<out.ended_before))throw new Error('The end period is empty: the first date must be before the second.');
      const events=readCount(evMin,evMax,'Events per case'),paths=readCount(pathMin,pathMax,'Path frequency');
      if(events)out.events=events;if(paths)out.path_cases=paths;
      if(reworkPick.value)out.rework=reworkPick.value==='true';
      const never=withoutActivity.value;
      if(never){if(/^[\p{White_Space}\u001c-\u001f]*$/u.test(never)||Array.from(never).length>200)throw new Error('Enter an activity label of at most 200 characters for "never does".');out.without_activity=never;}
      const t=tagName.value.trim();if(t){if(Array.from(t).length>60)throw new Error('A tag has at most 60 characters.');out[tagMode.value]=t;}
      return out;
    }
    message.id='owgCaseFilterStatus';message.setAttribute('role','status');message.tabIndex=-1;
    const names=activityNames.slice(0,500).map(a=>[a,a]);
    function render(){
      rows.replaceChildren(...draft.map((rule,i)=>{
        const n=i+1;
        const from=select('owgCaseFilterFrom'+n,names),mode=select('owgCaseFilterMode'+n,[['eventual','is eventually followed by'],['direct','is directly followed by'],['not-eventual','is never eventually followed by'],['not-direct','is never directly followed by']]),to=select('owgCaseFilterTo'+n,names);
        const exactFrom=input('owgCaseFilterFromExact'+n,'text','Exact label (optional)'),exactTo=input('owgCaseFilterToExact'+n,'text','Exact label (optional)');
        // 400 UTF-16 units allow 200 supplementary Unicode characters; the
        // literal rule validator enforces 200 Unicode code points.
        exactFrom.maxLength=exactTo.maxLength=400;
        const has=value=>names.some(option=>option[0]===value);
        from.value=has(rule.from)?rule.from:(names[0]?.[0]||'');
        to.value=has(rule.to)?rule.to:(names[0]?.[0]||'');mode.value=rule.mode;
        exactFrom.value=has(rule.from)?'':rule.from;exactTo.value=has(rule.to)?'':rule.to;
        from.onchange=()=>{exactFrom.value='';rule.from=from.value;fromLookup.reset();};
        to.onchange=()=>{exactTo.value='';rule.to=to.value;toLookup.reset();};
        const fromLookup=activityLabelLookup(exactFrom,{id:part=>'owgCaseFilterFrom'+part+n,subject:'activity for rule '+n}),toLookup=activityLabelLookup(exactTo,{id:part=>'owgCaseFilterTo'+part+n,subject:'later activity for rule '+n});
        exactFrom.oninput=()=>{rule.from=exactFrom.value||from.value;fromLookup.reset();};
        exactTo.oninput=()=>{rule.to=exactTo.value||to.value;toLookup.reset();};mode.onchange=()=>{rule.mode=mode.value;};
        const minH=input('owgCaseFilterMinHours'+n,'number','hours'),maxH=input('owgCaseFilterMaxHours'+n,'number','hours');
        for(const [node,key] of [[minH,'min_hours'],[maxH,'max_hours']]){node.min='0';node.step='any';if(rule[key]!==undefined)node.value=rule[key];node.oninput=()=>{rule[key]=node.value;};}
        const remove=button('Remove','secondary');remove.setAttribute('aria-label','Remove rule '+n);remove.onclick=()=>{draft.splice(i,1);render();};
        const row=el('div',null,'row');row.style.alignItems='end';
        row.append(field('Rule '+n+': activity',from),field('Rule '+n+': exact activity',exactFrom),fromLookup.wrap,field('Rule '+n+': order',mode),field('Rule '+n+': later activity',to),field('Rule '+n+': exact later activity',exactTo),toLookup.wrap,
          field('Rule '+n+': after at least (hours)',minH),field('Rule '+n+': within (hours)',maxH),remove);
        return row;
      }));
      add.disabled=draft.length>=5;
    }
    add.onclick=()=>{draft.push({from:names[0]?.[0]||'',to:(names[1]||names[0])?.[0]||'',mode:'eventual'});render();const focus=document.getElementById((names.length?'owgCaseFilterFrom':'owgCaseFilterFromExact')+draft.length);if(focus)focus.focus();};
    apply.onclick=()=>{try{if(contains.value&&(/^[\p{White_Space}\u001c-\u001f]*$/u.test(contains.value)||Array.from(contains.value).length>200))throw new Error('Enter an activity label of at most 200 characters.');
      const follows=followSpec(draft),attr=readAttr();state.follows=follows;state.attr=attr;state.caseActivity=contains.value;analyse().catch(error=>status(error.message));}catch(error){message.textContent=error.message;}};
    clear.onclick=()=>{state.follows=[];state.attr={};state.caseActivity='';analyse().catch(error=>status(error.message));};
    render();
    const controls=el('div',null,'row');controls.append(add,apply,clear);
    box.append(rows,field('Case contains exact activity',contains),containsLookup.wrap,attrRow,
      caseAttributeSearch(attrValue,rangeName,rangeMin,rangeMax),endRow,moreRow,controls,message,filterSetBlock(activityNames));
    return box;
  }

  // Named follower-rule sets saved with the log, applied to every filtered view at once.
  function filterSetBlock(activityNames){
    const wrap=el('div');wrap.id='owgFilterSets';
    const name=input('owgFilterSetName','text','Filter name');name.maxLength=120;
    const save=button('Save active filter','secondary');save.id='owgFilterSetSave';save.disabled=!filterOn();
    const message=el('div',filterOn()?'':'Apply a filter to save it.','muted');message.id='owgFilterSetStatus';message.setAttribute('role','status');
    const list=el('div');list.id='owgFilterSetList';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Name for the active filter',name),save);
    wrap.append(el('h4','Saved filters'),row,message,list);
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/filter-sets';
    const generation=state.generation;
    async function refresh(){
      const r=await getJSON(base());if(generation!==state.generation)return;
      list.replaceChildren(r.filter_sets.length?table(['Filter','Rules','Notes',''],r.filter_sets.map(set=>{
        const follows=set.cohort.follows||[],activity=set.cohort.activity||'',other=Object.keys(set.cohort).filter(k=>!['follows','attribute','attribute_range','activity','starts_with','ends_with','duration',...MORE_FILTER_KEYS].includes(k));
        const attr={...(set.cohort.attribute?{attribute:set.cohort.attribute}:{}),...(set.cohort.attribute_range?{attribute_range:set.cohort.attribute_range}:{}),...(set.cohort.starts_with?{starts_with:set.cohort.starts_with}:{}),...(set.cohort.ends_with?{ends_with:set.cohort.ends_with}:{}),...(set.cohort.duration?{duration:set.cohort.duration}:{}),...Object.fromEntries(MORE_FILTER_KEYS.filter(k=>set.cohort[k]!==undefined).map(k=>[k,set.cohort[k]]))};
        const use=button('Apply','secondary');use.setAttribute('aria-label','Apply saved filter '+set.name);use.disabled=!(follows.length||Object.keys(attr).length||activity)||other.length>0;
        use.onclick=()=>{state.follows=follows;state.attr=attr;state.caseActivity=activity;analyse().catch(error=>status(error.message));};
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete saved filter '+set.name);
        del.onclick=async()=>{try{await api(base()+'/'+encodeURIComponent(set.set_id)+'/delete',{method:'POST'});message.textContent='Saved filter deleted.';await refresh();}catch(error){message.textContent=error.message;}};
        const notes=[...(set.activities_not_in_log.length?['Not in this log: '+set.activities_not_in_log.join(', ')]:[]),
          ...(set.log_appended_since_saved?['Newer exports were appended since it was saved']:[]),
          ...(other.length?['Also filters by '+other.join(', ')+', which this panel cannot apply']:[])];
        const actions=el('span');actions.append(use,' ',del);
        return [set.name,[...follows.map(followText),...(activity?['contains activity '+JSON.stringify(activity)]:[]),attr.attribute||'',attr.attribute_range?rangeText(attr.attribute_range):'',attr.starts_with?'starts with '+JSON.stringify(attr.starts_with):'',attr.ends_with?'ends with '+JSON.stringify(attr.ends_with):'',attr.duration?durationText(attr.duration):'',moreFilterText(attr)].filter(Boolean).join('; ')||'—',notes.join('. ')||'—',actions];
      })):el('div','No saved filters yet.','muted'));
    }
    save.onclick=async()=>{
      try{
        await api(base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.value,cohort:globalCohort()})});
        message.textContent='Saved "'+name.value.trim()+'".';name.value='';await refresh();
      }catch(error){message.textContent=error.message;}
    };
    refresh().catch(error=>{message.textContent=error.message;});
    return wrap;
  }

  function cohortSpec(values){
    const spec={};
    const day=(v,label)=>{if(!v)return '';if(!/^\d{4}-\d{2}-\d{2}$/.test(v))throw new Error(label+' must be a date.');return v+'T00:00:00Z';};
    if(values.label&&values.label.trim())spec.label=values.label.trim().slice(0,80);
    const from=day(values.from,'Started from'),before=day(values.before,'Started before');
    if(from)spec.started_from=from;if(before)spec.started_before=before;
    if(from&&before&&from>=before)throw new Error('The cohort period is empty.');
    for(const key of ['resource','activity'])if(values[key]&&values[key].trim())spec[key]=values[key];
    if(values.attribute&&values.attribute.trim()){if(!/^[^=]+=.*/.test(values.attribute.trim()))throw new Error('Attribute must look like name=value.');spec.attribute=values.attribute.trim();}
    if(values.eventAttribute&&values.eventAttribute.trim()){if(!/^[^=]+=.*/.test(values.eventAttribute.trim()))throw new Error('Event attribute must look like name=value.');spec.event_attribute=values.eventAttribute.trim();}
    if(values.tag&&values.tag.trim())spec.tag=values.tag.trim().slice(0,60);
    // A numeric case attribute between two bounds (lower inclusive, upper exclusive; either may be left empty).
    const rangeName=(values.rangeName||'').replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu,''),rangeMin=values.rangeMin??'',rangeMax=values.rangeMax??'';
    if(rangeName||rangeMin!==''||rangeMax!==''){
      if(!rangeName)throw new Error('Name the numeric case attribute.');
      if(Array.from(rangeName).length>200)throw new Error('Enter a numeric case attribute name of at most 200 characters.');
      const r={name:rangeName};if(rangeMin!=='')r.min=Number(rangeMin);if(rangeMax!=='')r.max=Number(rangeMax);
      if(r.min===undefined&&r.max===undefined)throw new Error('Give a lower bound, an upper bound or both.');
      if([r.min,r.max].some(v=>v!==undefined&&!Number.isFinite(v)))throw new Error('Range bounds must be numbers.');
      if(r.min!==undefined&&r.max!==undefined&&!(r.min<r.max))throw new Error('The lower bound must be below the upper bound.');
      spec.attribute_range=r;}
    return spec;
  }
  function cohortFields(prefix,legend,activityNames){
    const box=document.createElement('fieldset');box.id=prefix;box.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin-top:8px';
    box.appendChild(el('legend',legend));
    const label=input(prefix+'Label','text','Name');label.maxLength=80;
    const from=input(prefix+'From','date'),before=input(prefix+'Before','date');
    const resource=input(prefix+'Resource','text','any');
    const activity=select(prefix+'Activity',[['','any'],...activityNames.slice(0,500).map(a=>[a,a])]);
    const attribute=input(prefix+'Attribute','text','name=value');
    const eventAttribute=input(prefix+'EventAttribute','text','name=value');eventAttribute.title='Cases with at least one event carrying this value, such as a column kept from a CSV import';
    const tag=input(prefix+'Tag','text','any');tag.maxLength=60;tag.title='Cases you tagged under Case tags';
    const rangeName=input(prefix+'RangeName','text','numeric attribute');rangeName.maxLength=200;
    const rangeMin=input(prefix+'RangeMin','number','from');rangeMin.step='any';
    const rangeMax=input(prefix+'RangeMax','number','below');rangeMax.step='any';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Name',label),field('Started from (UTC)',from),field('Started before (UTC)',before),field('Resource',resource),field('Contains activity',activity),field('Case attribute',attribute),field('Event attribute',eventAttribute),field('Tagged',tag));
    const rangeRow=el('div',null,'row');rangeRow.style.alignItems='end';
    rangeRow.append(field('Numeric case attribute',rangeName),field('From (inclusive)',rangeMin),field('Below (exclusive)',rangeMax));
    box.append(row,rangeRow);
    return {node:box,read:()=>cohortSpec({label:label.value,from:from.value,before:before.value,resource:resource.value,activity:activity.value,attribute:attribute.value,eventAttribute:eventAttribute.value,tag:tag.value,rangeName:rangeName.value,rangeMin:rangeMin.value,rangeMax:rangeMax.value})};
  }
  function variantCoverageSection(activityNames){
    const box=section('Variant coverage');
    const run=button('Analyse variant coverage','secondary');run.id='owgVariantCoverageRun';
    const budget=select('owgVariantCoverageLimit',[['20','20 variants'],['50','50 variants'],['100','100 variants'],['200','200 variants']]);
    const inspection=caseInspector(activityNames,{prefix:'owgVariantCoverageCases'});
    for(const control of inspection.node.querySelectorAll('button'))control.setAttribute('aria-label','Variant coverage: '+control.textContent);
    const out=el('div');box.append(el('p','See how many observed paths cover most cases, before deciding which variations to investigate.'),field('Variants per page',budget),run,out,inspection.node);
    const generation=state.generation;
    let revision=0;
    budget.onchange=()=>{++revision;run.disabled=false;out.replaceChildren();};
    async function load(offset){
      const ticket=++revision,cohort=globalCohort();
      const current=()=>generation===state.generation&&ticket===revision;
      run.disabled=true;
      out.replaceChildren(el('div','Calculating coverage…','muted'));
      try{
        const r=await getJSON(logPath('variant-coverage',{limit:budget.value,offset}));
        if(!current())return;
        if(offset&&offset>=r.distinct_variants)return load(Math.max(0,Math.floor((r.distinct_variants-1)/r.page.limit)*r.page.limit));
        const exports=exportButtons('owgVariantCoverage',()=>r,()=>[
          ['rank','path','cases','case_share','cumulative_cases','cumulative_share','path_truncated'],
          ...r.displayed_variants.map(v=>[v.rank,JSON.stringify(v.path),v.cases,v.share,v.cumulative_cases,v.cumulative_share,v.path_truncated])]);
        exports.enable(true);
        const previous=button('Previous page','secondary'),next=button('Next page','secondary');
        previous.disabled=offset===0;next.disabled=r.page.next_offset===null;
        previous.onclick=()=>load(offset-r.page.limit);next.onclick=()=>load(r.page.next_offset);
        const navigation=el('div',null,'row');navigation.append(previous,next);
        const coverage=el('p',r.displayed_variants.length?'Showing ranks '+(offset+1).toLocaleString()+'–'+(offset+r.displayed_variants.length).toLocaleString()+' of '+r.distinct_variants.toLocaleString()+'.':'No variants in this selection.','muted');
        coverage.setAttribute('role','status');
        out.replaceChildren(
          table(['Target case coverage','Variants needed'],r.coverage_targets.map(t=>[t.target_percent+'%',t.variants_needed??'—'])),
          coverage,
          table(['Rank','Observed path','Cases','Cumulative coverage','Evidence'],r.displayed_variants.map(v=>{
            const inspect=button('Inspect variant '+v.rank+' cases','secondary');
            inspect.onclick=()=>inspection.variant(v.variant_id,v.rank,cohort);
            return [v.rank,v.path.join(' → ')+(v.path_truncated?' … ('+v.path_activity_count+' steps)':''),v.cases,pct(v.cumulative_share),inspect];
          })),
          navigation,
          el('p',r.cases+' cases across '+r.distinct_variants+' variants. '+r.omitted_variants+' variants containing '+r.omitted_cases+' cases are outside the displayed rows. Targets use every variant, including omitted rows.','muted'),
          el('p',r.interpretation,'muted'),exports.node);
      }catch(error){if(current())out.replaceChildren(el('p',error.message,'muted'));}
      finally{if(current())run.disabled=false;}
    }
    run.onclick=()=>load(0);
    return box;
  }

  function toCSV(rows){
    const cell=v=>{const t=v===null||v===undefined?'':String(v);const safe=typeof v!=='number'&&(/^[\t\r\n]/.test(t)||/^[\s\ufeff]*[=+\-@]/.test(t))?"'"+t:t;return /[",\n\r]/.test(safe)?'"'+safe.replace(/"/g,'""')+'"':safe;};
    return rows.map(r=>r.map(cell).join(',')).join('\r\n')+'\r\n';
  }
  function download(name,text,type){
    const url=URL.createObjectURL(new Blob([text],{type}));
    const link=el('a');link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  // Analyst tags on cases: mark cases while investigating, then filter any analysis with "Tagged".
  // Explain one case: compare each of its gaps with the typical gap of the same transition.
  // Owner-chosen, read-only process intelligence for connected AI agents (MCP), per log.
  // For a log made from AI agent runs: append runs that finished since, now or every day.
  function agentRunsRefreshSection(){
    const box=section('Keep agent runs up to date');box.id='owgAgentRunsRefresh';box.hidden=true;
    const info=el('div',null,'muted');info.id='owgAgentRunsRefreshInfo';
    const now=button('Add new runs now','secondary');now.id='owgAgentRunsRefreshNow';
    const daily=button('Add new runs every day','secondary');daily.id='owgAgentRunsRefreshDaily';
    const out=el('div');out.id='owgAgentRunsRefreshStatus';out.setAttribute('role','status');
    const row=el('div',null,'row');row.append(now,daily);
    box.append(info,row,out);
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/agent-runs',generation=state.generation,logId=state.logId;
    const describe=r=>{info.textContent='This log was made from observed AI agent runs'+(r.include_model_calls?' (with model calls)':'')+'. Last updated '+String(r.last_refreshed_at||r.created_at).slice(0,16).replace('T',' ')+' UTC. New finished runs are appended; runs already here stay as they are.';};
    getJSON(base).then(r=>{if(generation!==state.generation||!r.agent_runs)return;box.hidden=false;describe(r);}).catch(()=>{});
    now.onclick=async()=>{now.disabled=true;out.textContent='Reading observed agent runs\u2026';
      try{const r=await (await api(base+'/refresh',{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}})).json();
        out.textContent=(r.runs_added?'Added '+r.runs_added+' run(s) with '+r.events_added+' steps. Analyse again to include them.':'No new finished runs.')+(r.runs_still_running?' '+r.runs_still_running+' run(s) still in progress will be added later.':'');
        describe(await getJSON(base));}
      catch(error){out.textContent=error.message;}finally{now.disabled=false;}};
    daily.onclick=async()=>{if(!window.confirm('Append newly finished AI agent runs to this log every day while OpenWorkGraph is running?'))return;
      try{await api('/v1/schedules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'event_log_agent_runs',target_id:logId,interval_minutes:1440,confirm_scheduled_runs:true})});
        out.textContent='Scheduled daily. Manage it under scheduled jobs. Signal rules on this log then watch your agents.';}
      catch(error){out.textContent=error.message;}};
    return box;
  }

  // Automatic deletion: the owner picks a date after which this log is deleted from this computer.
  function expirySection(){
    const box=section('Automatic deletion');box.id='owgExpiry';
    box.appendChild(el('div','Choose a date after which this log, and everything derived from it, is deleted from this computer automatically. You can change or clear the date at any time. Until then nothing changes.','muted'));
    const date=input('owgExpiryDate','date','');
    const save=button('Delete automatically after this date','secondary');save.id='owgExpirySave';
    const clear=button('Keep the log','secondary');clear.id='owgExpiryClear';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Delete after (end of day, UTC)',date),save,clear);
    const statusLine=el('div',null,'muted');statusLine.id='owgExpiryStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    box.append(row,statusLine);
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/expiry',generation=state.generation;
    const tomorrow=new Date(Date.now()+86400000).toISOString().slice(0,10);date.min=tomorrow;
    const show=r=>{if(generation!==state.generation)return;
      date.value=r.delete_after?r.delete_after.slice(0,10):'';clear.disabled=!r.delete_after;
      statusLine.textContent=r.delete_after?'This log will be deleted after '+r.delete_after.slice(0,16).replace('T',' ')+' UTC.':'This log is kept until you delete it.';};
    const send=async body=>{save.disabled=clear.disabled=true;
      try{show(await (await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json());}
      catch(error){statusLine.textContent=error.message;clear.disabled=false;}
      finally{save.disabled=false;}};
    save.onclick=()=>{if(!date.value){statusLine.textContent='Choose a date first.';return;}
      if(!window.confirm('Delete this log automatically after '+date.value+'? It cannot be recovered afterwards.'))return;
      send({delete_after:date.value+'T23:59:59Z'});};
    clear.onclick=()=>send({delete_after:null});
    getJSON(base).then(show).catch(error=>{statusLine.textContent=error.message;});
    return box;
  }

  function agentShareSection(){
    const box=section('Share with AI agents');box.id='owgAgentShare';
    box.appendChild(el('div','Let AI apps connected to OpenWorkGraph (for example Claude) ask about this process: overview, KPIs, variants, insights and root cause. At the cases level they can also explain individual cases. They can only read, unless you also let them tag cases below. AI access must also be on, and answers are redacted at your AI-context detail level, so case IDs stay pseudonymous unless you choose full detail.','muted'));
    const level=select('owgAgentShareLevel',[['','Not shared'],['aggregates','Process overview, insights and root cause'],['cases','Also individual cases']]);
    const apply=button('Save sharing','secondary');apply.id='owgAgentShareSave';
    const out=el('div');out.id='owgAgentShareStatus';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('AI agents may read',level),apply);
    const tagBox=input('owgAgentShareTags','checkbox');
    const tagLabel=el('label',' Let AI agents tag cases (tags start with \u201cagent:\u201d and say an agent added them; nothing else changes)');tagLabel.prepend(tagBox);
    const tagOut=el('div');tagOut.id='owgAgentShareTagsStatus';tagOut.setAttribute('role','status');
    // What connected agents asked for: time, tool and outcome only, never parameters or answers.
    const reads=button('Show recent agent reads','secondary');reads.id='owgAgentAccessLoad';
    const readsOut=el('div');readsOut.id='owgAgentAccess';readsOut.setAttribute('role','status');readsOut.setAttribute('aria-live','polite');
    box.append(row,out,tagLabel,tagOut,reads,readsOut);
    const OUTCOMES={ok:'Answered',not_shared:'Refused: not shared at that level',not_allowed:'Refused: not allowed',invalid:'Refused: invalid request',error:'Failed: the tool hit an error'};
    const toolName=op=>op.replace(/_/g,' ');
    reads.onclick=async()=>{reads.disabled=true;readsOut.replaceChildren(el('div','Loading agent reads\u2026','muted'));
      try{const r=await getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/agent-access?limit=50');
        if(!r.recorded){readsOut.replaceChildren(el('div','No AI agent has read this log yet.','muted'));return;}
        const summary=el('div',r.recorded+' agent call'+(r.recorded===1?'':'s')+' recorded'+(r.recorded>=r.kept_at_most?' (only the latest '+r.kept_at_most+' are kept)':'')+'. '+r.note,'muted');
        const counts=table(['Tool (last 30 days)','Calls','Refused'],r.last_30_days.map(x=>[toolName(x.operation),String(x.calls),String(x.refused)]));counts.id='owgAgentAccessTools';
        const calls=table(['When (UTC)','Tool','Outcome'],r.calls.map(c=>[c.at.slice(0,19).replace('T',' '),toolName(c.operation),OUTCOMES[c.outcome]||c.outcome]));calls.id='owgAgentAccessCalls';
        readsOut.replaceChildren(summary,counts,el('h4','Latest calls'),calls);}
      catch(error){readsOut.replaceChildren(el('div',error.message));}
      finally{reads.disabled=false;}};
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/agent-share',generation=state.generation;
    const show=r=>{level.value=r.level||'';
      out.textContent=r.level?('Shared with AI agents since '+String(r.granted_at).slice(0,16).replace('T',' ')+' UTC.'+(r.tools_listed?' Restart a connected AI app to see the process tools.':'')):'Not shared: AI agents cannot read this log.';
      tagLabel.hidden=r.level!=='cases';tagBox.checked=Boolean(r.agents_may_tag_cases);};
    tagBox.onchange=async()=>{
      const allowed=tagBox.checked;
      if(allowed&&!window.confirm('Let connected AI apps add tags to cases in this log? Their tags start with \u201cagent:\u201d and you can delete them.')){tagBox.checked=false;return;}
      try{const r=await (await api(base+'/tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({allowed})})).json();
        show(r);tagOut.textContent=r.agents_may_tag_cases?'AI agents may tag cases.':'AI agents cannot tag cases.';}
      catch(error){tagBox.checked=!allowed;tagOut.textContent=error.message;}
    };
    getJSON(base).then(r=>{if(generation===state.generation)show(r);}).catch(error=>{out.textContent=error.message;});
    apply.onclick=async()=>{
      if(level.value&&!window.confirm('Let connected AI apps read '+(level.value==='cases'?'this process and its individual cases':'this process overview, insights and root cause')+'? You can stop sharing at any time.'))return;
      try{show(await (await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({level:level.value||null})})).json());if(typeof listShares==='function')listShares();}
      catch(error){out.textContent=error.message;}
    };
    // One action that withdraws every share at once, for every log.
    const stopAll=button('Stop sharing every log','secondary');stopAll.id='owgAgentShareStopAll';
    const stopOut=el('div');stopOut.id='owgAgentShareStopAllStatus';stopOut.setAttribute('role','status');
    const everyShare=el('div',null,'muted');everyShare.id='owgAgentShareEvery';
    const LEVEL_TEXT={aggregates:'process overview',cases:'also individual cases'};
    const listShares=async()=>{try{const r=await getJSON('/v1/process-intelligence/logs');
      everyShare.textContent=r.logs.length?'Shared with AI agents now: '+r.logs.map(l=>l.name+' ('+(LEVEL_TEXT[l.level]||l.level)+')').join(', ')+'.':'No log is shared with AI agents.';}
      catch(error){everyShare.textContent=error.message;}};
    box.append(everyShare,stopAll,stopOut);
    listShares();
    stopAll.onclick=async()=>{
      if(!window.confirm('Stop sharing every event log with AI agents, including tag permissions?'))return;
      stopAll.disabled=true;
      try{const r=await (await api('/v1/process-intelligence/stop-sharing',{method:'POST'})).json();
        stopOut.textContent=r.stopped?'Stopped sharing '+r.stopped+' log'+(r.stopped===1?'':'s')+': '+r.logs.join(', ')+'. '+r.note:'No log was shared with AI agents.';
        show(await getJSON(base));listShares();}
      catch(error){stopOut.textContent=error.message;}
      finally{stopAll.disabled=false;}};
    return box;
  }

  // Automation candidates: activities ranked by volume, routine routing and the waiting before them.
  function automationSection(){
    const box=section('Automation candidates');box.id='owgAutomation';
    box.appendChild(el('div','Rank this process\u2019s activities by how often they happen, how fixed their routing is and how much waiting comes right before them. Every component is shown. It is evidence for a review, not a recommendation, and it estimates no savings.','muted'));
    const run=button('Rank activities','secondary');run.id='owgAutomationRun';
    const out=el('div');out.id='owgAutomationResults';out.setAttribute('role','status');
    box.append(run,out);
    const PATTERNS={fixed:'Fixed',mostly_fixed:'Mostly fixed',variable:'Variable'};
    const pct=x=>Math.round(x*100)+'%';
    run.onclick=async()=>{run.disabled=true;out.replaceChildren(el('div','Ranking activities\u2026','muted'));
      try{
        const r=await getJSON(logPath('automation-candidates',{limit:15}));
        if(!r.candidates.length){out.replaceChildren(el('div','No activity occurs in at least '+r.min_cases+' cases.','muted'));return;}
        const logName=(state.logs.find(l=>l.log_id===state.logId)||{}).name||state.logId;
        const rows=r.candidates.map(c=>{
          const add=button('Add to automation pipeline','secondary');add.setAttribute('aria-label','Add '+c.activity+' to the automation pipeline');
          add.onclick=async()=>{add.disabled=true;
            try{
              const description=('From event log \u201c'+logName+'\u201d: '+c.occurrences+' occurrences in '+c.cases+' cases ('+c.per_week+' per week), '+
                PATTERNS[c.routing_pattern].toLowerCase()+' routing (same predecessor '+pct(c.same_predecessor_share)+', same successor '+pct(c.same_successor_share)+'), '+
                (c.median_wait_before_seconds===null?'':'median wait before '+duration(c.median_wait_before_seconds)+', ')+c.resources+' resource(s), '+c.repeats_per_case+' per case. Score '+c.score+' of 100. Evidence only; assess before approving.').slice(0,2000);
              await api('/v1/automation-pipeline',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({title:('Automate '+c.activity).slice(0,200),description,source:{kind:'event_log',reference:(state.logId+':'+c.activity).slice(0,200)}})});
              add.replaceWith(el('span','Added to the pipeline as an idea.'));
            }catch(error){add.disabled=false;add.textContent=error.message;}};
          return [c.activity,String(c.score),pct(c.components.volume)+' / '+pct(c.components.routine)+' / '+pct(c.components.time),
            String(c.per_week),PATTERNS[c.routing_pattern],c.median_wait_before_seconds===null?'\u2014':duration(c.median_wait_before_seconds),
            String(c.resources),String(c.repeats_per_case),add];});
        out.replaceChildren(
          el('div','Score = 100 \u00d7 (0.4 volume + 0.4 routine + 0.2 waiting share), over '+Number(r.cases).toLocaleString()+' cases and '+r.weeks_observed+' weeks'+(r.cases_before_filter?' (filtered from '+Number(r.cases_before_filter).toLocaleString()+')':'')+'.'+(r.activities_below_min_cases?' '+r.activities_below_min_cases+' rarer activit'+(r.activities_below_min_cases===1?'y is':'ies are')+' left out.':''),'muted'),
          table(['Activity','Score','Volume / routine / waiting','Per week','Routing','Median wait before','Resources','Per case','Action'],rows),
          el('div','Fixed routing suggests rules may cover the step; variable routing suggests judgement, where an AI agent with human review may fit. '+r.caveat,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}};
    return box;
  }

  // Decision mining: which recorded data goes with which branch after an activity.
  function decisionSection(inspection){
    const box=section('Decision rules');box.id='owgDecisions';
    const branch=row=>row.next+(row.next_is_terminal?' · case completion':'');
    box.appendChild(el('div','Find the activities after which cases go different ways, then learn which recorded data goes with each branch. Only data recorded at or before the decision is used. Rules are associations in this log, checked on held-out cases, not the business rule.','muted'));
    const find=button('Find decision points','secondary');find.id='owgDecisionFind';
    const points=el('div');points.id='owgDecisionPoints';points.setAttribute('role','status');
    const out=el('div');out.id='owgDecisionResults';out.setAttribute('role','status');
    const observed=el('div');observed.id='owgDecisionObservedBranches';
    const review=el('div');review.id='owgDecisionBranchCases';
    box.append(find,points,observed,review,out);
    const generation=state.generation;
    let reviewTurn=0,ruleTurn=0;
    const pct=x=>Math.round(x*100)+'%';
    const number=x=>Number(x).toLocaleString();
    const cond=c=>{
      const value=typeof c.value==='number'?String(c.value):'“'+c.value+'”';
      const comparison=c.attribute+' '+({'==':'is','!=':'is not','>=':'≥','<':'<'}[c.op])+' '+value;
      return c.includes_missing?'('+comparison+' or missing'+(c.includes_non_numeric?' or nonnumeric':'')+')':comparison;
    };
    function showCases(endpoint,activity,item){
      const turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4',activity+' → '+branch(item));title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        const selection=item.next_is_terminal?{activity,ends_here:true}:{activity,next_activity:item.next};
        if(cursors[page]!==null)selection.after=cursors[page];
        try{
          const response=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(selection)});
          const result=await response.json();
          if(!current())return;
          index=page;following=result.next_after;
          status.textContent=number(result.occurrences)+' recorded occurrences in '+number(result.total)+' cases. Showing '+number(result.cases.length)+' on this page.';
          rows.replaceChildren(result.cases.length?table(['Case','Branch occurrences','Evidence'],result.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,number(c.occurrences),open];
          })):el('div','No cases on this page.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    function showBranches(url,point){
      const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
      const title=el('h4','Observed branches after '+point.activity);title.tabIndex=-1;
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous branch page','secondary'),next=button('Next branch page','secondary');
      const status=el('div');status.setAttribute('role','status');
      nav.append(previous,next);observed.replaceChildren(title,status,rows,nav);
      let offset=0;
      function render(){
        ++reviewTurn;review.replaceChildren();
        const page=point.branches.slice(offset,offset+25);
        status.textContent='Showing branches '+number(offset+1)+'–'+number(offset+page.length)+' of '+number(point.branches.length)+'.';
        rows.replaceChildren(table(['Next','Occurrences','Cases','Share','Evidence'],page.map(item=>{
          const inspect=button('Inspect cases','secondary');
          inspect.setAttribute('aria-label',item.next_is_terminal?'Inspect cases ending here':'Inspect cases continuing to '+item.next);
          inspect.onclick=()=>showCases(endpoint.pathname+endpoint.search,point.activity,item);
          return [branch(item),number(item.occurrences),number(item.cases),pct(item.share),inspect];
        })));
        previous.disabled=offset===0;next.disabled=offset+25>=point.branches.length;
      }
      nav.hidden=point.branches.length<=25;
      previous.onclick=()=>{offset-=25;render();};
      next.onclick=()=>{offset+=25;render();};
      render();title.focus();
    }
    const learn=async(activity)=>{
      const turn=++ruleTurn,current=()=>generation===state.generation&&turn===ruleTurn;
      out.replaceChildren(el('div','Learning rules for '+activity+'…','muted'));
      try{
        const r=await getJSON(logPath('decision-rules',{decision_activity:activity}));
        if(!current())return;
        const head=el('div','After '+activity+': '+r.branches.map(b=>branch(b)+' '+pct(b.share)).join(', ')+' ('+Number(r.occurrences).toLocaleString()+' occurrences'+(r.occurrences_capped?', the first '+Number(r.occurrences_used).toLocaleString()+' used':'')+').','muted');
        if(!r.rules.length){out.replaceChildren(head,el('div',r.note,'muted'));return;}
        const e=r.evaluation,parts=[];
        if('holdout_accuracy' in e)parts.push('Held-out accuracy '+pct(e.holdout_accuracy)+' on '+e.holdout_occurrences+' occurrences, against '+pct(e.majority_baseline_accuracy)+' for always predicting “'+e.majority_baseline+'”.'+(e.beats_baseline?'':' The rules do not beat that baseline; treat them as noise.'));
        else parts.push(e.note);
        const rows=r.rules.map(rule=>[rule.conditions.length?rule.conditions.map(cond).join(' and '):'(all occurrences)',branch(rule),pct(rule.confidence),String(rule.training_occurrences)]);
        out.replaceChildren(head,el('div',parts.join(' '),'muted'),
          table(['When','Then usually','Confidence','Training occurrences'],rows),
          el('div','Most informative data: '+(r.feature_importance.length?r.feature_importance.slice(0,5).map(f=>f.attribute+' '+pct(f.share)).join(', '):'none')+'.','muted'),
          el('div',r.caveat,'muted'));
      }catch(error){if(current())out.replaceChildren(el('div',error.message,'muted'));}};
    find.onclick=async()=>{
      ++reviewTurn;++ruleTurn;find.disabled=true;
      points.replaceChildren(el('div','Looking for decision points…','muted'));
      observed.replaceChildren();review.replaceChildren();out.replaceChildren();
      try{
        const url=logPath('decision-points'),r=await getJSON(url);
        if(generation!==state.generation)return;
        if(!r.decision_points.length){points.replaceChildren(el('div','No activity is followed by two or more branches seen at least '+r.min_branch_occurrences+' times each.','muted'));return;}
        points.replaceChildren(table(['Activity','Occurrences','Branches','Actions'],r.decision_points.map(p=>{
          const go=button('Learn rules','secondary');go.setAttribute('aria-label','Learn rules after '+p.activity);go.onclick=()=>learn(p.activity);
          const open=button('Review branches','secondary');open.setAttribute('aria-label','Review branches after '+p.activity);open.onclick=()=>showBranches(url,p);
          const actions=el('div',null,'row');actions.append(go,open);
          return [p.activity,number(p.occurrences),p.branches.slice(0,4).map(b=>branch(b)+' '+pct(b.share)).join(', ')+(p.branches.length>4?', …':''),actions];
        })));
      }catch(error){if(generation===state.generation)points.replaceChildren(el('div',error.message,'muted'));}
      finally{find.disabled=false;}};
    return box;
  }

  // A local folder whose new CSV exports are appended to this log with its recorded mapping.
  function folderFeedSection(){
    const box=section('Folder feed');box.id='owgFolderFeed';
    box.appendChild(el('div','Point this log at a folder where a system drops exports. Each run appends new files that match the pattern, using the column mapping of this log\u2019s latest CSV import. Files are read, never changed or deleted. A file is appended once, by its content.','muted'));
    const list=el('div');list.id='owgFolderFeedList';list.setAttribute('role','status');
    const folder=input('owgFolderFeedFolder','text','for example /Users/me/Exports/orders');folder.size=40;
    const pattern=input('owgFolderFeedPattern','text');pattern.value='*.csv';pattern.maxLength=164;
    const consent=input('owgFolderFeedConsent','checkbox');
    const consentLabel=el('label',' Read files from this folder and store their events with the log on this computer');consentLabel.prepend(consent);
    const create=button('Create feed','secondary');create.id='owgFolderFeedCreate';create.disabled=true;
    const out=el('div');out.id='owgFolderFeedResult';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Folder',folder),field('File pattern',pattern));
    const previewOut=el('div');previewOut.id='owgFolderFeedPreview';previewOut.setAttribute('role','status');
    box.append(list,row,consentLabel,create,out,previewOut);
    const ready=()=>{create.disabled=!(folder.value.trim()&&pattern.value.trim()&&consent.checked);};
    for(const control of [folder,pattern])control.addEventListener('input',ready);consent.addEventListener('change',ready);
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/feeds';
    const generation=state.generation;
    let previewTurn=0;
    const clearPreview=()=>{previewTurn++;previewOut.replaceChildren();};
    const describe=r=>r.files_appended+' file(s) appended with '+Number(r.events_added).toLocaleString()+' new events'+(r.files_failed?', '+r.files_failed+' could not be appended':'')+(r.files_already_appended?', '+r.files_already_appended+' already appended':'')+(r.files_previously_failed?', '+r.files_previously_failed+' previously failed (use Retry failed files)':'')+(r.files_still_being_written?', '+r.files_still_being_written+' still being written (next run)':'')+'.'+((r.files||[]).some(file=>file.warning)?' An append committed, but its quality report could not be recorded.':'');
    const render=r=>{
      clearPreview();
      if(!r.feeds.length){list.replaceChildren(el('div','No folder feed yet.','muted'));return;}
      list.replaceChildren(table(['Folder','Pattern','Files appended','Failed','Latest file',''],r.feeds.map(f=>{
        const wrap=el('div',null,'row');
        const previewButton=button('Preview files','secondary');
        previewButton.setAttribute('aria-label','Preview files for '+f.folder);
        previewButton.onclick=async()=>{
          const ticket=++previewTurn;
          const current=()=>ticket===previewTurn&&generation===state.generation&&wrap.isConnected;
          previewButton.disabled=true;previewOut.replaceChildren(el('div','Checking folder files…','muted'));
          try{
            const r=await getJSON('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/preview');
            if(!current())return;
            const labels={new_content:'New content',already_appended:'Already appended',previously_failed:'Previously failed',
              repeated_content:'Same content as another file in this preview',settling:'Still settling',
              too_large:'Above the import size limit',preview_limit:'Content unchecked at the preview limit',unavailable:'File unavailable'};
            const summary=r.files.length
              ?'Checked '+r.files.length+' matching files for '+f.pattern+'.'+(r.more_files?' Showing the oldest '+r.max_files+'.':'')
              :'No matching files in this folder.';
            previewOut.replaceChildren(el('div',summary,'muted'),
              ...(r.files.length?[table(['File','Size','Status'],r.files.map(file=>[
                file.name,Number(file.bytes).toLocaleString()+' bytes',labels[file.state]
              ])),el('div','Run now checks the latest files and validates their event rows.','muted')]:[]));
          }catch(error){if(current())previewOut.replaceChildren(el('div',error.message,'muted'));}
          finally{previewButton.disabled=false;}
        };
        const run=button('Run now','secondary');run.setAttribute('aria-label','Run feed for '+f.folder);
        const retry=button('Retry failed files','secondary');retry.setAttribute('aria-label','Retry failed files for '+f.folder);retry.disabled=!f.files_failed;
        const review=button('Review failures','secondary');review.setAttribute('aria-label','Review failures for '+f.folder);review.disabled=!f.files_failed;
        const schedule=button('Run every day','secondary');schedule.setAttribute('aria-label','Run feed for '+f.folder+' every day');
        const remove=button('Delete feed','secondary');remove.setAttribute('aria-label','Delete feed for '+f.folder);
        run.onclick=async()=>{clearPreview();run.disabled=true;out.replaceChildren(el('div','Reading the folder\u2026','muted'));
          try{const r2=await (await api('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/run',{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}})).json();
            out.replaceChildren(el('div',describe(r2)+(r2.events_added?' Analyse again to include them.':'')),...(r2.files||[]).filter(file=>file.outcome==='failed').map(file=>el('div',file.name+': '+file.error,'muted')));render(await getJSON(base));}
          catch(error){out.replaceChildren(el('div',error.message,'muted'));run.disabled=false;}};
        retry.onclick=async()=>{clearPreview();retry.disabled=true;out.replaceChildren(el('div','Retrying unchanged failed files…','muted'));
          try{const r2=await (await api('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/retry-failed',{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}})).json();
            out.replaceChildren(el('div',describe(r2)+' Only unchanged failed files were retried; new files are left for Run now.'+(!r2.files_appended&&!r2.files_failed?' No matching failed files were ready; they may have changed, been removed or still be settling.':'')+(r2.events_added?' Analyse again to include them.':'')),...(r2.files||[]).filter(file=>file.outcome==='failed').map(file=>el('div',file.name+': '+file.error,'muted')));render(await getJSON(base));}
          catch(error){out.replaceChildren(el('div',error.message,'muted'));retry.disabled=false;}};
        const failures=el('div');failures.setAttribute('role','status');let failureTurn=0;
        async function failurePage(after){
          const turn=++failureTurn;failures.replaceChildren(el('div','Loading failures…','muted'));
          try{
            const q=new URLSearchParams();if(after)q.set('after',after);
            const page=await getJSON('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/failures'+(q.toString()?'?'+q:''));
            if(generation!==state.generation||turn!==failureTurn||!wrap.isConnected)return;
            const rows=page.failures.map(item=>{
              const one=button('Retry this file','secondary');one.setAttribute('aria-label','Retry failed file '+item.name+' ('+item.sha256.slice(0,12)+')');
              one.onclick=async()=>{clearPreview();one.disabled=true;out.replaceChildren(el('div','Retrying selected failed content…','muted'));
                try{const result=await (await api('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/retry-failed/'+encodeURIComponent(item.sha256),{method:'POST',headers:{'X-OWG-Local-Storage-Confirmed':'true'}})).json();
                  if(generation!==state.generation)return;
                  out.replaceChildren(el('div',describe(result)+' Only the selected content was considered.'+(result.files.length||result.files_already_appended?'':' The selected content is not currently ready or available.')+(result.events_added?' Analyse again to include them.':'')),...(result.files||[]).filter(file=>file.outcome==='failed').map(file=>el('div',file.name+': '+file.error,'muted')));const updated=await getJSON(base);if(generation===state.generation)render(updated);}
                catch(error){if(generation===state.generation){out.replaceChildren(el('div',error.message,'muted'));one.disabled=false;}}};
              return [item.name,item.processed_at,item.error||'Unknown error',one];
            });
            const first=button('First failures','secondary');first.disabled=!after;first.onclick=()=>failurePage('');
            const next=button('Next failures','secondary');next.disabled=!page.next_after;next.onclick=()=>failurePage(page.next_after);
            failures.replaceChildren(rows.length?table(['File','Last failure (UTC)','Error',''],rows):el('div','No outstanding failures on this page.','muted'),first,next,el('div','Pages can change as files are retried. Retry matches unchanged content, including renamed files.','muted'));
          }catch(error){if(generation===state.generation&&turn===failureTurn)failures.replaceChildren(el('div',error.message,'muted'));}
        }
        review.onclick=()=>failurePage('');
        schedule.onclick=async()=>{if(!window.confirm('Read '+f.folder+' and append new files every day while OpenWorkGraph is running?'))return;
          try{await api('/v1/schedules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'event_log_folder_feed',target_id:f.feed_id,interval_minutes:1440,confirm_scheduled_runs:true})});
            out.replaceChildren(el('div','Scheduled daily. Manage it under scheduled jobs.'));}
          catch(error){out.replaceChildren(el('div',error.message,'muted'));}};
        remove.onclick=async()=>{if(!window.confirm('Delete this feed? Events already appended stay in the log.'))return;
          try{await api('/v1/event-logs/feeds/'+encodeURIComponent(f.feed_id)+'/delete',{method:'POST'});out.replaceChildren(el('div','Feed deleted.'));render(await getJSON(base));}
          catch(error){out.replaceChildren(el('div',error.message,'muted'));}};
        wrap.append(previewButton,run,retry,review,schedule,remove,failures);
        const latest=f.recent_files[0];
        return [f.folder,f.pattern,Number(f.files_appended).toLocaleString(),Number(f.files_failed).toLocaleString(),
          latest?latest.name+' \u2014 '+(latest.outcome==='appended'?Number(latest.added_events).toLocaleString()+' events added'+(latest.warning?' · '+latest.warning:''):'not appended: '+latest.error):'\u2014',wrap];})));
    };
    create.onclick=async()=>{create.disabled=true;
      try{await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:folder.value,pattern:pattern.value,confirm_folder_reads:true})});
        consent.checked=false;out.replaceChildren(el('div','Feed created. Run it now or every day.'));render(await getJSON(base));}
      catch(error){out.replaceChildren(el('div',error.message,'muted'));ready();}};
    getJSON(base).then(r=>{if(generation===state.generation)render(r);}).catch(error=>list.replaceChildren(el('div',error.message,'muted')));
    return box;
  }

  // The owner's notes on single cases: findings, phone calls, reasons for a delay.
  function caseNotesSection(inspection){
    const box=section('Case notes');box.id='owgCaseNotes';
    box.append(el('div','Write down what you found about a case. Notes stay on this computer, are never shared with AI agents and are deleted with the log.','muted'),
      caseNotesPanel('owgCaseNote',null,inspection));
    return box;
  }

  function caseNotesPanel(prefix,fixedCase,inspection){
    const panel=el('div');
    const generation=state.generation,logId=state.logId;
    const endpoint='/v1/event-logs/'+encodeURIComponent(logId)+'/case-notes';
    const current=()=>panel.isConnected&&generation===state.generation;
    panel.dataset.owgCaseNotesView=logId;
    const caseId=input(prefix+'Case','text','case ID');caseId.maxLength=500;caseId.autocomplete='off';
    const text=document.createElement('textarea');text.id=prefix+'Text';text.maxLength=2000;text.rows=2;
    const add=button('Add note','secondary');add.id=prefix+'Add';
    const form=el('div',null,'row');form.style.alignItems='end';
    form.append(...(fixedCase===null?[field('Case',caseId)]:[]),field('Note',text),add);
    const msg=el('div',null,'muted');msg.id=prefix+'Status';msg.setAttribute('aria-live','polite');
    if(fixedCase===null)msg.setAttribute('role','status');
    const search=input(prefix+'Search','search');search.maxLength=200;
    const find=button('Search notes','secondary'),clear=button('Clear search','secondary');
    const searchRow=el('div',null,'row');searchRow.style.alignItems='end';searchRow.append(field('Search by note text or case key',search),find,clear);
    const list=el('div');list.id=prefix+'List';
    const nav=el('div',null,'row'),previous=button('Previous note page','secondary'),next=button('Next note page','secondary');
    nav.append(previous,next);show(nav,false);
    panel.append(form,msg,...(fixedCase===null?[searchRow]:[]),list,nav);
    const plural=(n,w)=>Number(n).toLocaleString()+' '+w+(n===1?'':'s');
    let searchText='',loadTurn=0,pageIndex=0,following=null,cursors=[null],writing=false;
    const post=async(url,body)=>(await api(url,{method:'POST',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})})).json();
    function refreshViews(changedCase){
      document.querySelectorAll('[data-owg-case-notes-view]').forEach(view=>{
        if(view.dataset.owgCaseNotesView===logId)view.dispatchEvent(new CustomEvent('owg-case-note-change',{detail:changedCase}));
      });
    }
    async function load(page){
      if(!current())return;
      const ticket=++loadTurn,valid=()=>current()&&ticket===loadTurn;
      previous.disabled=next.disabled=true;list.inert=true;
      try{
        const r=await post(endpoint+'/query',{...(fixedCase!==null?{case_id:fixedCase}:{}),
          ...(searchText?{search:searchText}:{}),...(cursors[page]!==null?{after:cursors[page]}:{})});
        if(!valid())return;
        pageIndex=page;following=r.next_after;show(nav,pageIndex>0||following!==null);
        if(!r.notes.length){
          list.replaceChildren(el('div',searchText?'No notes match this search.':'No case notes yet.','muted'));
          return;
        }
        const headers=[...(fixedCase===null?['Case','Object type']:[]),'Note','Written','Actions'];
        const t=table(headers,r.notes.map(n=>{
          const actions=el('div',null,'row');
          if(inspection&&n.has_events&&n.object_type){
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+n.case_id);
            open.onclick=()=>inspection.open(n.case_id,n.object_type);actions.appendChild(open);
          }
          const del=button('Delete','secondary');del.setAttribute('aria-label','Delete note on case '+n.case_id);
          del.onclick=async()=>{
            if(writing||!current())return;
            writing=true;del.disabled=add.disabled=true;
            try{
              await post(endpoint+'/'+encodeURIComponent(n.note_id)+'/delete?compact=true');
              if(current())msg.textContent='Note deleted.';
              refreshViews(n.case_id);
            }catch(error){if(current())msg.textContent=error.message;}
            finally{writing=false;if(current())del.disabled=add.disabled=false;}
          };
          actions.appendChild(del);
          const body=el('div',n.text);body.style.whiteSpace='pre-wrap';
          return [...(fixedCase===null?[n.case_id,n.object_type||'—']:[]),body,n.created_at.slice(0,16).replace('T',' ')+' UTC',actions];
        }));
        const count=plural(r.notes_total,'note')+' on '+plural(r.matching_cases,'case')+'.';
        list.replaceChildren(el('div',count+(r.notes_total>r.notes.length?' Showing '+r.notes.length+' on this page.':''),'muted'),t);
      }catch(error){if(valid())msg.textContent=error.message;}
      finally{if(valid()){list.inert=false;previous.disabled=pageIndex===0;next.disabled=following===null;}}
    }
    const restart=()=>{cursors=[null];pageIndex=0;following=null;load(0);};
    previous.onclick=()=>load(pageIndex-1);
    next.onclick=()=>{cursors[pageIndex+1]=following;load(pageIndex+1);};
    find.onclick=()=>{searchText=search.value.trim();msg.textContent='';restart();};
    search.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();find.click();}};
    clear.onclick=()=>{search.value='';find.click();};
    panel.addEventListener('owg-case-note-change',event=>{
      if(fixedCase===null||event.detail===fixedCase)restart();
    });
    add.onclick=async()=>{
      if(writing||!current())return;
      const key=fixedCase??caseId.value,draft=text.value;
      if(!key||!draft.trim()){msg.textContent=fixedCase===null?'Enter a case ID and a note.':'Enter a note.';return;}
      writing=true;add.disabled=true;
      try{
        await post(endpoint+'?compact=true',{case_id:key,text:draft});
        if(current()){
          if(text.value===draft)text.value='';
          msg.textContent='Note added to case '+key+'.';
        }
        refreshViews(key);
      }catch(error){if(current())msg.textContent=error.message;}
      finally{writing=false;if(current())add.disabled=false;}
    };
    queueMicrotask(restart);
    return panel;
  }

  function caseExplainSection(){
    const box=section('Explain a case');box.id='owgCaseExplain';
    box.appendChild(el('div','Pick one of the slowest cases or enter a case ID to see which steps took longer than usual in this log.','muted'));
    const caseId=input('owgCaseExplainId','text','case ID');caseId.maxLength=500;caseId.autocomplete='off';
    const run=button('Explain case','secondary');run.id='owgCaseExplainRun';
    const slowList=el('div');slowList.id='owgCaseExplainSlowest';
    const out=el('div');out.id='owgCaseExplainResult';out.setAttribute('role','status');out.setAttribute('aria-live','polite');
    const other=input('owgCaseCompareId','text','another case ID');other.maxLength=500;other.autocomplete='off';
    const compare=button('Compare with case','secondary');compare.id='owgCaseCompareRun';
    const compared=el('div');compared.id='owgCaseCompareResult';compared.setAttribute('role','status');compared.setAttribute('aria-live','polite');
    const similarRun=button('Find similar cases','secondary');similarRun.id='owgSimilarRun';
    const similarOut=el('div');similarOut.id='owgSimilarResult';similarOut.setAttribute('role','status');similarOut.setAttribute('aria-live','polite');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Case',caseId),run,similarRun,field('Compare with',other),compare);
    box.append(slowList,row,out,similarOut,compared);
    similarRun.onclick=async()=>{
      const id=caseId.value.trim();
      if(!id){similarOut.replaceChildren(el('div','Enter a case ID.','muted'));return;}
      const generation=state.generation;similarRun.disabled=true;similarOut.replaceChildren(el('div','Looking for similar cases…','muted'));
      try{
        const r=await getJSON(logPath('similar-cases',{case_id:id,limit:'10'}));
        if(generation!==state.generation)return;
        const h=s=>s===null||s===undefined?'—':(s<172800?(Math.round(s/360)/10)+' h':(Math.round(s/8640)/10)+' d');
        const diff=c=>[...c.more_of.map(x=>'+'+x.activity+(x.times>1?' ×'+x.times:'')),...c.fewer_of.map(x=>'−'+x.activity+(x.times>1?' ×'+x.times:''))].join(', ')||(c.same_path?'same path':'same steps, other order');
        const t=table(['Case','Similarity','Differences from '+r.case.case_id,'Duration','Started','Compare'],r.similar.map(c=>[c.case_id,Math.round(c.similarity*100)+'%',diff(c),h(c.duration_seconds),(c.started_at||'').slice(0,10),'']));
        t.querySelectorAll('tbody tr').forEach((tr,i)=>{const go=button('Compare','secondary');go.setAttribute('aria-label','Compare with case '+r.similar[i].case_id);
          go.onclick=()=>{other.value=r.similar[i].case_id;compare.click();};tr.cells[5].appendChild(go);});
        similarOut.replaceChildren(el('div',Number(r.same_path_cases).toLocaleString()+' other cases follow exactly the same path. The '+r.similar.length+' most similar cases took a median of '+h(r.similar_median_duration_seconds)+'; this case took '+h(r.case.duration_seconds)+'.'+(r.search_complete?'':' The search stopped early; closer cases may exist.')),t,el('div',r.caveat,'muted'));
      }catch(error){similarOut.replaceChildren(el('div',error.message,'muted'));}
      finally{similarRun.disabled=false;}};
    compare.onclick=async()=>{
      const a=caseId.value.trim(),b=other.value.trim();
      if(!a||!b){compared.replaceChildren(el('div','Enter both case IDs.','muted'));return;}
      compare.disabled=true;
      try{
        const q=new URLSearchParams({case_a:a,case_b:b});if(state.caseType)q.set('case_type',state.caseType);
        const r=await getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/case-compare?'+q);
        if(generation!==state.generation)return;
        const h=x=>x===null?'—':(x<48?(Math.round(x*10)/10)+' h':(Math.round(x/24*10)/10)+' d');
        const kind={both:'both',only_a:'only '+r.case_a.case_id,only_b:'only '+r.case_b.case_id};
        compared.replaceChildren(
          el('div','Case '+r.case_a.case_id+' took '+h(r.case_a.hours)+' over '+r.case_a.events+' events; case '+r.case_b.case_id+' took '+h(r.case_b.hours)+' over '+r.case_b.events+'. '+r.shared_steps+' steps are shared.'+(r.largest_gap_growth?' Case '+r.case_b.case_id+' fell furthest behind between '+r.largest_gap_growth.from+' and '+r.largest_gap_growth.to+' (+'+h(r.largest_gap_growth.extra_hours)+').':'')),
          table(['Step','In',r.case_a.case_id+' (from start)',r.case_b.case_id+' (from start)'],r.steps.map(x=>[x.activity,kind[x.kind],h(x.a_hours_from_start),h(x.b_hours_from_start)])),
          el('div',r.caveat,'muted'));
      }catch(error){compared.replaceChildren(el('div',error.message,'muted'));}
      finally{compare.disabled=false;}};
    const generation=state.generation;
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/cases/';
    const post=(path,body)=>api(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    const explainCase=async id=>{
      run.disabled=true;
      try{
        const spec={case_id:id};if(state.caseType)spec.case_type=state.caseType;
        const r=await post('explain',spec);if(generation!==state.generation)return;
        const lines=[el('div','Case '+r.case_id+' took '+duration(r.duration_seconds)+' (log median '+duration(r.log_median_seconds)+', 90th percentile '+duration(r.log_p90_seconds)+'); it is slower than '+pct(r.slower_than_share_of_cases)+' of cases.')];
        if(r.biggest_excess.length)lines.push(el('div','Most of the extra time: '+r.biggest_excess.slice(0,3).map(g=>g.from+' → '+g.to+' ('+duration(g.excess_seconds)+(g.typical_gap_seconds===null?', a path few cases take':' over the usual '+duration(g.typical_gap_seconds))+')').join('; ')+'.'));
        else lines.push(el('div','No step took longer than usual for its transition.','muted'));
        if(r.repeated_activities.length)lines.push(el('div','Repeated: '+r.repeated_activities.map(a=>a.activity+' ×'+a.times).join(', ')+'.'));
        if(r.uncommon_activities.length)lines.push(el('div','Uncommon steps (in under 10% of cases): '+r.uncommon_activities.join(', ')+'.'));
        const rows=r.gaps.map(g=>[g.from+' → '+g.to,duration(g.gap_seconds),g.typical_gap_seconds===null?'too few cases':duration(g.typical_gap_seconds),g.excess_seconds>0?'+'+duration(g.excess_seconds):'—']);
        const model=el('div',null,'muted');model.id='owgCaseExplainModel';
        out.replaceChildren(...lines,model,table(['Step','This case','Typical (median)','Above typical'],rows),el('div',r.interpretation,'muted'));
        const q=new URLSearchParams({case_id:id,noise:'0.2'});if(state.caseType)q.set('case_type',state.caseType);
        getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/case-alignment?'+q).then(a=>{
          if(generation!==state.generation||!a.aligned)return;
          const parts=[];if(a.skipped_steps.length)parts.push('skipped '+a.skipped_steps.join(', '));if(a.extra_steps.length)parts.push('did '+a.extra_steps.join(', ')+' where the model does not allow it');
          model.textContent='Against the discovered model: '+(a.fits?'this case follows it exactly.':parts.join('; ')+' (alignment fitness '+pct(a.fitness)+').');
        }).catch(()=>{});
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    run.onclick=()=>{const id=caseId.value.trim();if(!id){out.replaceChildren(el('div','Enter a case ID.','muted'));return;}explainCase(id);};
    const spec={limit:5};if(state.caseType)spec.case_type=state.caseType;if(filterOn())spec.cohort=globalCohort();
    post('slowest',spec).then(r=>{
      if(generation!==state.generation||!r.cases.length)return;
      const wrap=el('div',null,'row');wrap.appendChild(el('span','Slowest cases:','muted'));
      r.cases.forEach(c=>{const b=button(c.case_id+' ('+duration(c.duration_seconds)+')','secondary');b.setAttribute('aria-label','Explain case '+c.case_id);
        b.onclick=()=>{caseId.value=c.case_id;explainCase(c.case_id);};wrap.appendChild(b);});
      slowList.replaceChildren(wrap);
    }).catch(()=>{});
    return box;
  }

  // Columns from a separate case table (customer, region, amount...) joined onto the cases by id.
  function caseAttributeSection(){
    const box=section('Case attributes from a table');box.id='owgCaseAttributes';
    box.appendChild(el('div','Add columns such as region, segment or contract value from a CSV or Excel worksheet keyed by case ID. They become case attributes for filters, root cause and attribute profiles. You can undo new uploads to restore the previous values of the cases they changed.','muted'));
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/case-attributes';
    const list=el('div');list.id='owgCaseAttributeUploads';list.setAttribute('role','status');
    const file=input('owgCaseAttributeFile','file');file.accept='.csv,.tsv,.txt,.xlsx';
    const delimiter=select('owgCaseAttributeDelimiter',[[',','Comma'],[';','Semicolon'],['t','Tab'],['|','Pipe']]);
    const worksheet=select('owgCaseAttributeWorksheet',[]);
    const sheetField=field('Case table worksheet',worksheet);sheetField.hidden=true;
    const workbookStatus=el('p');workbookStatus.id='owgCaseAttributeWorkbookStatus';workbookStatus.setAttribute('role','status');
    const idColumn=select('owgCaseAttributeId',[]);
    const columns=select('owgCaseAttributeColumns',[]);columns.multiple=true;columns.size=4;
    const replace=input('owgCaseAttributeReplace','checkbox');
    const consent=input('owgCaseAttributeConsent','checkbox');
    const add=button('Add to cases','secondary');add.id='owgCaseAttributeAdd';add.disabled=true;
    const out=el('div');out.id='owgCaseAttributeResult';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Case table (CSV or Excel)',file),sheetField,field('Delimiter',delimiter),field('Case ID column',idColumn),field('Columns to add',columns));
    const checks=el('div',null,'row');
    const replaceLabel=el('label',' Overwrite values the cases already have');replaceLabel.prepend(replace);
    const consentLabel=el('label',' Store these values with the log on this computer');consentLabel.prepend(consent);
    checks.append(replaceLabel,consentLabel);
    box.append(list,row,workbookStatus,checks,add,out);
    const generation=state.generation,notion=state.caseType;
    let revision=0,busy=false,workbook=null,converted=null,reading=false,workbookRequest=0;
    const effectiveFile=()=>workbook?converted:file.files?.[0];
    const ready=()=>{
      const complete=!!(!reading&&effectiveFile()&&idColumn.value&&columns.selectedOptions.length);
      add.disabled=busy||!complete||!consent.checked;
    };
    const readHeader=async()=>{
      const turn=++revision;
      const chosen=effectiveFile();setOptions(idColumn,[]);setOptions(columns,[]);ready();if(!chosen)return;
      const header=csvHeader(await chosen.slice(0,65536).text(),delimiter.value==='t'?'\t':delimiter.value);
      if(turn!==revision)return;
      setOptions(idColumn,header.map(c=>[c,c]));
      idColumn.value=header.find(c=>/case|order|ticket|^id$/i.test(c))||header[0]||'';
      const others=()=>setOptions(columns,header.filter(c=>c!==idColumn.value).map(c=>[c,c]));
      others();idColumn.onchange=()=>{others();ready();};ready();
    };
    async function readWorksheet(chosen,sheet){
      const ticket=++workbookRequest;
      workbook=chosen;converted=null;reading=true;consent.checked=false;delimiter.disabled=true;
      worksheet.disabled=true;readHeader();
      workbookStatus.textContent='Reading the Excel worksheet…';
      try{
        const query=sheet?'?'+new URLSearchParams({sheet}):'';
        const response=await api('/v1/event-logs/xlsx-to-csv'+query,{method:'POST',body:chosen,
          headers:{'Content-Type':'application/octet-stream'}});
        const result=await response.json();
        if(generation!==state.generation||ticket!==workbookRequest)return;
        setOptions(worksheet,result.sheets.map(name=>[name,name]));worksheet.value=result.sheet;
        sheetField.hidden=result.sheets.length<2;
        converted=new File([result.csv],'case-table.csv',{type:'text/csv'});
        delimiter.value=',';
        workbookStatus.textContent='Using worksheet '+JSON.stringify(result.sheet)+' ('+
          Number(result.rows).toLocaleString()+' rows). Match IDs exactly as stored in the log.';
        await readHeader();
      }catch(error){if(generation===state.generation&&ticket===workbookRequest)workbookStatus.textContent=error.message;}
      finally{if(ticket===workbookRequest){reading=false;worksheet.disabled=false;ready();}}
    }
    file.onchange=()=>{
      workbookRequest++;workbook=null;converted=null;reading=false;consent.checked=false;
      sheetField.hidden=true;setOptions(worksheet,[]);workbookStatus.textContent='';
      const chosen=file.files?.[0];
      if(chosen&&/\.xlsx$/i.test(chosen.name)){readWorksheet(chosen);return;}
      delimiter.disabled=false;
      if(chosen&&/\.tsv$/i.test(chosen.name))delimiter.value='t';
      readHeader();
    };
    worksheet.onchange=()=>{if(workbook)readWorksheet(workbook,worksheet.value);};
    delimiter.onchange=readHeader;
    columns.addEventListener('change',ready);
    consent.addEventListener('change',ready);
    const render=r=>{
      if(!r.uploads.length){list.replaceChildren(el('div','No case attributes added yet.','muted'));return;}
      list.replaceChildren(table(['Added','Columns','Cases updated','Rows not in this log','Cases without a row',''],r.uploads.map(u=>{
        const remove=button('Undo upload','secondary');remove.setAttribute('aria-label','Undo upload for '+u.columns.join(', '));
        remove.onclick=async()=>{
          if(!window.confirm('Undo this upload for '+u.columns.join(', ')+' and restore the previous values?'))return;
          remove.disabled=true;
          try{
            const r2=await (await api(base+'/remove',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({upload_id:u.upload_id})})).json();
            out.textContent='Undid '+r2.removed_columns.join(', ')+' in '+Number(r2.cases_changed).toLocaleString()+' cases.';
            render(await getJSON(base));
          }catch(error){out.textContent=error.message;remove.disabled=false;}
        };
        const action=u.undo_supported?remove:el('span','Undo unavailable for this older upload','muted');
        return [String(u.added_at).slice(0,16).replace('T',' '),u.columns.join(', '),Number(u.cases_updated).toLocaleString(),Number(u.rows_not_in_log).toLocaleString(),Number(u.cases_without_row).toLocaleString(),action];})));
    };
    function uploadQuery(){
      const q=new URLSearchParams({id_column:idColumn.value,delimiter:delimiter.value});
      [...columns.selectedOptions].forEach(o=>q.append('column',o.value));
      if(notion)q.set('object_type',notion);
      if(replace.checked)q.set('replace','true');
      return q;
    }
    add.onclick=async()=>{
      const q=uploadQuery();
      busy=true;ready();out.textContent='Adding…';
      try{
        const r=await (await api(base+'?'+q,{method:'POST',body:effectiveFile(),headers:{'Content-Type':'text/csv','X-OWG-Local-Storage-Confirmed':'true'}})).json();
        if(generation!==state.generation)return;
        out.textContent='Added '+r.columns.join(', ')+' to '+Number(r.cases_updated).toLocaleString()+' cases. '+Number(r.rows_not_in_log).toLocaleString()+' rows matched no case; '+Number(r.cases_without_row).toLocaleString()+' cases had no row.';
        consent.checked=false;render(await getJSON(base));
      }catch(error){if(generation===state.generation)out.textContent=error.message;}
      finally{busy=false;ready();}
    };
    getJSON(base).then(r=>{if(generation===state.generation)render(r);}).catch(error=>list.replaceChildren(el('div',error.message,'muted')));
    return box;
  }

  function caseTableExportSection(){
    const box=section('Download a case table');box.id='owgCaseTableExport';
    const columns=el('textarea');columns.id='owgCaseTableColumns';columns.rows=3;columns.maxLength=2700;
    columns.placeholder='One attribute name per line';
    const include=input('owgCaseTableIncludeValues','checkbox');
    const format=select('owgCaseTableFormat',[['exact','CSV for re-import'],['review','CSV for spreadsheet review']]);
    const download=button('Download case table','secondary');download.id='owgCaseTableDownload';
    const message=el('p');message.id='owgCaseTableStatus';message.setAttribute('role','status');
    const logId=state.logId,notion=state.caseType,generation=state.generation;
    box.append(el('p','One row per '+(notion||'case')+'. '+(filterOn()?'Uses the active case filter.':'Includes all cases in this case notion.'),'muted'),
      field('Attribute columns (optional)',columns),field('Include stored values for these columns',include),
      field('Case table format',format),download,message);
    const describe=()=>{
      message.textContent=format.value==='review'
        ? 'Review CSV changes formula-like cells and headers. Choose CSV for re-import to keep exact case IDs.'
        : 'Leave values blank for a template. To join an edited table, use case_id as the case ID column.';
    };
    format.onchange=describe;describe();
    download.onclick=async()=>{
      download.disabled=true;
      try{
        const selected=columns.value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
        const review=format.value==='review';
        const spec={columns:selected,include_values:include.checked,spreadsheet_review:review,
          ...(notion?{case_type:notion}:{}),...(filterOn()?{cohort:globalCohort()}:{})};
        message.textContent='Preparing the case table…';
        const response=await api('/v1/event-logs/'+encodeURIComponent(logId)+'/case-table/export',{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});
        const blob=await response.blob();
        if(generation!==state.generation||!box.isConnected)return;
        const url=URL.createObjectURL(blob);
        const link=el('a');link.href=url;link.download=logId+'-case-table.'+(review?'review.csv':'csv');
        document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
        message.textContent='Case table downloaded.';
      }catch(error){if(generation===state.generation)message.textContent=error.message;}
      finally{download.disabled=false;}
    };
    return box;
  }

  function caseTagSection(activityNames){
    const box=section('Case tags');box.id='owgCaseTags';
    box.appendChild(el('div','Tag cases you have looked at, such as “checked” or “root cause: missing PO”, then choose the tag under “Tagged” in any filter. Tags are your judgement, stored on this computer; the log itself is not changed.','muted'));
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/case-tags';
    const list=el('div');list.id='owgCaseTagList';list.setAttribute('role','status');
    const tag=input('owgCaseTagName','text','for example checked');tag.maxLength=60;tag.autocomplete='off';
    const note=input('owgCaseTagNote','text','optional');note.maxLength=500;
    const ids=document.createElement('textarea');ids.id='owgCaseTagIds';ids.rows=2;ids.placeholder='case IDs, one per line or comma-separated (leave empty to tag the cases matching the filter below)';
    const filter=cohortFields('owgCaseTagCohort','Or tag every case that matches',activityNames);
    const add=button('Tag cases','secondary');add.id='owgCaseTagAdd';
    const out=el('div');out.id='owgCaseTagResult';out.setAttribute('role','status');
    const review=el('div');review.id='owgCaseTagReview';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Tag',tag),field('Note',note));
    box.append(list,row,field('Cases',ids),filter.node,add,out,review);
    const generation=state.generation;
    const pageSize=100;
    let openTag=null,openOffset=0,reviewRequest=0;
    const removeTag=spec=>api(base+'/remove',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)
    }).then(response=>response.json());
    const loadCases=async(tagName,offset,focus=false)=>{
      openTag=tagName;
      openOffset=offset;
      const request=++reviewRequest;
      const current=()=>generation===state.generation&&request===reviewRequest;
      const heading=el('h4','Cases tagged '+tagName);heading.tabIndex=-1;
      const status=el('div','Loading tagged cases…','muted');status.setAttribute('role','status');
      review.replaceChildren(heading,status);
      if(focus)heading.focus();
      try{
        const query=new URLSearchParams({tag:tagName,limit:pageSize,offset});
        const result=await getJSON(base+'?'+query);
        if(!current())return;
        const {total,next_offset:nextOffset}=result.page;
        if(offset>0&&offset>=total){
          return loadCases(tagName,Math.max(0,Math.floor((total-1)/pageSize)*pageSize),focus);
        }
        if(!total){status.textContent='No cases have this tag.';return;}
        status.textContent='Showing '+(offset+1).toLocaleString()+'–'+(offset+result.cases.length).toLocaleString()+' of '+total.toLocaleString()+' cases.';
        const selected=new Set();
        const remove=button('Remove tag from selected cases','secondary');remove.disabled=true;
        const cases=table(['Select','Case','Note','Tagged'],result.cases.map(item=>{
          const checkbox=document.createElement('input');checkbox.type='checkbox';
          checkbox.setAttribute('aria-label','Select case '+item.case_id);
          checkbox.onchange=()=>{
            if(checkbox.checked)selected.add(item.case_id);
            else selected.delete(item.case_id);
            remove.disabled=!selected.size;
          };
          return [checkbox,item.case_id,item.note||'—',String(item.tagged_at).slice(0,16).replace('T',' ')];
        }));
        const previous=button('Previous page','secondary');previous.disabled=offset===0;
        const next=button('Next page','secondary');next.disabled=nextOffset===null;
        previous.onclick=()=>loadCases(tagName,offset-pageSize,true);
        next.onclick=()=>loadCases(tagName,nextOffset,true);
        remove.onclick=async()=>{
          if(!selected.size||!window.confirm('Remove “'+tagName+'” from '+selected.size+' selected '+(selected.size===1?'case':'cases')+'?'))return;
          remove.disabled=true;
          cases.querySelectorAll('input').forEach(checkbox=>{checkbox.disabled=true;});
          try{
            const response=await removeTag({tag:tagName,case_ids:[...selected]});
            if(generation!==state.generation)return;
            render(response);
            out.textContent='Removed “'+tagName+'” from '+response.removed.toLocaleString()+' '+(response.removed===1?'case':'cases')+'.';
            refreshReview(current());
          }catch(error){
            if(!current())return;
            out.textContent=error.message;
            cases.querySelectorAll('input').forEach(checkbox=>{checkbox.disabled=false;});
            remove.disabled=false;
          }
        };
        const controls=el('div',null,'row');controls.append(previous,next,remove);
        review.append(cases,controls);
      }catch(error){if(current())status.textContent=error.message;}
    };
    const refreshReview=(focus=false)=>{if(openTag!==null)loadCases(openTag,openOffset,focus);};
    const render=r=>{
      if(!r.tags.length){list.replaceChildren(el('div','No tags yet.','muted'));return;}
      list.replaceChildren(table(['Tag','Cases','With a note','Last tagged',''],r.tags.map(t=>{
        const wrap=el('div',null,'row');
        const showCases=button('Show cases','secondary');showCases.setAttribute('aria-label','Show cases tagged '+t.tag);
        const remove=button('Remove tag','secondary');remove.setAttribute('aria-label','Remove tag '+t.tag);
        showCases.onclick=()=>loadCases(t.tag,0,true);
        remove.onclick=async()=>{
          if(!window.confirm('Remove the tag “'+t.tag+'” from all '+t.cases+' cases?'))return;
          remove.disabled=true;
          try{
            const result=await removeTag({tag:t.tag});
            if(generation!==state.generation)return;
            render(result);
            out.textContent='Removed “'+t.tag+'” from '+result.removed.toLocaleString()+' cases.';
            if(openTag===t.tag)refreshReview();
          }catch(error){
            if(generation===state.generation)out.textContent=error.message;
            remove.disabled=false;
          }
        };
        wrap.append(showCases,remove);
        return [t.tag,Number(t.cases).toLocaleString(),Number(t.cases_with_note).toLocaleString(),String(t.last_tagged_at).slice(0,16).replace('T',' '),wrap];})));
    };
    getJSON(base).then(r=>{if(generation===state.generation)render(r);}).catch(error=>list.replaceChildren(el('div',error.message,'muted')));
    // Cases tagged from other sections (for example unusual cases) appear here without a reload.
    box.addEventListener('owg-tags-changed',()=>{getJSON(base).then(r=>{if(generation===state.generation){render(r);refreshReview();}}).catch(()=>{});});
    add.onclick=async()=>{
      add.disabled=true;
      try{
        if(!tag.value.trim())throw new Error('Enter a tag.');
        const spec={tag:tag.value.trim(),note:note.value.trim()};if(state.caseType)spec.case_type=state.caseType;
        const list2=ids.value.split(/[\n,]/).map(x=>x.trim()).filter(Boolean);
        if(list2.length)spec.case_ids=list2.slice(0,1000);
        else{const cohort=filter.read();delete cohort.label;if(!Object.keys(cohort).length)throw new Error('Enter case IDs or choose which cases match.');spec.cohort=cohort;}
        const r=await (await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        render(r);
        if(openTag===r.tag)refreshReview();
        out.replaceChildren(el('div','Tagged '+Number(r.tagged).toLocaleString()+' cases “'+r.tag+'”'+(r.already_tagged?' ('+r.already_tagged+' already had it; their note was replaced)':'')+(r.unknown_case_ids?'; '+r.unknown_case_ids+' IDs are not cases of this log':'')+'.'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{add.disabled=false;}
    };
    return box;
  }

  function exportButtons(prefix,getResult,csvRows,verifyBeforeDownload,onError){
    const json=button('Download JSON','secondary');json.id=prefix+'Json';json.disabled=true;
    const csv=button('Download CSV','secondary');csv.id=prefix+'Csv';csv.disabled=true;
    let exporting=false;
    const exportOne=async(format)=>{
      if(exporting)return;
      exporting=true;json.disabled=true;csv.disabled=true;
      try{
        const r=verifyBeforeDownload?await verifyBeforeDownload():getResult();
        if(!r)return;
        if(format==='json')download(prefix+'.json',JSON.stringify(r,null,2),'application/json');
        else download(prefix+'.csv',toCSV(csvRows(r)),'text/csv');
      }catch(error){if(onError)onError(error);else throw error;}
      finally{exporting=false;const available=!!getResult();json.disabled=!available;csv.disabled=!available;}
    };
    json.onclick=()=>exportOne('json');
    csv.onclick=()=>exportOne('csv');
    const row=el('div',null,'row');row.append(json,csv);
    return {node:row,enable:on=>{json.disabled=!on;csv.disabled=!on;}};
  }

  // Plain-language consequences of analysing the log with one object type as the case notion.
  function flatteningSummary(t){
    if(!t)return [];
    const n=x=>Number(x).toLocaleString(),d=t.deficiency,c=t.convergence,lines=[];
    lines.push(d.events_left_out?n(d.events_left_out)+' events ('+(d.share_of_log_events*100).toFixed(1)+'% of the log) are linked to no '+t.object_type+' and are left out'+(d.by_activity.length?': '+d.by_activity.slice(0,3).map(a=>a.activity+' ('+n(a.events)+')').join(', '):'')+'.':'No events are left out.');
    lines.push(c.convergent_events?n(c.convergent_events)+' events are linked to several '+t.object_type+' objects and are counted once per object: '+n(c.extra_event_rows)+' extra event rows, '+Number(c.duplication_factor).toFixed(2)+'× the linked events'+(c.by_activity.length?' (most from '+c.by_activity.slice(0,3).map(a=>a.activity).join(', ')+')':'')+'.':'No event is counted more than once.');
    const explained=t.divergence.by_activity.filter(a=>a.repetitions_differ_by.some(x=>x.groups_with_different_objects>0));
    if(explained.length)lines.push('Repeats that happen once per other object, not rework: '+explained.slice(0,3).map(a=>a.activity+' (per '+a.repetitions_differ_by.filter(x=>x.groups_with_different_objects>0).map(x=>x.object_type).join(', ')+')').join('; ')+'.');
    if(t.qualifiers.objects_with_several_qualifiers)lines.push(n(t.qualifiers.objects_with_several_qualifiers)+' '+t.object_type+' objects are linked under more than one qualifier.');
    return lines;
  }

  // Plain-language summary of object attributes whose recorded value changes over time.
  function attributeChangeSummary(t){
    if(!t)return [];
    const n=x=>Number(x).toLocaleString(),lines=[];
    const changing=t.attributes.filter(a=>a.value_changes>0);
    changing.slice(0,5).forEach(a=>{
      const top=a.most_frequent_changes.slice(0,3).map(c=>c.from+' → '+c.to+' ('+n(c.changes)+')').join(', ');
      let line=a.name+' changes on '+n(a.objects_whose_value_changes)+' of '+n(a.objects_with_values)+' '+t.object_type+' objects ('+n(a.value_changes)+' changes'+(top?'; most often '+top:'')+').';
      const l=a.event_links;
      if(l&&l.where_stored_value_differs_from_value_then)line+=' '+n(l.where_stored_value_differs_from_value_then)+' of '+n(l.examined)+' event links happened while it held another value than the one stored value analyses use.';
      if(l&&l.before_first_value)line+=' '+n(l.before_first_value)+' happened before any value was recorded.';
      if(a.times_with_several_values)line+=' '+n(a.times_with_several_values)+' times have several different values.';
      lines.push(line);
    });
    if(changing.length>5)lines.push((changing.length-5)+' more changing attributes are not listed.');
    if(!changing.length&&t.attributes.length)lines.push('The recorded attribute values of '+t.object_type+' objects do not change over time.');
    return lines;
  }

  const OBJECT_RULE_TEMPLATES=[['event_cardinality','Each A event involves a number of T objects'],['object_cardinality','Each T object is related to a number of U objects'],['synchronization','A of a T comes after B of every related U'],['supplied_object_cardinality','Supplied assertions: distinct direct U neighbors of each T']];
  // Builds one object-centric rule from form values; throws a readable error.
  function objectRule(values){
    const rule={template:values.template,object_type:values.objectType};
    if(!rule.object_type)throw new Error('Choose object type T.');
    const counts=()=>{
      const min=values.min===''?0:Number(values.min),max=values.max===''?null:Number(values.max);
      if(!Number.isInteger(min)||min<0||(max!==null&&(!Number.isInteger(max)||max<min)))throw new Error('Enter whole numbers with the maximum at least the minimum.');
      if(min===0&&max===null)throw new Error('Set a minimum above 0 or a maximum.');
      rule.min=min;if(max!==null)rule.max=max;
    };
    if(values.template==='event_cardinality'){if(!values.activity)throw new Error('Choose activity A.');rule.activity=values.activity;counts();return rule;}
    if(!values.relatedType)throw new Error('Choose the related object type U.');rule.related_type=values.relatedType;
    if(values.template==='object_cardinality'){if(values.via)rule.via_activity=values.via;counts();return rule;}
    if(values.template==='supplied_object_cardinality'){
      rule.direction=values.direction||'outgoing';
      if(!['outgoing','incoming','both'].includes(rule.direction))throw new Error('Choose incoming, outgoing or both directions.');
      if(!rule.object_type.trim()||!rule.related_type.trim())throw new Error('Choose literal object types T and U.');
      if(values.exactQualifier){
        const q=values.qualifier;
        if(typeof q!=='string'||Array.from(q).length>80||/[\u0000-\u001f\u007f-\u009f\ufffe\uffff]/.test(q)||Array.from(q).some(c=>c.codePointAt(0)>=0xd800&&c.codePointAt(0)<=0xdfff))throw new Error('Use a literal relationship label of at most 80 characters without control characters.');
        rule.qualifier=q;
      }
      counts();if(rule.min>1e6||rule.max>1e6)throw new Error('Use neighbor bounds from 0 to 1,000,000.');return rule;
    }
    if(!values.activity||!values.required)throw new Error('Choose activities A and B.');
    if(values.activity===values.required&&rule.object_type===rule.related_type)throw new Error('Choose a different activity B or object type U.');
    rule.activity=values.activity;rule.required_activity=values.required;return rule;
  }

  function objectRuleSection(graph){
    const box=el('div');box.id='owgObjectRules';
    box.append(el('h4','Object rules'),el('div','Check object relations over the whole retained log; the main case filter does not select these graph assertions. Existing rules relate objects through shared events. The separate supplied-assertion rule checks direct graph shape, including objects with no events; it does not infer temporal validity, synchronization or case membership. Results describe retained links, not verified business compliance or source completeness.','muted'));
    const typeLabel=value=>value.trim()===value?value:JSON.stringify(value);
    const types=(graph.object_types||[]).map(t=>[t.object_type,typeLabel(t.object_type)]);
    const allTypes=(state.objectTypes||[]).map(t=>[t.object_type,typeLabel(t.object_type)]);
    const acts=[...new Set((graph.object_types||[]).flatMap(t=>t.activities.map(a=>a.activity)))].slice(0,500).map(a=>[a,a]);
    const template=select('owgObjectRuleTemplate',OBJECT_RULE_TEMPLATES);
    const activity=select('owgObjectRuleActivity',acts),objectType=select('owgObjectRuleType',types),relatedType=select('owgObjectRuleRelated',types);
    if(types.length>1)relatedType.selectedIndex=1;
    const required=select('owgObjectRuleRequired',acts),via=select('owgObjectRuleVia',[['','Any shared event'],...acts]);
    const direction=select('owgObjectRuleDirection',[['outgoing','Outgoing supplied assertions'],['incoming','Incoming supplied assertions'],['both','Incoming and outgoing supplied assertions']]);
    const exact=input('owgObjectRuleExactQualifier','checkbox'),qualifier=input('owgObjectRuleQualifier','text','Exact relationship label');qualifier.maxLength=160;
    const min=input('owgObjectRuleMin','number');min.min='0';min.step='1';const max=input('owgObjectRuleMax','number');max.min='0';max.step='1';
    const add=button('Add object rule','secondary');add.id='owgObjectRuleAdd';
    const run=button('Check object rules');run.id='owgObjectRuleRun';run.disabled=true;
    const f={activity:field('Activity A',activity),related:field('Related type U',relatedType),required:field('Activity B',required),via:field('Related through',via),direction:field('Supplied assertion direction',direction),exact:field('Match exact supplied qualifier',exact),qualifier:field('Supplied qualifier (empty is a valid exact label)',qualifier),min:field('At least',min),max:field('At most',max)};
    if(types.length<2&&(allTypes.length>1||graph.log&&(graph.log.object_relationships||['ocel','ocelxml'].includes(graph.log.source_format))))template.value='supplied_object_cardinality';
    const sync=()=>{
      const t=template.value,supplied=t==='supplied_object_cardinality';
      for(const control of [objectType,relatedType]){const previous=control.value;setOptions(control,supplied&&allTypes.length?allTypes:types);if([...control.options].some(o=>o.value===previous))control.value=previous;}
      show(f.activity,!['object_cardinality','supplied_object_cardinality'].includes(t));show(f.related,t!=='event_cardinality');show(f.required,t==='synchronization');show(f.via,t==='object_cardinality');
      for(const key of ['direction','exact','qualifier'])show(f[key],supplied);
      show(f.min,t!=='synchronization');show(f.max,t!=='synchronization');
    };
    template.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Rule',template),field('Object type T',objectType),...Object.values(f),add);
    const pending=el('div');pending.id='owgObjectRuleList';
    const savedPick=select('owgObjectRuleSaved',[]);
    const savedName=input('owgObjectRuleSavedName','text');savedName.maxLength=120;
    const loadSet=button('Load rule set','secondary');loadSet.id='owgObjectRuleLoadSet';
    const saveSet=button('Save as new rule set','secondary');saveSet.id='owgObjectRuleSaveSet';
    const updateSet=button('Update loaded rule set','secondary');updateSet.id='owgObjectRuleUpdateSet';
    const deleteSet=button('Delete rule set','secondary');deleteSet.id='owgObjectRuleDeleteSet';
    const savedRow=el('div',null,'row');savedRow.style.alignItems='end';
    savedRow.append(field('Saved rule sets',savedPick),loadSet,deleteSet,field('Rule set name',savedName),saveSet,updateSet);
    const savedStatus=el('div',null,'muted');savedStatus.id='owgObjectRuleSavedStatus';savedStatus.setAttribute('role','status');
    const message=el('div',null,'muted');message.id='owgObjectRuleStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgObjectRuleResults';
    const review=el('div');review.id='owgObjectRuleViolations';
    const history=el('div');history.id='owgObjectRuleObjectHistory';
    box.append(row,pending,savedRow,savedStatus,run,message,out,review,history);
    const rules=[],generation=state.generation,logId=state.logId;let requestVersion=0,last=null;
    const current=turn=>generation===state.generation&&box.isConnected&&turn===requestVersion;
    const base='/v1/event-logs/'+encodeURIComponent(logId);
    const post=async(path,spec)=>(await api(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    const number=n=>Number(n).toLocaleString();
    const utc=timestamp=>timestamp?.replace('T',' ').replace('Z',' UTC')||'—';
    let reviewTurn=0,historyTurn=0;
    const clearHistory=()=>{++historyTurn;history.replaceChildren();};
    const clearReview=()=>{++reviewTurn;review.replaceChildren();clearHistory();};
    function showHistory(valid,object,type){
      const ticket=++historyTurn,active=()=>valid()&&ticket===historyTurn;
      const title=el('h4','Event history for '+object);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),more=button('More events','secondary');
      history.replaceChildren(title,status,rows,more);title.focus();
      let offset=0,shown=0;
      async function load(){
        more.disabled=true;status.textContent='Loading events…';
        try{
          const r=await post('/case-events/query?case_type='+encodeURIComponent(type),{case_id:object,offset,limit:50});
          if(!active())return;
          offset=r.next_offset;shown+=r.events.length;
          status.textContent='Showing '+number(shown)+' of '+number(r.total_events)+' events.';
          rows.appendChild(table(['Activity','Timestamp (UTC)','Event'],r.events.map(e=>[e.activity,utc(e.timestamp),e.event_id])));
          show(more,offset!==null);
        }catch(error){if(active())status.textContent=error.message;}
        finally{if(active())more.disabled=offset===null;}
      }
      more.onclick=load;load();
    }
    function inspect(turn,report,result){
      const ticket=++reviewTurn,valid=()=>current(turn)&&ticket===reviewTurn;
      const title=el('h4',result.description);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row'),previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const spec={rule:result.rule,expected_revision:report.log_revision};
      if(result.relationship_basis){
        const basis=report.supplied_relationship_basis;
        spec.expected_graph={relationships_sha256:basis.relationships_sha256,object_index_sha256:basis.object_index_sha256};
      }
      const event=result.unit!=='objects',object=result.unit!=='events';
      const headers=[...(event?['Event','Timestamp (UTC)']:[]),...(object?['Object']:[]),'Finding',...(object?['Evidence']:[])];
      const cursors=[null];let index=0,following=null;
      async function load(page){
        clearHistory();previous.disabled=next.disabled=true;rows.inert=true;status.textContent='Loading violations…';
        try{
          const r=await post('/object-constraints/violations',{...spec,...(cursors[page]!==null?{after:cursors[page]}:{})});
          if(!valid())return;
          if(!r.complete){
            following=null;rows.replaceChildren();status.textContent=r.unknown_reason==='analysis_memory_limit'?
              'Unknown within available analysis memory.':'Unknown within the disclosed relation work budget.';
            return;
          }
          index=page;following=r.next_after;
          status.textContent=number(r.total)+(r.total===1?' violation.':' violations.')+' Showing '+number(r.violations.length)+' on this page.';
          rows.replaceChildren(r.violations.length?table(headers,r.violations.map(v=>{
            let evidence='No retained events';
            if(v.has_events){
              evidence=button('Open event history','secondary');evidence.setAttribute('aria-label','Open event history for '+v.object_id);
              evidence.onclick=()=>showHistory(valid,v.object_id,result.rule.object_type);
            }
            return [...(event?[v.event_id,utc(v.timestamp)]:[]),...(object?[v.object_id]:[]),v.detail,...(object?[evidence]:[])];
          })):el('div','No matching violations.','muted'));
        }catch(error){if(valid())status.textContent=error.message;}
        finally{if(valid()){rows.inert=false;previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    const savedCurrent=()=>generation===state.generation&&box.isConnected;
    const savedBase=base+'/object-rule-sets';
    let sets=[],loaded=null,savedBusy=false;
    function syncSaved(){
      const selected=sets.find(entry=>entry.set_id===savedPick.value);
      savedPick.disabled=savedBusy||!sets.length;
      loadSet.disabled=deleteSet.disabled=savedBusy||!selected;
      const canSave=!savedBusy&&rules.length>0&&Boolean(savedName.value.trim());
      saveSet.disabled=!canSave;
      updateSet.disabled=!canSave||!loaded||!selected||loaded.set_id!==selected.set_id;
    }
    async function refreshSaved(preferred=savedPick.value){
      const data=await getJSON(savedBase);
      if(!savedCurrent())return;
      sets=data.sets;
      setOptions(savedPick,[['','Choose a saved rule set'],...sets.map(s=>[s.set_id,s.name+' ('+s.rule_count+' '+(s.rule_count===1?'rule':'rules')+')'])]);
      if(sets.some(s=>s.set_id===preferred))savedPick.value=preferred;
      syncSaved();
    }
    async function manageSaved(action){
      savedBusy=true;syncSaved();
      try{await action();}
      catch(error){if(savedCurrent())savedStatus.textContent=error.message;}
      finally{if(savedCurrent()){savedBusy=false;syncSaved();}}
    }
    const sendSaved=async(url,method,spec)=>(await api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    loadSet.onclick=()=>manageSaved(async()=>{
      const key=savedPick.value,turn=requestVersion;
      const r=await getJSON(savedBase+'/'+encodeURIComponent(key));
      if(!savedCurrent())return;
      if(turn!==requestVersion){savedStatus.textContent='The rule editor changed while this set was loading. Load it again to replace the draft.';return;}
      loaded=r;savedName.value=r.name;rules.splice(0,rules.length,...r.rules);drawPending();
      savedStatus.textContent='Loaded '+r.name+'.';
    });
    function storeSaved(existing){
      return manageSaved(async()=>{
        const spec={name:savedName.value.trim(),rules};
        const url=existing?savedBase+'/'+encodeURIComponent(loaded.set_id):savedBase;
        if(existing)spec.expected_revision=loaded.revision;
        const r=await sendSaved(url,existing?'PUT':'POST',spec);
        if(!savedCurrent())return;
        loaded=r;
        savedStatus.textContent=(existing?'Updated ':'Saved ')+r.name+'.';
        await refreshSaved(r.set_id);
      });
    }
    saveSet.onclick=()=>storeSaved(false);updateSet.onclick=()=>storeSaved(true);
    deleteSet.onclick=()=>{
      const selected=sets.find(entry=>entry.set_id===savedPick.value);
      if(!selected||!window.confirm('Delete saved rule set “'+selected.name+'”?'))return;
      manageSaved(async()=>{
        await api(savedBase+'/'+encodeURIComponent(selected.set_id)+'?expected_revision='+selected.revision,{method:'DELETE'});
        if(!savedCurrent())return;
        if(loaded?.set_id===selected.set_id)loaded=null;
        savedStatus.textContent='Deleted '+selected.name+'.';
        await refreshSaved('');
      });
    };
    savedPick.onchange=syncSaved;savedName.oninput=syncSaved;
    const exports=exportButtons('owgObjectRuleExport',()=>last,r=>[
      ['Rule','Complete','Units','Activated','Violating retained shape','Satisfied retained shape','Relationship digest','Object ID/type digest','Append revision','Basis','Independently verified','Source completeness','Relationship validity','Business compliance','Execution authorized'],
      ...r.results.map(res=>[res.description,res.complete,res.units??'',res.activated??'',res.violating??'',res.satisfied??'',res.relationship_basis?r.supplied_relationship_basis?.relationships_sha256||'':'',res.relationship_basis?r.supplied_relationship_basis?.object_index_sha256||'':'',res.relationship_basis?r.supplied_relationship_basis?.revision??'':'',res.relationship_basis||'recorded_event_links',false,res.source_completeness||'unknown',res.relationship_validity||'unknown',res.business_compliance||'unknown',false])]);
    show(exports.node,false);box.append(exports.node);
    const counts=r=>r.max===undefined?'at least '+r.min:r.min===r.max?'exactly '+r.min:r.min+'–'+r.max;
    const label=r=>r.template==='supplied_object_cardinality'?'supplied assertions: each '+typeLabel(r.object_type)+' has '+counts(r)+' distinct '+typeLabel(r.related_type)+' neighbors ('+r.direction+(r.qualifier!==undefined?', exact qualifier '+JSON.stringify(r.qualifier):', all qualifiers')+')':({event_cardinality:'each '+r.activity+' involves '+counts(r)+' '+r.object_type,object_cardinality:'each '+r.object_type+' is related to '+counts(r)+' '+r.related_type+(r.via_activity?' through '+r.via_activity:''),synchronization:r.activity+' events of '+r.object_type+' objects come after '+r.required_activity+' of every related '+r.related_type}[r.template]);
    function drawPending(){
      requestVersion++;last=null;exports.enable(false);show(exports.node,false);out.replaceChildren();clearReview();
      pending.replaceChildren(rules.length?table(['Rule',''],rules.map((r,i)=>{const del=button('Remove','secondary');del.setAttribute('aria-label','Remove object rule '+label(r));del.onclick=()=>{rules.splice(i,1);drawPending();};return [label(r),del];})):el('div','No object rules added yet.','muted'));
      run.disabled=!rules.length;
      syncSaved();
    }
    drawPending();
    manageSaved(()=>refreshSaved());
    add.onclick=()=>{try{if(rules.length>=20)throw new Error('Check at most 20 object rules at once.');rules.push(objectRule({template:template.value,activity:activity.value,objectType:objectType.value,relatedType:relatedType.value,required:required.value,via:via.value,min:min.value,max:max.value,direction:direction.value,exactQualifier:exact.checked,qualifier:qualifier.value}));message.textContent='';drawPending();}catch(error){message.textContent=error.message;}};
    run.onclick=async()=>{
      const turn=++requestVersion;run.disabled=true;last=null;exports.enable(false);show(exports.node,false);clearReview();
      try{
        const r=await post('/object-constraints',{rules});
        if(!current(turn))return;
        const unit={events:'events',objects:'objects',event_object_pairs:'event–object pairs'};
        const examples=res=>{
          const shown=res.violating_examples.slice(0,5),text=shown.map(e=>e.unit+' ('+e.detail+')').join(', ');
          return text+(res.violating>shown.length?'; showing '+shown.length+' of '+res.violating+' retained violations':'');
        };
        const rows=r.results.map(res=>{
          const open=button('Inspect violations','secondary');open.setAttribute('aria-label','Inspect violations for '+res.description);
          open.onclick=()=>inspect(turn,r,res);
          return res.complete?[res.description,Number(res.activated).toLocaleString()+' '+unit[res.unit],Number(res.violating).toLocaleString(),
          res.violation_rate===null?'—':pct(res.violation_rate)+(res.violation_rate_ci95?' ['+pct(res.violation_rate_ci95[0])+', '+pct(res.violation_rate_ci95[1])+']':' (retained set)'),
          examples(res),res.violating?open:'']:[res.description,'—',res.unknown_reason==='analysis_memory_limit'?'Unknown within available analysis memory':'Unknown within the disclosed relation work budget','—','',''];
        });
        out.replaceChildren(el('div',r.rules_with_violations+' of '+r.results.length+' object rules have violations.','muted'),
          table(['Rule','Applies to','Violating','Violation rate (95% CI)','Examples','Evidence'],rows),el('div',r.interpretation,'muted'));
        if(r.supplied_relationship_basis){
          const basis=r.supplied_relationship_basis;last=r;show(exports.node,true);exports.enable(true);
          out.append(el('p','Supplied retained graph: '+basis.retained_relationships+' assertions; digest '+basis.relationships_sha256+'; object ID/type digest '+basis.object_index_sha256+'; append revision '+basis.revision+'. This pins the retained assertion set and object population, not source completeness or verified business compliance.','muted'));
        }
      }catch(error){if(current(turn))out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(current(turn))run.disabled=!rules.length;}
    };
    return box;
  }

  // Discovered or edited object-centric model: arcs as Single/Variable/Removed choices.
  const ARC_CHOICES=[['single','Exactly one'],['variable','One or more'],['remove','Not involved']];
  function objectModelArcs(arcs,choices){
    return arcs.map((a,i)=>[a,choices[i]??(a.variable?'variable':'single')]).filter(([,c])=>c!=='remove')
      .map(([a,c])=>({activity:a.activity,object_type:a.object_type,variable:c==='variable'}));
  }
  const OBJECT_ARC_ISSUES={missing_object:'No object of the type',several_objects_on_single_arc:'Several objects where one is expected',type_without_arc:'Type involved without an arc'};
  // Plain reading of an object-centric conformance result.
  function objectModelText(r){
    const s=r.summary,ratio=(n,d)=>Number(n).toLocaleString()+' of '+Number(d).toLocaleString();
    const deviating=t=>Object.entries({...t.missing_tokens_by_activity}).slice(0,3).map(([a,n])=>a+' ('+n+')').join(', ')||'—';
    return {
      summary:ratio(s.fitting_objects,s.replayed_objects)+' replayed objects fit their type’s model; '+ratio(s.fitting_events,s.events_checked)+' events match their arcs.'+(s.complete?'':' Some objects were not replayed (see Unknown).'),
      lifecycles:r.lifecycles.map(t=>[t.object_type,Number(t.objects).toLocaleString(),ratio(t.fitting_objects,t.replayed_objects),t.token_fitness===null?'—':Number(t.token_fitness).toFixed(3),Number(t.unknown_objects).toLocaleString(),deviating(t)]),
      issues:r.arcs.issues_by_arc.slice(0,20).map(i=>[i.activity,i.object_type,OBJECT_ARC_ISSUES[i.issue]||i.issue,Number(i.events).toLocaleString()]),
      examples:r.arcs.examples.slice(0,5).map(e=>e.event_id+' ('+e.activity+', '+e.object_type+': '+e.objects+')')};
  }

  function objectModelSection(){
    const box=el('div');box.id='owgObjectModel';
    box.append(el('h4','Object process model'),el('div','Discover one lifecycle model per object type and which object types each activity involves, then check every object and event of the log against it. Edit the arcs to state what you expect. This checks lifecycles and arcs separately; use object rules to check that objects move together. Results describe recorded links, not verified business relationships.','muted'));
    const discover=button('Discover object model','secondary');discover.id='owgObjectModelDiscover';
    const check=button('Check object model');check.id='owgObjectModelCheck';check.disabled=true;
    const message=el('div',null,'muted');message.id='owgObjectModelStatus';message.setAttribute('role','status');
    const arcsOut=el('div');arcsOut.id='owgObjectModelArcs';
    const out=el('div');out.id='owgObjectModelResults';
    const issueList=el('div');issueList.id='owgObjectModelArcIssues';
    const eventList=el('div');eventList.id='owgObjectModelArcEvents';
    const objectList=el('div');objectList.id='owgObjectModelEventObjects';
    const lifecycle=el('div');lifecycle.id='owgObjectModelObjectEvents';
    box.append(discover,arcsOut,check,message,out,eventList,objectList,lifecycle);
    const generation=state.generation,logId=state.logId;let turn=0,model=null,selects=[];
    const current=t=>generation===state.generation&&box.isConnected&&t===turn;
    const base='/v1/event-logs/'+encodeURIComponent(logId);
    const path=p=>base+'/object-process-model/'+p;
    const post=async(url,spec)=>(await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    const number=n=>Number(n).toLocaleString();
    const utc=timestamp=>timestamp.replace('T',' ').replace('Z',' UTC');
    let eventTurn=0,objectTurn=0,lifecycleTurn=0;
    const clearLifecycle=()=>{++lifecycleTurn;lifecycle.replaceChildren();};
    const clearObjects=()=>{++objectTurn;objectList.replaceChildren();clearLifecycle();};
    const clearEvents=()=>{++eventTurn;eventList.replaceChildren();clearObjects();};
    function pageReview(panel,titleText,url,spec,valid,render,reset){
      const heading=el('h4',titleText);heading.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row'),previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);panel.replaceChildren(heading,status,rows,nav);heading.focus();
      const cursors=[null];let index=0,following=null;
      async function load(page){
        reset();previous.disabled=next.disabled=true;status.textContent='Loading evidence…';rows.inert=true;
        try{
          const r=await post(url,{...spec,...(cursors[page]!==null?{after:cursors[page]}:{})});
          if(!valid())return;
          index=page;following=r.next_after;
          const content=render(r);status.textContent=content.text;rows.replaceChildren(content.table);
        }catch(error){if(valid())status.textContent=error.message;}
        finally{if(valid()){rows.inert=false;previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    function showLifecycle(t,item){
      const ticket=++lifecycleTurn,valid=()=>current(t)&&ticket===lifecycleTurn;
      const heading=el('h4','Event history for '+item.object_id);heading.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),more=button('More events','secondary');
      lifecycle.replaceChildren(heading,status,rows,more);heading.focus();
      let offset=0,shown=0;
      async function load(){
        more.disabled=true;
        try{
          const r=await post(base+'/case-events/query?case_type='+encodeURIComponent(item.object_type),{case_id:item.object_id,offset,limit:50});
          if(!valid())return;
          offset=r.next_offset;shown+=r.events.length;
          status.textContent='Showing '+number(shown)+' of '+number(r.total_events)+' events.';
          rows.appendChild(table(['Activity','Timestamp (UTC)','Event'],r.events.map(e=>[e.activity,utc(e.timestamp),e.event_id])));
          show(more,offset!==null);
        }catch(error){if(valid())status.textContent=error.message;}
        finally{if(valid())more.disabled=offset===null;}
      }
      more.onclick=load;load();
    }
    function showObjects(t,selection,event){
      const ticket=++objectTurn,valid=()=>current(t)&&ticket===objectTurn;
      pageReview(objectList,'Linked objects for '+event.event_id,path('conformance/arc-evidence'),
        {...selection,event_id:event.event_id},valid,r=>({
          text:number(r.total)+(r.total===1?' linked object.':' linked objects.')+' '+number(r.objects_of_type)+' of type '+selection.object_type+'. Showing '+number(r.objects.length)+' on this page.',
          table:r.objects.length?table(['Object','Type','Distinct link labels','Evidence'],r.objects.map(item=>{
            const open=button('Open event history','secondary');open.setAttribute('aria-label','Open event history for '+item.object_id);
            open.onclick=()=>showLifecycle(t,item);
            return [item.object_id,item.object_type,number(item.links),open];
          })):el('div','No retained objects linked to this event.','muted')
        }),clearLifecycle);
    }
    function showIssue(t,spec,issue){
      const ticket=++eventTurn,valid=()=>current(t)&&ticket===eventTurn;
      const selection={model:spec.model,activity:issue.activity,object_type:issue.object_type,issue:issue.issue};
      pageReview(eventList,issue.activity+' · '+issue.object_type+' · '+OBJECT_ARC_ISSUES[issue.issue],
        path('conformance/arc-evidence'),selection,valid,r=>({
          text:number(r.total)+(r.total===1?' matching event.':' matching events.')+' Showing '+number(r.events.length)+' on this page.',
          table:r.events.length?table(['Event','Timestamp','Objects of this type','Evidence'],r.events.map(e=>{
            const open=button('View linked objects','secondary');open.setAttribute('aria-label','View linked objects for '+e.event_id);
            open.onclick=()=>showObjects(t,selection,e);
            return [e.event_id,utc(e.timestamp),number(e.objects),open];
          })):el('div','No matching events.','muted')
        }),clearObjects);
    }
    function showIssues(t,spec,issues){
      issueList.replaceChildren();clearEvents();
      if(!issues.length)return;
      const rows=el('div'),notice=el('div');notice.setAttribute('role','status');
      const nav=el('div',null,'row'),previous=button('Previous issue page','secondary'),next=button('Next issue page','secondary');
      nav.append(previous,next);show(nav,issues.length>20);
      issueList.append(el('h4','Object arc issues'),notice,rows,nav);
      let offset=0;
      function render(){
        clearEvents();
        const page=issues.slice(offset,offset+20);
        notice.textContent='Showing issue groups '+number(offset+1)+'–'+number(offset+page.length)+' of '+number(issues.length)+'.';
        rows.replaceChildren(table(['Activity','Object type','Arc issue','Events','Evidence'],page.map(item=>{
          const label=OBJECT_ARC_ISSUES[item.issue];
          const inspect=button('Inspect events','secondary');
          inspect.setAttribute('aria-label','Inspect events for '+item.activity+' · '+item.object_type+' · '+label);
          inspect.onclick=()=>showIssue(t,spec,item);
          return [item.activity,item.object_type,label,number(item.events),inspect];
        })));
        previous.disabled=offset===0;next.disabled=offset+20>=issues.length;
      }
      previous.onclick=()=>{offset-=20;render();};
      next.onclick=()=>{offset+=20;render();};
      render();
    }
    discover.onclick=async()=>{
      const t=++turn;discover.disabled=check.disabled=true;out.replaceChildren();issueList.replaceChildren();clearEvents();message.textContent='Discovering…';
      try{
        const r=await (await api(path('discovery'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
        if(!current(t))return;
        model=r.model;selects=model.arcs.map((a,i)=>{const s=select('owgObjectModelArc'+i,ARC_CHOICES);s.value=a.variable?'variable':'single';s.setAttribute('aria-label',a.activity+' involves '+a.object_type);return s;});
        const skipped=r.types_not_discovered.map(x=>x.object_type+': '+x.reason);
        message.textContent='Discovered '+model.types.length+' object type models with '+model.arcs.length+' arcs.'+(skipped.length?' Not discovered: '+skipped.join('; '):'');
        arcsOut.replaceChildren(table(['Activity','Object type','Objects per event (measured)','Expected'],model.arcs.map((a,i)=>{
          const m=r.arc_measurements.find(x=>x.activity===a.activity&&x.object_type===a.object_type);
          return [a.activity,a.object_type,m?m.objects_per_event.min+'–'+m.objects_per_event.max+' in '+Number(m.events_with_type).toLocaleString()+' events':'—',selects[i]];})));
        check.disabled=false;
      }catch(error){if(current(t))message.textContent=error.message;}
      finally{if(current(t))discover.disabled=false;}
    };
    check.onclick=async()=>{
      const t=++turn;check.disabled=true;out.replaceChildren(el('div','Checking…','muted'));issueList.replaceChildren();clearEvents();
      try{
        const spec={model:{...model,arcs:objectModelArcs(model.arcs,selects.map(s=>s.value))}};
        const r=await post(path('conformance'),spec);
        if(!current(t))return;
        const v=objectModelText(r);
        out.replaceChildren(el('div',v.summary,'note'),table(['Object type','Objects','Fitting','Token fitness','Unknown','Missing tokens at'],v.lifecycles),
          ...(v.issues.length?[]:[el('div','Every checked event matches its arcs.','muted')]),
          el('div',r.interpretation,'muted'),issueList);
        showIssues(t,spec,r.arcs.issues_by_arc);
      }catch(error){if(current(t))out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(current(t))check.disabled=false;}
    };
    return box;
  }

  // Impact request from the form: a local date becomes midnight UTC; empty optional fields are left out.
  function impactSpec(v){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(v.date||''))throw new Error('Enter the date the change went live.');
    const spec={intervention_at:v.date+'T00:00:00Z',metric:v.metric};
    if(v.metric==='sla_breach'){const h=Number(v.slaHours);if(!(h>0))throw new Error('Enter the SLA in hours.');spec.sla_seconds=Math.round(h*3600);}
    if(v.metric==='activity_count'){if(!v.activity)throw new Error('Choose the activity to count.');spec.activity=v.activity;}
    if(String(v.window||'').trim()){const w=Number(v.window);if(!Number.isInteger(w)||w<1)throw new Error('The window is a whole number of days.');spec.window_days=w;}
    if(String(v.recent||'').trim()){const r=Number(v.recent);if(!Number.isInteger(r)||r<0)throw new Error('Recent days is a whole number.');spec.exclude_recent_days=r;}
    const treated={};if(v.treatedActivity)treated.activity=v.treatedActivity;if(v.treatedResource)treated.resource=v.treatedResource;
    if(Object.keys(treated).length)spec.treated=treated;
    return spec;
  }
  // Plain rows for an impact result.
  function impactRows(r){
    const shown=x=>x===null||x===undefined?'—':r.unit==='seconds'?duration(Math.abs(x)):r.unit==='share of cases'?pct(Math.abs(x)):Number(Math.abs(x)).toFixed(2);
    const signed=x=>x===null||x===undefined?'—':(x<0?'−':x>0?'+':'')+shown(x);
    const range=ci=>ci?signed(ci[0])+' to '+signed(ci[1]):'—';
    const rows=Object.entries(r.groups).map(([name,g])=>[name==='all'?'All cases':name==='treated'?'Treated':'Control',Number(g.baseline.cases).toLocaleString(),shown(g.baseline.mean),Number(g.follow_up.cases).toLocaleString(),shown(g.follow_up.mean)]);
    const c=r.change,d=r.control_adjusted;
    return {rows,
      change:c.estimated?'Mean change '+signed(c.mean_change)+' (95% '+range(c.mean_change_ci95)+'); median change '+signed(c.median_change)+' (95% '+range(c.median_change_ci95)+').':c.reason,
      control:d?(d.estimated?'Against the control cohort: '+signed(d.difference_in_differences)+' (95% '+range(d.ci95)+').':d.reason):null,
      placebo:r.placebo.estimated?'Placebo check at '+String(r.placebo.placebo_at).slice(0,10)+': '+signed(r.placebo.mean_change)+' (95% '+range(r.placebo.mean_change_ci95)+')'+(r.placebo.pre_trend_detected?', already changing before the intervention.':', no earlier change.'):'Placebo check: '+r.placebo.reason+'.'};
  }

  function stuckSection(inspection){
    const box=section('Stuck open cases');box.id='owgEventLogStuck';
    box.appendChild(el('div','Open cases idle longer than usual after their latest activity. The case filter selects which cases to check. Wait history and the latest event come from the selected case notion.','muted'));
    const ends=input('owgStuckEnds','text','Close, Cancel');
    const pctInput=select('owgStuckPercentile',[['0.9','Longer than 90% of past waits'],['0.95','Longer than 95%'],['0.75','Longer than 75%']]);
    const run=button('Find stuck cases','secondary');run.id='owgStuckRun';
    const out=el('div');out.id='owgStuckResults';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities that end a case (comma-separated or JSON array)',ends),field('Stuck when idle',pctInput),run);
    box.append(row,out);
    const generation=state.generation;
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const raw=ends.value.trim();
        const endActivities=/^\[\s*(?:"|\])/.test(raw)?JSON.parse(raw):raw.split(',').map(x=>x.trim()).filter(Boolean);
        const spec={end_activities:endActivities,percentile:Number(pctInput.value),cohort:globalCohort()};
        if(state.caseType)spec.case_type=state.caseType;
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/stuck-cases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const parts=[el('div',Number(r.stuck_cases).toLocaleString()+' of '+Number(r.judged_open_cases).toLocaleString()+' judged open cases look stuck'+(r.not_judged_open_cases?'; '+Number(r.not_judged_open_cases).toLocaleString()+' open cases sit at activities with too little history to judge':'')+'.')];
        if(r.cases.length)parts.push(table(['Case','Latest activity','Idle for','Usually waits','Stuck after','Events so far','Evidence'],r.cases.map(c=>{
          const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
          open.onclick=()=>inspection.open(c.case_id);
          return [c.case_id,c.latest_activity,duration(c.idle_seconds),duration(c.usual_wait_seconds),duration(c.threshold_seconds),c.events_so_far,open];
        })));
        if(r.cases_truncated)parts.push(el('div','Showing '+r.cases.length+' of '+Number(r.stuck_cases).toLocaleString()+' stuck cases.','muted'));
        const acts=r.by_activity.filter(a=>a.stuck_cases);
        if(acts.length)parts.push(el('h4','Where they wait'),table(['Latest activity','Open cases','Stuck'],acts.map(a=>[a.activity,a.open_cases,a.stuck_cases])));
        parts.push(el('div',r.interpretation,'muted'));out.replaceChildren(...parts);
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function impactSection(activityNames,inspection){
    const box=section('Intervention impact');box.id='owgImpact';
    box.appendChild(el('div','Compare cases that started before and after a change went live, such as an automation or a new rule. Optionally compare with cases the change did not apply to. Results show what changed between the periods, not proof that the change caused it.','muted'));
    const date=input('owgImpactDate','date'),metric=select('owgImpactMetric',[['cycle_time','Cycle time'],['rework','Rework (an activity repeated)'],['sla_breach','SLA breaches'],['activity_count','Count of one activity']]);
    const sla=input('owgImpactSla','number');sla.min='0';sla.step='any';sla.placeholder='hours';
    const activity=select('owgImpactActivity',activityNames.map(a=>[a,a]));
    const windowDays=input('owgImpactWindow','number');windowDays.min='1';windowDays.placeholder='all';
    const recent=input('owgImpactRecent','number');recent.min='0';recent.placeholder='0';
    const treatedActivity=select('owgImpactTreatedActivity',[['','Every case'],...activityNames.map(a=>[a,'Cases with '+a])]);
    const treatedResource=input('owgImpactTreatedResource','text','Resource');treatedResource.maxLength=200;
    const run=button('Measure impact');run.id='owgImpactRun';
    const f={sla:field('SLA (hours)',sla),activity:field('Activity',activity)};
    const sync=()=>{show(f.sla,metric.value==='sla_breach');show(f.activity,metric.value==='activity_count');};metric.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Change went live (UTC)',date),field('Measure',metric),...Object.values(f),field('Days on each side',windowDays),field('Leave out the last … days',recent),field('Change applies to',treatedActivity),field('…or to resource',treatedResource),run);
    const out=el('div');out.id='owgImpactResults';out.setAttribute('role','status');
    // Change markers: pick a saved date, or save this one with a label, so the change is recorded once.
    const markerPick=select('owgImpactMarker',[['','Pick a saved change']]);
    const markerLabel=input('owgImpactMarkerLabel','text','e.g. Automation went live');markerLabel.maxLength=120;
    const markerSave=button('Save this date as a change','secondary');markerSave.id='owgImpactMarkerSave';
    const markerRemove=button('Remove saved change','secondary');markerRemove.id='owgImpactMarkerRemove';markerRemove.disabled=true;
    const markerStatus=el('div',null,'muted');markerStatus.id='owgImpactMarkerStatus';markerStatus.setAttribute('role','status');
    const markerRow=el('div',null,'row');markerRow.style.alignItems='end';
    markerRow.append(field('Saved changes',markerPick),markerRemove,field('Label for this date',markerLabel),markerSave);
    const evidence=el('div');evidence.id='owgImpactCaseReview';
    box.append(markerRow,markerStatus,row,out,evidence);
    const markersBase='/v1/event-logs/'+encodeURIComponent(state.logId)+'/change-markers';let markerList=[];
    const showMarkers=r=>{markerList=r.markers;
      setOptions(markerPick,[['',r.markers.length?'Pick a saved change':'No saved changes yet'],...r.markers.map(m=>[m.marker_id,m.marked_at.slice(0,10)+' — '+m.label+(m.within_log?'':' (outside this log)')])]);
      markerRemove.disabled=true;};
    markerPick.onchange=()=>{const m=markerList.find(x=>x.marker_id===markerPick.value);markerRemove.disabled=!m;
      if(m){date.value=m.marked_at.slice(0,10);markerStatus.textContent='Using the change "'+m.label+'" on '+m.marked_at.slice(0,10)+'.'+(m.note?' '+m.note:'');}};
    markerSave.onclick=async()=>{
      if(!date.value){markerStatus.textContent='Choose the date the change went live first.';return;}
      if(!markerLabel.value.trim()){markerStatus.textContent='Give the change a label.';return;}
      try{const r=await (await api(markersBase,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({marked_at:date.value+'T00:00:00Z',label:markerLabel.value})})).json();
        showMarkers(r);markerPick.value=r.added;markerRemove.disabled=false;markerLabel.value='';
        markerStatus.textContent='Saved the change on '+date.value+'.';}
      catch(error){markerStatus.textContent=error.message;}};
    markerRemove.onclick=async()=>{const m=markerList.find(x=>x.marker_id===markerPick.value);if(!m)return;
      try{showMarkers(await (await api(markersBase+'/'+encodeURIComponent(m.marker_id)+'/delete',{method:'POST'})).json());
        markerStatus.textContent='Removed the saved change "'+m.label+'".';}
      catch(error){markerStatus.textContent=error.message;}};
    const markersGeneration=state.generation;
    getJSON(markersBase).then(r=>{if(markersGeneration===state.generation)showMarkers(r);}).catch(error=>{markerStatus.textContent=error.message;});
    const generation=state.generation,logId=state.logId;let turn=0;
    const invalidate=()=>{turn++;run.disabled=false;out.replaceChildren();evidence.replaceChildren();};
    for(const control of [date,metric,sla,activity,windowDays,recent,treatedActivity,treatedResource]){
      control.addEventListener('input',invalidate);control.addEventListener('change',invalidate);
    }
    markerPick.addEventListener('change',invalidate);
    run.onclick=async()=>{
      const t=++turn;
      evidence.replaceChildren();
      let spec;try{spec=impactSpec({date:date.value,metric:metric.value,slaHours:sla.value,activity:activity.value,window:windowDays.value,recent:recent.value,treatedActivity:treatedActivity.value,treatedResource:treatedResource.value.trim()});}
      catch(error){out.replaceChildren(el('div',error.message,'muted'));return;}
      if(state.caseType)spec.case_type=state.caseType;
      run.disabled=true;out.replaceChildren(el('div','Measuring…','muted'));
      try{
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(logId)+'/impact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation||!box.isConnected||t!==turn)return;
        const v=impactRows(r);
        const rows=Object.entries(r.groups).map(([group,values],index)=>{
          const open=(period,count)=>{
            if(!count)return '0';
            const b=button(Number(count).toLocaleString(),'secondary');
            b.setAttribute('aria-label','Review '+group+' '+period+' cases');
            b.onclick=()=>{
              const panel=el('div'),heading=el('h4',group+' · '+period),message=el('div'),list=el('div');
              const next=button('Next case page','secondary');let cursor=null;
              panel.append(heading,message,list,next);evidence.replaceChildren(panel);
              async function load(after){
                next.disabled=true;message.textContent='Loading cases…';
                try{
                  const data=await (await api('/v1/event-logs/'+encodeURIComponent(logId)+'/impact/cases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({analysis:spec,group,period,after})})).json();
                  if(t!==turn||!panel.isConnected)return;
                  cursor=data.next_after;message.textContent=Number(data.selected_cases).toLocaleString()+' matching cases.';
                  list.replaceChildren(table(['Case','Started','Ended','Value'],data.cases.map(c=>{
                    const link=button(c.case_id,'secondary');link.setAttribute('aria-label','Open case '+c.case_id);link.onclick=()=>inspection.open(c.case_id);
                    return [link,c.started_at,c.ended_at,c.value];
                  })));
                  next.disabled=cursor===null;
                }catch(error){if(t===turn&&panel.isConnected)message.textContent=error.message;}
              }
              next.onclick=()=>load(cursor);load(null);
            };
            return b;
          };
          return [v.rows[index][0],open('baseline',values.baseline.cases),v.rows[index][2],open('follow_up',values.follow_up.cases),v.rows[index][4]];
        });
        out.replaceChildren(el('div',r.reading,'note'),table(['Cases','Before','Mean before','After','Mean after'],rows),el('div',v.change,'muted'),
          ...(v.control?[el('div',v.control,'muted')]:[]),el('div',v.placebo,'muted'),el('div',r.interpretation,'muted'));
      }catch(error){if(t===turn)out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(t===turn)run.disabled=false;}
    };
    return box;
  }

  // Domain pack mapping: role -> chosen log name, leaving out roles mapped to nothing.
  function packMapping(roles,values){
    const out={object_types:{},activities:{}};
    for(const kind of ['object_types','activities'])for(const r of roles[kind]){const v=values[kind][r.role];if(v)out[kind][r.role]=v;}
    return out;
  }
  // A starting guess: the installed mapping, else a log name equal to the role's label (ignoring case).
  function packGuess(role,names,installed){
    if(installed&&names.includes(installed))return installed;
    return names.find(n=>n.trim().toLowerCase()===role.label.toLowerCase())||'';
  }
  // Plain rows for a domain pack report.
  function packReportRows(r){
    const value=(m,x)=>x===null||x===undefined?'—':m.unit==='seconds'?duration(x):Number(x).toLocaleString(undefined,{maximumFractionDigits:2});
    return {
      metrics:r.metrics.map(m=>[m.name,m.case_type,Number(m.cases).toLocaleString(),value(m,m.median),value(m,m.p90),Number(m.coverage.cases_with_value??m.cases).toLocaleString()+' of '+Number(m.cases).toLocaleString()]),
      compliance:r.compliance.flatMap(g=>g.results.map(x=>[g.case_type,x.description,Number(x.activated_cases).toLocaleString(),Number(x.violating_cases).toLocaleString(),Number(x.unknown_cases||0).toLocaleString()])),
      objectRules:(r.object_rules?r.object_rules.results:[]).map(x=>[x.description,x.complete===false?'—':Number(x.activated).toLocaleString(),x.complete===false?'Unknown':Number(x.violating).toLocaleString()]),
      model:r.object_model?Number(r.object_model.summary.fitting_objects).toLocaleString()+' of '+Number(r.object_model.summary.replayed_objects).toLocaleString()+' objects fit the reference lifecycles; '+Number(r.object_model.summary.fitting_events).toLocaleString()+' of '+Number(r.object_model.summary.events_checked).toLocaleString()+' events match its arcs.':'No reference lifecycle applies with this mapping.',
      notApplicable:r.not_applicable.map(n=>n.section.replace(/_/g,' ')+' '+(n.index+1)+' (needs '+n.unmapped_role.replace(/_/g,' ')+')')};
  }

  function domainPackSection(){
    const box=section('Domain packs');box.id='owgDomainPacks';
    box.appendChild(el('div','Apply ready-made definitions for a well-known process to this log: case metrics, compliance and object rules and a reference object model. Map each role to the names your log uses; the pack states its assumptions and is not validated against your source system.','muted'));
    const status=el('div',null,'muted');status.id='owgDomainPackStatus';status.setAttribute('role','status');
    const form=el('div');form.id='owgDomainPackForm';
    const out=el('div');out.id='owgDomainPackReport';
    const historyOut=el('div');historyOut.id='owgDomainPackConfigurations';
    let historyTurn=0;
    const setup=button('Set up a domain pack','secondary');setup.id='owgDomainPackSetup';
    box.append(setup,form,status,out,historyOut);
    const generation=state.generation,logId=state.logId,base='/v1/event-logs/'+encodeURIComponent(logId)+'/domain-packs';
    let turn=0;const current=t=>generation===state.generation&&box.isConnected&&t===turn;
    const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    setup.onclick=async()=>{
      const t=++turn;setup.disabled=true;historyTurn++;historyOut.replaceChildren();form.replaceChildren();out.replaceChildren();status.textContent='Loading…';
      try{
        const [catalog,installs]=await Promise.all([getJSON('/v1/event-log-domain-packs'),getJSON(base)]);
        if(!current(t))return;
        const chooser=select('owgDomainPackChoice',catalog.packs.map(p=>[p.pack_id,p.title]));
        const preferred=catalog.packs.find(p=>installs.installs.some(i=>i.pack_id===p.pack_id));if(preferred)chooser.value=preferred.pack_id;
        const show=()=>{
          const pack=catalog.packs.find(p=>p.pack_id===chooser.value),installed=installs.installs.find(i=>i.pack_id===pack.pack_id);
          const choices={object_types:{},activities:{}},inputs={};
          const rows=[];
          for(const [kind,names] of [['object_types',installs.log_object_types],['activities',installs.log_activities]])for(const r of pack[kind]){
            // Allow an exact source label beyond the bounded suggestions. Pick
            // a sentinel that cannot collide with a real suggested label.
            let custom='__owg_exact_label__';while(names.includes(custom))custom+='_';
            const s=select('owgDomainPackRole-'+r.role,[['',r.required?'Choose (required)':'Not in this log'],...names.map(n=>[n,n]),[custom,'Enter exact label…']]);
            const exact=input('owgDomainPackExact-'+r.role,'text');exact.maxLength=400; // HTML counts UTF-16 units; server enforces 200 Unicode characters.
            exact.setAttribute('aria-label','Exact label for '+r.label);
            const saved=installed&&installed.mapping[kind][r.role];
            s.value=saved&&!names.includes(saved)?custom:packGuess(r,names,saved);
            exact.value=s.value===custom?saved:'';exact.hidden=s.value!==custom;
            s.setAttribute('aria-label',r.label+(r.required?' (required)':' (optional)'));
            const selected=()=>s.value===custom?exact.value:s.value;
            s.onchange=()=>{exact.hidden=s.value!==custom;choices[kind][r.role]=selected();edited();};
            exact.oninput=()=>{choices[kind][r.role]=selected();edited();};
            choices[kind][r.role]=selected();
            const lookup=input('owgDomainPackLookup-'+r.role,'search');lookup.maxLength=400;
            lookup.setAttribute('aria-label','Search labels for '+r.label);
            const find=button('Find labels','secondary');find.setAttribute('aria-label','Find labels for '+r.label);
            const more=button('More labels','secondary');more.hidden=true;more.setAttribute('aria-label','More labels for '+r.label);
            const found=el('div');found.id='owgDomainPackMatches-'+r.role;
            const hint=el('div',null,'muted');hint.setAttribute('role','status');
            let lookupTurn=0,nextAfter=null;
            const findPage=async after=>{
              if(generation!==state.generation||!box.isConnected)return;
              const q=lookup.value,request=++lookupTurn;find.disabled=true;more.hidden=true;
              if(!q||Array.from(q).length>200){hint.textContent='Enter 1 to 200 characters.';find.disabled=false;return;}
              hint.textContent='Searching…';found.replaceChildren();
              try{
                const url='/v1/event-logs/'+encodeURIComponent(logId)+'/domain-pack-labels';
                const result=await (await post(url,{kind,search:q,after:after||''})).json();
                if(!s.isConnected||generation!==state.generation||request!==lookupTurn)return;
                for(const label of result.labels){
                  const choose=button(label,'secondary');
                  choose.onclick=()=>{s.value=custom;exact.value=label;exact.hidden=false;choices[kind][r.role]=label;edited();hint.textContent='Label selected.';};
                  found.appendChild(choose);
                }
                nextAfter=result.next_after;more.hidden=!nextAfter;
                hint.textContent=result.labels.length?result.labels.length+' labels found on this page.':'No labels found.';
              }catch(error){if(s.isConnected&&generation===state.generation&&request===lookupTurn)hint.textContent=error.message;}
              finally{if(s.isConnected&&generation===state.generation&&request===lookupTurn)find.disabled=false;}
            };
            lookup.oninput=()=>{lookupTurn++;find.disabled=false;more.hidden=true;nextAfter=null;found.replaceChildren();hint.textContent='';};
            lookup.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();findPage('');}};
            find.onclick=()=>findPage('');more.onclick=()=>findPage(nextAfter);
            const finder=el('details');finder.id='owgDomainPackFinder-'+r.role;
            finder.append(el('summary','Find a label'),lookup,find,more,hint,found);
            const control=el('div');control.append(s,exact,finder);
            rows.push([kind==='object_types'?'Object type':'Activity',r.label+(r.required?'':' (optional)'),control]);
          }
          const params=pack.parameters.map(p=>{const i=input('owgDomainPackParam-'+p.name,'number');i.min=String(p.min);i.max=String(p.max);i.value=String(installed?installed.parameters[p.name]:p.default);inputs[p.name]=i;i.oninput=()=>edited();return field(p.label,i);});
          const install=button(installed?'Install '+pack.latest_version+(installed.version===pack.latest_version?' again':' (upgrade from '+installed.version+')'):'Install '+pack.title+' '+pack.latest_version);install.id='owgDomainPackInstall';
          const run=button('Run pack report','secondary');run.id='owgDomainPackRun';run.disabled=!installed;
          const remove=button('Uninstall','secondary');remove.id='owgDomainPackUninstall';remove.disabled=!installed;
          const paramRow=el('div',null,'row');paramRow.style.alignItems='end';paramRow.append(...params);
          let reportSnapshot=null,reportTurn=null;
          const exportReport=button('Download report JSON','secondary');exportReport.id='owgDomainPackDownload';exportReport.disabled=true;
          const invalidate=()=>{turn++;reportSnapshot=null;reportTurn=null;exportReport.disabled=true;out.replaceChildren();};
          const edited=()=>{invalidate();run.disabled=true;status.textContent='Inputs changed. Install the pack to apply them before running a new report.';};
          exportReport.onclick=()=>{
            if(!reportSnapshot||!current(reportTurn))return;
            download('owg-domain-pack-'+pack.pack_id+'-'+reportSnapshot.report.pack.version+'.json',
              JSON.stringify(reportSnapshot,null,2),'application/json');
          };
          const reviewHistory=button('Review saved configurations','secondary');reviewHistory.id='owgDomainPackHistory';reviewHistory.disabled=!installed;
          reviewHistory.onclick=async()=>{
            const t2=turn,h=++historyTurn;reviewHistory.disabled=true;
            historyOut.replaceChildren(el('div','Loading saved configurations…','muted'));
            try{
              const r=await getJSON(base+'/'+pack.pack_id+'/configurations');
              if(!current(t2)||h!==historyTurn)return;
              historyOut.replaceChildren(el('h4','Saved configurations'),el('div',
                'Setup snapshots only. Older entries may be unavailable. These do not reproduce historical events or verified outcomes.','muted'));
              for(const entry of [...r.entries].reverse()){
                const details=el('details');
                details.appendChild(el('summary',(entry.current?'Current':'Earlier')+' '+entry.version+' — '+entry.installed_at));
                details.appendChild(el('div','Definitions: '+entry.definitions_sha256,'muted'));
                if(entry.configuration_available){
                  const rows=[];
                  for(const [kind,roles] of Object.entries(entry.mapping))for(const [role,name] of Object.entries(roles))rows.push([kind==='object_types'?'Object type':'Activity',role,name]);
                  details.append(table(['Kind','Role','Mapped label'],rows),table(['Parameter','Value'],Object.entries(entry.parameters)));
                }else details.appendChild(el('div','Configuration unavailable: this older entry retained only version, date and digest.','muted'));
                historyOut.appendChild(details);
              }
            }catch(error){if(current(t2)&&h===historyTurn)historyOut.replaceChildren(el('div',error.message,'muted'));}
            finally{if(reviewHistory.isConnected)reviewHistory.disabled=!installs.installs.some(i=>i.pack_id===pack.pack_id);}
          };
          const actions=el('div',null,'row');actions.append(install,run,remove,exportReport,reviewHistory);
          const assumptions=el('ul');assumptions.className='muted';pack.assumptions.forEach(a=>assumptions.appendChild(el('li',a)));
          form.replaceChildren(field('Domain pack',chooser),el('h4',pack.title+' '+pack.latest_version),el('div',pack.description,'muted'),table(['Kind','Role','Name in this log'],rows),paramRow,actions,el('div','Report downloads include supplied case/object examples and mapped labels. Save and share them only where authorized.','muted'),el('h4','Assumptions'),assumptions);
          status.textContent=(installed?'Installed: '+installed.version+' on '+String(installed.installed_at).slice(0,10)+'.':'Not installed on this log.')+(installs.names_truncated?' Suggestions show the 1,000 most frequent labels; enter an exact label for others.':'');
          install.onclick=async()=>{
            invalidate();const t2=++turn;install.disabled=true;run.disabled=true;historyTurn++;historyOut.replaceChildren();
            try{
              const parameters={};for(const [k,i] of Object.entries(inputs))parameters[k]=Number(i.value);
              const basis=installs.installs.find(i=>i.pack_id===pack.pack_id);
              if(basis&&!/^[a-f0-9]{64}$/.test(basis.installation_token||''))throw new Error('Installation review is unavailable. Reload setup before saving.');
              const r=await (await post(base+'/'+pack.pack_id,{version:pack.latest_version,mapping:packMapping(pack,choices),parameters,expected_installation:basis?basis.installation_token:null})).json();
              // Keep the completed save's basis even if later inputs invalidated its display.
              installs.installs=[...installs.installs.filter(i=>i.pack_id!==pack.pack_id),r];
              if(!current(t2))return;
              run.disabled=remove.disabled=reviewHistory.disabled=false;
              status.textContent=({installed:'Installed',upgraded:'Upgraded from '+r.previous_version+' to',reconfigured:'Reinstalled'}[r.change])+' '+r.version+'. '+r.metric_ids.length+' case metrics were added to this log’s metric library.'+(r.not_applicable.length?' '+r.not_applicable.length+' definitions do not apply without their optional roles.':'');
            }catch(error){if(current(t2))status.textContent=error.message;}
            finally{if(install.isConnected)install.disabled=false;}
          };
          run.onclick=async()=>{
            invalidate();const t2=++turn;run.disabled=true;out.replaceChildren(el('div','Running…','muted'));
            try{
              const r=await getJSON(base+'/'+pack.pack_id+'/report');
              if(!current(t2))return;
              reportTurn=t2;
              reportSnapshot={format:'openworkgraph.domain-pack-report-snapshot.v1',
                generated_at:new Date().toISOString(),report:r};
              exportReport.disabled=false;
              const v=packReportRows(r);
              out.replaceChildren(el('div',r.pack.title+' '+r.pack.version+' applied through your mapping.','muted'),
                el('div','Saved parameters: '+Object.entries(r.parameters).map(([name,value])=>
                  (pack.parameters.find(p=>p.name===name)?.label||name)+': '+value).join('; '),'muted'),
                el('h4','Case metrics'),table(['Metric','Object type','Cases','Median','90th percentile','With a value'],v.metrics),
                el('h4','Compliance rules'),table(['Object type','Rule','Applies to','Violating','Unknown'],v.compliance),
                ...(v.objectRules.length?[el('h4','Object rules'),table(['Rule','Applies to','Violating'],v.objectRules)]:[]),
                el('h4','Reference object model'),el('div',v.model,'note'),
                ...(v.notApplicable.length?[el('div','Not applicable with this mapping: '+v.notApplicable.join(', ')+'.','muted')]:[]),
                el('div',r.interpretation,'muted'));
            }catch(error){if(current(t2))out.replaceChildren(el('div',error.message,'muted'));}
            finally{if(current(t2))run.disabled=false;}
          };
          remove.onclick=async()=>{
            invalidate();const t2=++turn;remove.disabled=true;run.disabled=true;historyTurn++;historyOut.replaceChildren();
            try{
              const basis=installs.installs.find(i=>i.pack_id===pack.pack_id);
              if(!basis||!/^[a-f0-9]{64}$/.test(basis.installation_token||''))throw new Error('Installation review is unavailable. Reload setup before removing.');
              const r=await (await api(base+'/'+pack.pack_id+'?expected_installation='+encodeURIComponent(basis.installation_token),{method:'DELETE'})).json();
              if(!current(t2))return;
              installs.installs=installs.installs.filter(i=>i.pack_id!==pack.pack_id);
            run.disabled=reviewHistory.disabled=true;out.replaceChildren();status.textContent='Uninstalled '+r.version+'; '+r.metrics_removed+' pack case metrics were removed.';
            }catch(error){if(current(t2)){status.textContent=error.message;remove.disabled=false;}}
          };
        };
        // Switching packs drops any answer still on its way for the previous one.
        chooser.onchange=()=>{turn++;historyTurn++;historyOut.replaceChildren();out.replaceChildren();show();};
        show();
      }catch(error){if(current(t))status.textContent=error.message;}
      finally{if(current(t))setup.disabled=false;}
    };
    return box;
  }

  function objectMapSection(){
    const box=section('Object-centric map');box.id='owgObjectCentricMap';
    const generation=state.generation;
    const current=()=>box.isConnected&&generation===state.generation;
    box.appendChild(el('div','Each object type has its own map: every object (an order, an item, a ticket…) is one lifecycle. Links count objects that share events; they are not verified relationships.','muted'));
    const pick=select('owgEventLogObjectType',[]);
    const showMap=button('Show map','secondary');showMap.id='owgEventLogObjectShow';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Object type',pick),showMap);
    const out=el('div');out.id='owgEventLogObjectMap';
    const shared=el('div');
    const flatOut=el('div');flatOut.id='owgEventLogFlatteningBox';
    const attrOut=el('div');attrOut.id='owgEventLogObjectAttributesBox';
    box.append(row,out,flatOut,attrOut,shared);
    let graph=null,flat=null,attrs=null;
    const renderAttrs=()=>{
      const type=attrs&&(attrs.object_types||[]).find(t=>t.object_type===pick.value);
      if(!type){attrOut.replaceChildren();return;}
      const list=el('ul');list.id='owgEventLogObjectAttributes';attributeChangeSummary(type).forEach(x=>list.appendChild(el('li',x)));
      attrOut.replaceChildren(el('h4','Attributes of '+type.object_type+' over time'),list,
        ...(attrs.stored_value_comparison_complete===false?[el('p','How many events see another value than the stored one is unknown within the analysis limits for this log.','muted')]:[]),
        el('p',attrs.interpretation,'muted'));
    };
    // Filled on its own so late diagnostics never reset an open map selection or case browser.
    const renderFlat=()=>{
      const flatType=flat&&(flat.object_types||[]).find(t=>t.object_type===pick.value);
      if(!flatType){flatOut.replaceChildren();return;}
      const list=el('ul');list.id='owgEventLogFlattening';flatteningSummary(flatType).forEach(x=>list.appendChild(el('li',x)));
      flatOut.replaceChildren(el('h4','Analysing '+flatType.object_type+' as the case'),list,
        ...(flat.divergence_explanation_complete===false?[el('p','Which other objects explain repeated activities is unknown within the analysis limits for this log.','muted')]:[]),
        el('p',flat.interpretation,'muted'));
    };
    const render=()=>{
      if(!graph)return;
      const type=(graph.object_types||[]).find(t=>t.object_type===pick.value);if(!type)return;
      const inspection=caseInspector(type.activities.map(a=>a.activity),{prefix:'owgEventLogObjectCases',caseType:type.object_type});
      inspection.node.querySelector('h3').textContent='Map case evidence · '+type.object_type;
      show(inspection.node,false);
      const selection=el('div');selection.id='owgEventLogObjectSelection';selection.tabIndex=-1;
      const chooseActivity=activity=>{
        const item=type.activities.find(a=>a.activity===activity.label);if(!item)return;
        const browse=button('Browse cases containing '+item.activity,'secondary');browse.onclick=()=>{show(inspection.node,true);inspection.activity(item.activity);};
        selection.replaceChildren(el('h4',item.activity),el('p',item.event_count+' recorded events across '+item.case_count+' '+type.object_type+' objects in the complete log.'),browse);selection.focus();
      };
      const chooseEdge=edge=>{
        const item=type.transitions.find(t=>t.from===edge.from&&t.to===edge.to);if(!item)return;
        const browse=button('Browse cases for adjacent transition','secondary');browse.onclick=()=>{show(inspection.node,true);inspection.transition([item.from,item.to]);};
        selection.replaceChildren(el('h4',item.from+' → '+item.to),el('p',item.transition_count+' recorded adjacent transitions across '+item.case_count+' '+type.object_type+' objects. Median elapsed gap '+duration(item.median_elapsed_gap_seconds)+'. Gaps are not measured labor or verified waiting.'),el('p',graph.ordering,'muted'),browse);selection.focus();
      };
      const parts=[table(['Objects','Events','Median events per object','Starts with','Ends with'],[[Number(type.objects).toLocaleString(),Number(type.events).toLocaleString(),type.median_events_per_object,
        Object.entries(type.start_activities).slice(0,3).map(([a,n])=>a+' ('+n+')').join(', '),Object.entries(type.end_activities).slice(0,3).map(([a,n])=>a+' ('+n+')').join(', ')]])];
      parts.push(el('p','Showing '+type.activities.length+' of '+type.activity_types+' activity types and '+type.transitions.length+' of '+type.transition_types+' transition types. Counts use the complete log for this object type; the map is bounded. A combined route may never occur in one case. '+graph.ordering,'muted'));
      const draw=window.owgProcessGraph&&window.owgProcessGraph.drawObserved;
      if(draw&&type.activities.length){
        try{
          const start=Object.keys(type.start_activities)[0];
          const drawing=draw({activities:type.activities,transitions:type.transitions,cases:start?[{path:[start]}]:[]},chooseActivity,chooseEdge);
          drawing.id='owgEventLogObjectGraph';parts.push(drawing);
        }catch(_){parts.push(el('div','Map unavailable for this object type; transitions are listed below.','muted'));}
      }
      parts.push(selection,table(['From','To','Transitions','Objects','Median gap','Evidence'],type.transitions.slice(0,25).map(t=>{
        const inspect=button('Inspect transition','secondary');inspect.onclick=()=>chooseEdge(t);
        return [t.from,t.to,Number(t.transition_count).toLocaleString(),Number(t.case_count).toLocaleString(),duration(t.median_elapsed_gap_seconds),inspect];
      })));
      const links=(graph.relations||[]).filter(r=>r.from_type===type.object_type);
      if(graph.relations_calculation_complete===false)parts.push(el('p','Inter-object link counts are unknown within the analysis limits for this log. Lifecycle maps and case evidence remain available.','muted'));
      if(links.length)parts.push(table(['Linked to','Share of observed '+type.object_type+' objects','Mean links per linked object','Most linked'],links.map(r=>[r.to_type,pct(r.share_of_from_objects),r.mean_linked_per_object,r.max_linked_per_object])));
      renderFlat();renderAttrs();parts.push(inspection.node);out.replaceChildren(...parts);
    };
    showMap.onclick=render;pick.onchange=render;
    getJSON(logPath('object-map').replace(/\?.*$/,'')).then(async g=>{
      if(!current())return;
      if(!state.objectTypes.length){
        const types=await getJSON(logPath('object-types').replace(/\?.*$/,''));
        if(!current())return;
        state.objectTypes=types.object_types||[];
      }
      graph=g;setOptions(pick,(g.object_types||[]).map(t=>[t.object_type,t.object_type+' ('+Number(t.objects).toLocaleString()+')']));
      if(state.caseType)pick.value=state.caseType;
      const touching=g.activities_touching_several_types||[];
      shared.replaceChildren(el('p','Showing '+g.object_types.length+' of '+g.object_type_count+' object types.','muted'),...(touching.length?[el('h4','Activities shared by several object types'),table(['Activity','Object types','Events'],touching.slice(0,15).map(a=>[a.activity,a.object_types.join(', '),Number(a.events).toLocaleString()]))]:[]),
        ...((g.object_types||[]).length>1||state.objectTypes.length>1||g.log&&(g.log.object_relationships||['ocel','ocelxml'].includes(g.log.source_format))?[objectRuleSection(g),objectModelSection()]:[]));
      render();
    }).catch(error=>{if(current())out.replaceChildren(el('div',error.message,'muted'));});
    getJSON(logPath('object-flattening').replace(/\?.*$/,'')).then(f=>{if(current()){flat=f;renderFlat();}}).catch(()=>{});
    getJSON(logPath('object-attributes').replace(/\?.*$/,'')).then(a=>{if(current()){attrs=a;renderAttrs();}}).catch(()=>{});
    return box;
  }

  function objectRelationshipSection(){
    const box=section('Supplied object relationships');box.id='owgObjectRelationships';box.style.minWidth='0';
    const generation=state.generation,logId=state.logId,current=()=>box.isConnected&&generation===state.generation;
    box.append(el('p','These directed links were supplied in the log. They are separate from shared-event associations, do not merge case histories, and do not establish causality or when a relationship was valid. Counts describe retained assertions, not executions.'));
    const object=input('owgObjectRelationObject','text','All objects'),sourceType=input('owgObjectRelationSourceType','text','All source types'),targetType=input('owgObjectRelationTargetType','text','All target types');
    for(const control of [object,sourceType,targetType]){control.maxLength=200;control.style.maxWidth='100%';}
    const direction=select('owgObjectRelationDirection',[['both','Incoming and outgoing'],['outgoing','Outgoing'],['incoming','Incoming']]);direction.disabled=true;
    const exact=input('owgObjectRelationExactQualifier','checkbox'),qualifier=input('owgObjectRelationQualifier','text','Exact relationship label');qualifier.maxLength=160;qualifier.style.maxWidth='100%';
    const limit=select('owgObjectRelationLimit',[['25','25'],['50','50'],['100','100'],['200','200']]);limit.value='50';
    const showRows=button('Show relationships','secondary');showRows.id='owgObjectRelationRun';
    const previous=button('Previous page','secondary'),next=button('Next page','secondary');previous.id='owgObjectRelationPrevious';next.id='owgObjectRelationNext';previous.disabled=next.disabled=true;
    const controls=el('div',null,'row');controls.style.alignItems='end';controls.append(field('Object ID (optional)',object),field('Direction for selected object',direction),field('Source type (optional)',sourceType),field('Target type (optional)',targetType),field('Match exact relationship label',exact),field('Relationship label (empty is a valid exact label)',qualifier),field('Rows per page',limit),showRows,previous,next);
    const statusLine=el('p');statusLine.id='owgObjectRelationStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const groups=el('details');groups.id='owgObjectRelationGroups';groups.append(el('summary','Supplied relationship groups'));
    const groupRows=el('div');groupRows.style.overflowX='auto';groups.append(groupRows);
    const drawing=el('div');drawing.id='owgObjectRelationDrawing';drawing.style.maxWidth='100%';
    const rowsOut=el('div');rowsOut.id='owgObjectRelationRows';rowsOut.style.cssText='overflow-x:auto;max-width:100%';
    const evidence=el('div');evidence.id='owgObjectRelationEvidence';evidence.setAttribute('aria-live','polite');
    const traceHeading=el('h4','Follow related objects (up to three hops)');
    const traceNote=el('p','Trace links in the retained OCEL relationship graph. This is a bounded navigation view, not a reconstructed case or validated handoff. Select one literal object ID above first. The direction and optional exact qualifier filter above also apply here.','muted');
    const traceHops=select('owgObjectRelationTraceHops',[['1','1 hop'],['2','2 hops'],['3','3 hops']]);traceHops.value='2';
    const traceRun=button('Trace related objects','secondary');traceRun.id='owgObjectRelationTraceRun';traceRun.disabled=true;
    const traceStatus=el('p');traceStatus.id='owgObjectRelationTraceStatus';traceStatus.setAttribute('role','status');traceStatus.setAttribute('aria-live','polite');
    const traceView=el('div');traceView.id='owgObjectRelationTraceResult';traceView.style.cssText='overflow-x:auto;max-width:100%';
    const traceControls=el('div',null,'row');traceControls.style.alignItems='end';traceControls.append(field('Traversal depth',traceHops),traceRun);
    box.append(controls,statusLine,groups,drawing,rowsOut,evidence,
      traceHeading,traceNote,traceControls,traceStatus,traceView);
    let requestVersion=0,nextCursor=null,bookmarks=[null];
    const base='/v1/event-logs/'+encodeURIComponent(logId);
    const literal=value=>value===''?'(unqualified)':value.trim()!==value?JSON.stringify(value):value;
    let traceVersion=0;
    function scopeChanged(){requestVersion++;traceVersion++;nextCursor=null;bookmarks=[null];previous.disabled=next.disabled=true;showRows.disabled=false;direction.disabled=object.value==='';traceRun.disabled=object.value==='';rowsOut.replaceChildren();drawing.replaceChildren();evidence.replaceChildren();traceView.replaceChildren();traceStatus.textContent=object.value?'Select Trace related objects to inspect more than one hop.':'Select an object for multi-hop tracing.';statusLine.textContent='Filters changed. Show relationships to refresh.';}
    for(const control of [object,sourceType,targetType,direction,exact,qualifier,limit]){
      control.addEventListener(control.tagName==='INPUT'&&control.type==='text'?'input':'change',scopeChanged);
    }
    traceHops.addEventListener('change',()=>{
      traceVersion++;traceRun.disabled=object.value==='';traceView.replaceChildren();
      traceStatus.textContent='Traversal depth changed. Trace again for the selected object.';
    });
    async function inspect(selected,turn){
      evidence.replaceChildren(el('h4','Current linked log events for '+literal(selected.object_id)));
      if(!selected.observed_events){evidence.append(el('p','No retained events are linked to this object. Its supplied object relationships remain visible; no lifecycle steps are inferred.'));return;}
      try{
        const response=await(await api(base+'/case-events/query?case_type='+encodeURIComponent(selected.object_type),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:selected.object_id,limit:20})})).json();
        if(!current()||turn!==requestVersion)return;
        evidence.append(table(['Activity','Timestamp','Resource'],response.events.map(event=>[event.activity,event.timestamp,event.resource||'—'])),el('p','Showing '+response.events.length+' linked events. '+(response.ordering||'Timestamp order is not proof of causality.'),'muted'));
      }catch(error){if(current()&&turn===requestVersion)evidence.append(el('p',error.message,'muted'));}
    }
    function choose(id){if(!current())return;object.value=id;direction.value='both';scopeChanged();load().catch(()=>{});}
    function objectButton(id){const node=button(literal(id),'secondary');node.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:16rem';node.setAttribute('aria-label','Inspect object '+id);node.onclick=()=>choose(id);return node;}
    function draw(response){
      drawing.replaceChildren();const selected=response.selected_object;
      if(!selected){drawing.append(el('p','Choose an object in the table to inspect its direct neighbors and linked log events.','muted'));return;}
      const all=[...new Set(response.rows.flatMap(row=>[row.source_id,row.target_id]))].filter(id=>id!==selected.object_id),neighbors=all.slice(0,12);
      const positions=new Map([[selected.object_id,[410,220]]]);neighbors.forEach((id,index)=>{const angle=2*Math.PI*index/Math.max(1,neighbors.length);positions.set(id,[410+300*Math.cos(angle),220+165*Math.sin(angle)]);});
      const shown=response.rows.filter(row=>positions.has(row.source_id)&&positions.has(row.target_id)).slice(0,24);
      const note=el('p','Direct assertions on this page: diagram shows '+shown.length+' of '+response.rows.length+' links and '+neighbors.length+' of '+all.length+' neighbor objects. The table retains every row on this page. Arrows follow supplied direction.','muted');drawing.append(note);
      const ns='http://www.w3.org/2000/svg',svg=(name,attrs={},text)=>{const node=document.createElementNS(ns,name);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,String(value)));if(text!==undefined)node.textContent=text;return node;};
      const graph=svg('svg',{viewBox:'0 0 820 440',role:'group','aria-label':'Direct supplied object relationships for '+selected.object_id});graph.style.cssText='width:100%;height:auto;display:block';
      const markerId='owgO2OArrow'+generation,defs=svg('defs'),marker=svg('marker',{id:markerId,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#44516b'}));defs.append(marker);graph.append(defs);
      shown.forEach((row,index)=>{
        const [sx,sy]=positions.get(row.source_id),[tx,ty]=positions.get(row.target_id),self=row.source_id===row.target_id;
        const distance=Math.hypot(tx-sx,ty-sy)||1,ux=(tx-sx)/distance,uy=(ty-sy)/distance,bend=(index%3-1)*16;
        const path=self?'M '+(sx+35)+' '+(sy-22)+' C '+(sx+100)+' '+(sy-95)+' '+(sx-100)+' '+(sy-95)+' '+(sx-35)+' '+(sy-22):'M '+(sx+ux*65)+' '+(sy+uy*24)+' Q '+((sx+tx)/2-uy*bend)+' '+((sy+ty)/2+ux*bend)+' '+(tx-ux*65)+' '+(ty-uy*24);
        const line=svg('path',{d:path,fill:'none',stroke:'#44516b','stroke-width':1.5,'marker-end':'url(#'+markerId+')','aria-hidden':'true'});line.append(svg('title',{},row.source_id+' → '+row.target_id+' · '+literal(row.qualifier)));graph.append(line);
        const text=svg('text',{x:self?sx:(sx+tx)/2-uy*bend,y:self?sy-75:(sy+ty)/2+ux*bend-5,'text-anchor':'middle','font-size':11,fill:'#26344d','aria-hidden':'true'},literal(row.qualifier).slice(0,30));graph.append(text);
      });
      for(const [id,[x,y]] of positions){
        const group=svg('g',{role:'button',tabindex:0,'aria-label':'Inspect object '+id,'data-owg-object':id});group.style.cursor='pointer';
        const rectangle=svg('rect',{x:x-70,y:y-22,width:140,height:44,rx:7,fill:id===selected.object_id?'#e5edff':'#f3f6fb',stroke:'#44516b','stroke-width':1.5});
        group.append(rectangle,svg('title',{},id),svg('text',{x,y:y+4,'text-anchor':'middle','font-size':12,fill:'#172841'},literal(id).length>23?literal(id).slice(0,22)+'…':literal(id)));
        group.onclick=()=>choose(id);group.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(id);}};
        group.onfocus=()=>rectangle.setAttribute('stroke-width','4');group.onblur=()=>rectangle.setAttribute('stroke-width','1.5');graph.append(group);
      }
      drawing.append(graph);
    }
    async function load(cursor=null){
      const turn=++requestVersion;showRows.disabled=true;previous.disabled=next.disabled=true;
      const spec={direction:object.value===''?'both':direction.value,limit:Number(limit.value)};
      for(const [key,control] of [['object_id',object],['source_type',sourceType],['target_type',targetType]])if(control.value!=='')spec[key]=control.value;
      if(exact.checked)spec.qualifier=qualifier.value;if(cursor)spec.cursor=cursor;
      try{
        const response=await(await api(base+'/object-relationships/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(!current()||turn!==requestVersion)return;
        nextCursor=response.next_cursor;next.disabled=!nextCursor;previous.disabled=bookmarks.length<=1;
        statusLine.textContent='Page '+bookmarks.length+': '+response.rows.length+' of '+Number(response.matching_relationships).toLocaleString()+' matching retained assertions. Static supplied links; no case joins or causal order inferred.';
        rowsOut.replaceChildren(table(['Source object','Source type','Relationship label','Target object','Target type','Source log events','Target log events'],response.rows.map(row=>[objectButton(row.source_id),row.source_type,literal(row.qualifier),objectButton(row.target_id),row.target_type,row.source_observed_events,row.target_observed_events])));
        draw(response);evidence.replaceChildren();if(response.selected_object)await inspect(response.selected_object,turn);
      }catch(error){if(current()&&turn===requestVersion){rowsOut.replaceChildren();drawing.replaceChildren();evidence.replaceChildren();nextCursor=null;bookmarks=[null];statusLine.textContent=error.message;}}
      finally{if(current()&&turn===requestVersion)showRows.disabled=false;}
    }
    traceRun.onclick=async()=>{
      if(!object.value||!current())return;
      const turn=++traceVersion;
      traceRun.disabled=true;traceView.replaceChildren();
      traceStatus.textContent='Tracing bounded retained relationships locally…';
      const spec={object_id:object.value,direction:direction.value,
        max_hops:Number(traceHops.value),max_nodes:60,max_edges:100};
      if(exact.checked)spec.qualifier=qualifier.value;
      try{
        const response=await(await api(base+'/object-relationships/trace',{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(!current()||turn!==traceVersion)return;
        traceStatus.textContent=Number(response.shown_nodes)+' objects and '+
          Number(response.shown_edges)+' original relationship assertions through '+
          Number(response.hops_visited)+' hop(s)'+
          (response.truncated?' — limited: '+String(response.truncation_reason).replaceAll('_',' '):' — selected depth fully traversed within the retained graph')+
          '. This does not verify case membership or causality.';
        traceView.replaceChildren(
          el('p','Objects: source IDs and types as imported, with the shortest displayed hop distance.','muted'),
          table(['Distance','Object ID','Object type'],(response.nodes||[]).map(n=>[
            n.hop,objectButton(n.object_id),n.object_type
          ])),
          el('p','Links: their original direction and literal qualifier are preserved; no new links or event connections are inferred.','muted'),
          table(['First seen hop','Source','Relationship label','Target'],(response.edges||[]).map(e=>[
            e.encountered_at_hop,objectButton(e.source_id),literal(e.qualifier),objectButton(e.target_id)
          ]))
        );
      }catch(error){
        if(current()&&turn===traceVersion){traceView.replaceChildren();traceStatus.textContent=error.message||'Relationship trace unavailable.';}
      }finally{
        if(current()&&turn===traceVersion)traceRun.disabled=object.value==='';
      }
    };
    showRows.onclick=()=>{bookmarks=[null];load().catch(()=>{});};
    next.onclick=()=>{if(nextCursor){bookmarks.push(nextCursor);load(nextCursor).catch(()=>{});}};
    previous.onclick=()=>{if(bookmarks.length>1){bookmarks.pop();load(bookmarks[bookmarks.length-1]).catch(()=>{});}};
    let groupVersion=0,groupRevision=null,groupOffset=0;
    async function loadGroups(offset=0){
      const turn=++groupVersion;
      groupRows.replaceChildren(el('p','Loading relationship groups…','muted'));
      try{
        const query=offset?'?offset='+offset+'&revision='+groupRevision:'';
        const profile=await getJSON(base+'/object-relationships'+query);
        if(!current()||turn!==groupVersion)return;
        groupRevision=profile.revision;groupOffset=offset;
        const previousGroups=button('Previous groups','secondary'),nextGroups=button('Next groups','secondary');
        previousGroups.id='owgObjectRelationGroupsPrevious';nextGroups.id='owgObjectRelationGroupsNext';
        previousGroups.disabled=offset===0;nextGroups.disabled=profile.groups_next_offset==null;
        previousGroups.onclick=()=>loadGroups(Math.max(0,groupOffset-200));
        nextGroups.onclick=()=>loadGroups(profile.groups_next_offset);
        groupRows.replaceChildren(el('p','Retained assertions: '+Number(profile.retained_relationships).toLocaleString()+'. Showing '+(profile.groups.length?offset+1:0)+'–'+(offset+profile.groups.length)+' of '+profile.groups_total+' relationship groups.','muted'),table(['Source type','Relationship label','Target type','Assertions','Inspect'],profile.groups.map(group=>{
          const inspect=button('Inspect group','secondary');inspect.onclick=()=>{object.value='';sourceType.value=group.source_type;targetType.value=group.target_type;exact.checked=true;qualifier.value=group.qualifier;scopeChanged();load().catch(()=>{});};
          return [group.source_type,literal(group.qualifier),group.target_type,group.relationships,inspect];
        })),previousGroups,nextGroups);
        if(requestVersion===0)load().catch(()=>{});
      }catch(error){
        if(!current()||turn!==groupVersion)return;
        const restart=button('Reload relationship groups','secondary');restart.onclick=()=>loadGroups();
        groupRows.replaceChildren(el('p',error.message,'muted'),restart);
      }
    }
    loadGroups();
    return box;
  }

  function workInProgressSection(inspection){
    const box=section('Work in progress over time');box.id='owgEventLogWip';
    box.appendChild(el('div','Cases arriving, completing and still open per day or week. A case completes at its last event when that is an end activity (or, with none given, at its last event).','muted'));
    const ends=input('owgWipEnds','text','Close, Cancel');
    const bucket=select('owgWipBucket',[['day','Per day'],['week','Per week']]);
    const run=button('Show work in progress','secondary');run.id='owgWipRun';
    const out=el('div');out.id='owgWipResults';out.setAttribute('role','status');
    const review=el('div');review.id='owgWipCaseReview';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities that end a case',ends),field('Buckets',bucket),run);
    box.append(row,out,review);
    const generation=state.generation,endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/work-in-progress';
    const groups={arrivals:'Arrivals',completions:'Completions',open_at_end:'Open at end'};
    const number=n=>Number(n).toLocaleString();
    let reviewTurn=0;
    function inspect(settings,bucketStart,group){
      const turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4',groups[group]+' for '+(settings.bucket==='week'?'week of ':'')+bucketStart);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      const dateTime=value=>value?value.replace('T',' ').replace('Z',' UTC'):'—';
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        const spec={...settings,bucket_start:bucketStart,group};
        if(cursors[page]!==null)spec.after=cursors[page];
        try{
          const r=await (await api(endpoint+'/cases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
          index=page;following=r.next_after;
          status.textContent=number(r.total)+(r.total===1?' case.':' cases.')+' Showing '+number(r.cases.length)+' on this page.';
          rows.replaceChildren(r.cases.length?table(['Case','Started','Completed by bucket end','Evidence'],r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,dateTime(c.started_at),dateTime(c.completed_at),open];
          })):el('div','No cases on this page.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    function countCell(spec,start,group,count){
      if(!count)return '0';
      const details=button(number(count),'secondary');
      const label=group==='open_at_end'?(count===1?'case open at end':'cases open at end'):groups[group].toLowerCase();
      details.setAttribute('aria-label','Inspect '+number(count)+' '+label+' for '+(spec.bucket==='week'?'week of ':'')+start);
      details.onclick=()=>inspect(spec,start,group);
      return details;
    }
    run.onclick=async()=>{
      ++reviewTurn;review.replaceChildren();run.disabled=true;
      out.replaceChildren(el('div','Calculating work in progress…','muted'));
      try{
        const spec={bucket:bucket.value,end_activities:ends.value.split(',').map(x=>x.trim()).filter(Boolean)};
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await (await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const b=r.buckets;
        if(!b.length){out.replaceChildren(el('div','No cases match this selection.','muted'));return;}
        const ns='http://www.w3.org/2000/svg',W=600,H=160,max=Math.max(1,...b.map(x=>Math.max(x.open_at_end,x.arrivals,x.completions)));
        const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+W+' '+(H+20));svg.setAttribute('role','img');
        svg.setAttribute('aria-label','Open cases per '+spec.bucket+': from '+(b[0]?b[0].open_at_end:0)+' to '+(b.length?b[b.length-1].open_at_end:0)+', peak '+Math.max(0,...b.map(x=>x.open_at_end))+'. The table below lists every '+spec.bucket+'.');
        svg.style.cssText='width:100%;max-width:640px;height:auto;display:block';
        const x=i=>b.length<2?W/2:i*(W-10)/(b.length-1)+5,y=v=>H-v/max*(H-10);
        [['open_at_end','#2563eb'],['arrivals','#16a34a'],['completions','#9333ea']].forEach(([k,c])=>{const p=document.createElementNS(ns,'polyline');
          p.setAttribute('points',b.map((v,i)=>x(i).toFixed(1)+','+y(v[k]).toFixed(1)).join(' '));p.setAttribute('fill','none');p.setAttribute('stroke',c);p.setAttribute('stroke-width',k==='open_at_end'?'2.5':'1.5');svg.appendChild(p);});
        const legend=el('div','Blue: open at the end of each '+spec.bucket+' · green: arrivals · purple: completions.','muted');
        const law=r.littles_law;
        out.replaceChildren(svg,legend,
          el('div',Number(r.cases).toLocaleString()+' cases; '+Number(r.never_completed_cases).toLocaleString()+' never reached an end activity. Average open: '+law.average_open_cases+'; arrival rate × mean cycle time: '+(law.arrival_rate_times_cycle_time===null?'—':law.arrival_rate_times_cycle_time)+(law.ratio===null?'':' (ratio '+law.ratio+')')+'. '+law.note,'muted'),
          table(['Starting',...Object.values(groups)],b.map(v=>{
            const start=v.start.slice(0,10);
            return [start,...Object.keys(groups).map(group=>countCell(spec,start,group,v[group]))];
          })),
          el('div',r.interpretation,'muted'));
      }catch(error){if(generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function kpiSection(activityNames){
    const box=section('KPIs');
    const sla=input('owgEventLogSla','number','e.g. 48');sla.min='0';sla.step='any';
    const bots=input('owgEventLogBots','text','bot, system');
    const run=button('Calculate KPIs','secondary');run.id='owgEventLogKpiRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('SLA (hours)',sla),field('Automation resources (comma-separated or JSON array for exact labels)',bots),run);
    const filter=cohortFields('owgEventLogKpiCohort','Only cases matching (optional)',activityNames);
    let last=null;
    const exports=exportButtons('owgEventLogKpi',()=>last,k=>[
      ['metric','value'],['cases',k.cases],['throughput_p50_seconds',(k.throughput_seconds||{}).p50],['throughput_p90_seconds',(k.throughput_seconds||{}).p90],
      ['throughput_p95_seconds',(k.throughput_seconds||{}).p95],['rework_rate',k.rework_rate],['sla_breach_rate',k.sla?k.sla.breach_rate:''],
      ['automation_rate',k.automation?k.automation.automation_rate:''],[],['week_starting','cases','median_throughput_seconds'],
      ...(k.completed_per_week||[]).map(w=>[w.week_starting,w.cases,w.median_throughput_seconds])]);
    const out=el('div');box.append(row,filter.node,out,exports.node);
    run.onclick=async()=>{
      try{
        const params={...filter.read()};delete params.label;const hours=Number(sla.value);
        if(sla.value&&hours>0)params.sla_seconds=String(Math.round(hours*3600));
        if(bots.value.trim()){
          if(bots.value.trimStart().startsWith('[')){
            const resources=JSON.parse(bots.value);
            if(!Array.isArray(resources)||!resources.every(r=>typeof r==='string'))throw new Error('Resource JSON must be an array of strings.');
            params.automation_resource=resources;
          }else params.automation_resources=bots.value;
        }
        const k=await getJSON(logPath('kpis',params));const t=k.throughput_seconds||{};last=k;exports.enable(true);
        out.replaceChildren(table(['Cases','Throughput p50','p90','p95','Rework rate','SLA breach rate','Automation rate'],[[Number(k.cases).toLocaleString(),duration(t.p50),duration(t.p90),duration(t.p95),pct(k.rework_rate),k.sla?pct(k.sla.breach_rate):'—',k.automation?pct(k.automation.automation_rate):'—']]),
          el('div','Rework means a case repeats an activity. Automation rate counts events by the resources you listed.','muted'));
      }catch(error){last=null;exports.enable(false);out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  // Exact, non-overlapping bands of observed first-to-last event duration.
  // Every activity and every resource of the selection, as spreadsheet-safe CSV for review elsewhere.
  function statisticsExportSection(){
    const box=section('Activity and resource statistics');box.id='owgStatisticsExport';
    box.appendChild(el('div','Download every activity or resource of the selection with its frequency, cases, repeats and waiting time. The case filter, grouped and hidden activities apply.','muted'));
    const status=el('div',null,'muted');status.id='owgStatisticsExportStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const row=el('div',null,'row');
    for(const [kind,label] of [['activities','Download activity statistics'],['resources','Download resource statistics']]){
      const b=button(label,'secondary');b.id='owgStatisticsExport'+kind[0].toUpperCase()+kind.slice(1);
      b.onclick=async()=>{
        b.disabled=true;
        try{const response=await api(logPath(kind+'.csv',{}));const blob=await response.blob();
          downloadBlob(blob,(state.logId||'log')+'-'+kind+'.csv');
          const info=response.headers.get('X-OWG-Rows')||'';
          status.textContent='Downloaded '+(/written=(\d+)/.exec(info)||[,'the'])[1]+' '+kind+(/truncated=true/.test(info)?' (the most frequent; the list was cut)':'')+'.';}
        catch(error){status.textContent=error.message;}
        finally{b.disabled=false;}};
      row.appendChild(b);
    }
    box.append(row,status);
    return box;
  }

  function reworkSection(){
    const box=section('Rework and loops');box.id='owgEventLogRework';
    box.appendChild(el('div','Activities that happen again in the same case, the loops they form, and how cases with rework compare with cases without.','muted'));
    const run=button('Find rework','secondary');run.id='owgReworkRun';
    const out=el('div');out.id='owgReworkResults';out.setAttribute('role','status');
    box.append(run,out);
    const generation=state.generation;
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const spec={};if(state.caseType)spec.case_type=state.caseType;
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/rework',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const k=r.loop_kinds,c=r.comparison;
        const parts=[el('div',pct(r.rework_rate)+' of '+Number(r.cases).toLocaleString()+' cases repeat at least one activity: '+k.self_loop+' with an immediate repeat, '+k.short_loop+' with a short loop (one step in between), '+k.longer_loop+' with a longer return.')];
        if(r.activities.length)parts.push(table(['Activity','Cases repeating it','Share of its cases','Extra occurrences'],r.activities.map(a=>[a.activity,Number(a.cases_repeating).toLocaleString(),pct(a.repeat_share_of_cases_with_activity),Number(a.extra_occurrences).toLocaleString()])));
        if(r.loop_patterns.length)parts.push(el('h4','Most common loops'),table(['Loop','Cases'],r.loop_patterns.map(p=>[p.pattern.join(' → '),Number(p.cases).toLocaleString()])));
        if(c)parts.push(el('div','Median throughput '+duration(c.median_throughput_with_rework_seconds)+' with rework vs '+duration(c.median_throughput_without_rework_seconds)+' without'+(c.median_difference_ci95_seconds?' (difference 95% interval '+duration(c.median_difference_ci95_seconds[0])+' to '+duration(c.median_difference_ci95_seconds[1])+')':'')+'; '+c.mean_events_with_rework+' vs '+c.mean_events_without_rework+' events per case.'));
        parts.push(el('div',r.interpretation,'muted'));out.replaceChildren(...parts);
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function durationDistributionSection(inspection){
    const box=section('Case duration distribution');
    const bounds=input('owgDurationBounds','text','1, 4, 24, 72');bounds.value='1, 4, 24, 72';bounds.maxLength=110;
    const unit=select('owgDurationUnit',[['1','seconds'],['60','minutes'],['3600','hours'],['86400','days']]);unit.value='3600';
    const run=button('Analyse duration distribution','secondary');run.id='owgDurationRun';
    const controls=el('div',null,'row');controls.style.alignItems='end';
    controls.append(field('Thresholds, smallest to largest',bounds),field('Threshold unit',unit),run);
    const out=el('div');out.id='owgDurationResults';out.setAttribute('role','status');
    box.append(el('p','Count cases by time between their first and last recorded events. Enter up to 10 whole-number thresholds. This is observed elapsed time, not work time or proof of completion.','muted'),controls,out);
    let version=0;
    function invalidate(){version++;run.disabled=false;out.replaceChildren(el('div','Thresholds changed. Analyse again to refresh these results.','muted'));}
    bounds.oninput=invalidate;unit.onchange=invalidate;
    function textFor(seconds){
      if(seconds%86400===0)return (seconds/86400)+' '+(seconds===86400?'day':'days');
      if(seconds%3600===0)return (seconds/3600)+' '+(seconds===3600?'hour':'hours');
      if(seconds%60===0)return (seconds/60)+' '+(seconds===60?'minute':'minutes');
      return seconds+' '+(seconds===1?'second':'seconds');
    }
    function label(b){
      if(b.upper_seconds===null)return textFor(b.lower_seconds)+' or more';
      if(b.lower_seconds===0)return 'Less than '+textFor(b.upper_seconds);
      return textFor(b.lower_seconds)+' to less than '+textFor(b.upper_seconds);
    }
    run.onclick=async()=>{
      const generation=state.generation,ticket=++version,cohort=globalCohort();run.disabled=true;
      const current=()=>generation===state.generation&&ticket===version&&box.isConnected;
      out.replaceChildren(el('div','Analysing duration distribution…','muted'));
      try{
        const parts=bounds.value.split(',').map(x=>x.trim()),multiplier=Number(unit.value);
        if(!parts.length||parts.length>10||parts.some(x=>!/^[0-9]{1,10}$/.test(x)))throw new Error('Enter 1–10 whole-number thresholds separated by commas.');
        const cutoffs=parts.map(x=>Number(x)*multiplier);
        if(cutoffs.some((x,i)=>!Number.isSafeInteger(x)||x<1||x>3155760000||(i>0&&x<=cutoffs[i-1])))throw new Error('Thresholds must be positive, strictly increasing, and at most 100 years.');
        const r=await getJSON(logPath('duration-distribution',{cutoffs_seconds:cutoffs.join(',')}));
        if(!current())return;
        const exports=exportButtons('owgDurationDistribution',()=>r,()=>[
          ['lower_seconds_inclusive','upper_seconds_exclusive','cases','share'],
          ...r.bands.map(b=>[b.lower_seconds,b.upper_seconds,b.cases,b.share])]);exports.enable(true);
        const rows=r.bands.map(b=>{
          const share=el('div',b.share===null?'—':pct(b.share));
          if(b.share!==null){const bar=document.createElement('progress');bar.max=1;bar.value=b.share;bar.style.cssText='display:block;width:100%;max-width:200px';bar.setAttribute('aria-label',label(b)+': '+pct(b.share)+' of selected cases');share.appendChild(bar);}
          const title=label(b),actions=el('div',null,'row');
          const inspect=button('Inspect cases','secondary'),filter=button('Use band as filter','secondary');
          inspect.setAttribute('aria-label','Inspect cases with duration '+title);
          filter.setAttribute('aria-label','Use duration band '+title+' as filter');
          inspect.disabled=filter.disabled=b.cases===0;
          if(b.cases){
            const duration={min:b.lower_seconds};
            if(b.upper_seconds!==null)duration.max=b.upper_seconds;
            const selection=mergeActiveCohort({duration},cohort);
            inspect.onclick=()=>{if(current())inspection.cohort(selection,'Observed duration: '+title+'.');};
            filter.onclick=async()=>{
              if(!current())return;
              state.attr={...state.attr,duration:selection.duration};
              const refresh=analyse(),appliedGeneration=state.generation;
              try{
                await refresh;
                if(appliedGeneration===state.generation)document.getElementById('owgCaseFilter')?.scrollIntoView({block:'nearest'});
              }catch(error){if(appliedGeneration===state.generation)status(error.message);}
            };
          }
          actions.append(inspect,filter);
          return [title,Number(b.cases).toLocaleString(),share,actions];
        });
        const scope=r.cases_before_filter==null?'':(' Filter applied: '+Number(r.cases).toLocaleString()+' of '+Number(r.cases_before_filter).toLocaleString()+' cases.');
        out.replaceChildren(el('p',Number(r.cases).toLocaleString()+' selected cases with retained events; '+Number(r.single_event_cases).toLocaleString()+' contain only one retained event.'+scope,'muted'),
          table(['Observed duration','Cases','Share of selected cases','Cases and filters'],rows),el('p',r.interpretation,'muted'),exports.node);
      }catch(error){if(current())out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  function timePatternSection(activityNames,inspection){
    const box=section('When work happens');box.id='owgEventLogTimePatterns';
    box.appendChild(el('div','Events and new cases by weekday and hour in your time zone, and the share of events outside working hours. Recorded time is not proof of when people worked.','muted'));
    const zone=input('owgPatternZone','text');zone.maxLength=64;
    try{zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch(_){zone.value='UTC';}
    const act=select('owgPatternActivity',[['','All activities'],...activityNames.slice(0,500).map(a=>[a,a])]);
    const startH=input('owgPatternStart','number');startH.min='0';startH.max='23';startH.value='8';
    const endH=input('owgPatternEnd','number');endH.min='1';endH.max='24';endH.value='18';
    const workingDays=document.createElement('fieldset');
    workingDays.appendChild(el('legend','Working days'));
    const days=el('div',null,'row');workingDays.appendChild(days);
    const weekdays=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const dayChecks=weekdays.map((weekday,i)=>{
      const check=input('owgPatternDay'+(i+1),'checkbox');check.checked=i<5;
      const label=el('label',' '+weekday);label.prepend(check);days.appendChild(label);
      return check;
    });
    const mode=select('owgPatternMode',[['events','Recorded events'],['case_arrivals','Case arrivals']]);
    const run=button('Show when work happens','secondary');run.id='owgPatternRun';
    const out=el('div');out.id='owgPatternResults';out.setAttribute('role','status');
    const grid=el('div');grid.id='owgPatternDisplay';
    const review=el('div');review.id='owgPatternCaseReview';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Time zone',zone),field('Activity',act),field('Working hours from',startH),field('to',endH),run);
    const gridControl=field('Show in the grid',mode);gridControl.hidden=true;
    box.append(row,workingDays,out,gridControl,grid,review);
    const generation=state.generation,endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/time-patterns';
    const number=n=>Number(n).toLocaleString();
    const hourText=h=>String(h).padStart(2,'0')+':00';
    const dateTime=value=>value.replace('T',' ').replace('Z',' UTC');
    let reviewTurn=0,report=null;
    const clearReview=()=>{++reviewTurn;review.replaceChildren();};
    const request=async(suffix,spec)=>(await api(endpoint+suffix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    function inspect(spec,group,label,cell={}){
      const turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4',label+' in '+spec.time_zone);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      const arrivals=group==='case_arrivals';
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        try{
          const r=await request('/cases',{...spec,group,...cell,...(cursors[page]!==null?{after:cursors[page]}:{})});
          if(!current())return;
          index=page;following=r.next_after;
          status.textContent=(arrivals?'':number(r.events)+(r.events===1?' event in ':' events in '))+
            number(r.total)+(r.total===1?' case.':' cases.')+' Showing '+number(r.cases.length)+' on this page.';
          const headers=arrivals?['Case','Arrival','Evidence']:['Case','Matching events','First matching event','Last matching event','Evidence'];
          rows.replaceChildren(r.cases.length?table(headers,r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return arrivals?[c.case_id,dateTime(c.first_at),open]:[c.case_id,number(c.matches),dateTime(c.first_at),dateTime(c.last_at),open];
          })):el('div','No matching cases.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    function drawGrid(){
      clearReview();
      if(!report)return;
      const {r,spec}=report,group=mode.value,counts=r[group];
      const noun=group==='events'?'events':'case arrivals';
      const max=Math.max(1,...counts.flat());
      const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('tr');t.id='owgPatternGrid';
      wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Hourly grid for '+noun);
      const corner=el('th','Weekday');corner.scope='col';head.appendChild(corner);
      for(let h=0;h<24;h++){const th=el('th',String(h).padStart(2,'0'));th.scope='col';head.appendChild(th);}
      const thead=el('thead');thead.appendChild(head);const body=el('tbody');
      counts.forEach((rowCounts,d)=>{
        const tr=el('tr'),th=el('th',r.weekdays[d].slice(0,3));th.scope='row';tr.appendChild(th);
        rowCounts.forEach((n,h)=>{
          const td=el('td');td.style.textAlign='center';
          if(n){
            td.style.background='rgba(37,99,235,'+(0.12+0.6*n/max).toFixed(3)+')';
            const location=r.weekdays[d]+' '+hourText(h);
            const matching=group==='events'?(n===1?'event':'events'):(n===1?'case arrival':'case arrivals');
            const open=button(number(n),'secondary');
            open.setAttribute('aria-label','Inspect '+number(n)+' '+matching+' for '+location);
            open.onclick=()=>inspect(spec,group,location+' '+noun,{weekday:d+1,hour:h});
            td.appendChild(open);
          }
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
      t.append(thead,body);wrap.appendChild(t);
      const busy=r.busiest.map(b=>b.weekday+' '+hourText(b.hour)+' ('+b.events+')').join(', ');
      grid.replaceChildren(wrap,...(group==='events'?[el('div',busy?'Busiest: '+busy+'.':'No events.','muted')]:[]));
    }
    mode.onchange=drawGrid;
    run.onclick=async()=>{
      run.disabled=true;report=null;grid.replaceChildren();gridControl.hidden=true;clearReview();
      out.replaceChildren(el('div','Calculating time patterns…','muted'));
      try{
        const spec={time_zone:zone.value.trim()||'UTC',work_start_hour:Number(startH.value),work_end_hour:Number(endH.value),
          work_days:dayChecks.flatMap((check,i)=>check.checked?[i+1]:[])};
        if(act.value)spec.activity=act.value;if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await request('',spec);
        if(generation!==state.generation)return;
        report={r,spec};gridControl.hidden=false;drawGrid();
        const outside=button('Inspect outside-hours events','secondary');outside.onclick=()=>inspect(spec,'outside_working_hours','Outside-hours events');
        out.replaceChildren(el('div',number(r.total_events)+' events and '+number(r.total_case_arrivals)+' new cases in '+r.settings.time_zone+'. '+(r.outside_working_hours_share===null?'':pct(r.outside_working_hours_share)+' of events fell outside '+hourText(r.settings.work_start_hour)+'–'+hourText(r.settings.work_end_hour)+' on working days.')),
          ...(r.outside_working_hours_events?[outside]:[]),el('div',r.interpretation,'muted'));
      }catch(error){if(generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function benchmarkSection(activityNames){
    const box=section('Compare cohorts');
    box.appendChild(el('div','Compare two groups of cases, for example two periods, teams or case types. Differences describe the cohorts; they do not say why they differ.','muted'));
    const first=cohortFields('owgEventLogCohortA','First cohort',activityNames),second=cohortFields('owgEventLogCohortB','Second cohort',activityNames);
    const sla=input('owgEventLogBenchSla','number','optional');sla.min='0';sla.step='any';
    const run=button('Compare','secondary');run.id='owgEventLogBenchRun';
    const row=el('div',null,'row');row.style.cssText='align-items:end;margin-top:8px';const mapRun=button('Compare maps','secondary');mapRun.id='owgMapCompareRun';
    row.append(field('SLA (hours)',sla),run,mapRun);
    let last=null;
    const ci=d=>(d.difference>0?'+':'')+(d.difference*100).toFixed(1)+' pts ['+(d.ci95[0]*100).toFixed(1)+', '+(d.ci95[1]*100).toFixed(1)+']';
    const exports=exportButtons('owgEventLogBenchmark',()=>last,r=>[
      ['activity','share_in_first','share_in_second','difference','ci95_low','ci95_high'],
      ...r.activity_presence.map(a=>[a.activity,a.share_in_first,a.share_in_second,a.difference,a.ci95[0],a.ci95[1]])]);
    const out=el('div');box.append(first.node,second.node,row,out,exports.node);
    const mapOut=el('div');mapOut.id='owgMapCompareResults';mapOut.setAttribute('role','status');box.appendChild(mapOut);
    mapRun.onclick=async()=>{
      const generation=state.generation;
      try{
        mapRun.disabled=true;mapOut.replaceChildren(el('div','Comparing maps…','muted'));
        const cohortsSpec=[first.read(),second.read()];
        const r=await (await api(logPath('map-comparison'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cohorts:cohortsSpec})})).json();
        if(generation!==state.generation)return;
        const names=[cohortsSpec[0].label||'First',cohortsSpec[1].label||'Second'];
        const maps=el('div');maps.style.cssText='display:flex;flex-wrap:wrap;gap:12px';
        r.maps.forEach((m,i)=>{
          const pane=el('div');pane.style.cssText='flex:1 1 300px;min-width:0';
          pane.appendChild(el('h4',names[i]+' · '+Number(m.cases).toLocaleString()+' cases'));
          const draw=window.owgProcessGraph&&window.owgProcessGraph.drawObserved;
          if(draw&&m.activities.length){try{const start=Object.keys(m.start_activities)[0];pane.appendChild(draw({activities:m.activities,transitions:m.transitions,cases:start?[{path:[start]}]:[]},()=>{},()=>{}));}
            catch(_){pane.appendChild(el('div','Map unavailable for this cohort; the paths are listed below.','muted'));}}
          maps.appendChild(pane);
        });
        const share=p=>pct(p.case_share_in_first)+' → '+pct(p.case_share_in_second);
        const ci=p=>(p.case_share_difference>0?'+':'')+(p.case_share_difference*100).toFixed(1)+' pts ['+(p.ci95[0]*100).toFixed(1)+', '+(p.ci95[1]*100).toFixed(1)+']'+(p.only_in?' · only in '+names[p.only_in==='first'?0:1]:'');
        const gap=p=>p.median_gap_difference?((p.median_gap_difference.seconds>=0?'+':'−')+duration(Math.abs(p.median_gap_difference.seconds))+' ['+duration(p.median_gap_difference.ci95[0])+', '+duration(p.median_gap_difference.ci95[1])+']'):'';
        mapOut.replaceChildren(maps,
          el('div',Number(r.paths_only_in_second).toLocaleString()+' paths appear only in '+names[1]+' and '+Number(r.paths_only_in_first).toLocaleString()+' only in '+names[0]+', of '+Number(r.paths_total).toLocaleString()+'.','muted'),
          table(['Path','Cases with path','Difference (95% CI)','Median gap','Gap difference (95% CI)'],r.paths.slice(0,25).map(p=>[p.from+' → '+p.to,share(p),ci(p),duration(p.median_gap_seconds_first)+' → '+duration(p.median_gap_seconds_second),gap(p)])),
          el('div',(r.overlapping_cases?Number(r.overlapping_cases).toLocaleString()+' cases are in both cohorts; ':'')+r.note+'.','muted'));
      }catch(error){if(generation===state.generation)mapOut.replaceChildren(el('div',error.message,'muted'));}
      finally{mapRun.disabled=false;}
    };
    run.onclick=async()=>{
      try{
        const spec={cohorts:[first.read(),second.read()]};const hours=Number(sla.value);
        if(sla.value&&hours>0)spec.sla_seconds=Math.round(hours*3600);
        const r=await (await api(logPath('benchmark'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        last=r;exports.enable(true);
        const name=(c,i)=>c.definition.label||(i?'Second':'First');
        const diff=r.differences_second_minus_first,m=diff.median_throughput_seconds;
        out.replaceChildren(
          table(['Cohort','Cases','Median time','90th percentile','Rework rate','SLA breach rate'],r.cohorts.map((c,i)=>[name(c,i),Number(c.cases).toLocaleString(),duration((c.throughput_seconds||{}).p50),duration((c.throughput_seconds||{}).p90),pct(c.rework_rate),c.sla_breach_rate===undefined?'—':pct(c.sla_breach_rate)])),
          table(['Difference (second − first)','Value (95% CI)'],[
            ['Median time',(m.difference>=0?'+':'−')+duration(Math.abs(m.difference))+' ['+duration(m.ci95[0])+', '+duration(m.ci95[1])+']'],
            ['Rework rate',ci(diff.rework_rate)],...(diff.sla_breach_rate?[['SLA breach rate',ci(diff.sla_breach_rate)]]:[])]),
          table(['Activity','Share of first','Share of second','Difference (95% CI)'],r.activity_presence.slice(0,10).map(a=>[a.activity,pct(a.share_in_first),pct(a.share_in_second),ci(a)])),
          table(['Variant','Share of first','Share of second'],r.variants.slice(0,10).map(v=>[v.path.join(' → '),pct(v.share_in_first),pct(v.share_in_second)])),
          el('div',(r.overlapping_cases?Number(r.overlapping_cases).toLocaleString()+' cases are in both cohorts. ':'')+r.note+'.','muted'));
      }catch(error){last=null;exports.enable(false);out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  function graphMetricSummary(result){
    const fitness=result.structural_fitness,precision=result.visible_prefix_precision;
    if(!fitness||!precision)return [];
    const reasons={precision_work_budget:'Precision replay reached its work limit.',no_exact_matching_cases:'No exact matching cases are available for precision.',prefix_replay_mismatch:'Precision replay did not establish the complete prefixes.',no_enabled_visible_activities:'There are no visible enabled activities to measure.'};
    const summary=table(['Structural fitness','Full-population fitness range','Analyzed cases fitness','Visible-label precision'],[[fitness.score===null?'Unknown':pct(fitness.score),fitness.lower_bound===null?'—':pct(fitness.lower_bound)+' – '+pct(fitness.upper_bound),pct(fitness.analyzed_score),precision.score===null?'Unknown':pct(precision.score)]]);
    summary.id='owgEventLogGraphMetrics';
    const parts=[summary,el('div','Fitness normalizes activity-edit cost. Unknown cases give a range. Precision uses '+precision.matching_cases+' exact matching cases ('+pct(precision.case_population_coverage)+' of selected cases) and measures unused next activity labels, not individual node identity or business rules. '+(reasons[precision.unknown_reason]||''),'muted')];
    const replay=result.token_replay;
    if(replay&&replay.replayed_cases){
      const top=counts=>Object.entries(counts||{}).slice(0,3).map(([label,n])=>label+' ('+Number(n).toLocaleString()+')').join(', ')||'—';
      const tokens=table(['Token-replay fitness','Fitting cases','Missing tokens (activity ran without being enabled)','Remaining tokens (expected but not done)'],
        [[pct(replay.token_fitness),Number(replay.fitting_cases).toLocaleString()+' of '+Number(replay.replayed_cases).toLocaleString(),top(replay.missing_tokens_by_activity),top(replay.remaining_tokens_by_activity)]]);
      tokens.id='owgEventLogGraphTokenReplay';
      parts.push(tokens,el('div','Classic token replay counts where cases depart from the graph. It is greedy, so it can count more deviations than the minimum-edit alignment above. '+(replay.replay_truncated?'Only '+pct(replay.case_population_coverage)+' of cases were replayed within the work limit.':''),'muted'));
    }
    return parts;
  }

  function savedGraphSection(activityNames,inspection){
    const box=section('Compare with a saved process');box.id='owgEventLogSavedGraph';
    const generation=state.generation,current=()=>box.isConnected&&generation===state.generation;
    box.appendChild(el('div','Choose an exact saved local process revision. This compares literal activity order; business rules, outcomes and supplied identities remain unverified.','muted'));
    const load=button('Load saved processes','secondary'),next=button('Next processes','secondary');
    load.id='owgEventLogGraphLoad';next.disabled=true;
    const pick=select('owgEventLogGraphPick',[]),revision=input('owgEventLogGraphRevision','number');revision.min='1';revision.step='1';
    const open=button('Load revision','secondary');open.id='owgEventLogGraphOpen';open.disabled=true;
    const row=el('div',null,'row');row.style.alignItems='end';row.append(load,next,field('Saved local process',pick),field('Revision',revision),open);
    const message=el('div');message.id='owgEventLogGraphStatus';message.setAttribute('role','status');
    const diagram=el('div');diagram.id='owgEventLogSavedGraphMap';
    const filters=cohortFields('owgEventLogGraphCohort','Cases to compare (optional)',activityNames);
    const run=button('Compare saved revision','secondary');run.id='owgEventLogGraphRun';run.disabled=true;
    const out=el('div');out.id='owgEventLogGraphResults';
    let page=[],after=null,pinned=null,last=null,loading=0,comparing=0;
    const exports=exportButtons('owgEventLogGraph',()=>last,r=>[
      ['process','revision','cases','analyzed_cases','structural_matches','structural_deviations','unknown_cases','fitness','fitness_lower_bound','fitness_upper_bound','analyzed_fitness','visible_label_precision','precision_matching_cases','precision_case_coverage'],
      [r.process_reference.title,r.process_reference.revision,r.cases,r.analyzed_cases,r.structural_match_cases,r.structural_deviation_cases,r.unknown_cases,r.structural_fitness?.score??'',r.structural_fitness?.lower_bound??'',r.structural_fitness?.upper_bound??'',r.structural_fitness?.analyzed_score??'',r.visible_prefix_precision?.score??'',r.visible_prefix_precision?.matching_cases??'',r.visible_prefix_precision?.case_population_coverage??'']]);
    box.append(row,message,diagram,filters.node,run,out,exports.node);
    function invalidate(){loading++;comparing++;pinned=null;last=null;run.disabled=true;exports.enable(false);diagram.replaceChildren();out.replaceChildren();}
    function choose(){invalidate();const record=page.find(p=>p.workflow_id===pick.value);revision.value=record?String(record.revision):'';open.disabled=!record;message.textContent='Load the revision to inspect its graph before comparing.';}
    pick.onchange=choose;revision.oninput=()=>{invalidate();open.disabled=!pick.value;message.textContent='Load the selected revision again.';};
    async function loadPage(cursor){
      invalidate();const ticket=loading;load.disabled=true;next.disabled=true;open.disabled=true;
      try{
        const q=new URLSearchParams({limit:'30'});if(cursor)q.set('after',cursor);
        const result=await getJSON('/v1/process-catalog?'+q);
        if(!current()||ticket!==loading)return;
        page=result.processes||[];after=result.next_after;
        setOptions(pick,page.map(p=>[p.workflow_id,p.title+' · revision '+p.revision]));choose();
        next.disabled=!result.has_more;
        if(!page.length)message.textContent=result.has_more?'No process designs on this page. Continue to the next page.':'No saved local process designs. Save a reviewed design in the process catalog first.';
      }catch(error){if(current())message.textContent=error.message;}
      finally{if(current())load.disabled=false;}
    }
    load.onclick=()=>loadPage(null);next.onclick=()=>loadPage(after);
    open.onclick=async()=>{
      invalidate();const ticket=loading;open.disabled=true;
      try{
        const n=Number(revision.value);if(!Number.isSafeInteger(n)||n<1)throw new Error('Choose a positive whole revision number.');
        const q=new URLSearchParams({workflow_id:pick.value,revision:String(n)});
        const record=await getJSON('/v1/process-catalog/revision?'+q);
        if(!current()||ticket!==loading)return;
        pinned={workflow_id:record.workflow_id,revision:record.revision,content_sha256:record.content_sha256};
        message.textContent=record.title+' · revision '+record.revision+'. Select a graph activity to browse cases containing its literal label; that does not verify assignment to a particular graph node.';
        if(window.owgProcessGraph)diagram.appendChild(window.owgProcessGraph.draw(record.process_model,node=>{
          if(node.kind!=='activity')return;
          if(activityNames.includes(node.label))inspection.activity(node.label);
          else message.textContent='No displayed log activity has the exact label '+JSON.stringify(node.label)+'. Activity support is limited to the 500 listed labels.';
        }));
        run.disabled=false;
      }catch(error){if(current()&&ticket===loading)message.textContent=error.message;}
      finally{if(current()&&ticket===loading)open.disabled=false;}
    };
    function alignmentDetails(variant){
      if(!variant.comparison_complete)return 'Alignment unavailable within the work limit.';
      const details=el('details');
      details.append(el('summary','Show alignment'));
      const labels={matched:'Matched',model_only:'Expected by model',log_only:'Observed only'};
      details.append(table(['Step','Activity','Alignment','Log position'],variant.alignment.map((step,index)=>[
        index+1,step.activity,labels[step.type]||step.type,step.log_position??'—'
      ])));
      if(variant.alignment_truncated)details.append(el('p','Only the first 80 alignment steps are shown.','muted'));
      return details;
    }
    async function compare(spec,variantStatus='all',offset=0,restoreFocus=false){
      const ticket=++comparing;run.disabled=true;last=null;exports.enable(false);out.replaceChildren(el('div','Comparing cases…'));
      try{
        const body={...spec,variant_status:variantStatus,variant_offset:offset};
        const result=await (await api(logPath('process-conformance'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
        if(!current()||ticket!==comparing)return;
        last=result;exports.enable(true);
        const variantPage=result.variant_page;
        const counts=variantPage?.by_status;
        const countLabel=(label,key)=>counts?label+' ('+Number(counts[key]).toLocaleString()+')':label;
        const states=select('owgEventLogGraphVariantStatus',[['all','All results'],
          ['deviation',countLabel('Deviations','deviation')],['match',countLabel('Matches','match')],
          ['unknown',countLabel('Unknown within reviewed variants','unknown')]]);
        states.value=variantStatus;states.onchange=()=>compare(spec,states.value,0,true);
        const previous=button('Previous variants','secondary'),nextVariants=button('Next variants','secondary');
        previous.disabled=!variantPage||offset===0;nextVariants.disabled=variantPage?.next_offset==null;
        previous.onclick=()=>compare(spec,variantStatus,Math.max(0,offset-variantPage.size),true);
        nextVariants.onclick=()=>compare(spec,variantStatus,variantPage.next_offset,true);
        const variantTools=el('div',null,'row');variantTools.append(field('Variant result',states),previous,nextVariants);
        const variantNote=variantPage
          ? 'Showing '+(result.displayed_variants.length?offset+1:0)+'–'+(offset+result.displayed_variants.length)+' of '+variantPage.total+' selected variant results. Pages refresh against the current log. Cases excluded by the trace or variant limit are counted above.'
          : 'Showing '+result.displayed_variants.length+' variant results.';
        const reasons={'trace_length_limit':'Case histories exceed the event limit','variant_limit':'Variant selection limit','alignment_work_budget':'Alignment work limit'};
        out.replaceChildren(
          el('h4',result.process_reference.title+' · revision '+result.process_reference.revision),
          table(['Cases','Structurally matched','Structural deviations','Unknown','Analysis coverage','Cases with timestamp ties'],[[result.cases,result.structural_match_cases,result.structural_deviation_cases,result.unknown_cases,pct(result.analysis_coverage),result.ordering_uncertain_cases]]),
          ...graphMetricSummary(result),
          el('div','Applied cohort: '+JSON.stringify(result.cohort)+'. Timestamp ties can overlap any result. Order is timestamp then event ID for display; it is not verified business order.','muted'),
          table(['Unknown reason','Cases'],Object.entries(result.unknown_case_reasons).filter(([,n])=>n>0).map(([reason,n])=>[reasons[reason]||reason,n])),
          ...(variantPage?[variantTools]:[]),el('div',variantNote,'muted'),
          table(['Observed path','Cases','Structural result','Minimum activity edits','Possible incomplete log prefix','Alignment','Evidence'],result.displayed_variants.map(v=>{
            const inspect=button('Inspect cases','secondary');inspect.onclick=()=>inspection.path(v.path,spec.cohort);
            return [v.path.slice(0,40).join(' → ')+(v.path.length>40?' …':''),v.cases,v.comparison_complete?(v.exact_structural_match?'Match':'Deviation'):'Unknown',v.minimum_activity_edit_count===null?'—':v.minimum_activity_edit_count,v.could_be_incomplete_log_prefix===null?'Unknown':(v.could_be_incomplete_log_prefix?'Yes':'No'),alignmentDetails(v),inspect];
          })),
          el('div',result.cases===0?'No cases match this selection; no verdict was established.':result.comparison_complete?'Every selected case was structurally compared. Business rules and outcomes were not checked.':'Some cases remain unknown. They are neither matches nor deviations. Business rules and outcomes were not checked.','muted'));
        if(restoreFocus&&document.activeElement===document.body&&states.isConnected)states.focus();
      }catch(error){if(current()&&ticket===comparing)out.replaceChildren(el('div',error.message));}
      finally{if(current()&&ticket===comparing)run.disabled=false;}
    }
    run.onclick=()=>{
      if(!pinned)return;
      try{compare({...pinned,cohort:filters.read()});}
      catch(error){last=null;exports.enable(false);out.replaceChildren(el('div',error.message));}
    };
    return box;
  }

  // "0.6: A > B > C" per line; the weight is optional and defaults to 1.
  function parseWeightedPaths(text){
    if(String(text||'').trimStart().startsWith('[')){
      const paths=JSON.parse(text);
      if(!Array.isArray(paths)||!paths.length||paths.length>20)throw new Error('Use 1 to 20 weighted paths.');
      const decoded=paths.map(p=>{
        if(!p||!Array.isArray(p.activities)||!p.activities.length||p.activities.some(a=>typeof a!=='string'||!a.trim())||typeof p.weight!=='number'||!Number.isFinite(p.weight)||p.weight<0)throw new Error('Each JSON path needs literal activities and a nonnegative numeric weight.');
        return {activities:p.activities.slice(),weight:p.weight};
      });
      if(!decoded.some(p=>p.weight>0))throw new Error('At least one path weight must be positive.');
      return decoded;
    }
    const paths=[];
    for(const line of String(text||'').split(/\r?\n/)){
      if(!line.trim())continue;
      const m=line.match(/^\s*([0-9]*\.?[0-9]+)\s*:\s*(.*)$/);
      const weight=m?Number(m[1]):1,steps=(m?m[2]:line).split(/>|→/).map(x=>x.trim()).filter(Boolean);
      if(!steps.length)throw new Error('Each path needs at least one activity.');
      paths.push({activities:steps,weight});
    }
    if(!paths.length)throw new Error('Enter at least one path.');
    if(paths.length>20)throw new Error('Use at most 20 paths.');
    return paths;
  }
  const formatWeightedPaths=paths=>paths.some(p=>Number(Number(p.weight).toFixed(3))!==Number(p.weight)||p.activities.some(a=>a!==a.trim()||/[>→]/.test(a)))
    ?JSON.stringify(paths,null,2):paths.map(p=>Number(p.weight).toFixed(3)+': '+p.activities.join(' > ')).join('\n');

  // A working calendar for the simulation from form values, or null for around the clock; throws a readable error.
  const SIM_DAYS={weekdays:['mon','tue','wed','thu','fri'],weekdays_sat:['mon','tue','wed','thu','fri','sat'],every_day:['mon','tue','wed','thu','fri','sat','sun']};
  function simCalendar(values){
    if(!values.days)return null;
    const days=SIM_DAYS[values.days];if(!days)throw new Error('Choose the working days.');
    const from=String(values.from||''),to=String(values.to||'');
    if(!/^\d{2}:\d{2}$/.test(from)||!/^\d{2}:\d{2}$/.test(to)||to<=from)throw new Error('Enter working hours with the end after the start.');
    const timezone=String(values.timezone||'').trim();if(!timezone)throw new Error('Enter the time zone, for example Europe/Berlin.');
    const holidays=String(values.holidays||'').split(/[\s,]+/).filter(Boolean);
    if(holidays.some(d=>!/^\d{4}-\d{2}-\d{2}$/.test(d)))throw new Error('Enter holidays as dates such as 2025-12-25.');
    const hours={};days.forEach(d=>{hours[d]=[[from,to]];});
    const calendar={timezone,hours};if(holidays.length)calendar.holidays=holidays;
    if(values.start)calendar.start=values.start;
    return calendar;
  }

  // One shared resource pool from form values, or null when no pool is named; throws a readable error.
  // Priority classes from "urgent 30% 0, standard 70% 1": name, share and rank (0 served first); empty is none.
  function simPriorities(text){
    const items=String(text||'').split(',').map(x=>x.trim()).filter(Boolean);
    if(!items.length)return null;
    if(items.length<2)throw new Error('Enter at least two priority classes, such as "urgent 30% 0, standard 70% 1".');
    return items.map(item=>{
      const m=/^(.+?)\s+(\d+(?:\.\d+)?)%\s+(\d)$/.exec(item);
      if(!m)throw new Error('Write each priority class as a name, a share in percent and a rank from 0 to 9, such as "urgent 30% 0".');
      return {name:m[1].trim(),share:Math.round(Number(m[2])*1e4)/1e6,rank:Number(m[3])};
    });
  }
  // A batch for one activity from form values, or null without one: {activity, batch:{size, max_wait_minutes?}}.
  function simBatch(values){
    if(!values.activity)return null;
    const size=Number(values.size);
    if(!Number.isInteger(size)||size<2)throw new Error('A batch holds a whole number of at least 2 cases.');
    const batch={size};
    if(String(values.maxWaitHours||'').trim()){const h=Number(values.maxWaitHours);if(!(h>0))throw new Error('The longest wait for a batch is a positive number of hours.');batch.max_wait_minutes=Math.round(h*60*1000)/1000;}
    return {activity:values.activity,batch};
  }
  function simPool(values){
    const name=String(values.name||'').trim();
    if(!name)return null;
    const resources=Number(values.resources);
    if(!Number.isInteger(resources)||resources<1)throw new Error('Enter a whole number of people for the pool.');
    if(!values.activities||values.activities.length<2)throw new Error('Choose at least two activities for the shared pool.');
    return {[name]:{resources,activities:[...values.activities]}};
  }
  // A seasonal arrival pattern from form values, or null for a steady rate; throws a readable error.
  function simArrivals(values){
    const timezone=String(values.timezone||'').trim();
    if(!values.mode)return null;
    if(values.mode==='from_log')return timezone?{from_log:true,timezone}:{from_log:true};
    if(values.mode!=='weights')throw new Error('Choose how cases arrive.');
    const list=(text,count,what)=>{
      const parts=String(text||'').split(/[\s,;]+/).filter(Boolean);
      if(!parts.length)return null;
      const nums=parts.map(Number);
      if(nums.length!==count||nums.some(n=>!Number.isFinite(n)||n<0)||!nums.some(n=>n>0))throw new Error('Enter '+count+' non-negative '+what+' weights, not all zero.');
      return nums;
    };
    const pattern={};
    const hourly=list(values.hourly,24,'hourly'),daily=list(values.daily,7,'daily (Monday first)');
    if(hourly)pattern.hourly=hourly;if(daily)pattern.daily=daily;
    if(!hourly&&!daily)throw new Error('Enter hourly or daily arrival weights.');
    if(timezone)pattern.timezone=timezone;
    return pattern;
  }
  const arrivalText=p=>!p?'steady around the clock':(p.source==='measured_case_starts'?'measured from '+Number(p.basis.case_starts).toLocaleString()+' case starts over '+Number(p.basis.measured_days).toFixed(0)+' days':'your weights')+
    ' ('+p.timezone+'); the busiest hour gets '+Number(p.peak_factor).toFixed(1)+'× the average rate';
  const calendarText=c=>!c?'around the clock':Object.keys(c.hours).join(', ')+' '+Object.values(c.hours)[0].map(x=>x.join('–')).join(', ')+' ('+c.timezone+')'+(c.holidays.length?', '+c.holidays.length+' holidays':'');

  // Plain-language reading of a held-out validation result.
  function validationText(r){
    const c=r.cycle_time_seconds,e=c.relative_error,d=r.distribution_distance;
    const sign=x=>x===null?'—':(x>=0?'+':'−')+Math.abs(x*100).toFixed(0)+'%';
    const routes=r.simulated_routes,stopped=routes?.stopped_at_path_limit||0;
    return {
      stopped:stopped?Number(stopped).toLocaleString()+' of '+Number(routes.cases_simulated).toLocaleString()+' simulated cases reached the '+routes.path_event_limit+'-event path limit and were stopped there. These stopped prefixes are compared as if finished, which shortens simulated times; the fit below does not assess completed routes.':null,
      routing:r.routing==='transitions'?'Step-by-step routing is first-order: the next activity depends only on the current one, not on earlier steps.':null,
      cases:'Calibrated on '+Number(r.cases.calibration).toLocaleString()+' cases finished before '+String(r.cutoff).slice(0,10)+' and compared with the '+Number(r.cases.held_out).toLocaleString()+' cases that started after it; '+Number(r.cases.spanning_cutoff_left_out).toLocaleString()+' cases spanning the cutoff are left out.',
      rows:[['Median cycle time',duration(c.simulated.p50),duration(c.observed_held_out.p50),sign(e.p50)],
            ['90th percentile',duration(c.simulated.p90),duration(c.observed_held_out.p90),sign(e.p90)],
            ['Mean',duration(c.simulated.mean),duration(c.observed_held_out.mean),sign(e.mean)]],
      verdict:d.distributions_differ_at_5pct===null?'Too few cases to compare the distributions.':
        d.distributions_differ_at_5pct?'The simulated and later cycle times differ more than chance explains (Kolmogorov–Smirnov '+d.kolmogorov_smirnov.toFixed(3)+' > '+d.critical_value_5pct.toFixed(3)+' at 5%). Treat what-if results with caution and check the assumptions.':
        'The simulated and later cycle times are consistent at the 5% level (Kolmogorov–Smirnov '+d.kolmogorov_smirnov.toFixed(3)+' ≤ '+d.critical_value_5pct.toFixed(3)+'). This is evidence about one period, not a forecast guarantee.'};
  }

  // The measured gap that stands for an activity's time; the mean suits variable (exponential) times.
  const TIME_BASES=[['median','Median gap (robust)'],['mean','Mean gap (suits variable times)']];
  const ROUTINGS=[['paths','Most frequent complete paths'],['transitions','Step by step, from every case']];
  // Visits per case for each activity in both runs, with the change; step-by-step routing only.
  function visitRows(baseline,result){
    const b=baseline.visits_per_case||{},s=result.visits_per_case||{};
    return [...new Set([...Object.keys(b),...Object.keys(s)])].sort().map(a=>{const x=b[a]||0,y=s[a]||0,d=y-x;
      return [a,x.toFixed(2),y.toFixed(2),(d>0?'+':d<0?'−':'')+Math.abs(d).toFixed(2)];});
  }
  // Replications for branch and arrival uncertainty: empty or 0 is off, otherwise 2–50.
  function simUncertainty(value){
    const text=String(value??'').trim();if(text===''||text==='0')return null;
    const n=Number(text);if(!Number.isInteger(n)||n<2||n>50)throw new Error('Enter 2 to 50 replications, or leave it empty.');
    return {replications:n};
  }
  // One line per interval: median and 95% range across replications.
  function uncertaintyRows(u){
    const span=x=>x?duration(x.median)+' ('+duration(x.low)+' – '+duration(x.high)+')':'—';
    const signed=x=>{if(!x)return '—';const f=v=>(v>0?'+':v<0?'−':'')+duration(Math.abs(v));return f(x.median)+' ('+f(x.low)+' to '+f(x.high)+')';};
    const c=u.cycle_time_seconds;
    return [['Baseline',span(c.baseline.p50),span(c.baseline.p90)],['Scenario',span(c.result.p50),span(c.result.p90)],['Change',signed(c.change.p50),signed(c.change.p90)]];
  }
  const measuredMinutes=(p,basis)=>basis==='mean'&&p.mean_minutes!==undefined?p.mean_minutes:p.minutes;

  function validationBlock(){
    const wrap=el('div');
    const share=select('owgEventLogSimHoldout',[['0.3','Latest 30% of cases'],['0.2','Latest 20% of cases'],['0.4','Latest 40% of cases']]);
    const dist=select('owgEventLogSimHoldoutDistribution',[['exponential','Variable (exponential)'],['fixed','Fixed']]);
    const basis=select('owgEventLogSimHoldoutBasis',TIME_BASES);
    const holdoutRouting=select('owgEventLogSimHoldoutRouting',ROUTINGS);
    const run=button('Check against later cases','secondary');run.id='owgEventLogSimValidate';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Hold out',share),field('Activity times',dist),field('Measured time per activity',basis),field('Routing',holdoutRouting),run);
    const out=el('div');out.id='owgEventLogSimValidation';out.setAttribute('role','status');
    wrap.append(el('h4','Does the model reproduce later cases?'),el('div','Calibrate on earlier cases only, simulate, and compare with the cases that started afterwards.','muted'),row,out);
    run.onclick=async()=>{
      run.disabled=true;out.replaceChildren(el('div','Checking…','muted'));
      try{
        const r=await (await api(logPath('simulation/validation'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({holdout_share:Number(share.value),distribution:dist.value,activity_time_basis:basis.value,routing:holdoutRouting.value})})).json();
        const t=validationText(r);
        out.replaceChildren(el('div',t.cases,'muted'),...[t.routing,t.stopped].filter(Boolean).map(x=>el('div',x,'note')),table(['','Simulated','Later cases','Difference'],t.rows),el('div',t.verdict,'note'),
          (()=>{const list=el('ul');list.className='muted';r.assumptions.slice(0,4).forEach(x=>list.appendChild(el('li',x)));return list;})());
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return wrap;
  }

  function simulationSection(){
    const box=section('What-if simulation');
    box.appendChild(el('div','Simulate cases flowing through the process with the times and staffing you set. Start from values measured in this log, change them, and compare. Results are model outputs under the listed assumptions, not forecasts.','muted'));
    const load=button('Load measured values','secondary');load.id='owgEventLogSimLoad';
    const form=el('div');form.id='owgEventLogSimForm';
    const out=el('div');box.append(load,form,out,validationBlock());
    load.onclick=async()=>{
      try{
        const cal=await getJSON(logPath('simulation/calibration'));
        const coverage='Calibrated '+cal.calibrated_cases+' of '+cal.observed_cases+' complete cases ('+pct(cal.paths_cover_cases_share)+'). Omitted cases: '+
          Object.entries(cal.excluded_case_reasons||{}).map(([reason,n])=>reason.replace(/_/g,' ')+': '+n).join(', ')+'. Arrivals and activity parameters use the selected complete cases only.';
        if(!cal.paths?.length){form.replaceChildren(el('div',coverage+' No complete paths fit the simulation limits.','muted'));return;}
        const arrivals=input('owgEventLogSimArrivals','number');arrivals.min='0';arrivals.step='any';arrivals.value=cal.arrivals_per_hour===null?'':String(cal.arrivals_per_hour);
        const cases=input('owgEventLogSimCases','number');cases.min='10';cases.max='20000';cases.value='2000';
        const dist=select('owgEventLogSimDistribution',[['exponential','Variable (exponential)'],['fixed','Fixed']]);
        const label=input('owgEventLogSimLabel','text','e.g. Two reviewers');label.maxLength=80;
        const top=el('div',null,'row');top.style.alignItems='end';
        const timeBasis=select('owgEventLogSimTimeBasis',TIME_BASES);
        const spread=input('owgEventLogSimReplications','number','Off');spread.min='0';spread.max='50';spread.step='1';
        top.append(field('Scenario name',label),field('Cases arriving per hour',arrivals),field('Activity times',dist),field('Measured time per activity',timeBasis),field('Cases to simulate',cases),field('Uncertainty replications (2–50)',spread));
        const dayChoices=[['weekdays','Monday to Friday'],['weekdays_sat','Monday to Saturday'],['every_day','Every day']];
        const days=select('owgEventLogSimDays',[['','Around the clock'],...dayChoices]);
        const scenarioDays=select('owgEventLogSimScenarioDays',[['','Same as the baseline'],...dayChoices]);
        const from=input('owgEventLogSimFrom','time');from.value='09:00';const to=input('owgEventLogSimTo','time');to.value='17:00';
        const zone=input('owgEventLogSimTimezone','text','Europe/Berlin');zone.maxLength=64;
        try{zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'';}catch(_){}
        const holidays=input('owgEventLogSimHolidays','text','2025-12-25, 2025-12-26');
        const calRow=el('div',null,'row');calRow.style.alignItems='end';
        const calFields={from:field('Working from',from),to:field('Until',to),zone:field('Time zone',zone),holidays:field('Holidays (dates)',holidays),scenario:field('Scenario working days',scenarioDays)};
        const syncCal=()=>Object.values(calFields).forEach(f=>show(f,!!days.value));days.onchange=syncCal;syncCal();
        calRow.append(field('Working days (both runs)',days),...Object.values(calFields));
        // How cases arrive over the week, for both runs: steady, measured from this log, or weights you enter.
        const arrivalMode=select('owgEventLogSimArrivalMode',[['','Steady around the clock'],['from_log','Measured from this log (hour of week)'],['weights','Weights I enter']]);
        const hourly=input('owgEventLogSimHourly','text','24 numbers, midnight first, e.g. 0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 0 0 0 0 0 0 0');
        const daily=input('owgEventLogSimDaily','text','7 numbers, Monday first, e.g. 1 1 1 1 1 0 0');
        const arrivalFields=[field('Hourly arrival weights',hourly),field('Daily arrival weights',daily)];
        const zoneField=calFields.zone;
        const syncArrivals=()=>{arrivalFields.forEach(f=>show(f,arrivalMode.value==='weights'));show(zoneField,!!days.value||!!arrivalMode.value);};
        arrivalMode.onchange=syncArrivals;days.addEventListener('change',syncArrivals);syncArrivals();
        const arrivalRow=el('div',null,'row');arrivalRow.style.alignItems='end';
        arrivalRow.append(field('How cases arrive (both runs)',arrivalMode),...arrivalFields);
        const paths=document.createElement('textarea');paths.id='owgEventLogSimPaths';paths.rows=4;paths.style.width='100%';paths.value=formatWeightedPaths(cal.paths||[]);
        const measuredRouting=paths.value,measuredArrivals=arrivals.value;
        const rows=[];
        const body=el('tbody');
        const addRow=(name,p)=>{
          const i=rows.length;
          const minutes=input('owgEventLogSimMinutes'+i,'number');minutes.min='0';minutes.step='any';minutes.value=String(p.minutes);minutes.setAttribute('aria-label','Minutes for '+name);
          const people=input('owgEventLogSimResources'+i,'number');people.min='1';people.step='1';people.value=String(p.resources);people.setAttribute('aria-label','Resources for '+name);
          rows.push({name,minutes,people,measured:cal.activities&&cal.activities[name]});
          const tr=el('tr');const a=el('td'),b=el('td');a.appendChild(minutes);b.appendChild(people);tr.append(el('td',name),a,b);body.appendChild(tr);
        };
        Object.entries(cal.activities||{}).forEach(([name,p])=>addRow(name,p));
        // Switching the basis refills the measured minutes; activities added by hand keep what was typed.
        timeBasis.onchange=()=>rows.forEach(r=>{if(r.measured)r.minutes.value=String(measuredMinutes(r.measured,timeBasis.value));});
        const wrap=el('div',null,'table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: activity minutes and resources');const t=el('table');const head=el('thead');const hr=el('tr');['Activity','Minutes per case','Resources'].forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
        t.append(head,body);wrap.appendChild(t);
        const newName=input('owgEventLogSimNewName','text','New activity');newName.maxLength=200;
        const add=button('Add activity','secondary');add.id='owgEventLogSimAdd';
        add.onclick=()=>{const name=newName.value.trim();if(!name||rows.some(r=>r.name===name))return;addRow(name,{minutes:0,resources:1});newName.value='';};
        const addRowNode=el('div',null,'row');addRowNode.style.alignItems='end';addRowNode.append(field('Activity not in the log',newName),add);
        // One shared pool for the scenario: the chosen activities draw on one team through one queue.
        const poolName=input('owgEventLogSimPoolName','text','e.g. Review team');poolName.maxLength=80;
        const poolSize=input('owgEventLogSimPoolResources','number');poolSize.min='1';poolSize.step='1';poolSize.value='2';
        const poolActivities=select('owgEventLogSimPoolActivities',Object.keys(cal.activities||{}).map(a=>[a,a]));poolActivities.multiple=true;poolActivities.size=3;
        const poolRow=el('div',null,'row');poolRow.style.alignItems='end';
        const priorityText=input('owgEventLogSimPriorities','text','e.g. urgent 30% 0, standard 70% 1');priorityText.maxLength=300;
        const batchActivity=select('owgEventLogSimBatchActivity',[['','No batching'],...Object.keys(cal.activities||{}).map(a=>[a,a])]);
        const batchSize=input('owgEventLogSimBatchSize','number');batchSize.min='2';batchSize.step='1';batchSize.value='10';
        const batchWait=input('owgEventLogSimBatchWait','number');batchWait.min='0';batchWait.step='any';batchWait.placeholder='no limit';
        poolRow.append(field('Shared pool in the scenario (optional)',poolName),field('People in the pool',poolSize),field('Activities the pool does',poolActivities),field('Priority classes in the scenario (optional; rank 0 is served first)',priorityText),
          field('Batch an activity in the scenario',batchActivity),field('Cases per batch',batchSize),field('Longest wait for a batch (hours)',batchWait));
        const run=button('Run simulation','secondary');run.id='owgEventLogSimRun';
        const routeKind=select('owgEventLogSimRouting',ROUTINGS);
        const pathsField=field('Routing: weighted lines or JSON for exact labels and weights',paths);
        // Step-by-step routing can also depend on the previous activity or on a case attribute (#370 R14).
        const routeContext=select('owgEventLogSimRoutingContext',[['current','The current activity only'],['history','The previous and current activity'],['attribute','A case attribute']]);
        const routeAttribute=input('owgEventLogSimRoutingAttribute','text','e.g. priority');routeAttribute.maxLength=64;
        const contextField=field('The next step depends on',routeContext),attributeField=field('Case attribute',routeAttribute);
        const showContext=()=>{const steps=routeKind.value==='transitions';show(contextField,steps);show(attributeField,steps&&routeContext.value==='attribute');};
        routeContext.onchange=showContext;
        routeKind.onchange=()=>{show(pathsField,routeKind.value==='paths');showContext();};showContext();
        form.replaceChildren(top,calRow,arrivalRow,field('How cases move between activities',routeKind),contextField,attributeField,pathsField,wrap,addRowNode,poolRow,
          el('div','Measured times are median gaps before each activity and include waiting. '+coverage,'muted'),run);

        // Compare 2–4 reviewed staffing proposals against ONE static calibration.
        // This small visual entry point changes one mapped activity's staffing
        // per alternative; the API also supports multi-activity configurations.
        const portfolioPanel=el('div');portfolioPanel.id='owgEventLogSimPortfolio';
        const portfolioChoices=el('div');
        const portfolioCases=input('owgEventLogSimPortfolioCases','number');
        portfolioCases.min='10';portfolioCases.max='5000';portfolioCases.step='1';portfolioCases.value='400';
        const portfolioRows=[];
        function addPortfolioChoice(){
          if(portfolioRows.length>=4)return;
          const n=portfolioRows.length+1;
          const title=input('owgEventLogSimPortfolioName'+n,'text');
          title.value='Alternative '+n;title.maxLength=80;
          const activity=select('owgEventLogSimPortfolioActivity'+n,
            Object.keys(cal.activities||{}).map(value=>[value,value]));
          const staff=input('owgEventLogSimPortfolioStaff'+n,'number');
          staff.min='1';staff.max='1000';staff.step='1';
          const original=cal.activities[activity.value]?.resources||1;
          staff.value=String(Math.min(1000,original+(n-1)));
          activity.onchange=()=>{staff.value=String(cal.activities[activity.value]?.resources||1);};
          const row=el('div',null,'row');row.style.alignItems='end';
          row.append(field('Alternative name',title),field('Measured activity',activity),
            field('People assigned',staff));
          portfolioChoices.append(row);
          portfolioRows.push({title,activity,staff});
        }
        addPortfolioChoice();addPortfolioChoice();
        const addPortfolio=button('Add staffing alternative','secondary');
        addPortfolio.id='owgEventLogSimPortfolioAdd';
        addPortfolio.onclick=()=>{addPortfolioChoice();addPortfolio.disabled=portfolioRows.length>=4;};
        const comparePortfolio=button('Compare staffing alternatives','secondary');
        comparePortfolio.id='owgEventLogSimPortfolioRun';
        const portfolioResult=el('div');portfolioResult.id='owgEventLogSimPortfolioResults';
        portfolioResult.setAttribute('role','status');portfolioResult.setAttribute('aria-live','polite');
        const compareToolbar=el('div',null,'row');
        compareToolbar.style.alignItems='end';
        compareToolbar.append(field('Cases per simulated run',portfolioCases),addPortfolio,comparePortfolio);
        portfolioPanel.append(el('h4','Compare several staffing alternatives'),
          el('p','Select two to four candidate staffing levels. The same observed paths, input calibration, random seed and sample count are used across every run. This is hypothetical process modeling, not a staffing recommendation, forecast or employee performance score.','muted'),
          portfolioChoices,compareToolbar,portfolioResult);
        form.append(portfolioPanel);
        comparePortfolio.onclick=async()=>{
          try{
            const caseCount=Number(portfolioCases.value);
            if(!Number.isInteger(caseCount)||caseCount<10||caseCount>5000)
              throw new Error('Choose 10–5000 cases for the portfolio.');
            const choices=portfolioRows.map(item=>{
              const name=item.title.value.trim(),activity=item.activity.value,
                staff=Number(item.staff.value);
              if(!name||name.length>80||!activity||!Number.isInteger(staff)||staff<1||staff>1000)
                throw new Error('Review each alternative name, activity and staffing level.');
              // Object.create(null) prevents activity names like __proto__
              // from changing any object's prototype during construction.
              const overrides=Object.create(null);overrides[activity]={resources:staff};
              return {label:name,activities:overrides};
            });
            if(new Set(choices.map(x=>x.label)).size!==choices.length)
              throw new Error('Give every alternative a distinct name.');
            const baseline={cases:caseCount,seed:1,activity_time_basis:timeBasis.value};
            if(arrivals.value!=='')baseline.arrivals_per_hour=Number(arrivals.value);
            if(days.value)baseline.calendar=simCalendar({
              days:days.value,from:from.value,to:to.value,
              timezone:zone.value,holidays:holidays.value});
            const body={format:'openworkgraph.simulation-portfolio.v1',baseline,alternatives:choices};
            comparePortfolio.disabled=true;portfolioResult.replaceChildren(el('p','Comparing the selected alternatives locally…','muted'));
            const report=await(await api(logPath('simulation/portfolio'),{
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
            })).json();
            const cycle=result=>result.cycle_time_seconds?.p50===null?'—':duration(result.cycle_time_seconds?.p50);
            const entries=[['Measured baseline',cycle(report.baseline),report.baseline.cases_measured,
              report.baseline.unstable?'Overloaded':'Reference only']];
            for(const item of report.alternatives){
              entries.push([item.label,cycle(item.result),item.result.cases_measured,
                item.descriptive_cycle_time_comparable?
                  'Descriptive change '+(item.cycle_time_change_seconds.p50>=0?'+':'−')+
                    duration(Math.abs(item.cycle_time_change_seconds.p50)):
                  'No comparable delta: '+item.reasons_deltas_withheld.join(', ').replaceAll('_',' ')]);
            }
            portfolioResult.replaceChildren(
              el('p','Shared source and calibration digest '+report.calibration_and_shared_inputs_sha256+
                '. All numbers are simulations; a smaller median is not proof of savings.','muted'),
              table(['Run','Median cycle time','Post-warm-up cases completed','Comparison status'],entries),
              el('p','Runs with unstable queues or incomplete/abandoned cases have their deltas withheld. No winning option is selected or executed.','muted')
            );
          }catch(error){portfolioResult.replaceChildren(el('p',error.message,'muted'));}
          finally{comparePortfolio.disabled=false;}
        };
        run.onclick=async()=>{
          try{
            const activities=Object.create(null);
            for(const r of rows){const m=Number(r.minutes.value),n=Number(r.people.value);if(!(m>=0)||!(n>=1)||!Number.isInteger(n))throw new Error('Check the minutes and resources for '+r.name+'.');activities[r.name]={minutes:m,resources:n};}
            const scenario={format:'openworkgraph.simulation-scenario.v1',label:label.value.trim(),paths:parseWeightedPaths(paths.value),activities,distribution:dist.value,cases:Math.round(Number(cases.value)||2000),activity_time_basis:timeBasis.value};
            if(arrivals.value!=='')scenario.arrivals_per_hour=Number(arrivals.value);
            if(routeKind.value==='transitions'){scenario.routing='transitions';delete scenario.paths;
              if(routeContext.value==='history')scenario.routing_context='history';
              else if(routeContext.value==='attribute'){const name=routeAttribute.value.trim();if(!name)throw new Error('Enter the case attribute that routing depends on.');scenario.routing_context='attribute:'+name;}}
            const pool=simPool({name:poolName.value,resources:poolSize.value,activities:[...poolActivities.selectedOptions].map(o=>o.value)});
            if(pool)scenario.pools=pool;
            const batched=simBatch({activity:batchActivity.value,size:batchSize.value,maxWaitHours:batchWait.value});
            if(batched)activities[batched.activity]={...activities[batched.activity],batch:batched.batch};
            const classes=simPriorities(priorityText.value);
            if(classes)scenario.priorities=classes;
            const calValues={days:days.value,from:from.value,to:to.value,timezone:zone.value,holidays:holidays.value};
            const calendar=simCalendar(calValues);
            const pattern=simArrivals({mode:arrivalMode.value,timezone:zone.value,hourly:hourly.value,daily:daily.value});
            if(pattern)scenario.arrival_pattern=pattern;
            if(calendar){scenario.calendar=calendar;if(scenarioDays.value&&scenarioDays.value!==days.value)scenario.scenario_calendar=simCalendar({...calValues,days:scenarioDays.value});}
            const uncertainty=simUncertainty(spread.value);
            if(uncertainty){
              scenario.uncertainty=uncertainty;
              // Values still as measured are left to the server, which draws them from what the log supports.
              if(paths.value===measuredRouting)delete scenario.paths;
              if(arrivals.value===measuredArrivals)delete scenario.arrivals_per_hour;
              for(const r of rows){
                if(!r.measured||r.minutes.value!==String(measuredMinutes(r.measured,timeBasis.value)))continue;
                if(Number(r.people.value)===r.measured.resources)delete activities[r.name];else delete activities[r.name].minutes;
              }
            }
            run.disabled=true;out.replaceChildren(el('div','Simulating…','muted'));
            const r=await (await api(logPath('simulate'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scenario)})).json();
            const line=(name,x)=>{const c=x.cycle_time_seconds||{};return [name,duration(c.p50),duration(c.p90),duration(c.mean),x.completed_per_day===null?'—':Number(x.completed_per_day).toFixed(1)];};
            const change=r.median_cycle_time_change_seconds;
            const report={format:'openworkgraph.simulation-report.v1',generated_at:new Date().toISOString(),
              interpretation:'Modelled outcomes under stated assumptions; not measured savings or execution approval.',
              submitted_scenario:scenario,analysis:r};
            const exports=exportButtons('owgEventLogSimulation',()=>report,()=>{
              const data=[['section','run','metric','value','unit']];
              for(const [name,result] of [['baseline',r.baseline],['scenario',r.result]]){
                for(const metric of ['p50','p90','mean'])data.push(['cycle_time',name,metric,result.cycle_time_seconds?.[metric],'seconds']);
                data.push(['throughput',name,'completed_per_day',result.completed_per_day,'cases/day']);
                data.push(['stability',name,'unstable',Boolean(result.unstable),'boolean']);
                for(const activity of result.activities||[])for(const metric of ['resources','minutes','utilization','mean_wait_seconds','p90_wait_seconds'])
                  data.push(['activity',name,activity.activity+' / '+metric,activity[metric],metric==='minutes'?'minutes':metric.endsWith('_seconds')?'seconds':metric==='utilization'?'fraction':'resources']);
              }
              for(const assumption of r.assumptions||[])data.push(['assumption','both',assumption,'','']);
              data.push(['interpretation','both',report.interpretation,'','']);
              return data;
            });
            exports.enable(true);
            out.replaceChildren(
              exports.node,
              el('div','Downloads include activity labels, log metadata, declared schedules and model assumptions. Review them before sharing. JSON contains the complete run; CSV summarizes metrics in their stated units.','muted'),
              ...(r.result.unstable?[el('div','Warning: at least one activity has more work arriving than its resources can handle, so its queue keeps growing. Times below depend on how many cases you simulate.','note')]:[]),
              table(['Run','Median cycle time','90th percentile','Mean','Completed per day'],[line('Simulated baseline',r.baseline),line(r.scenario.label||'Scenario',r.result)]),
              el('div',change===null?'':'Median cycle time change: '+(change>=0?'+':'−')+duration(Math.abs(change))+'.','muted'),
              ...(r.result.visits_per_case?[el('h4','Visits per case (step-by-step routing)'),
                table(['Activity','Baseline','Scenario','Change'],visitRows(r.baseline,r.result)),
                ...(r.result.routes_cut_at_path_limit||r.baseline.routes_cut_at_path_limit?[el('div',(r.result.routes_cut_at_path_limit+r.baseline.routes_cut_at_path_limit)+' simulated cases reached the path-length limit and were stopped there.','note')]:[])]:[]),
              ...(r.uncertainty?[(()=>{const box=el('div');box.id='owgEventLogSimUncertainty';
                box.append(el('h4','Uncertainty from routing and arrivals ('+r.uncertainty.replications+' replications, 95% range)'),
                  table(['Run','Median cycle time','90th percentile'],uncertaintyRows(r.uncertainty)),
                  table(['Measured path','Cases','Share drawn (95% range)'],r.uncertainty.measured_routing.map(p=>[p.activities.join(' → '),Number(p.observed_cases).toLocaleString(),pct(p.drawn.median)+' ('+pct(p.drawn.low)+' – '+pct(p.drawn.high)+')'])),
                  ...((r.uncertainty.measured_activity_minutes||[]).length?[table(['Measured activity','Gaps measured','Minutes drawn (95% range)'],r.uncertainty.measured_activity_minutes.map(a=>[a.activity,Number(a.observed_gaps).toLocaleString(),a.drawn.median.toFixed(1)+' ('+a.drawn.low.toFixed(1)+' – '+a.drawn.high.toFixed(1)+')']))]:[]),
                  el('div','The scenario median is faster in '+r.uncertainty.replications_where_scenario_median_is_faster+' of '+r.uncertainty.replications+' replications. Values you entered stay fixed.','muted'));return box;})()]:[]),
              ...(r.scenario.arrival_pattern?[el('div','Arrivals: '+arrivalText(r.scenario.arrival_pattern)+'. The arrival rate you set stays the long-run average.','muted')]:[]),
              ...(r.baseline_calendar||r.scenario.calendar?[el('div','Working time: baseline '+calendarText(r.baseline_calendar)+'; scenario '+calendarText(r.scenario.calendar)+'. Utilization is measured against working time.','muted')]:[]),
              table(['Activity','Resources','Minutes','Utilization','Mean wait','90th percentile wait'],r.result.activities.map(a=>[a.activity+(a.pool?' ('+a.pool+')':''),a.resources,a.minutes,pct(a.utilization),duration(a.mean_wait_seconds),duration(a.p90_wait_seconds)])),
              ...(r.result.activities.some(a=>a.batch)?[table(['Batched activity','Cases per batch','Longest wait','Batches run','Average batch','Mean wait','Still waiting'],r.result.activities.filter(a=>a.batch).map(a=>[a.activity,a.batch.size,a.batch.max_wait_minutes?duration(a.batch.max_wait_minutes*60):'no limit',Number(a.batch.batches).toLocaleString(),a.batch.mean_batch_size===null?'—':Number(a.batch.mean_batch_size).toFixed(1),duration(a.mean_wait_seconds),a.batch.waiting_in_unfilled_batch]))]:[]),
              ...(r.result.priority_classes?[table(['Priority class','Share','Rank','Median cycle time','90th percentile','Mean wait per step'],r.result.priority_classes.map(c=>[c.name,pct(c.share),c.rank,duration(c.cycle_time_seconds.p50),duration(c.cycle_time_seconds.p90),duration(c.mean_wait_seconds_per_step)]))]:[]),
              ...(r.result.pools?[table(['Shared pool','People','Activities','Utilization','Mean wait','90th percentile wait'],r.result.pools.map(p=>[p.pool,p.resources,p.activities.join(', '),pct(p.utilization),duration(p.mean_wait_seconds),duration(p.p90_wait_seconds)]))]:[]),
              (()=>{const list=el('ul');list.className='muted';r.assumptions.forEach(x=>list.appendChild(el('li',x)));return list;})());
          }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
          finally{run.disabled=false;}
        };
      }catch(error){form.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  function conformanceSection(topVariant){
    const box=section('Compare with a reference process');
    const model=document.createElement('textarea');model.id='owgEventLogModel';model.rows=3;model.style.width='100%';
    model.placeholder='One accepted path per line, e.g. Receive > Review > Close, or reference-model JSON for exact labels';
    if(topVariant&&!topVariant.path_truncated&&topVariant.path.length<=100)model.value=JSON.stringify({format:MODEL_FORMAT,paths:[topVariant.path]});
    const run=button('Check conformance','secondary');run.id='owgEventLogConformanceRun';
    const out=el('div');box.append(field('Reference paths or exact-label JSON',model),run,out);
    run.onclick=async()=>{
      try{
        const body=JSON.stringify(parsePaths(model.value));
        const r=await (await api(logPath('conformance'),{method:'POST',headers:{'Content-Type':'application/json'},body})).json();
        out.replaceChildren(
          table(['Fitness','Fitting cases','Aligned cases'],[[r.fitness===null?'—':Number(r.fitness).toFixed(3),pct(r.fitting_share),pct(r.aligned_share)]]),
          table(['Skipped reference step','Cases'],(r.skipped_model_activities||[]).slice(0,10).map(x=>[x.activity,Number(x.cases).toLocaleString()])),
          table(['Extra observed step','Cases'],(r.extra_observed_activities||[]).slice(0,10).map(x=>[x.activity,Number(x.cases).toLocaleString()])),
          el('div','Fitness is 1 when every case follows a reference path exactly. The reference is what you entered, not an approved policy.','muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  function rootCauseSection(activityNames,inspection){
    const box=section('What is associated with an outcome');
    const outcome=select('owgEventLogOutcome',[['duration_over','Case takes longer than'],['rework','Case has rework'],['contains_activity','Case contains activity'],['ends_with','Case ends with activity'],['tagged','Case has tag']]);
    const outcomeTag=input('owgEventLogOutcomeTag','text','for example off-model');outcomeTag.maxLength=60;outcomeTag.autocomplete='off';
    const hours=input('owgEventLogOutcomeHours','number','hours');hours.min='0';hours.step='any';
    const activity=select('owgEventLogOutcomeActivity',activityNames.slice(0,500).map(a=>[a,a]));
    const exact=input('owgEventLogOutcomeActivityExact','text','Exact activity label (optional)');exact.maxLength=400;
    const lookup=activityLabelLookup(exact,{id:part=>'owgEventLogOutcomeActivity'+part,subject:'outcome activity'});
    exact.addEventListener('input',lookup.reset);activity.onchange=()=>{exact.value='';lookup.reset();};
    const run=button('Find associations','secondary');run.id='owgEventLogRootCauseRun';
    const hoursField=field('Hours',hours),activityField=field('Activity',activity),exactField=field('Exact outcome activity',exact),tagField=field('Tag',outcomeTag);
    const activityNote=el('div','An exact label overrides the suggestion. Matching is literal; an unknown label may match no cases.','muted');
    const sync=()=>{const needsActivity=['contains_activity','ends_with'].includes(outcome.value);
      show(hoursField,outcome.value==='duration_over');show(tagField,outcome.value==='tagged');show(activityField,needsActivity);show(exactField,needsActivity);show(lookup.wrap,needsActivity);show(activityNote,needsActivity);};
    outcome.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Outcome',outcome),hoursField,tagField,activityField,exactField,lookup.wrap,run);
    const out=el('div');out.id='owgEventLogRootCauseResults';box.append(row,activityNote,out);
    // Each association carries the case filter that selects exactly its cases: look at them, tag them or save them.
    const casePanel=el('div');casePanel.id='owgEventLogRootCauseCases';casePanel.setAttribute('role','status');
    const caseGeneration=state.generation,casesBase='/v1/event-logs/'+encodeURIComponent(state.logId);
    const postJSON=(path,body)=>api(casesBase+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(x=>x.json());
    const withType=body=>{if(state.caseType)body.case_type=state.caseType;return body;};
    function caseActions(a){
      const label=a.feature.kind+': '+a.feature.value;
      const wrap=el('div',null,'row');
      const inspect=button('Inspect cases','secondary');inspect.setAttribute('aria-label','Inspect cases with '+label);
      inspect.onclick=()=>inspection.cohort(a.cohort);
      const slowest=button('Slowest cases','secondary');slowest.setAttribute('aria-label','Slowest cases with '+label);
      const tag=button('Tag cases','secondary');tag.setAttribute('aria-label','Tag the cases with '+label);
      const save=button('Save as log','secondary');save.setAttribute('aria-label','Save the cases with '+label+' as a new log');
      slowest.onclick=async()=>{
        try{
          const r=await postJSON('/cases/slowest',withType({cohort:a.cohort,limit:10}));
          if(caseGeneration!==state.generation)return;
          const list=el('div',null,'row');
          r.cases.forEach(c=>{const b=button(c.case_id+' ('+duration(c.duration_seconds)+')','secondary');b.setAttribute('aria-label','Explain case '+c.case_id);
            b.onclick=()=>{const id=document.getElementById('owgCaseExplainId'),run=document.getElementById('owgCaseExplainRun');
              if(!id||!run)return;id.value=c.case_id;run.click();document.getElementById('owgCaseExplain').scrollIntoView({block:'start'});};
            list.appendChild(b);});
          casePanel.replaceChildren(el('h4','Slowest of the '+Number(a.cases_with_feature).toLocaleString()+' cases with '+label),
            r.cases.length?list:el('div','No cases.','muted'),el('div','Choose a case to explain where its time went.','muted'));
        }catch(error){casePanel.replaceChildren(el('div',error.message,'muted'));}
      };
      tag.onclick=async()=>{
        const name=(window.prompt('Tag for the '+a.cases_with_feature+' cases with '+label,'')||'').trim();
        if(!name)return;
        try{const r=await postJSON('/case-tags',withType({tag:name.slice(0,60),cohort:a.cohort,note:('root cause: '+label).slice(0,500)}));
          casePanel.replaceChildren(el('div','Tagged '+Number(r.tagged).toLocaleString()+' cases “'+name.slice(0,60)+'”. Choose the tag under Tagged in any filter.'));}
        catch(error){casePanel.replaceChildren(el('div',error.message,'muted'));}
      };
      save.onclick=async()=>{
        const name=(window.prompt('Name for a new log with the '+a.cases_with_feature+' cases with '+label,label.slice(0,120))||'').trim();
        if(!name)return;
        try{const r=await postJSON('/derive',withType({name:name.slice(0,120),cohort:a.cohort}));
          casePanel.replaceChildren(el('div','Saved '+Number(r.copied.events).toLocaleString()+' events of '+Number(r.derived_from.cases_matched).toLocaleString()+' cases as “'+r.log.name+'”. This log is unchanged.'));}
        catch(error){casePanel.replaceChildren(el('div',error.message,'muted'));}
      };
      if(a.cohort.attribute&&!filterOn()){
        const go=button('Filter','secondary');go.setAttribute('aria-label','Filter every view to the cases with '+label);
        go.onclick=()=>{state.attr={attribute:a.cohort.attribute};analyse().catch(error=>status(error.message));};
        wrap.appendChild(go);
      }
      if(a.cohort.attribute_range||a.cohort.activity){
        const go=button('Filter views','secondary');
        go.setAttribute('aria-label','Filter views to '+a.feature.value+'; replace the case filter');
        go.onclick=async()=>{
          // Association counts are for the full case notion: explicitly replace,
          // rather than silently intersect with, the old workspace filter.
          state.follows=[];state.attr=a.cohort.attribute_range?{attribute_range:{...a.cohort.attribute_range}}:{};state.caseActivity=a.cohort.activity||'';
          const expected=state.generation+1;
          try{
            await analyse();
            if(state.generation===expected){
              const target=document.getElementById('owgCaseFilterStatus');
              if(target){target.scrollIntoView({block:'nearest'});target.focus();}
            }
          }catch(error){status(error.message);}
        };
        wrap.appendChild(go);
      }
      wrap.append(inspect,slowest,tag,save);return wrap;
    }
    run.onclick=async()=>{
      try{
        const params={outcome:outcome.value};
        if(outcome.value==='duration_over'){const h=Number(hours.value);if(!(h>0))throw new Error('Enter the number of hours.');params.threshold_seconds=String(Math.round(h*3600));}
        if(outcome.value==='tagged'){const t=outcomeTag.value.trim();if(!t)throw new Error('Enter the tag of the cases to explain.');params.outcome_tag=t;}
        if(!activityField.hidden){const label=exact.value||activity.value;
          if(!label||Array.from(label).length>200)throw new Error('Choose an activity label of at most 200 characters.');params.activity=label;}
        const r=await getJSON(logPath('root-cause',params));
        const rows=(r.associations||[]).map(a=>[a.feature.kind+': '+a.feature.value,Number(a.cases_with_feature).toLocaleString(),pct(a.outcome_rate_with),pct(a.outcome_rate_without),(a.risk_difference>0?'+':'')+(a.risk_difference*100).toFixed(1)+' pts ['+(a.ci95[0]*100).toFixed(1)+', '+(a.ci95[1]*100).toFixed(1)+']',a.holds_after_bonferroni?'yes':'no',a.cohort?caseActions(a):'—']);
        casePanel.replaceChildren();
        out.replaceChildren(
          el('div','Outcome in '+pct(r.outcome_rate)+' of '+Number(r.cases).toLocaleString()+' cases; '+Number(r.features_tested)+' features tested.','muted'),
          table(['Feature','Cases with it','Outcome with','Outcome without','Difference (95% CI)','Holds after correction','Its cases'],rows),
          ...((r.associations||[]).some(a=>a.cohort?.attribute_range||a.cohort?.activity)?[el('div','Filter views replaces the current case filter with the selected exact activity or numeric range. Save the current filter first to restore it afterward.','muted')]:[]),
          el('div',r.note||'These are associations in this log, not causes. A step can be a consequence of the outcome rather than its reason.','muted'),casePanel);
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  // Plain-language outline of a discovered process tree, one line per step.
  function treeOutline(node,depth=0){
    const pad='  '.repeat(depth);
    if(node.op==='activity')return [pad+node.activity];
    if(node.op==='tau')return [pad+'(skip)'];
    const kids=node.children||[];
    if(node.op==='seq')return node.simplified_from==='parallel'?[pad+'In the most common observed order:',...kids.flatMap(c=>treeOutline(c,depth+1))]:kids.flatMap(c=>treeOutline(c,depth));
    if(node.op==='xor')return [pad+'Choose one of:',...kids.flatMap((c,i)=>[pad+'  Option '+(i+1)+' · '+Number(node.cases[i]).toLocaleString()+' '+(node.unit||'cases')+':',...treeOutline(c,depth+2)])];
    if(node.op==='and')return [pad+'In parallel:',...kids.flatMap((c,i)=>[pad+'  Branch '+(i+1)+':',...treeOutline(c,depth+2)])];
    const [body,redo]=kids;
    return [pad+(node.unstructured?'In any order, as often as needed:':'Repeat as needed ('+Number(node.repeats).toLocaleString()+' repeats):'),...treeOutline(body,depth+1),
      ...(redo&&redo.op!=='tau'?[pad+'  going round again through:',...treeOutline(redo,depth+2)]:[])];
  }

  function discoverySection(){
    const box=section('Discover a process model');box.id='owgDiscovery';
    box.appendChild(el('div','Builds a draft model with decisions, loops and parallel steps from the cases in this log, like an inductive miner. Choices are labelled with the share of cases observed, not with business rules. Open the draft in the process catalog to review it, state the real conditions and export it as BPMN.','muted'));
    const noise=input('owgDiscoveryNoise','range');noise.min='0';noise.max='50';noise.step='5';noise.value='20';
    const acts=input('owgDiscoveryActivities','range');acts.min='10';acts.max='100';acts.step='5';acts.value='100';
    const noiseOut=el('output','20%');noiseOut.id='owgDiscoveryNoiseValue';
    const actsOut=el('output','100%');actsOut.id='owgDiscoveryActivitiesValue';
    noise.oninput=()=>{noiseOut.textContent=noise.value+'%';};acts.oninput=()=>{actsOut.textContent=acts.value+'%';};
    const noiseField=field('Leave out infrequent behaviour below',noise);noiseField.appendChild(noiseOut);
    const actsField=field('Activities covering this share of events',acts);actsField.appendChild(actsOut);
    const run=button('Discover model','secondary');run.id='owgDiscoveryRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(noiseField,actsField,run);
    const statusLine=el('div',null,'muted');statusLine.id='owgDiscoveryStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgDiscoveryResults';
    box.append(row,statusLine,out);
    run.onclick=async()=>{
      const generation=state.generation;
      try{
        run.disabled=true;out.replaceChildren();statusLine.textContent='Discovering…';
        const r=await getJSON(logPath('model-discovery',{noise:String(Number(noise.value)/100),activities:String(Number(acts.value)/100)}));
        if(generation!==state.generation)return;
        const q=r.quality,kinds=r.process_model.activities.map(a=>a.kind);
        const count=kind=>kinds.filter(k=>k===kind).length;
        const loops=r.process_model.activities.filter(a=>a.id.startsWith('repeat_')).length;
        statusLine.textContent='Draft with '+count('activity')+' steps, '+(count('decision')-loops)+' decisions, '+loops+' loops and '+count('parallel_split')+' parallel regions. It fits '+Number(q.fitting_cases).toLocaleString()+' of '+Number(q.checked_cases).toLocaleString()+' checked cases ('+pct(q.fitting_case_share)+').'+(q.activities_hidden?' '+q.activities_hidden+' less frequent activities are hidden.':'');
        const outline=el('pre',treeOutline(r.tree).join('\n'));outline.id='owgDiscoveryOutline';outline.style.cssText='white-space:pre-wrap;max-width:100%;overflow-x:auto';
        out.appendChild(outline);
        if(r.simplifications.length){const list=el('ul');r.simplifications.forEach(n=>list.appendChild(el('li',n)));out.appendChild(list);}
        const excluded=q.cases_excluded_trace_length+q.cases_excluded_variant_limit;
        out.appendChild(el('div',r.interpretation+(excluded?' '+Number(excluded).toLocaleString()+' cases with very long or rare paths were left out of discovery.':''),'muted'));
        out.appendChild(el('div','Recording a source snapshot keeps its digests, selected filters, settings and coverage counts with this imported log; no graph or case copies. The snapshot is deleted with the log. Review and save the graph separately.','muted'));
        const draft=button('Record source snapshot and review draft');draft.id='owgDiscoveryDraft';
        draft.onclick=async()=>{
          try{
            if(!window.owgProcessCatalog?.draftDiscovered)throw new Error('Process catalog unavailable. Reopen the local dashboard.');
            draft.disabled=true;statusLine.textContent='Recording current discovery source metadata…';
            const saved=await (await api('/v1/event-logs/'+encodeURIComponent(r.log.log_id)+'/model-discovery-draft',{
              method:'POST',headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'},
              body:JSON.stringify({expected_model_sha256:r.process_model_sha256,case_type:r.case_notion,
                noise:r.settings.noise,activities:r.settings.activities,cohort:r.cohort})})).json();
            if(generation!==state.generation)return;
            if(window.owgProcessCatalog.draftDiscovered({model:saved.process_model,logName:(saved.log&&saved.log.name)||'',sourceReference:saved.source_snapshot.reference}))statusLine.textContent='Source snapshot recorded. Opened in the process catalog as an unsaved draft.';
            else statusLine.textContent='Source snapshot recorded; existing catalog draft kept.';
          }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
          finally{draft.disabled=false;}
        };
        out.appendChild(draft);
        const petri=button('Download Petri net (PNML)','secondary');petri.id='owgDiscoveryPnml';
        petri.onclick=async()=>{
          try{
            petri.disabled=true;
            const response=await api(logPath('model-discovery.pnml',{noise:String(r.settings.noise),activities:String(r.settings.activities)}));
            download(state.logId+'-discovered.pnml',await response.text(),'application/xml');
            statusLine.textContent='Petri net downloaded ('+(response.headers.get('X-OWG-Petri-Net')||'').replace(/; /g,', ')+'). It opens in ProM, PM4Py and other PNML tools.';
          }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
          finally{petri.disabled=false;}
        };
        out.appendChild(petri);
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}
    };
    return box;
  }

  function waitingSection(inspection){
    const box=section('Processing and waiting time');box.id='owgWaiting';
    box.appendChild(el('div','Separates recorded activity intervals from gaps before they start, from start/complete lifecycle values or an end-time column. Waiting includes nights, weekends and work that was not recorded. Nothing is attributed to a person.','muted'));
    const lifecycle=input('owgWaitingLifecycle','text');lifecycle.value='lifecycle:transition';lifecycle.maxLength=120;
    const end=input('owgWaitingEnd','text','for example end_time');end.maxLength=120;
    const run=button('Show processing and waiting','secondary');run.id='owgWaitingRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Lifecycle attribute',lifecycle),field('Or end-time attribute (with timezone)',end),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgWaitingStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgWaitingResults';
    const review=el('div');review.id='owgWaitingCases';
    const enabled=input('owgWaitingCalendarEnabled','checkbox');
    const days=select('owgWaitingCalendarDays',[['mon','Monday'],['tue','Tuesday'],['wed','Wednesday'],['thu','Thursday'],['fri','Friday'],['sat','Saturday'],['sun','Sunday']]);
    days.multiple=true;days.size=4;for(const option of days.options)option.selected=['mon','tue','wed','thu','fri'].includes(option.value);
    const from=input('owgWaitingCalendarFrom','text','HH:MM');from.value='09:00';from.maxLength=5;
    const until=input('owgWaitingCalendarUntil','text','HH:MM or 24:00');until.value='17:00';until.maxLength=5;
    const zone=input('owgWaitingCalendarZone','text','IANA time zone');zone.value='UTC';zone.maxLength=64;
    const holidays=el('textarea');holidays.id='owgWaitingCalendarHolidays';holidays.rows=2;holidays.maxLength=6000;
    const calendarFields=el('div',null,'row');calendarFields.append(field('Calendar working days',days),field('Calendar opens',from),field('Calendar closes',until),field('Calendar time zone',zone),field('Calendar holidays (YYYY-MM-DD)',holidays));show(calendarFields,false);
    const exportReport=button('Download calendar and elapsed aggregates','secondary');exportReport.id='owgWaitingCalendarExport';exportReport.disabled=true;show(exportReport,false);
    let artifact=null,requestVersion=0,reviewVersion=0;
    const invalidate=()=>{requestVersion++;out.replaceChildren();review.replaceChildren();statusLine.textContent='Settings changed. Run the analysis again.';run.disabled=false;artifact=null;exportReport.disabled=true;};
    for(const control of [lifecycle,end,days,from,until,zone,holidays])control.addEventListener('input',invalidate);
    enabled.addEventListener('change',()=>{show(calendarFields,enabled.checked);show(exportReport,enabled.checked);invalidate();});
    exportReport.onclick=()=>{if(artifact&&enabled.checked)download('working-calendar-aggregates.json',JSON.stringify(artifact,null,2),'application/json');};
    box.append(field('Add a declared working-calendar lens',enabled),calendarFields,row,statusLine,out,review,exportReport);
    function showHotspot(url,hotspot){
      const generation=state.generation,version=requestVersion,selection=++reviewVersion;
      const current=()=>generation===state.generation&&version===requestVersion&&selection===reviewVersion;
      const title=el('h4','Waiting from '+hotspot.from+' to '+hotspot.to);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),controls=el('div',null,'row');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      controls.append(previous,next);review.replaceChildren(title,status,rows,controls);title.focus();
      const cursors=[null];let page=0,following=null;
      async function load(index){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        try{
          const path=new URL(url,window.location.origin);path.pathname+='/cases';
          path.searchParams.set('source',hotspot.from);path.searchParams.set('destination',hotspot.to);
          if(cursors[index]!==null)path.searchParams.set('after',cursors[index]);
          const result=await getJSON(path.pathname+path.search);
          if(!current())return;
          if(!result.available)throw new Error(result.reason);
          page=index;following=result.next_after;
          status.textContent=Number(result.total_cases).toLocaleString()+' matching cases. Showing '+result.cases.length+' on this page.';
          rows.replaceChildren(result.cases.length?table(['Case','Times','Total elapsed waiting','Longest elapsed wait','Evidence'],result.cases.map(item=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+item.case_id);
            open.onclick=()=>inspection.open(item.case_id);
            return [item.case_id,Number(item.instances).toLocaleString(),duration(item.waiting_seconds),duration(item.longest_wait_seconds),open];
          })):el('div','No matching cases on this page.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=page===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(page-1);
      next.onclick=()=>{cursors[page+1]=following;load(page+1);};
      load(0);
    }
    run.onclick=async()=>{
      const generation=state.generation,version=++requestVersion;
      const current=()=>generation===state.generation&&version===requestVersion;
      try{
        run.disabled=true;artifact=null;exportReport.disabled=true;out.replaceChildren();review.replaceChildren();statusLine.textContent='Measuring…';
        const params={lifecycle_attribute:lifecycle.value.trim()||'lifecycle:transition'};
        if(end.value.trim())params.end_attribute=end.value.trim();
        const cal=enabled.checked?simCalendar({days:'every_day',from:from.value,to:until.value,timezone:zone.value,holidays:holidays.value}):null;
        if(cal){const selected=[...days.selectedOptions].map(o=>o.value);if(!selected.length)throw new Error('Choose at least one calendar working day.');cal.hours=Object.fromEntries(selected.map(day=>[day,cal.hours[day]]));}
        const url=logPath('waiting-time',params);
        const r=cal?await (await api(logPath('waiting-time'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...params,working_calendar:cal})})).json():await getJSON(url);
        if(!current())return;
        if(!r.available){
          statusLine.textContent=r.reason;
          if(r.attribute_names.length)out.appendChild(el('div','Attributes in this log: '+r.attribute_names.join(', '),'muted'));
          return;
        }
        const i=r.instances,f=r.flow_efficiency;
        statusLine.textContent=Number(i.with_processing_time).toLocaleString()+' of '+Number(i.total).toLocaleString()+' activity instances have a start and an end'+(f.median===null?'.':'; in the median case, recorded intervals cover '+pct(f.median)+' of the elapsed time.')+(i.unmatched_starts?' '+Number(i.unmatched_starts).toLocaleString()+' starts have no completion yet.':'');
        out.append(
          table(['Activity','Instances','Median processing','90th percentile processing','Median waiting before','Total waiting before','Share of all waiting'],
            r.activities.slice(0,25).map(a=>[a.activity,Number(a.instances).toLocaleString(),duration(a.processing.median_seconds),duration(a.processing.p90_seconds),duration(a.waiting_before.median_seconds),duration(a.waiting_before.total_seconds),pct(a.share_of_all_waiting)])),
          el('h4','Where cases wait'),
          table(['After','Before','Times','Median waiting','Share of all waiting','Evidence'],r.waiting_hotspots.slice(0,10).map(h=>{
            const inspect=button('Inspect cases','secondary');inspect.setAttribute('aria-label','Inspect cases waiting from '+h.from+' to '+h.to);
            inspect.onclick=()=>showHotspot(url,h);
            return [h.from,h.to,Number(h.count).toLocaleString(),duration(h.median_waiting_seconds),pct(h.share_of_all_waiting),inspect];
          })),
          el('div',r.interpretation,'muted'));
        if(r.working_calendar?.available){
          const c=r.working_calendar,flow=c.flow_efficiency;
          out.append(el('h4','Declared working-calendar lens'),
            el('p','Calendar: '+c.calendar.timezone+'. '+c.instances_crossing_closed_time+' recorded intervals cross closed time. These are schedule-adjusted intervals; actual labor, staffing and policy remain unverified. Elapsed metrics above retain all recorded time.'),
            table(['Activity','Median scheduled processing','Median scheduled gap','Total scheduled gap'],c.activities.slice(0,25).map(a=>[a.activity,duration(a.processing.median_seconds),duration(a.waiting_before.median_seconds),duration(a.waiting_before.total_seconds)])),
            el('p','Calendar flow efficiency: '+(flow.median===null?'undefined':pct(flow.median))+'. '+flow.cases_with_zero_calendar_span+' cases have no scheduled span. '+c.interpretation));
          artifact={format:'openworkgraph.working-calendar-aggregate-export.v1',
            log_reference:{log_id:r.log.log_id,case_notion:r.case_notion,imported_at:r.log.imported_at,events:r.log.events,objects:r.log.objects},
            settings:r.settings,mode_used:r.mode_used,working_calendar:c,
            elapsed:{activities:r.activities,waiting_hotspots:r.waiting_hotspots,flow_efficiency:r.flow_efficiency,instances:r.instances},
            activity_labels_included:true,raw_events_exported:false,raw_case_ids_exported:false,resource_names_exported:false,execution_authorized:false};
          exportReport.disabled=false;
          out.appendChild(el('p','Aggregate downloads include activity labels and your declared schedule. Raw events, case IDs and resource fields are excluded.','muted'));
        }else if(r.working_calendar){out.appendChild(el('p',r.working_calendar.reason||'No calendar intervals are available.'));}
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  // Reference categorical palette (validated light-mode slots 1–7) plus a neutral "Other".
  const DOT_COLOURS=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#8a8984'];
  // Pure layout for the dotted chart: pixel positions and axis ticks.
  function dotLayout(result,width,rowHeight){
    const left=8,right=8,top=8,bottom=28,rows=Math.max(1,result.cases.length);
    const height=top+bottom+rows*rowHeight,span=Math.max(1,result.span_seconds),plot=Math.max(1,width-left-right);
    const x=t=>left+t/span*plot;
    const points=[];
    result.cases.forEach((c,row)=>c.points.forEach(([t,slot])=>points.push({x:x(t),y:top+row*rowHeight+rowHeight/2,slot,t,row})));
    const ticks=(width<520?[0,0.5,1]:[0,0.25,0.5,0.75,1]).map(f=>({x:x(f*span),seconds:f*span}));
    return {width,height,points,ticks,top,bottom,left};
  }

  // Performance spectrum: each occurrence of a step-to-step segment as a line from its start to its end.
  const SPECTRUM_COLOURS={fast:'#2a78c5',normal:'#8a8a84',slow:'#d08a1f',very_slow:'#c0392b'};
  const SPECTRUM_NAMES={fast:'Fast',normal:'Normal',slow:'Slow',very_slow:'Very slow'};
  function spectrumSection(inspection){
    const box=section('Performance spectrum');box.id='owgSpectrum';
    box.appendChild(el('div','For the busiest step-to-step segments, each occurrence is a line from when the first step happened (top) to when the next one did (bottom), coloured by how long it took compared with the segment’s other occurrences. Lines that meet at the bottom show work released together; crossing lines show later work overtaking earlier work.','muted'));
    const run=button('Draw performance spectrum','secondary');run.id='owgSpectrumRun';
    const statusLine=el('div',null,'muted');statusLine.id='owgSpectrumStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgSpectrumResults';
    const review=el('div');review.id='owgSpectrumCaseReview';
    box.append(run,statusLine,out,review);
    const pct=x=>x===null?'—':Math.round(x*100)+'%';
    const number=n=>Number(n).toLocaleString();
    const classes=['fast','normal','slow','very_slow'];
    let reviewTurn=0;
    function inspect(url,seg,period,category){
      const generation=state.generation,turn=++reviewTurn;
      const current=()=>generation===state.generation&&turn===reviewTurn;
      const selection={segment:[seg.from,seg.to]};
      if(period!==null){selection.bin=period.bin;selection.class=category;}
      const label=seg.from+' → '+seg.to+(period!==null?' · '+SPECTRUM_NAMES[category]+' · period '+(period.bin+1):'');
      const title=el('h4',label);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      const dateTime=value=>value.replace('T',' ').replace('Z',' UTC');
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        try{
          const spec={...selection,...(cursors[page]!==null?{after:cursors[page]}:{})};
          const r=await (await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
          index=page;following=r.next_after;
          status.textContent=number(r.occurrences)+(r.occurrences===1?' occurrence in ':' occurrences in ')+
            number(r.total)+(r.total===1?' case.':' cases.')+' Showing '+number(r.cases.length)+' on this page.';
          rows.replaceChildren(r.cases.length?table(['Case','Occurrences','First start','Last finish','Median duration','Evidence'],r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,number(c.occurrences),dateTime(c.first_started_at),dateTime(c.last_finished_at),duration(c.median_seconds),open];
          })):el('div','No matching cases.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;statusLine.textContent='Drawing…';out.replaceChildren();
      ++reviewTurn;review.replaceChildren();
      try{
        const url=logPath('performance-spectrum',{top:'6',bins:'30'}),r=await getJSON(url);
        if(generation!==state.generation)return;
        const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';endpoint.searchParams.delete('top');
        const evidenceURL=endpoint.pathname+endpoint.search;
        if(!r.segments.length){statusLine.textContent='No case has two consecutive events.';return;}
        statusLine.textContent=r.segments.length+' of '+r.segments_total+' segments, busiest first.'+(r.cases_before_filter?' Filtered from '+Number(r.cases_before_filter).toLocaleString()+' cases.':'');
        const legend=el('ul');legend.style.cssText='list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:4px 14px;margin:6px 0';
        for(const k of Object.keys(SPECTRUM_COLOURS)){const item=el('li');const sw=el('span');sw.setAttribute('aria-hidden','true');
          sw.style.cssText='display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px;background:'+SPECTRUM_COLOURS[k];
          item.append(sw,document.createTextNode(SPECTRUM_NAMES[k]+' — '+r.classes[k]));legend.appendChild(item);}
        out.appendChild(legend);
        const span=r.bin_width_seconds*r.bins;
        const stamp=s=>new Date(Date.parse(r.time_origin)+s*1000).toISOString().slice(0,23).replace('T',' ');
        const ink=getComputedStyle(box).color||'#52514e';
        for(const seg of r.segments){
          const title=el('h4',seg.from+' → '+seg.to);title.style.margin='12px 0 4px';
          const width=Math.max(260,Math.min(1000,out.clientWidth||600)),height=90,pad=8;
          const canvas=document.createElement('canvas');const ratio=window.devicePixelRatio||1;
          canvas.width=width*ratio;canvas.height=height*ratio;canvas.style.cssText='width:'+width+'px;height:'+height+'px;display:block';
          canvas.setAttribute('role','img');
          canvas.setAttribute('aria-label','Performance spectrum of '+seg.from+' to '+seg.to+': '+seg.occurrences+' occurrences, '+pct(seg.fifo_share)+' finish in start order, '+pct(seg.batched_share)+' released together. The table below gives the figures.');
          const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
          const end=Math.max(span,...seg.lines.map(l=>l.end));
          const x=t=>pad+(width-2*pad)*t/end;
          ctx.strokeStyle='rgba(128,128,128,0.5)';ctx.beginPath();ctx.moveTo(pad,pad);ctx.lineTo(width-pad,pad);ctx.moveTo(pad,height-pad);ctx.lineTo(width-pad,height-pad);ctx.stroke();
          ctx.lineWidth=1;
          for(const l of seg.lines){ctx.strokeStyle=SPECTRUM_COLOURS[l.class];ctx.globalAlpha=0.7;ctx.beginPath();ctx.moveTo(x(l.start),pad);ctx.lineTo(x(l.end),height-pad);ctx.stroke();}
          ctx.globalAlpha=1;ctx.fillStyle=ink;ctx.font='11px system-ui,sans-serif';ctx.textAlign='left';
          const d=seg.duration_seconds;
          const facts=el('div',seg.occurrences.toLocaleString()+' occurrences'+(seg.lines_sampled?' ('+seg.lines.length+' drawn)':'')+' · median '+duration(d.median)+', 25th–75th percentile '+duration(d.p25)+'–'+duration(d.p75)+', 95th '+duration(d.p95)+' · '+pct(seg.fifo_share)+' finish in start order · '+pct(seg.batched_share)+' released together (≥'+r.batch_min+' ending at one instant).','muted');
          const open=button('Inspect cases','secondary');
          open.setAttribute('aria-label','Inspect cases from '+seg.from+' to '+seg.to);
          open.onclick=()=>inspect(evidenceURL,seg,null,null);
          const busy=seg.bins.filter(b=>Object.values(b.counts).some(n=>n));
          const tbl=table(['Started from (UTC)','Fast','Normal','Slow','Very slow','Median'],busy.map(b=>{
            const start=stamp(b.bin*r.bin_width_seconds);
            return [start,...classes.map(k=>{
              const n=b.counts[k];if(!n)return '0';
              const pick=button(number(n),'secondary');
              pick.setAttribute('aria-label','Inspect '+number(n)+' '+SPECTRUM_NAMES[k].toLowerCase()+' in period '+(b.bin+1)+' starting '+start+' UTC');
              pick.onclick=()=>inspect(evidenceURL,seg,b,k);return pick;
            }),duration(b.median_seconds)];
          }));
          const more=document.createElement('details');more.appendChild(el('summary','Occurrences per period'));more.appendChild(tbl);
          out.append(title,canvas,facts,open,more);
        }
        out.appendChild(el('div',r.caveat,'muted'));
        statusLine.textContent+=' Time runs from '+stamp(0)+' to '+stamp(span)+'.';
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function heuristicsSection(inspection){
    const box=section('Heuristics net');box.id='owgHeuristics';
    box.appendChild(el('div','Build a model from directly following activities. The miner uses the dependency and loop thresholds to keep arrows, with optional fallback connections. Split and join labels use the observed frequency of both branch orders.','muted'));
    const dep=select('owgHeuristicsDependency',[['0.5','0.5 (default)'],['0.7','0.7'],['0.9','0.9 (strict)'],['0.3','0.3 (loose)']]);
    const and=select('owgHeuristicsAnd',[['0.65','0.65 (default)'],['0.5','0.5'],['0.8','0.8'],['1','1.0']]);
    const loop=select('owgHeuristicsLoop',[['0.5','0.5 (default)'],['0.75','0.75'],['0.9','0.9'],['1','1.0']]);
    const connected=input('owgHeuristicsConnected','checkbox');connected.checked=true;
    const support=input('owgHeuristicsSupport','number');support.min='1';support.max='1000000';support.step='1';support.value='1';
    const run=button('Discover heuristics net','secondary');run.id='owgHeuristicsRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Dependency threshold',dep),field('AND threshold',and),
      field('Back-and-forth loop threshold',loop),field('Seen at least (times)',support),field('Keep best fallback connections',connected),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgHeuristicsStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const graphHost=el('div');graphHost.id='owgHeuristicsGraph';
    const out=el('div');out.id='owgHeuristicsResults';
    const review=el('div');review.id='owgHeuristicsCaseReview';
    box.append(row,statusLine,graphHost,out,review);
    const kind=k=>({and:'AND (parallel)',xor:'XOR (choice)',mixed:'Mixed'})[k]||k;
    const number=n=>Number(n).toLocaleString();
    let reviewTurn=0;
    function evidenceButton(url,group,activities,label){
      const pick=button('Inspect cases','secondary');pick.setAttribute('aria-label','Inspect cases for '+label);
      pick.onclick=()=>{
        const generation=state.generation,turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
        const title=el('h4',label);title.tabIndex=-1;
        const status=el('div');status.setAttribute('role','status');
        const rows=el('div'),nav=el('div',null,'row');
        const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
        nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
        const cursors=[null];let index=0,following=null;
        const time=value=>value.replace('T',' ').replace('Z',' UTC');
        async function load(page){
          previous.disabled=next.disabled=true;status.textContent='Loading cases…';rows.inert=true;
          try{
            const spec={group,activities,...(cursors[page]!==null?{after:cursors[page]}:{})};
            const r=await (await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
            if(!current())return;
            index=page;following=r.next_after;
            status.textContent=number(r.occurrences)+(r.occurrences===1?' occurrence in ':' occurrences in ')+
              number(r.total)+(r.total===1?' case.':' cases.')+' Showing '+number(r.cases.length)+' on this page.';
            rows.replaceChildren(r.cases.length?table(['Case','Occurrences','First occurrence start','Last occurrence start','Evidence'],r.cases.map(c=>{
              const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
              open.onclick=()=>inspection.open(c.case_id);
              return [c.case_id,number(c.occurrences),time(c.first_at),time(c.last_at),open];
            })):el('div','No matching cases.','muted'));
          }catch(error){if(current())status.textContent=error.message;}
          finally{if(current()){rows.inert=false;previous.disabled=index===0;next.disabled=following===null;}}
        }
        previous.onclick=()=>load(index-1);
        next.onclick=()=>{cursors[index+1]=following;load(index+1);};
        load(0);
      };
      return pick;
    }
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;statusLine.textContent='Discovering…';graphHost.replaceChildren();out.replaceChildren();
      ++reviewTurn;review.replaceChildren();
      try{
        if(!support.value||!support.validity.valid)throw new Error('Enter a whole number from 1 to 1,000,000 for minimum occurrences.');
        const settings={dependency_threshold:dep.value,and_threshold:and.value,loop_two_threshold:loop.value,
          min_occurrences:String(Number(support.value)),all_connected:connected.checked};
        const url=logPath('heuristics-net',settings),r=await getJSON(url);
        if(generation!==state.generation)return;
        const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
        for(const key of Object.keys(settings))endpoint.searchParams.delete(key);
        const evidenceURL=endpoint.pathname+endpoint.search;
        const arrows=r.edges.filter(e=>e.dependency!==null);
        statusLine.textContent=arrows.length+' arrows kept between '+r.activities.length+' activities, covering '+Math.round((r.coverage.transitions_on_kept_arrows_share||0)*100)+'% of recorded transitions; '+r.splits.length+' splits, '+r.joins.length+' joins, '+r.loops.length+' loops.'+(r.cases_before_filter?' Filtered from '+Number(r.cases_before_filter).toLocaleString()+' cases.':'');
        const draw=window.owgProcessGraph&&window.owgProcessGraph.drawObserved;
        if(draw&&r.activities.length){try{const drawing=draw({activities:r.activities,transitions:arrows,cases:[]},()=>{},()=>{});drawing.id='owgHeuristicsDrawing';graphHost.appendChild(drawing);}
          catch(_){graphHost.appendChild(el('div','The graph could not be drawn; the arrows are listed below.','muted'));}}
        const gates=[...r.splits.map(s=>['Split after',s]),...r.joins.map(j=>['Join before',j])];
        if(gates.length){out.appendChild(el('h4','Splits and joins'));
          out.appendChild(table(['Where','Activity','Branches','Type'],gates.map(([w,g])=>[w,g.activity,g.branches.join(', '),kind(g.type)])));}
        if(r.loops.length){out.appendChild(el('h4','Loops'));
          out.appendChild(table(['Kind','Activities','Occurrences','Measure','Evidence'],r.loops.map(l=>{
            const label=l.kind==='self'?l.activities[0]+' repeated':l.activities.join(' ⇄ ');
            return [l.kind==='self'?'Repeats itself':'Back and forth',l.activities.join(' ⇄ '),number(l.occurrences),String(l.measure),
              evidenceButton(evidenceURL,l.kind==='self'?'transition':'length_two',l.kind==='self'?[l.activities[0],l.activities[0]]:l.activities,label)];
          })));}
        const more=document.createElement('details');more.appendChild(el('summary','All arrows'));
        more.appendChild(table(['From','To','Occurrences','Dependency','Kept because','Evidence'],r.edges.map(e=>{
          const group=e.dependency!==null?'transition':e.kept_by;
          const activities=group==='transition'?[e.from,e.to]:[group==='start'?e.to:e.from];
          const label=group==='transition'?e.from+' → '+e.to:(group==='start'?'Start at ':'End at ')+activities[0];
          return [e.from,e.to,number(e.transition_count),e.dependency===null?'—':String(e.dependency),
            ({threshold:'above threshold',best_connection:'best connection',short_loop:'short loop',start:'case starts here',end:'case ends here'})[e.kept_by],
            evidenceButton(evidenceURL,group,activities,label)];
        })));
        out.append(more,el('div',r.note,'muted'));
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function breakdownSection(activityNames,inspection){
    const box=section('Breakdown table');box.id='owgBreakdown';
    box.appendChild(el('div','Case figures by one or two dimensions, like a pivot table: choose what goes in the rows and, optionally, the columns. Less common values are grouped as (other); cells with fewer than 5 cases are marked *.','muted'));
    const builtIn=[['start_month','Month the case started'],['start_weekday','Weekday the case started'],['start_activity','First activity'],['end_activity','Last activity'],['duration_band','Case duration (bands)'],['rework','Rework (repeats an activity or not)']];
    const rowsPick=select('owgBreakdownRows',builtIn),colsPick=select('owgBreakdownColumns',[['','(none)'],...builtIn]);colsPick.value='end_activity';
    const measures=[['cases','Cases'],['share','Share of cases'],['median_hours','Median duration'],['p90_hours','90th percentile duration'],['rework_rate','Share of cases with rework'],['events_per_case','Events per case'],['with_activity_share','Share doing an activity']];
    const measurePick=select('owgBreakdownMeasure',measures);
    const activityPick=select('owgBreakdownActivity',activityNames.slice(0,500).map(a=>[a,a]));
    const activityField=field('Activity',activityPick);activityField.hidden=true;
    measurePick.onchange=()=>{activityField.hidden=measurePick.value!=='with_activity_share';};
    const run=button('Show breakdown','secondary');run.id='owgBreakdownRun';
    const csv=button('Download as CSV','secondary');csv.id='owgBreakdownCsv';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Rows',rowsPick),field('Columns',colsPick),field('Figure',measurePick),activityField,run,csv);
    const statusLine=el('div',null,'muted');statusLine.id='owgBreakdownStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgBreakdownResults';
    const review=el('div');review.id='owgBreakdownCaseReview';
    box.append(row,statusLine,out,review);
    // Attribute dimensions are listed once the section exists; the built-in ones work without them.
    const generation=state.generation;
    getJSON(logPath('breakdown/dimensions',{})).then(r=>{
      if(generation!==state.generation)return;
      const extra=(r.dimensions||[]).filter(d=>d.dimension.includes(':')).map(d=>[d.dimension,d.label.charAt(0).toUpperCase()+d.label.slice(1)]);
      if(!extra.length)return;
      const keep=[rowsPick.value,colsPick.value];
      setOptions(rowsPick,[...builtIn,...extra]);setOptions(colsPick,[['','(none)'],...builtIn,...extra]);
      rowsPick.value=keep[0];colsPick.value=keep[1];
    }).catch(()=>{});
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    const hrs=x=>x===null||x===undefined?'—':(x<48?(Math.round(x*10)/10)+' h':(Math.round(x/24*10)/10)+' d');
    const show={cases:x=>Number(x).toLocaleString(),share:pct,median_hours:hrs,p90_hours:hrs,rework_rate:pct,events_per_case:x=>String(Math.round(x*10)/10),with_activity_share:pct};
    let reviewTurn=0;
    function inspect(url,cell,label,withActivity){
      const turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4',label);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const selection={row_key:cell.row_key??null,column_key:cell.column_key??null};
      const cursors=[null];let index=0,following=null;
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        try{
          const spec={...selection,...(cursors[page]!==null?{after:cursors[page]}:{})};
          const r=await (await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
          index=page;following=r.next_after;
          status.textContent=Number(r.total).toLocaleString()+(r.total===1?' case.':' cases.')+' Showing '+r.cases.length+' on this page.';
          const headers=['Case','Duration','Events','Repeated an activity',...(withActivity?['Did '+withActivity]:[]),'Evidence'];
          rows.replaceChildren(r.cases.length?table(headers,r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,duration(c.duration_seconds),Number(c.events).toLocaleString(),c.rework?'Yes':'No',
              ...(withActivity?[c.did_activity?'Yes':'No']:[]),open];
          })):el('div','No matching cases.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    csv.onclick=async()=>{
      if(colsPick.value&&colsPick.value===rowsPick.value){statusLine.textContent='Choose two different dimensions.';return;}
      const params={rows:rowsPick.value};if(colsPick.value)params.columns=colsPick.value;
      if(measurePick.value==='with_activity_share'&&activityPick.value)params.with_activity=activityPick.value;
      csv.disabled=true;
      try{const response=await api(logPath('breakdown.csv',params));const blob=await response.blob();
        const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=(state.logId||'log')+'-breakdown.csv';
        document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
        statusLine.textContent='Downloaded the breakdown with one line per cell followed by the totals.';}
      catch(error){statusLine.textContent=error.message;}
      finally{csv.disabled=false;}};
    run.onclick=async()=>{
      ++reviewTurn;review.replaceChildren();
      if(colsPick.value&&colsPick.value===rowsPick.value){statusLine.textContent='Choose two different dimensions.';out.replaceChildren();return;}
      const measure=measurePick.value;
      const params={rows:rowsPick.value};if(colsPick.value)params.columns=colsPick.value;
      if(measure==='with_activity_share'){if(!activityPick.value){statusLine.textContent='Choose an activity.';return;}params.with_activity=activityPick.value;}
      run.disabled=true;statusLine.textContent='Grouping cases…';out.replaceChildren();
      try{
        const url=logPath('breakdown',params),r=await getJSON(url);
        if(generation!==state.generation)return;
        const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
        const evidenceURL=endpoint.pathname+endpoint.search;
        const fmt=show[measure],cols=r.columns?r.column_values:['All cases'];
        const cells=new Map();
        r.cells.forEach(c=>{if(!cells.has(c.row))cells.set(c.row,new Map());cells.get(c.row).set(c.column,c);});
        const values=r.cells.map(c=>c[measure]).filter(v=>v!==null&&v!==undefined),top=Math.max(...values,0);
        const shown=c=>{
          if(!c)return '';
          const wrap=el('div'),value=fmt(c[measure])+(c.small?' *':'');
          const label=[c.row,c.column].filter(v=>v!==undefined).join(' · ')||r.total_label;
          const open=button(value,'secondary');
          open.setAttribute('aria-label','Inspect cases for '+label+' ('+value+')');
          open.onclick=()=>inspect(evidenceURL,c,label,r.with_activity);wrap.appendChild(open);
          if(top>0&&c[measure]!==null){const bar=el('div');bar.setAttribute('aria-hidden','true');bar.style.cssText='height:4px;background:currentColor;opacity:.35;margin-top:4px;width:'+Math.round(c[measure]/top*100)+'%';wrap.appendChild(bar);}
          return wrap;};
        const totals=new Map(r.row_totals.map(t=>[t.row,t]));
        const header=[r.rows.label.charAt(0).toUpperCase()+r.rows.label.slice(1),...(r.columns?cols:[]),r.total_label];
        const body=r.row_values.map(v=>[v,...(r.columns?cols.map(c=>shown(cells.get(v)?.get(c))):[]),shown(totals.get(v))]);
        const colTotals=new Map(r.column_totals.map(t=>[t.column,t]));
        body.push([r.total_label,...(r.columns?cols.map(c=>shown(colTotals.get(c))):[]),shown(r.overall)]);
        statusLine.textContent=measures.find(m=>m[0]===measure)[1]+' by '+r.rows.label+(r.columns?' and '+r.columns.label:'')+', over '+Number(r.cases).toLocaleString()+' cases. Overall: '+fmt(r.overall[measure])+'.';
        out.append(table(header,body),el('div',r.caveat,'muted'));
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function activityProfileSection(activityNames){
    const box=section('Activity profile');box.id='owgActivityProfile';
    box.appendChild(el('div','One activity from every side: how often it happens and repeats, what comes before and after it, when in the week and day, and how long cases with it take.','muted'));
    const pick=select('owgActivityProfilePick',activityNames.slice(0,500).map(a=>[a,a]));
    const run=button('Show profile','secondary');run.id='owgActivityProfileRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activity',pick),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgActivityProfileStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgActivityProfileResults';
    box.append(row,statusLine,out);
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    const hrs=x=>x===null||x===undefined?'—':(x<48?(Math.round(x*10)/10)+' h':(Math.round(x/24*10)/10)+' d');
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;statusLine.textContent='Profiling…';out.replaceChildren();
      try{
        const r=await getJSON(logPath('activity-profile',{profile_activity:pick.value}));
        if(generation!==state.generation)return;
        const c=r.case_hours;
        statusLine.textContent=r.activity+' happens in '+Number(r.cases_with_activity).toLocaleString()+' of '+Number(r.cases).toLocaleString()+' cases ('+pct(r.share_of_cases)+'), '+r.occurrences_per_case+' times per case on average; '+pct(r.repeat_share)+' of those cases repeat it. Cases with it take a median '+hrs(c.with_activity.median)+', without it '+hrs(c.without_activity.median)+'.';
        const side=(title,list)=>{out.appendChild(el('h4',title));out.appendChild(table(['Activity','Times','Share','Median gap'],list.map(n=>[n.activity,Number(n.times).toLocaleString(),pct(n.share),hrs(n.median_hours)])));};
        side('Comes after',r.comes_after);side('Comes before',r.comes_before);
        out.appendChild(el('h4','When it happens'));
        out.appendChild(table(['Weekday','Times'],r.by_weekday.map(d=>[d.day,Number(d.times).toLocaleString()])));
        const busy=r.by_hour.filter(h=>h.times).sort((a,b)=>b.times-a.times).slice(0,3).map(h=>String(h.hour).padStart(2,'0')+':00 ('+h.times+')');
        if(busy.length)out.appendChild(el('div','Busiest hours (UTC): '+busy.join(', ')+'.'));
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function cooccurrenceSection(inspection){
    const box=section('Activities that happen together');box.id='owgCooccurrence';
    box.appendChild(el('div','For the most common activities: when a case has the activity in the row, how often it also has the one in the column, in any order. Lift above 1 means the two go together more than chance would give; 0 means they never occur in the same case, which often marks alternative branches.','muted'));
    const sizePick=select('owgCooccurrenceSize',[['8','8 most common activities'],['12','12 most common activities'],['20','20 most common activities']]);sizePick.value='12';
    const run=button('Show activities that happen together','secondary');run.id='owgCooccurrenceRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities',sizePick),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgCooccurrenceStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgCooccurrenceResults';
    box.append(row,statusLine,out);
    const pct=x=>x===null||x===undefined?'—':Math.round(x*100)+'%';
    run.onclick=async()=>{
      const generation=state.generation,cohort=globalCohort();
      run.disabled=true;statusLine.textContent='Comparing activities…';out.replaceChildren();
      try{
        const r=await getJSON(logPath('co-occurrence',{limit:sizePick.value}));
        if(generation!==state.generation)return;
        statusLine.textContent=r.activities.length+' activities over '+Number(r.cases).toLocaleString()+' cases. '+(r.never_together.length?r.never_together.length+' pairs never occur in the same case.':'Every pair occurs together in some case.');
        const names=r.activities.map(a=>a.activity);
        const cell=w=>{const s=el('span',pct(w.share_of_a));s.title=w.both+' cases'+(w.lift===null?'':', lift '+w.lift);return s;};
        const t=table(['When a case has … it also has',...names],r.matrix.map(m=>[m.activity+' ('+Number(m.cases).toLocaleString()+')',...m.with.map(cell)]));t.id='owgCooccurrenceMatrix';
        out.appendChild(t);
        const options=names.map(name=>[name,name]);
        const first=select('owgCooccurrenceFirst',options),second=select('owgCooccurrenceSecond',options);
        second.selectedIndex=1;
        const both=button('Inspect cases with both','secondary');
        const firstOnly=button('Inspect cases with first only','secondary');
        const secondOnly=button('Inspect cases with second only','secondary');
        const counts=el('div',null,'muted');counts.setAttribute('role','status');
        const byName=new Map(r.matrix.map(item=>[item.activity,item]));
        const update=()=>{
          const a=byName.get(first.value),b=byName.get(second.value);
          if(a===b){
            both.disabled=firstOnly.disabled=secondOnly.disabled=true;
            counts.textContent='Choose two different activities.';
            return;
          }
          const intersection=a.with.find(item=>item.activity===b.activity).both;
          const aOnly=a.cases-intersection,bOnly=b.cases-intersection;
          both.disabled=intersection===0;firstOnly.disabled=aOnly===0;secondOnly.disabled=bOnly===0;
          counts.textContent=intersection.toLocaleString()+' cases with both, '+aOnly.toLocaleString()+' with first only, '+bOnly.toLocaleString()+' with second only.';
        };
        first.onchange=second.onchange=update;
        both.onclick=()=>inspection.activityPresence([first.value,second.value],[],cohort);
        firstOnly.onclick=()=>inspection.activityPresence([first.value],[second.value],cohort);
        secondOnly.onclick=()=>inspection.activityPresence([second.value],[first.value],cohort);
        const pair=el('div',null,'row');pair.style.alignItems='end';
        pair.append(field('First activity',first),field('Second activity',second),both,firstOnly,secondOnly);
        update();
        out.append(pair,counts);
        if(r.go_together.length){out.appendChild(el('h4','Go together most'));
          out.appendChild(table(['Activities','Cases with both','Lift'],r.go_together.map(p=>[p.a+' + '+p.b,Number(p.both).toLocaleString(),String(p.lift)])));}
        if(r.never_together.length){out.appendChild(el('h4','Never in the same case'));
          const nt=table(['Activities','Cases with each'],r.never_together.map(p=>[p.a+' / '+p.b,Number(p.cases_a).toLocaleString()+' / '+Number(p.cases_b).toLocaleString()]));nt.id='owgCooccurrenceNever';out.appendChild(nt);}
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function glossarySection(){
    const box=section('Activity glossary');box.id='owgGlossary';
    box.appendChild(el('div','Write down what each activity means, which team usually does it, the system it happens in and how automated it is, so the map can be read by anyone. Your notes are stored locally with the log, never change its events, and agents can read them when you share the log.','muted'));
    const load=button('Show glossary','secondary');load.id='owgGlossaryLoad';
    const csv=button('Download glossary (CSV)','secondary');csv.id='owgGlossaryCsv';
    const upload=document.createElement('input');upload.type='file';upload.id='owgGlossaryImport';upload.accept='.csv,text/csv';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(load,csv,field('Import glossary (CSV)',upload));
    const statusLine=el('div',null,'muted');statusLine.id='owgGlossaryStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgGlossaryResults';
    box.append(row,statusLine,out);
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/activity-glossary';
    const levels=[['','Not set'],['manual','Manual'],['partly','Partly automated'],['automated','Automated'],['candidate','Automation candidate']];
    const summary=r=>r.coverage.described+' of '+r.coverage.activities_in_log+' activities described, covering '+Math.round(r.coverage.events_described_share*100)+'% of events.'+(r.automation.candidate?' '+r.automation.candidate+' marked as automation candidates.':'');
    const render=r=>{
      out.replaceChildren();
      const t=table(['Activity','Events','Description','Team','System','Automation',''],r.activities.map(()=>['','','','','','','']));t.id='owgGlossaryTable';
      t.querySelector('tbody').querySelectorAll('tr').forEach((tr,i)=>{const a=r.activities[i];
        tr.cells[0].textContent=a.activity+(a.in_log?'':' (no longer in the log)');
        tr.cells[1].textContent=Number(a.events).toLocaleString();
        const text=(value,label,max)=>{const node=document.createElement('input');node.type='text';node.value=value||'';node.maxLength=max;node.setAttribute('aria-label',label+' for '+a.activity);return node;};
        const description=text(a.description,'Description',2000),team=text(a.owner_team,'Team',200),system=text(a.system,'System',200);
        const automation=document.createElement('select');setOptions(automation,levels);automation.value=a.automation||'';automation.setAttribute('aria-label','Automation for '+a.activity);
        const save=button('Save','secondary');save.setAttribute('aria-label','Save notes for '+a.activity);
        save.onclick=async()=>{save.disabled=true;
          try{const body={activity:a.activity,description:description.value,owner_team:team.value,system:system.value,automation:automation.value||null};
            const updated=await (await api(base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
            statusLine.textContent=(updated.removed?'Cleared notes for ':'Saved notes for ')+a.activity+'. '+summary(updated);}
          catch(error){statusLine.textContent=error.message;}
          finally{save.disabled=false;}};
        tr.cells[2].appendChild(description);tr.cells[3].appendChild(team);tr.cells[4].appendChild(system);tr.cells[5].appendChild(automation);tr.cells[6].appendChild(save);});
      out.append(t,el('div',r.caveat,'muted'));};
    load.onclick=async()=>{
      const generation=state.generation;load.disabled=true;statusLine.textContent='Loading the glossary…';out.replaceChildren();
      try{const r=await getJSON(base());if(generation!==state.generation)return;statusLine.textContent=summary(r);render(r);}
      catch(error){statusLine.textContent=error.message;}
      finally{load.disabled=false;}};
    upload.onchange=async()=>{const file=upload.files&&upload.files[0];if(!file)return;
      const generation=state.generation;upload.disabled=true;statusLine.textContent='Importing the glossary…';
      try{const r=await (await api(base()+'/import',{method:'POST',headers:{'Content-Type':'text/csv'},body:await file.text()})).json();
        if(generation!==state.generation)return;
        statusLine.textContent='Imported '+r.imported+' entries.'+(r.skipped_not_in_log_total?' Skipped '+r.skipped_not_in_log_total+' activities not in this log.':'')+' '+summary(r);render(r);}
      catch(error){statusLine.textContent=error.message;}
      finally{upload.disabled=false;upload.value='';}};
    csv.onclick=async()=>{csv.disabled=true;
      try{const response=await api(base()+'.csv');downloadBlob(await response.blob(),(state.logId||'log')+'-activity-glossary.csv');statusLine.textContent='Glossary downloaded.';}
      catch(error){statusLine.textContent=error.message;}
      finally{csv.disabled=false;}};
    return box;
  }

  function pathExplorerSection(inspection){
    const box=section('Path explorer');box.id='owgPaths';
    box.appendChild(el('div','Walk the process one step at a time: see which activities come next, in how many cases and how fast, then choose one to go deeper.','muted'));
    const anywhere=document.createElement('input');anywhere.type='checkbox';anywhere.id='owgPathsAnywhere';
    const anywhereLabel=el('label',' From anywhere in the case, not only its start');anywhereLabel.prepend(anywhere);
    const start=button('Explore from the start','secondary');start.id='owgPathsStart';
    const back=button('Back one step','secondary');back.id='owgPathsBack';back.disabled=true;
    const row=el('div',null,'row');row.style.alignItems='center';row.append(start,back,anywhereLabel);
    const trail=el('div');trail.id='owgPathsTrail';
    const statusLine=el('div',null,'muted');statusLine.id='owgPathsStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgPathsResults';
    const review=el('div');review.id='owgPathsCaseReview';
    box.append(row,trail,statusLine,out,review);
    let path=[],anchored=0,request=0,reviewTurn=0;
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    const hrs=x=>x===null||x===undefined?'—':(x<48?(Math.round(x*10)/10)+' h':(Math.round(x/24*10)/10)+' d');
    function showCases(url,branch,reportCurrent){
      const turn=++reviewTurn;
      const current=()=>reportCurrent()&&turn===reviewTurn;
      const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
      const title=el('h4',branch.is_end?'Cases ending here':'Cases continuing to '+branch.activity);title.tabIndex=-1;
      const message=el('div');message.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);review.replaceChildren(title,message,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      async function load(page){
        previous.disabled=next.disabled=true;message.textContent='Loading cases…';
        const selection=branch.is_end?{ends_here:true}:{activity:branch.activity};
        if(cursors[page]!==null)selection.after=cursors[page];
        try{
          const response=await api(endpoint.pathname+endpoint.search,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(selection)});
          const result=await response.json();
          if(!current())return;
          index=page;following=result.next_after;
          message.textContent=Number(result.total).toLocaleString()+(result.total===1?' case':' cases')+' in this branch. Showing '+result.cases.length+' on this page.';
          rows.replaceChildren(result.cases.length?table(['Case','Events','Evidence'],result.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,Number(c.events).toLocaleString(),open];
          })):el('div','No cases on this page.','muted'));
        }catch(error){if(current())message.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    const show=async()=>{
      if(anywhere.checked&&!path.length){statusLine.textContent='Choose a first step from the start, then look for it anywhere.';anywhere.checked=false;}
      const generation=state.generation,turn=++request;
      const current=()=>generation===state.generation&&turn===request;
      start.disabled=back.disabled=true;statusLine.textContent='Following cases…';out.replaceChildren();review.replaceChildren();
      trail.textContent=path.length?'Path: '+(anywhere.checked?'… → ':'(start) → ')+path.join(' → '):'';
      try{
        const params={step:path,anchor:anywhere.checked?'anywhere':'start'};
        if(anywhere.checked)params.anchor_steps=anchored;
        const url=logPath('next-steps',params),r=await getJSON(url);
        if(!current())return;
        statusLine.textContent=(path.length?Number(r.cases_at_point).toLocaleString()+' cases ('+pct(r.share_of_cases)+') reach this point'+(r.hours_to_reach_point?', in a median '+hrs(r.hours_to_reach_point.median):'')+'.':Number(r.cases).toLocaleString()+' cases start with these activities.')+(r.next_total>r.next.length?' Showing the '+r.next.length+' most common of '+r.next_total+' next steps.':'');
        if(!r.next.length)return;
        const t=table(['Next','Cases','Share','From previous (median)','To case end (median)','Actions'],r.next.map(x=>[x.is_end?'Case ends here':x.activity,Number(x.cases).toLocaleString(),pct(x.share),x.hours_from_previous?hrs(x.hours_from_previous.median):'—',x.hours_to_case_end?hrs(x.hours_to_case_end.median):'—','']));
        t.querySelectorAll('tbody tr').forEach((tr,i)=>{const x=r.next[i];
          const bar=el('div');bar.setAttribute('aria-hidden','true');bar.style.cssText='height:4px;background:currentColor;opacity:.35;margin-top:4px;width:'+Math.round((x.share||0)*100)+'%';tr.cells[2].appendChild(bar);
          if(!x.is_end){const go=button('Go to '+x.activity,'secondary');go.onclick=()=>{path=[...path,x.activity];show();};tr.cells[5].appendChild(go);}
          const inspect=button('Inspect cases','secondary');
          inspect.setAttribute('aria-label',x.is_end?'Inspect cases ending here':'Inspect cases continuing to '+x.activity);
          inspect.onclick=()=>showCases(url,x,current);tr.cells[5].appendChild(inspect);
        });
        out.appendChild(t);
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current()){start.disabled=false;back.disabled=!path.length;}}};
    start.onclick=()=>{path=[];anchored=0;anywhere.checked=false;show();};
    back.onclick=()=>{path=path.slice(0,-1);anchored=Math.min(anchored,path.length);if(!path.length)anywhere.checked=false;show();};
    anywhere.onchange=()=>{anchored=anywhere.checked?path.length:0;if(path.length)show();else if(anywhere.checked){anywhere.checked=false;statusLine.textContent='Choose a first step from the start, then look for it anywhere.';}};
    return box;
  }

  function dueDatesSection(activityNames,inspection){
    const box=section('Due dates');box.id='owgDue';
    box.appendChild(el('div','Did the step that matters (a payment, a shipment) happen by the date it was due? The due date comes from an attribute such as a payment due date column, or is a fixed number of days after the case started.','muted'));
    const activity=select('owgDueActivity',activityNames.slice(0,500).map(a=>[a,a]));
    if(activityNames.length)activity.value=activityNames[activityNames.length-1];
    const mode=select('owgDueMode',[['attribute','From an attribute'],['days','Days after the case starts']]);
    const attribute=input('owgDueAttribute','text','e.g. due_date');attribute.maxLength=200;attribute.autocomplete='off';
    const days=input('owgDueDays','number');days.min='0.01';days.step='any';days.value='30';
    const early=select('owgDueEarly',[['','No early window'],['3','Early if more than 3 days before'],['7','Early if more than 7 days before'],['14','Early if more than 14 days before']]);
    const attributeField=field('Due-date attribute',attribute),daysField=field('Days after start',days);
    const sync=()=>{attributeField.hidden=mode.value!=='attribute';daysField.hidden=mode.value!=='days';};mode.onchange=sync;sync();
    const run=button('Check due dates','secondary');run.id='owgDueRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activity that is due',activity),field('Due date',mode),attributeField,daysField,field('Early window',early),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgDueStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgDueResults';
    const review=el('div');review.id='owgDueCaseReview';
    const alertCount=input('owgDueAlertCount','number');alertCount.min='0';alertCount.step='1';alertCount.value='0';
    const alertSave=button('Alert me','secondary');alertSave.id='owgDueAlertSave';
    const alertMessage=el('div',null,'muted');alertMessage.id='owgDueAlertMessage';alertMessage.setAttribute('role','status');alertMessage.setAttribute('aria-live','polite');
    const alertRow=el('div',null,'row');alertRow.style.alignItems='end';alertRow.append(field('Signal when open overdue cases are more than',alertCount),alertSave);
    box.append(row,statusLine,out,review,alertRow,alertMessage);
    alertSave.onclick=async()=>{
      const when={kind:'due_date',activity:activity.value,metric:'open_overdue_cases',operator:'above',threshold:Math.max(0,Math.floor(Number(alertCount.value)||0))};
      if(mode.value==='attribute'){if(!attribute.value.trim()){alertMessage.textContent='Enter the name of the due-date attribute.';return;}when.due_attribute=attribute.value.trim();}
      else{if(!(Number(days.value)>0)){alertMessage.textContent='Enter a number of days above 0.';return;}when.due_days=Number(days.value);}
      const sig={name:('Overdue '+activity.value).slice(0,120),when};if(state.caseType)sig.case_type=state.caseType;
      alertSave.disabled=true;
      try{await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sig)});
        document.querySelectorAll('#owgEventLogSignals').forEach(node=>node.dispatchEvent(new CustomEvent('owg-signals-changed')));
        alertMessage.textContent='Saved as a signal. Use Check signals to evaluate it; it counts open cases past their due date each time.';}
      catch(error){alertMessage.textContent=error.message;}
      finally{alertSave.disabled=false;}};
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    const names={on_time:'On time',early:'Early',late:'Late',open_overdue:'Open, overdue',open_not_due:'Open, not yet due',no_due_date:'No due date',unreadable_due_date:'Unreadable due date'};
    let reviewTurn=0;
    function showGroup(url,group){
      const generation=state.generation,turn=++reviewTurn;
      const current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4',names[group]);title.tabIndex=-1;
      const message=el('div');message.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);
      review.replaceChildren(title,message,rows,nav);
      title.focus();
      const cursors=[null];let index=0,following=null;
      const date=value=>value?value.replace('T',' ').slice(0,16)+' UTC':'—';
      async function load(page){
        previous.disabled=next.disabled=true;
        message.textContent='Loading cases…';
        try{
          const path=new URL(url,window.location.origin);
          path.pathname+='/cases';
          path.searchParams.set('due_group',group);
          if(cursors[page]!==null)path.searchParams.set('after',cursors[page]);
          const result=await getJSON(path.pathname+path.search);
          if(!current())return;
          index=page;following=result.next_after;
          message.textContent=Number(result.total).toLocaleString()+' cases in this group. Showing '+result.cases.length+' on this page.';
          rows.replaceChildren(result.cases.length?table(['Case','Due','Done','Days from due','Evidence'],result.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,date(c.due_at),date(c.done_at),c.days_from_due??'—',open];
          })):el('div','No cases on this page.','muted'));
        }catch(error){if(current())message.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    run.onclick=async()=>{
      ++reviewTurn;review.replaceChildren();
      const params={due_activity:activity.value};
      if(mode.value==='attribute'){if(!attribute.value.trim()){statusLine.textContent='Enter the name of the due-date attribute.';out.replaceChildren();return;}params.due_attribute=attribute.value.trim();}
      else{if(!(Number(days.value)>0)){statusLine.textContent='Enter a number of days above 0.';out.replaceChildren();return;}params.due_days=days.value;}
      if(early.value)params.early_days=early.value;
      const generation=state.generation;run.disabled=true;statusLine.textContent='Checking due dates…';out.replaceChildren();
      try{
        const url=logPath('due-dates',params);
        const r=await getJSON(url);
        if(generation!==state.generation)return;
        const g=r.groups;
        statusLine.textContent=r.activity+' happened on time in '+pct(r.on_time_share_of_done)+' of the cases where it happened ('+Number(g.on_time).toLocaleString()+' on time, '+Number(g.late).toLocaleString()+' late'+(r.early_days?', '+Number(g.early).toLocaleString()+' early':'')+'). '+Number(g.open_overdue).toLocaleString()+' open cases are overdue.'+(r.days_late.median!==null?' Late cases were a median '+r.days_late.median+' days late.':'')+((g.no_due_date+g.unreadable_due_date)?' '+Number(g.no_due_date+g.unreadable_due_date).toLocaleString()+' cases have no readable due date.':'');
        out.appendChild(table(['Group','Cases','Evidence'],Object.keys(names).filter(k=>g[k]||k==='on_time'||k==='late').map(k=>{
          const inspect=button('Inspect cases','secondary');inspect.setAttribute('aria-label','Inspect '+names[k]+' cases');
          inspect.disabled=!g[k];inspect.onclick=()=>showGroup(url,k);
          return [names[k],Number(g[k]).toLocaleString(),inspect];
        })));
        if(r.by_due_month.length>1){out.appendChild(el('h4','By due month'));
          out.appendChild(table(['Due month','Due','On time','Late','Open, overdue','On time of done'],r.by_due_month.map(m=>[m.due_month,String(m.due),String(m.on_time),String(m.late),String(m.open_overdue),pct(m.on_time_share_of_done)])));}
        if(r.most_late_cases&&r.most_late_cases.length){const more=document.createElement('details');more.appendChild(el('summary','Most late cases ('+r.most_late_cases.length+')'));
          more.appendChild(table(['Case','Days late','Done','Due'],r.most_late_cases.map(c=>[String(c.case_id),String(c.days_late),c.done_at.replace('T',' ').slice(0,16),c.due_at.replace('T',' ').slice(0,16)])));out.appendChild(more);}
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function unusualCasesSection(){
    const box=section('Unusual cases');box.id='owgUnusual';
    box.appendChild(el('div','Cases that stand out from the rest of this selection, each with the reasons: a rare path, unusually slow or fast, unusually many events, heavy rework, or an activity almost no other case has. Unusual is not wrong; this is a list to look at.','muted'));
    const label={rare_path:'Rare path',unusually_slow:'Unusually slow',unusually_fast:'Unusually fast',unusually_long:'Unusually many events',heavy_rework:'Heavy rework',rare_activity:'Rare activity'};
    const signal=select('owgUnusualSignal',[['','Any reason'],...Object.entries(label)]);
    const limit=select('owgUnusualLimit',[['25','25'],['50','50'],['100','100']]);
    const run=button('Find unusual cases','secondary');run.id='owgUnusualRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Reason',signal),field('Cases per page',limit),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgUnusualStatus';statusLine.setAttribute('role','status');
    const out=el('div');out.id='owgUnusualResults';
    box.append(row,statusLine,out);
    const generation=state.generation,logId=state.logId,caseType=state.caseType;
    const number=n=>Number(n).toLocaleString();
    let turn=0;
    limit.onchange=signal.onchange=()=>{
      ++turn;run.disabled=false;out.replaceChildren();statusLine.textContent='Find unusual cases with these settings.';
    };
    async function scan(offset){
      const request=++turn;
      const current=()=>generation===state.generation&&request===turn;
      const params={limit:limit.value,offset};
      if(signal.value)params.signal=signal.value;
      run.disabled=true;statusLine.textContent='Comparing cases…';out.replaceChildren();
      try{
        const r=await getJSON(logPath('unusual-cases',params));
        if(!current())return;
        const page=r.page;
        if(offset&&offset>=page.total)return scan(Math.max(0,Math.floor((page.total-1)/page.limit)*page.limit));
        const counts=Object.entries(r.by_signal).map(([key,n])=>label[key]+' '+number(n)).join(', ');
        statusLine.textContent=number(r.unusual_cases)+' of '+number(r.cases)+' cases stand out'+(counts?' ('+counts+')':'')+'. The median case takes '+r.baseline.median_hours+' h and has '+r.baseline.median_events+' events.';
        const shown=r.top.length?'Showing '+number(offset+1)+'–'+number(offset+r.top.length)+' of '+number(page.total)+' matching cases.':'No unusual cases match this selection.';
        const pageStatus=el('div',shown,'muted');pageStatus.setAttribute('role','status');
        out.appendChild(pageStatus);
        if(!r.top.length)return;
        const ids=r.top.map(c=>c.case_id),selected=new Set(ids),checks=[];
        const t=table(['Case','Score','Why','Events','Hours','Explain','Select'],r.top.map(c=>{
          const id=c.case_id,explain=button('Explain','secondary');
          explain.setAttribute('aria-label','Explain case '+id);
          explain.onclick=()=>{
            const field=document.querySelector('#owgCaseExplainId'),go=document.querySelector('#owgCaseExplainRun');
            if(!field||!go)return;
            field.value=id;go.click();document.querySelector('#owgCaseExplain').scrollIntoView({block:'start'});
          };
          const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=true;
          checkbox.setAttribute('aria-label','Select case '+id);
          checkbox.onchange=()=>{if(checkbox.checked)selected.add(id);else selected.delete(id);updateTag();};
          checks.push(checkbox);
          return [id,String(c.score),c.reasons.map(x=>label[x.signal]+': '+x.detail).join('; '),String(c.events),String(c.hours),explain,checkbox];
        }));
        const previous=button('Previous page','secondary'),next=button('Next page','secondary');
        previous.disabled=offset===0;next.disabled=page.next_offset===null;
        previous.onclick=()=>scan(offset-page.limit);next.onclick=()=>scan(page.next_offset);
        const nav=el('div',null,'row');nav.append(previous,next);
        out.append(t,nav);
        const tagName=input('owgUnusualTagName','text');tagName.value='unusual';tagName.maxLength=60;tagName.autocomplete='off';
        const tagRun=button('','secondary');tagRun.id='owgUnusualTag';
        const all=button('Select all on page','secondary'),clear=button('Clear selection','secondary');
        function updateTag(){
          tagRun.textContent='Tag '+number(selected.size)+' selected '+(selected.size===1?'case':'cases');
          tagRun.disabled=selected.size===0;all.disabled=selected.size===ids.length;clear.disabled=selected.size===0;
        }
        const selectPage=checked=>{
          checks.forEach(box=>{box.checked=checked;});
          selected.clear();if(checked)ids.forEach(id=>selected.add(id));updateTag();
        };
        all.onclick=()=>selectPage(true);clear.onclick=()=>selectPage(false);updateTag();
        const tagMessage=el('div',null,'muted');tagMessage.id='owgUnusualTagMessage';tagMessage.setAttribute('role','status');
        const tagRow=el('div',null,'row');tagRow.style.alignItems='end';tagRow.append(all,clear,field('Tag',tagName),tagRun);
        out.append(tagRow,tagMessage,el('div',r.caveat,'muted'));
        tagRun.onclick=async()=>{
          if(!tagName.value.trim()){tagMessage.textContent='Enter a tag.';return;}
          tagRun.disabled=all.disabled=clear.disabled=true;checks.forEach(box=>{box.disabled=true;});
          try{
            const spec={tag:tagName.value.trim(),note:'listed as unusual',case_ids:[...selected]};if(caseType)spec.case_type=caseType;
            const result=await (await api('/v1/event-logs/'+encodeURIComponent(logId)+'/case-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
            if(generation!==state.generation)return;
            document.querySelectorAll('#owgCaseTags').forEach(node=>node.dispatchEvent(new CustomEvent('owg-tags-changed')));
            if(current())tagMessage.textContent='Tagged '+number(result.tagged)+' cases “'+result.tag+'”. Use the tag in the case filter to focus every analysis on them.';
          }catch(error){if(current())tagMessage.textContent=error.message;}
          finally{checks.forEach(box=>{box.disabled=false;});updateTag();}
        };
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())run.disabled=false;}
    }
    run.onclick=()=>scan(0);
    return box;
  }

  function funnelSection(activityNames){
    const box=section('Milestone funnel');box.id='owgFunnel';
    box.appendChild(el('div','Choose activities in order to see how many cases reach each one after the previous ones, how long that takes, and what the cases that stopped did last. Other activities may happen in between.','muted'));
    const options=[['','(choose an activity)'],...activityNames.slice(0,500).map(a=>[a,a])];
    const steps=el('div',null,'row');steps.style.alignItems='end';steps.id='owgFunnelSteps';
    const pickers=[];
    const addStep=()=>{if(pickers.length>=8)return;const n=pickers.length+1;const pick=select('owgFunnelStep'+n,options);
      if(activityNames[n-1])pick.value=activityNames[n-1];pickers.push(pick);steps.appendChild(field('Step '+n,pick));more.disabled=pickers.length>=8;};
    const more=button('Add step','secondary');more.id='owgFunnelAdd';more.onclick=addStep;
    const windowPick=select('owgFunnelWindow',[['','No time limit'],['24','Within 24 hours of the previous step'],['168','Within 7 days'],['720','Within 30 days']]);
    const evidenceStep=select('owgFunnelEvidenceStep',[['','No case evidence'],...Array.from({length:8},(_,i)=>[String(i+1),'Step '+(i+1)])]);
    const evidenceStatus=select('owgFunnelEvidenceStatus',[['reached','Reached step'],['dropped','Stopped before step']]);
    const run=button('Show funnel','secondary');run.id='owgFunnelRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Conversion window',windowPick),field('Case evidence',evidenceStep),field('Evidence group',evidenceStatus),more,run);
    const statusLine=el('div',null,'muted');statusLine.id='owgFunnelStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgFunnelResults';
    box.append(steps,row,statusLine,out);
    for(let i=0;i<3;i++)addStep();
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    const hrs=x=>x===null||x===undefined?'—':(x<48?(Math.round(x*10)/10)+' h':(Math.round(x/24*10)/10)+' d');
    run.onclick=async()=>{
      const chosen=pickers.map(p=>p.value).filter(Boolean);
      if(chosen.length<2){statusLine.textContent='Choose at least two steps.';out.replaceChildren();return;}
      if(evidenceStep.value&&(Number(evidenceStep.value)>chosen.length||(evidenceStep.value==='1'&&evidenceStatus.value==='dropped'))){statusLine.textContent='Choose a valid evidence step; drop-off requires step 2 or later.';out.replaceChildren();return;}
      const generation=state.generation;run.disabled=true;statusLine.textContent='Following cases…';out.replaceChildren();
      try{
        const params={step:chosen};if(windowPick.value)params.within_hours=windowPick.value;
        if(evidenceStep.value){params.evidence_step=evidenceStep.value;params.evidence_status=evidenceStatus.value;}
        const r=await getJSON(logPath('funnel',params));
        if(generation!==state.generation)return;
        const first=r.steps[0],last=r.steps[r.steps.length-1];
        statusLine.textContent=Number(last.cases).toLocaleString()+' of '+Number(first.cases).toLocaleString()+' cases that reached '+first.activity+' went on to reach '+last.activity+' ('+pct(r.completed.share_of_first_step)+'), in a median '+hrs(r.completed.hours_first_to_last.median)+'.'+(r.largest_drop?' The largest drop is before step '+r.largest_drop.to_step+' ('+Number(r.largest_drop.cases).toLocaleString()+' cases).':'');
        const t=table(['Step','Reached','Of step 1','From previous','Dropped','Median time from previous','Stopped cases did last'],r.steps.map(x=>[x.step+'. '+x.activity,Number(x.cases).toLocaleString(),pct(x.share_of_first_step),x.step===1?'—':pct(x.conversion_from_previous),x.step===1?'—':Number(x.dropped_after_previous).toLocaleString()+(x.stopped_cases_possibly_open?' ('+x.stopped_cases_possibly_open+' possibly open)':''),x.step===1?'—':hrs(x.hours_from_previous.median),x.step===1?'—':(x.stopped_cases_last_activity.map(a=>a.activity+' ('+a.cases+')').join(', ')||'—')]));
        t.querySelector('tbody').querySelectorAll('tr').forEach((tr,i)=>{const bar=el('div');bar.setAttribute('aria-hidden','true');bar.style.cssText='height:4px;background:currentColor;opacity:.35;margin-top:4px;width:'+Math.round((r.steps[i].share_of_first_step||0)*100)+'%';tr.cells[1].appendChild(bar);});
        out.appendChild(t);
        if(r.evidence){const e=r.evidence;out.appendChild(el('h4','Case evidence'));
          out.appendChild(el('div','Showing '+e.cases.length+' of '+e.total+' cases '+(e.status==='reached'?'reaching step ':'stopping before step ')+e.step+(e.truncated?' (first 50 by case ID).':'.'),'muted'));
          if(e.cases.length)out.appendChild(table(['Case ID','Last recorded activity','Steps reached'],e.cases.map(c=>[c.case_id,c.last_activity,c.steps_reached])));}
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function modelChangeSection(){
    const box=section('Model change');box.id='owgModelChange';
    box.appendChild(el('div','How the discovered model differs before and after a date: which activities now follow each other differently, which activities are new or gone, and how well each period\'s cases fit the other period\'s model.','muted'));
    const mode=select('owgModelChangeMode',[['periods','Before and after a date'],['groups','Two groups of cases']]);
    const groupA=input('owgModelChangeCohortAAttribute','text','e.g. region=EU');groupA.maxLength=400;groupA.autocomplete='off';
    const groupB=input('owgModelChangeCohortBAttribute','text','e.g. region=US');groupB.maxLength=400;groupB.autocomplete='off';
    const split=input('owgModelChangeSplit','date');
    const noise=select('owgModelChangeNoise',[['0.2','Leave out paths under 20% (default)'],['0','Keep every path'],['0.4','Leave out paths under 40%']]);
    const run=button('Compare periods','secondary');run.id='owgModelChangeRun';
    const splitField=field('Split at (empty: median case start)',split),aField=field('Group A (attribute=value)',groupA),bField=field('Group B (attribute=value)',groupB);
    const sync=()=>{splitField.hidden=mode.value!=='periods';aField.hidden=bField.hidden=mode.value!=='groups';};mode.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Compare',mode),splitField,aField,bField,field('Models',noise),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgModelChangeStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgModelChangeResults';
    box.append(row,statusLine,out);
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    run.onclick=async()=>{
      if(mode.value==='groups'){
        if(!groupA.value.includes('=')||!groupB.value.includes('=')){statusLine.textContent='Enter both groups as attribute=value.';out.replaceChildren();return;}
        const generation=state.generation;run.disabled=true;statusLine.textContent='Discovering both groups…';out.replaceChildren();
        try{
          const r=await getJSON(logPath('model-comparison',{group_a:groupA.value.trim(),group_b:groupB.value.trim(),noise:noise.value}));
          if(generation!==state.generation)return;
          const c=r.cross_fit,A=r.groups.a,B=r.groups.b;
          statusLine.textContent=A.filter+' ('+Number(A.cases).toLocaleString()+' cases) against '+B.filter+' ('+Number(B.cases).toLocaleString()+'): '+r.orderings.changed+' of '+r.orderings.pairs_compared+' activity orderings differ. '+B.filter+' cases fit the '+A.filter+' model '+pct(c.b_cases_on_a_model.fitness)+', against '+pct(c.a_cases_on_a_model.fitness)+' for its own cases.';
          const a=r.activities;
          if(a.only_a.length||a.only_b.length)out.appendChild(el('div',(a.only_a.length?'Only in '+A.filter+': '+a.only_a.join(', ')+'. ':'')+(a.only_b.length?'Only in '+B.filter+': '+a.only_b.join(', ')+'.':'')));
          if(r.differences.length){out.appendChild(el('h4','Orderings that differ'));
            out.appendChild(table(['Activity','Activity',A.filter,B.filter],r.differences.map(x=>[x.from,x.to,x.a,x.b])));}
          out.appendChild(el('h4','Cross-fit'));
          out.appendChild(table(['Cases','Model','Alignment fitness','Follow it exactly'],[
            [A.filter,A.filter,pct(c.a_cases_on_a_model.fitness),pct(c.a_cases_on_a_model.fitting_case_share)],
            [B.filter,A.filter,pct(c.b_cases_on_a_model.fitness),pct(c.b_cases_on_a_model.fitting_case_share)],
            [B.filter,B.filter,pct(c.b_cases_on_b_model.fitness),pct(c.b_cases_on_b_model.fitting_case_share)],
            [A.filter,B.filter,pct(c.a_cases_on_b_model.fitness),pct(c.a_cases_on_b_model.fitting_case_share)]]));
          out.appendChild(el('div',r.caveat,'muted'));
        }catch(error){statusLine.textContent=error.message;}
        finally{run.disabled=false;}
        return;
      }
      const generation=state.generation;run.disabled=true;statusLine.textContent='Discovering both periods…';out.replaceChildren();
      try{
        const params={noise:noise.value};if(split.value)params.split=split.value+'T00:00:00Z';
        const r=await getJSON(logPath('model-change',params));
        if(generation!==state.generation)return;
        const c=r.cross_fit,drop=r.fitness_drop_on_earlier_model;
        statusLine.textContent='Split at '+r.split.slice(0,10)+(r.split_chosen==='given'?'':' (median case start)')+': '+Number(r.periods.before.cases).toLocaleString()+' cases before, '+Number(r.periods.after.cases).toLocaleString()+' after. '+r.orderings.changed+' of '+r.orderings.pairs_compared+' activity orderings changed. Later cases fit the earlier model '+pct(c.after_cases_on_before_model.fitness)+', against '+pct(c.before_cases_on_before_model.fitness)+' for the earlier cases'+(drop!==null?(drop>0.02?' — the way the work runs changed.':' — little change in how the work runs.'):'.');
        const a=r.activities;
        if(a.only_before.length||a.only_after.length)out.appendChild(el('div',(a.only_after.length?'New after the split: '+a.only_after.join(', ')+'. ':'')+(a.only_before.length?'Gone after the split: '+a.only_before.join(', ')+'.':'')));
        if(r.changes.length){out.appendChild(el('h4','Orderings that changed'));
          out.appendChild(table(['Activity','Activity','Before','After'],r.changes.map(x=>[x.from,x.to,x.before,x.after])));}
        out.appendChild(el('h4','Cross-fit'));
        out.appendChild(table(['Cases','Model','Alignment fitness','Follow it exactly'],[
          ['Before','Before',pct(c.before_cases_on_before_model.fitness),pct(c.before_cases_on_before_model.fitting_case_share)],
          ['After','Before',pct(c.after_cases_on_before_model.fitness),pct(c.after_cases_on_before_model.fitting_case_share)],
          ['After','After',pct(c.after_cases_on_after_model.fitness),pct(c.after_cases_on_after_model.fitting_case_share)],
          ['Before','After',pct(c.before_cases_on_after_model.fitness),pct(c.before_cases_on_after_model.fitting_case_share)]]));
        out.appendChild(el('div','→: the first is followed by the second; ←: the reverse; ||: both orders or in parallel; #: not directly related. '+r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function modelQualitySection(){
    const box=section('Model quality');box.id='owgModelQuality';
    box.appendChild(el('div','How good the discovered model is at several noise levels: fitness (can it replay the cases), precision (does it allow little that no case did), their balance (F-score), generalization (is each part backed by many cases) and simplicity (how tangled it is). Use it to choose the noise setting of model discovery.','muted'));
    const levels=select('owgModelQualityLevels',[['0,0.1,0.2,0.3,0.5','0, 10%, 20%, 30% and 50% (default)'],['0,0.05,0.1,0.15,0.2','Fine steps up to 20%'],['0,0.2,0.4,0.6,0.8','Coarse steps up to 80%']]);
    const run=button('Score models','secondary');run.id='owgModelQualityRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Noise levels',levels),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgModelQualityStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgModelQualityResults';
    box.append(row,statusLine,out);
    const pct=x=>x===null||x===undefined?'—':Math.round(x*1000)/10+'%';
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;statusLine.textContent='Discovering and scoring…';out.replaceChildren();
      try{
        const r=await getJSON(logPath('model-quality',{levels:levels.value}));
        if(generation!==state.generation)return;
        const best=r.levels.find(l=>l.noise===r.best_f_score_noise);
        statusLine.textContent='Scored '+r.levels.length+' models on '+Number(r.cases).toLocaleString()+' cases.'+(best?' The best balance of fitness and precision is at noise '+pct(best.noise)+' (F-score '+pct(best.f_score)+', fitness '+pct(best.fitness)+', precision '+pct(best.precision)+').':'')+(r.cases_excluded?' '+Number(r.cases_excluded).toLocaleString()+' cases beyond the length or variant limit were left out.':'');
        const t=table(['Noise','Fitness','Precision','F-score','Generalization','Simplicity','Places / transitions'],r.levels.map(l=>[pct(l.noise)+(l.noise===r.best_f_score_noise?' (best F-score)':''),pct(l.fitness),pct(l.precision),pct(l.f_score),pct(l.generalization),pct(l.simplicity),l.places+' / '+l.transitions]));
        out.appendChild(t);
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function footprintsSection(inspection){
    const box=section('Footprints');box.id='owgFootprints';
    box.appendChild(el('div','For every pair of activities, whether one directly follows the other (→), both orders occur (||) or neither (#): once from the recorded cases and once from the discovered model draft. Cells where they differ show orderings the model leaves out or allows without any case taking them.','muted'));
    const noise=select('owgFootprintsNoise',[['0.2','Model drops orderings under 20% (default)'],['0','Model keeps every ordering'],['0.4','Model drops orderings under 40%']]);
    const coverage=select('owgFootprintsActivityShare',[['1','All activities'],['0.9','90% of events'],['0.75','75% of events'],['0.5','50% of events'],['0.25','25% of events']]);
    const run=button('Compare footprints','secondary');run.id='owgFootprintsRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Model',noise),field('Activity coverage',coverage),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgFootprintsStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgFootprintsResults';
    const review=el('div');review.id='owgFootprintsCaseReview';
    box.append(row,statusLine,out,review);
    const number=n=>Number(n).toLocaleString();
    let reviewTurn=0;
    function evidenceButton(url,retained,group,activities,label){
      const open=button('Inspect cases','secondary');
      open.setAttribute('aria-label','Inspect cases for '+label);
      open.onclick=()=>{
        const generation=state.generation,turn=++reviewTurn;
        const current=()=>generation===state.generation&&turn===reviewTurn;
        const heading=el('h4',label);heading.tabIndex=-1;
        const status=el('div');status.setAttribute('role','status');
        const rows=el('div'),nav=el('div',null,'row');
        const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
        nav.append(previous,next);review.replaceChildren(heading,status,rows,nav);heading.focus();
        const cursors=[null];let index=0,following=null;
        async function load(page){
          previous.disabled=next.disabled=true;status.textContent='Loading cases…';
          try{
            const body={retained_activities:retained,group,activities,...(cursors[page]!==null?{after:cursors[page]}:{})};
            const r=await (await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
            if(!current())return;
            index=page;following=r.next_after;
            status.textContent=number(r.occurrences)+(r.occurrences===1?' occurrence in ':' occurrences in ')+
              number(r.total)+(r.total===1?' case.':' cases.');
            if(r.projected_occurrences)status.append(' Excluding activities from the model changes '+number(r.projected_occurrences)+(r.projected_occurrences===1?' occurrence.':' occurrences.'));
            rows.replaceChildren(r.cases.length?table(['Case','Occurrences','Occurrences affected by excluded activities','Events','Evidence'],r.cases.map(c=>{
              const inspect=button('Open case','secondary');inspect.setAttribute('aria-label','Open case '+c.case_id);
              inspect.onclick=()=>inspection.open(c.case_id);
              return [c.case_id,number(c.occurrences),number(c.projected_occurrences),number(c.events),inspect];
            })):el('div','No matching cases.','muted'));
          }catch(error){if(current())status.textContent=error.message;}
          finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
        }
        previous.onclick=()=>load(index-1);
        next.onclick=()=>{cursors[index+1]=following;load(index+1);};
        load(0);
      };
      return open;
    }
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;statusLine.textContent='Comparing…';out.replaceChildren();
      ++reviewTurn;review.replaceChildren();
      try{
        const url=logPath('footprints',{noise:noise.value,activities:coverage.value}),r=await getJSON(url);
        if(generation!==state.generation)return;
        const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
        endpoint.searchParams.delete('noise');endpoint.searchParams.delete('activities');
        const evidenceURL=endpoint.pathname+endpoint.search;
        const c=r.conformance;
        statusLine.textContent=Math.round((c.footprint_fitness||0)*100)+'% of '+c.cells+' cells agree across '+r.activities.length+' activities in '+Number(r.cases).toLocaleString()+' cases; '+c.log_pairs_not_in_model+' orderings only in the log, '+c.model_pairs_not_in_log+' only in the model.'+(r.cases_before_filter?' Filtered from '+Number(r.cases_before_filter).toLocaleString()+' cases.':'');
        if(r.differences.length){out.appendChild(el('h4','Where log and model differ'));
          out.appendChild(table(['From','To','Seen in','Cases','Log','Model','Evidence'],r.differences.map(d=>[
            d.from,d.to,d.kind==='in_log_not_in_model'?'Log only':'Model only',number(d.cases),d.log,d.model,
            d.cases?evidenceButton(evidenceURL,r.activities,'transition',[d.from,d.to],d.from+' → '+d.to):''
          ])));
          if(r.differences_total>r.differences.length)out.appendChild(el('div','Showing '+number(r.differences.length)+' of '+number(r.differences_total)+' differences.','muted'));}
        const boundaries=[['start',r.start_activities],['end',r.end_activities]].flatMap(([group,boundary])=>boundary.log_only.map(activity=>[
          group==='start'?'Start':'End',activity,number(boundary.log_only_counts[activity]),evidenceButton(evidenceURL,r.activities,group,[activity],(group==='start'?'Start: ':'End: ')+activity)
        ]));
        if(boundaries.length)out.append(el('h4','Start and end activities only in the log'),table(['Position','Activity','Cases','Evidence'],boundaries));
        const grid=document.createElement('details');grid.appendChild(el('summary','Footprint matrix (log / model)'));
        const t=table(['',...r.activities],r.matrix.map(row=>[row.activity,...row.cells.map(x=>x.log===x.model?x.log:x.log+' / '+x.model)]));
        t.id='owgFootprintsMatrix';
        r.matrix.forEach((row,i)=>row.cells.forEach((x,j)=>{if(x.log!==x.model){const cell=t.querySelector('tbody').rows[i].cells[j+1];cell.style.fontWeight='600';cell.setAttribute('aria-label',row.activity+' to '+r.activities[j]+': log '+x.log+', model '+x.model+' (differs)');}}));
        grid.appendChild(t);
        out.append(grid,el('div',r.caveat,'muted'));
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}};
    return box;
  }

  function petriReplaySection(inspection){
    const box=section('Check against a Petri net');box.id='owgPetri';
    const netsBase=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/reference-nets';
    box.appendChild(el('div','Replay the recorded cases on a Petri net from ProM, PM4Py, Apromore or another tool (a .pnml file), or on the net discovered here. Tokens that are missing or left behind show where the cases and the net disagree. The file is read for this check and not stored.','muted'));
    const file=input('owgPetriFile','file');file.accept='.pnml,application/xml,text/xml';
    const run=button('Replay on this net','secondary');run.id='owgPetriRun';
    const own=button('Replay on the discovered net','secondary');own.id='owgPetriDiscovered';
    const happy=button('Check against the most common path','secondary');happy.id='owgPetriHappy';
    const align=button('Explain each case step by step','secondary');align.id='owgPetriAlign';align.disabled=true;
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Petri net (.pnml)',file),run,own,happy,align);
    const statusLine=el('div',null,'muted');statusLine.id='owgPetriStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgPetriResults';
    const aligned=el('div');aligned.id='owgPetriAlignResults';
    const alertShare=select('owgPetriAlertShare',[['0.1','more than 10%'],['0.2','more than 20%'],['0.05','more than 5%'],['0.3','more than 30%']]);
    const alertWindow=select('owgPetriAlertWindow',[['30','started in the last 30 days'],['7','started in the last 7 days'],['','in the whole log']]);
    const alertSave=button('Alert me','secondary');alertSave.id='owgPetriAlertSave';alertSave.disabled=true;
    const alertMessage=el('div',null,'muted');alertMessage.id='owgPetriAlertMessage';alertMessage.setAttribute('role','status');alertMessage.setAttribute('aria-live','polite');
    const alertRow=el('div',null,'row');alertRow.style.alignItems='end';alertRow.append(field('Signal when cases not fitting this net are',alertShare),field('Among cases',alertWindow),alertSave);
    box.append(row,statusLine,out,aligned,alertRow,alertMessage);
    // Reference nets saved with this log: keep the net just checked, or check again against a saved one.
    const savedPick=select('owgPetriSaved',[]);const savedRun=button('Replay on saved net','secondary');savedRun.id='owgPetriSavedRun';
    const savedDelete=button('Delete saved net','secondary');savedDelete.id='owgPetriSavedDelete';
    const saveName=input('owgPetriSaveName','text','e.g. Target process 2025');saveName.maxLength=120;saveName.autocomplete='off';
    const saveRun=button('Save this net','secondary');saveRun.id='owgPetriSave';saveRun.disabled=true;
    const savedMessage=el('div',null,'muted');savedMessage.id='owgPetriSavedMessage';savedMessage.setAttribute('role','status');savedMessage.setAttribute('aria-live','polite');
    const savedRow=el('div',null,'row');savedRow.style.alignItems='end';savedRow.append(field('Saved reference nets',savedPick),savedRun,savedDelete,field('Save the net just checked as',saveName),saveRun);
    box.append(savedRow,savedMessage);
    const refreshSaved=async()=>{try{const r=await getJSON(netsBase());
        savedPick.replaceChildren(...r.nets.map(n=>{const o=document.createElement('option');o.value=n.net_id;o.textContent=n.name+' ('+n.transitions+' transitions)';return o;}));
        savedRun.disabled=savedDelete.disabled=!r.nets.length;if(!r.nets.length){const o=document.createElement('option');o.value='';o.textContent='(none saved yet)';savedPick.appendChild(o);}}
      catch(error){savedMessage.textContent=error.message;}};
    refreshSaved();
    savedRun.onclick=async()=>{if(!savedPick.value)return;
      try{const response=await api(netsBase()+'/'+encodeURIComponent(savedPick.value)+'.pnml');await replay(await response.text(),savedPick.selectedOptions[0].textContent.replace(/ \(\d+ transitions\)$/,''));}
      catch(error){statusLine.textContent=error.message;}};
    savedDelete.onclick=async()=>{if(!savedPick.value||!window.confirm('Delete the saved net “'+savedPick.selectedOptions[0].textContent+'”?'))return;
      try{await api(netsBase()+'/'+encodeURIComponent(savedPick.value)+'/delete',{method:'POST'});savedMessage.textContent='Saved net deleted.';await refreshSaved();}
      catch(error){savedMessage.textContent=error.message;}};
    saveRun.onclick=async()=>{if(!lastNet)return;
      if(!saveName.value.trim()){savedMessage.textContent='Enter a name for the net.';return;}
      saveRun.disabled=true;
      try{await api(netsBase(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:saveName.value.trim(),pnml:lastNet.body})});
        savedMessage.textContent='Saved “'+saveName.value.trim()+'” with this log.';saveName.value='';await refreshSaved();}
      catch(error){savedMessage.textContent=error.message;}
      finally{saveRun.disabled=!lastNet;}};
    const pct=x=>Math.round((x||0)*1000)/10+'%';
    const sectionGeneration=state.generation;
    const inspectVariant=(variant,net,report)=>{
      const inspect=button('Inspect cases','secondary');
      const preview=variant.path.slice(0,5).join(' → ')+(variant.path.length>5?' … ('+variant.path.length+' events)':'');
      inspect.setAttribute('aria-label','Inspect cases on '+preview);
      inspect.onclick=()=>inspection.variant(variant.variant_id,null,net.cohort,report+' on '+net.label+'. Exact path for '+preview+'.');
      return inspect;
    };
    let lastNet=null;
    const replay=async(body,label)=>{
      if(sectionGeneration!==state.generation)return;
      const generation=sectionGeneration,cohort=globalCohort();
      run.disabled=own.disabled=happy.disabled=align.disabled=alertSave.disabled=true;aligned.replaceChildren();alertMessage.textContent='';lastNet=null;statusLine.textContent='Replaying on '+label+'…';out.replaceChildren();
      try{
        const r=await (await api(logPath('petri-net-replay',{}),{method:'POST',headers:{'Content-Type':'application/xml'},body})).json();
        if(generation!==state.generation)return;
        lastNet={body,label,generation,cohort};
        const f=r.fitness;
        statusLine.textContent='Fitness '+pct(f.log)+' on '+label+': '+Number(f.fitting_cases).toLocaleString()+' of '+Number(r.cases).toLocaleString()+' cases ('+pct(f.fitting_case_share)+') replay without missing or remaining tokens.'+(r.precision.precision!==null?' Precision '+pct(r.precision.precision)+'.':'')+' The net has '+r.net.places+' places and '+r.net.transitions+' transitions ('+r.net.silent_transitions+' silent).'+(r.cases_excluded?' '+Number(r.cases_excluded).toLocaleString()+' cases beyond the length or variant limit were left out.':'');
        if(!r.net.final_marking_declared)out.appendChild(el('div','The file declares no final marking, so cases are expected to end with one token on each place nothing consumes from.','muted'));
        if(r.places.length){out.appendChild(el('h4','Places where tokens went missing or were left behind'));
          out.appendChild(table(['Place','Missing tokens','Remaining tokens'],r.places.map(p=>[p.place,Number(p.missing).toLocaleString(),Number(p.remaining).toLocaleString()])));}
        if(r.transitions_fired_without_tokens.length){out.appendChild(el('h4','Steps taken when the net did not allow them'));
          out.appendChild(table(['Transition','Times'],r.transitions_fired_without_tokens.map(t=>[t.transition,Number(t.times).toLocaleString()])));}
        if(r.precision.escaping_edges.length){out.appendChild(el('h4','Steps the net allows that no case took'));
          out.appendChild(table(['After','Net also allows','Cases at that point'],r.precision.escaping_edges.map(e=>[e.after===null?'(case start)':e.after,e.net_also_allows,Number(e.cases).toLocaleString()])));}
        if(r.activities_not_in_net.length)out.appendChild(el('div','Activities the net does not have (skipped): '+r.activities_not_in_net.map(a=>a.activity+' ('+a.cases+' cases)').join(', ')+'.','muted'));
        if(r.net_labels_never_seen.length)out.appendChild(el('div','Net transitions no case took: '+r.net_labels_never_seen.join(', ')+'.','muted'));
        if(r.non_fitting_variants.length){const more=document.createElement('details');more.appendChild(el('summary','Variants that do not fit ('+r.non_fitting_variants.length+')'));
          more.appendChild(table(['Variant','Cases','Fitness','Missing','Remaining','Evidence'],r.non_fitting_variants.map(v=>[
            v.path.join(' → '),Number(v.cases).toLocaleString(),pct(v.fitness),String(v.missing_tokens),String(v.remaining_tokens),inspectVariant(v,lastNet,'Token replay')
          ])));out.appendChild(more);}
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){statusLine.textContent=error.message;}
      finally{run.disabled=own.disabled=happy.disabled=false;align.disabled=alertSave.disabled=saveRun.disabled=!lastNet;}};
    alertSave.onclick=async()=>{
      if(!lastNet)return;
      if(lastNet.body.length>60000){alertMessage.textContent='This net is too large to keep with a signal (over 60,000 characters).';return;}
      const when={kind:'conformance',net_name:lastNet.label.slice(0,200),pnml:lastNet.body,metric:'non_fitting_share',operator:'above',threshold:Number(alertShare.value)};
      if(alertWindow.value)when.recent_days=Number(alertWindow.value);
      const sig={name:('Cases not fitting '+lastNet.label).slice(0,120),when};if(state.caseType)sig.case_type=state.caseType;
      alertSave.disabled=true;
      try{await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sig)});
        document.querySelectorAll('#owgEventLogSignals').forEach(node=>node.dispatchEvent(new CustomEvent('owg-signals-changed')));
        alertMessage.textContent='Saved as a signal with a copy of this net. Use Check signals to evaluate it now; it keeps checking the same net as the log grows.';}
      catch(error){alertMessage.textContent=error.message;}
      finally{alertSave.disabled=!lastNet;}};
    const step=s=>s.move==='sync'?s.log:s.move==='log'?'+'+s.log+' (not in the net here)':'−'+s.model+' (skipped)';
    align.onclick=async()=>{
      if(!lastNet||lastNet.generation!==state.generation){align.disabled=true;return;}
      const generation=state.generation,net=lastNet;run.disabled=own.disabled=align.disabled=true;
      const status=el('div','Aligning each variant on '+net.label+'…','muted');status.id='owgPetriAlignStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      aligned.replaceChildren(el('h4','Step-by-step explanation (alignments)'),status);
      try{
        const r=await (await api(logPath('petri-net-alignments',{}),{method:'POST',headers:{'Content-Type':'application/xml'},body:net.body})).json();
        if(generation!==state.generation)return;
        const f=r.fitness;
        status.textContent=(r.cases?'Alignment fitness '+pct(f.log)+': '+Number(f.fitting_cases).toLocaleString()+' of '+Number(r.cases).toLocaleString()+' cases follow the net exactly. On average a case needs '+r.deviation_cost.per_case+' extra or skipped steps to fit.':'No cases could be aligned.')+
          (r.unaligned.variants?' Alignment did not finish for '+Number(r.unaligned.variants).toLocaleString()+' variants covering '+Number(r.unaligned.cases).toLocaleString()+' cases.':'')+
          (r.cases_excluded?' '+Number(r.cases_excluded).toLocaleString()+' cases were outside the trace length or variant limits.':'');
        if(r.by_month.length>1){aligned.appendChild(el('h4','Fitness by month the cases started'));
          const t=table(['Month','Cases','Follow the net exactly','Alignment fitness'],r.by_month.map(m=>[m.month,Number(m.cases).toLocaleString(),pct(m.fitting_case_share),pct(m.fitness)]));t.id='owgPetriAlignMonths';aligned.appendChild(t);}
        if(r.log_moves.length){aligned.appendChild(el('h4','Steps the cases took that the net does not allow there'));
          aligned.appendChild(table(['Activity','Cases','Times'],r.log_moves.map(m=>[m.activity,Number(m.cases).toLocaleString(),Number(m.times).toLocaleString()])));}
        if(r.model_moves.length){aligned.appendChild(el('h4','Steps the net requires that the cases skipped'));
          aligned.appendChild(table(['Activity','Cases','Times'],r.model_moves.map(m=>[m.activity,Number(m.cases).toLocaleString(),Number(m.times).toLocaleString()])));}
        if(r.non_fitting_variants.length){const more=document.createElement('details');more.open=true;more.appendChild(el('summary','How each non-fitting variant is explained ('+r.non_fitting_variants.length+' of '+r.non_fitting_variants_total+')'));
          more.appendChild(table(['Variant','Cases','Fitness','Explanation','Evidence'],r.non_fitting_variants.map(v=>[
            v.path.join(' → '),Number(v.cases).toLocaleString(),pct(v.fitness),v.alignment.map(step).join(' → '),inspectVariant(v,net,'Step-by-step alignment')
          ])));aligned.appendChild(more);}
        if(r.unaligned_variants?.length){
          const more=document.createElement('details');
          more.appendChild(el('summary','Variants without a completed alignment ('+r.unaligned_variants.length+' of '+r.unaligned.variants+')'));
          more.appendChild(table(['Variant','Cases','Evidence'],r.unaligned_variants.map(v=>[
            v.path.join(' → '),Number(v.cases).toLocaleString(),inspectVariant(v,net,'Variant without a completed alignment')
          ])));
          aligned.appendChild(more);
        }
        aligned.appendChild(el('div',r.caveat,'muted'));
        if(r.non_fitting_variants.length){
          const download=button('Download non-fitting cases (CSV)','secondary');download.id='owgPetriCasesCsv';
          download.onclick=async()=>{
            download.disabled=true;
            try{const response=await api(logPath('petri-net-alignments/cases.csv',{}),{method:'POST',headers:{'Content-Type':'application/xml'},body:net.body});
              const blob=await response.blob();const link=document.createElement('a');link.href=URL.createObjectURL(blob);
              link.download=(state.logId||'log')+'-non-fitting-cases.csv';document.body.appendChild(link);link.click();link.remove();
              setTimeout(()=>URL.revokeObjectURL(link.href),1000);}
            catch(error){status.textContent=error.message;}
            finally{download.disabled=false;}};
          aligned.appendChild(download);
          // Tag every non-fitting case, so the tag can focus other analyses on them (or compare them).
          const tagName=input('owgPetriTagName','text');tagName.value='off-model';tagName.maxLength=60;tagName.autocomplete='off';
          const tagRun=button('Tag the '+Number(r.cases-f.fitting_cases).toLocaleString()+' non-fitting cases','secondary');tagRun.id='owgPetriTag';
          const tagMessage=el('div',null,'muted');tagMessage.id='owgPetriTagMessage';tagMessage.setAttribute('role','status');tagMessage.setAttribute('aria-live','polite');
          const tagRow=el('div',null,'row');tagRow.style.alignItems='end';tagRow.append(field('Tag',tagName),tagRun);
          aligned.append(tagRow,tagMessage);
          tagRun.onclick=async()=>{
            if(!tagName.value.trim()){tagMessage.textContent='Enter a tag.';return;}
            tagRun.disabled=true;tagMessage.textContent='Tagging…';
            try{const t2=await (await api(logPath('petri-net-alignments/tag',{new_tag:tagName.value.trim()}),{method:'POST',headers:{'Content-Type':'application/xml'},body:net.body})).json();
              document.querySelectorAll('#owgCaseTags').forEach(node=>node.dispatchEvent(new CustomEvent('owg-tags-changed')));
              tagMessage.textContent='Tagged '+Number(t2.tagged).toLocaleString()+' cases “'+t2.tag+'”'+(t2.cases_not_tagged?' ('+Number(t2.cases_not_tagged).toLocaleString()+' on less common paths were left out)':'')+'. Use the tag in the case filter to focus every analysis on them.';}
            catch(error){tagMessage.textContent=error.message;}
            finally{tagRun.disabled=false;}};
          const why=button('What goes with these deviations?','secondary');why.id='owgPetriCauses';
          const causes=el('div');causes.id='owgPetriCausesResults';causes.setAttribute('role','status');causes.setAttribute('aria-live','polite');
          aligned.append(why,causes);
          why.onclick=async()=>{
            if(generation!==state.generation)return;why.disabled=true;causes.replaceChildren(el('div','Comparing deviating and fitting cases…','muted'));
            try{
              const c=await (await api(logPath('petri-net-deviation-causes',{}),{method:'POST',headers:{'Content-Type':'application/xml'},body:net.body})).json();
              if(generation!==state.generation)return;
              const pts=x=>(x>0?'+':'')+(x*100).toFixed(1)+' pts';
              const list=(c.associations||[]).slice(0,15);
              causes.replaceChildren(el('div',Number(c.outcome_cases||0).toLocaleString()+' of '+Number(c.cases).toLocaleString()+' cases deviate; '+Number(c.features_tested||0)+' case features tested.'+(list.length?'':' None goes with deviating cases in this log.'),'muted'));
              if(list.length)causes.appendChild(table(['Feature','Cases with it','Deviate with','Deviate without','Difference (95% CI)','Holds after correction'],list.map(a=>[a.feature.kind+': '+a.feature.value,Number(a.cases_with_feature).toLocaleString(),pct(a.outcome_rate_with),pct(a.outcome_rate_without),pts(a.risk_difference)+' ['+(a.ci95[0]*100).toFixed(1)+', '+(a.ci95[1]*100).toFixed(1)+']',a.holds_after_bonferroni?'Yes':'No'])));
              causes.appendChild(el('div',c.caveat||c.note||'','muted'));
            }catch(error){causes.replaceChildren(el('div',error.message,'muted'));}
            finally{why.disabled=false;}};}
      }catch(error){status.textContent=error.message;}
      finally{run.disabled=own.disabled=false;align.disabled=!lastNet;}};
    run.onclick=async()=>{const chosen=file.files&&file.files[0];if(!chosen){statusLine.textContent='Choose a .pnml file first.';return;}
      if(chosen.size>2*1024*1024){statusLine.textContent='The Petri net file is larger than 2 MB.';return;}
      await replay(await chosen.text(),chosen.name);};
    happy.onclick=async()=>{
      try{statusLine.textContent='Building the most common path…';const response=await api(logPath('variant.pnml',{rank:'1'}));
        const share=/share=([0-9.]+)/.exec(response.headers.get('X-OWG-Path-Cases')||'');
        await replay(await response.text(),'the most common path'+(share?' (followed by '+Math.round(Number(share[1])*1000)/10+'% of cases)':''));}
      catch(error){statusLine.textContent=error.message;}};
    own.onclick=async()=>{
      try{statusLine.textContent='Discovering…';const xml=await (await api(logPath('model-discovery.pnml',{noise:'0'}))).text();await replay(xml,'the discovered net (every path kept)');}
      catch(error){statusLine.textContent=error.message;}};
    return box;
  }

  function temporalProfileSection(inspection){
    const box=section('Temporal profile');box.id='owgTemporal';
    box.appendChild(el('div','For every pair of activities in a case, even far apart, the usual time from the first to the second. Cases whose time is more than the chosen number of standard deviations from the usual one are listed: unusual timings to review, not errors.','muted'));
    const zeta=select('owgTemporalZeta',[['3','3 standard deviations'],['2','2 standard deviations'],['4','4 standard deviations'],['6','6 standard deviations']]);
    const run=button('Check timings','secondary');run.id='owgTemporalRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Flag times beyond',zeta),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgTemporalStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgTemporalResults';
    const review=el('div');review.id='owgTemporalCases';
    box.append(row,statusLine,out,review);
    const generation=state.generation;
    let revision=0,selection=0;
    zeta.onchange=()=>{
      ++revision;run.disabled=false;out.replaceChildren();review.replaceChildren();
      statusLine.textContent='Threshold changed. Check timings again.';
    };
    function reviewCases(url,pair){
      const report=revision,chosen=++selection;
      const heading=el('h4',pair?'Timing deviations from '+pair.from+' to '+pair.to:'All cases with unusual timings');heading.tabIndex=-1;
      const direction=select('owgTemporalDirection',[['all','Both directions'],['slower','Slower than usual'],['faster','Faster than usual']]);
      const message=el('div');message.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);review.replaceChildren(heading,field('Direction',direction),message,rows,nav);heading.focus();
      const cursors=[null];let page=0,more=null,turn=0;
      async function load(index){
        const ticket=++turn;
        const current=()=>generation===state.generation&&report===revision&&chosen===selection&&ticket===turn;
        previous.disabled=next.disabled=true;message.textContent='Loading cases…';rows.replaceChildren();
        try{
          const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
          const spec={direction:direction.value};
          if(pair){spec.source=pair.from;spec.destination=pair.to;}
          if(cursors[index]!==null)spec.after=cursors[index];
          const result=await (await api(endpoint.pathname+endpoint.search,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
          page=index;more=result.next_after;
          const count=(value,label)=>value.toLocaleString()+' '+label+(value===1?'':'s');
          message.textContent=count(result.deviating_cases,'matching case')+' with '+count(result.deviations,'deviation')+'. Showing '+result.cases.length+'.';
          rows.replaceChildren(result.cases.length?table(['Case','Deviations','Strongest pair','Direction','Took','Usual','Evidence'],
            result.cases.map(item=>{
              const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+item.case_id);
              open.onclick=()=>inspection.open(item.case_id);
              return [item.case_id,item.deviations,item.strongest.from+' → '+item.strongest.to,item.direction,
                duration(item.strongest.seconds),duration(item.strongest.mean_seconds),open];
            })):el('div','No cases match these timings.','muted'));
        }catch(error){if(current())message.textContent=error.message;}
        finally{if(current()){previous.disabled=page===0;next.disabled=more===null;}}
      }
      previous.onclick=()=>load(page-1);next.onclick=()=>{cursors[page+1]=more;load(page+1);};
      direction.onchange=()=>{cursors.length=1;page=0;more=null;load(0);};load(0);
    }
    run.onclick=async()=>{
      const report=++revision;
      const current=()=>generation===state.generation&&report===revision;
      run.disabled=true;statusLine.textContent='Checking…';out.replaceChildren();review.replaceChildren();
      try{
        const url=logPath('temporal-profile',{zeta:zeta.value}),r=await getJSON(url);
        if(!current())return;
        if(!r.profile_pairs){statusLine.textContent='No pair of activities occurs often enough, with varying times, to check.';return;}
        statusLine.textContent=r.deviating_cases.toLocaleString()+' of '+r.cases.toLocaleString()+' cases ('+Math.round((r.deviating_case_share||0)*100)+'%) have at least one unusual timing across '+r.profile_pairs+' checked pairs.'+(r.cases_before_filter?' Filtered from '+Number(r.cases_before_filter).toLocaleString()+' cases.':'');
        const pairs=r.pairs.filter(p=>p.deviations).slice(0,15);
        if(pairs.length){out.appendChild(el('h4','Pairs most often outside their usual time'));
          out.appendChild(table(['From','To','Usual time','Allowed range','Outside','Slower','Faster','Evidence'],pairs.map(p=>{
            const inspect=button('Inspect cases','secondary');inspect.setAttribute('aria-label','Inspect timings from '+p.from+' to '+p.to);
            inspect.onclick=()=>reviewCases(url,p);
            return [p.from,p.to,duration(p.mean_seconds),duration(p.allowed_seconds[0])+'–'+duration(p.allowed_seconds[1]),
              p.deviations+' of '+p.occurrences,String(p.slower),String(p.faster),inspect];
          })));}
        if(r.top_cases.length){out.appendChild(el('h4','Cases with most unusual timings'));
          const all=button('Browse all deviating cases','secondary');all.onclick=()=>reviewCases(url);
          out.appendChild(all);
          out.appendChild(table(['Case','Unusual pairs','Strongest','Took','Usual'],r.top_cases.map(c=>[c.case_id,String(c.deviations),c.strongest.from+' → '+c.strongest.to+' ('+c.strongest.standard_deviations+' sd)',duration(c.strongest.seconds),duration(c.strongest.mean_seconds)])));}
        out.appendChild(el('div',r.caveat,'muted'));
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())run.disabled=false;}};
    return box;
  }

  function dottedSection(inspection){
    const box=section('Events over time');box.id='owgDots';
    box.appendChild(el('div','A dotted chart: each row is a case and each dot an event at its recorded time, coloured by activity. Use calendar time to see waves, batches and backlogs, or time since each case started to compare how cases unfold.','muted'));
    const time=select('owgDotsTime',[['absolute','Calendar time'],['relative','Time since case start']]);
    const order=select('owgDotsOrder',[['start','Case start'],['duration','Longest cases first']]);
    const run=button('Draw dotted chart','secondary');run.id='owgDotsRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Time',time),field('Order rows by',order),run);
    const statusLine=el('div',null,'muted');statusLine.id='owgDotsStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgDotsResults';out.style.position='relative';
    box.append(row,statusLine,out);
    const stamp=(r,seconds)=>r.time_origin?new Date(Date.parse(r.time_origin)+seconds*1000).toISOString().slice(0,16).replace('T',' '):duration(seconds);
    const generation=state.generation;
    let revision=0,watcher=null;
    function reset(){
      ++revision;watcher?.disconnect();watcher=null;
      run.disabled=false;out.replaceChildren();
    }
    time.onchange=order.onchange=()=>{reset();statusLine.textContent='Chart settings changed.';};
    run.onclick=async()=>{
      reset();
      const ticket=revision;
      const current=()=>ticket===revision&&generation===state.generation&&box.isConnected;
      try{
        run.disabled=true;statusLine.textContent='Drawing…';
        const r=await getJSON(logPath('dotted-chart',{time:time.value,order:order.value,max_cases:'300'}));
        if(!current())return;
        const c=r.coverage;
        statusLine.textContent=Number(c.cases_shown).toLocaleString()+' of '+Number(c.cases).toLocaleString()+' cases'+(c.cases_sampled?' (evenly sampled)':'')+' with '+Number(c.points_shown).toLocaleString()+' events'+(c.points_thinned?' (thinned within long cases)':'')+'.';
        const legend=el('ul');legend.id='owgDotsLegend';legend.style.cssText='list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:4px 14px;margin:6px 0';
        const label=a=>a.activity===null?'Other ('+a.activities+' activities)':a.activity;
        r.activities.forEach(a=>{const item=el('li');const swatch=el('span');swatch.setAttribute('aria-hidden','true');
          swatch.style.cssText='display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:'+DOT_COLOURS[a.slot];
          item.append(swatch,document.createTextNode(label(a)+' · '+Number(a.events).toLocaleString()));legend.appendChild(item);});
        const plotHost=el('div');plotHost.style.position='relative';
        const tip=el('div');tip.id='owgDotsTip';tip.setAttribute('role','tooltip');
        tip.style.cssText='position:absolute;pointer-events:none;background:#fcfcfb;color:#0b0b0b;border:1px solid #c3c2b7;border-radius:6px;padding:4px 8px;font-size:12px;display:none;white-space:nowrap;z-index:1';
        const names=Object.fromEntries(r.activities.map(a=>[a.slot,label(a)]));
        let drawnWidth=0;
        const draw=()=>{
          const width=Math.max(260,Math.min(1100,plotHost.clientWidth||out.clientWidth||600));
          if(Math.abs(width-drawnWidth)<4)return;drawnWidth=width;
          const rowHeight=Math.max(2,Math.min(12,Math.floor(480/Math.max(1,r.cases.length))));
          const layout=dotLayout(r,width,rowHeight);
          const canvas=document.createElement('canvas');canvas.id='owgDotsCanvas';
          const ratio=window.devicePixelRatio||1;canvas.width=layout.width*ratio;canvas.height=layout.height*ratio;
          canvas.style.cssText='width:'+layout.width+'px;height:'+layout.height+'px;display:block';
          canvas.setAttribute('role','img');
          canvas.setAttribute('aria-label','Dotted chart of '+r.cases.length+' cases over '+(r.time_origin?'calendar time':'time since case start')+'. Open a dot or use the case table below to review its events.');
          const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
          const ink=getComputedStyle(box).color||'#52514e';
          ctx.strokeStyle='rgba(128,128,128,0.35)';ctx.fillStyle=ink;ctx.font='11px system-ui,sans-serif';
          layout.ticks.forEach((t,i)=>{ctx.beginPath();ctx.moveTo(t.x,layout.top);ctx.lineTo(t.x,layout.height-layout.bottom);ctx.stroke();
            ctx.textAlign=i===0?'left':i===layout.ticks.length-1?'right':'center';ctx.fillText(stamp(r,t.seconds),t.x,layout.height-10);});
          const radius=Math.max(1.5,Math.min(4,rowHeight/2));
          layout.points.forEach(p=>{ctx.beginPath();ctx.arc(p.x,p.y,radius,0,Math.PI*2);ctx.fillStyle=DOT_COLOURS[p.slot];ctx.fill();});
          canvas.__owgPoints=layout.points;
          const nearest=event=>{
            const rect=canvas.getBoundingClientRect();
            const mx=event.clientX-rect.left,my=event.clientY-rect.top;
            let best=null,dist=64;
            for(const p of layout.points){const d=(p.x-mx)**2+(p.y-my)**2;if(d<dist){dist=d;best=p;}}
            return best;
          };
          canvas.addEventListener('click',event=>{
            const point=nearest(event);
            if(point&&current())inspection.open(r.cases[point.row].case_id);
          });
          canvas.addEventListener('mousemove',event=>{
            const best=nearest(event);canvas.style.cursor=best?'pointer':'default';
            if(!best){tip.style.display='none';return;}
            tip.textContent='Case '+r.cases[best.row].case_id+' · '+names[best.slot]+' · '+stamp(r,best.t);
            tip.style.left=Math.max(0,Math.min(layout.width-200,best.x+12))+'px';tip.style.top=(best.y+8)+'px';tip.style.display='block';
          });
          canvas.addEventListener('mouseleave',()=>{tip.style.display='none';});
          plotHost.replaceChildren(canvas,tip);
        };
        const details=el('details');details.appendChild(el('summary','Table view of these cases'));
        details.appendChild(table(['Case','Started (UTC)','Duration','Events'],r.cases.map(k=>{
          const open=button(k.case_id,'secondary');open.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%';
          open.setAttribute('aria-label','Open case '+k.case_id);
          open.onclick=()=>{if(current())inspection.open(k.case_id);};
          return [open,k.start.slice(0,16).replace('T',' '),duration(k.duration_seconds),Number(k.events).toLocaleString()];
        })));
        out.append(legend,plotHost,details,el('div',r.interpretation,'muted'));
        draw();
        if(window.ResizeObserver){
          const observer=new ResizeObserver(()=>{if(!current()){observer.disconnect();return;}draw();});
          watcher=observer;observer.observe(plotHost);
        }
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  function driftSection(inspection){
    const box=section('Changes over time');box.id='owgConceptDrift';
    box.appendChild(el('div','Finds when the way cases run changed: cases are grouped by when they started, and each window\'s paths are compared with the next. A change point means the difference is larger than random splits of the same cases give. It does not say why, and a change in how events are recorded looks the same.','muted'));
    const windowKind=select('owgConceptDriftWindow',[['month','Month'],['week','Week'],['cases','Fixed number of cases']]);
    const perWindow=input('owgConceptDriftCases','number');perWindow.min='20';perWindow.step='1';perWindow.value='500';
    const casesField=field('Cases per window',perWindow);show(casesField,false);
    const run=button('Find changes','secondary');run.id='owgConceptDriftRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Window',windowKind),casesField,run);
    const statusLine=el('div',null,'muted');statusLine.id='owgConceptDriftStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgConceptDriftResults';
    const review=el('div');review.id='owgConceptDriftCaseReview';
    box.append(row,statusLine,out,review);
    const generation=state.generation;
    let revision=0,selection=0;
    function reset(){
      ++revision;run.disabled=false;out.replaceChildren();review.replaceChildren();
      statusLine.textContent='Settings changed. Find changes again.';
    }
    windowKind.onchange=()=>{show(casesField,windowKind.value==='cases');reset();};
    perWindow.oninput=reset;
    function reviewWindow(url,index,label,path){
      const report=revision,chosen=++selection;
      const current=()=>generation===state.generation&&report===revision&&chosen===selection;
      const title=el('h4',label+(path?' · '+path.path:''));title.tabIndex=-1;
      const message=el('div');message.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const prev=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(prev,next);review.replaceChildren(title,message,rows,nav);title.focus();
      const cursors=[null];let page=0,more=null;
      async function load(at){
        prev.disabled=next.disabled=true;message.textContent='Loading the current sample…';
        try{
          const endpoint=new URL(url,window.location.origin);endpoint.pathname+='/cases';
          const spec={window_index:index,expected_window:label};
          if(path){spec.source=path.from;spec.destination=path.to;}
          if(cursors[at]!==null)spec.after=cursors[at];
          const result=await (await api(endpoint.pathname+endpoint.search,{method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
          page=at;more=result.next_after;
          const n=value=>Number(value).toLocaleString();
          message.textContent=n(result.matching_cases)+' matches in '+n(result.sampled_cases)+' sampled cases ('+n(result.window_cases)+' cases in the window). Showing '+result.cases.length+'.';
          rows.replaceChildren(result.cases.length?table(['Case','Observed duration','Evidence'],result.cases.map(item=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+item.case_id);
            open.onclick=()=>inspection.open(item.case_id);
            return [item.case_id,duration(item.duration_seconds),open];
          })):el('div','No sampled cases match.','muted'));
        }catch(error){if(current())message.textContent=error.message;}
        finally{if(current()){prev.disabled=page===0;next.disabled=more===null;}}
      }
      prev.onclick=()=>load(page-1);next.onclick=()=>{cursors[page+1]=more;load(page+1);};
      load(0);
    }
    run.onclick=async()=>{
      const report=++revision;
      const current=()=>generation===state.generation&&report===revision;
      try{
        run.disabled=true;out.replaceChildren();review.replaceChildren();statusLine.textContent='Comparing windows…';
        const params={window:windowKind.value};
        if(windowKind.value==='cases'){const n=Math.round(Number(perWindow.value));if(!(n>=20))throw new Error('Use at least 20 cases per window.');params.window_cases=String(n);params.min_cases=String(Math.max(10,Math.min(30,n)));}
        const url=logPath('drift',params),r=await getJSON(url);
        if(!current())return;
        statusLine.textContent=r.change_points.length+' change point'+(r.change_points.length===1?'':'s')+' among '+r.boundaries_tested+' window boundaries tested'+(r.boundaries_skipped_small_windows?' ('+r.boundaries_skipped_small_windows+' skipped because a window had fewer than '+r.settings.min_cases+' cases)':'')+'.';
        const byAfter=Object.fromEntries(r.boundaries.map(b=>[b.after,b]));
        const p=v=>v<0.0001?'< 0.0001':v.toFixed(4);
        const windows=new Map(r.windows.map((w,index)=>[w.window,index]));
        const inspect=(label,path)=>{
          const open=button('Inspect sampled cases','secondary');
          open.setAttribute('aria-label','Inspect sampled cases in '+label+(path?' with '+path.path:''));
          open.onclick=()=>reviewWindow(url,windows.get(label),label,path);
          return open;
        };
        out.appendChild(table(['Window','Cases','Sampled cases','Median case time','Change from previous window','Evidence'],r.windows.map(w=>{const b=byAfter[w.window];
          return [w.window,Number(w.cases).toLocaleString(),Number(w.sampled_cases).toLocaleString(),duration(w.median_case_duration_seconds),b?(b.change_point?'Change point · ':'')+'distance '+b.distance.toFixed(3)+', p '+p(b.p_value):'—',inspect(w.window)];})));
        r.change_points.forEach(c=>{
          out.appendChild(el('h4','Paths that moved most: '+c.before+' → '+c.after));
          out.appendChild(table(['Path','Sampled share before','Sampled share after','Before cases','After cases'],c.paths_that_moved.map(m=>[
            m.path,pct(m.case_share_before),pct(m.case_share_after),inspect(c.before,m),inspect(c.after,m)
          ])));
        });
        out.appendChild(el('div',r.interpretation,'muted'));
      }catch(error){if(current())statusLine.textContent=error.message;}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  function relationshipText(profile){return profile?' Retained '+Number(profile.retained_relationships).toLocaleString()+' supplied object relationships; these do not establish case membership or causality.':'';}

  function sameSourceText(matches){
    if(!Array.isArray(matches)||!matches.length)return '';
    const names=matches.slice(0,3).map(m=>m.this_log?'an earlier batch of this log':'\u201c'+m.name+'\u201d');
    return ' This exact file was already loaded as '+names.join(', ')+(matches.length>3?' and '+(matches.length-3)+' more':'')+'; delete the copy you do not need to save space.';
  }
  function identityText(identity){return identity?' Supplied source-ID matching: '+Number(identity.duplicate_source_ids_skipped||0).toLocaleString()+' repeated IDs skipped; namespace and case associations are caller-declared. Plain CSV/XES/OCEL exports omit these keys.':'';}

  function qualitySection(){
    const box=section('Data quality');box.id='owgQuality';
    const out=el('div');out.id='owgQualityResults';out.setAttribute('role','status');
    const details=el('details');details.appendChild(el('summary','Import reports, digests and diagnostics'));details.appendChild(out);
    box.append(el('div','Import diagnostics show counts without row values; rejected rows are kept only if you chose to keep them at import. The digests let you check that the same file with the same mapping gives the same stored log.','muted'),details,quarantineBlock());
    const generation=state.generation;
    const tieBox=el('div');tieBox.id='owgQualityTies';
    const tieRun=button('Analyze timestamp ordering','secondary');tieRun.id='owgQualityTiesRun';
    const tieOut=el('div');tieOut.id='owgQualityTiesResults';tieOut.setAttribute('role','status');
    tieBox.append(el('p','Find activities affected by events with the same timestamp in the selected cases. This displays activity names and scans the selected log on demand.','muted'),tieRun,tieOut);box.appendChild(tieBox);
    tieRun.onclick=async()=>{
      tieRun.disabled=true;tieOut.replaceChildren(el('p','Analyzing timestamp ordering…','muted'));
      try{
        const r=await getJSON(logPath('quality/timestamp-ties'));
        if(generation!==state.generation||!tieOut.isConnected)return;
        tieOut.replaceChildren(el('p',Number(r.tied_case_event_occurrences).toLocaleString()+' of '+Number(r.case_event_occurrences).toLocaleString()+' case-event occurrences share a timestamp in '+Number(r.cases_with_ties).toLocaleString()+' of '+Number(r.cases).toLocaleString()+' cases. '+Number(r.groups_with_different_activities).toLocaleString()+' timestamp groups contain different activity labels.','muted'),
          el('p',r.interpretation,'muted'),
          ...(r.activities_truncated?[el('p','Showing the 100 activities with the most tied occurrences; other activities are omitted.','muted')]:[]),
          table(['Activity','Occurrences','Tied occurrences','Cases with ties'],r.activities.map(a=>[a.activity,Number(a.occurrences).toLocaleString(),Number(a.tied_occurrences).toLocaleString(),Number(a.cases_with_ties).toLocaleString()])));
      }catch(error){if(generation===state.generation&&tieOut.isConnected)tieOut.replaceChildren(el('p',error.message,'muted'));}
      finally{if(generation===state.generation&&tieRun.isConnected)tieRun.disabled=false;}
    };
    let loaded=false,pending=false;
    const loadQuality=()=>{
      if(loaded||pending||generation!==state.generation)return;
      pending=true;out.replaceChildren(el('div','Calculating current quality diagnostics…','muted'));
      getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/quality/overview').then(r=>{
      if(generation!==state.generation)return;
      const d=r.current.diagnostics,short=h=>typeof h==='string'&&h?h.slice(0,12)+'…':'—';
      // This is a live snapshot, not the historical import-time report.
      const futureReference=d.future_event_reference||null;
      const futureBasis=futureReference&&futureReference.basis==='report_created'?'report creation':
        futureReference&&futureReference.basis==='current_query'?'current query':'current query (legacy reference not recorded)';
      const futureAt=futureReference&&typeof futureReference.at==='string'?futureReference.at:'not recorded';
      const futureCount=d.events_dated_after_reference===undefined?d.events_dated_after_import:d.events_dated_after_reference;
      const coverage=Array.isArray(d.attribute_coverage)?d.attribute_coverage:[];
      const totalKeys=Number.isSafeInteger(d.attribute_coverage_total_keys)&&
        d.attribute_coverage_total_keys>=coverage.length?d.attribute_coverage_total_keys:null;
      const coverageNote=d.attribute_coverage_truncated===true?
        'Showing '+coverage.length.toLocaleString()+' of '+(totalKeys===null?'an unknown number of':totalKeys.toLocaleString())+' attribute keys; omitted keys are not absent.':
        d.attribute_coverage_truncated===false&&totalKeys!==null?
        'Showing all '+totalKeys.toLocaleString()+' attribute keys in the supplied rows.':
        'Showing up to '+coverage.length.toLocaleString()+' attribute keys; whether more were omitted was not recorded (legacy server).';
      // Recovered or legacy report rows can lack metadata. Never hide the integrity
      // verdict because a damaged historical report cannot populate this table.
      const reports=(Array.isArray(r.reports)?r.reports:[]).map(p=>{
        const report=p&&typeof p==='object'?p:{};
        const transform=report.transform&&typeof report.transform==='object'?report.transform:{};
        const rows=report.rows&&typeof report.rows==='object'?report.rows:{};
        const count=v=>Number.isSafeInteger(v)&&v>=0?v.toLocaleString():'—';
        return [report.batch===0?'Import':Number.isSafeInteger(report.batch)?'Append '+report.batch:'Unknown batch',
          transform.format||'—',count(rows.accepted),count(rows.rejected)+(rows.rejected?' ('+reasonText(rows.rejected_by_reason)+')':''),
          rows.duplicates_skipped_on_append===undefined?'—':count(rows.duplicates_skipped_on_append),
          el('code',short(transform.transform_sha256)),el('code',short(report.content_sha256)),el('code',short(report.content_sha256_v2))];
      });
      const rawMapping=r.first_report?.transform?.mapping;
      const mapping=rawMapping&&typeof rawMapping==='object'&&!Array.isArray(rawMapping)?rawMapping:{};
      const integrity=r.integrity&&typeof r.integrity==='object'?r.integrity:{};
      const integrityLabel=integrity.status==='verified'?'Verified: current saved content matches the recorded import digest.':
        integrity.status==='mismatch'?'Mismatch: saved content differs from the recorded import digest. Investigate before relying on this log.':
        'Not verifiable: the available reports cannot establish current saved-content integrity.';
      const integrityStatus=el('p',integrityLabel,'muted');integrityStatus.id='owgQualityIntegrity';
      integrityStatus.setAttribute('role','status');
      integrityStatus.dataset.status=['verified','mismatch'].includes(integrity.status)?integrity.status:'not_verifiable';
      const mapRows=mapping.case_column!==undefined?[['Case column',mapping.case_column],['Activity column',mapping.activity_column],['Timestamp column',mapping.timestamp_column],['Resource column',mapping.resource_column||'(none)'],['Extra columns kept',(Array.isArray(mapping.attribute_columns)?mapping.attribute_columns:[]).join(', ')||'(none)'],['Mapping version',mapping.mapping_version]]:[['Standard',mapping.standard||'—'],['Mapping version',mapping.mapping_version||'—']];
      if(mapping.source_identity)for(const [key,value] of Object.entries(mapping.source_identity))if(value)mapRows.push(['Source identity: '+key,value]);
      out.replaceChildren(
        ...(r.log.source_identity?[el('p','Source-ID deduplication compares declared namespace and supplied event ID. The content-duplicate diagnostic below compares payloads; distinct source IDs with identical payloads remain separate events. Neither establishes verified provider identity or complete history.')]:[]),
        el('div','Showing '+Number(r.window.batch_count).toLocaleString()+' of '+Number(r.window.total_batches).toLocaleString()+' batches. Use Compare batches below to browse older history.','muted'),
        integrityStatus,
        table(['Batch','Format','Accepted','Rejected','Duplicates skipped','Transform','Content v1','Content v2'],reports),
        ...(r.batches_without_report.length?[el('div','No report was stored on this page for '+r.batches_without_report.map(b=>b?'append '+b:'the import').join(', ')+' (imported before reports existed, or the report could not be written).','muted')]:[]),
        table(['Mapping',''],mapRows),
        table(['Diagnostic (whole log)','Events'],[
          ['Duplicate event payloads (including attributes and qualified links; IDs excluded)',Number(d.duplicate_events).toLocaleString()],
          ['Events sharing a timestamp within a case',Number(d.events_sharing_a_timestamp_in_a_case).toLocaleString()+' in '+Number(d.cases_with_shared_timestamps).toLocaleString()+' cases; order falls back to the event ID'],
          ['Events without a resource',Number(d.events_without_resource).toLocaleString()],
          ['Events without any retained object link',Number(d.events_without_case).toLocaleString()],
          ['Events dated after reference ('+futureBasis+')',Number(futureCount).toLocaleString()+'; UTC reference: '+futureAt],
          ...d.case_notions.map(n=>['Single-event '+n.object_type+' cases',Number(n.single_event_cases).toLocaleString()+' of '+Number(n.cases).toLocaleString()])]),
        el('div',coverageNote,'muted'),
        ...(coverage.length?[table(['Attribute','Events with a value','Without'],coverage.map(a=>[a.attribute,Number(a.events_with_value).toLocaleString(),Number(a.events_without_value).toLocaleString()]))]:[]),
        ...(r.current.object_relationships?[el('div','Retained supplied object assertions: '+r.current.object_relationships.retained_relationships+'. Separate whole-log relationship digest: '+r.current.object_relationships.relationships_sha256+'. This does not establish source completeness, case membership or causality.','muted')]:[]),
        ...(r.current.source_identity_map_sha256?[el('div','Current source identity map digest: '+r.current.source_identity_map_sha256,'muted')]:[]),
        el('div','Current content digest v1: '+r.current.content_sha256+'. '+
          (r.current.content_sha256_v2?' Current content digest v2 (delimiter-safe): '+r.current.content_sha256_v2+'.':' Content digest v2 not recorded (legacy server).')+
          ' '+r.current.completeness.note,'muted'));
      if(d.attribute_coverage_truncated===true){
        const coverageHost=el('div');coverageHost.id='owgQualityCoveragePages';
        const browse=button('Browse all attribute coverage','secondary');
        browse.id='owgQualityCoverageBrowse';out.append(browse,coverageHost);
        let coverageTurn=0,coverageRevision=null;
        async function loadCoverage(offset=0){
          const turn=++coverageTurn;browse.disabled=true;
          coverageHost.replaceChildren(el('p','Loading attribute coverage counts…','muted'));
          try{
            const query=offset?'?offset='+offset+'&revision='+coverageRevision:'';
            const page=await getJSON('/v1/event-logs/'+encodeURIComponent(state.logId)+'/quality/attribute-coverage'+query);
            if(generation!==state.generation||!coverageHost.isConnected||turn!==coverageTurn)return;
            coverageRevision=page.revision;
            const previous=button('Previous attributes','secondary'),next=button('Next attributes','secondary');
            previous.id='owgQualityCoveragePrevious';next.id='owgQualityCoverageNext';
            previous.disabled=offset===0;next.disabled=page.next_offset==null;
            previous.onclick=()=>loadCoverage(Math.max(0,offset-32));next.onclick=()=>loadCoverage(page.next_offset);
            coverageHost.replaceChildren(el('p','Showing '+(page.attributes.length?offset+1:0)+'–'+(offset+page.attributes.length)+' of '+Number(page.total_keys).toLocaleString()+' attribute keys across '+Number(page.events).toLocaleString()+' events. Counts are current, not the original import report.','muted'),
              table(['Attribute','Events with a value','Without'],page.attributes.map(a=>[a.attribute,Number(a.events_with_value).toLocaleString(),Number(a.events_without_value).toLocaleString()])),previous,next);
          }catch(error){
            if(generation!==state.generation||!coverageHost.isConnected||turn!==coverageTurn)return;
            const reload=button('Reload attribute coverage','secondary');reload.onclick=()=>loadCoverage();
            coverageHost.replaceChildren(el('p',error.message,'muted'),reload);
          }finally{if(generation===state.generation&&coverageHost.isConnected&&turn===coverageTurn)browse.disabled=false;}
        }
        browse.onclick=()=>loadCoverage();
      }
      const refresh=button('Refresh quality diagnostics','secondary');
      refresh.onclick=()=>{loaded=false;loadQuality();};out.appendChild(refresh);
      loaded=true;
      }).catch(error=>{
        if(generation!==state.generation)return;
        const retry=button('Retry quality diagnostics','secondary');retry.onclick=loadQuality;
        out.replaceChildren(el('div',error.message,'muted'),retry);
      }).finally(()=>{pending=false;});
    };
    details.addEventListener('toggle',()=>{if(details.open)loadQuality();});
    return box;
  }

  // Rejected CSV rows the user chose to keep: review them, then release with an explicit offset decision or discard.
  function quarantineBlock(){
    const wrap=el('div');wrap.id='owgQuarantine';show(wrap,false);
    const base='/v1/event-logs/'+encodeURIComponent(state.logId)+'/quarantine';
    const generation=state.generation;
    getJSON(base+'?limit=20').then(r=>{
      if(generation!==state.generation||(!r.rows_kept&&!r.decisions.length))return;
      show(wrap,true);
      const offset=input('owgQuarantineOffset','text','+01:00');offset.maxLength=6;
      const timeFormat=input('owgQuarantineTimestampFormat','text','e.g. %d/%m/%Y %H:%M');timeFormat.maxLength=64;
      const batch=input('owgQuarantineBatch','number');batch.min='0';batch.max='2147483647';batch.step='1';
      const rowStart=input('owgQuarantineRowStart','number');rowStart.min='0';rowStart.max='999999999';rowStart.step='1';
      const rowEnd=input('owgQuarantineRowEnd','number');rowEnd.min='0';rowEnd.max='999999999';rowEnd.step='1';
      const readScope=()=>{
        const raw=[batch.value.trim(),rowStart.value.trim(),rowEnd.value.trim()];
        if(raw.some(x=>x&&!/^\d+$/.test(x)))throw new Error('Use whole numbers for batch and row range.');
        const nums=raw.map(x=>x?Number(x):null);
        if(nums.some(x=>x!==null&&!Number.isSafeInteger(x))||nums[0]>2147483647||nums[1]>999999999||nums[2]>999999999)throw new Error('Use whole numbers within the allowed batch and row limits.');
        if((nums[1]!==null||nums[2]!==null)&&(nums[0]===null||nums[1]===null||nums[2]===null||nums[2]<nums[1]))
          throw new Error('Select a batch and both ends of an inclusive row range.');
        return {...(nums[0]!==null?{batch:nums[0]}:{}),...(nums[1]!==null?{row_start:nums[1],row_end:nums[2]}:{})};
      };
      const preview=button('Preview release','secondary');preview.id='owgQuarantinePreview';
      const release=button('Release rows','secondary');release.id='owgQuarantineRelease';
      const discard=button('Discard kept rows','secondary');discard.id='owgQuarantineDiscard';
      const message=el('div',null,'muted');message.id='owgQuarantineStatus';message.setAttribute('role','status');
      let reviewed=null;
      let previewGeneration=0;
      preview.disabled=discard.disabled=!r.rows_kept;
      release.disabled=true;
      const pageHost=el('div');pageHost.id='owgQuarantineRows';
      const firstPage=button('First rows','secondary');firstPage.id='owgQuarantineFirst';
      const nextPage=button('Next rows','secondary');nextPage.id='owgQuarantineNext';
      let pageTurn=0,nextCursor=null;
      const renderPage=page=>{
        const rows=page.rows.map(x=>{
          const selectRow=button('Select row','secondary');
          selectRow.setAttribute('aria-label','Select quarantine batch '+x.batch+' row '+x.row);
          selectRow.onclick=()=>{
            batch.value=String(x.batch);
            rowStart.value=rowEnd.value=String(x.row);
            batch.dispatchEvent(new Event('input',{bubbles:true}));
            rowStart.focus();
          };
          return [x.batch===0?'import (0)':x.batch,x.row,x.case,x.activity,x.time,
            x.reasons.map(k=>REASON_LABELS[k]||k).join(', '),selectRow];
        });
        pageHost.replaceChildren(rows.length?table(['Batch','Row','Case','Activity','Time as supplied','Why rejected','Review'],rows)
          :el('div','No retained rows in this batch.','muted'));
        nextCursor=page.next_after;nextPage.disabled=nextCursor===null||nextCursor===undefined;
      };
      const loadPage=async after=>{
        const request=++pageTurn;nextPage.disabled=firstPage.disabled=true;
        try{
          const chosen=batch.value.trim();
          if(chosen&&(!/^\d+$/.test(chosen)||!Number.isSafeInteger(Number(chosen))||Number(chosen)>2147483647))throw new Error('Use a valid batch number.');
          const params=new URLSearchParams({limit:'20',after:String(after)});
          if(chosen)params.set('batch',chosen);
          const page=await getJSON(base+'?'+params);
          if(generation!==state.generation||request!==pageTurn)return;
          renderPage(page);
        }catch(error){if(generation===state.generation&&request===pageTurn)message.textContent=error.message;}
        finally{if(generation===state.generation&&request===pageTurn){firstPage.disabled=false;nextPage.disabled=nextCursor===null||nextCursor===undefined;}}
      };
      firstPage.onclick=()=>loadPage(0);nextPage.onclick=()=>{if(nextCursor!==null&&nextCursor!==undefined)loadPage(nextCursor);};
      renderPage(r);
      const batchesHost=el('div'),batchesStatus=el('div',null,'muted');
      const batchesFirst=button('First batches'),batchesNext=button('Next batches');
      batchesFirst.id='owgQuarantineBatchesFirst';batchesNext.id='owgQuarantineBatchesNext';
      let batchesTurn=0,batchesCursor=r.batches_next_after??null,batchesStart=0;
      const renderBatches=(page,start)=>{
        const batches=page.batches||[],total=Number(page.total??r.batches_total??batches.length);
        batchesCursor=page.next_after??null;batchesStart=start;
        const mapped=batches.map(item=>{
          const selectBatch=button('Select batch','secondary');
          selectBatch.setAttribute('aria-label','Select quarantine batch '+item.batch);
          selectBatch.onclick=()=>{
            batch.value=String(item.batch);
            rowStart.value=rowEnd.value='';
            batch.dispatchEvent(new Event('input',{bubbles:true}));
            batch.focus();
          };
          return [item.batch===0?'Import (0)':'Append '+item.batch,Number(item.rows).toLocaleString(),selectBatch];
        });
        batchesHost.replaceChildren(...(mapped.length?[table(['Batch','Rows kept','Review'],mapped)]:[]));
        batchesStatus.textContent=total?(batches.length?'Showing '+(start+1).toLocaleString()+'–'+(start+batches.length).toLocaleString()+' of '+total.toLocaleString()+' batches':'No batches on this page; '+total.toLocaleString()+' in total')+'. History can change between pages.':'';
        show(batchesFirst,total>100||start>0);show(batchesNext,total>100||start>0);
        batchesFirst.disabled=start===0;batchesNext.disabled=batchesCursor===null;
      };
      const loadBatches=async(after,start)=>{
        const request=++batchesTurn;batchesFirst.disabled=true;batchesNext.disabled=true;
        try{const page=await getJSON(base+'/batches?after='+after+'&limit=100');
          if(generation===state.generation&&request===batchesTurn)renderBatches(page,start);
        }catch(error){if(generation===state.generation&&request===batchesTurn){batchesStatus.textContent=error.message;batchesFirst.disabled=false;batchesNext.disabled=batchesCursor===null;}}
      };
      batchesFirst.onclick=()=>loadBatches(-1,0);batchesNext.onclick=()=>{if(batchesCursor!==null)loadBatches(batchesCursor,batchesStart+100);};
      renderBatches({batches:r.batches||[],total:r.batches_total??(r.batches||[]).length,next_after:r.batches_next_after??null},0);
      const decisionHost=el('div'),decisionStatus=el('div',null,'muted');
      const decisionFirst=button('First decisions'),decisionNext=button('Next decisions');
      decisionFirst.id='owgQuarantineDecisionsFirst';decisionNext.id='owgQuarantineDecisionsNext';
      let decisionTurn=0,decisionNextOffset=r.decisions_next_offset??null;
      const scopeText=({batch,row_start:start,row_end:end})=>{
        if(batch==null)return 'all kept rows';
        if(start==null)return 'all kept rows in batch '+batch;
        return (start===end?'row '+start:'rows '+start+'–'+end)+' in batch '+batch;
      };
      const timestampDecisionText=({timestamp_format:format,naive_timestamp_offset:assumed})=>{
        const offsetText=assumed?'assuming UTC'+(assumed==='Z'?'+00:00':assumed):'';
        return [format?'using '+format:'',offsetText].filter(Boolean).join(' ');
      };
      const renderDecisions=(page)=>{
        const decisions=page.decisions||[],total=Number(page.total??r.decisions_total??decisions.length),start=Number(page.offset||0);
        decisionNextOffset=page.next_offset??null;
        decisionHost.replaceChildren(...(decisions.length?[table(['Decision','Scope','When','Result'],decisions.map(d=>[d.kind==='release'?(d.timestamp_format?'Released '+timestampDecisionText(d):'Released with offset '+d.naive_timestamp_offset):'Discarded',scopeText(d),d.decided_at.slice(0,16).replace('T',' ')+' UTC',
          d.kind==='release'?d.rows_released+' of '+d.rows_considered+' rows released, '+(d.events_added||0)+' events added, '+(d.duplicates_skipped||0)+' already in the log':d.rows_discarded+' rows discarded']))]:[]));
        decisionStatus.textContent=total?(decisions.length?'Showing '+(start+1).toLocaleString()+'–'+(start+decisions.length).toLocaleString()+' of '+total.toLocaleString()+' decisions':'No decisions on this page; '+total.toLocaleString()+' in total')+'. History can change between pages.':'';
        show(decisionFirst,total>100);show(decisionNext,total>100);
        decisionFirst.disabled=start===0;decisionNext.disabled=decisionNextOffset===null;
      };
      const loadDecisions=async offset=>{
        const request=++decisionTurn;decisionFirst.disabled=true;decisionNext.disabled=true;
        try{const page=await getJSON(base+'/decisions?offset='+offset+'&limit=100');
          if(generation===state.generation&&request===decisionTurn)renderDecisions(page);
        }catch(error){if(generation===state.generation&&request===decisionTurn){decisionStatus.textContent=error.message;decisionFirst.disabled=false;decisionNext.disabled=decisionNextOffset===null;}}
      };
      decisionFirst.onclick=()=>loadDecisions(0);decisionNext.onclick=()=>{if(decisionNextOffset!==null)loadDecisions(decisionNextOffset);};
      renderDecisions({decisions:r.decisions,total:r.decisions_total??r.decisions.length,offset:0,next_offset:r.decisions_next_offset??null});
      const row=el('div',null,'row');row.style.alignItems='end';
      row.append(field('Timestamp format (blank for ISO 8601)',timeFormat),field('UTC offset to assume for times written without one',offset),field('Batch (blank for all)',batch),field('First row (optional)',rowStart),field('Last row (optional)',rowEnd),preview,release,discard);
      wrap.append(el('h4','Rejected rows kept for review'),
        el('div',Number(r.rows_kept).toLocaleString()+' rows kept ('+reasonText(r.reasons)+'). '+r.note,'muted'),
        batchesStatus,batchesHost,batchesFirst,batchesNext,pageHost,firstPage,nextPage,
        el('div','Batch and row range apply to release and discard. Leave them blank to include all kept rows. For release, leave the assumed offset blank if the format includes %z.','muted'),row,message,
        decisionStatus,decisionHost,decisionFirst,decisionNext);
      for(const control of [offset,timeFormat,batch,rowStart,rowEnd])control.addEventListener('input',()=>{
        previewGeneration++;reviewed=null;release.disabled=true;
        message.textContent='Selection changed. Preview again before releasing.';
      });
      batch.addEventListener('input',()=>{pageTurn++;nextCursor=null;nextPage.disabled=true;firstPage.disabled=false;if(!batch.value.trim()||/^\d+$/.test(batch.value.trim()))loadPage(0);});
      preview.onclick=async()=>{
        const requestId=++previewGeneration;
        reviewed=null;release.disabled=true;
        const chosen=offset.value.trim();
        const pattern=timeFormat.value.trim();
        let scope;try{scope=readScope();}catch(error){message.textContent=error.message;return;}
        message.textContent='Checking retained rows without changing the log…';
        try{
          const result=await (await api(base+'/preview',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({naive_timestamp_offset:chosen,...(pattern?{timestamp_format:pattern}:{}),...scope})})).json();
          if(generation!==state.generation||requestId!==previewGeneration||offset.value.trim()!==chosen||timeFormat.value.trim()!==pattern)return;
          if(result.naive_timestamp_offset!==chosen||(result.timestamp_format||'')!==pattern){
            message.textContent='The preview used different timestamp settings. Check the format and offset, then preview again.';
            return;
          }
          reviewed={...result,scope};
          release.disabled=!(Number.isSafeInteger(result.rows_eligible)&&result.rows_eligible>0);
          message.textContent='Preview: '+result.rows_eligible+' of '+result.rows_considered+
            ' retained rows parseable '+timestampDecisionText(result)+'; '+
            result.rows_still_rejected+' remain rejected. No rows changed. '+
            'Duplicate skipping may reduce the number of events added. Release is a separate decision.';
        }catch(error){if(generation===state.generation&&requestId===previewGeneration)message.textContent=error.message;}
      };
      release.onclick=async()=>{
        if(!reviewed||release.disabled||generation!==state.generation)return;
        const selected=reviewed;
        previewGeneration++;reviewed=null;release.disabled=true;
        try{
          const result=await (await api(base+'/release',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({naive_timestamp_offset:selected.naive_timestamp_offset,
              ...(selected.timestamp_format?{timestamp_format:selected.timestamp_format}:{}),
              ...selected.scope,expected_preview_sha256:selected.expected_preview_sha256})})).json();
          if(generation!==state.generation)return;
          const d=result.decision;
          await refreshLogs(state.logId);await analyse();
          status('Released '+d.rows_released+' of '+d.rows_considered+' kept rows '+timestampDecisionText(d)+': '+(d.events_added||0)+' events added; '+d.rows_still_kept+' rows still kept.');
        }catch(error){if(generation===state.generation)message.textContent=error.message+' Preview again before releasing.';}
      };
      discard.onclick=async()=>{
        if(generation!==state.generation||discard.disabled)return;
        let scope;
        try{scope=readScope();}catch(error){message.textContent=error.message;return;}
        if(!window.confirm('Permanently discard '+scopeText(scope)+'?'))return;
        previewGeneration++;reviewed=null;release.disabled=true;
        discard.disabled=true;
        try{
          const result=await (await api(base+'/discard',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify(scope)})).json();
          if(generation!==state.generation)return;
          const refreshed=analyse(),refreshGeneration=state.generation;
          await refreshed;
          if(refreshGeneration===state.generation){
            const count=result.decision.rows_discarded;
            status('Discarded '+count.toLocaleString()+' kept '+(count===1?'row':'rows')+'.');
          }
        }catch(error){
          if(generation===state.generation)message.textContent=error.message;
          discard.disabled=false;
        }
      };
    }).catch(()=>{});
    return wrap;
  }

  // Compare this log with another saved log: KPIs, activities and paths present in only one, and case-share differences.
  function logComparisonSection(){
    const box=section('Compare event logs');box.id='owgLogComparison';
    box.appendChild(el('div','Compare two saved logs or two case selections from this log. Activities match by exact name after each log’s activity mapping.','muted'));
    const others=state.logs.filter(l=>l.log_id!==state.logId);
    const other=select('owgLogCompareOther',[...others.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']),
      [state.logId,'This log (compare two case selections)']]);
    const otherType=select('owgLogCompareCaseType',[['','Log default']]);
    const run=button('Compare logs','secondary');run.id='owgLogCompareRun';
    const save=button('Download comparison JSON','secondary');save.id='owgLogCompareJson';save.disabled=true;
    const out=el('div');out.id='owgLogCompareResults';out.setAttribute('role','status');
    const evidence=el('div');evidence.id='owgLogCompareEvidence';
    const options=document.createElement('details');options.appendChild(el('summary','Case selection'));
    const baseFrom=input('owgLogCompareBaselineFrom','date'),baseBefore=input('owgLogCompareBaselineBefore','date');
    const otherFrom=input('owgLogCompareOtherFrom','date'),otherBefore=input('owgLogCompareOtherBefore','date');
    const active=input('owgLogCompareUseActiveFilter','checkbox');
    active.disabled=!filterOn();
    const dates=el('div',null,'row');
    dates.append(field('Baseline cases start on or after (UTC)',baseFrom),field('Baseline cases start before (UTC)',baseBefore),
      field('Comparison cases start on or after (UTC)',otherFrom),field('Comparison cases start before (UTC)',otherBefore));
    const typeStatus=el('div',null,'muted');typeStatus.setAttribute('role','status');
    options.append(field('Comparison case notion',otherType),typeStatus,dates,field('Use the workspace case filter for the baseline',active));
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Comparison log',other),run,save);
    box.append(row,options,out,evidence);
    const generation=state.generation,baseId=state.logId,baseType=state.caseType;
    const enabled=()=>generation===state.generation&&box.isConnected;
    let typeTurn=0,runTurn=0,result=null,loadedTypes=null;
    const invalidate=()=>{++runTurn;run.disabled=false;save.disabled=true;result=null;out.replaceChildren();evidence.replaceChildren();};
    async function loadTypes(){
      const selected=other.value,ticket=++typeTurn;
      const current=()=>enabled()&&ticket===typeTurn&&other.value===selected;
      setOptions(otherType,[['','Log default']]);otherType.disabled=true;
      typeStatus.textContent='Loading case notions…';
      try{
        const response=await getJSON('/v1/event-logs/'+encodeURIComponent(selected)+'/object-types');
        if(!current())return;
        setOptions(otherType,[['','Log default'],...response.object_types.map(item=>[item.object_type,item.object_type])]);
        loadedTypes=selected;
        typeStatus.textContent=response.object_types.length?'':'This log has no case notions.';
      }catch(error){if(current())typeStatus.textContent='Could not load case notions. '+error.message;}
      finally{if(current())otherType.disabled=false;}
    }
    other.onchange=()=>{
      invalidate();++typeTurn;loadedTypes=null;typeStatus.textContent='';
      setOptions(otherType,[['','Log default']]);otherType.disabled=false;
      if(options.open)loadTypes();
    };
    options.addEventListener('toggle',()=>{if(options.open&&loadedTypes!==other.value&&!otherType.disabled)loadTypes();});
    otherType.onchange=active.onchange=invalidate;
    for(const date of [baseFrom,baseBefore,otherFrom,otherBefore])date.oninput=invalidate;
    save.onclick=()=>{if(result&&enabled())download('event-log-comparison.json',JSON.stringify(result,null,2),'application/json');};
    const datesIn=(start,end,cohort={})=>{
      const picked={...cohort};
      if(start.value)picked.started_from=start.value+'T00:00:00Z';
      if(end.value)picked.started_before=end.value+'T00:00:00Z';
      if(picked.started_from&&picked.started_before&&Date.parse(picked.started_from)>=Date.parse(picked.started_before)){
        throw new Error('A start date must be earlier than its end date.');
      }
      return picked;
    };
    const shareCell=r=>r.share_difference===null?'—':(r.share_difference>0?'+':'')+(r.share_difference*100).toFixed(1)+' pts'+(r.interval_excludes_zero?' *':'');
    const signed=(v,f)=>v===null||v===undefined?'—':(v>0?'+':v<0?'−':'')+f(Math.abs(v));
    function reviewCases(side,scope,selector,label,reportCurrent){
      const panel=el('div');
      const heading=el('h4',label);heading.tabIndex=-1;
      const status=el('div',null,'muted');status.setAttribute('role','status');
      const list=el('div'),events=el('div');
      const next=button('Next case page','secondary'),first=button('First case page','secondary');
      const controls=el('div',null,'row');controls.append(first,next);
      panel.append(heading,status,list,controls,events);
      evidence.replaceChildren(panel);heading.focus();
      const current=()=>reportCurrent()&&panel.isConnected;
      const endpoint='/v1/event-logs/'+encodeURIComponent(side.log_id)+'/';
      const query='?case_type='+encodeURIComponent(side.case_notion);
      const post=async(route,body)=>(await api(endpoint+route+query,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
      let cursor=null;
      async function loadCases(after){
        first.disabled=next.disabled=true;list.replaceChildren();events.replaceChildren();
        status.textContent='Loading cases…';
        try{
          const found=await post('cases/query',{cohort:scope.cohort,...selector,limit:30,after});
          if(!current())return;
          cursor=found.next_after;
          status.textContent=found.selected_cases.toLocaleString()+' matching cases in '+side.name+
            ' ('+found.case_notion+'). Showing '+found.cases.length+' on this page.';
          if(found.cases.length)list.appendChild(table(['Supplied case key','Events','Elapsed time','Equal-timestamp events'],
            found.cases.map(c=>{
              const open=button(c.case_id,'secondary');open.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%';
              open.setAttribute('aria-label','Open case '+c.case_id);open.onclick=()=>openCase(c.case_id);
              return [open,c.events,duration(c.elapsed_seconds),c.timestamp_ties];
            })));
          first.disabled=after===null;next.disabled=cursor===null;
        }catch(error){
          if(!current())return;
          status.textContent=error.message;
          const retry=button('Retry loading cases','secondary');retry.onclick=()=>loadCases(after);list.appendChild(retry);
        }
      }
      function openCase(caseId){
        const detail=el('div'),title=el('h4','Case '+caseId);title.tabIndex=-1;
        const progress=el('div',null,'muted');progress.setAttribute('role','status');
        const rows=el('div'),more=button('More case events','secondary');
        detail.append(title,progress,rows,more);events.replaceChildren(detail);title.focus();
        let offset=0;
        async function loadEvents(){
          more.disabled=true;progress.textContent='Loading events…';
          try{
            const found=await post('case-events/query',{case_id:caseId,offset,limit:100});
            if(!current()||!detail.isConnected)return;
            progress.textContent='Showing '+(offset+found.events.length)+' of '+found.total_events+' events. '+
              found.timestamp_ties+' equal-timestamp events. '+found.ordering;
            rows.appendChild(table(['Activity','Timestamp (UTC)','Resource label','Event ID'],
              found.events.map(e=>[e.activity,e.timestamp,e.resource||'(missing)',e.event_id])));
            offset=found.next_offset;show(more,offset!==null);more.disabled=offset===null;
            more.textContent='More case events';
          }catch(error){
            if(!current()||!detail.isConnected)return;
            progress.textContent=error.message;more.textContent='Retry loading events';more.disabled=false;
          }
        }
        more.onclick=loadEvents;loadEvents();
      }
      first.onclick=()=>loadCases(null);next.onclick=()=>loadCases(cursor);loadCases(null);
    }
    run.onclick=async()=>{
      const ticket=++runTurn;
      const current=()=>enabled()&&ticket===runTurn;
      result=null;save.disabled=true;
      run.disabled=true;evidence.replaceChildren();out.replaceChildren(el('div','Comparing…','muted'));
      try{
        const spec={baseline:{log_id:baseId,cohort:datesIn(baseFrom,baseBefore,active.checked?globalCohort():{})},
          comparison:{log_id:other.value,cohort:datesIn(otherFrom,otherBefore)},limit:15};
        if(baseType)spec.baseline.case_type=baseType;
        if(otherType.value)spec.comparison.case_type=otherType.value;
        const r=await (await api('/v1/event-logs/comparison',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(!current())return;
        const b=r.kpis.baseline,c=r.kpis.comparison,d=r.kpis.difference;
        const casesButton=(side,count,selector,label,share)=>{
          const text=count.toLocaleString()+(count===1?' case':' cases')+(share===undefined?'':' · '+pct(share));
          if(!count)return text;
          const sideLabel=side==='baseline'?'Baseline':'Comparison';
          const control=button(text,'secondary');
          control.setAttribute('aria-label','Review '+sideLabel.toLowerCase()+' cases for '+label+' ('+count+')');
          control.setAttribute('aria-controls',evidence.id);
          control.onclick=()=>reviewCases(r[side],spec[side],selector,sideLabel+' cases for '+label,current);
          return control;
        };
        const kpiTable=table(['Measure',r.baseline.name+' (this log)',r.comparison.name,'Difference'],[
          ['Cases',casesButton('baseline',b.cases,{},'entire selection'),casesButton('comparison',c.cases,{},'entire selection'),signed(d.cases,v=>Number(v).toLocaleString())],
          ['Median case time',duration(b.duration_seconds.median),duration(c.duration_seconds.median),signed(d.median_duration_seconds,duration)],
          ['90th percentile case time',duration(b.duration_seconds.p90),duration(c.duration_seconds.p90),signed(d.p90_duration_seconds,duration)],
          ['Events per case',String(b.events_per_case),String(c.events_per_case),signed(d.events_per_case,v=>v.toFixed(2))],
          ['Cases repeating an activity',pct(b.repeat_rate),pct(c.repeat_rate),signed(d.repeat_rate,v=>(v*100).toFixed(1)+' pts')],
          ['Distinct variants',Number(b.distinct_variants).toLocaleString(),Number(c.distinct_variants).toLocaleString(),'']]);
        const longer=r.duration.probability_comparison_case_takes_longer;
        const only=(label,list)=>list.length?el('div',label+': '+list.slice(0,20).join(', ')+(list.length>20?' and '+(list.length-20)+' more':'')+'.'):null;
        const shareRows=(rows,describe)=>rows.map(item=>{
          const {label,selector}=describe(item);
          return [label,casesButton('baseline',item.baseline_cases,selector,label,item.baseline_share),
            casesButton('comparison',item.comparison_cases,selector,label,item.comparison_share),shareCell(item)];
        });
        const actRows=shareRows(r.activities.largest_share_differences,item=>({
          label:item.activity,selector:{required_activities:[item.activity]}}));
        const pathRows=shareRows(r.paths.largest_share_differences,item=>({
          label:item.from+' → '+item.to,selector:{transition:[item.from,item.to]}}));
        const variantRows=shareRows(r.variants.largest_share_differences,item=>({
          label:item.path.slice(0,20).join(' → ')+(item.path.length>20?' … ('+item.path.length+' steps)':''),
          selector:{variant_id:item.variant_id}}));
        const selection=side=>{
          const {started_from:start,started_before:end}=side.cohort;
          return side.name+' uses '+side.case_notion+(start||end?' cases starting'+
            (start?' on or after '+start.slice(0,10):'')+(start&&end?' and':'')+(end?' before '+end.slice(0,10):'')+' UTC':' cases')+'.';
        };
        const headers=label=>[label,'Baseline · '+r.baseline.name,'Comparison · '+r.comparison.name,'Difference'];
        out.replaceChildren(el('p','Baseline '+selection(r.baseline)+' Comparison '+selection(r.comparison),'muted'),kpiTable,
          el('div','A random case in '+r.comparison.name+' takes longer than a random case in '+r.baseline.name+' '+pct(longer)+' of the time (50% means no shift).','muted'),
          ...[only('Only in '+r.baseline.name,r.activities.only_baseline),only('Only in '+r.comparison.name,r.activities.only_comparison)].filter(Boolean),
          el('h4','Activities: share of cases'),table(headers('Activity'),actRows),
          el('h4','Paths: share of cases'),table(headers('Path'),pathRows),
          el('h4','Variants: share of cases'),table(headers('Variant'),variantRows),
          el('div',r.variants.shared_variants.toLocaleString()+' variants occur in both logs; they cover '+pct(r.variants.baseline_cases_in_shared_variants)+' of '+r.baseline.name+' cases and '+pct(r.variants.comparison_cases_in_shared_variants)+' of '+r.comparison.name+' cases.','muted'),
          el('div','* the 95% interval of the difference excludes zero. '+r.interpretation,'muted'));
        result=r;save.disabled=false;
      }catch(error){if(current())out.replaceChildren(el('div',error.message,'muted'));}
      finally{if(current())run.disabled=false;}
    };
    return box;
  }

  // A differentially private process map that can be shared without revealing any single case.
  function privateMapSection(){
    const box=section('Share a private process map');box.id='owgPrivateMap';
    box.appendChild(el('div','Creates a map of activities and the paths between them with calibrated random noise (differential privacy), so the result hardly changes whether or not any one case is in the log. It contains no case, time, resource or attribute, but activity names are shown as they are. Every map you create draws new noise and uses up privacy again. It covers every case of this log, not the case filter.','muted'));
    const eps=input('owgPrivateMapEpsilon','number');eps.min='0.01';eps.max='10';eps.step='0.1';eps.value='1';
    const k=input('owgPrivateMapK','number');k.min='1';k.max='100';k.step='1';k.value='10';
    const run=button('Create private map','secondary');run.id='owgPrivateMapRun';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Privacy budget ε (smaller is more private)',eps),field('Most transitions counted per case',k),run);
    const out=el('div');out.id='owgPrivateMapResults';out.setAttribute('role','status');
    box.append(row,out);
    run.onclick=async()=>{
      const generation=state.generation;run.disabled=true;out.replaceChildren(el('div','Adding noise…','muted'));
      try{
        const body={epsilon:Number(eps.value),max_transitions_per_case:Number(k.value)};
        if(!Number.isFinite(body.epsilon)||!Number.isInteger(body.max_transitions_per_case))throw new Error('Enter a number for ε and a whole number of transitions.');
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/private-map'+(state.caseType?'?case_type='+encodeURIComponent(state.caseType):''),
          {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
        if(generation!==state.generation)return;
        const p=r.privacy,own=r.owner_only_not_private;
        const shared={...r};delete shared.owner_only_not_private;
        const save=button('Download shareable map (JSON)','secondary');save.id='owgPrivateMapDownload';
        save.onclick=()=>download(state.logId+'-private-map.json',JSON.stringify(shared,null,2),'application/json');
        out.replaceChildren(
          el('div','ε = '+p.epsilon+': '+r.edges.length+' paths and '+r.activities.length+' activities released. Noise scale '+p.noise.edge_scale+' on path counts; paths below '+p.release_threshold.edges+' are left out.','muted'),
          el('div','Only you see this: '+Number(own.cases).toLocaleString()+' cases, '+Number(own.cases_clipped).toLocaleString()+' longer than the transition cap (a random part of those was counted). It is not in the download.','muted'),
          table(['From','To','About'],r.edges.slice(0,40).map(e=>[e.from,e.to,Number(e.count).toLocaleString()])),
          save,el('div',r.note,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function organizationSection(inspection){
    const box=section('Who hands work to whom');box.id='owgEventLogOrganization';
    box.appendChild(el('div','Handovers and shared cases between the resources in this log. Resources are shown as Resource 01, 02… unless you choose to show names. No speed, waiting time or performance score is calculated for anyone.','muted'));
    const namesLabel=el('label');namesLabel.style.cssText='display:flex;gap:6px;align-items:center';
    const names=input('owgEventLogOrgNames','checkbox');namesLabel.append(names,document.createTextNode(' Show resource names from the log'));
    const run=button('Show handovers','secondary');run.id='owgEventLogOrgRun';
    const rolesRun=button('Discover roles','secondary');rolesRun.id='owgEventLogRolesRun';
    const threshold=input('owgEventLogRolesThreshold','number');threshold.value='0.65';threshold.min='0.01';threshold.max='0.99';threshold.step='0.01';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(namesLabel,run,field('Role similarity threshold',threshold),rolesRun);
    const out=el('div');out.id='owgEventLogOrgResults';out.setAttribute('role','status');
    const evidence=el('div');evidence.id='owgEventLogOrgCaseReview';
    box.append(row,out,evidence);
    let request=0;
    const clear=()=>{request++;out.replaceChildren();evidence.replaceChildren();};
    names.addEventListener('change',clear);threshold.addEventListener('input',clear);
    function reviewCases(kind,pair,report,current){
      const panel=el('div');
      const titles={handover:'Handover',return:'Return handover',shared:'Shared cases'};
      const heading=el('h4',titles[kind]+' · '+pair.join(kind==='shared'?' + ':' → '));heading.tabIndex=-1;
      const message=el('div',null,'muted');message.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const first=button('First case page','secondary'),next=button('Next case page','secondary');
      nav.append(first,next);panel.append(heading,message,rows,nav);
      evidence.replaceChildren(panel);heading.focus();
      const valid=()=>current()&&names.checked&&panel.isConnected;
      let cursor=null;
      async function load(after){
        first.disabled=next.disabled=true;rows.replaceChildren();message.textContent='Loading cases…';
        try{
          const endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/organization/cases?case_type='+encodeURIComponent(report.case_notion);
          const found=await (await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({names:'shown',kind,resources:pair,cohort:report.cohort,after,limit:30})})).json();
          if(!valid())return;
          cursor=found.next_after;
          message.textContent=found.selected_cases.toLocaleString()+' matching cases. Showing '+found.cases.length+' on this page.';
          if(found.cases.length){
            rows.appendChild(table(['Case',...(kind==='shared'?[]:['Matches in case'])],found.cases.map(item=>{
              const open=button(item.case_id,'secondary');open.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%';
              open.setAttribute('aria-label','Open case '+item.case_id);
              open.onclick=()=>{if(valid())inspection.open(item.case_id);};
              return [open,...(kind==='shared'?[]:[item.matches])];
            })));
          }
          first.disabled=after===null;next.disabled=cursor===null;
        }catch(error){
          if(!valid())return;
          message.textContent=error.message;
          const retry=button('Retry loading cases','secondary');retry.onclick=()=>load(after);rows.appendChild(retry);
        }
      }
      first.onclick=()=>load(null);next.onclick=()=>load(cursor);load(null);
    }
    rolesRun.onclick=async()=>{
      const generation=state.generation,ticket=++request;
      try{
        rolesRun.disabled=true;evidence.replaceChildren();out.replaceChildren(el('div','Grouping resources by the activities they perform…','muted'));
        const cutoff=Number(threshold.value);
        if(!(cutoff>0&&cutoff<1))throw new Error('Choose a role similarity threshold above 0 and below 1.');
        const r=await getJSON(logPath('roles',{names:names.checked?'shown':'pseudonymous',threshold:cutoff}));
        if(generation!==state.generation||ticket!==request)return;
        if(!r.roles.length){
          out.textContent=r.coverage.events_with_resource
            ? 'No events fall within both the resource and activity analysis limits. Try a narrower case filter.'
            : 'No events in the selected cases have a resource label.';
          return;
        }
        out.replaceChildren(
          el('p',r.roles.length+' roles from '+r.coverage.resources_represented+' resources whose activity mixes correlate at '+r.threshold+' or more.','muted'),
          el('p','Coverage: '+Number(r.coverage.events_represented).toLocaleString()+' of '+
            Number(r.coverage.events_with_resource).toLocaleString()+' events with a resource, '+
            Number(r.coverage.resources_represented).toLocaleString()+' of '+Number(r.coverage.resources).toLocaleString()+' resources, and '+
            Number(r.coverage.activities_represented).toLocaleString()+' of '+Number(r.coverage.activities).toLocaleString()+' activities.','muted'),
          table(['Role','Members','Events','Main activities','Only this role'],r.roles.map(x=>[x.role,x.members.join(', '),Number(x.events).toLocaleString(),
            x.activity_mix.slice(0,4).map(a=>a.activity+' '+Math.round(a.share*100)+'%').join(', '),x.exclusive_activities.join(', ')||'\u2014'])),
          el('div','“Only this role” checks every resource label in the selected cases. '+r.interpretation,'muted'));
      }catch(error){if(generation===state.generation&&ticket===request)out.textContent=error.message;}
      finally{rolesRun.disabled=false;}
    };
    run.onclick=async()=>{
      const generation=state.generation,ticket=++request;
      const current=()=>generation===state.generation&&ticket===request&&box.isConnected;
      try{
        evidence.replaceChildren();out.replaceChildren(el('div','Reading resources…','muted'));
        const r=await getJSON(logPath('organization',{names:names.checked?'shown':'pseudonymous'}));
        if(!current())return;
        if(!r.resources.length){out.replaceChildren(el('div','This log has no resource column values to analyse.','muted'));return;}
        const m=r.activity_resource_matrix,named=new Set(r.resources.map(item=>item.resource));
        const count=(kind,pair,n)=>{
          if(r.names!=='shown'||!pair.every(label=>named.has(label)))return n.toLocaleString();
          const open=button(n.toLocaleString(),'secondary');
          open.setAttribute('aria-label','Review '+kind+' cases for '+pair.join(kind==='shared'?' + ':' → '));
          open.setAttribute('aria-controls',evidence.id);
          open.onclick=()=>reviewCases(kind,pair,r,current);
          return open;
        };
        out.replaceChildren(
          el('div',Number(r.coverage.events_with_resource).toLocaleString()+' of '+Number(r.coverage.events).toLocaleString()+' events name a resource; '+Number(r.cases_with_handover).toLocaleString()+' cases contain a handover.'+(r.resources_truncated?' Less active resources are grouped as “Other resources”.':''),'muted'),
          table(['From','To','Handovers','Cases'],r.handover_of_work.slice(0,25).map(h=>[h.from,h.to,Number(h.handovers).toLocaleString(),count('handover',[h.from,h.to],h.cases)])),
          ...(r.subcontracting&&r.subcontracting.length?[el('h4','Subcontracting: work passed to another resource and straight back'),table(['From','To and back','Times','Cases'],r.subcontracting.slice(0,25).map(s=>[s.from,s.to,Number(s.times).toLocaleString(),count('return',[s.from,s.to],s.cases)]))]:[]),
          table(['Resources','Shared cases'],r.working_together.slice(0,25).map(w=>[w.resources.join(' + '),count('shared',w.resources,w.shared_cases)])),
          table(['Activity',...m.resources.slice(0,10)],m.activities.slice(0,15).map((a,i)=>[a,...m.events[i].slice(0,10).map(n=>n?Number(n).toLocaleString():'')])),
          el('div',r.interpretation+(r.working_together_excluded_cases?' '+r.working_together_excluded_cases+' cases with very many resources are left out of shared-case pairs.':''),'muted'));
      }catch(error){if(generation===state.generation&&ticket===request)out.textContent=error.message;}
    };
    return box;
  }

  const ANALYSIS_DIMENSIONS={
    cases:[['','(none)'],['start_month','Start month'],['start_week','Start week'],['end_week','End week'],['first_activity','First activity'],['last_activity','Last activity'],['variant','Variant'],['has_rework','Rework'],['attribute','Case attribute…']],
    events:[['','(none)'],['activity','Activity'],['resource','Resource'],['month','Month'],['week','Week'],['weekday','Weekday'],['hour','Hour of day'],['attribute','Event attribute…']]};
  const ANALYSIS_MEASURES={
    cases:[['count','Number of cases'],['share','Share of cases'],['median:duration_seconds','Median case time'],['p90:duration_seconds','90th percentile case time'],['avg:duration_seconds','Average case time'],['avg:events','Average events per case'],['max:events','Most events in a case']],
    events:[['count','Number of events'],['cases','Number of cases'],['share','Share of events']]};
  // Builds an analysis spec from form values; throws a readable error.
  function analysisSpec(values){
    const dims=[];
    for(const [kind,name] of [[values.dim1,values.attr1],[values.dim2,values.attr2]]){
      if(!kind)continue;
      if(kind==='attribute'){const n=String(name||'').trim();if(!n)throw new Error('Enter the attribute name.');dims.push('attribute:'+n);}else dims.push(kind);
    }
    const measures=[values.measure1,values.measure2].filter(Boolean).map(m=>{const i=m.indexOf(':');return i<0?{fn:m}:{fn:m.slice(0,i),of:m.slice(i+1)};});
    const spec={level:values.level,dimensions:dims,measures,limit:Number(values.limit)||100};
    if(values.level==='cases'&&values.metrics&&values.metrics.length)spec.metrics=values.metrics;
    if(values.level==='cases'){
      const where={};const h=Number(values.hours);
      if(values.hours!==''&&values.hours!==undefined){if(!(h>=0))throw new Error('Enter hours as a number.');where.duration_over_seconds=Math.round(h*3600);}
      if(values.endsWith)where.ends_with=values.endsWith;
      if(Object.keys(where).length)spec.where=where;
    }
    return spec;
  }

  // Restore only analysis specs the visible builder can faithfully represent.
  // Validate the complete draft before changing any form controls or metric state.
  function analysisDraft(spec,selectedCaseType=''){
    if(!spec||typeof spec!=='object'||Array.isArray(spec))throw new Error('Saved analysis has no usable definition.');
    // Stored specs echo every optional field as null; null means "not set".
    const unset=o=>o&&typeof o==='object'&&!Array.isArray(o)?Object.fromEntries(Object.entries(o).filter(([,v])=>v!==null)):o;
    const unsetMetric=m=>{const out=unset(m);
      // The default stage selector means nothing without a stage, as the builder writes it.
      if(out&&typeof out==='object'&&!out.from_activity&&!out.to_activity&&out.stage_occurrence==='first')delete out.stage_occurrence;
      return out;};
    spec={...spec,where:unset(spec.where),metrics:Array.isArray(spec.metrics)?spec.metrics.map(unsetMetric):spec.metrics};
    const allowed=new Set(['level','dimensions','measures','limit','metrics','where','case_type']);
    // A stored spec echoes server defaults; only a non-default value is an unsupported option.
    const defaults={cohort:'{}',activity_in:'[]',order:'"first_measure_desc"',time_zone:'null',time_of_week:'null'};
    if(Object.keys(spec).some(k=>!allowed.has(k)&&!(Object.hasOwn(defaults,k)&&JSON.stringify(spec[k]??null)===defaults[k])))
      throw new Error('This saved analysis uses options not supported by the builder; use Run instead.');
    if(!['cases','events'].includes(spec.level)||spec.limit!==100)throw new Error('This saved analysis cannot be edited in this builder; use Run instead.');
    if((spec.case_type||'')!==selectedCaseType)throw new Error('Select the saved analysis case notion before loading it.');
    if(!Array.isArray(spec.dimensions)||spec.dimensions.length>2)throw new Error('Saved analysis dimensions cannot be edited here.');
    const dimOptions=new Set(ANALYSIS_DIMENSIONS[spec.level].map(x=>x[0]));
    const dims=spec.dimensions.map(d=>{
      if(typeof d!=='string')throw new Error('Saved analysis has an invalid dimension.');
      if(d.startsWith('attribute:')){
        const attribute=d.slice(10);
        if(!attribute.trim())throw new Error('Saved analysis has an empty attribute dimension.');
        return {kind:'attribute',attribute};
      }
      if(!d||!dimOptions.has(d))throw new Error('Saved analysis uses an unsupported dimension.');
      return {kind:d,attribute:''};
    });
    const where=spec.where||{};
    if(!where||typeof where!=='object'||Array.isArray(where)||Object.keys(where).some(k=>!['duration_over_seconds','ends_with'].includes(k)))
      throw new Error('Saved analysis uses filters not supported by the builder.');
    if(spec.level!=='cases'&&Object.keys(where).length)throw new Error('Event analysis has unsupported case filters.');
    let hours='';
    if(Object.hasOwn(where,'duration_over_seconds')){
      const seconds=where.duration_over_seconds;
      if(!Number.isSafeInteger(seconds)||seconds<0)throw new Error('Saved analysis has an invalid duration filter.');
      hours=String(seconds/3600);
    }
    if(where.ends_with!==undefined&&typeof where.ends_with!=='string')throw new Error('Saved analysis has an invalid ending activity.');
    const metrics=spec.metrics||[];
    if(!Array.isArray(metrics)||metrics.length>4||(spec.level==='events'&&metrics.length))
      throw new Error('Saved analysis has metrics the builder cannot represent.');
    const prior=new Set();
    for(const m of metrics){
      if(!m||typeof m!=='object'||Array.isArray(m)||!METRIC_KINDS.some(k=>k[0]===m.kind))
        throw new Error('Saved analysis has an unsupported metric.');
      if(typeof m.name!=='string'||!/^[a-z][a-z0-9_]{0,39}$/.test(m.name)||prior.has(m.name))
        throw new Error('Saved analysis has invalid or duplicate metric names.');
      if(m.kind==='arithmetic'){
        if(m.expression_version!=='1'||!['add','subtract','multiply','divide','minimum','maximum','average','absolute_difference'].includes(m.operation))
          throw new Error('Saved analysis has an unsupported arithmetic expression.');
        for(const operand of [m.left,m.right]){
          if(!operand||!['metric','constant'].includes(operand.kind))throw new Error('Saved analysis has an invalid arithmetic operand.');
          if(operand.kind==='metric'&&!prior.has(operand.name))throw new Error('Saved analysis has an out-of-order metric dependency.');
          if(operand.kind==='constant'&&(!Number.isFinite(operand.value)||typeof operand.unit!=='string'||!/^[A-Za-z%$€£¥/._ -]{1,20}$/.test(operand.unit)))
            throw new Error('Saved analysis has an invalid typed constant.');
        }
      }else{
        // Reuse the form's validation rules for supported non-arithmetic metrics.
        caseMetric({name:m.name,kind:m.kind,from:m.from_activity,to:m.to_activity,
          fromOccurrence:m.from_occurrence,toOccurrence:m.to_occurrence,activity:m.activity,
          attribute:m.attribute,unit:m.unit,statistic:m.statistic,missingPolicy:m.missing_policy,
          statActivity:m.activity,stageFrom:m.from_activity,stageTo:m.to_activity,
          stageOccurrence:m.stage_occurrence});
      }
      prior.add(m.name);
    }
    if(!Array.isArray(spec.measures)||spec.measures.length<1||spec.measures.length>2)
      throw new Error('Saved analysis has an unsupported measure selection.');
    const standard=new Set(ANALYSIS_MEASURES[spec.level].map(x=>x[0]));
    const measures=spec.measures.map(m=>{
      if(!m||typeof m!=='object'||typeof m.fn!=='string')throw new Error('Saved analysis has an invalid measure.');
      const value=m.of===undefined?m.fn:m.fn+':'+m.of;
      if(!standard.has(value)&&!(spec.level==='cases'&&['median','avg','sum','max'].includes(m.fn)&&
          typeof m.of==='string'&&m.of.startsWith('metric:')&&prior.has(m.of.slice(7))))
        throw new Error('Saved analysis uses a measure not available in the builder.');
      return value;
    });
    return {level:spec.level,dim1:dims[0]?.kind||'',attr1:dims[0]?.attribute||'',
      dim2:dims[1]?.kind||'',attr2:dims[1]?.attribute||'',measure1:measures[0],
      measure2:measures[1]||'',hours,endsWith:where.ends_with||'',
      metrics:metrics.map(m=>JSON.parse(JSON.stringify(m)))};
  }

  // One typed case metric from form values; throws a readable error.
  function caseMetric(values){
    const name=String(values.name||'').trim();
    if(!/^[a-z][a-z0-9_]{0,39}$/.test(name))throw new Error('Name the metric with lower-case letters, digits and _ (for example wait_for_approval).');
    if(values.kind==='time_between'){
      if(!values.from||!values.to)throw new Error('Choose both activities.');
      return {name,kind:'time_between',from_activity:values.from,to_activity:values.to,from_occurrence:values.fromOccurrence||'first',to_occurrence:values.toOccurrence||'first'};
    }
    if(values.kind==='activity_count'){if(!values.activity)throw new Error('Choose the activity to count.');return {name,kind:'activity_count',activity:values.activity};}
    if(values.kind==='arithmetic'){
      const operand=side=>{
        if(values[side+'Kind']==='constant'){
          const raw=String(values[side+'Value']??'').trim(),value=Number(raw),unit=String(values[side+'Unit']||'ratio').trim();
          if(!raw||!Number.isFinite(value))throw new Error('Enter a finite constant for the '+side+' operand.');
          if(!/^[A-Za-z%$€£¥/._ -]{1,20}$/.test(unit))throw new Error('Enter the constant unit, or ratio for a dimensionless number.');
          return {kind:'constant',value,unit};
        }
        const ref=String(values[side+'Metric']||'');
        if(!/^[a-z][a-z0-9_]{0,39}$/.test(ref)||ref===name)throw new Error('Choose an earlier metric for the '+side+' operand.');
        return {kind:'metric',name:ref};
      };
      if(!['add','subtract','multiply','divide','minimum','maximum','average','absolute_difference'].includes(values.operation))throw new Error('Choose the arithmetic operation.');
      return {name,kind:'arithmetic',expression_version:'1',operation:values.operation,left:operand('left'),right:operand('right')};
    }
    if(values.kind==='event_attribute_statistic'){
      const attribute=String(values.attribute||'').trim(),unit=String(values.unit||'').trim();
      const statistic=String(values.statistic||''),missingPolicy=String(values.missingPolicy||'');
      const stageFrom=String(values.stageFrom||''),stageTo=String(values.stageTo||'');
      if(!attribute)throw new Error('Enter the numeric event attribute name.');
      if(!/^[A-Za-z%$€£¥/._ -]{1,20}$/.test(unit))throw new Error('Enter the unit of the values, for example EUR or kg.');
      if(!['average','minimum','maximum','median','p90','total','population_stddev'].includes(statistic))throw new Error('Choose a supported within-case statistic.');
      if(!['available','complete'].includes(missingPolicy))throw new Error('Choose how to handle missing numeric values.');
      if(!!stageFrom!==!!stageTo)throw new Error('Choose both stage start and end, or leave both empty.');
      if(!['first','last_completed'].includes(values.stageOccurrence||'first'))throw new Error('Choose the first or last completed stage.');
      const metric={name,kind:'event_attribute_statistic',attribute,unit,statistic,missing_policy:missingPolicy};
      if(values.statActivity)metric.activity=String(values.statActivity);
      if(stageFrom){metric.from_activity=stageFrom;metric.to_activity=stageTo;metric.stage_occurrence=values.stageOccurrence||'first';}
      return metric;
    }
    if(values.kind==='event_attribute_sum'||values.kind==='case_attribute_number'){
      const attribute=String(values.attribute||'').trim(),unit=String(values.unit||'').trim();
      if(!attribute)throw new Error('Enter the attribute name.');
      if(!/^[A-Za-z%$€£¥/._ -]{1,20}$/.test(unit))throw new Error('Enter the unit of the values, for example EUR or kg.');
      return {name,kind:values.kind,attribute,unit};
    }
    throw new Error('Choose what the metric measures.');
  }
  // Build a reproducible first/last comparison from the same observed stage and
  // missingness policy. The signed delta is last minus first, never an absolute change.
  function stageComparisonMetrics(baseMetric, relative=false){
    if(baseMetric.kind!=='event_attribute_statistic'||!baseMetric.from_activity||!baseMetric.to_activity)
      throw new Error('Choose a numeric event statistic with both stage start and end.');
    if(baseMetric.name.length>(relative?31:34))
      throw new Error('Use a base metric name of at most '+(relative?31:34)+' characters for the stage comparison.');
    const first={...baseMetric,name:baseMetric.name+'_first',stage_occurrence:'first'};
    const last={...baseMetric,name:baseMetric.name+'_last',stage_occurrence:'last_completed'};
    const delta={name:baseMetric.name+'_delta',kind:'arithmetic',expression_version:'1',
      operation:'subtract',left:{kind:'metric',name:last.name},right:{kind:'metric',name:first.name}};
    if(relative){
      const fraction={name:baseMetric.name+'_relative',kind:'arithmetic',expression_version:'1',
        operation:'divide',left:{kind:'metric',name:delta.name},right:{kind:'metric',name:first.name}};
      return [first,last,delta,fraction];
    }
    return [first,last,delta];
  }
  // Compose an exact-activity A/B comparison from existing typed case metrics.
  // This does not infer missing business activity or convert declared units.
  function activityComparisonMetrics(baseMetric,activityA,activityB){
    if(baseMetric.kind!=='event_attribute_statistic')
      throw new Error('Choose a numeric event statistic to compare activities.');
    if(baseMetric.from_activity||baseMetric.to_activity)
      throw new Error('Clear stage start and end before comparing two activities.');
    if(baseMetric.activity)
      throw new Error('Clear Only this activity before choosing two comparison activities.');
    if(!activityA||!activityB||activityA===activityB)
      throw new Error('Choose two different activities for the comparison.');
    if(baseMetric.name.length>34)
      throw new Error('Use a base metric name of at most 34 characters for the activity comparison.');
    const first={...baseMetric,name:baseMetric.name+'_a',activity:activityA};
    const second={...baseMetric,name:baseMetric.name+'_b',activity:activityB};
    const delta={name:baseMetric.name+'_delta',kind:'arithmetic',expression_version:'1',
      operation:'subtract',left:{kind:'metric',name:second.name},right:{kind:'metric',name:first.name}};
    return [first,second,delta];
  }
  // A selected activity's recorded contribution to the whole-case numeric total.
  // A fraction is not necessarily bounded by 0..1 when recorded values have signs.
  function activityShareMetrics(baseMetric){
    if(baseMetric.kind!=='event_attribute_statistic'||baseMetric.statistic!=='total')
      throw new Error('Choose Total as the within-case numeric statistic for an activity fraction.');
    if(!baseMetric.activity)
      throw new Error('Choose Only this activity to calculate its fraction of the whole case.');
    if(baseMetric.from_activity||baseMetric.to_activity)
      throw new Error('Clear stage start and end for a whole-case activity fraction.');
    if(baseMetric.name.length>31)
      throw new Error('Use a base metric name of at most 31 characters for an activity fraction.');
    const selected={...baseMetric,name:baseMetric.name+'_selected'};
    const whole={...baseMetric,name:baseMetric.name+'_all'};
    delete whole.activity;
    const fraction={name:baseMetric.name+'_fraction',kind:'arithmetic',expression_version:'1',
      operation:'divide',left:{kind:'metric',name:selected.name},right:{kind:'metric',name:whole.name}};
    return [selected,whole,fraction];
  }
  // Refactor a typed metric graph as one draft operation. References are exact
  // metric identifiers, not text substitutions, so constants and source
  // attribute names cannot be rewritten accidentally.
  function refactorMetricGraph(definitions,oldName,replacement,selectedMeasures){
    const index=definitions.findIndex(m=>m.name===oldName);
    if(index<0)throw new Error('Select a metric to edit first.');
    if(definitions.some((m,i)=>i!==index&&m.name===replacement.name))
      throw new Error('Another metric already has that name.');
    const rewritten=definitions.map((m,i)=>{
      const copy=JSON.parse(JSON.stringify(i===index?replacement:m));
      if(i>index&&copy.kind==='arithmetic'){
        for(const side of ['left','right']){
          if(copy[side].kind==='metric'&&copy[side].name===oldName)
            copy[side].name=replacement.name;
        }
      }
      return copy;
    });
    const earlier=new Set();
    for(const metric of rewritten){
      if(earlier.has(metric.name))throw new Error('Metric names must be unique.');
      if(metric.kind==='arithmetic')for(const operand of [metric.left,metric.right]){
        if(operand.kind==='metric'&&!earlier.has(operand.name))
          throw new Error('Arithmetic operands must reference an earlier metric.');
      }
      earlier.add(metric.name);
    }
    const measures=selectedMeasures.map(value=>{
      if(typeof value!=='string')return value;
      for(const fn of ['median','avg','sum','max']){
        if(value===fn+':metric:'+oldName)return fn+':metric:'+replacement.name;
      }
      return value;
    });
    return {metrics:rewritten,measures};
  }
  // Load saved typed metrics as a dependency-ordered, all-or-nothing draft.
  // Saved templates belong to the current log; never substitute a different
  // same-name definition or turn a missing operand into an observed zero.
  function savedMetricBundle(target,savedEntries,currentMetrics){
    const validName=name=>typeof name==='string'&&/^[a-z][a-z0-9_]{0,39}$/.test(name);
    const normalized=value=>{
      if(Array.isArray(value))return value.map(normalized);
      if(value&&typeof value==='object')
        return Object.fromEntries(Object.keys(value).sort().map(key=>[key,normalized(value[key])]));
      return value;
    };
    const identical=(a,b)=>JSON.stringify(normalized(a))===JSON.stringify(normalized(b));
    if(!target||!validName(target.name))throw new Error('Invalid saved metric name.');
    const saved=new Map(),ambiguous=new Set();
    for(const entry of savedEntries){
      const metric=entry.metric||entry;
      if(!metric||!validName(metric.name))continue;
      if(saved.has(metric.name)&&!identical(saved.get(metric.name),metric))ambiguous.add(metric.name);
      else saved.set(metric.name,metric);
    }
    const current=new Map(currentMetrics.map(metric=>[metric.name,metric]));
    if(current.has(target.name))throw new Error('This analysis already has a metric named '+target.name+'.');
    const added=[],done=new Set(),visiting=new Set();
    const visit=name=>{
      if(!validName(name))throw new Error('A saved metric has an invalid dependency name.');
      if(ambiguous.has(name))throw new Error('Saved metric '+name+' has conflicting templates; resolve them before adding.');
      const metric=saved.get(name);
      if(current.has(name)){
        if(metric&&!identical(metric,current.get(name)))
          throw new Error('Metric '+name+' already exists with a different definition.');
        return;
      }
      if(!metric)throw new Error('Missing saved dependency '+name+'. Save it for reuse or add it to this analysis first.');
      if(done.has(name))return;
      if(visiting.has(name))throw new Error('Saved metric dependency cycle at '+name+'.');
      if(!METRIC_KINDS.some(([kind])=>kind===metric.kind))
        throw new Error('Saved metric '+name+' has an unsupported type.');
      visiting.add(name);
      if(metric.kind==='arithmetic'){
        if(metric.expression_version!=='1'||!['add','subtract','multiply','divide','minimum','maximum','average','absolute_difference'].includes(metric.operation))
          throw new Error('Saved metric '+name+' uses unsupported arithmetic.');
        for(const operand of [metric.left,metric.right]){
          if(operand?.kind==='metric')visit(operand.name);
          else if(operand?.kind!=='constant'||!Number.isFinite(operand.value)||
            typeof operand.unit!=='string'||!/^[A-Za-z%$€£¥/._ -]{1,20}$/.test(operand.unit))
            throw new Error('Saved metric '+name+' has an invalid typed operand.');
        }
      }
      visiting.delete(name);done.add(name);
      added.push(JSON.parse(JSON.stringify(metric)));
      if(currentMetrics.length+added.length>4)
        throw new Error('Saved metric and dependencies exceed four case metrics; remove metrics first.');
    };
    visit(target.name);
    return added;
  }
  const METRIC_KINDS=[['time_between','Time from one activity to another'],['activity_count','How often an activity occurs'],['event_attribute_statistic','Numeric event values within each case or stage'],['event_attribute_sum','Sum of a numeric event attribute'],['case_attribute_number','A numeric case attribute'],['arithmetic','Arithmetic on earlier metrics']];
  function metricText(m){
    if(m.kind==='time_between')return m.name+': time from the '+m.from_occurrence+' '+m.from_activity+' to the '+m.to_occurrence+' '+m.to_activity;
    if(m.kind==='activity_count')return m.name+': number of '+m.activity+' events';
    if(m.kind==='event_attribute_statistic'){
      const label={average:'average',minimum:'minimum',maximum:'maximum',median:'median',p90:'90th percentile',total:'total',population_stddev:'population standard deviation'}[m.statistic]||m.statistic;
      return m.name+': '+label+' of event attribute '+m.attribute+(m.activity?' at '+m.activity:'')+
        (m.from_activity?' from '+m.from_activity+' to '+m.to_activity+' ('+(m.stage_occurrence||'first')+' completed stage)':'')+
        ' ('+m.unit+', '+m.missing_policy+' numeric values)';
    }
    if(m.kind==='arithmetic'){
      const text=o=>o.kind==='metric'?o.name:String(o.value)+' '+o.unit;
      const pair={minimum:'Smaller of',maximum:'Larger of',average:'Average of',absolute_difference:'Absolute difference between'}[m.operation];
      if(pair)return m.name+': '+pair+' '+text(m.left)+' and '+text(m.right);
      return m.name+': '+text(m.left)+' '+({add:'+',subtract:'−',multiply:'×',divide:'÷'}[m.operation])+' '+text(m.right);
    }
    return m.name+': '+(m.kind==='event_attribute_sum'?'sum of event attribute ':'case attribute ')+m.attribute+' ('+m.unit+')';
  }

  function analysisSection(activityNames){
    const metrics=[];let editingMetric=null;
    const box=section('Build an analysis');box.id='owgEventLogAnalysis';
    box.appendChild(el('div','Group cases or events by up to two dimensions and choose what to measure. The choices are fixed, so no query text is ever run directly.','muted'));
    const level=select('owgAnalysisLevel',[['cases','Cases'],['events','Events']]);
    const dim1=select('owgAnalysisDim1',[]),dim2=select('owgAnalysisDim2',[]);
    const attr1=input('owgAnalysisAttr1','text','attribute name'),attr2=input('owgAnalysisAttr2','text','attribute name');
    const measure1=select('owgAnalysisMeasure1',[]),measure2=select('owgAnalysisMeasure2',[]);
    const hours=input('owgAnalysisHours','number','any');hours.min='0';hours.step='any';
    const endsWith=input('owgAnalysisEnds','text','any activity');
    const run=button('Run analysis');run.id='owgAnalysisRun';
    const background=input('owgAnalysisBackground','checkbox');background.checked=true;
    const cancel=button('Cancel analysis','secondary');cancel.id='owgAnalysisCancel';cancel.disabled=true;
    let running=false,cancelRequested=false,job=null,attempt=0;
    const name=input('owgAnalysisName','text','Analysis name');name.maxLength=120;
    const save=button('Save analysis','secondary');save.id='owgAnalysisSave';save.disabled=true;
    const f={attr1:field('First attribute',attr1),attr2:field('Second attribute',attr2),hours:field('Only cases longer than (hours)',hours),ends:field('Only cases ending with',endsWith)};
    const sync=()=>{
      const keep=[dim1.value,dim2.value,measure1.value,measure2.value];
      setOptions(dim1,ANALYSIS_DIMENSIONS[level.value]);setOptions(dim2,ANALYSIS_DIMENSIONS[level.value]);
      const extra=level.value==='cases'?metrics.flatMap(m=>[['median','Median'],['avg','Average'],['sum','Total'],['max','Highest']].map(([fn,label])=>[fn+':metric:'+m.name,label+' '+m.name])):[];
      setOptions(measure1,[...ANALYSIS_MEASURES[level.value],...extra]);setOptions(measure2,[['','(none)'],...ANALYSIS_MEASURES[level.value],...extra]);
      [dim1,dim2,measure1,measure2].forEach((node,i)=>{if([...node.options].some(o=>o.value===keep[i]))node.value=keep[i];});
      show(f.attr1,dim1.value==='attribute');show(f.attr2,dim2.value==='attribute');show(f.hours,level.value==='cases');show(f.ends,level.value==='cases');
    };
    level.onchange=dim1.onchange=dim2.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Analyse',level),field('Group by',dim1),f.attr1,field('Then by',dim2),f.attr2,field('Measure',measure1),field('Also measure',measure2),f.hours,f.ends,run,cancel);
    // Case metrics: typed per-case values that the measures above can use.
    const names=(activityNames||[]).slice(0,500).map(a=>[a,a]);
    const mKind=select('owgMetricKind',METRIC_KINDS),mName=input('owgMetricName','text','wait_for_approval');mName.maxLength=40;
    const mFrom=select('owgMetricFrom',names),mTo=select('owgMetricTo',names),mActivity=select('owgMetricActivity',names);
    const occurrences=[['first','first'],['last','last']];
    const mFromOcc=select('owgMetricFromOccurrence',occurrences),mToOcc=select('owgMetricToOccurrence',occurrences);
    const mAttribute=input('owgMetricAttribute','text','amount'),mUnit=input('owgMetricUnit','text','EUR');mUnit.maxLength=20;
    const mStatistic=select('owgMetricStatistic',[['average','Average'],['minimum','Minimum'],['maximum','Maximum'],['median','Median'],['p90','90th percentile'],['total','Total'],['population_stddev','Population standard deviation']]);
    const mMissing=select('owgMetricMissingPolicy',[['available','Use available finite values'],['complete','Require every matching event to have a finite value']]);
    const mStatActivity=select('owgMetricStatActivity',[['','(all activities)'],...names]);
    const mStageFrom=select('owgMetricStageFrom',[['','(whole case)'],...names]);
    const mStageTo=select('owgMetricStageTo',[['','(whole case)'],...names]);
    const mStageOccurrence=select('owgMetricStageOccurrence',[['first','First observed completed stage'],['last_completed','Last completed stage (ignore unfinished trailing stage)']]);
    const mCompareActivityA=select('owgMetricCompareActivityA',[['','(choose activity A)'],...names]);
    const mCompareActivityB=select('owgMetricCompareActivityB',[['','(choose activity B)'],...names]);
    const mOperation=select('owgMetricOperation',[['add','Add'],['subtract','Subtract'],['multiply','Multiply'],['divide','Divide'],['minimum','Smaller of two values'],['maximum','Larger of two values'],['average','Average of two values'],['absolute_difference','Absolute difference (nonnegative)']]);
    const operands={};
    for(const side of ['left','right']){
      const title=side==='left'?'Left':'Right';
      operands[side]={kind:select('owgMetric'+title+'Kind',[['metric','Earlier metric'],['constant','Typed constant']]),
        metric:select('owgMetric'+title+'Metric',[]),value:input('owgMetric'+title+'Value','number','constant'),unit:input('owgMetric'+title+'Unit','text','ratio')};
      operands[side].value.step='any';operands[side].unit.maxLength=20;
    }
    const mAdd=button('Add metric to this analysis','secondary');mAdd.id='owgMetricAdd';
    const mUpdate=button('Update selected metric','secondary');mUpdate.id='owgMetricUpdate';mUpdate.disabled=true;
    const mSave=button('Save metric for reuse','secondary');mSave.id='owgMetricSave';
    const mCompare=button('Compare first and last completed stages (3 metrics)','secondary');mCompare.id='owgMetricCompareStages';
    const mCompareRelative=button('Compare fractional first-to-last change (4 metrics)','secondary');mCompareRelative.id='owgMetricCompareRelative';
    const mCompareActivities=button('Compare two activities (3 metrics)','secondary');mCompareActivities.id='owgMetricCompareActivities';
    const mCompareShare=button('Compare selected activity with whole-case total (3 metrics)','secondary');mCompareShare.id='owgMetricCompareActivityShare';
    const mf={from:field('From activity',mFrom),fromOcc:field('Occurrence of the from activity',mFromOcc),to:field('To activity',mTo),toOcc:field('Occurrence of the to activity',mToOcc),
      activity:field('Activity to count',mActivity),attribute:field('Attribute',mAttribute),unit:field('Unit of the values',mUnit),
      statistic:field('Statistic within each case',mStatistic),missing:field('Missing numeric values',mMissing),
      statActivity:field('Only this activity (optional)',mStatActivity),stageFrom:field('Stage start (optional)',mStageFrom),
      stageTo:field('Stage end (optional)',mStageTo),stageOccurrence:field('Which completed stage',mStageOccurrence),
      compareActivityA:field('Comparison activity A',mCompareActivityA),compareActivityB:field('Comparison activity B',mCompareActivityB),
      operation:field('Operation',mOperation)};
    for(const side of ['left','right'])for(const key of ['kind','metric','value','unit'])mf[side+key]=field((side==='left'?'Left':'Right')+' '+({kind:'operand',metric:'metric',value:'constant',unit:'constant unit'}[key]),operands[side][key]);
    const mSync=()=>{const k=mKind.value;show(mf.from,k==='time_between');show(mf.fromOcc,k==='time_between');show(mf.to,k==='time_between');show(mf.toOcc,k==='time_between');
      const numeric=k==='event_attribute_statistic';
      show(mf.activity,k==='activity_count');show(mf.attribute,numeric||k.endsWith('_number')||k.endsWith('_sum'));show(mf.unit,numeric||k.endsWith('_number')||k.endsWith('_sum'));
      show(mf.statistic,numeric);show(mf.missing,numeric);show(mf.statActivity,numeric);show(mCompare,numeric);show(mCompareRelative,numeric);show(mCompareActivities,numeric);show(mCompareShare,numeric);
      show(mf.compareActivityA,numeric);show(mf.compareActivityB,numeric);
      show(mf.stageFrom,numeric);show(mf.stageTo,numeric);show(mf.stageOccurrence,numeric&&(!!mStageFrom.value||!!mStageTo.value));
      show(mf.operation,k==='arithmetic');
      for(const side of ['left','right'])for(const key of ['kind','metric','value','unit'])show(mf[side+key],k==='arithmetic'&&(key==='kind'||(key==='metric'?operands[side].kind.value==='metric':operands[side].kind.value==='constant')));
    };
    operands.left.kind.onchange=operands.right.kind.onchange=mSync;
    mKind.onchange=mStageFrom.onchange=mStageTo.onchange=mSync;mSync();
    const metricRow=el('div',null,'row');metricRow.style.alignItems='end';
    metricRow.append(field('Case metric',mKind),field('Metric name',mName),...Object.values(mf),mAdd,mUpdate,mCompare,mCompareRelative,mCompareActivities,mCompareShare,mSave);
    const metricList=el('div');metricList.id='owgMetricList';
    const library=el('div');library.id='owgMetricLibrary';
    const metricBox=document.createElement('fieldset');metricBox.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin-top:8px';
    const legend=el('legend','Case metrics (optional)');metricBox.append(legend,el('div','Define up to four values per case, then choose a measure. Numeric event statistics use only observed finite values; choose available or require all matching rows, optionally one activity and a first or last completed stage. Compare first and last completed stages adds three metrics atomically; fractional first-to-last change adds four, including (last minus first) divided by first, and selects a dimensionless ratio (2 means +200%, not 2%). A zero first-stage value leaves relative change unknown. Compare two distinct activities adds A, B, and signed B minus A as three ordinary metrics; absent or invalid matching values remain unknown. Compare a selected activity with the whole case using Total and Only this activity: the selected total divided by all recorded totals is a ratio, not a guaranteed 0–100% share. Missing selected values or zero whole-case totals remain unknown. Clear stage boundaries and the single-activity filter before comparing. Unfinished stages are not completed. Times are in seconds; declared units are never converted. Arithmetic uses earlier metrics or typed constants; ratio means dimensionless. Add/subtract need identical units; division by zero is missing. Saved arithmetic templates can add their saved dependencies together, but conflicting or missing definitions are refused atomically. Editing a metric name updates its dependent arithmetic and selected measures in the draft; rerun before saving or exporting. Save an analysis to retain all definitions together.','muted'),metricRow,metricList,library);
    const readMetric=()=>caseMetric({kind:mKind.value,name:mName.value,from:mFrom.value,to:mTo.value,fromOccurrence:mFromOcc.value,toOccurrence:mToOcc.value,activity:mActivity.value,attribute:mAttribute.value,unit:mUnit.value,statistic:mStatistic.value,missingPolicy:mMissing.value,statActivity:mStatActivity.value,stageFrom:mStageFrom.value,stageTo:mStageTo.value,stageOccurrence:mStageOccurrence.value,operation:mOperation.value,
      ...Object.fromEntries(Object.entries(operands).flatMap(([side,controls])=>Object.entries(controls).map(([key,node])=>[side+key[0].toUpperCase()+key.slice(1),node.value])))});
    // A changed metric draft cannot reuse an earlier result or its export/share grants.
    // Invalidate synchronously, including when a dependent metric is removed.
    function invalidateMetricResult(){
      resetSharing();last=null;lastSpec=null;out.replaceChildren();exports.enable(false);
      save.disabled=true;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;
    }
    function useMetric(metric){
      if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
      if(metrics.some(m=>m.name===metric.name))throw new Error('This analysis already has a metric named '+metric.name+'.');
      if(metrics.length>=4)throw new Error('Use at most 4 case metrics.');
      if(metric.kind==='arithmetic')for(const o of [metric.left,metric.right])if(o.kind==='metric'&&!metrics.some(m=>m.name===o.name))throw new Error('Add earlier metric '+o.name+' first.');
      metrics.push(metric);renderMetrics();invalidateMetricResult();
    }
    function renderMetrics(){
      metricList.replaceChildren(...metrics.map((m,i)=>{const item=el('div',metricText(m)+' ');
        const edit=button('Edit','secondary');edit.setAttribute('aria-label','Edit metric '+m.name);
        edit.onclick=()=>{try{populateMetric(m);editingMetric=m.name;mUpdate.disabled=false;message.textContent='Editing '+m.name+'. Update the selected metric to apply changes; the saved analysis remains unchanged.';}catch(error){message.textContent=error.message;}};
        const rm=button('Remove','secondary');rm.setAttribute('aria-label','Remove metric '+m.name);rm.onclick=()=>{
        if(running){message.textContent='Wait for the current analysis to finish or cancel it before changing this draft.';return;}
        const dependent=metrics.find(x=>x.kind==='arithmetic'&&[x.left,x.right].some(o=>o.kind==='metric'&&o.name===m.name));
        if(dependent){message.textContent='Remove dependent metric '+dependent.name+' first.';return;}
        metrics.splice(i,1);if(editingMetric===m.name){editingMetric=null;mUpdate.disabled=true;}renderMetrics();invalidateMetricResult();};item.append(edit,rm);return item;}));
      for(const controls of Object.values(operands)){
        const keep=controls.metric.value;setOptions(controls.metric,metrics.length?metrics.map(m=>[m.name,m.name]):[['','(add an earlier metric)']]);
        if(metrics.some(m=>m.name===keep))controls.metric.value=keep;
      }
      sync();
    }
    function populateMetric(m){
      const choose=(control,value)=>{const v=String(value||'');if(v&&![...control.options].some(o=>o.value===v))throw new Error('This metric references an activity no longer listed in the log; it can still be run.');control.value=v;};
      mKind.value=m.kind;mName.value=m.name;
      if(m.kind==='time_between'){
        choose(mFrom,m.from_activity);choose(mTo,m.to_activity);
        mFromOcc.value=m.from_occurrence||'first';mToOcc.value=m.to_occurrence||'first';
      }else if(m.kind==='activity_count')choose(mActivity,m.activity);
      else if(m.kind==='event_attribute_statistic'||m.kind==='event_attribute_sum'||m.kind==='case_attribute_number'){
        mAttribute.value=m.attribute||'';mUnit.value=m.unit||'';
        if(m.kind==='event_attribute_statistic'){
          mStatistic.value=m.statistic;mMissing.value=m.missing_policy||'available';
          choose(mStatActivity,m.activity);choose(mStageFrom,m.from_activity);choose(mStageTo,m.to_activity);
          mStageOccurrence.value=m.stage_occurrence||'first';
        }
      }else if(m.kind==='arithmetic'){
        mOperation.value=m.operation;
        for(const side of ['left','right']){
          const operand=m[side],controls=operands[side];controls.kind.value=operand.kind;
          if(operand.kind==='metric')controls.metric.value=operand.name;
          else{controls.value.value=String(operand.value);controls.unit.value=operand.unit;}
        }
      }
      mSync();
    }
    const saveRow=el('div',null,'row');saveRow.style.alignItems='end';saveRow.append(field('Name',name),save);
    const message=el('div',null,'muted');message.id='owgAnalysisStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgAnalysisResults';
    const savedList=el('div');savedList.id='owgAnalysisSaved';
    let last=null,lastSpec=null;
    let sharingAttempt=0,sharingSnapshot=null,casePacket=null,caseDownloadReady=false,bundlePacket=null;
    const prepareShare=button('Prepare sharing snapshot','secondary');prepareShare.id='owgAnalysisPrepareShare';prepareShare.disabled=true;
    const prepareCases=button('Prepare case sharing files','secondary');prepareCases.id='owgAnalysisPrepareCases';prepareCases.disabled=true;
    const prepareCompany=button('Prepare paired company handoff','secondary');prepareCompany.id='owgAnalysisPrepareCompany';prepareCompany.disabled=true;
    const sharing=el('div');sharing.id='owgAnalysisSharingPreview';sharing.hidden=true;
    const sharingTable=el('div');sharingTable.style.overflowX='auto';
    const sharingInfo=el('p',null,'muted');sharingInfo.style.overflowWrap='anywhere';
    const sharingConsent=input('owgAnalysisSharingConsent','checkbox');
    const sharingDownload=button('Download reviewed snapshot','secondary');sharingDownload.id='owgAnalysisSharingDownload';sharingDownload.disabled=true;
    const freshness=button('Check snapshot freshness','secondary');freshness.id='owgAnalysisFreshness';freshness.disabled=true;
    const freshnessStatus=el('p','Freshness not checked.','muted');freshnessStatus.id='owgAnalysisFreshnessStatus';freshnessStatus.setAttribute('role','status');
    sharing.append(el('p','This snapshot recomputes the analysis, replaces group labels with codes and omits raw events, case IDs, filter values and metric definitions. Declared units and numeric values may still be sensitive. Review every cell before sharing. Upload the downloaded file to your organization’s Shared analyses portal; downloading sends nothing to a Gateway.'),sharingTable,sharingInfo,
      freshness,freshnessStatus,field('I reviewed this snapshot and intend to share it with my organization',sharingConsent),sharingDownload);
    const casePane=el('div');casePane.hidden=true;const caseTable=el('div');caseTable.style.overflowX='auto';
    const caseConsent=input('owgAnalysisCaseConsent','checkbox');const caseDownload=button('Download reviewed case dataset','secondary');caseDownload.id='owgAnalysisCaseDownload';caseDownload.disabled=true;
    const bundleDownload=button('Download paired company handoff','secondary');bundleDownload.id='owgAnalysisBundleDownload';bundleDownload.disabled=true;bundleDownload.hidden=true;
    casePane.append(el('p','This separate case file contains complete coded cases and within-case elapsed microseconds. Original case/activity/person labels, resources, attributes and absolute dates are omitted. Codes, topology and timings are still sensitive and are not anonymization. Codes apply only to this file; no group-row mapping or cross-case time order is disclosed. Review every timeline below. A separate case-read scope and explicit revision grant are required at the Gateway.'),caseTable,field('I reviewed the complete coded case timelines and intend this case-level disclosure',caseConsent),caseDownload,bundleDownload,
      el('p','A paired handoff contains both the minimized aggregate and these coded case timelines. Downloading sends nothing to the company portal. Publishing the aggregate and attaching case data still require distinct confirmations there.','muted'));sharing.append(casePane);
    function resetSharing(){caseDownloadReady=false;casePacket=null;bundlePacket=null;bundleDownload.hidden=true;bundleDownload.disabled=true;casePane.hidden=true;caseTable.replaceChildren();caseConsent.checked=false;caseDownload.disabled=true;sharingAttempt++;sharingSnapshot=null;sharing.hidden=true;sharingConsent.checked=false;sharingDownload.disabled=true;freshness.disabled=true;freshnessStatus.textContent='Freshness not checked.';freshnessStatus.dataset.stale='true';}
    // A consent withdrawal invalidates an in-flight authorization even if the box is rechecked before HTTP completes.
    let sharingConsentRevision=0;
    function sharingReady(){
      sharingDownload.disabled=!sharingConsent.checked||!sharingSnapshot||freshnessStatus.dataset.stale==='true';
      caseDownload.disabled=!caseConsent.checked||!casePacket||!caseDownloadReady;
      bundleDownload.disabled=!bundlePacket||!sharingConsent.checked||!caseConsent.checked||freshnessStatus.dataset.stale==='true';
    }
    sharingConsent.onchange=()=>{sharingConsentRevision++;sharingReady();};
    async function prepareSharing(withCases=false,company=false){
      if(!lastSpec)return;
      resetSharing();const attempt=sharingAttempt;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;
      try{
        const result=await(await api(base()+(company?'/company-handoff-preview':withCases?'/case-sharing-preview':'/analysis-sharing-preview'),{
          method:'POST',headers:{'Content-Type':'application/json',
            ...(company?{'X-OWG-Company-Handoff-Preview':'true'}:withCases?{'X-OWG-Case-Sharing-Preview':'true'}:{'X-OWG-Analysis-Sharing-Preview':'true'})},
          body:JSON.stringify(lastSpec)})).json();
        const r=withCases?result.aggregate_snapshot:result;
        if(generation!==state.generation||attempt!==sharingAttempt)return;
        sharingSnapshot=r;sharing.hidden=false;freshness.disabled=withCases;freshnessStatus.dataset.stale=String(!withCases);freshnessStatus.textContent=withCases?'Paired preview was recomputed; review both files before download.':'Freshness not checked. Check current source, metric definitions and numeric results before downloading.';
        if(company){bundlePacket=result;bundleDownload.hidden=false;}
        if(withCases){casePacket=result.case_dataset;caseDownloadReady=true;casePane.hidden=false;caseTable.replaceChildren(...casePacket.cases.map(c=>{const details=el('details'),summary=el('summary',c.code+' · '+c.events.length+' events'),list=el('ol');for(const e of c.events)list.append(el('li',e.activity+' · '+e.elapsed_microseconds/1000000+' seconds since case start'));details.append(summary,list);return details;}));}
        sharingTable.replaceChildren(table(r.columns.map(c=>c.heading+(c.aggregation?' · '+c.aggregation:'')+(c.value_basis?' · '+c.value_basis:'')+(c.metric_slot?' '+c.metric_slot:'')+(c.unit?' ('+c.unit+')':'')),r.rows),
          ...r.metric_coverage.map(c=>el('p','Metric '+c.slot+': '+c.with_value+' cases with a value; '+c.without_value+' without; '+c.missing_operand+' missing operand; '+c.zero_divisor+' zero divisor; '+c.non_finite+' nonfinite.')));
        sharingInfo.textContent=r.matching_cases+' selected cases; '+r.matching_rows+' selected '+r.level+' rows. '+(r.rows_truncated?'Only the bounded result rows are included. ':'')+r.non_finite_results_withheld+' output results withheld as nonfinite. Generated '+r.generated_at+'. Source fingerprint: '+r.source_content_sha256+'. Analysis fingerprint: '+r.analysis_spec_sha256+'. Group codes apply only within this snapshot; source freshness after export is unknown.';
      }catch(error){if(generation===state.generation&&attempt===sharingAttempt)message.textContent=error.message;}
      finally{if(generation===state.generation&&attempt===sharingAttempt){prepareShare.disabled=!lastSpec;prepareCases.disabled=!lastSpec;prepareCompany.disabled=!lastSpec;}}
    };
    freshness.onclick=async()=>{
      if(!sharingSnapshot||!lastSpec||generation!==state.generation)return;
      const attempt=sharingAttempt,snapshot=sharingSnapshot,spec=lastSpec;
      freshness.disabled=true;freshnessStatus.dataset.stale='true';sharingDownload.disabled=true;freshnessStatus.textContent='Checking current source and metric lineage…';
      try{
        const response=await api(base()+'/analysis-sharing-freshness',{method:'POST',headers:{'Content-Type':'application/json','X-OWG-Analysis-Sharing-Preview':'true'},body:JSON.stringify({spec,snapshot})});
        if(!response.ok)throw new Error('Freshness check failed ('+response.status+').');
        const result=await response.json();
        if(generation!==state.generation||attempt!==sharingAttempt||snapshot!==sharingSnapshot)return;
        if(result.format!=='openworkgraph.analysis-snapshot-freshness.v1'||typeof result.lineage_current!=='boolean'||typeof result.current_results_match!=='boolean')throw new Error('Unexpected freshness response.');
        freshnessStatus.textContent=!result.lineage_current?'Snapshot lineage is stale. Prepare and review a new snapshot before sharing.':!result.current_results_match?'Snapshot values differ from the current recomputation. Prepare and review a new snapshot before sharing.':'Current source, specification, metric lineage and minimized values match. Original publication and source authenticity are not verified.';
        freshnessStatus.dataset.stale=String(!result.lineage_current||!result.current_results_match);sharingDownload.disabled=!result.lineage_current||!result.current_results_match||!sharingConsent.checked;
      }catch(error){if(generation===state.generation&&attempt===sharingAttempt){freshnessStatus.textContent=error.message+' Freshness unknown; prepare a new snapshot.';freshnessStatus.dataset.stale='true';sharingDownload.disabled=true;}}
      finally{if(generation===state.generation&&attempt===sharingAttempt)freshness.disabled=false;}
    };
    prepareShare.onclick=()=>prepareSharing();prepareCases.onclick=()=>prepareSharing(true);prepareCompany.onclick=()=>prepareSharing(true,true);
    caseConsent.onchange=sharingReady;
    caseDownload.onclick=async()=>{
      if(!caseConsent.checked||!casePacket||!sharingSnapshot||!lastSpec||!caseDownloadReady||generation!==state.generation||caseDownload.disabled)return;
      // The preview is not a download authorization. Recompute both files under
      // the server's case-sharing lock and reject any changed case sequence,
      // elapsed timing, source/spec fingerprint or aggregate numeric result.
      const attempt=sharingAttempt,selectedGeneration=state.generation;
      const packet=casePacket,snapshot=sharingSnapshot,spec=lastSpec;
      // Single-use reviewed preview: a failed, cancelled or revoked attempt
      // cannot be rearmed by toggling the consent checkbox.
      caseDownloadReady=false;
      caseDownload.disabled=true;
      try{
        const response=await api(base()+'/case-sharing-preview',{method:'POST',
          headers:{'Content-Type':'application/json','X-OWG-Case-Sharing-Preview':'true'},
          body:JSON.stringify(spec)});
        if(!response.ok)throw new Error('Case revalidation failed ('+response.status+').');
        const current=await response.json();
        if(attempt!==sharingAttempt||selectedGeneration!==state.generation||
           packet!==casePacket||snapshot!==sharingSnapshot||!caseConsent.checked)return;
        const freshPacket=current.case_dataset, freshAggregate=current.aggregate_snapshot;
        // generated_at is intentionally excluded: a fresh recomputation has a
        // different clock time even when the complete minimized data is equal.
        const stablePacket=p=>JSON.stringify([p?.source_content_sha256,p?.analysis_spec_sha256,p?.cases]);
        const stableAggregate=p=>JSON.stringify([p?.source_content_sha256,p?.analysis_spec_sha256,
          p?.columns,p?.rows,p?.metric_coverage,p?.matching_cases,p?.matching_rows,
          p?.rows_truncated,p?.non_finite_results_withheld]);
        if(!freshPacket||!freshAggregate||stablePacket(packet)!==stablePacket(freshPacket)||
           stableAggregate(snapshot)!==stableAggregate(freshAggregate)){
          throw new Error('Case dataset or aggregate changed; prepare and review a new paired preview.');
        }
        download('owg-analysis-case-dataset.json',JSON.stringify(packet),'application/json');
        caseConsent.checked=false;
        freshnessStatus.textContent='Case download revalidated against the current saved log. Later changes cannot be ruled out.';
      }catch(error){
        if(attempt===sharingAttempt&&selectedGeneration===state.generation)
          freshnessStatus.textContent=error.message+' Case download refused.';
      }finally{
        // A fresh preview and explicit review are required before another download.
        if(attempt===sharingAttempt&&selectedGeneration===state.generation)caseDownload.disabled=true;
      }
    };
    bundleDownload.onclick=async()=>{
      if(!bundlePacket||!caseConsent.checked||!sharingConsent.checked||!lastSpec||
         freshnessStatus.dataset.stale==='true'||generation!==state.generation||bundleDownload.disabled)return;
      const attempt=sharingAttempt,snapshot=sharingSnapshot,cases=casePacket,spec=lastSpec,reviewedBundle=bundlePacket,consentRevision=sharingConsentRevision;
      if(!snapshot||!cases||reviewedBundle.aggregate_snapshot!==snapshot||reviewedBundle.case_dataset!==cases||
         snapshot.source_content_sha256!==cases.source_content_sha256||
         snapshot.analysis_spec_sha256!==cases.analysis_spec_sha256||
         snapshot.matching_cases!==cases.cases.length)return;
      bundleDownload.disabled=true;freshnessStatus.dataset.stale='true';
      freshnessStatus.textContent='Rechecking source, metric lineage and paired snapshot before download…';
      try{
        const result=await(await api(base()+'/analysis-sharing-freshness',{method:'POST',
          headers:{'Content-Type':'application/json','X-OWG-Analysis-Sharing-Preview':'true'},
          body:JSON.stringify({spec,snapshot})})).json();
        if(attempt!==sharingAttempt||generation!==state.generation||snapshot!==sharingSnapshot||
           cases!==casePacket||reviewedBundle!==bundlePacket||!caseConsent.checked||!sharingConsent.checked||
           consentRevision!==sharingConsentRevision)return;
        if(result.format!=='openworkgraph.analysis-snapshot-freshness.v1'||
           result.lineage_current!==true||result.current_results_match!==true){
          freshnessStatus.textContent='Paired handoff stale or changed. Prepare and review a new handoff.';
          return;
        }
        download('owg-company-analysis-handoff.json',JSON.stringify(reviewedBundle),'application/json');
        caseConsent.checked=false;sharingConsent.checked=false;
        freshnessStatus.textContent='Current source and aggregate verified at download. Changes after the check remain possible.';
      }catch(error){
        if(attempt===sharingAttempt&&generation===state.generation)
          freshnessStatus.textContent=error.message+' No handoff downloaded.';
      }finally{
        if(attempt===sharingAttempt&&generation===state.generation)
          sharingReady();
      }
    };
    sharingDownload.onclick=async()=>{
      if(!sharingConsent.checked||!sharingSnapshot||!lastSpec||freshnessStatus.dataset.stale==='true'||generation!==state.generation||sharingDownload.disabled)return;
      const attempt=sharingAttempt,snapshot=sharingSnapshot,spec=lastSpec,selectedGeneration=state.generation,consentRevision=sharingConsentRevision;
      // A reviewed preview is single-use, even if the HTTP request fails or consent changes.
      sharingDownload.disabled=true;freshness.disabled=true;freshnessStatus.dataset.stale='true';
      freshnessStatus.textContent='Authorizing the reviewed export against the current saved log…';
      try{
        const reviewed={spec,source_content_sha256:snapshot.source_content_sha256,
          analysis_spec_sha256:snapshot.analysis_spec_sha256,
          metric_definition_sha256:snapshot.metric_definition_sha256};
        const headers={'Content-Type':'application/json','X-OWG-Analysis-Sharing-Preview':'true'};
        const authorization=await api(base()+'/analysis-sharing-authorize',{method:'POST',
          headers,body:JSON.stringify(reviewed)});
        if(!authorization.ok)throw new Error('Analysis authorization refused ('+authorization.status+').');
        const grant=await authorization.json();
        if(!grant.ticket||attempt!==sharingAttempt||selectedGeneration!==state.generation||
           snapshot!==sharingSnapshot||!sharingConsent.checked||consentRevision!==sharingConsentRevision)return;
        const response=await api(base()+'/analysis-sharing-download',{method:'POST',
          headers,body:JSON.stringify({...reviewed,ticket:grant.ticket})});
        if(!response.ok)throw new Error('Source-bound download refused ('+response.status+').');
        const current=await response.json();
        if(attempt!==sharingAttempt||selectedGeneration!==state.generation||snapshot!==sharingSnapshot||!sharingConsent.checked||consentRevision!==sharingConsentRevision)return;
        // The server recomputes under the workspace lock; do not release changed
        // minimized values even if their lineage is unexpectedly identical.
        if(current.source_content_sha256!==snapshot.source_content_sha256||
           current.analysis_spec_sha256!==snapshot.analysis_spec_sha256||
           JSON.stringify(current.metric_definition_sha256)!==JSON.stringify(snapshot.metric_definition_sha256)||
           JSON.stringify(current.columns)!==JSON.stringify(snapshot.columns)||
           JSON.stringify(current.rows)!==JSON.stringify(snapshot.rows)){
          throw new Error('Reviewed snapshot differs from the current recomputation.');
        }
        download('owg-analysis-snapshot.json',JSON.stringify(current,null,2),'application/json');
        sharingConsent.checked=false;
        freshnessStatus.textContent='Downloaded the server-recomputed, source-bound snapshot. Prepare and review again for another export.';
      }catch(error){
        if(attempt===sharingAttempt&&selectedGeneration===state.generation)
          freshnessStatus.textContent=error.message+' Prepare and review a new snapshot.';
      }finally{
        if(attempt===sharingAttempt&&selectedGeneration===state.generation){
          sharingDownload.disabled=true;freshness.disabled=true;
        }
      }
    };
    // Ordinary JSON/CSV exports are source-bound too: recompute under the saved-log
    // API lock immediately before download. Never release a cached result if its
    // data or metric coverage changed since the user reviewed it.
    let exportAttempt=0;
    async function verifiedAnalysisExport(){
      if(running||!last||!lastSpec||generation!==state.generation)throw new Error('Run and review the analysis before exporting.');
      const selected=last,spec=lastSpec,selectedGeneration=state.generation,request=++exportAttempt;
      const response=await api(base()+'/analysis-source-bound',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});
      const current=await response.json();
      if(request!==exportAttempt||selectedGeneration!==state.generation||selected!==last||spec!==lastSpec||running)
        throw new Error('Analysis changed during export. Run and review it again.');
      const stable=r=>JSON.stringify([r.columns,r.rows,r.units,r.matching,r.rows_truncated,r.metrics,r.non_finite_results_withheld]);
      if(!selected.source_content_sha256||!current.source_content_sha256||current.source_content_sha256!==selected.source_content_sha256||stable(current)!==stable(selected)){
        invalidateMetricResult();
        throw new Error('Saved-log data or analysis results changed. Run and review the analysis again before downloading.');
      }
      return current;
    }
    const exports=exportButtons('owgAnalysis',()=>last,r=>[r.columns,...r.rows],verifiedAnalysisExport,error=>{message.textContent=error.message;});
    box.append(field('Run as a cancellable local job',background),
      el('p','Local jobs have a two-minute deadline including queue time. They use the log when execution starts; results are static. Results expire after ten minutes or restart. Leaving this view stops polling; work may continue until its deadline. Nothing is uploaded.','muted'),
      row,metricBox,message,out,exports.node,prepareShare,prepareCases,prepareCompany,sharing,saveRow,savedList);
    mAdd.onclick=()=>{try{useMetric(readMetric());message.textContent='Metric added; choose it under Measure.';}catch(error){message.textContent=error.message;}};
    mUpdate.onclick=()=>{
      try{
        if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
        const index=metrics.findIndex(m=>m.name===editingMetric);
        if(index<0)throw new Error('Select a metric to edit first.');
        const next=readMetric(),old=editingMetric;
        // Resolve every dependent and selected measure before mutating the UI.
        // A refused refactor leaves the complete draft and its exports intact.
        const draft=refactorMetricGraph(metrics,old,next,[measure1.value,measure2.value]);
        metrics.splice(0,metrics.length,...draft.metrics);
        editingMetric=null;mUpdate.disabled=true;renderMetrics();
        measure1.value=draft.measures[0];measure2.value=draft.measures[1];
        // A previous result and its export/share grants belong to the old definition.
        resetSharing();last=null;lastSpec=null;out.replaceChildren();exports.enable(false);
        save.disabled=true;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;
        message.textContent='Metric updated in this draft; dependent arithmetic and selected measures follow any rename. Run analysis to recalculate before saving or exporting.';
      }catch(error){message.textContent=error.message;}
    };
    function addStageComparison(relative){
      try{
        if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
        const proposed=stageComparisonMetrics(readMetric(),relative);
        if(metrics.length+proposed.length>4)throw new Error('A stage comparison needs '+(relative?'four':'three')+' free metric slots (four maximum). Remove other metrics first.');
        const existing=new Set(metrics.map(m=>m.name));
        if(proposed.some(m=>existing.has(m.name)))throw new Error('A comparison metric name already exists. Choose a different base name.');
        // Validate the entire batch before mutation; no half-added comparisons.
        metrics.push(...proposed);renderMetrics();invalidateMetricResult();
        // Case metrics cannot be measured at the event level.
        level.value='cases';sync();
        measure1.value='avg:metric:'+proposed[proposed.length-1].name;measure2.value='';
        message.textContent=relative
          ?'Added first, last completed, signed change, and fractional change metrics. Ratio 2 means +200%; zero first-stage values and missing stages remain unknown.'
          :'Added first, last completed, and signed last minus first metrics. Run analysis; missing stages remain unknown.';
      }catch(error){message.textContent=error.message;}
    }
    mCompare.onclick=()=>addStageComparison(false);
    mCompareRelative.onclick=()=>addStageComparison(true);
    mCompareActivities.onclick=()=>{
      try{
        if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
        const proposed=activityComparisonMetrics(readMetric(),mCompareActivityA.value,mCompareActivityB.value);
        if(metrics.length+proposed.length>4)
          throw new Error('An activity comparison needs three free metric slots (four maximum). Remove other metrics first.');
        const existing=new Set(metrics.map(m=>m.name));
        if(proposed.some(m=>existing.has(m.name)))
          throw new Error('An activity comparison metric name already exists. Choose a different base name.');
        // Validate the entire batch before mutation, including dependency order.
        metrics.push(...proposed);renderMetrics();invalidateMetricResult();
        level.value='cases';sync();
        measure1.value='avg:metric:'+proposed[2].name;measure2.value='';
        message.textContent='Added activity A, activity B, and signed B minus A metrics. Missing activity values remain unknown, never zero.';
      }catch(error){message.textContent=error.message;}
    };
    mCompareShare.onclick=()=>{
      try{
        if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
        const proposed=activityShareMetrics(readMetric());
        if(metrics.length+proposed.length>4)
          throw new Error('An activity fraction needs three free metric slots (four maximum). Remove other metrics first.');
        const existing=new Set(metrics.map(m=>m.name));
        if(proposed.some(m=>existing.has(m.name)))
          throw new Error('An activity fraction metric name already exists. Choose a different base name.');
        // The numerator, denominator and typed division are one atomic addition.
        metrics.push(...proposed);renderMetrics();invalidateMetricResult();
        level.value='cases';sync();
        measure1.value='avg:metric:'+proposed[2].name;measure2.value='';
        message.textContent='Added selected activity total, whole-case total and their fraction. Missing values and zero denominators remain unknown; signed totals may produce ratios outside 0–1.';
      }catch(error){message.textContent=error.message;}
    };
    mSave.onclick=async()=>{try{await api(base()+'/metrics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({metric:readMetric()})});message.textContent='Metric saved for reuse.';await refreshLibrary();}catch(error){message.textContent=error.message;}};
    async function refreshLibrary(){
      const r=await getJSON(base()+'/metrics');if(generation!==state.generation)return;
      library.replaceChildren(r.metrics.length?table(['Saved metric','',''],r.metrics.map(entry=>{
        const m=entry.metric;const add=button('Add','secondary');add.setAttribute('aria-label','Add saved metric '+m.name);
        add.onclick=()=>{try{
          if(running)throw new Error('Wait for the current analysis to finish or cancel it before changing this draft.');
          const additions=savedMetricBundle(m,r.metrics,metrics);
          metrics.push(...additions);renderMetrics();
          resetSharing();last=null;lastSpec=null;out.replaceChildren();exports.enable(false);
          save.disabled=true;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;
          message.textContent='Added '+additions.length+' saved metric'+(additions.length===1?'':'s')+
            ' in dependency order. Choose a measure and rerun before saving or exporting.';
        }catch(error){message.textContent=error.message;}};
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete saved metric '+m.name);
        del.onclick=async()=>{try{await api(base()+'/metrics/'+encodeURIComponent(entry.metric_id)+'/delete',{method:'POST'});message.textContent='Saved metric deleted.';await refreshLibrary();}catch(error){message.textContent=error.message;}};
        return [metricText(m),add,del];})):el('div','No saved metrics yet.','muted'));
    }
    const generation=state.generation;
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId);
    const cell=(column,value,unit)=>value===null||value===undefined?'—':unit==='seconds'?duration(value):unit==='share'?pct(value):typeof value==='number'?Number(value).toLocaleString()+(unit&&!['cases','events','activities'].includes(unit)?' '+unit:''):String(value);
    cancel.onclick=async()=>{
      const cancellationAttempt=attempt;
      cancelRequested=true;cancel.disabled=true;message.textContent='Cancellation requested; no result will be displayed.';
      if(job)try{await api(job+'/cancel',{method:'POST',signal:AbortSignal.timeout(10000)});}catch(_){
        if(generation===state.generation&&box.isConnected&&cancellationAttempt===attempt)message.textContent='Cancellation could not be confirmed. No result will be displayed; the local deadline still applies.';
      }
    };
    async function execute(spec){
      if(running)throw new Error('Wait for this analysis or cancel it first.');
      const ownAttempt=++attempt,jobBase=base()+'/analysis-jobs';
      const current=()=>generation===state.generation&&box.isConnected&&ownAttempt===attempt;
      running=true;cancelRequested=false;job=null;run.disabled=true;background.disabled=true;cancel.disabled=!background.checked;
      resetSharing();last=null;lastSpec=null;out.replaceChildren();exports.enable(false);save.disabled=true;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;
      if(state.caseType)spec.case_type=state.caseType;
      let r;
      try{
        if(background.checked){
          message.textContent='Submitting local analysis…';
          const submitted=await (await api(jobBase,{method:'POST',signal:AbortSignal.timeout(10000),headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          job=jobBase+'/'+encodeURIComponent(submitted.job_id);
          let status=submitted;
          while(current()&&!cancelRequested&&['queued','running'].includes(status.state)){
            message.textContent=status.state==='queued'?'Analysis queued locally…':'Analysis running locally…';
            await new Promise(resolve=>setTimeout(resolve,250));
            if(!current()||cancelRequested)break;
            status=await(await api(job,{signal:AbortSignal.timeout(10000)})).json();
          }
          if(!current()||cancelRequested)return;
          if(status.state!=='succeeded')throw new Error(status.state==='timed_out'?'Analysis reached its deadline; no partial result was returned.':status.state==='cancelled'?'Analysis cancelled; no result was returned.':status.error||'Analysis failed; no partial result was returned.');
          // The job result has no atomic source fingerprint. Recompute under the
          // same lock as the digest before showing a result that can be exported.
          // A changed source between job completion and this read is reflected
          // in the newly reviewed result, never silently attributed to the job.
          r=await(await api(base()+'/analysis-source-bound',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(cancelRequested||!current())return;
        }else{
          message.textContent='Analysis running…';
          r=await (await api(base()+'/analysis-source-bound',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
          if(!current())return;
        }
      }catch(error){if(current())message.textContent=error.message;return;}
      finally{
        const cleanup=job;job=null;running=false;
        // Deletion cancels unfinished work and erases retained bytes. Failure is bounded by server expiry.
        if(cleanup)api(cleanup+'/delete',{method:'POST',signal:AbortSignal.timeout(10000)}).catch(()=>{});
        if(current()){run.disabled=false;background.disabled=false;cancel.disabled=true;
          if(cancelRequested)message.textContent='Analysis cancelled in this view; no result was displayed. Server cleanup requested; its deadline still applies.';}
      }
      if(!current())return;
      message.textContent='Analysis complete. Static result; rerun after changing the log.';
      resetSharing();last=r;lastSpec=spec;exports.enable(true);save.disabled=false;prepareShare.disabled=false;prepareCases.disabled=false;prepareCompany.disabled=false;
      const units=r.units||{};
      out.replaceChildren(el('div',Number(r.matching.cases).toLocaleString()+' matching cases'+(r.rows_truncated?'; showing the first '+r.rows.length+' rows.':'.'),'muted'),
        table(r.columns,r.rows.map(row=>row.map((v,i)=>cell(r.columns[i],v,units[r.columns[i]])))),
        ...(r.metrics||[]).map(c=>el('div',c.name+': '+Number(c.cases_with_value).toLocaleString()+' cases have a value, '+Number(c.cases_without_value).toLocaleString()+' do not'
          +(c.cases_without_from_activity!==undefined?' ('+c.cases_without_from_activity+' without '+c.definition.from_activity+', '+c.cases_without_to_activity+' without '+c.definition.to_activity+', '+c.cases_where_to_precedes_from+' where '+c.definition.to_activity+' comes first)':'')
          +(c.definition.kind==='arithmetic'?' ('+c.cases_with_missing_operand+' with a missing operand, '+c.cases_with_zero_divisor+' with a zero divisor; unit '+c.unit+')':'')
          +(c.cases_with_non_finite_value?' ('+c.cases_with_non_finite_value+' with a value that is not a finite number, such as NaN or an overflowing sum)':'')+'; cases without a value are left out of its measures.','muted')),
        ...(r.non_finite_results_withheld?[el('div',r.non_finite_results_withheld+' results overflowed and are shown as —.','muted')]:[]));
    }
    async function refresh(){
      const r=await getJSON(base()+'/analyses');if(generation!==state.generation)return;
      savedList.replaceChildren(r.analyses.length?table(['Saved analysis','','',''],r.analyses.map(a=>{
        const open=button('Run','secondary');open.setAttribute('aria-label','Run saved analysis '+a.name);
        open.onclick=()=>execute({...a.spec}).catch(error=>{message.textContent=error.message;});
        const load=button('Load into builder','secondary');load.setAttribute('aria-label','Load saved analysis '+a.name+' into builder');
        load.onclick=()=>{
          try{
            if(running)throw new Error('Wait for the current analysis to finish or cancel it before loading a draft.');
            const draft=analysisDraft(a.spec,state.caseType);
            // All checks have passed; replace the draft in one synchronous operation.
            level.value=draft.level;
            metrics.splice(0,metrics.length,...draft.metrics);
            editingMetric=null;mUpdate.disabled=true;renderMetrics();
            dim1.value=draft.dim1;dim2.value=draft.dim2;
            attr1.value=draft.attr1;attr2.value=draft.attr2;
            hours.value=draft.hours;endsWith.value=draft.endsWith;
            sync();measure1.value=draft.measure1;measure2.value=draft.measure2;
            resetSharing();last=null;lastSpec=null;out.replaceChildren();exports.enable(false);
            save.disabled=true;prepareShare.disabled=true;prepareCases.disabled=true;prepareCompany.disabled=true;name.value='';
            message.textContent='Loaded '+a.name+' into the builder. Edit and run it, then save under a new name; the original is unchanged.';
          }catch(error){message.textContent=error.message;}
        };
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete saved analysis '+a.name);
        del.onclick=async()=>{try{await api(base()+'/analyses/'+encodeURIComponent(a.analysis_id)+'/delete',{method:'POST'});message.textContent='Analysis deleted.';await refresh();}catch(error){message.textContent=error.message;}};
        return [a.name,open,load,del];})):el('div','No saved analyses yet.','muted'));
    }
    run.onclick=async()=>{
      try{message.textContent='';await execute(analysisSpec({level:level.value,dim1:dim1.value,attr1:attr1.value,dim2:dim2.value,attr2:attr2.value,measure1:measure1.value,measure2:measure2.value,hours:hours.value,endsWith:endsWith.value.trim(),metrics:[...metrics]}));}
      catch(error){message.textContent=error.message;}
    };
    save.onclick=async()=>{
      try{
        if(!lastSpec)throw new Error('Run the analysis first.');
        await api(base()+'/analyses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.value.trim(),spec:lastSpec})});
        message.textContent='Analysis saved.';name.value='';await refresh();
      }catch(error){message.textContent=error.message;}
    };
    refresh().catch(error=>{message.textContent=error.message;});
    refreshLibrary().catch(error=>{message.textContent=error.message;});
    return box;
  }

  const COMPLIANCE_TEMPLATES=[['response','Every A is eventually followed by B'],['chain_response','Every A is immediately followed by B'],['precedence','Every B needs an earlier A'],['response_within','Every A is followed by B within N hours'],['segregation_of_duties','A and B are done by different people'],['not_coexistence','A and B never happen in the same case'],['existence','A happens a set number of times']];
  // Builds one compliance rule from form values; throws a readable error.
  function complianceRule(values){
    const rule={template:values.template,a:values.a};
    if(!rule.a)throw new Error('Choose activity A.');
    if(values.template==='existence'){
      const min=values.min===''?0:Number(values.min),max=values.max===''?null:Number(values.max);
      if(!Number.isInteger(min)||min<0||(max!==null&&(!Number.isInteger(max)||max<min)))throw new Error('Enter whole numbers with the maximum at least the minimum.');
      if(min===0&&max===null)throw new Error('Set a minimum above 0 or a maximum.');
      rule.min=min;if(max!==null)rule.max=max;return rule;
    }
    rule.b=values.b;if(!rule.b||rule.b===rule.a)throw new Error('Choose a different activity B.');
    if(values.template==='response_within'){const h=Number(values.hours);if(!(h>0))throw new Error('Enter the time limit in hours.');rule.seconds=Math.round(h*3600);}
    return rule;
  }

  function clusterSection(inspection){
    const box=section('Trace clusters');box.id='owgEventLogClusters';
    box.appendChild(el('div','Group cases whose activities and orderings are similar, so a tangled map splits into a few ways of working. Clusters describe this log; they are not process types or causes.','muted'));
    const k=select('owgClusterK',[['','Choose automatically'],...[2,3,4,5,6,7,8].map(n=>[String(n),n+' clusters'])]);
    const run=button('Find clusters');run.id='owgClusterRun';
    const message=el('div',null,'muted');message.id='owgClusterStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgClusterResults';
    const review=el('div');review.id='owgClusterCaseReview';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Number of clusters',k),run);
    box.append(row,message,out,review);
    const generation=state.generation,endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/trace-clusters';
    const number=n=>Number(n).toLocaleString();
    let reviewTurn=0;
    const request=async(suffix,spec)=>(await api(endpoint+suffix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    function inspect(settings,cluster){
      const turn=++reviewTurn,current=()=>generation===state.generation&&turn===reviewTurn;
      const title=el('h4','Cases in cluster '+cluster.cluster);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous case page','secondary'),next=button('Next case page','secondary');
      nav.append(previous,next);review.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      async function load(page){
        previous.disabled=next.disabled=true;status.textContent='Loading cases…';
        try{
          const spec={...settings,cluster_key:cluster.cluster_key,...(cursors[page]!==null?{after:cursors[page]}:{})};
          const r=await request('/cases',spec);
          if(!current())return;
          index=page;following=r.next_after;
          status.textContent=number(r.total)+(r.total===1?' case.':' cases.')+' Showing '+number(r.cases.length)+' on this page.';
          rows.replaceChildren(r.cases.length?table(['Case','First activity','Last activity','Duration','Events','Evidence'],r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,c.first_activity,c.last_activity,duration(c.duration_seconds),number(c.events),open];
          })):el('div','No cases on this page.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    const shares=list=>list.map(a=>a.activity+' ('+pct(a.case_share_in_cluster)+' vs '+pct(a.case_share_in_log)+')').join('; ')||'—';
    run.onclick=async()=>{
      ++reviewTurn;review.replaceChildren();out.replaceChildren();
      try{
        message.textContent='Clustering…';run.disabled=true;
        const body={};if(k.value)body.k=Number(k.value);if(state.caseType)body.case_type=state.caseType;
        if(filterOn())body.cohort=globalCohort();
        const r=await request('',body);
        if(generation!==state.generation)return;
        if(!r.cases){message.textContent='No cases match this selection.';return;}
        const rows=r.clusters.map(c=>{
          const open=button('Inspect cases','secondary');open.setAttribute('aria-label','Inspect cases in cluster '+c.cluster);
          open.onclick=()=>inspect(body,c);
          return ['Cluster '+c.cluster,number(c.cases)+' ('+pct(c.share)+')',number(c.variants),
          c.median_throughput_seconds===null?'—':duration(c.median_throughput_seconds),c.mean_events_per_case===null?'—':c.mean_events_per_case.toFixed(1),
          (c.top_variants[0]||{activities:[]}).activities.join(' → '),shares(c.more_common_activities),shares(c.less_common_activities),open];
        });
        const how=r.k_chosen_by==='silhouette'?r.k+' clusters chosen by silhouette ('+(r.silhouette===null?'—':r.silhouette.toFixed(2))+')':r.k+' clusters as requested';
        out.replaceChildren(el('div',Number(r.cases).toLocaleString()+' cases, '+Number(r.variants).toLocaleString()+' variants; '+how+'.'+(r.variants_assigned_to_nearest?' '+Number(r.variants_assigned_to_nearest).toLocaleString()+' rare variants joined their nearest cluster.':''),'muted'),
          table(['Cluster','Cases','Variants','Median throughput','Events per case','Most common variant','More common than in the log','Less common than in the log','Evidence'],rows),
          el('div',r.interpretation,'muted'));
        message.textContent='';
      }catch(error){if(generation===state.generation)message.textContent=error.message;}finally{run.disabled=false;}
    };
    return box;
  }

  function complianceSection(activityNames,inspection){
    const box=section('Compliance rules');box.id='owgEventLogCompliance';
    box.appendChild(el('div','Check cases against rules such as "every order is approved before payment" or "the person who creates a purchase order does not approve it". Results describe this log; they are not an audit or legal finding, and no names are shown.','muted'));
    const template=select('owgComplianceTemplate',COMPLIANCE_TEMPLATES);
    const options=activityNames.slice(0,500).map(x=>[x,x]);
    const a=select('owgComplianceA',options),b=select('owgComplianceB',options);if(options.length>1)b.selectedIndex=1;
    const hours=input('owgComplianceHours','number');hours.min='0';hours.step='any';
    const min=input('owgComplianceMin','number');min.min='0';min.step='1';const max=input('owgComplianceMax','number');max.min='0';max.step='1';
    const add=button('Add rule','secondary');add.id='owgComplianceAdd';
    const run=button('Check rules');run.id='owgComplianceRun';run.disabled=true;
    const f={b:field('Activity B',b),hours:field('Within (hours)',hours),min:field('At least',min),max:field('At most',max)};
    const sync=()=>{const t=template.value;show(f.b,t!=='existence');show(f.hours,t==='response_within');show(f.min,t==='existence');show(f.max,t==='existence');};
    template.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Rule',template),field('Activity A',a),...Object.values(f),add);
    const pending=el('div');pending.id='owgComplianceRules';
    const message=el('div',null,'muted');message.id='owgComplianceStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgComplianceResults';
    const review=el('div');review.id='owgComplianceViolations';
    const confidence=input('owgComplianceConfidence','number');confidence.min='50';confidence.max='100';confidence.step='1';confidence.value='95';
    const support=input('owgComplianceSupport','number');support.min='0';support.max='100';support.step='1';support.value='10';
    const suggest=button('Suggest rules from this log','secondary');suggest.id='owgComplianceSuggest';
    const suggestions=el('div');suggestions.id='owgComplianceSuggestions';
    const suggestRow=el('div',null,'row');suggestRow.style.alignItems='end';
    suggestRow.append(field('Followed by at least (% of cases)',confidence),field('Applies to at least (% of cases)',support),suggest);
    box.append(row,suggestRow,suggestions,pending,run,message,out,review);
    const rules=[];const generation=state.generation;
    const endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/compliance';
    const post=(suffix,body)=>api(endpoint+suffix,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)}).then(response=>response.json());
    let checkTurn=0,reviewTurn=0;
    function reviewViolations(result,scope){
      const ticket=++reviewTurn;
      const current=()=>ticket===reviewTurn&&generation===state.generation;
      const heading=el('h4','Violations of '+result.description);heading.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      const controls=el('div',null,'row');controls.append(previous,next);
      review.replaceChildren(heading,status,rows,controls);
      heading.focus();
      const cursors=[null];
      let page=0,nextAfter=null;
      async function load(index){
        previous.disabled=next.disabled=true;
        status.textContent='Loading violations…';
        try{
          const response=await post('/violations',{...scope,rule:result.rule,after:cursors[index]});
          if(!current())return;
          page=index;nextAfter=response.next_after;
          status.textContent=Number(response.violating_cases).toLocaleString()+' violations. Showing '+response.cases.length+' cases on this page.';
          rows.replaceChildren(response.cases.length?table(['Case','Reason','Evidence'],response.cases.map(item=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+item.case_id);
            open.onclick=()=>inspection.open(item.case_id);
            return [item.case_id,item.detail,open];
          })):el('div','No violations on this page.','muted'));
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=page===0;next.disabled=nextAfter===null;}}
      }
      previous.onclick=()=>load(page-1);
      next.onclick=()=>{cursors[page+1]=nextAfter;load(page+1);};
      load(0);
    }
    suggest.onclick=async()=>{
      try{
        const body={min_confidence:Number(confidence.value)/100,min_support:Number(support.value)/100};if(state.caseType)body.case_type=state.caseType;
        const r=await post('/discover',body);
        if(generation!==state.generation)return;
        const same=(x,y)=>JSON.stringify(x)===JSON.stringify(y);
        const rows=r.candidates.map(c=>{
          const take=button('Add','secondary');take.setAttribute('aria-label','Add suggested rule: '+c.description);
          take.onclick=()=>{if(rules.length>=20){message.textContent='Check at most 20 rules at once.';return;}if(!rules.some(x=>same(x,c.rule)))rules.push(c.rule);drawPending();};
          return [c.description,pct(c.confidence)+' ['+pct(c.confidence_ci95[0])+', '+pct(c.confidence_ci95[1])+']',pct(c.support),Number(c.violating_cases).toLocaleString(),take];});
        suggestions.replaceChildren(el('div',r.candidates.length+' suggestions from '+Number(r.cases).toLocaleString()+' cases'+(r.activities_truncated?' (the '+r.activities_considered+' most frequent of '+r.activities_in_log+' activities)':'')+'. '+r.interpretation,'muted'),
          r.candidates.length?table(['Suggested rule','Followed by (95% CI)','Applies to','Violating cases',''],rows):el('div','No rule meets these thresholds. Lower them to see more.','muted'));
      }catch(error){suggestions.replaceChildren(el('div',error.message,'muted'));}
    };
    const label=r=>({response:'every '+r.a+' is eventually followed by '+r.b,chain_response:'every '+r.a+' is immediately followed by '+r.b,precedence:'every '+r.b+' needs an earlier '+r.a,response_within:'every '+r.a+' is followed by '+r.b+' within '+duration(r.seconds),segregation_of_duties:r.a+' and '+r.b+' are done by different people',not_coexistence:r.a+' and '+r.b+' never happen together',existence:r.a+' happens '+(r.max===undefined?'at least '+r.min:r.min===r.max?'exactly '+r.min:r.min+'–'+r.max)+' times'}[r.template]);
    function drawPending(){
      pending.replaceChildren(rules.length?table(['Rule',''],rules.map((r,i)=>{const del=button('Remove','secondary');del.setAttribute('aria-label','Remove rule '+label(r));del.onclick=()=>{rules.splice(i,1);drawPending();};return [label(r),del];})):el('div','No rules added yet.','muted'));
      run.disabled=!rules.length;
    }
    drawPending();
    add.onclick=()=>{try{if(rules.length>=20)throw new Error('Check at most 20 rules at once.');rules.push(complianceRule({template:template.value,a:a.value,b:b.value,hours:hours.value,min:min.value,max:max.value}));message.textContent='';drawPending();}catch(error){message.textContent=error.message;}};
    run.onclick=async()=>{
      const ticket=++checkTurn;
      const current=()=>ticket===checkTurn&&generation===state.generation;
      ++reviewTurn;review.replaceChildren();out.replaceChildren();
      try{
        const scope=state.caseType?{case_type:state.caseType}:{};
        const r=await post('',{...scope,rules});
        if(!current())return;
        const rows=r.results.map(res=>{
          const keep=button('Save as signal','secondary');keep.setAttribute('aria-label','Save as signal: '+res.description);
          keep.onclick=async()=>{try{const sig={name:res.description.slice(0,120),when:{kind:'compliance',rule:res.rule}};if(state.caseType)sig.case_type=state.caseType;
            await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sig)});message.textContent='Saved as a signal.';}catch(error){message.textContent=error.message;}};
          const rate=res.violation_rate===null?'—':pct(res.violation_rate)+' ['+pct(res.violation_rate_ci95[0])+', '+pct(res.violation_rate_ci95[1])+']';
          const actions=el('div',null,'row');
          if(res.violating_cases){
            const browse=button('Review violations','secondary');browse.setAttribute('aria-label','Review violations of '+res.description);
            browse.onclick=()=>reviewViolations(res,scope);
            actions.append(browse);
          }
          actions.append(keep);
          return [res.description,Number(res.activated_cases).toLocaleString(),Number(res.violating_cases).toLocaleString(),rate,res.unknown_cases?Number(res.unknown_cases).toLocaleString():'—',res.violating_case_examples.slice(0,5).map(e=>e.case_id).join(', '),actions];});
        out.replaceChildren(el('div',r.rules_with_violations+' of '+r.results.length+' rules have violations.','muted'),
          table(['Rule','Cases it applies to','Violating cases','Violation rate (95% CI)','Unknown','Example cases',''],rows),el('div',r.interpretation,'muted'));
      }catch(error){if(current())out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  // Self-contained SVG of a drawn map: computed styles are inlined so it renders the same outside the dashboard.
  function exportableSvg(svg,caption){
    const clone=svg.cloneNode(true);
    const source=[svg,...svg.querySelectorAll('*')],target=[clone,...clone.querySelectorAll('*')];
    const keep=['fill','stroke','stroke-width','stroke-dasharray','opacity','fill-opacity','stroke-opacity','font-size','font-family','font-weight','text-anchor','dominant-baseline','visibility','display'];
    source.forEach((node,i)=>{const cs=getComputedStyle(node);const decl=keep.map(k=>{const v=cs.getPropertyValue(k);return v?k+':'+v:'';}).filter(Boolean).join(';');if(decl)target[i].setAttribute('style',decl);});
    clone.querySelectorAll('[tabindex],[role=button]').forEach(n=>{n.removeAttribute('tabindex');n.removeAttribute('role');});
    clone.setAttribute('xmlns','http://www.w3.org/2000/svg');
    const box=svg.viewBox&&svg.viewBox.baseVal&&svg.viewBox.baseVal.width?svg.viewBox.baseVal:{x:0,y:0,width:svg.clientWidth||800,height:svg.clientHeight||600};
    const pad=44,ns='http://www.w3.org/2000/svg';
    clone.setAttribute('viewBox',box.x+' '+box.y+' '+box.width+' '+(box.height+pad));
    clone.setAttribute('width',String(Math.round(box.width)));clone.setAttribute('height',String(Math.round(box.height+pad)));
    clone.removeAttribute('style');
    const bg=document.createElementNS(ns,'rect');Object.entries({x:box.x,y:box.y,width:box.width,height:box.height+pad,fill:'#ffffff'}).forEach(([k,v])=>bg.setAttribute(k,String(v)));
    clone.insertBefore(bg,clone.firstChild);
    const desc=document.createElementNS(ns,'desc');desc.textContent=caption;clone.insertBefore(desc,clone.firstChild);
    caption.split('\n').slice(0,2).forEach((line,i)=>{const t=document.createElementNS(ns,'text');
      Object.entries({x:box.x+8,y:box.y+box.height+16+i*16,'font-size':11,'font-family':'sans-serif',fill:'#334155'}).forEach(([k,v])=>t.setAttribute(k,String(v)));t.textContent=line.slice(0,160);clone.appendChild(t);});
    return {text:new XMLSerializer().serializeToString(clone),width:box.width,height:box.height+pad};
  }
  function downloadBlob(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function mapFileName(ext){return 'process-map-'+String(state.logId||'log').replace(/[^A-Za-z0-9_-]/g,'').slice(0,40)+'.'+ext;}

  function processMapSection(inspection){
    const box=section('Process map');box.id='owgEventLogProcessMap';
    box.appendChild(el('div','Simplify the map with the sliders. Hidden activities are removed first, so arrows can join steps that had hidden work between them. Times are gaps between recorded events, not measured work.','muted'));
    const slider=(id,label,value)=>{const node=input(id,'range');node.min='5';node.max='100';node.step='5';node.value=String(value);
      const out=el('output',value+'%');out.id=id+'Value';out.htmlFor=id;node.oninput=()=>{out.textContent=node.value+'%';schedule();};
      const wrap=field(label,node);wrap.appendChild(out);return {node,wrap};};
    const acts=slider('owgMapActivities','Activities shown',100),paths=slider('owgMapPaths','Paths shown',100);
    const row=el('div',null,'row');row.style.alignItems='end';row.append(acts.wrap,paths.wrap);
    const status=el('div',null,'muted');status.id='owgMapStatus';status.setAttribute('role','status');
    const graphHost=el('div');graphHost.id='owgMapGraph';const slow=el('div');slow.id='owgMapSlowest';
    const player=mapPlayback(box,inspection);
    const svgButton=button('Download map (SVG)','secondary');svgButton.id='owgMapDownloadSvg';
    const pngButton=button('Download map (PNG)','secondary');pngButton.id='owgMapDownloadPng';
    const csvButton=button('Download map data (CSV)','secondary');csvButton.id='owgMapDownloadCsv';
    const exportRow=el('div',null,'row');exportRow.append(svgButton,pngButton,csvButton);
    box.append(row,status,player.node,graphHost,exportRow,slow);
    const generation=state.generation;let timer=null,request=0,lastCaption='',lastMap=null;
    // The numbers behind the drawn map, one row per activity, start, path and end, for a spreadsheet.
    csvButton.onclick=()=>{
      if(!lastMap){status.textContent='No map is drawn to download.';return;}
      const r=lastMap,lines=[['kind','from','to','count','cases','median_gap_seconds','mean_gap_seconds','p90_gap_seconds']];
      r.activities.forEach(a=>lines.push(['activity',a.activity,'',a.event_count,a.case_count,'','','']));
      Object.entries(r.start_activities||{}).forEach(([a,n])=>lines.push(['start','',a,n,'','','','']));
      r.transitions.forEach(t=>lines.push(['path',t.from,t.to,t.transition_count,t.case_count,t.median_elapsed_gap_seconds,t.mean_elapsed_gap_seconds,t.p90_elapsed_gap_seconds]));
      Object.entries(r.end_activities||{}).forEach(([a,n])=>lines.push(['end',a,'',n,'','','','']));
      downloadBlob(new Blob([toCSV(lines)],{type:'text/csv'}),mapFileName('csv'));
      status.textContent='Downloaded the map data: '+r.activities.length+' activities and '+r.transitions.length+' paths at the current settings.';
    };
    const currentSvg=()=>graphHost.querySelector('#owgMapDrawing svg')||graphHost.querySelector('svg');
    svgButton.onclick=()=>{const svg=currentSvg();if(!svg){status.textContent='No map is drawn to download.';return;}
      downloadBlob(new Blob([exportableSvg(svg,lastCaption).text],{type:'image/svg+xml'}),mapFileName('svg'));};
    pngButton.onclick=()=>{const svg=currentSvg();if(!svg){status.textContent='No map is drawn to download.';return;}
      const out=exportableSvg(svg,lastCaption),img=new Image(),scale=2;
      img.onload=()=>{const canvas=document.createElement('canvas');canvas.width=Math.round(out.width*scale);canvas.height=Math.round(out.height*scale);
        const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.drawImage(img,0,0,out.width,out.height);
        canvas.toBlob(blob=>{if(blob)downloadBlob(blob,mapFileName('png'));else status.textContent='The PNG could not be created; download the SVG instead.';},'image/png');};
      img.onerror=()=>{status.textContent='The PNG could not be created; download the SVG instead.';};
      img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(out.text);};
    function schedule(){clearTimeout(timer);timer=setTimeout(()=>draw().catch(error=>{status.textContent=error.message;}),250);}
    async function draw(){
      const mine=++request;
      const r=await getJSON(logPath('process-map',{activities:String(Number(acts.node.value)/100),paths:String(Number(paths.node.value)/100)}));
      if(mine!==request||generation!==state.generation)return;
      lastMap=r;
      const c=r.coverage;
      lastCaption=(document.querySelector('#owgEventLogPick option:checked')?.textContent||'Event log')+' · activities '+acts.node.value+'%, paths '+paths.node.value+'%'+(r.activity_mapping?' · grouped by '+r.activity_mapping.name:'')+
        '\nShowing '+c.activities_shown+' activities ('+pct(c.event_share_shown)+' of events) and '+c.transition_types_shown+' of '+c.transition_types_total+' paths. Derived from the supplied log; not a verified business workflow.';
      status.textContent='Showing '+c.activities_shown+' activities ('+pct(c.event_share_shown)+' of events) and '+c.transition_types_shown+' of '+c.transition_types_total+' paths ('+pct(c.transition_share_shown)+' of transitions).'+(c.activity_limit_reached?' The map shows at most 64 activities.':'');
      const draw=window.owgProcessGraph&&window.owgProcessGraph.drawObserved;
      graphHost.replaceChildren();player.reset();
      if(draw&&r.activities.length){
        try{const start=Object.keys(r.start_activities)[0];const drawing=draw({activities:r.activities,transitions:r.transitions,cases:start?[{path:[start]}]:[]},()=>{},()=>{});drawing.id='owgMapDrawing';graphHost.appendChild(drawing);player.load(drawing,Number(acts.node.value)/100);}
        catch(_){graphHost.appendChild(el('div','Map unavailable at this level; the paths are listed below.','muted'));}
      }
      const slowest=[...r.transitions].filter(t=>t.median_elapsed_gap_seconds!==null).sort((x,y)=>y.median_elapsed_gap_seconds-x.median_elapsed_gap_seconds).slice(0,10);
      slow.replaceChildren(el('h4','Slowest paths'),table(['From','To','Transitions','Median gap','Mean gap','90th percentile gap'],slowest.map(t=>[t.from,t.to,Number(t.transition_count).toLocaleString(),duration(t.median_elapsed_gap_seconds),duration(t.mean_elapsed_gap_seconds),duration(t.p90_elapsed_gap_seconds)])));
    }
    draw().catch(error=>{status.textContent=error.message;});
    return box;
  }

  // Where a case's token is at time t: between events i and i+1 (fraction f), at the last event, or not started/finished.
  function tokenAt(events,t){
    if(!events.length||t<events[0][0]||t>events[events.length-1][0])return null;
    let lo=0,hi=events.length-1;
    while(lo<hi){const mid=(lo+hi+1)>>1;if(events[mid][0]<=t)lo=mid;else hi=mid-1;}
    if(lo===events.length-1)return {i:lo,f:0};
    const a=events[lo][0],b=events[lo+1][0];
    return {i:lo,f:b>a?(t-a)/(b-a):1};
  }

  // Disco-style playback: one token per sampled case moves along the drawn map between its recorded events.
  function mapPlayback(owner,inspection){
    const node=el('div');node.id='owgMapPlayback';
    const play=button('Play','secondary');play.id='owgPlaybackPlay';play.setAttribute('aria-pressed','false');play.disabled=true;
    const position=input('owgPlaybackPosition','range');position.min='0';position.max='1000';position.step='1';position.value='0';position.disabled=true;
    const length=select('owgPlaybackLength',[['20','20 seconds'],['60','1 minute'],['120','2 minutes']]);
    const focus=select('owgPlaybackCase',[['','All sampled cases']]);focus.disabled=true;
    const inspect=button('Open selected case','secondary');inspect.id='owgPlaybackOpenCase';inspect.disabled=true;
    const clock=el('span','—');clock.id='owgPlaybackTime';
    const message=el('div','Playback starts once the map is drawn.','muted');message.id='owgPlaybackStatus';message.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';
    const where=field('Playback position',position);where.appendChild(clock);
    row.append(play,where,field('Playback length',length));
    const review=el('div',null,'row');review.style.alignItems='end';
    review.append(field('Case to follow',focus),inspect);
    node.append(el('h4','Case playback'),row,review,message);
    const reduced=()=>window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let data=null,svgNode=null,layer=null,tokens=[],edges=new Map(),centres=new Map(),frame=0,last=0,ticket=0;
    const generation=state.generation;
    const stamp=t=>data&&data.origin?new Date(Date.parse(data.origin)+t*1000).toISOString().slice(0,16).replace('T',' ')+' UTC':'—';
    function stop(){cancelAnimationFrame(frame);frame=0;play.textContent='Play';play.setAttribute('aria-pressed','false');}
    function reset(){
      stop();ticket++;data=null;tokens=[];if(layer)layer.remove();layer=null;
      play.disabled=position.disabled=focus.disabled=inspect.disabled=true;
      setOptions(focus,[['','All sampled cases']]);clock.textContent='—';position.removeAttribute('aria-valuetext');
    }
    function paint(){
      if(!data)return;
      const focused=focus.value===''?null:Number(focus.value);
      const steps=focused===null?null:data.cases[focused].events;
      const start=steps?.[0]?.[0]??0,end=steps?.[steps.length-1]?.[0]??data.span_seconds;
      const fraction=Number(position.value)/1000;
      const t=fraction===1?end:start+fraction*(end-start);
      let moving=0;
      data.cases.forEach((c,k)=>{
        const token=tokens[k],at=focused===null||focused===k?tokenAt(c.events,t):null;
        if(!at){token.setAttribute('visibility','hidden');return;}
        const from=data.activities[c.events[at.i][1]],next=c.events[at.i+1];
        let x,y;
        if(!next||at.f===0){[x,y]=centres.get(from)||[0,0];}
        else{
          const to=data.activities[next[1]],edge=edges.get(from+'\u0000'+to);
          if(edge){const p=edge.path.getPointAtLength(at.f*edge.length);x=p.x;y=p.y;}
          else{const a=centres.get(from)||[0,0],b=centres.get(to)||[0,0];x=a[0]+(b[0]-a[0])*at.f;y=a[1]+(b[1]-a[1])*at.f;}
        }
        token.setAttribute('cx',x.toFixed(1));token.setAttribute('cy',y.toFixed(1));token.setAttribute('visibility','visible');moving++;
      });
      const shown=focused===null?moving+' cases in progress':moving?'Selected case in progress':'Selected case outside this position';
      clock.textContent=stamp(t)+' · '+shown;
      position.setAttribute('aria-valuetext',stamp(t));
    }
    function tick(now){
      if(!owner.isConnected||generation!==state.generation||!data){stop();return;}
      const total=Number(length.value)*1000,step=reduced();
      const elapsed=now-last;
      if(!step||elapsed>=1000){
        const advance=step?50:Math.max(0,elapsed)/total*1000;
        last=now;
        const value=Math.min(1000,Number(position.value)+advance);
        position.value=String(value);paint();
        if(value>=1000){stop();return;}
      }
      frame=requestAnimationFrame(tick);
    }
    play.onclick=()=>{
      if(frame){stop();return;}
      if(Number(position.value)>=1000)position.value='0';
      play.textContent='Pause';play.setAttribute('aria-pressed','true');last=performance.now();frame=requestAnimationFrame(tick);
    };
    position.oninput=paint;
    focus.onchange=()=>{
      inspect.disabled=focus.value==='';
      stop();position.value='0';paint();
    };
    inspect.onclick=()=>{
      if(!data||focus.value==='')return;
      stop();inspection.open(data.cases[Number(focus.value)].case_id);
    };
    async function load(drawing,share){
      const mine=++ticket;
      message.textContent='Loading playback…';
      try{
        const r=await getJSON(logPath('playback',{activities:String(share),max_cases:'200'}));
        if(mine!==ticket||generation!==state.generation||!drawing.isConnected)return;
        svgNode=drawing.querySelector('svg');if(!svgNode)return;
        edges=new Map();centres=new Map();
        svgNode.querySelectorAll('path[aria-label^="Inspect observed transition "]').forEach(path=>{
          const pair=path.getAttribute('aria-label').slice('Inspect observed transition '.length).split(' → ');
          if(pair.length===2)edges.set(pair[0]+'\u0000'+pair[1],{path,length:path.getTotalLength()});
        });
        svgNode.querySelectorAll('g[aria-label^="Inspect observed activity "]').forEach(g=>{
          const rect=g.querySelector('rect');if(!rect)return;
          centres.set(g.getAttribute('aria-label').slice('Inspect observed activity '.length),[Number(rect.getAttribute('x'))+Number(rect.getAttribute('width'))/2,Number(rect.getAttribute('y'))+Number(rect.getAttribute('height'))/2]);
        });
        data=r;
        layer=document.createElementNS('http://www.w3.org/2000/svg','g');layer.setAttribute('aria-hidden','true');layer.setAttribute('pointer-events','none');
        tokens=r.cases.map(()=>{const c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('r','5');c.setAttribute('fill','#c2410c');c.setAttribute('fill-opacity','0.75');c.setAttribute('visibility','hidden');c.dataset.owgToken='';layer.appendChild(c);return c;});
        svgNode.appendChild(layer);
        let offMap=0;
        r.cases.forEach(c=>{for(let i=1;i<c.events.length;i++)if(!edges.has(r.activities[c.events[i-1][1]]+'\u0000'+r.activities[c.events[i][1]]))offMap++;});
        const cov=r.coverage;
        message.textContent='Playing '+Number(cov.cases_shown).toLocaleString()+' of '+Number(cov.cases).toLocaleString()+' cases'+(cov.cases_sampled?' (evenly sampled by start time)':'')+' over '+duration(r.span_seconds)+' of recorded time.'
          +(cov.steps_sharing_a_timestamp?' '+Number(cov.steps_sharing_a_timestamp).toLocaleString()+' steps share a timestamp and jump instantly.':'')
          +(offMap?' '+Number(offMap).toLocaleString()+' steps follow paths hidden from the map and move in a straight line.':'')
          +(r.activities_hidden?' Events of '+r.activities_hidden+' hidden activities are left out.':'')
          +(cov.cases_truncated?' '+cov.cases_truncated+' long cases are shortened.':'')+' '+r.interpretation;
        setOptions(focus,[['','All sampled cases'],...r.cases.map((c,i)=>[String(i),c.case_id])]);
        position.value='0';play.disabled=position.disabled=focus.disabled=!r.cases.length;paint();
      }catch(error){if(mine===ticket)message.textContent=error.message;}
    }
    return {node,load,reset};
  }

  // Builds a signal rule body from the form values; throws a readable error.
  function newCaseChange(signal){
    if(signal.new_cases_since_last_evaluation!==null&&signal.new_cases_since_last_evaluation!==undefined)
      return Number(signal.new_cases_since_last_evaluation).toLocaleString();
    const c=signal.new_cases_comparison;
    if(!c||c.reason==='first_check')return 'first check';
    if(c.lower_bound!==null&&c.upper_bound!==null)
      return Number(c.lower_bound).toLocaleString()+'–'+Number(c.upper_bound).toLocaleString()+' (bounded history)';
    return 'unknown (bounded history)';
  }
  function signalValueText(signal){
    const {when,value}=signal;
    if(when.kind==='due_date')return value===null?'—':when.metric==='late_share'?pct(value)+' late':Number(value).toLocaleString()+' overdue';
    if(when.kind==='conformance')return value===null?'—':pct(value)+' of '+Number(signal.conformance.cases).toLocaleString()+' cases';
    if(when.kind==='kpi'){
      if(value===null)return '—';
      if(when.metric.endsWith('_rate'))return pct(value);
      return when.metric==='case_count'?Number(value).toLocaleString():duration(value);
    }
    return Number(signal.matching_cases).toLocaleString()+(signal.matching_cases===1?' case':' cases');
  }
  function signalEvidenceText(signal){
    return (signal.evidence_cases||[]).slice(0,5).map(c=>c.case_id+(signal.when.condition==='open_longer_than'&&c.age_since_first_event_seconds!==undefined?
      ' (age '+duration(c.age_since_first_event_seconds)+', idle '+duration(c.idle_since_last_event_seconds)+')':'')).join(', ');
  }
  function signalRule(values){
    const name=String(values.name||'').trim();if(!name)throw new Error('Name the signal.');
    const hours=Number(values.hours);
    if(values.kind==='kpi'){
      const rate=values.metric.endsWith('_rate');const raw=Number(values.threshold);
      if(!(raw>=0))throw new Error('Enter a threshold.');
      const when={kind:'kpi',metric:values.metric,operator:values.operator,threshold:rate?raw/100:values.metric==='case_count'?raw:Math.round(raw*3600)};
      if(values.metric==='sla_breach_rate'){if(!(hours>0))throw new Error('Enter the SLA in hours.');when.sla_seconds=Math.round(hours*3600);}
      return {name,when};
    }
    const when={kind:'cases',condition:values.condition};
    if(['duration_over','open_longer_than'].includes(values.condition)){if(!(hours>0))throw new Error('Enter the number of hours.');when.seconds=Math.round(hours*3600);}
    if(['contains_activity','lacks_activity'].includes(values.condition))when.activity=values.activity;
    if(values.condition==='open_longer_than'){when.end_activities=String(values.ends||'').split(',').map(x=>x.trim()).filter(Boolean);if(!when.end_activities.length)throw new Error('List the activities that end a case.');}
    return {name,when};
  }

  function signalSection(activityNames){
    const box=section('Signals');box.id='owgEventLogSignals';
    box.appendChild(el('div','Save conditions to check this log against, such as a slow median or cases left open. Checking shows which signals hold now and which cases are new since the last check. Signals never execute business actions. Optional outbound counts-only alerts require separate opt-in below.','muted'));
    const name=input('owgSignalName','text','Signal name');name.maxLength=120;
    const kind=select('owgSignalKind',[['cases','Cases that…'],['kpi','A KPI that…']]);
    const condition=select('owgSignalCondition',[['open_longer_than','are open longer than'],['duration_over','take longer than'],['lacks_activity','never reach activity'],['contains_activity','contain activity'],['has_rework','have rework']]);
    const metric=select('owgSignalMetric',[['median_throughput_seconds','Median case time (hours)'],['p90_throughput_seconds','90th percentile case time (hours)'],['rework_rate','Share of cases with rework (%)'],['sla_breach_rate','Share of cases over an SLA (%)'],['case_count','Number of cases']]);
    const operator=select('owgSignalOperator',[['above','is above'],['below','is below']]);
    const threshold=input('owgSignalThreshold','number');threshold.min='0';threshold.step='any';
    const hours=input('owgSignalHours','number');hours.min='0';hours.step='any';
    const activity=select('owgSignalActivity',activityNames.slice(0,500).map(a=>[a,a]));
    const ends=input('owgSignalEnds','text','Close, Cancel');
    const add=button('Save signal','secondary');add.id='owgSignalAdd';
    const preview=button('Preview signal','secondary');preview.id='owgSignalPreview';
    const previewStatus=el('div',null,'muted');previewStatus.id='owgSignalPreviewStatus';previewStatus.setAttribute('role','status');
    const evaluate=button('Check signals');evaluate.id='owgSignalEvaluate';
    const fields={condition:field('Condition',condition),metric:field('KPI',metric),operator:field('Comparison',operator),threshold:field('Threshold',threshold),hours:field('Hours',hours),activity:field('Activity',activity),ends:field('Activities that end a case',ends)};
    const sync=()=>{const k=kind.value==='kpi';show(fields.condition,!k);show(fields.metric,k);show(fields.operator,k);show(fields.threshold,k);
      show(fields.hours,k?metric.value==='sla_breach_rate':['duration_over','open_longer_than'].includes(condition.value));
      show(fields.activity,!k&&['contains_activity','lacks_activity'].includes(condition.value));show(fields.ends,!k&&condition.value==='open_longer_than');};
    kind.onchange=condition.onchange=metric.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Name',name),field('Watch',kind),...Object.values(fields),preview,add);
    const rules=el('div');rules.id='owgSignalRules';
    const message=el('div',null,'muted');message.id='owgSignalStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgSignalResults';
    const reviewHeading=el('h4','Review evaluated signals');
    const reviewNote=el('p','Acknowledge a specific triggered result locally, or reopen it. Snooze a result for one hour or one day; it returns on refresh after expiry. Reviews never send notifications, approve actions, or imply business outcomes. Stale results require a fresh Check signals.','muted');
    const inbox=el('div');inbox.id='owgSignalInbox';
    const reviewRefresh=button('Refresh review inbox','secondary');reviewRefresh.id='owgSignalReviewRefresh';
    const deliveryHeading=el('h4','Optional external signal alerts');
    const deliveryNote=el('p','Only after you schedule saved signal checks and explicitly opt in, OWG can POST the NUMBER of newly unreviewed signal states to your HTTPS webhook. No log names, rule names, case IDs, source identifiers, metric values or evidence leave this computer. Alerts cannot authorize business actions. The receiver controls delivery and retention after receipt.','muted');
    const deliveryUrl=input('owgSignalDeliveryUrl','url','https://your-https-webhook.example/alerts');
    deliveryUrl.maxLength=700;
    const deliveryConsent=input('owgSignalDeliveryConsent','checkbox');
    const countsConsent=input('owgSignalCountsConsent','checkbox');
    const deliveryEnable=button('Enable counts-only webhook','secondary');deliveryEnable.id='owgSignalDeliveryEnable';deliveryEnable.disabled=true;
    const deliveryRefresh=button('Refresh delivery status','secondary');deliveryRefresh.id='owgSignalDeliveryRefresh';
    const deliveryRemove=button('Remove webhook and erase queued alerts','secondary');deliveryRemove.id='owgSignalDeliveryRemove';deliveryRemove.disabled=true;
    const deliveryResume=button('Resume paused webhook','secondary');deliveryResume.id='owgSignalDeliveryResume';deliveryResume.disabled=true;
    const deliveryStatus=el('p','No external delivery is enabled by default.','muted');
    deliveryStatus.id='owgSignalDeliveryStatus';deliveryStatus.setAttribute('role','status');
    const deliverySecret=input('owgSignalDeliverySecret','password');deliverySecret.readOnly=true;deliverySecret.autocomplete='off';
    const revealSecret=button('Reveal one-time signing secret','secondary');
    const secretPane=el('div');secretPane.hidden=true;
    secretPane.append(el('p','This HMAC secret is shown only when the channel is created. Store it with your receiving webhook, not in source control. It cannot be recovered from this page.','muted'),
      field('One-time HMAC signing secret',deliverySecret),revealSecret);
    revealSecret.onclick=()=>{deliverySecret.type=deliverySecret.type==='password'?'text':'password';};
    const deliveryPanel=el('div');deliveryPanel.id='owgSignalDeliveryPanel';
    deliveryPanel.append(deliveryHeading,deliveryNote,field('HTTPS webhook receiver',deliveryUrl),
      field('I authorize OWG to send external signal attention notifications',deliveryConsent),
      field('I reviewed the outbound disclosure: only counts and opaque notification IDs, no saved work or case evidence',countsConsent),
      deliveryEnable,deliveryRefresh,deliveryResume,deliveryRemove,deliveryStatus,secretPane);
    const transitionHeading=el('h4','Signal state changes');
    const transitionNote=el('p','Locally recorded triggered and cleared transitions from explicit or scheduled evaluations. Repeated unchanged results do not create alerts. This is not live source monitoring or a notification feed. Up to 5,000 transitions are retained per log.','muted');
    const transitionRefresh=button('Refresh signal changes','secondary');transitionRefresh.id='owgSignalTransitionRefresh';
    const transitionPrevious=button('Newer signal changes','secondary');transitionPrevious.id='owgSignalTransitionPrevious';transitionPrevious.disabled=true;
    const transitionNext=button('Older signal changes','secondary');transitionNext.id='owgSignalTransitionNext';transitionNext.disabled=true;
    const transitionStatus=el('div','Refresh to inspect saved signal changes.','muted');
    transitionStatus.id='owgSignalTransitionStatus';transitionStatus.setAttribute('role','status');transitionStatus.setAttribute('aria-live','polite');
    const transitionResults=el('div');transitionResults.id='owgSignalTransitions';
    const allHeading=el('h4','Across all saved logs');
    const allNote=el('p','One local overview of saved signal review states across every imported dataset. This does not re-evaluate rules, read source accounts, send alerts or execute work. Open the corresponding log for the existing state-pinned review controls.','muted');
    const allFilter=select('owgSignalAllFilter',[
      ['attention','Needs attention'],['all','All states'],['unreviewed','Unreviewed'],
      ['stale','Stale'],['not_evaluated','Not yet evaluated'],['snoozed','Snoozed'],
      ['acknowledged','Acknowledged'],['cleared','Cleared'],['unavailable','Unavailable']]);
    allFilter.setAttribute('aria-label','Across-log signal status filter');
    const allRefresh=button('Refresh all-log signals','secondary');allRefresh.id='owgSignalAllRefresh';
    const allPrevious=button('Previous signals','secondary');allPrevious.id='owgSignalAllPrevious';allPrevious.disabled=true;
    const allNext=button('Next signals','secondary');allNext.id='owgSignalAllNext';allNext.disabled=true;
    const allStatus=el('div','Load saved signals across logs to review them here.','muted');
    allStatus.id='owgSignalAllStatus';allStatus.setAttribute('role','status');allStatus.setAttribute('aria-live','polite');
    const allResults=el('div');allResults.id='owgSignalAllResults';
    const timelineHeading=el('h4','Across-log signal change timeline');
    const timelineNote=el('p','Recent triggered and cleared changes from saved local evaluations, across every imported dataset. This is historical evidence, not current alert status. Reading it never contacts providers, sends notifications or authorizes actions.','muted');
    const timelineFilter=select('owgSignalTimelineFilter',[
      ['all','All changes'],['triggered','Triggered only'],['cleared','Cleared only']]);
    timelineFilter.setAttribute('aria-label','Across-log signal change filter');
    const timelineRefresh=button('Refresh all-log changes','secondary');timelineRefresh.id='owgSignalTimelineRefresh';
    const timelinePrevious=button('Newer changes','secondary');timelinePrevious.id='owgSignalTimelinePrevious';timelinePrevious.disabled=true;
    const timelineNext=button('Older changes','secondary');timelineNext.id='owgSignalTimelineNext';timelineNext.disabled=true;
    const timelineStatus=el('div','Load locally recorded changes across saved logs.','muted');
    timelineStatus.id='owgSignalTimelineStatus';timelineStatus.setAttribute('role','status');timelineStatus.setAttribute('aria-live','polite');
    const timelineResults=el('div');timelineResults.id='owgSignalTimelineResults';
    const timelineControls=el('div',null,'actions');timelineControls.append(timelineFilter,timelineRefresh,timelinePrevious,timelineNext);
    const allControls=el('div',null,'actions');allControls.append(allFilter,allRefresh,allPrevious,allNext);
    box.append(row,previewStatus,message,rules,evaluate,out,reviewHeading,reviewNote,reviewRefresh,inbox,
      deliveryPanel,transitionHeading,transitionNote,transitionRefresh,transitionPrevious,transitionNext,transitionStatus,transitionResults,
      allHeading,allNote,allControls,allStatus,allResults,
      timelineHeading,timelineNote,timelineControls,timelineStatus,timelineResults);
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals';
    const selectedLogId=state.logId;
    const inboxBase=()=>'/v1/event-logs/'+encodeURIComponent(selectedLogId)+'/signal-inbox';
    const deliveryBase=()=>'/v1/schedules/signal-delivery/'+encodeURIComponent(selectedLogId);
    let deliveryRequestId=0,hasDelivery=false;
    const deliverySync=()=>{
      deliveryEnable.disabled=hasDelivery||!deliveryConsent.checked||!countsConsent.checked||!deliveryUrl.value.trim();
      deliveryRemove.disabled=!hasDelivery;
    };
    deliveryConsent.onchange=countsConsent.onchange=deliveryUrl.oninput=deliverySync;
    const refreshDelivery=async()=>{
      const requestId=++deliveryRequestId;
      const value=await getJSON(deliveryBase());
      if(generation!==state.generation||requestId!==deliveryRequestId)return;
      hasDelivery=value.configured===true;
      deliveryResume.disabled=!hasDelivery||value.enabled!==false;
      deliveryStatus.textContent=hasDelivery
        ?'Counts-only webhook: '+(value.enabled?'enabled':'paused')+' for '+value.recipient_host+
          '; '+value.pending_notifications+' pending, '+value.delivered_notifications+
          ' acknowledged by receiver.'+(value.paused_reason?' Paused: '+value.paused_reason+'.':'')+
          ' No source facts or identities are included.'
        :'External alerts are disabled. Create an explicit saved-signal schedule first, then review the two delivery acknowledgements.';
      deliverySync();
    };
    deliveryEnable.onclick=async()=>{
      if(!deliveryConsent.checked||!countsConsent.checked||hasDelivery||generation!==state.generation)return;
      deliveryEnable.disabled=true;secretPane.hidden=true;deliverySecret.value='';
      try{
        const response=await (await api(deliveryBase(),{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({receiver_url:deliveryUrl.value.trim(),
            confirm_external_delivery:true,confirm_counts_only:true})})).json();
        if(generation!==state.generation)return;
        deliverySecret.value=response.notification_secret||'';
        secretPane.hidden=!deliverySecret.value;
        deliveryConsent.checked=false;countsConsent.checked=false;
        await refreshDelivery();
      }catch(error){deliveryStatus.textContent=error.message;}
      finally{if(generation===state.generation)deliverySync();}
    };
    deliveryRemove.onclick=async()=>{
      if(generation!==state.generation||!hasDelivery)return;
      if(!window.confirm('Remove this webhook, its signing key and all queued alerts? Previously delivered messages cannot be recalled.'))return;
      deliveryRemove.disabled=true;
      try{
        await api(deliveryBase()+'/remove',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({confirm_remove:true})});
        if(generation!==state.generation)return;
        secretPane.hidden=true;deliverySecret.value='';deliveryConsent.checked=false;countsConsent.checked=false;
        await refreshDelivery();
      }catch(error){deliveryStatus.textContent=error.message;}
      finally{if(generation===state.generation)deliverySync();}
    };
    deliveryResume.onclick=async()=>{
      if(generation!==state.generation||!hasDelivery)return;
      if(!window.confirm('Resume outbound counts-only notification delivery to the previously approved webhook?'))return;
      deliveryResume.disabled=true;
      try{
        await api(deliveryBase()+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({confirm_external_delivery:true})});
        if(generation===state.generation)await refreshDelivery();
      }catch(error){deliveryStatus.textContent=error.message;}
    };
    deliveryRefresh.onclick=()=>refreshDelivery().catch(error=>{deliveryStatus.textContent=error.message;});
    const transitionBase=()=>'/v1/event-logs/'+encodeURIComponent(selectedLogId)+'/signal-history';
    const generation=state.generation;
    let transitionRequestId=0,transitionCursor='',transitionHistory=[],transitionNextCursor=null;
    async function refreshHistory(before='',history=[]){
      const requestId=++transitionRequestId;
      transitionRefresh.disabled=true;transitionPrevious.disabled=true;transitionNext.disabled=true;transitionResults.replaceChildren();
      transitionStatus.textContent='Loading locally recorded signal changes…';
      try{
        const params=new URLSearchParams({limit:'100'});if(before)params.set('before',before);
        const r=await getJSON(transitionBase()+'?'+params);
        if(generation!==state.generation||requestId!==transitionRequestId)return;
        transitionCursor=before;transitionHistory=history;transitionNextCursor=r.next_before??null;
        const events=r.items||[];
        transitionStatus.textContent=Number(r.retained_total||0)+' saved transitions in this log; showing '+
          events.length+(before?' (older page)':'')+
          '. Results can change between pages. Evaluations only; no notifications or automated actions.';
        transitionResults.replaceChildren(events.length
          ?table(['Evaluated (UTC)','Signal','Change'],events.map(x=>[
            x.evaluated_at||'—',x.name,x.transition==='triggered'?'Triggered':'Cleared']))
          :el('div','No triggered/cleared changes recorded for this dataset yet.','muted'));
      }catch(error){
        if(generation===state.generation&&requestId===transitionRequestId){
          transitionCursor='';transitionHistory=[];transitionNextCursor=null;
          transitionStatus.textContent=error.message||'Signal change history unavailable.';
          transitionResults.replaceChildren();
        }
      }finally{
        if(generation===state.generation&&requestId===transitionRequestId){transitionRefresh.disabled=false;transitionPrevious.disabled=!transitionHistory.length;transitionNext.disabled=transitionNextCursor===null;}
      }
    }
    transitionRefresh.onclick=()=>refreshHistory();
    transitionPrevious.onclick=()=>{if(transitionHistory.length)refreshHistory(transitionHistory[transitionHistory.length-1],transitionHistory.slice(0,-1));};
    transitionNext.onclick=()=>{if(transitionNextCursor!==null)refreshHistory(transitionNextCursor,[...transitionHistory,transitionCursor]);};
    let inboxRefreshId=0;
    async function refreshInbox(){
      const requestId=++inboxRefreshId;
      const r=await getJSON(inboxBase());
      if(generation!==state.generation||requestId!==inboxRefreshId)return;
      const selected=new Map();
      const bulk=button('Acknowledge selected (0)','secondary');
      bulk.id='owgSignalBulkAcknowledge';
      bulk.disabled=true;
      const updateBulk=()=>{
        bulk.textContent='Acknowledge selected ('+selected.size+')';
        bulk.disabled=selected.size===0||selected.size>20;
      };
      const rows=(r.items||[]).map(item=>{
        const action=el('div');
        let selection='—';
        if(item.status==='unreviewed'&&item.state_fingerprint){
          const check=el('input');check.type='checkbox';
          check.setAttribute('aria-label','Select signal '+item.name+' for acknowledgement');
          check.onchange=()=>{
            if(check.checked)selected.set(item.rule_id,{rule_id:item.rule_id,state_fingerprint:item.state_fingerprint});
            else selected.delete(item.rule_id);
            updateBulk();
          };
          selection=check;
        }
        if(item.state_fingerprint&&item.triggered===true){
          const acknowledged=item.status==='acknowledged';
          const click=button(acknowledged?'Reopen':'Acknowledge','secondary');
          click.setAttribute('aria-label',(acknowledged?'Reopen':'Acknowledge')+' signal '+item.name);
          click.onclick=async()=>{
            if(generation!==state.generation||state.logId!==selectedLogId)return;
            click.disabled=true;
            try{
              await api(inboxBase()+'/'+encodeURIComponent(item.rule_id)+'/'+(acknowledged?'reopen':'acknowledge'),{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state_fingerprint:item.state_fingerprint})});
              message.textContent=acknowledged?'Signal reopened.':'Signal acknowledged locally.';
            }catch(error){message.textContent=error.message;}
            finally{await refreshInbox().catch(error=>{message.textContent=error.message;});}
          };
          action.appendChild(click);
          if(!acknowledged)for(const [label,minutes] of (item.status==='snoozed'?[['Resume review',0]]:[['Snooze 1 hour',60],['Snooze 1 day',1440]])){
            const defer=button(label,'secondary');defer.setAttribute('aria-label',label+' for signal '+item.name);
            defer.onclick=async()=>{
              if(generation!==state.generation||state.logId!==selectedLogId)return;
              defer.disabled=true;
              try{await api(inboxBase()+'/'+encodeURIComponent(item.rule_id)+'/snooze',{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state_fingerprint:item.state_fingerprint,minutes})});
                message.textContent=minutes?'Signal snoozed locally.':'Signal returned to review.';
              }catch(error){message.textContent=error.message;}
              finally{await refreshInbox().catch(error=>{message.textContent=error.message;});}
            };action.appendChild(defer);
          }
        }
        return [selection,item.name,item.status.replace(/_/g,' '),item.evaluated_at||'not checked',
          item.acknowledged_at||'—',item.snoozed_until||'—',action];
      });
      bulk.onclick=async()=>{
        if(generation!==state.generation||state.logId!==selectedLogId||bulk.disabled)return;
        const reviews=[...selected.values()];
        if(!reviews.length||reviews.length>20)return;
        if(!window.confirm('Acknowledge '+reviews.length+' selected signal(s)? This records local review only, never an approval for actions.'))return;
        bulk.disabled=true;
        try{
          await api(inboxBase()+'/acknowledge-batch',{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviews})});
          message.textContent=reviews.length+' signal(s) acknowledged locally.';
        }catch(error){message.textContent=error.message;}
        finally{await refreshInbox().catch(error=>{message.textContent=error.message;});}
      };
      inbox.replaceChildren(
        el('div',r.unreviewed+' signals currently require review. No business actions. Outbound counts-only alerts are available only with a separate explicit webhook opt-in.','muted'),
        el('div','Select up to 20 unreviewed signals for one atomic acknowledgement. Snoozed and stale results cannot be bulk-reviewed.','muted'),
        bulk,
        rows.length?table(['Select','Signal','Review status','Evaluated','Acknowledged','Snoozed until',''],rows):el('div','No saved signals to review.','muted'));
    }
    // Cross-log overview is read-only. Counts cover every saved rule before
    // bounded display, and the source-log selection is refreshed before opening.
    // An old response cannot replace the current view after switching datasets.
    let allRequestId=0,allPreviousOffset=null,allNextOffset=null;
    async function refreshAllSignals(offset=0){
      const requestId=++allRequestId;
      allRefresh.disabled=true;allPrevious.disabled=true;allNext.disabled=true;
      allResults.replaceChildren();
      allStatus.textContent='Inspecting saved signal states across local logs…';
      try{
        const params=new URLSearchParams({status:allFilter.value,limit:'100',offset:String(offset)});
        const r=await getJSON('/v1/event-logs/signal-overview?'+params);
        if(generation!==state.generation||requestId!==allRequestId)return;
        const counts=r.counts||{};
        allPreviousOffset=r.previous_offset??null;allNextOffset=r.next_offset??null;
        const returned=Number(r.returned_rules||0),matched=Number(r.matched_rules||0),start=Number(r.offset||0);
        const pageText=returned&&(start||allNextOffset!==null)?(start+1)+'–'+(start+returned):String(returned);
        allStatus.textContent=Number(r.attention||0)+' signal states need attention across '+
          Number(r.datasets_scanned||0)+' saved logs ('+Number(r.datasets_with_rules||0)+
          ' with rules). '+Number(counts.unreviewed||0)+' unreviewed, '+
          Number(counts.stale||0)+' stale, '+Number(counts.not_evaluated||0)+
          ' unevaluated, '+Number(counts.unavailable||0)+' unavailable; '+
          Number(counts.snoozed||0)+' snoozed. Showing '+pageText+' of '+matched+' matching rules.'+
          (start&&!returned?' The list changed; use Previous or Refresh.':'')+
          ' Results can change between pages. No source refresh or automatic action.';
        const rows=(r.items||[]).map(item=>{
          const open=button('Open log','secondary');
          open.setAttribute('aria-label','Open dataset '+item.log_name+' to review signal '+item.name);
          open.onclick=async()=>{
            if(generation!==state.generation)return;
            if(!state.logs.some(log=>log.log_id===item.log_id)){
              allStatus.textContent='Dataset selection is stale; refresh the saved logs list.';
              return;
            }
            open.disabled=true;
            try{
              await refreshLogs(item.log_id);
              if(state.logId!==item.log_id)return;
              await analyse();
              document.getElementById('owgEventLogSignals')?.scrollIntoView({block:'start'});
            }catch(error){status(error.message||'Could not open that local dataset.');}
          };
          return [item.log_name,item.name,item.status.replace(/_/g,' '),
            item.evaluated_at||'Not checked',
            item.scheduled_check?'Scheduled':'On demand',
            open];
        });
        allResults.replaceChildren(rows.length
          ? table(['Saved log','Signal','Review status','Evaluated','Check mode',''],rows)
          : el('div','No saved signals match this filter.','muted'));
      }catch(error){
        if(generation===state.generation&&requestId===allRequestId){
          allPreviousOffset=null;allNextOffset=null;
          allResults.replaceChildren();
          allStatus.textContent=error.message||'Cannot inspect local saved signals.';
        }
      }finally{
        if(generation===state.generation&&requestId===allRequestId){allRefresh.disabled=false;allPrevious.disabled=allPreviousOffset===null;allNext.disabled=allNextOffset===null;}
      }
    }
    allFilter.onchange=()=>refreshAllSignals();
    allRefresh.onclick=()=>refreshAllSignals();
    allPrevious.onclick=()=>{if(allPreviousOffset!==null)refreshAllSignals(allPreviousOffset);};
    allNext.onclick=()=>{if(allNextOffset!==null)refreshAllSignals(allNextOffset);};
    // This journal describes state *changes*, not the current signal inbox.
    // An old request cannot repaint a different dataset after navigation.
    let timelineRequestId=0,timelineCursor='',timelineHistory=[],timelineNextCursor=null;
    async function refreshTimeline(before='',history=[]){
      const requestId=++timelineRequestId;
      timelineRefresh.disabled=true;timelinePrevious.disabled=true;timelineNext.disabled=true;timelineResults.replaceChildren();
      timelineStatus.textContent='Loading recorded signal changes across local logs…';
      try{
        const params=new URLSearchParams({transition:timelineFilter.value,limit:'100'});
        if(before)params.set('before',before);
        const r=await getJSON('/v1/event-logs/signal-timeline?'+params);
        if(generation!==state.generation||requestId!==timelineRequestId)return;
        timelineCursor=before;timelineHistory=history;timelineNextCursor=r.next_before??null;
        timelineStatus.textContent=Number(r.retained_total||0)+' retained changes ('+
          Number(r.counts?.triggered||0)+' triggered, '+Number(r.counts?.cleared||0)+
          ' cleared); showing '+Number(r.returned_transitions||0)+' of '+
          Number(r.matched_transitions||0)+' matching'+
          (before?' (older page)':'')+
          '. Results can change between pages. This is not a live notification feed.';
        const rows=(r.items||[]).map(item=>{
          const open=button('Open log','secondary');
          open.setAttribute('aria-label','Open dataset '+item.log_name+' for signal '+item.name);
          open.onclick=async()=>{
            if(generation!==state.generation)return;
            if(!state.logs.some(log=>log.log_id===item.log_id)){
              timelineStatus.textContent='Dataset selection is stale; refresh the saved logs list.';
              return;
            }
            open.disabled=true;
            try{
              await refreshLogs(item.log_id);
              if(state.logId!==item.log_id)return;
              await analyse();
              document.getElementById('owgEventLogSignals')?.scrollIntoView({block:'start'});
            }catch(error){status(error.message||'Could not open the source dataset.');}
          };
          return [item.evaluated_at||'—',item.log_name,item.name,
            item.transition==='triggered'?'Triggered':'Cleared',open];
        });
        timelineResults.replaceChildren(rows.length
          ?table(['Evaluated (UTC)','Saved log','Signal','Change',''],rows)
          :el('div','No saved signal changes match this filter.','muted'));
      }catch(error){
        if(generation===state.generation&&requestId===timelineRequestId){
          timelineCursor='';timelineHistory=[];timelineNextCursor=null;
          timelineResults.replaceChildren();
          timelineStatus.textContent=error.message||'Signal change timeline unavailable.';
        }
      }finally{
        if(generation===state.generation&&requestId===timelineRequestId){timelineRefresh.disabled=false;timelinePrevious.disabled=!timelineHistory.length;timelineNext.disabled=timelineNextCursor===null;}
      }
    }
    timelineFilter.onchange=()=>refreshTimeline();
    timelineRefresh.onclick=()=>refreshTimeline();
    timelinePrevious.onclick=()=>{if(timelineHistory.length)refreshTimeline(timelineHistory[timelineHistory.length-1],timelineHistory.slice(0,-1));};
    timelineNext.onclick=()=>{if(timelineNextCursor!==null)refreshTimeline(timelineNextCursor,[...timelineHistory,timelineCursor]);};
    reviewRefresh.onclick=()=>refreshInbox().catch(error=>{message.textContent=error.message;});
    async function refresh(){
      const r=await getJSON(base());if(generation!==state.generation)return;
      rules.replaceChildren(r.rules.length?table(['Signal','Rule',''],r.rules.map(rule=>{
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete signal '+rule.name);
        del.onclick=async()=>{try{await api(base()+'/'+encodeURIComponent(rule.rule_id)+'/delete',{method:'POST'});message.textContent='Signal deleted.';await refresh();}catch(error){message.textContent=error.message;}};
        const w=rule.when;const text=w.kind==='kpi'?w.metric.replace(/_/g,' ')+' '+w.operator+' '+w.threshold
          :w.kind==='conformance'?w.metric.replace(/_/g,' ')+' on '+w.net_name+' '+w.operator+' '+w.threshold+(w.recent_days?' (cases started in the last '+w.recent_days+' days)':'')
          :w.kind==='due_date'?(w.metric==='open_overdue_cases'?'open cases overdue for '+w.activity:'late share of '+w.activity)+' '+w.operator+' '+w.threshold+(w.due_attribute?' (due: '+w.due_attribute+')':' (due '+w.due_days+' days after start)')
          :w.kind==='compliance'?'compliance rule'+(w.rule&&w.rule.kind?': '+String(w.rule.kind).replace(/_/g,' '):'')
          :w.condition.replace(/_/g,' ')+(w.activity?' '+w.activity:'')+(w.seconds?' '+duration(w.seconds):'');
        return [rule.name,text,del];})):el('div','No saved signals yet.','muted'));
      evaluate.disabled=!r.rules.length;
      await refreshInbox();
      await refreshAllSignals();
      await refreshTimeline();
      await refreshHistory();
      await refreshDelivery().catch(error=>{deliveryStatus.textContent=error.message;});
    }
    const draftRule=()=>{
      const values={name:name.value,kind:kind.value,condition:condition.value,metric:metric.value,operator:operator.value,threshold:threshold.value,hours:hours.value,activity:activity.value,ends:ends.value};
      const body=signalRule(values);if(state.caseType)body.case_type=state.caseType;
      return body;
    };
    let previewTurn=0;
    const clearPreview=()=>{++previewTurn;previewStatus.textContent='';preview.disabled=false;};
    row.addEventListener('input',clearPreview);row.addEventListener('change',clearPreview);
    preview.onclick=async()=>{
      const ticket=++previewTurn;
      const current=()=>ticket===previewTurn&&generation===state.generation&&box.isConnected;
      preview.disabled=true;previewStatus.textContent='Checking draft signal…';
      try{
        const r=await (await api(base()+'/preview',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(draftRule())})).json();
        if(!current())return;
        const examples=signalEvidenceText(r.signal);
        const cases=Number(r.cohort_cases).toLocaleString()+(r.cohort_cases===1?' case.':' cases.');
        previewStatus.textContent=(r.signal.triggered?'Would trigger. ':'Would stay clear. ')+
          'Result: '+signalValueText(r.signal)+'. Checked '+cases+(examples?' Examples: '+examples+'.':'');
      }catch(error){if(current())previewStatus.textContent=error.message;}
      finally{if(current())preview.disabled=false;}
    };
    add.onclick=async()=>{
      try{
        const body=draftRule();
        await api(base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        message.textContent='Signal saved.';name.value='';clearPreview();await refresh();
      }catch(error){message.textContent=error.message;}
    };
    evaluate.onclick=async()=>{
      try{
        const r=await (await api(base()+'/evaluate',{method:'POST'})).json();if(generation!==state.generation)return;
        out.replaceChildren(
          el('div',r.triggered+' of '+r.signals.length+' signals hold now.','muted'),
          table(['Signal','Holds','Value or matching cases','New since last check','Example cases'],r.signals.map(sig=>[
            sig.name,sig.triggered?'yes':'no',
            signalValueText(sig),
            (sig.when.kind==='kpi'||sig.when.kind==='conformance'||sig.when.kind==='due_date')?(sig.changed_since_last_evaluation?'changed':'—'):newCaseChange(sig),
            signalEvidenceText(sig)])),
          el('div',r.interpretation,'muted'));
        await refresh();
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    refresh().catch(error=>{message.textContent=error.message;});
    // Signals saved from other sections (e.g. a Petri net conformance alert) appear without a reload.
    box.addEventListener('owg-signals-changed',()=>{refresh().catch(error=>{message.textContent=error.message;});});
    // Month-by-month scorecard of the value-based signals (read-only).
    const scoreRun=button('Scorecard by month','secondary');scoreRun.id='owgSignalScorecardRun';
    const scoreOut=el('div');scoreOut.id='owgSignalScorecard';scoreOut.setAttribute('role','status');scoreOut.setAttribute('aria-live','polite');
    box.append(scoreRun,scoreOut);
    scoreRun.onclick=async()=>{
      scoreRun.disabled=true;scoreOut.replaceChildren(el('div','Computing each month…','muted'));
      try{
        const r=await getJSON(base()+'/scorecard?months=6');if(generation!==state.generation)return;
        if(!r.rows.length){scoreOut.replaceChildren(el('div','No KPI, conformance or due-date signals to show by month.','muted'));return;}
        const fmt=(row,v)=>{if(v===null||v===undefined)return '—';const m=row.when.metric||'';
          return (m.endsWith('_rate')||m.endsWith('_share')||m==='alignment_fitness')?Math.round(v*1000)/10+'%':m.endsWith('_seconds')?duration(v):Number(v).toLocaleString();};
        scoreOut.replaceChildren(table(['Signal',...r.months],r.rows.map(row=>[row.name,...r.months.map(m=>{const cell=row.months.find(x=>x.month===m);
          return cell?fmt(row,cell.value)+(cell.holds===true?' (holds)':''):'—';})])),el('div',r.interpretation,'muted'));
      }catch(error){scoreOut.replaceChildren(el('div',error.message,'muted'));}
      finally{scoreRun.disabled=false;}};
    return box;
  }

  // Builds prediction settings from form values; throws a readable error.
  function batchingSection(inspection){
    const box=section('Batch processing');box.id='owgEventLogBatching';
    box.appendChild(el('div','Find work done in batches: events of one activity by the same resource that follow each other within a short gap. Batches are inferred from timestamps and are not proof that work was deliberately batched.','muted'));
    const gap=input('owgBatchGap','number');gap.min='0';gap.max='86400';gap.value='60';
    const size=input('owgBatchSize','number');size.min='2';size.max='1000';size.value='3';
    const byResource=input('owgBatchByResource','checkbox');byResource.checked=true;
    const run=button('Find batches','secondary');run.id='owgBatchRun';
    const out=el('div');out.id='owgBatchResults';out.setAttribute('role','status');
    const batches=el('div');batches.id='owgBatchList';
    const cases=el('div');cases.id='owgBatchCases';
    const resWrap=el('div');const resLabel=el('label',' Separate by resource');resLabel.htmlFor=byResource.id;resWrap.append(byResource,resLabel);
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Gap within a batch (seconds)',gap),field('Smallest batch',size),resWrap,run);
    box.append(row,out,batches,cases);
    const generation=state.generation,endpoint='/v1/event-logs/'+encodeURIComponent(state.logId)+'/batching';
    const number=n=>Number(n).toLocaleString();
    const utc=value=>value.replace('T',' ').replace('Z',' UTC');
    let batchTurn=0,caseTurn=0;
    const clearCases=()=>{++caseTurn;cases.replaceChildren();};
    const request=async(suffix,spec)=>(await api(endpoint+suffix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    function review(panel,titleText,spec,suffix,current,render,reset){
      const title=el('h4',titleText);title.tabIndex=-1;
      const status=el('div');status.setAttribute('role','status');
      const rows=el('div'),nav=el('div',null,'row');
      const previous=button('Previous page','secondary'),next=button('Next page','secondary');
      nav.append(previous,next);panel.replaceChildren(title,status,rows,nav);title.focus();
      const cursors=[null];let index=0,following=null;
      async function load(page){
        if(reset)reset();
        previous.disabled=next.disabled=true;status.textContent='Loading…';
        try{
          const r=await request(suffix,{...spec,...(cursors[page]!==null?{after:cursors[page]}:{})});
          if(!current())return;
          index=page;following=r.next_after;
          const view=render(r);status.textContent=view.message;rows.replaceChildren(view.table);
        }catch(error){if(current())status.textContent=error.message;}
        finally{if(current()){previous.disabled=index===0;next.disabled=following===null;}}
      }
      previous.onclick=()=>load(index-1);
      next.onclick=()=>{cursors[index+1]=following;load(index+1);};
      load(0);
    }
    function showCases(spec,batch){
      const turn=++caseTurn,current=()=>generation===state.generation&&turn===caseTurn;
      review(cases,'Cases in the '+spec.activity+' batch at '+utc(batch.started_at),{...spec,batch_id:batch.batch_id},
        '/batches/cases',current,r=>({
          message:number(r.total)+' matching '+(r.total===1?'case':'cases')+' across '+number(r.events)+' batch events. Showing '+number(r.cases.length)+' on this page.',
          table:table(['Case','Events in this batch','First batch event','Last batch event','Evidence'],r.cases.map(c=>{
            const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+c.case_id);
            open.onclick=()=>inspection.open(c.case_id);
            return [c.case_id,number(c.events),utc(c.started_at),utc(c.ended_at),open];
          }))
        }));
    }
    function showBatches(spec,activity){
      const turn=++batchTurn,current=()=>generation===state.generation&&turn===batchTurn;
      const selection={...spec,activity};
      review(batches,'Batches for '+activity,selection,'/batches',current,r=>({
        message:number(r.total)+' '+(r.total===1?'batch.':'batches.')+' Showing '+number(r.batches.length)+' on this page.',
        table:r.batches.length?table(['First event','Last event','Events','Timing','Evidence'],r.batches.map((b,i)=>{
          const inspect=button('Inspect cases','secondary');
          inspect.setAttribute('aria-label','Inspect cases for batch '+(i+1)+' on this page, starting '+utc(b.started_at));
          inspect.onclick=()=>showCases(selection,b);
          return [utc(b.started_at),utc(b.ended_at),number(b.events),b.simultaneous?'Simultaneous':'Sequential',inspect];
        })):el('div','No matching batches.','muted')
      }),clearCases);
    }
    run.onclick=async()=>{
      ++batchTurn;batches.replaceChildren();clearCases();run.disabled=true;
      out.replaceChildren(el('div','Finding batches…','muted'));
      try{
        const spec={gap_seconds:Number(gap.value),min_batch_size:Number(size.value),by_resource:byResource.checked};
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await request('',spec);
        if(generation!==state.generation)return;
        const shown=r.activities.filter(a=>a.batches>0);
        const rows=shown.map(a=>{
          const inspect=button('View batches','secondary');inspect.setAttribute('aria-label','View batches for '+a.activity);
          inspect.onclick=()=>showBatches(spec,a.activity);
          return [a.activity,pct(a.batched_share)+' of '+Number(a.events).toLocaleString(),String(a.batches)+(a.simultaneous_batches?' ('+a.simultaneous_batches+' at one instant)':''),
          a.batch_size?a.batch_size.median+' (90th '+a.batch_size.p90+', max '+a.batch_size.max+')':'—',
          duration(a.median_wait_before_batched_seconds)+' vs '+duration(a.median_wait_before_unbatched_seconds),
          a.simulation_batch_proposal?'size '+a.simulation_batch_proposal.size+(a.simulation_batch_proposal.max_wait_minutes?', wait up to '+a.simulation_batch_proposal.max_wait_minutes+' min':''):'—',inspect];
        });
        out.replaceChildren(
          el('div',Number(r.batched_events).toLocaleString()+' of '+Number(r.events).toLocaleString()+' events were in batches ('+r.grouping+(r.resources_recorded?'':'; no resources are recorded in this log')+').','muted'),
          shown.length?table(['Activity','Events in batches','Batches','Batch size (median)','Wait before: batched vs not','Simulation proposal','Evidence'],rows):el('div','No batches with these settings.','muted'),
          el('div',r.interpretation,'muted'));
      }catch(error){if(generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  // Which event attributes the log carries: coverage, type, case- or event-level, frequent values.
  function attributeProfileSection(inspection){
    const box=section('Attributes');box.id='owgAttributeProfile';
    box.appendChild(el('div','See which attributes the events carry, how often, whether each stays fixed within a case, and the most frequent values with the median duration of their cases. Use it to choose filters and comparisons.','muted'));
    const run=button('Profile attributes','secondary');run.id='owgAttributeProfileRun';
    const out=el('div');out.id='owgAttributeProfileResults';out.setAttribute('role','status');
    box.append(run,out);
    const generation=state.generation;
    const num=v=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:2});
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const spec={top_values:5};
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/attribute-profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        if(!r.attributes.length){out.replaceChildren(el('div','The events carry no attributes. Keep extra columns as attributes when importing a CSV.','muted'));return;}
        const review=(attribute,selection,label,text,count)=>{
          if(!count)return el('span',text);
          const open=button(text,'secondary');
          open.setAttribute('aria-label','Review cases '+label);
          open.onclick=()=>inspection.attributeMatch(r.cohort,attribute,selection,'Cases '+label+'.');
          return open;
        };
        const rows=r.attributes.map(a=>{
          const present=el('div',null,'row');
          present.appendChild(review(a.attribute,{},'with '+a.attribute,pct(a.case_coverage)+' of cases',a.cases));
          const missing=r.cases-a.cases;
          if(missing)present.appendChild(review(a.attribute,{match:'missing'},'missing '+a.attribute,
            num(missing)+' missing',missing));
          const level=a.cases_where_value_changes
            ?review(a.attribute,{match:'varying'},'where '+a.attribute+' changes',
              'Changes within '+num(a.cases_where_value_changes)+' cases',a.cases_where_value_changes)
            :'Fixed per case';
          const values=el('div');
          if(a.numeric)values.appendChild(el('div',num(a.numeric.min)+' – '+num(a.numeric.max)+', median '+num(a.numeric.median)));
          if(!a.unique_per_event&&a.top_values.length){
            const details=document.createElement('details');
            const list=el('ul');list.style.cssText='padding-left:20px;margin:4px 0';
            a.top_values.forEach(v=>{
              const item=el('li');
              item.append(review(a.attribute,{value:v.value},'with '+a.attribute+' = '+v.value,
                v.value+' ('+pct(v.case_share)+', '+duration(v.median_case_duration_seconds)+')',v.cases));
              list.appendChild(item);
            });
            details.append(el('summary','Frequent values'),list);values.appendChild(details);
          }
          return [a.attribute,present,a.kind==='numeric'?'number':'text',level,
            num(a.distinct_values)+(a.unique_per_event?' (unique per event)':''),values];
        });
        out.replaceChildren(
          el('div',Number(r.cases).toLocaleString()+' cases; median case time '+duration(r.median_case_duration_seconds)+'. Frequent values show the share of cases and their median case time.','muted'),
          table(['Attribute','Present in','Type','Level','Distinct values','Range or frequent values'],rows),
          ...(r.attributes_truncated?[el('div','Only the 100 most common attributes are shown.','muted')]:[]),
          el('div',r.interpretation,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  function predictionSpec(values){
    const ends=String(values.ends||'').split(',').map(x=>x.trim()).filter(Boolean);
    if(!ends.length)throw new Error('List the activities that end a case.');
    const spec={end_activities:ends,limit:50};
    if(values.outcome==='duration_over'){const h=Number(values.hours);if(!(h>0))throw new Error('Enter the hours for the outcome.');spec.outcome={kind:'duration_over',seconds:Math.round(h*3600)};}
    if(values.outcome==='contains_activity'){if(!values.activity)throw new Error('Choose the outcome activity.');spec.outcome={kind:'contains_activity',activity:values.activity};}
    if(values.estimator==='survival')spec.estimator='survival';
    else if(values.estimator&&values.estimator!=='completed_cases')throw new Error('Choose what to learn from.');
    return spec;
  }

  function predictionCaseButton(inspection,caseId){
    const open=button('Open case','secondary');open.setAttribute('aria-label','Open case '+caseId);
    open.onclick=()=>inspection.open(caseId);
    return open;
  }

  function predictionCaseStatus(result){
    return {
      not_in_selection:'This case key does not match any case in the current selection.',
      completed:'This case has reached one of the selected end activities.',
      unsupported:'This open case has too little comparable history for an estimate.'
    }[result.case_selection];
  }

  // What usually happens next in open cases, learned from completed cases and checked by a temporal backtest.
  function nextActivitySection(activityNames,inspection){
    const box=section('Next activity in open cases');box.id='owgNextActivity';
    box.appendChild(el('div','Estimate what usually comes next in each open case, from completed cases that had the same recent activities. Check the backtest before relying on it.','muted'));
    const ends=input('owgNextEnds','text','for example Pay, Close');ends.maxLength=2000;
    const listId='owgNextEndsList';const list=document.createElement('datalist');list.id=listId;activityNames.slice(0,500).forEach(a=>{const o=document.createElement('option');o.value=a;list.appendChild(o);});ends.setAttribute('list',listId);
    const history=select('owgNextHistory',[['1','1 activity'],['2','2 activities'],['3','3 activities']]);history.value='2';
    const exact=input('owgNextCaseKey','text');exact.maxLength=200;
    const run=button('Predict next activities','secondary');run.id='owgNextRun';
    const out=el('div');out.id='owgNextResults';out.setAttribute('role','status');
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities that end a case (comma-separated)',ends),field('Recent history to match',history),field('Exact case key (optional)',exact),run);
    box.append(row,list,out);
    const generation=state.generation;
    const name=n=>n.case_ends?'(case ends)':n.activity;
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const endList=ends.value.split(',').map(x=>x.trim()).filter(Boolean);
        if(!endList.length)throw new Error('List the activities that end a case.');
        const spec={end_activities:endList,history:Number(history.value),limit:50};
        if(exact.value)spec.case_id=exact.value;
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/next-activity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const b=r.backtest;
        const quality=b.available?'Backtest on '+Number(b.test_cases).toLocaleString()+' cases that ended after '+String(b.cutoff_case_ended_at).slice(0,10)+': the first guess was right '+pct(b.top1_accuracy)+' of the time and one of the top three '+pct(b.top3_accuracy)+', against '+pct(b.baseline_current_activity_only)+' when looking at the current activity only and '+pct(b.baseline_most_frequent_next)+' when always guessing the most common step.':'No backtest: '+b.reason+'.';
        const rows=r.predictions.map(p=>[p.case_id,p.last_activity,p.next.map(n=>name(n)+' '+pct(n.probability)).join(', '),p.history_used?'last '+p.history_used+' '+(p.history_used===1?'activity':'activities'):'all completed cases',Number(p.support).toLocaleString(),predictionCaseButton(inspection,p.case_id)]);
        const expected=r.expected_next_across_open_cases.map(e=>[name(e),e.expected_cases.toLocaleString()]);
        const heading=spec.case_id?'Selected case':rows.length<r.open_cases?'Most recently active open cases (showing '+rows.length+' of '+Number(r.open_cases).toLocaleString()+')':'Most recently active open cases';
        out.replaceChildren(
          el('div',Number(r.open_cases).toLocaleString()+' open and '+Number(r.completed_cases).toLocaleString()+' completed cases. '+quality),
          el('h4','Expected next steps across open cases'),table(['Next step','Expected cases'],expected),
          el('h4',heading),
          rows.length?table(['Case','Last activity','Likely next','Matched on','Completed-case steps behind it','Evidence'],rows):el('div',predictionCaseStatus(r)||'No open cases.','muted'),
          el('div',r.interpretation,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    return box;
  }

  // Remaining-time cell: from the last event, or (censoring-aware) from the end of the log; a withheld estimate shows its lower bound.
  const remainingText=p=>p.estimate_withheld?'more than '+duration(p.remaining_at_least_seconds)+' (withheld)':duration(p.median_remaining_seconds)+(p.remaining_measured_from==='end_of_log'?' from now':'');
  const asOfBacktestText=b=>{
    const head='As-of backtest at '+String(b.cutoff).slice(0,10)+': '+Number(b.cases_in_progress_at_cutoff).toLocaleString()+' cases were in progress';
    if(!b.available)return head+'. No score: '+b.reason+'.';
    const c=b.completed_cases_estimator;
    return head+'; '+Number(b.scored_cases).toLocaleString()+' have finished since. Typical error '+duration(b.mean_absolute_error_seconds)+' vs '+duration(c.mean_absolute_error_seconds)+
      ' learning from completed cases only; the range held the actual time '+pct(b.p10_p90_coverage)+' of the time vs '+pct(c.p10_p90_coverage)+' (target 80%). '+
      Number(b.still_open_unscored_cases).toLocaleString()+' are still open and not scored'+(b.still_open_already_beyond_p90?', '+Number(b.still_open_already_beyond_p90).toLocaleString()+' already past their 90th percentile':'')+'. '+b.scoring_note;
  };

  function predictionSection(activityNames,inspection){
    const box=section('Predict open cases');box.id='owgEventLogPredictions';
    box.appendChild(el('div','Estimate how much longer open cases will take, and how likely an outcome is, from cases that were at the same point. Learning from completed cases only is biased toward short times when long cases are still open; including still-open cases corrects for that and takes into account how long each case has already waited. A backtest shows how accurate these estimates have been. Estimates are not commitments, and nobody is rated.','muted'));
    const ends=input('owgPredictEnds','text','Close, Cancel');
    const outcome=select('owgPredictOutcome',[['','Remaining time only'],['duration_over','Case takes longer than…'],['contains_activity','Case will include activity…']]);
    const hours=input('owgPredictHours','number');hours.min='0';hours.step='any';
    const activity=select('owgPredictActivity',activityNames.slice(0,500).map(x=>[x,x]));
    const estimator=select('owgPredictEstimator',[['completed_cases','Completed cases only'],['survival','Completed and still-open cases']]);
    const exact=input('owgPredictCaseKey','text');exact.maxLength=200;
    const run=button('Predict');run.id='owgPredictRun';
    const watch=button('Monitor quality over time','secondary');watch.id='owgPredictMonitor';
    const f={hours:field('Hours',hours),activity:field('Activity',activity)};
    const sync=()=>{show(f.hours,outcome.value==='duration_over');show(f.activity,outcome.value==='contains_activity');};
    outcome.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities that end a case',ends),field('Also predict',outcome),f.hours,f.activity,field('Learn from',estimator),field('Exact case key (optional)',exact),run,watch);
    const out=el('div');out.id='owgPredictResults';out.setAttribute('role','status');
    const monitorOut=el('div');monitorOut.id='owgPredictMonitoring';monitorOut.setAttribute('role','status');
    const snapButton=button('Save a snapshot to score later','secondary');snapButton.id='owgPredictSnapshot';
    const snapOut=el('div');snapOut.id='owgPredictSnapshots';
    const snapStatus=el('div',null,'muted');snapStatus.id='owgPredictSnapshotStatus';snapStatus.setAttribute('role','status');snapStatus.setAttribute('aria-live','polite');
    box.append(row,out,monitorOut,el('h4','Prediction snapshots'),
      el('div','A snapshot freezes today\u2019s estimates for every open case (completed-cases learning). After you append newer events, score it to see how the estimates held up against what actually happened.','muted'),
      snapButton,snapStatus,snapOut);
    const generation=state.generation;
    const snapBase=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/prediction-snapshots';
    const listSnapshots=async()=>{
      const r=await (await api(snapBase())).json();
      if(generation!==state.generation)return;
      if(!r.snapshots.length){snapOut.replaceChildren(el('div','No snapshots yet.','muted'));return;}
      const list=el('ul');
      r.snapshots.forEach(sn=>{
        const li=el('li',(sn.as_of||'').slice(0,16).replace('T',' ')+' · '+Number(sn.predicted_cases).toLocaleString()+' open cases frozen ');
        const score=button('Score now','secondary');score.setAttribute('aria-label','Score snapshot from '+(sn.as_of||''));
        const remove=button('Delete','secondary');remove.setAttribute('aria-label','Delete snapshot from '+(sn.as_of||''));
        const result=el('div');result.className='owgSnapshotScore';
        score.onclick=async()=>{
          try{
            const e=await (await api(snapBase()+'/'+encodeURIComponent(sn.snapshot_id)+'/evaluation')).json();
            const parts=[el('div',Number(e.finished_cases).toLocaleString()+' finished, '+Number(e.still_open_cases).toLocaleString()+' still open'+(e.cases_no_longer_in_log?', '+e.cases_no_longer_in_log+' no longer in the log':'')+'; '+duration(e.seconds_since_snapshot)+' of newer events.')];
            const t=e.remaining_time;
            if(t)parts.push(el('div','Typical error '+duration(t.mean_absolute_error_seconds)+' (median '+duration(t.median_absolute_error_seconds)+') vs '+(t.naive_mean_absolute_error_seconds===null?'—':duration(t.naive_mean_absolute_error_seconds))+' for one estimate for every case. Range covered '+pct(t.coverage_10_90)+' ['+pct(t.coverage_10_90_ci95[0])+', '+pct(t.coverage_10_90_ci95[1])+'] against 80% nominal. '+t.still_open_known_misses+' still-open cases are already past their range.'));
            else parts.push(el('div','No frozen case has finished yet; append newer events and score again.','muted'));
            if(e.outcome)parts.push(el('div','Outcome: Brier '+Number(e.outcome.brier_score).toFixed(3)+(e.outcome.base_rate_brier_score===null?'':' vs '+Number(e.outcome.base_rate_brier_score).toFixed(3)+' for the base rate')+'; happened in '+pct(e.outcome.observed_rate)+' against '+pct(e.outcome.mean_predicted)+' predicted.'));
            parts.push(el('div',e.interpretation,'muted'));result.replaceChildren(...parts);
          }catch(error){result.replaceChildren(el('div',error.message,'muted'));}
        };
        remove.onclick=async()=>{try{await api(snapBase()+'/'+encodeURIComponent(sn.snapshot_id)+'/delete',{method:'POST'});await listSnapshots();}catch(error){snapStatus.textContent=error.message;}};
        li.append(score,remove,result);list.appendChild(li);
      });
      snapOut.replaceChildren(list);
    };
    snapButton.onclick=async()=>{
      try{
        const spec={...predictionSpec({ends:ends.value,outcome:outcome.value,hours:hours.value,activity:activity.value,estimator:'completed_cases'})};
        delete spec.limit;if(state.caseType)spec.case_type=state.caseType;
        const sn=await (await api(snapBase(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        snapStatus.textContent='Saved a snapshot of '+Number(sn.predicted_cases).toLocaleString()+' open cases'+(sn.unsupported_open_cases?' ('+sn.unsupported_open_cases+' without enough comparable history)':'')+'.';
        await listSnapshots();
      }catch(error){snapStatus.textContent=error.message;}
    };
    listSnapshots().catch(()=>{});
    run.onclick=async()=>{
      run.disabled=true;
      try{
        const spec=predictionSpec({ends:ends.value,outcome:outcome.value,hours:hours.value,activity:activity.value,estimator:estimator.value});
        if(exact.value)spec.case_id=exact.value;
        if(state.caseType)spec.case_type=state.caseType;
        if(filterOn())spec.cohort=globalCohort();
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/predictions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const b=r.backtest;
        const quality=b.design==='as_of_replay'?asOfBacktestText(b):b.available?'Backtest on '+Number(b.scored_prefixes).toLocaleString()+' later steps: typical error '+duration(b.mean_absolute_error_seconds)+' vs '+duration(b.naive_mean_absolute_error_seconds)+' for a naive guess; the range held the actual time '+pct(b.p10_p90_coverage)+' of the time (target 80%).'+(b.brier_score!==undefined?' Outcome Brier score '+b.brier_score.toFixed(3)+' vs '+b.base_rate_brier_score.toFixed(3)+' for the base rate.':''):'No backtest: '+b.reason+'.';
        const headers=['Case','Latest activity','Open for','Expected remaining','Likely range',...(spec.outcome?['Outcome likelihood']:[]),'Based on','Evidence'];
        const rows=r.predictions.map(p=>[p.case_id,p.last_activity,duration(p.age_seconds),remainingText(p),
          (p.remaining_p10_seconds===null?'unknown':duration(p.remaining_p10_seconds))+' – '+(p.remaining_p90_seconds===null?'unknown':duration(p.remaining_p90_seconds)),
          ...(spec.outcome?[p.outcome_probability_ci95?pct(p.outcome_probability)+' ['+pct(p.outcome_probability_ci95[0])+', '+pct(p.outcome_probability_ci95[1])+']':'—']:[]),
          Number(p.comparable_cases).toLocaleString()+' cases ('+({same_activity_and_step:'same step',same_activity:'same activity',all_completed_cases:'all cases',all_cases:'all cases'})[p.basis]+')'+
            (p.comparable_cases_still_open?', '+Number(p.comparable_cases_still_open).toLocaleString()+' still open':''),
          predictionCaseButton(inspection,p.case_id)]);
        const selection=predictionCaseStatus(r);
        out.replaceChildren(el('div',Number(r.open_cases).toLocaleString()+' open cases; '+Number(r.predicted_open_cases).toLocaleString()+' have enough comparable history'+
            (r.withheld_estimates?'; '+Number(r.withheld_estimates).toLocaleString()+' estimates withheld because too many comparable cases are still open':'')+'.','muted'),
          el('div',quality,'muted'),
          ...(r.predictions_truncated?[el('div','Showing '+rows.length+' estimates. Enter an exact case key to look up another open case.','muted')]:[]),
          rows.length?table(headers,rows):el('div',selection||'No estimates are available for the selected open cases.','muted'),
          el('div',r.interpretation,'muted'));
      }catch(error){if(generation===state.generation)out.replaceChildren(el('div',error.message,'muted'));}
      finally{run.disabled=false;}
    };
    let watchTurn=0;
    watch.onclick=async()=>{
      const t=++watchTurn;watch.disabled=true;monitorOut.replaceChildren(el('div','Replaying past windows…','muted'));
      try{
        // Monitoring scores the completed-cases estimator, whatever the live setting.
        const spec={...predictionSpec({ends:ends.value,outcome:outcome.value,hours:hours.value,activity:activity.value,estimator:'completed_cases'}),windows:6};
        delete spec.estimator;if(state.caseType)spec.case_type=state.caseType;
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/predictions/monitoring',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation||t!==watchTurn)return;
        const v=monitoringRows(r);
        const caseQuality=r.outcome_case_reliability;
        const caseDetail=!spec.outcome?[]:caseQuality?[
          el('h4','Outcome calibration by independent completed cases'),
          el('p','Latest supported window #'+caseQuality.window+'. Each completed case is counted once, with its scored probabilities averaged before assigning a risk band. '+
            caseQuality.total_distinct_scored_cases+' independent completed cases, compared with '+
            caseQuality.total_scored_prediction_points+' prediction points. At least '+
            caseQuality.minimum_distinct_completed_cases_per_displayed_bin+' independent cases are needed per displayed band; '+
            caseQuality.withheld_low_support_bins+' low-support band(s) withheld. No case or resource identities are shown.','muted'),
          caseQuality.bins.length?table(
            ['Predicted risk band','Completed cases','Prediction points','Predicted risk','Outcome observed (95% case interval)','Brier loss per case'],
            caseQuality.bins.map(b=>[
              pct(b.predicted_from)+'–'+pct(b.predicted_to_exclusive===null?b.predicted_to_inclusive:b.predicted_to_exclusive),
              Number(b.completed_cases).toLocaleString(),Number(b.scored_prediction_points).toLocaleString(),
              pct(b.mean_case_averaged_predicted_risk),
              pct(b.observed_outcome_share)+' ['+pct(b.observed_outcome_ci95[0])+', '+pct(b.observed_outcome_ci95[1])+']',
              Number(b.case_averaged_brier_score).toFixed(3)
            ]))
          :el('p','Every nonempty risk band has fewer than the minimum independent completed cases. No case-level calibration table is shown.','muted'),
          el('p',caseQuality.interpretation+' Outcomes come from the selected event log, not independently verified source receipts. No risk model is recalibrated automatically.','muted')
        ]:[el('p','No window has enough labeled completed cases for outcome calibration.','muted')];
        const openCoverage=r.open_case_coverage;
        const openCoverageDetail=openCoverage?[
          el('h4','Still-open cases excluded from monitoring'),
          el('p',Number(openCoverage.still_open_cases_not_scored).toLocaleString()+
            ' of '+Number(openCoverage.selected_cases_total).toLocaleString()+
            ' cases ('+pct(openCoverage.still_open_share)+') in this selected cohort have not reached a selected end activity. '+
            'They were not scored as completed cases. '+
            Number(openCoverage.open_cases_with_exact_state_support).toLocaleString()+
            ' have at least '+Number(openCoverage.minimum_completed_examples_for_exact_state).toLocaleString()+
            ' completed examples matching their latest activity and step; '+
            Number(openCoverage.open_cases_without_exact_state_support).toLocaleString()+
            ' do not and require broader fallback or abstention. '+
            Number(openCoverage.open_cases_with_no_exact_completed_prefix).toLocaleString()+
            ' have no exact completed example.','muted'),
          openCoverage.open_states.length?table(
            ['Latest activity','Step','Still-open cases','Matching completed cases','Exact state supported'],
            openCoverage.open_states.map(s=>[
              s.latest_activity,String(s.step),Number(s.open_cases).toLocaleString(),
              Number(s.exact_completed_prefix_cases).toLocaleString(),
              s.exact_state_supported?'Yes':'No'
            ]))
            :el('p','No unfinished cases in this selected cohort.','muted'),
          ...(openCoverage.states_truncated?[el('p','Showing only the eight largest open-case states; counts above include every state.','muted')]:[]),
          el('p',openCoverage.interpretation,'muted')
        ]:[];
        const activityQuality=r.activity_quality;
        const activityDetail=activityQuality?[
          el('h4','Where prediction errors occur'),
          el('p','Latest supported window #'+activityQuality.window+'. The table averages repeated predictions within each completed case first, then across cases. At least '+activityQuality.minimum_distinct_completed_cases+' completed cases per activity; '+activityQuality.withheld_low_support_states+' states withheld for low support and '+activityQuality.withheld_over_limit_states+' beyond the display limit. A case can contribute to several states. No employee rankings or causal claims.','muted'),
          activityQuality.activities.length?table(
            ['Activity state','Completed cases','Prediction points','Typical error','Naive error','Range covered (case-level)',...(spec.outcome?['Outcome Brier','Base-rate Brier']:[])],
            activityQuality.activities.map(a=>[
              a.activity,Number(a.scored_cases).toLocaleString(),Number(a.scored_prefixes).toLocaleString(),
              duration(a.mean_absolute_error_seconds),duration(a.naive_mean_absolute_error_seconds),
              pct(a.p10_p90_case_averaged_coverage)+' ['+pct(a.p10_p90_coverage_ci95[0])+', '+pct(a.p10_p90_coverage_ci95[1])+']',
              ...(spec.outcome?[
                a.case_averaged_brier_score===null?'—':Number(a.case_averaged_brier_score).toFixed(3),
                a.case_averaged_base_rate_brier_score===null?'—':Number(a.case_averaged_base_rate_brier_score).toFixed(3)]:[])]))
            :el('p','No activity states have the minimum completed-case support.','muted'),
          el('p',activityQuality.interpretation,'muted')
        ]:[el('p','No historical prediction window has enough comparable completed cases for an activity breakdown.','muted')];
        monitorOut.replaceChildren(el('h4','Prediction quality over time'),el('div',v.summary,'note'),
          table(['Window','Cases scored','Typical error','Naive error','Range held the time',...(spec.outcome?['Brier score']:[])],v.rows.map(row=>spec.outcome?row:row.slice(0,5))),
          ...activityDetail,
          ...openCoverageDetail,
          ...caseDetail,
          ...(v.proposal?[el('div',v.proposal,'muted')]:[]),el('div',r.interpretation,'muted'));
      }catch(error){if(t===watchTurn)monitorOut.replaceChildren(el('div',error.message,'muted'));}
      finally{if(t===watchTurn)watch.disabled=false;}
    };
    return box;
  }

  // Plain reading of prediction monitoring: one row per window, flags and the proposal in words.
  function monitoringRows(r){
    const flagText={ranges_too_narrow:'ranges have become too narrow',ranges_too_wide:'ranges have become too wide',error_rising:'typical error has risen'};
    const rows=r.windows.map(w=>w.scored?['#'+w.window+' to '+String(w.to).slice(0,10),Number(w.scored_cases).toLocaleString(),duration(w.mean_absolute_error_seconds),duration(w.naive_mean_absolute_error_seconds),
      pct(w.p10_p90_coverage)+' ['+pct(w.p10_p90_coverage_ci95[0])+', '+pct(w.p10_p90_coverage_ci95[1])+']'+(w.coverage_flag?' ⚠':''),w.brier_score===undefined?'—':Number(w.brier_score).toFixed(3)]
      :['#'+w.window+' to '+String(w.to).slice(0,10),'—','—','—',w.reason,'—']);
    const flags=r.flags.map(f=>flagText[f.flag]||f.flag);
    const p=r.recalibration_proposal;
    return {rows,
      summary:flags.length?'In the latest window '+flags.join(' and ')+'. Review the predictions before relying on them.':'No drift flagged in the latest window.',
      proposal:p?'Proposal, not applied: scale each range around its median by '+Number(p.range_scaling_factor).toFixed(2)+'×. That would have held '+pct(p.latest_window_coverage_after)+' of the latest window (now '+pct(p.latest_window_coverage_before)+')'+
        (p.previous_window_coverage_after===null?'.':' and '+pct(p.previous_window_coverage_after)+' of the window before.'):null};
  }

  window.__owgEventLogModel={simBatch,simPriorities,objectModelArcs,objectModelText,visitRows,simUncertainty,uncertaintyRows,objectRule,attributeChangeSummary,measuredMinutes,validationText,flatteningSummary,simArrivals,simPool,simCalendar,caseMetric,refactorMetricGraph,savedMetricBundle,stageComparisonMetrics,activityComparisonMetrics,activityShareMetrics,metricText,reasonText,followSpec,followText,mergeActiveCohort,tokenAt,dotLayout,treeOutline,predictionSpec,complianceRule,analysisSpec,analysisDraft,signalRule,newCaseChange,detectFormat,csvHeader,parsePaths,duration,cohortSpec,toCSV,parseWeightedPaths,formatWeightedPaths,packMapping,packGuess,packReportRows,monitoringRows,impactSpec,impactRows};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
