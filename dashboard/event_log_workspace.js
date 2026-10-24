/* Local event-log workspace: import CSV/XES/OCEL logs, analyse, compare with a reference model, export. */
(() => {
  'use strict';
  const MODEL_FORMAT='openworkgraph.reference-model.v1';
  const state={logs:[],logId:'',caseType:'',generation:0,refresh:0};

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
  function detectFormat(name){
    const lower=String(name||'').toLowerCase();
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

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Event-log request failed ('+response.status+').');}
    return response;
  }
  const getJSON=async path=>(await api(path)).json();
  const logPath=(suffix,params)=>{const q=new URLSearchParams();for(const [key,value] of Object.entries(params||{})){if(Array.isArray(value))value.forEach(v=>q.append(key,v));else q.set(key,value);}if(state.caseType)q.set('case_type',state.caseType);const s=q.toString();return '/v1/event-logs/'+encodeURIComponent(state.logId)+'/'+suffix+(s?'?'+s:'');};

  function table(headers,rows){
    const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');
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

    const file=input('owgEventLogFile','file');file.accept='.csv,.tsv,.txt,.xes,.json,.jsonocel,.xmlocel,.xml';
    const format=select('owgEventLogFormat',[['csv','CSV'],['xes','XES'],['ocel','OCEL 2.0 JSON'],['ocelxml','OCEL 2.0 XML']]);
    const name=input('owgEventLogName','text','Log name');name.maxLength=120;
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('File',file),field('Format',format),field('Name',name));
    const mapping=el('div',null,'row');mapping.id='owgEventLogMapping';mapping.style.alignItems='end';
    const cols={case:select('owgEventLogCase',[]),activity:select('owgEventLogActivity',[]),time:select('owgEventLogTime',[]),resource:select('owgEventLogResource',[])};
    const delimiter=select('owgEventLogDelimiter',[[',','Comma'],[';','Semicolon'],['t','Tab'],['|','Pipe']]);
    mapping.append(field('Case column',cols.case),field('Activity column',cols.activity),field('Timestamp column (with timezone)',cols.time),field('Resource column',cols.resource),field('Delimiter',delimiter));
    const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    const consent=input('owgEventLogConsent','checkbox');consentLabel.append(consent,document.createTextNode(' Store this log on this computer until I delete it'));
    const importButton=button('Import log');importButton.id='owgEventLogImport';importButton.disabled=true;
    const statusLine=el('div',null,'muted');statusLine.id='owgEventLogStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const appendButton=button('Add file to selected log','secondary');appendButton.id='owgEventLogAppend';appendButton.disabled=true;
    appendButton.title='Adds events from a newer export to the stored log chosen below; events it already holds are skipped.';
    const importRow=el('div',null,'row');importRow.append(importButton,appendButton);
    card.append(row,mapping,consentLabel,importRow,statusLine);

    const pick=select('owgEventLogPick',[]);const caseType=select('owgEventLogCaseType',[]);
    const exportFormat=select('owgEventLogExportFormat',[['csv','CSV'],['xes','XES'],['ocel','OCEL 2.0 JSON'],['ocelxml','OCEL 2.0 XML']]);
    const analyze=button('Analyse');analyze.id='owgEventLogAnalyze';
    const exportButton=button('Export','secondary');exportButton.id='owgEventLogExport';
    const remove=button('Delete log','secondary');remove.id='owgEventLogDelete';
    const manage=el('div',null,'row');manage.id='owgEventLogManage';manage.style.cssText='align-items:end;margin-top:12px';show(manage,false);
    manage.append(field('Stored log',pick),field('Case notion',caseType),analyze,field('Export as',exportFormat),exportButton,remove);
    const results=el('div');results.id='owgEventLogResults';
    card.append(manage,results);

    const browse=document.getElementById('owgProcessCSV');
    if(browse)browse.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    const refreshImport=()=>{const ready=file.files&&file.files[0]&&consent.checked;importButton.disabled=!(ready&&name.value.trim());appendButton.disabled=!(ready&&state.logId);show(mapping,format.value==='csv');};
    async function readColumns(){
      const chosen=file.files&&file.files[0];if(!chosen||format.value!=='csv')return;
      const sep=delimiter.value==='t'?'\t':delimiter.value;
      const columns=csvHeader(await chosen.slice(0,65536).text(),sep);
      const options=columns.map(c=>[c,c]);
      setOptions(cols.case,options);setOptions(cols.activity,options);setOptions(cols.time,options);setOptions(cols.resource,[['','(none)'],...options]);
      cols.case.value=guess(columns,['case','order','ticket','id'])||columns[0]||'';
      cols.activity.value=guess(columns,['activity','event','step','action'])||columns[1]||'';
      cols.time.value=guess(columns,['time','date','ts'])||columns[2]||'';
      cols.resource.value=guess(columns,['resource','user','who','agent','owner']);
    }
    file.onchange=async()=>{const chosen=file.files&&file.files[0];if(chosen){format.value=detectFormat(chosen.name);if(!name.value)name.value=chosen.name.replace(/\.[^.]+$/,'').slice(0,120);if(chosen.name.toLowerCase().endsWith('.tsv'))delimiter.value='t';}refreshImport();try{await readColumns();}catch(_){status('Could not read the column names.');}};
    format.onchange=()=>{refreshImport();readColumns().catch(()=>{});};
    delimiter.onchange=()=>readColumns().catch(()=>{});
    consent.onchange=refreshImport;name.oninput=refreshImport;
    importButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      const q=new URLSearchParams({format:format.value,name:name.value.trim()});
      if(format.value==='csv'){q.set('case_column',cols.case.value);q.set('activity_column',cols.activity.value);q.set('timestamp_column',cols.time.value);q.set('resource_column',cols.resource.value);q.set('delimiter',delimiter.value);}
      importButton.disabled=true;status('Importing '+chosen.name+'…');
      try{
        const log=await (await api('/v1/event-logs/import?'+q,{method:'POST',body:chosen,headers:{'Content-Type':'application/octet-stream','X-OWG-Local-Storage-Confirmed':'true'}})).json();
        status('Imported '+Number(log.events).toLocaleString()+' events'+(log.rejected_events?' ('+Number(log.rejected_events).toLocaleString()+' rejected: missing case, activity or timezone-qualified time)':'')+'.');
        consent.checked=false;file.value='';name.value='';
        await refreshLogs(log.log_id);
      }catch(error){status(error.message);}finally{refreshImport();}
    };
    appendButton.onclick=async()=>{
      const chosen=file.files&&file.files[0];const target=state.logs.find(l=>l.log_id===state.logId);if(!chosen||!consent.checked||!target)return;
      const q=new URLSearchParams({format:format.value});
      if(format.value==='csv'){q.set('case_column',cols.case.value);q.set('activity_column',cols.activity.value);q.set('timestamp_column',cols.time.value);q.set('resource_column',cols.resource.value);q.set('delimiter',delimiter.value);}
      appendButton.disabled=true;status('Adding '+chosen.name+' to '+target.name+'…');
      try{
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(target.log_id)+'/append?'+q,{method:'POST',body:chosen,headers:{'Content-Type':'application/octet-stream','X-OWG-Local-Storage-Confirmed':'true'}})).json();
        consent.checked=false;file.value='';
        await refreshLogs(target.log_id);
        status('Added '+Number(r.added_events).toLocaleString()+' new events to '+target.name+'; skipped '+Number(r.duplicate_events_skipped).toLocaleString()+' already in the log'+(r.rejected_events?' and rejected '+Number(r.rejected_events).toLocaleString():'')+'.');
      }catch(error){status(error.message);}finally{refreshImport();}
    };
    pick.onchange=()=>{selectLog(pick.value);refreshImport();};
    caseType.onchange=()=>{state.generation++;state.caseType=caseType.value;results.replaceChildren();};
    analyze.onclick=()=>analyse().catch(error=>status(error.message));
    exportButton.onclick=async()=>{
      try{
        status('Preparing export…');
        const response=await api(logPath('export',{format:exportFormat.value}));
        const url=URL.createObjectURL(await response.blob());
        const link=el('a');link.href=url;link.download=state.logId+'.'+({ocel:'jsonocel',ocelxml:'xmlocel'}[exportFormat.value]||exportFormat.value);
        document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        status('Export ready.');
      }catch(error){status(error.message);}
    };
    remove.onclick=async()=>{
      const chosen=state.logs.find(l=>l.log_id===state.logId);if(!chosen)return;
      if(!window.confirm('Delete the event log "'+chosen.name+'" from this computer? This cannot be undone.'))return;
      try{state.generation++;results.replaceChildren();await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/delete',{method:'POST'});await refreshLogs('');status('Event log deleted.');}
      catch(error){status(error.message);}
    };
    refreshImport();
    refreshLogs('').catch(()=>{});
  }

  async function refreshLogs(selectId){
    const refresh=++state.refresh;
    const data=await getJSON('/v1/event-logs');
    if(refresh!==state.refresh)return;
    state.logs=Array.isArray(data.logs)?data.logs:[];
    const pick=document.getElementById('owgEventLogPick');
    setOptions(pick,state.logs.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']));
    show(document.getElementById('owgEventLogManage'),state.logs.length>0);
    const next=state.logs.some(l=>l.log_id===selectId)?selectId:(state.logs[0]?state.logs[0].log_id:'');
    if(next){pick.value=next;await selectLog(next);}else state.logId='';
  }

  async function selectLog(logId){
    const generation=++state.generation;state.logId=logId;state.caseType='';
    document.getElementById('owgEventLogResults').replaceChildren();
    const types=await getJSON('/v1/event-logs/'+encodeURIComponent(logId)+'/object-types');
    if(generation!==state.generation)return;
    const options=(types.object_types||[]).map(t=>[t.object_type,t.object_type+' ('+Number(t.objects).toLocaleString()+')']);
    const caseType=document.getElementById('owgEventLogCaseType');
    setOptions(caseType,options);
    state.caseType=options.some(o=>o[0]==='case')?'case':(options[0]?options[0][0]:'');
    caseType.value=state.caseType;
  }

  async function analyse(){
    const generation=++state.generation;
    const results=document.getElementById('owgEventLogResults');results.replaceChildren();
    status('Analysing…');
    const [summary,variants,activities]=await Promise.all([getJSON(logPath('summary')),getJSON(logPath('variants',{limit:10})),getJSON(logPath('activities'))]);
    if(generation!==state.generation)return;
    const names=(activities.activities||[]).map(a=>a.activity);
    const inspection=caseInspector(names);
    const overview=section('Overview');
    const d=summary.case_duration_seconds||{};
    overview.appendChild(table(['Cases','Events','Activities','Median case time','90th percentile'],[[Number(summary.cases).toLocaleString(),Number(summary.events).toLocaleString(),summary.activities,duration(d.median),duration(d.p90)]]));
    results.appendChild(overview);

    const variantBox=section('Most common variants');
    variantBox.appendChild(table(['Path','Cases','Share','Median time','Evidence'],(variants.variants||[]).map(v=>{const open=button('Inspect cases','secondary');open.onclick=()=>inspection.path(v.path);return [v.path.join(' → '),Number(v.cases).toLocaleString(),pct(v.share),duration(v.median_duration_seconds),open];})));
    variantBox.appendChild(el('div',Number(variants.distinct_variants).toLocaleString()+' distinct variants.','muted'));
    results.appendChild(variantBox);
    results.appendChild(objectMapSection());
    results.appendChild(processMapSection());

    const activityBox=section('Activities');
    activityBox.appendChild(table(['Activity','Events','Cases','Cases repeating activity','Evidence'],(activities.activities||[]).map(a=>{const open=button('Inspect cases','secondary');open.setAttribute('aria-label','Inspect cases containing '+a.activity);open.onclick=()=>inspection.activity(a.activity);return [a.activity,a.events,a.cases,a.repeated_in_cases,open];})));
    results.append(activityBox,inspection.node);
    results.appendChild(kpiSection(names));
    results.appendChild(benchmarkSection(names));
    results.appendChild(savedGraphSection(names,inspection));
    results.appendChild(simulationSection());
    results.appendChild(conformanceSection((variants.variants||[])[0]));
    results.appendChild(discoverySection());
    results.appendChild(driftSection());
    results.appendChild(rootCauseSection((activities.activities||[]).map(a=>a.activity)));
    results.appendChild(organizationSection());
    results.appendChild(analysisSection());
    results.appendChild(signalSection((activities.activities||[]).map(a=>a.activity)));
    results.appendChild(predictionSection((activities.activities||[]).map(a=>a.activity)));
    results.appendChild(complianceSection((activities.activities||[]).map(a=>a.activity)));
    status('');
  }

  function caseInspector(activityNames,options={}){
    const prefix=options.prefix||'owgEventLogCases';
    const box=section('Case evidence');box.id=prefix;
    const generation=state.generation;
    const current=()=>box.isConnected&&generation===state.generation;
    const queryPath=suffix=>{
      const url=new URL(logPath(suffix),window.location.origin);
      if(options.caseType!==undefined)url.searchParams.set('case_type',options.caseType);
      return url.pathname+url.search;
    };
    const filters=cohortFields(prefix+'Filter','Select complete cases',activityNames);
    const browse=button('Browse cases','secondary');browse.id=prefix+'Browse';
    const first=button('First case page','secondary'),next=button('Next case page','secondary');
    first.disabled=true;next.disabled=true;
    const note=el('div','Case keys and labels are supplied by this log. They do not verify business identity, people or outcomes.','muted');
    const chosen=el('div','All paths.','muted');chosen.id=prefix+'Path';
    const reset=button('All paths','secondary');reset.onclick=()=>{path=null;transition=null;chosen.textContent='All paths.';};
    const message=el('div');message.id=prefix+'Status';message.setAttribute('role','status');
    const list=el('div');list.id=prefix+'List';
    const detail=el('div');detail.id=prefix==='owgEventLogCases'?'owgEventLogCaseEvents':prefix+'Events';
    const controls=el('div',null,'row');controls.append(browse,reset,first,next);
    box.append(note,filters.node,chosen,controls,message,list,detail);
    let path=null,transition=null,spec=null,nextAfter=null,listing=0,opening=0;
    async function load(after){
      const ticket=++listing;first.disabled=true;next.disabled=true;browse.disabled=true;
      message.textContent='Loading cases…';
      try{
        const payload={...spec};if(after!==null)payload.after=after;
        const result=await (await api(queryPath('cases/query'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
        if(!current()||ticket!==listing)return;
        nextAfter=result.next_after;
        message.textContent=Number(result.selected_cases).toLocaleString()+' matching cases. Showing '+result.cases.length+' on this page.';
        list.replaceChildren(table(['Supplied case key','Events','Elapsed time','Equal-timestamp events'],result.cases.map(c=>{
          const open=button(c.case_id,'secondary');open.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%';
          open.setAttribute('aria-label','Open case '+c.case_id);open.onclick=()=>openCase(c.case_id);
          return [open,c.events,duration(c.elapsed_seconds),c.timestamp_ties];
        })));
        first.disabled=after===null;next.disabled=nextAfter===null;
      }catch(error){if(current()&&ticket===listing){message.textContent=error.message;list.replaceChildren();}}
      finally{if(current()&&ticket===listing)browse.disabled=false;}
    }
    function start(){
      try{spec={cohort:filters.read(),limit:30};if(path)spec.path=path;if(transition)spec.transition=transition;detail.replaceChildren();opening++;load(null);}
      catch(error){message.textContent=error.message;}
    }
    browse.onclick=start;first.onclick=()=>load(null);next.onclick=()=>load(nextAfter);
    async function openCase(caseId){
      const ticket=++opening;
      const heading=el('h4','Case '+caseId);heading.tabIndex=-1;
      const progress=el('div','Loading events…');progress.setAttribute('role','status');
      const rows=el('div'),more=button('More case events','secondary');more.id=prefix==='owgEventLogCases'?'owgEventLogCaseEventsMore':prefix+'EventsMore';more.disabled=true;
      detail.replaceChildren(heading,progress,rows,more);
      heading.focus();let offset=0,shown=0;
      async function page(){
        more.disabled=true;
        try{
          const result=await (await api(queryPath('case-events/query'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:caseId,offset,limit:100})})).json();
          if(!current()||ticket!==opening)return;
          shown+=result.events.length;offset=result.next_offset;
          progress.textContent='Showing '+shown+' of '+result.total_events+' events. '+result.timestamp_ties+' equal-timestamp events. '+result.ordering;
          rows.appendChild(table(['Activity','Timestamp (UTC)','Resource label','Event ID'],result.events.map(e=>[e.activity,e.timestamp,e.resource||'(missing)',e.event_id])));
          more.disabled=offset===null;show(more,offset!==null);
        }catch(error){if(current()&&ticket===opening)progress.textContent=error.message;}
      }
      more.onclick=page;await page();
    }
    return {node:box,path(value){
      if(value.length>500){message.textContent='This path exceeds the 500-step case selector limit. Browse by activity or cohort instead.';box.scrollIntoView({block:'nearest'});return;}
      path=value.slice();transition=null;chosen.textContent='Exact path: '+JSON.stringify(path);start();box.scrollIntoView({block:'nearest'});
    },activity(value){
      const picker=document.getElementById(prefix+'FilterActivity');
      if(!Array.from(picker.options).some(option=>option.value===value))picker.add(new Option(value,value));
      path=null;transition=null;chosen.textContent='All paths.';picker.value=value;start();box.scrollIntoView({block:'nearest'});
    },transition(value){
      path=null;transition=value.slice();document.getElementById(prefix+'FilterActivity').value='';
      chosen.textContent='Adjacent transition: '+JSON.stringify(transition)+'. Timestamp ties do not verify order.';
      start();box.scrollIntoView({block:'nearest'});
    }};
  }

  // Cohort fields; dates are whole UTC days on the case's first event.
  function cohortSpec(values){
    const spec={};
    const day=(v,label)=>{if(!v)return '';if(!/^\d{4}-\d{2}-\d{2}$/.test(v))throw new Error(label+' must be a date.');return v+'T00:00:00Z';};
    if(values.label&&values.label.trim())spec.label=values.label.trim().slice(0,80);
    const from=day(values.from,'Started from'),before=day(values.before,'Started before');
    if(from)spec.started_from=from;if(before)spec.started_before=before;
    if(from&&before&&from>=before)throw new Error('The cohort period is empty.');
    for(const key of ['resource','activity'])if(values[key]&&values[key].trim())spec[key]=values[key];
    if(values.attribute&&values.attribute.trim()){if(!/^[^=]+=.*/.test(values.attribute.trim()))throw new Error('Attribute must look like name=value.');spec.attribute=values.attribute.trim();}
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
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Name',label),field('Started from (UTC)',from),field('Started before (UTC)',before),field('Resource',resource),field('Contains activity',activity),field('Case attribute',attribute));
    box.appendChild(row);
    return {node:box,read:()=>cohortSpec({label:label.value,from:from.value,before:before.value,resource:resource.value,activity:activity.value,attribute:attribute.value})};
  }
  function toCSV(rows){
    const cell=v=>{const t=v===null||v===undefined?'':String(v);const safe=/^[=+\-@\t\r]/.test(t)?"'"+t:t;return /[",\n\r]/.test(safe)?'"'+safe.replace(/"/g,'""')+'"':safe;};
    return rows.map(r=>r.map(cell).join(',')).join('\r\n')+'\r\n';
  }
  function download(name,text,type){
    const url=URL.createObjectURL(new Blob([text],{type}));
    const link=el('a');link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function exportButtons(prefix,getResult,csvRows){
    const json=button('Download JSON','secondary');json.id=prefix+'Json';json.disabled=true;
    const csv=button('Download CSV','secondary');csv.id=prefix+'Csv';csv.disabled=true;
    json.onclick=()=>{const r=getResult();if(r)download(prefix+'.json',JSON.stringify(r,null,2),'application/json');};
    csv.onclick=()=>{const r=getResult();if(r)download(prefix+'.csv',toCSV(csvRows(r)),'text/csv');};
    const row=el('div',null,'row');row.append(json,csv);
    return {node:row,enable:on=>{json.disabled=!on;csv.disabled=!on;}};
  }

  function objectMapSection(){
    const box=section('Object-centric map');
    const generation=state.generation;
    const current=()=>box.isConnected&&generation===state.generation;
    box.appendChild(el('div','Each object type has its own map: every object (an order, an item, a ticket…) is one lifecycle. Links count objects that share events; they are not verified relationships.','muted'));
    const pick=select('owgEventLogObjectType',[]);
    const showMap=button('Show map','secondary');showMap.id='owgEventLogObjectShow';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Object type',pick),showMap);
    const out=el('div');out.id='owgEventLogObjectMap';
    const shared=el('div');
    box.append(row,out,shared);
    let graph=null;
    const render=()=>{
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
      parts.push(inspection.node);out.replaceChildren(...parts);
    };
    showMap.onclick=render;pick.onchange=render;
    getJSON(logPath('object-map').replace(/\?.*$/,'')).then(g=>{
      if(!current())return;
      graph=g;setOptions(pick,(g.object_types||[]).map(t=>[t.object_type,t.object_type+' ('+Number(t.objects).toLocaleString()+')']));
      if(state.caseType)pick.value=state.caseType;
      const touching=g.activities_touching_several_types||[];
      shared.replaceChildren(el('p','Showing '+g.object_types.length+' of '+g.object_type_count+' object types.','muted'),...(touching.length?[el('h4','Activities shared by several object types'),table(['Activity','Object types','Events'],touching.slice(0,15).map(a=>[a.activity,a.object_types.join(', '),Number(a.events).toLocaleString()]))]:[]));
      render();
    }).catch(error=>{if(current())out.replaceChildren(el('div',error.message,'muted'));});
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

  function benchmarkSection(activityNames){
    const box=section('Compare cohorts');
    box.appendChild(el('div','Compare two groups of cases, for example two periods, teams or case types. Differences describe the cohorts; they do not say why they differ.','muted'));
    const first=cohortFields('owgEventLogCohortA','First cohort',activityNames),second=cohortFields('owgEventLogCohortB','Second cohort',activityNames);
    const sla=input('owgEventLogBenchSla','number','optional');sla.min='0';sla.step='any';
    const run=button('Compare','secondary');run.id='owgEventLogBenchRun';
    const row=el('div',null,'row');row.style.cssText='align-items:end;margin-top:8px';row.append(field('SLA (hours)',sla),run);
    let last=null;
    const ci=d=>(d.difference>0?'+':'')+(d.difference*100).toFixed(1)+' pts ['+(d.ci95[0]*100).toFixed(1)+', '+(d.ci95[1]*100).toFixed(1)+']';
    const exports=exportButtons('owgEventLogBenchmark',()=>last,r=>[
      ['activity','share_in_first','share_in_second','difference','ci95_low','ci95_high'],
      ...r.activity_presence.map(a=>[a.activity,a.share_in_first,a.share_in_second,a.difference,a.ci95[0],a.ci95[1]])]);
    const out=el('div');box.append(first.node,second.node,row,out,exports.node);
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
    run.onclick=async()=>{
      if(!pinned)return;
      const reference={...pinned},ticket=++comparing;run.disabled=true;last=null;exports.enable(false);out.replaceChildren(el('div','Comparing cases…'));
      try{
        const spec={...reference,cohort:filters.read()};
        const result=await (await api(logPath('process-conformance'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(!current()||ticket!==comparing)return;
        last=result;exports.enable(true);
        const reasons={'trace_length_limit':'Case histories exceed the event limit','variant_limit':'Variant selection limit','alignment_work_budget':'Alignment work limit'};
        out.replaceChildren(
          el('h4',result.process_reference.title+' · revision '+result.process_reference.revision),
          table(['Cases','Structurally matched','Structural deviations','Unknown','Analysis coverage','Cases with timestamp ties'],[[result.cases,result.structural_match_cases,result.structural_deviation_cases,result.unknown_cases,pct(result.analysis_coverage),result.ordering_uncertain_cases]]),
          ...graphMetricSummary(result),
          el('div','Applied cohort: '+JSON.stringify(result.cohort)+'. Timestamp ties can overlap any result. Order is timestamp then event ID for display; it is not verified business order.','muted'),
          table(['Unknown reason','Cases'],Object.entries(result.unknown_case_reasons).filter(([,n])=>n>0).map(([reason,n])=>[reasons[reason]||reason,n])),
          el('div','Showing '+result.displayed_variants.length+' variant results'+(result.variant_results_truncated?'; additional variants are not shown.':'.'),'muted'),
          table(['Observed path','Cases','Structural result','Minimum activity edits','Possible incomplete log prefix','Evidence'],result.displayed_variants.map(v=>{
            const inspect=button('Inspect cases','secondary');inspect.onclick=()=>inspection.path(v.path);
            return [v.path.slice(0,40).join(' → ')+(v.path.length>40?' …':''),v.cases,v.comparison_complete?(v.exact_structural_match?'Match':'Deviation'):'Unknown',v.minimum_activity_edit_count===null?'—':v.minimum_activity_edit_count,v.could_be_incomplete_log_prefix===null?'Unknown':(v.could_be_incomplete_log_prefix?'Yes':'No'),inspect];
          })),
          el('div',result.cases===0?'No cases match this selection; no verdict was established.':result.comparison_complete?'Every selected case was structurally compared. Business rules and outcomes were not checked.':'Some cases remain unknown. They are neither matches nor deviations. Business rules and outcomes were not checked.','muted'));
      }catch(error){if(current()&&ticket===comparing)out.replaceChildren(el('div',error.message));}
      finally{if(current()&&ticket===comparing)run.disabled=false;}
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

  function simulationSection(){
    const box=section('What-if simulation');
    box.appendChild(el('div','Simulate cases flowing through the process with the times and staffing you set. Start from values measured in this log, change them, and compare. Results are model outputs under the listed assumptions, not forecasts.','muted'));
    const load=button('Load measured values','secondary');load.id='owgEventLogSimLoad';
    const form=el('div');form.id='owgEventLogSimForm';
    const out=el('div');box.append(load,form,out);
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
        top.append(field('Scenario name',label),field('Cases arriving per hour',arrivals),field('Activity times',dist),field('Cases to simulate',cases));
        const paths=document.createElement('textarea');paths.id='owgEventLogSimPaths';paths.rows=4;paths.style.width='100%';paths.value=formatWeightedPaths(cal.paths||[]);
        const rows=[];
        const body=el('tbody');
        const addRow=(name,p)=>{
          const i=rows.length;
          const minutes=input('owgEventLogSimMinutes'+i,'number');minutes.min='0';minutes.step='any';minutes.value=String(p.minutes);minutes.setAttribute('aria-label','Minutes for '+name);
          const people=input('owgEventLogSimResources'+i,'number');people.min='1';people.step='1';people.value=String(p.resources);people.setAttribute('aria-label','Resources for '+name);
          rows.push({name,minutes,people});
          const tr=el('tr');const a=el('td'),b=el('td');a.appendChild(minutes);b.appendChild(people);tr.append(el('td',name),a,b);body.appendChild(tr);
        };
        Object.entries(cal.activities||{}).forEach(([name,p])=>addRow(name,p));
        const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');['Activity','Minutes per case','Resources'].forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
        t.append(head,body);wrap.appendChild(t);
        const newName=input('owgEventLogSimNewName','text','New activity');newName.maxLength=200;
        const add=button('Add activity','secondary');add.id='owgEventLogSimAdd';
        add.onclick=()=>{const name=newName.value.trim();if(!name||rows.some(r=>r.name===name))return;addRow(name,{minutes:0,resources:1});newName.value='';};
        const addRowNode=el('div',null,'row');addRowNode.style.alignItems='end';addRowNode.append(field('Activity not in the log',newName),add);
        const run=button('Run simulation','secondary');run.id='owgEventLogSimRun';
        form.replaceChildren(top,field('Routing: weighted lines or JSON for exact labels and weights',paths),wrap,addRowNode,
          el('div','Measured times are median gaps before each activity and include waiting. '+coverage,'muted'),run);
        run.onclick=async()=>{
          try{
            const activities=Object.create(null);
            for(const r of rows){const m=Number(r.minutes.value),n=Number(r.people.value);if(!(m>=0)||!(n>=1)||!Number.isInteger(n))throw new Error('Check the minutes and resources for '+r.name+'.');activities[r.name]={minutes:m,resources:n};}
            const scenario={format:'openworkgraph.simulation-scenario.v1',label:label.value.trim(),paths:parseWeightedPaths(paths.value),activities,distribution:dist.value,cases:Math.round(Number(cases.value)||2000)};
            if(arrivals.value!=='')scenario.arrivals_per_hour=Number(arrivals.value);
            run.disabled=true;out.replaceChildren(el('div','Simulating…','muted'));
            const r=await (await api(logPath('simulate'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scenario)})).json();
            const line=(name,x)=>{const c=x.cycle_time_seconds||{};return [name,duration(c.p50),duration(c.p90),duration(c.mean),x.completed_per_day===null?'—':Number(x.completed_per_day).toFixed(1)];};
            const change=r.median_cycle_time_change_seconds;
            out.replaceChildren(
              ...(r.result.unstable?[el('div','Warning: at least one activity has more work arriving than its resources can handle, so its queue keeps growing. Times below depend on how many cases you simulate.','note')]:[]),
              table(['Run','Median cycle time','90th percentile','Mean','Completed per day'],[line('Simulated baseline',r.baseline),line(r.scenario.label||'Scenario',r.result)]),
              el('div',change===null?'':'Median cycle time change: '+(change>=0?'+':'−')+duration(Math.abs(change))+'.','muted'),
              table(['Activity','Resources','Minutes','Utilization','Mean wait','90th percentile wait'],r.result.activities.map(a=>[a.activity,a.resources,a.minutes,pct(a.utilization),duration(a.mean_wait_seconds),duration(a.p90_wait_seconds)])),
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
    if(topVariant&&topVariant.path.length<=100)model.value=JSON.stringify({format:MODEL_FORMAT,paths:[topVariant.path]});
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

  function rootCauseSection(activityNames){
    const box=section('What is associated with an outcome');
    const outcome=select('owgEventLogOutcome',[['duration_over','Case takes longer than'],['rework','Case has rework'],['contains_activity','Case contains activity'],['ends_with','Case ends with activity']]);
    const hours=input('owgEventLogOutcomeHours','number','hours');hours.min='0';hours.step='any';
    const activity=select('owgEventLogOutcomeActivity',activityNames.slice(0,500).map(a=>[a,a]));
    const run=button('Find associations','secondary');run.id='owgEventLogRootCauseRun';
    const hoursField=field('Hours',hours),activityField=field('Activity',activity);
    const sync=()=>{show(hoursField,outcome.value==='duration_over');show(activityField,['contains_activity','ends_with'].includes(outcome.value));};
    outcome.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Outcome',outcome),hoursField,activityField,run);
    const out=el('div');box.append(row,out);
    run.onclick=async()=>{
      try{
        const params={outcome:outcome.value};
        if(outcome.value==='duration_over'){const h=Number(hours.value);if(!(h>0))throw new Error('Enter the number of hours.');params.threshold_seconds=String(Math.round(h*3600));}
        if(!activityField.hidden)params.activity=activity.value;
        const r=await getJSON(logPath('root-cause',params));
        const rows=(r.associations||[]).map(a=>[a.feature.kind+': '+a.feature.value,Number(a.cases_with_feature).toLocaleString(),pct(a.outcome_rate_with),pct(a.outcome_rate_without),(a.risk_difference>0?'+':'')+(a.risk_difference*100).toFixed(1)+' pts ['+(a.ci95[0]*100).toFixed(1)+', '+(a.ci95[1]*100).toFixed(1)+']',a.holds_after_bonferroni?'yes':'no']);
        out.replaceChildren(
          el('div','Outcome in '+pct(r.outcome_rate)+' of '+Number(r.cases).toLocaleString()+' cases; '+Number(r.features_tested)+' features tested.','muted'),
          table(['Feature','Cases with it','Outcome with','Outcome without','Difference (95% CI)','Holds after correction'],rows),
          el('div',r.note||'These are associations in this log, not causes. A step can be a consequence of the outcome rather than its reason.','muted'));
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
        const draft=button('Review as catalog draft');draft.id='owgDiscoveryDraft';
        draft.onclick=()=>{
          try{
            if(!window.owgProcessCatalog?.draftDiscovered)throw new Error('Process catalog unavailable. Reopen the local dashboard.');
            if(window.owgProcessCatalog.draftDiscovered({model:r.process_model,logName:(r.log&&r.log.name)||''}))statusLine.textContent='Opened in the process catalog as an unsaved draft.';
          }catch(error){statusLine.textContent=error.message;}
        };
        out.appendChild(draft);
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}
    };
    return box;
  }

  function driftSection(){
    const box=section('Changes over time');box.id='owgConceptDrift';
    box.appendChild(el('div','Finds when the way cases run changed: cases are grouped by when they started, and each window\'s paths are compared with the next. A change point means the difference is larger than random splits of the same cases give. It does not say why, and a change in how events are recorded looks the same.','muted'));
    const windowKind=select('owgConceptDriftWindow',[['month','Month'],['week','Week'],['cases','Fixed number of cases']]);
    const perWindow=input('owgConceptDriftCases','number');perWindow.min='20';perWindow.step='1';perWindow.value='500';
    const casesField=field('Cases per window',perWindow);show(casesField,false);
    windowKind.onchange=()=>show(casesField,windowKind.value==='cases');
    const run=button('Find changes','secondary');run.id='owgConceptDriftRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Window',windowKind),casesField,run);
    const statusLine=el('div',null,'muted');statusLine.id='owgConceptDriftStatus';statusLine.setAttribute('role','status');statusLine.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgConceptDriftResults';
    box.append(row,statusLine,out);
    run.onclick=async()=>{
      const generation=state.generation;
      try{
        run.disabled=true;out.replaceChildren();statusLine.textContent='Comparing windows…';
        const params={window:windowKind.value};
        if(windowKind.value==='cases'){const n=Math.round(Number(perWindow.value));if(!(n>=20))throw new Error('Use at least 20 cases per window.');params.window_cases=String(n);params.min_cases=String(Math.max(10,Math.min(30,n)));}
        const r=await getJSON(logPath('drift',params));
        if(generation!==state.generation)return;
        statusLine.textContent=r.change_points.length+' change point'+(r.change_points.length===1?'':'s')+' among '+r.boundaries_tested+' window boundaries tested'+(r.boundaries_skipped_small_windows?' ('+r.boundaries_skipped_small_windows+' skipped because a window had fewer than '+r.settings.min_cases+' cases)':'')+'.';
        const byAfter=Object.fromEntries(r.boundaries.map(b=>[b.after,b]));
        const p=v=>v<0.0001?'< 0.0001':v.toFixed(4);
        out.appendChild(table(['Window','Cases','Median case time','Change from previous window'],r.windows.map(w=>{const b=byAfter[w.window];
          return [w.window,Number(w.cases).toLocaleString(),duration(w.median_case_duration_seconds),b?(b.change_point?'Change point · ':'')+'distance '+b.distance.toFixed(3)+', p '+p(b.p_value):'—'];})));
        r.change_points.forEach(c=>{
          out.appendChild(el('h4','Paths that moved most: '+c.before+' → '+c.after));
          out.appendChild(table(['Path','Cases with path before','After'],c.paths_that_moved.map(m=>[m.path,pct(m.case_share_before),pct(m.case_share_after)])));
        });
        out.appendChild(el('div',r.interpretation,'muted'));
      }catch(error){if(generation===state.generation)statusLine.textContent=error.message;}
      finally{run.disabled=false;}
    };
    return box;
  }

  function organizationSection(){
    const box=section('Who hands work to whom');box.id='owgEventLogOrganization';
    box.appendChild(el('div','Handovers and shared cases between the resources in this log. Resources are shown as Resource 01, 02… unless you choose to show names. No speed, waiting time or performance score is calculated for anyone.','muted'));
    const namesLabel=el('label');namesLabel.style.cssText='display:flex;gap:6px;align-items:center';
    const names=input('owgEventLogOrgNames','checkbox');namesLabel.append(names,document.createTextNode(' Show resource names from the log'));
    const run=button('Show handovers','secondary');run.id='owgEventLogOrgRun';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(namesLabel,run);
    const out=el('div');out.id='owgEventLogOrgResults';out.setAttribute('role','status');
    box.append(row,out);
    run.onclick=async()=>{
      const generation=state.generation;
      try{
        out.replaceChildren(el('div','Reading resources…','muted'));
        const r=await getJSON(logPath('organization',{names:names.checked?'shown':'pseudonymous'}));
        if(generation!==state.generation)return;
        if(!r.resources.length){out.replaceChildren(el('div','This log has no resource column values to analyse.','muted'));return;}
        const m=r.activity_resource_matrix;
        out.replaceChildren(
          el('div',Number(r.coverage.events_with_resource).toLocaleString()+' of '+Number(r.coverage.events).toLocaleString()+' events name a resource; '+Number(r.cases_with_handover).toLocaleString()+' cases contain a handover.'+(r.resources_truncated?' Less active resources are grouped as “Other resources”.':''),'muted'),
          table(['From','To','Handovers','Cases'],r.handover_of_work.slice(0,25).map(h=>[h.from,h.to,Number(h.handovers).toLocaleString(),Number(h.cases).toLocaleString()])),
          table(['Resources','Shared cases'],r.working_together.slice(0,25).map(w=>[w.resources.join(' + '),Number(w.shared_cases).toLocaleString()])),
          table(['Activity',...m.resources.slice(0,10)],m.activities.slice(0,15).map((a,i)=>[a,...m.events[i].slice(0,10).map(n=>n?Number(n).toLocaleString():'')])),
          el('div',r.interpretation+(r.working_together_excluded_cases?' '+r.working_together_excluded_cases+' cases with very many resources are left out of shared-case pairs.':''),'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
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
    const measures=[values.measure1,values.measure2].filter(Boolean).map(m=>{const [fn,of]=m.split(':');return of?{fn,of}:{fn};});
    const spec={level:values.level,dimensions:dims,measures,limit:Number(values.limit)||100};
    if(values.level==='cases'){
      const where={};const h=Number(values.hours);
      if(values.hours!==''&&values.hours!==undefined){if(!(h>=0))throw new Error('Enter hours as a number.');where.duration_over_seconds=Math.round(h*3600);}
      if(values.endsWith)where.ends_with=values.endsWith;
      if(Object.keys(where).length)spec.where=where;
    }
    return spec;
  }

  function analysisSection(){
    const box=section('Build an analysis');box.id='owgEventLogAnalysis';
    box.appendChild(el('div','Group cases or events by up to two dimensions and choose what to measure. The choices are fixed, so no query text is ever run directly.','muted'));
    const level=select('owgAnalysisLevel',[['cases','Cases'],['events','Events']]);
    const dim1=select('owgAnalysisDim1',[]),dim2=select('owgAnalysisDim2',[]);
    const attr1=input('owgAnalysisAttr1','text','attribute name'),attr2=input('owgAnalysisAttr2','text','attribute name');
    const measure1=select('owgAnalysisMeasure1',[]),measure2=select('owgAnalysisMeasure2',[]);
    const hours=input('owgAnalysisHours','number','any');hours.min='0';hours.step='any';
    const endsWith=input('owgAnalysisEnds','text','any activity');
    const run=button('Run analysis');run.id='owgAnalysisRun';
    const name=input('owgAnalysisName','text','Analysis name');name.maxLength=120;
    const save=button('Save analysis','secondary');save.id='owgAnalysisSave';save.disabled=true;
    const f={attr1:field('First attribute',attr1),attr2:field('Second attribute',attr2),hours:field('Only cases longer than (hours)',hours),ends:field('Only cases ending with',endsWith)};
    const sync=()=>{
      const keep=[dim1.value,dim2.value,measure1.value,measure2.value];
      setOptions(dim1,ANALYSIS_DIMENSIONS[level.value]);setOptions(dim2,ANALYSIS_DIMENSIONS[level.value]);
      setOptions(measure1,ANALYSIS_MEASURES[level.value]);setOptions(measure2,[['','(none)'],...ANALYSIS_MEASURES[level.value]]);
      [dim1,dim2,measure1,measure2].forEach((node,i)=>{if([...node.options].some(o=>o.value===keep[i]))node.value=keep[i];});
      show(f.attr1,dim1.value==='attribute');show(f.attr2,dim2.value==='attribute');show(f.hours,level.value==='cases');show(f.ends,level.value==='cases');
    };
    level.onchange=dim1.onchange=dim2.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('Analyse',level),field('Group by',dim1),f.attr1,field('Then by',dim2),f.attr2,field('Measure',measure1),field('Also measure',measure2),f.hours,f.ends,run);
    const saveRow=el('div',null,'row');saveRow.style.alignItems='end';saveRow.append(field('Name',name),save);
    const message=el('div',null,'muted');message.id='owgAnalysisStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgAnalysisResults';
    const savedList=el('div');savedList.id='owgAnalysisSaved';
    let last=null,lastSpec=null;
    const exports=exportButtons('owgAnalysis',()=>last,r=>[r.columns,...r.rows]);
    box.append(row,message,out,exports.node,saveRow,savedList);
    const generation=state.generation;
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId);
    const cell=(column,value)=>value===null||value===undefined?'—':/duration_seconds$/.test(column)?duration(value):column==='share'?pct(value):typeof value==='number'?Number(value).toLocaleString():String(value);
    async function execute(spec){
      if(state.caseType)spec.case_type=state.caseType;
      const r=await (await api(base()+'/analysis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
      if(generation!==state.generation)return;
      last=r;lastSpec=spec;exports.enable(true);save.disabled=false;
      out.replaceChildren(el('div',Number(r.matching.cases).toLocaleString()+' matching cases'+(r.rows_truncated?'; showing the first '+r.rows.length+' rows.':'.'),'muted'),
        table(r.columns,r.rows.map(row=>row.map((v,i)=>cell(r.columns[i],v)))));
    }
    async function refresh(){
      const r=await getJSON(base()+'/analyses');if(generation!==state.generation)return;
      savedList.replaceChildren(r.analyses.length?table(['Saved analysis','',''],r.analyses.map(a=>{
        const open=button('Run','secondary');open.setAttribute('aria-label','Run saved analysis '+a.name);
        open.onclick=()=>execute({...a.spec}).catch(error=>{message.textContent=error.message;});
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete saved analysis '+a.name);
        del.onclick=async()=>{try{await api(base()+'/analyses/'+encodeURIComponent(a.analysis_id)+'/delete',{method:'POST'});message.textContent='Analysis deleted.';await refresh();}catch(error){message.textContent=error.message;}};
        return [a.name,open,del];})):el('div','No saved analyses yet.','muted'));
    }
    run.onclick=async()=>{
      try{message.textContent='';await execute(analysisSpec({level:level.value,dim1:dim1.value,attr1:attr1.value,dim2:dim2.value,attr2:attr2.value,measure1:measure1.value,measure2:measure2.value,hours:hours.value,endsWith:endsWith.value.trim()}));}
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
    return box;
  }

  const COMPLIANCE_TEMPLATES=[['response','Every A is eventually followed by B'],['precedence','Every B needs an earlier A'],['response_within','Every A is followed by B within N hours'],['segregation_of_duties','A and B are done by different people'],['not_coexistence','A and B never happen in the same case'],['existence','A happens a set number of times']];
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

  function complianceSection(activityNames){
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
    box.append(row,pending,run,message,out);
    const rules=[];const generation=state.generation;
    const label=r=>({response:'every '+r.a+' is eventually followed by '+r.b,precedence:'every '+r.b+' needs an earlier '+r.a,response_within:'every '+r.a+' is followed by '+r.b+' within '+duration(r.seconds),segregation_of_duties:r.a+' and '+r.b+' are done by different people',not_coexistence:r.a+' and '+r.b+' never happen together',existence:r.a+' happens '+(r.max===undefined?'at least '+r.min:r.min===r.max?'exactly '+r.min:r.min+'–'+r.max)+' times'}[r.template]);
    function drawPending(){
      pending.replaceChildren(rules.length?table(['Rule',''],rules.map((r,i)=>{const del=button('Remove','secondary');del.setAttribute('aria-label','Remove rule '+label(r));del.onclick=()=>{rules.splice(i,1);drawPending();};return [label(r),del];})):el('div','No rules added yet.','muted'));
      run.disabled=!rules.length;
    }
    drawPending();
    add.onclick=()=>{try{if(rules.length>=20)throw new Error('Check at most 20 rules at once.');rules.push(complianceRule({template:template.value,a:a.value,b:b.value,hours:hours.value,min:min.value,max:max.value}));message.textContent='';drawPending();}catch(error){message.textContent=error.message;}};
    run.onclick=async()=>{
      try{
        const body={rules};if(state.caseType)body.case_type=state.caseType;
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/compliance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
        if(generation!==state.generation)return;
        const rows=r.results.map(res=>{
          const keep=button('Save as signal','secondary');keep.setAttribute('aria-label','Save as signal: '+res.description);
          keep.onclick=async()=>{try{const sig={name:res.description.slice(0,120),when:{kind:'compliance',rule:res.rule}};if(state.caseType)sig.case_type=state.caseType;
            await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sig)});message.textContent='Saved as a signal.';}catch(error){message.textContent=error.message;}};
          const rate=res.violation_rate===null?'—':pct(res.violation_rate)+' ['+pct(res.violation_rate_ci95[0])+', '+pct(res.violation_rate_ci95[1])+']';
          return [res.description,Number(res.activated_cases).toLocaleString(),Number(res.violating_cases).toLocaleString(),rate,res.unknown_cases?Number(res.unknown_cases).toLocaleString():'—',res.violating_case_examples.slice(0,5).map(e=>e.case_id).join(', '),keep];});
        out.replaceChildren(el('div',r.rules_with_violations+' of '+r.results.length+' rules have violations.','muted'),
          table(['Rule','Cases it applies to','Violating cases','Violation rate (95% CI)','Unknown','Example cases',''],rows),el('div',r.interpretation,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  function processMapSection(){
    const box=section('Process map');box.id='owgEventLogProcessMap';
    box.appendChild(el('div','Simplify the map with the sliders. Hidden activities are removed first, so arrows can join steps that had hidden work between them. Times are gaps between recorded events, not measured work.','muted'));
    const slider=(id,label,value)=>{const node=input(id,'range');node.min='5';node.max='100';node.step='5';node.value=String(value);
      const out=el('output',value+'%');out.id=id+'Value';out.htmlFor=id;node.oninput=()=>{out.textContent=node.value+'%';schedule();};
      const wrap=field(label,node);wrap.appendChild(out);return {node,wrap};};
    const acts=slider('owgMapActivities','Activities shown',100),paths=slider('owgMapPaths','Paths shown',100);
    const row=el('div',null,'row');row.style.alignItems='end';row.append(acts.wrap,paths.wrap);
    const status=el('div',null,'muted');status.id='owgMapStatus';status.setAttribute('role','status');
    const graphHost=el('div');graphHost.id='owgMapGraph';const slow=el('div');slow.id='owgMapSlowest';
    box.append(row,status,graphHost,slow);
    const generation=state.generation;let timer=null,request=0;
    function schedule(){clearTimeout(timer);timer=setTimeout(()=>draw().catch(error=>{status.textContent=error.message;}),250);}
    async function draw(){
      const mine=++request;
      const r=await getJSON(logPath('process-map',{activities:String(Number(acts.node.value)/100),paths:String(Number(paths.node.value)/100)}));
      if(mine!==request||generation!==state.generation)return;
      const c=r.coverage;
      status.textContent='Showing '+c.activities_shown+' activities ('+pct(c.event_share_shown)+' of events) and '+c.transition_types_shown+' of '+c.transition_types_total+' paths ('+pct(c.transition_share_shown)+' of transitions).'+(c.activity_limit_reached?' The map shows at most 64 activities.':'');
      const draw=window.owgProcessGraph&&window.owgProcessGraph.drawObserved;
      graphHost.replaceChildren();
      if(draw&&r.activities.length){
        try{const start=Object.keys(r.start_activities)[0];const drawing=draw({activities:r.activities,transitions:r.transitions,cases:start?[{path:[start]}]:[]},()=>{},()=>{});drawing.id='owgMapDrawing';graphHost.appendChild(drawing);}
        catch(_){graphHost.appendChild(el('div','Map unavailable at this level; the paths are listed below.','muted'));}
      }
      const slowest=[...r.transitions].filter(t=>t.median_elapsed_gap_seconds!==null).sort((x,y)=>y.median_elapsed_gap_seconds-x.median_elapsed_gap_seconds).slice(0,10);
      slow.replaceChildren(el('h4','Slowest paths'),table(['From','To','Transitions','Median gap','Mean gap','90th percentile gap'],slowest.map(t=>[t.from,t.to,Number(t.transition_count).toLocaleString(),duration(t.median_elapsed_gap_seconds),duration(t.mean_elapsed_gap_seconds),duration(t.p90_elapsed_gap_seconds)])));
    }
    draw().catch(error=>{status.textContent=error.message;});
    return box;
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
    box.appendChild(el('div','Save conditions to check this log against, such as a slow median or cases left open. Checking shows which signals hold now and which cases are new since the last check. Signals never send messages or change anything.','muted'));
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
    const evaluate=button('Check signals');evaluate.id='owgSignalEvaluate';
    const fields={condition:field('Condition',condition),metric:field('KPI',metric),operator:field('Comparison',operator),threshold:field('Threshold',threshold),hours:field('Hours',hours),activity:field('Activity',activity),ends:field('Activities that end a case',ends)};
    const sync=()=>{const k=kind.value==='kpi';show(fields.condition,!k);show(fields.metric,k);show(fields.operator,k);show(fields.threshold,k);
      show(fields.hours,k?metric.value==='sla_breach_rate':['duration_over','open_longer_than'].includes(condition.value));
      show(fields.activity,!k&&['contains_activity','lacks_activity'].includes(condition.value));show(fields.ends,!k&&condition.value==='open_longer_than');};
    kind.onchange=condition.onchange=metric.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Name',name),field('Watch',kind),...Object.values(fields),add);
    const rules=el('div');rules.id='owgSignalRules';
    const message=el('div',null,'muted');message.id='owgSignalStatus';message.setAttribute('role','status');
    const out=el('div');out.id='owgSignalResults';
    box.append(row,message,rules,evaluate,out);
    const base=()=>'/v1/event-logs/'+encodeURIComponent(state.logId)+'/signals';
    const generation=state.generation;
    async function refresh(){
      const r=await getJSON(base());if(generation!==state.generation)return;
      rules.replaceChildren(r.rules.length?table(['Signal','Rule',''],r.rules.map(rule=>{
        const del=button('Delete','secondary');del.setAttribute('aria-label','Delete signal '+rule.name);
        del.onclick=async()=>{try{await api(base()+'/'+encodeURIComponent(rule.rule_id)+'/delete',{method:'POST'});message.textContent='Signal deleted.';await refresh();}catch(error){message.textContent=error.message;}};
        const w=rule.when;const text=w.kind==='kpi'?w.metric.replace(/_/g,' ')+' '+w.operator+' '+w.threshold:w.condition.replace(/_/g,' ')+(w.activity?' '+w.activity:'')+(w.seconds?' '+duration(w.seconds):'');
        return [rule.name,text,del];})):el('div','No saved signals yet.','muted'));
      evaluate.disabled=!r.rules.length;
    }
    add.onclick=async()=>{
      try{
        const values={name:name.value,kind:kind.value,condition:condition.value,metric:metric.value,operator:operator.value,threshold:threshold.value,hours:hours.value,activity:activity.value,ends:ends.value};
        const body=signalRule(values);if(state.caseType)body.case_type=state.caseType;
        await api(base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        message.textContent='Signal saved.';name.value='';await refresh();
      }catch(error){message.textContent=error.message;}
    };
    evaluate.onclick=async()=>{
      try{
        const r=await (await api(base()+'/evaluate',{method:'POST'})).json();if(generation!==state.generation)return;
        out.replaceChildren(
          el('div',r.triggered+' of '+r.signals.length+' signals hold now.','muted'),
          table(['Signal','Holds','Value or matching cases','New since last check','Example cases'],r.signals.map(sig=>[
            sig.name,sig.triggered?'yes':'no',
            sig.when.kind==='kpi'?(sig.value===null?'—':sig.when.metric.endsWith('_rate')?pct(sig.value):sig.when.metric==='case_count'?Number(sig.value).toLocaleString():duration(sig.value)):Number(sig.matching_cases).toLocaleString()+' cases',
            sig.when.kind==='kpi'?(sig.changed_since_last_evaluation?'changed':'—'):newCaseChange(sig),
            (sig.evidence_cases||[]).slice(0,5).map(c=>c.case_id+(sig.when.condition==='open_longer_than'&&c.age_since_first_event_seconds!==undefined?
              ' (age '+duration(c.age_since_first_event_seconds)+', idle '+duration(c.idle_since_last_event_seconds)+')':'')).join(', ')])),
          el('div',r.interpretation,'muted'));
        await refresh();
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    refresh().catch(error=>{message.textContent=error.message;});
    return box;
  }

  // Builds prediction settings from form values; throws a readable error.
  function predictionSpec(values){
    const ends=String(values.ends||'').split(',').map(x=>x.trim()).filter(Boolean);
    if(!ends.length)throw new Error('List the activities that end a case.');
    const spec={end_activities:ends,limit:50};
    if(values.outcome==='duration_over'){const h=Number(values.hours);if(!(h>0))throw new Error('Enter the hours for the outcome.');spec.outcome={kind:'duration_over',seconds:Math.round(h*3600)};}
    if(values.outcome==='contains_activity'){if(!values.activity)throw new Error('Choose the outcome activity.');spec.outcome={kind:'contains_activity',activity:values.activity};}
    return spec;
  }

  function predictionSection(activityNames){
    const box=section('Predict open cases');box.id='owgEventLogPredictions';
    box.appendChild(el('div','Estimate how much longer open cases will take, and how likely an outcome is, from completed cases that were at the same point. A backtest on later cases shows how accurate these estimates have been. Estimates are not commitments, and nobody is rated.','muted'));
    const ends=input('owgPredictEnds','text','Close, Cancel');
    const outcome=select('owgPredictOutcome',[['','Remaining time only'],['duration_over','Case takes longer than…'],['contains_activity','Case will include activity…']]);
    const hours=input('owgPredictHours','number');hours.min='0';hours.step='any';
    const activity=select('owgPredictActivity',activityNames.slice(0,500).map(x=>[x,x]));
    const run=button('Predict');run.id='owgPredictRun';
    const f={hours:field('Hours',hours),activity:field('Activity',activity)};
    const sync=()=>{show(f.hours,outcome.value==='duration_over');show(f.activity,outcome.value==='contains_activity');};
    outcome.onchange=sync;sync();
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Activities that end a case',ends),field('Also predict',outcome),f.hours,f.activity,run);
    const out=el('div');out.id='owgPredictResults';out.setAttribute('role','status');
    box.append(row,out);
    const generation=state.generation;
    run.onclick=async()=>{
      try{
        const spec=predictionSpec({ends:ends.value,outcome:outcome.value,hours:hours.value,activity:activity.value});
        if(state.caseType)spec.case_type=state.caseType;
        const r=await (await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/predictions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
        if(generation!==state.generation)return;
        const b=r.backtest;
        const quality=b.available?'Backtest on '+Number(b.scored_prefixes).toLocaleString()+' later steps: typical error '+duration(b.mean_absolute_error_seconds)+' vs '+duration(b.naive_mean_absolute_error_seconds)+' for a naive guess; the range held the actual time '+pct(b.p10_p90_coverage)+' of the time (target 80%).'+(b.brier_score!==undefined?' Outcome Brier score '+b.brier_score.toFixed(3)+' vs '+b.base_rate_brier_score.toFixed(3)+' for the base rate.':''):'No backtest: '+b.reason+'.';
        const headers=['Case','Latest activity','Open for','Expected remaining','Likely range',...(spec.outcome?['Outcome likelihood']:[]),'Based on'];
        const rows=r.predictions.map(p=>[p.case_id,p.last_activity,duration(p.age_seconds),duration(p.median_remaining_seconds),duration(p.remaining_p10_seconds)+' – '+duration(p.remaining_p90_seconds),
          ...(spec.outcome?[pct(p.outcome_probability)+' ['+pct(p.outcome_probability_ci95[0])+', '+pct(p.outcome_probability_ci95[1])+']']:[]),
          Number(p.comparable_cases).toLocaleString()+' cases ('+({same_activity_and_step:'same step',same_activity:'same activity',all_completed_cases:'all cases'})[p.basis]+')']);
        out.replaceChildren(el('div',Number(r.open_cases).toLocaleString()+' open cases; '+Number(r.predicted_open_cases).toLocaleString()+' have enough comparable history.','muted'),
          el('div',quality,'muted'),table(headers,rows),el('div',r.interpretation,'muted'));
      }catch(error){out.replaceChildren(el('div',error.message,'muted'));}
    };
    return box;
  }

  window.__owgEventLogModel={treeOutline,predictionSpec,complianceRule,analysisSpec,signalRule,newCaseChange,detectFormat,csvHeader,parsePaths,duration,cohortSpec,toCSV,parseWeightedPaths,formatWeightedPaths};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
