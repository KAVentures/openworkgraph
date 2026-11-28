/* Retained run interval review; no live runtime checks or mutations. */
export function mount(host,{api,dep,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const field=(title,n)=>{const l=el('label');l.append(el('span',title+' '),n);return l;};
  const input=(type,id)=>{const n=el('input');n.type=type;n.id=id;return n;};
  const start=input('date','owgConcurrencyStart'),end=input('date','owgConcurrencyEnd'),
    hash=input('text','owgConcurrencySpec'),unknown=input('checkbox','owgConcurrencyUnknown'),capacity=input('number','owgConcurrencyCapacity');
  hash.maxLength=64;capacity.min='1';capacity.max='10000';capacity.step='1';
  const view=el('select');view.id='owgConcurrencyView';
  for(const [key,name] of [['segments','Constant measured concurrency intervals'],['runs','Overlapping run evidence'],['excluded','Excluded retained records'],['uncertainty','Conditional concurrency uncertainty']]){const opt=el('option',name);opt.value=key;view.append(opt);}
  const run=button('Review retained run concurrency'),prev=button('Previous concurrency page'),next=button('Next concurrency page'),save=button('Download concurrency page JSON');
  run.id='owgConcurrencyRun';save.id='owgConcurrencyDownload';
  const status=el('p');status.id='owgConcurrencyStatus';status.setAttribute('role','status');
  const output=el('div');output.id='owgConcurrencyResult';
  let serial=0,busy=false,report=null;
  const live=n=>n===serial&&host.isConnected&&isCurrent();
  const controls=()=>{run.disabled=busy;prev.disabled=busy||!report||report.offset===0;next.disabled=busy||!report||report.next_offset===null;save.disabled=busy||!report;};
  function clear(){serial++;busy=false;report=null;output.replaceChildren();hash.disabled=unknown.checked;status.textContent='Review the exact dates, version scope and capacity threshold.';controls();}
  for(const n of [start,end,hash,capacity])n.oninput=clear;unknown.onchange=view.onchange=clear;
  const seconds=v=>{const x=BigInt(v),s=x/1000000n,f=x%1000000n;return s.toString()+(f?'.'+f.toString().padStart(6,'0').replace(/0+$/,''):'');};
  function table(name,headers,rows){
    const wrapper=el('div');wrapper.style.cssText='max-width:100%;overflow-x:auto';
    const t=el('table');t.setAttribute('aria-label',name);const head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}head.append(hr);
    for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const td=el(i?'td':'th',v??'Unknown');if(!i)td.scope='row';tr.append(td);});body.append(tr);}t.append(head,body);wrapper.append(t);return wrapper;
  }
  function render(r){
    const s=r.summary;output.replaceChildren(el('h4','Measured retained concurrency'),el('p',s.measured_intervals+' measured intervals of '+s.selected_records+' selected records; '+s.excluded_records+' excluded. Capacity threshold '+r.capacity+'.'),
      table('Concurrency totals',['Measure','Value'],Object.entries(s).map(([k,v])=>[k.replace(/_us$/,' seconds').replaceAll('_',' '),k.endsWith('_us')?seconds(v):v])),
      table('Concurrency duration histogram',['Concurrent measured runs','Seconds'],r.histogram.map(x=>[x.concurrent_runs,seconds(x.duration_us)])),
      el('p',r.matching_rows+' matching rows · offset '+r.offset+' · exact interval review '+r.review_sha256));
    if(r.view==='uncertainty'){
      const u=r.uncertainty_summary;
      output.append(el('h4','Conditional concurrency bounds'),el('p',u.unresolved_records+' selected unresolved records. The upper envelope is hypothetical, not verified live activity.'),
        table('Conditional envelope totals',['Measure','Value'],Object.entries(u).map(([k,v])=>[k.replace(/_us$/,' seconds').replaceAll('_',' '),k.endsWith('_us')?seconds(v):v])),
        table('Conditional envelope intervals',['Start seconds','End exclusive seconds','Lower runs','Upper runs','Threshold classification'],r.rows.map(x=>[seconds(x.start_us),seconds(x.end_us),x.lower_runs,x.upper_runs,x.capacity_state.replaceAll('_',' ')])));
    }else if(r.view==='segments')output.append(table('Constant concurrency intervals',['Start seconds from UTC window start','End exclusive seconds','Concurrent runs'],r.rows.map(x=>[seconds(x.start_us),seconds(x.end_us),x.concurrent_runs])));
    else if(r.view==='runs')output.append(table('Measured run overlap evidence',['Run ID','Reported start UTC','Reported finish UTC','Status','Clipped start seconds','Clipped end seconds','Overlapping peers'],r.rows.map(x=>[x.run_id,x.started_at,x.finished_at,x.status,seconds(x.clipped_start_us),seconds(x.clipped_end_us),x.overlapping_run_count])));
    else output.append(table('Excluded retained interval evidence',['Run ID','Reported start UTC','Reported finish UTC','Status','Reason'],r.rows.map(x=>[x.run_id,x.started_at,x.finished_at,x.status,x.reason])));
    output.append(...r.interpretation.map(x=>el('p',x)));
  }
  async function load(offset=0,pin=null){
    if(!isCurrent()||busy)return;const n=++serial;busy=true;report=null;output.replaceChildren();controls();
    status.textContent='Reviewing exact retained interval evidence…';
    try{
      if(!start.value||!end.value||!capacity.value||!capacity.checkValidity()||!Number.isSafeInteger(Number(capacity.value))||(!unknown.checked&&!/^[a-f0-9]{64}$/.test(hash.value)))throw Error('Choose completed UTC dates, exact version or unknown attribution, and a positive integer threshold.');
      const payload={start_date:start.value,end_date:end.value,reported_spec_sha256:unknown.checked?null:hash.value,capacity:Number(capacity.value),view:view.value,offset,limit:50,...(pin?{expected_review_sha256:pin}:{})};
      const r=await api('/v1/automation-monitoring/deployments/'+encodeURIComponent(dep)+'/run-concurrency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!live(n))return;
      if(r.format!=='openworkgraph.automation-run-concurrency.v1'||r.deployment_id!==dep||r.view!==payload.view||r.start_date!==payload.start_date||r.end_date_exclusive!==payload.end_date||r.reported_spec_sha256!==payload.reported_spec_sha256||r.capacity!==payload.capacity||r.execution_authorized!==false||pin&&pin!==r.review_sha256)throw Error('Unexpected retained interval review.');
      report=r;render(r);status.textContent='Retained concurrency reviewed. Live capacity and utilization are not verified.';
    }catch(error){if(live(n)){report=null;output.replaceChildren();status.textContent=error.message;}}
    finally{if(live(n)){busy=false;controls();}}
  }
  run.onclick=()=>load();prev.onclick=()=>{if(report)load(Math.max(0,report.offset-50),report.review_sha256);};next.onclick=()=>{if(report&&report.next_offset!==null)load(report.next_offset,report.review_sha256);};
  save.onclick=()=>{if(!report||busy||!isCurrent())return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-run-concurrency-page.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  host.replaceChildren(el('h3','Reported run concurrency and overlap'),el('p','Review overlapping finished run intervals across a completed UTC window. Unfinished runs are counted separately; no duration or live activity is imputed.'),
    field('UTC start date',start),field('UTC end date (exclusive)',end),field('Exact reported specification SHA-256',hash),field('Select unknown attribution only',unknown),field('Declared concurrent-run capacity threshold',capacity),field('Evidence view',view),run,prev,next,save,status,output);clear();
}
