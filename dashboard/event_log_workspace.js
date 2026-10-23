/* Local event-log workspace: import CSV/XES/OCEL logs, analyse, compare with a reference model, export. */
(() => {
  'use strict';
  const MODEL_FORMAT='openworkgraph.reference-model.v1';
  const state={logs:[],logId:'',caseType:''};

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
    const body=el('tbody');rows.forEach(r=>{const tr=el('tr');r.forEach(v=>tr.appendChild(el('td',v)));body.appendChild(tr);});
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

    const file=input('owgEventLogFile','file');file.accept='.csv,.tsv,.txt,.xes,.json,.jsonocel';
    const format=select('owgEventLogFormat',[['csv','CSV'],['xes','XES'],['ocel','OCEL 2.0 JSON']]);
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
    card.append(row,mapping,consentLabel,importButton,statusLine);

    const pick=select('owgEventLogPick',[]);const caseType=select('owgEventLogCaseType',[]);
    const exportFormat=select('owgEventLogExportFormat',[['csv','CSV'],['xes','XES'],['ocel','OCEL 2.0']]);
    const analyze=button('Analyse');analyze.id='owgEventLogAnalyze';
    const exportButton=button('Export','secondary');exportButton.id='owgEventLogExport';
    const remove=button('Delete log','secondary');remove.id='owgEventLogDelete';
    const manage=el('div',null,'row');manage.id='owgEventLogManage';manage.style.cssText='align-items:end;margin-top:12px';show(manage,false);
    manage.append(field('Stored log',pick),field('Case notion',caseType),analyze,field('Export as',exportFormat),exportButton,remove);
    const results=el('div');results.id='owgEventLogResults';
    card.append(manage,results);

    const browse=document.getElementById('owgProcessCSV');
    if(browse)browse.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    const refreshImport=()=>{importButton.disabled=!(file.files&&file.files[0]&&consent.checked&&name.value.trim());show(mapping,format.value==='csv');};
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
    pick.onchange=()=>selectLog(pick.value);
    caseType.onchange=()=>{state.caseType=caseType.value;results.replaceChildren();};
    analyze.onclick=()=>analyse().catch(error=>status(error.message));
    exportButton.onclick=async()=>{
      try{
        status('Preparing export…');
        const response=await api(logPath('export',{format:exportFormat.value}));
        const url=URL.createObjectURL(await response.blob());
        const link=el('a');link.href=url;link.download=state.logId+'.'+(exportFormat.value==='ocel'?'jsonocel':exportFormat.value);
        document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        status('Export ready.');
      }catch(error){status(error.message);}
    };
    remove.onclick=async()=>{
      const chosen=state.logs.find(l=>l.log_id===state.logId);if(!chosen)return;
      if(!window.confirm('Delete the event log "'+chosen.name+'" from this computer? This cannot be undone.'))return;
      try{await api('/v1/event-logs/'+encodeURIComponent(state.logId)+'/delete',{method:'POST'});status('Event log deleted.');results.replaceChildren();await refreshLogs('');}
      catch(error){status(error.message);}
    };
    refreshImport();
    refreshLogs('').catch(()=>{});
  }

  async function refreshLogs(selectId){
    const data=await getJSON('/v1/event-logs');
    state.logs=Array.isArray(data.logs)?data.logs:[];
    const pick=document.getElementById('owgEventLogPick');
    setOptions(pick,state.logs.map(l=>[l.log_id,l.name+' · '+Number(l.events).toLocaleString()+' events']));
    show(document.getElementById('owgEventLogManage'),state.logs.length>0);
    const next=state.logs.some(l=>l.log_id===selectId)?selectId:(state.logs[0]?state.logs[0].log_id:'');
    if(next){pick.value=next;await selectLog(next);}else state.logId='';
  }

  async function selectLog(logId){
    state.logId=logId;state.caseType='';
    document.getElementById('owgEventLogResults').replaceChildren();
    const types=await getJSON('/v1/event-logs/'+encodeURIComponent(logId)+'/object-types');
    const options=(types.object_types||[]).map(t=>[t.object_type,t.object_type+' ('+Number(t.objects).toLocaleString()+')']);
    const caseType=document.getElementById('owgEventLogCaseType');
    setOptions(caseType,options);
    state.caseType=options.some(o=>o[0]==='case')?'case':(options[0]?options[0][0]:'');
    caseType.value=state.caseType;
  }

  async function analyse(){
    const results=document.getElementById('owgEventLogResults');results.replaceChildren();
    status('Analysing…');
    const [summary,variants,activities]=await Promise.all([getJSON(logPath('summary')),getJSON(logPath('variants',{limit:10})),getJSON(logPath('activities'))]);
    const overview=section('Overview');
    const d=summary.case_duration_seconds||{};
    overview.appendChild(table(['Cases','Events','Activities','Median case time','90th percentile'],[[Number(summary.cases).toLocaleString(),Number(summary.events).toLocaleString(),summary.activities,duration(d.median),duration(d.p90)]]));
    results.appendChild(overview);

    const variantBox=section('Most common variants');
    variantBox.appendChild(table(['Path','Cases','Share','Median time'],(variants.variants||[]).map(v=>[v.path.join(' → '),Number(v.cases).toLocaleString(),pct(v.share),duration(v.median_duration_seconds)])));
    variantBox.appendChild(el('div',Number(variants.distinct_variants).toLocaleString()+' distinct variants.','muted'));
    results.appendChild(variantBox);

    const names=(activities.activities||[]).map(a=>a.activity);
    results.appendChild(kpiSection(names));
    results.appendChild(benchmarkSection(names));
    results.appendChild(conformanceSection((variants.variants||[])[0]));
    results.appendChild(rootCauseSection((activities.activities||[]).map(a=>a.activity)));
    status('');
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

  window.__owgEventLogModel={detectFormat,csvHeader,parsePaths,duration,cohortSpec,toCSV};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
