/* Session-owned analysis job console; no new query is executed by a download. */
export function mount(host,{api,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const panel=el('details');panel.id='owgAnalysisJobConsole';panel.append(el('summary','Local analysis job console'),
    el('p','Only jobs owned by this dashboard session are shown, across its local logs. Completed results expire from submission after the existing retention interval or restart. Downloads are historical retained query results, not fresh source verification; they can contain sensitive data.'));
  const refresh=button('Refresh session jobs'),clear=button('Clear reviewed completed jobs'),confirm=el('input');confirm.type='checkbox';confirm.id='owgJobConsoleConfirm';
  refresh.id='owgJobConsoleRefresh';clear.id='owgJobConsoleClear';const consent=el('label');consent.append(confirm,el('span','I reviewed this exact terminal-job population and want to erase its retained results.'));
  const status=el('p');status.id='owgJobConsoleStatus';status.setAttribute('role','status');
  const result=el('div');result.id='owgJobConsoleResult';
  let serial=0,report=null,busy=false;
  const live=n=>n===serial&&host.isConnected&&isCurrent();
  function controls(){refresh.disabled=busy;confirm.disabled=busy||!report||!report.terminal_count;clear.disabled=busy||!report||!report.terminal_count||!confirm.checked;}
  function invalidate(){serial++;report=null;confirm.checked=false;result.replaceChildren();controls();}
  async function load(){
    const n=++serial;busy=true;report=null;confirm.checked=false;result.replaceChildren();controls();status.textContent='Reading session-owned local jobs…';
    try{const r=await(await api('/v1/event-log-analysis-jobs')).json();if(!live(n))return;
      if(r.format!=='openworkgraph.analysis-job-console.v1'||r.session_scoped!==true||r.execution_authorized!==false)throw Error('Unexpected job inventory.');
      report=r;render(r);status.textContent=r.owned_job_count+' session-owned retained jobs. Refresh for current state.';}
    catch(e){if(live(n)){report=null;result.replaceChildren();status.textContent=e.message;}}
    finally{if(live(n)){busy=false;controls();}}
  }
  async function action(job,verb){
    if(busy||!report||!isCurrent())return;
    if(!window.confirm((verb==='cancel'?'Cancel job ':'Erase job and any retained result ')+job.job_id+'?'))return;
    const path='/v1/event-logs/'+encodeURIComponent(job.log_id)+'/analysis-jobs/'+encodeURIComponent(job.job_id)+'/'+verb;
    const n=++serial;busy=true;report=null;confirm.checked=false;result.replaceChildren();controls();
    try{await api(path,{method:'POST'});if(live(n))await load();}
    catch(e){if(live(n)){status.textContent=e.message;busy=false;controls();}}
  }
  async function download(job,btn,r){
    if(busy||report!==r||!isCurrent())return;
    if(!window.confirm('Download the historical retained result? The source has not been rechecked, and the file may contain sensitive data.'))return;
    const n=serial;btn.disabled=true;
    try{const path='/v1/event-logs/'+encodeURIComponent(job.log_id)+'/analysis-jobs/'+encodeURIComponent(job.job_id)+'/result';
      const blob=await(await api(path)).blob();
      if(!live(n)||report!==r)return;
      if(blob.size!==job.result_bytes)throw Error('Retained result size changed; refresh jobs.');
      const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=job.job_id+'-historical-analysis.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      status.textContent='Historical retained result downloaded. Current source not verified.';
    }catch(e){if(live(n))status.textContent=e.message;}
    finally{if(live(n))btn.disabled=false;}
  }
  function render(r){
    const summary=el('p',Object.entries(r.owned_state_counts).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · '));
    const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
    const table=el('table');table.setAttribute('aria-label','Session-owned analysis jobs');const head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const text of ['Job / log','State','Submitted / finished UTC','Result basis','Bytes','Expires in (seconds)','Actions']){const th=el('th',text);th.scope='col';hr.append(th);}head.append(hr);
    for(const job of r.jobs){const tr=el('tr'),name=el('th');name.scope='row';name.append(el('div',job.job_id),el('div',job.log_id));tr.append(name);
      for(const value of [job.state,job.submitted_at+' / '+(job.finished_at||'Pending'),job.source_bound_result_available?'Source pinned at execution':'No available source-bound result',job.result_bytes??'None',job.expires_in_seconds])tr.append(el('td',value));
      const actions=el('td');
      if(['queued','running'].includes(job.state)){const cancel=button('Cancel job');cancel.setAttribute('aria-label','Cancel '+job.job_id);cancel.onclick=()=>action(job,'cancel');actions.append(cancel);}
      if(job.result_available){const d=button('Download historical result');d.setAttribute('aria-label','Download historical result '+job.job_id);d.onclick=()=>download(job,d,r);actions.append(d);}
      if(!['queued','running'].includes(job.state)){const erase=button('Erase job');erase.setAttribute('aria-label','Erase '+job.job_id);erase.onclick=()=>action(job,'delete');actions.append(erase);}
      tr.append(actions);body.append(tr);}
    table.append(head,body);wrap.append(table);result.replaceChildren(summary,wrap,el('p',r.terminal_count+' terminal jobs in reviewed cleanup population · '+r.terminal_review_sha256));
  }
  clear.onclick=async()=>{
    if(!report||clear.disabled||!isCurrent())return;
    const pin=report.terminal_review_sha256,n=++serial;busy=true;report=null;confirm.checked=false;result.replaceChildren();controls();
    try{const r=await(await api('/v1/event-log-analysis-jobs/clear-terminal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_terminal_review_sha256:pin,confirm:true})})).json();
      if(live(n)){await load();if(host.isConnected&&isCurrent())status.textContent=r.removed_terminal_jobs+' terminal jobs erased. Active jobs were not cancelled.';}}
    catch(e){if(live(n)){status.textContent=e.message;busy=false;controls();}}
  };
  refresh.onclick=load;confirm.onchange=controls;panel.append(refresh,consent,clear,status,result);host.replaceChildren(panel);invalidate();
}
