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
  function mountServiceReview(){
    const anchor=document.getElementById('owgEventLogSimLoad');
    if(!anchor||anchor.parentElement.querySelector('#owgServiceLevelReview'))return;
    const panel=el('details');panel.id='owgServiceLevelReview';
    const button=(label,id)=>{const b=el('button',label);b.type='button';b.className='secondary';if(id)b.id=id;return b;};
    const field=(label,node)=>{const l=el('label',label+' ');l.append(node);return l;};
    const input=(id,type,value)=>{const n=el('input');n.id=id;n.type=type;if(value!==undefined)n.value=value;return n;};
    const option=(value,label)=>{const o=el('option',label);o.value=value;return o;};
    const logs=el('select');logs.id='owgServiceReviewLog';logs.append(option('','Choose retained log'));
    const notion=input('owgServiceReviewCaseType','text');notion.maxLength=200;
    const load=button('Load retained simulation logs','owgServiceReviewLoad');
    const config=el('textarea');config.id='owgServiceReviewGrid';config.rows=12;config.style.width='100%';
    config.value=JSON.stringify({format:'openworkgraph.simulation-capacity-grid.v1',baseline:{cases:200,seed:17,distribution:'fixed'},
      designs:[{label:'Alternative (edit staffing overrides)',activities:{}}],arrival_rates_per_hour:[0.5,1],
      replications:2,target:{cycle_time_minutes:240,minimum_completion_share:0.9}},null,2);
    const file=input('owgServiceReviewImport','file');file.accept='.json,application/json';
    const targets=el('div'),targetRows=[];
    const shares=input('owgServiceReviewShares','text','50,90,95');
    const prepare=button('Prepare exact multi-SLA grid','owgServiceReviewPrepare'),run=button('Run multi-SLA capacity review','owgServiceReviewRun');
    const download=button('Download multi-SLA JSON','owgServiceReviewJson'),csv=button('Download service target CSV','owgServiceReviewCsv');
    const review=el('textarea');review.id='owgServiceReviewPrepared';review.rows=8;review.readOnly=true;review.style.width='100%';
    const status=el('p');status.id='owgServiceReviewStatus';status.setAttribute('role','status');
    const output=el('div');output.id='owgServiceReviewOutput';
    let generation=0,prepared=null,report=null;
    function invalidate(message='Inputs changed; prepare the exact grid again.'){
      generation++;prepared=report=null;review.value='';output.replaceChildren();run.disabled=download.disabled=csv.disabled=true;status.textContent=message;
    }
    function addTarget(name='',deadline='',share=''){
      if(targetRows.length>=8){status.textContent='At most eight service targets.';return;}
      const group=el('fieldset');group.append(el('legend','Service target'));
      const label=el('input');label.maxLength=80;label.value=name;label.setAttribute('aria-label','Service target name');
      const minutes=el('input');minutes.type='number';minutes.min='.001';minutes.max='525600';minutes.step='any';minutes.value=deadline;minutes.setAttribute('aria-label','Service deadline minutes');
      const percentage=el('input');percentage.type='number';percentage.min='1';percentage.max='100';percentage.step='any';percentage.value=share;percentage.setAttribute('aria-label','Service minimum completion percent');
      const remove=button('Remove service target');const row={group,label,minutes,percentage};
      remove.onclick=()=>{targetRows.splice(targetRows.indexOf(row),1);group.remove();invalidate();};
      group.append(field('Target label',label),field('Deadline minutes',minutes),field('Minimum completion (%)',percentage),remove);
      targetRows.push(row);targets.append(group);invalidate();
    }
    const add=button('Add service target','owgServiceReviewAdd');add.onclick=()=>addTarget();
    for(const node of [config,notion,shares,targets])node.addEventListener('input',()=>invalidate());
    logs.onchange=()=>invalidate();
    async function call(path,body){
      await window.__owgAuthReady;
      const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Local service review request refused ('+response.status+').');
      return data;
    }
    load.onclick=async()=>{
      invalidate('Reading retained local logs…');const n=generation,previous=logs.value;
      load.disabled=true;
      try{
        const data=await call('/v1/event-logs');if(n!==generation||!panel.isConnected)return;
        logs.replaceChildren(option('','Choose retained log'),...data.logs.map(l=>option(l.log_id,l.name+' · '+l.events+' events')));
        if(data.logs.some(l=>l.log_id===previous))logs.value=previous;
        status.textContent='Select the exact retained source and review scenario assumptions.';
      }catch(e){if(n===generation&&panel.isConnected)status.textContent=e.message;}finally{load.disabled=false;}
    };
    file.onchange=async()=>{
      invalidate('Reading local grid input…');const n=generation,f=file.files?.[0];if(!f)return;
      try{
        if(f.size>2*1024*1024)throw Error('Choose a grid input or report of at most 2 MiB.');
        const parsed=JSON.parse(await f.text());if(n!==generation||!panel.isConnected)return;
        const grid=parsed.inputs||parsed;
        if(!grid||grid.format!=='openworkgraph.simulation-capacity-grid.v1')throw Error('Choose a supported capacity-grid input or report.');
        if(!confirm('Replace temporary grid assumptions with the selected file? Its source log is not selected automatically.'))return;
        config.value=JSON.stringify(Object.fromEntries(Object.entries(grid).filter(([k])=>k!=='service_review')),null,2);
        if(grid.service_review){
          const s=grid.service_review;
          if(!Array.isArray(s.targets)||s.targets.length<1||s.targets.length>8||!Array.isArray(s.inverse_completion_shares))throw Error('Invalid imported service settings.');
          targets.replaceChildren();targetRows.length=0;
          for(const t of s.targets)addTarget(t.label,String(t.cycle_time_minutes),String(t.minimum_completion_share*100));
          shares.value=s.inverse_completion_shares.map(x=>x*100).join(',');
        }
        invalidate('Imported temporary inputs. Review the current source and all assumptions.');file.value='';
      }catch(e){if(panel.isConnected)status.textContent=e.message;}
    };
    function numeric(n){
      if(n.value===''||!n.checkValidity()||!Number.isFinite(Number(n.value)))throw Error('Enter every service target value explicitly.');
      return Number(n.value);
    }
    prepare.onclick=()=>{
      invalidate();
      try{
        if(!logs.value)throw Error('Choose a retained log explicitly.');
        const grid=JSON.parse(config.value);
        if(!grid||grid.format!=='openworkgraph.simulation-capacity-grid.v1')throw Error('Enter a capacity grid input object.');
        if(!targetRows.length)throw Error('Add at least one service target.');
        const inverse=shares.value.split(',').map(s=>{if(!s.trim()||!Number.isFinite(Number(s))||Number(s)<1||Number(s)>100)throw Error('Use inverse completion percentages between 1 and 100.');return Number(s)/100;});
        const service_review={targets:targetRows.map(t=>{if(!t.label.value.trim())throw Error('Name each target.');
          return {label:t.label.value.trim(),cycle_time_minutes:numeric(t.minutes),minimum_completion_share:numeric(t.percentage)/100};}),inverse_completion_shares:inverse};
        const body={...grid,service_review};if(new TextEncoder().encode(JSON.stringify(body)).length>65536)throw Error('Grid input exceeds 64 KiB.');
        prepared={log:logs.value,notion:notion.value.trim(),body};review.value=JSON.stringify(prepared,null,2);run.disabled=false;
        status.textContent='Review the exact source and grid. The same seeded simulation runs will supply all service targets.';
      }catch(e){status.textContent=e.message;}
    };
    function table(headers,rows){
      const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
      const t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');
      for(const label of headers){const th=el('th',label);th.scope='col';hr.append(th);}h.append(hr);
      for(const row of rows){const tr=el('tr');for(const v of row)tr.append(el('td',v===null?'No finite endpoint':String(v??'—')));b.append(tr);}
      t.append(h,b);wrap.append(t);return wrap;
    }
    const pct=v=>(100*v).toFixed(2)+'%';
    const mins=v=>v===null?null:(v/60).toFixed(4);
    function save(value,type,name){
      const url=URL.createObjectURL(new Blob([value],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    download.onclick=()=>{if(report)save(JSON.stringify(report,null,2),'application/json','owg-multi-sla-capacity.json');};
    csv.onclick=()=>{
      if(!report)return;
      const rows=[['design','demand_per_hour','seed','target','deadline_minutes','required_share','on_time','known_misses','unknown','arrivals','required_count','lower_share','upper_share','status']];
      for(const c of report.service_review.cells)for(const r of c.runs)for(const [i,t] of r.targets.entries()){
        const s=report.service_review.inputs.targets[i];
        rows.push([c.design,c.arrival_rate_per_hour,r.seed,t.label,s.cycle_time_minutes,s.minimum_completion_share,t.on_time,t.known_misses,t.unresolved,r.arrivals,t.required_completions,t.share_lower,t.share_upper,t.status]);
      }
      save(rows.map(r=>r.map(csvCell).join(',')).join('\r\n')+'\r\n','text/csv','owg-multi-sla-targets.csv');
    };
    run.onclick=async()=>{
      if(!prepared)return;
      const n=++generation,p=prepared;report=null;run.disabled=download.disabled=csv.disabled=true;output.replaceChildren();
      status.textContent='Running bounded capacity grid once for all service targets…';
      try{
        const data=await call('/v1/event-logs/'+encodeURIComponent(p.log)+'/simulation/capacity-grid'+(p.notion?'?case_type='+encodeURIComponent(p.notion):''),p.body);
        if(n!==generation||!panel.isConnected)return;
        if(!data.service_review||data.execution_authorized!==false)throw Error('Unexpected service review report.');
        report=data;download.disabled=csv.disabled=false;const s=data.service_review;
        output.append(el('h5','Joint service-level operating envelope'),
          el('p',data.simulated_cases+' simulated arrivals across original grid runs; '+s.inputs.targets.length+' service targets reuse those runs.'),
          table(['Design','Jointly passing tested rates / hour','Other tested rates','Highest tested passing rate','Nonmonotonic pattern'],
            s.joint_operating_envelopes.map(x=>[x.design,x.jointly_passing_tested_rates.join(', ')||'None',x.other_tested_rates.join(', ')||'None',x.highest_jointly_passing_tested_rate,x.nonmonotonic_tested_pattern?'Yes':'No'])),
          table(['Design','Demand / hour','Target','Deadline minutes','Required share','Worst lower','Worst upper','Status'],
            s.cells.flatMap(c=>c.targets.map(t=>[c.design,c.arrival_rate_per_hour,t.label,t.cycle_time_minutes,pct(t.minimum_completion_share),
              pct(t.worst_replication_share_lower),pct(t.worst_replication_share_upper),t.status]))),
          el('h5','Inverse deadline review (all arrivals)'),
          table(['Design','Demand / hour','Completion share','Minimum possible minutes','Minimum guaranteed minutes','All runs stable'],
            s.cells.flatMap(c=>c.inverse_deadlines.map(t=>[c.design,c.arrival_rate_per_hour,pct(t.share),
              mins(t.minimum_possible_deadline_across_all_runs_seconds),mins(t.minimum_guaranteed_deadline_across_all_runs_seconds),t.stable_all_runs?'Yes':'No']))),
          el('h5','Pairwise service comparison'),
          table(['Left design','Right design','Demand / hour','Direction','Target','Right minus left bounds (pp)'],
            s.pairwise_service_comparisons.flatMap(c=>c.targets.map(t=>[c.left_design,c.right_design,c.arrival_rate_per_hour,c.direction,t.label,
              (100*t.right_minus_left_share_lower).toFixed(2)+' to '+(100*t.right_minus_left_share_upper).toFixed(2)]))));
        const evidence=el('details');evidence.append(el('summary','Seed-level service accounting'),
          table(['Design','Demand / hour','Seed','Target','All arrivals','On time','Known misses','Unknown','Required count','Status'],
            s.cells.flatMap(c=>c.runs.flatMap(r=>r.targets.map(t=>[c.design,c.arrival_rate_per_hour,r.seed,t.label,r.arrivals,t.on_time,t.known_misses,t.unresolved,t.required_completions,t.status])))));
        output.append(evidence,el('p',s.interpretation));status.textContent='Multi-SLA capacity review complete. No production service guarantee is established.';
      }catch(e){if(n===generation&&panel.isConnected)status.textContent=e.message;}
      finally{if(n===generation&&panel.isConnected)run.disabled=false;}
    };
    addTarget('Standard','240','90');addTarget('Expedited','120','80');
    panel.append(el('summary','Multi-SLA capacity and inverse deadline review'),
      el('p','Compare multiple service deadlines and completion shares on one capacity grid. Choose the exact retained source; advanced staffing and routing assumptions use the existing grid JSON format. Starter values are illustrative, not measured.'),
      load,field('Retained log',logs),field('Optional exact object case type',notion),field('Import capacity grid input or report',file),
      field('Capacity grid assumptions JSON',config),el('h5','Service targets'),targets,add,
      field('Inverse completion shares (%) comma-separated',shares),prepare,field('Exact reviewed input',review),run,download,csv,status,output);
    anchor.insertAdjacentElement('afterend',panel);invalidate('Select a retained log and review assumptions.');
  }
  function start(){const render=()=>{mount();mountServiceReview();};new MutationObserver(render).observe(document.body,{childList:true,subtree:true});render();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
