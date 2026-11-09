/* Compare explicitly selected OWG simulation reports, entirely in this browser. */
(function () {
  'use strict';
  const FORMAT='openworkgraph.simulation-report.v1', MAX_FILES=4, MAX_BYTES=2*1024*1024;
  const object=o=>o!==null&&typeof o==='object'&&!Array.isArray(o);
  const short=(v,n=300)=>typeof v==='string'?v.slice(0,n):'';
  const number=v=>v===null?null:(typeof v==='number'&&Number.isFinite(v)&&v>=0?v:undefined);
  function runMetrics(value) {
    if(!object(value)||!object(value.cycle_time_seconds)||typeof value.unstable!=='boolean')throw Error('A report has incomplete simulation results.');
    const c=value.cycle_time_seconds, fields=['p50','p90','mean'];
    const out={unstable:value.unstable};
    for(const f of fields){out[f]=number(c[f]);if(out[f]===undefined)throw Error('A report has invalid cycle-time metrics.');}
    out.completed_per_day=number(value.completed_per_day);
    if(out.completed_per_day===undefined)throw Error('A report has invalid throughput.');
    return out;
  }
  function parseReport(raw, name) {
    if(typeof raw!=='string'||raw.length>MAX_BYTES)throw Error('Each report must be at most 2 MiB.');
    let doc;try{doc=JSON.parse(raw);}catch(_){throw Error('A report is not valid JSON.');}
    const a=doc?.analysis;
    if(!object(doc)||doc.format!==FORMAT||!object(a)||a.simulated_not_observed!==true||a.execution_authorized!==false||
       !object(a.log)||!object(a.scenario)||!object(a.calibration)||!Array.isArray(a.assumptions)||a.assumptions.length>100||
       a.assumptions.some(x=>typeof x!=='string'||x.length>2000))throw Error('Choose OWG simulation reports with model assumptions and no execution authority.');
    const cal=a.calibration;
    const observed=number(cal.observed_cases),calibrated=number(cal.calibrated_cases);
    if(observed===undefined||calibrated===undefined||!Number.isSafeInteger(observed)||!Number.isSafeInteger(calibrated)||calibrated>observed)
      throw Error('A report has invalid calibration coverage.');
    const cases=number(a.scenario.cases);
    if(cases===undefined||!Number.isSafeInteger(cases)||cases<1)throw Error('A report has an invalid case count.');
    return {file:short(name,200),generated_at:short(doc.generated_at,40),label:short(a.scenario.label,200)||'Unnamed scenario',
      log_name:short(a.log.name,200),log_id:short(a.log.log_id,100),case_notion:short(a.case_notion,200),
      observed_cases:observed,calibrated_cases:calibrated,cases,
      distribution:short(a.scenario.distribution,80),routing:short(a.scenario.routing,80),
      time_basis:short(a.scenario.activity_time_basis,80),baseline:runMetrics(a.baseline),scenario:runMetrics(a.result),
      assumptions:a.assumptions.map(x=>x.slice(0,2000)),uncertainty:object(a.uncertainty),
      model_inputs:JSON.stringify({arrivals_per_hour:a.scenario.arrivals_per_hour??null,calendar:a.scenario.calendar??null,
        arrival_pattern:a.scenario.arrival_pattern??null,pools:a.scenario.pools??null,priorities:a.scenario.priorities??null,
        paths:a.scenario.paths??null,transitions:a.scenario.transitions??null,activities:a.scenario.activities??null},null,2)};
  }
  function csvCell(value){const s=value===null||value===undefined?'':String(value);const v=/^(?:[\t\r]|[\s\uFEFF]*[=+\-@])/.test(s)?"'"+s:s;return /[",\r\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
  function toCsv(reports){
    const rows=[['report','scenario','generated_at','log','log_id','case_notion','observed_cases','calibrated_cases','simulated_cases','distribution','routing','time_basis','uncertainty_in_json','run','metric','value','unit','interpretation']];
    for(const r of reports)for(const [run,m] of [['baseline',r.baseline],['scenario',r.scenario]])
      for(const [key,unit] of [['p50','seconds'],['p90','seconds'],['mean','seconds'],['completed_per_day','cases/day'],['unstable','boolean']])
        rows.push([r.file,r.label,r.generated_at,r.log_name,r.log_id,r.case_notion,r.observed_cases,r.calibrated_cases,r.cases,r.distribution,r.routing,r.time_basis,r.uncertainty,run,key,m[key],unit,'Modelled; dataset and scope equivalence unverified.']);
    return rows.map(row=>row.map(csvCell).join(',')).join('\r\n')+'\r\n';
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={parseReport,toCsv,MAX_BYTES,MAX_FILES};
  if(typeof document==='undefined')return;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  function mount(){
    const anchor=document.getElementById('owgEventLogSimLoad');
    if(!anchor||anchor.parentElement.querySelector('#owgSimulationReportCompare'))return;
    const panel=el('section');panel.id='owgSimulationReportCompare';panel.style.marginTop='12px';
    const heading=el('h4','Compare saved simulation reports');
    const note=el('p','Choose 2–4 OWG simulation JSON reports. Files stay in this browser. Each column is a modelled result, not observed savings. Matching log labels or case counts do not prove the same data, filters or model; review the inputs and assumptions before comparing.');note.className='muted';
    const label=el('label','Simulation report files');label.htmlFor='owgSimulationCompareFiles';
    const files=el('input');files.type='file';files.id='owgSimulationCompareFiles';files.multiple=true;files.accept='.json,application/json';label.appendChild(files);
    const run=el('button','Compare reports');run.type='button';run.className='secondary';run.disabled=true;
    const csv=el('button','Download comparison CSV');csv.type='button';csv.className='secondary';csv.disabled=true;
    const status=el('p','Select at least two reports.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const output=el('div');let generation=0,reports=[];
    files.onchange=()=>{generation++;reports=[];output.replaceChildren();csv.disabled=true;const n=files.files?.length||0;run.disabled=n<2||n>MAX_FILES;status.textContent=n>MAX_FILES?'Choose at most four reports.':n<2?'Select at least two reports.':'Ready to compare '+n+' local files.';};
    run.onclick=async()=>{
      const current=++generation,selected=[...files.files];reports=[];csv.disabled=true;output.replaceChildren();run.disabled=true;
      status.textContent='Reading selected reports…';
      try{
        if(selected.length<2||selected.length>MAX_FILES||selected.some(f=>f.size>MAX_BYTES))throw Error('Choose 2–4 reports, each at most 2 MiB.');
        const values=await Promise.all(selected.map(async f=>parseReport(await f.text(),f.name)));
        if(current!==generation||!panel.isConnected)return;
        reports=values;const table=el('table'),head=el('thead'),tr=el('tr');
        for(const x of ['Report','Modelled run','Median (seconds)','90th percentile (seconds)','Mean (seconds)','Completed cases/day','Queue unstable']){const th=el('th',x);th.scope='col';tr.appendChild(th);}
        head.appendChild(tr);const tbody=el('tbody');
        const fmt=x=>x===null?'Not available':typeof x==='boolean'?(x?'Yes':'No'):String(x);
        for(const r of values)for(const [runName,m] of [['Baseline',r.baseline],['Scenario',r.scenario]]){
          const row=el('tr');for(const x of [r.file+' — '+r.label,runName,fmt(m.p50),fmt(m.p90),fmt(m.mean),fmt(m.completed_per_day),fmt(m.unstable)])row.appendChild(el('td',x));tbody.appendChild(row);
        }
        table.append(head,tbody);const wrap=el('div');wrap.className='table-wrap';wrap.appendChild(table);output.appendChild(wrap);
        for(const r of values){const detail=el('details'),summary=el('summary',r.file+' — '+r.label+' · scope and assumptions');detail.appendChild(summary);
          detail.appendChild(el('p','Saved '+(r.generated_at||'unknown')+' · Log '+(r.log_name||'unknown')+' ('+(r.log_id||'unknown ID')+') · Case notion '+(r.case_notion||'unknown')+'.'));
          detail.appendChild(el('p','Calibrated '+r.calibrated_cases+' of '+r.observed_cases+' recorded cases; simulated '+r.cases+'. Distribution: '+(r.distribution||'unknown')+'; routing: '+(r.routing||'unknown')+'; time basis: '+(r.time_basis||'unknown')+'. Uncertainty results '+(r.uncertainty?'included in the original JSON report':'not included')+'.'));
          const list=el('ul');for(const a of r.assumptions)list.appendChild(el('li',a));detail.appendChild(list);
          const inputs=el('details'),title=el('summary','Show recorded scenario inputs'),pre=el('pre',r.model_inputs);pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-height:20rem;overflow:auto';inputs.append(title,pre);detail.appendChild(inputs);output.appendChild(detail);
        }
        status.textContent='Showing '+values.length+' user-selected reports. Scope and dataset equality are not verified.';csv.disabled=false;
      }catch(error){if(current===generation&&panel.isConnected)status.textContent=error.message||'Could not read the selected reports.';}
      finally{if(current===generation&&panel.isConnected)run.disabled=false;}
    };
    csv.onclick=()=>{if(!reports.length)return;const url=URL.createObjectURL(new Blob([toCsv(reports)],{type:'text/csv'}));const a=el('a');a.href=url;a.download='owg-simulation-comparison.csv';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    panel.append(heading,note,label,run,csv,status,output);anchor.insertAdjacentElement('afterend',panel);
  }
  function start(){new MutationObserver(mount).observe(document.body,{childList:true,subtree:true});mount();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
