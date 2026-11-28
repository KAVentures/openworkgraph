/* Metadata-only whole-window review; source excerpts remain in existing date view. */
export function mount(host,{request,isCurrent,openDate}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const panel=el('details');panel.id='owgPolicyWindowReview';panel.append(el('summary','Whole-window policy question review'),
    el('p','Review up to 12 distinct active document bundles across the prepared timeline. Unresolved intervals are not compared. No existing decision is imported, and question disappearance is not proof of resolution.'));
  const run=button('Review whole policy window'),save=button('Download policy window metadata JSON');
  run.id='owgPolicyWindowRun';save.id='owgPolicyWindowDownload';save.disabled=true;
  const status=el('p');status.setAttribute('role','status');const result=el('div');result.id='owgPolicyWindowResult';
  let serial=0,report=null;
  function table(name,headers,rows){
    const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
    const t=el('table');t.setAttribute('aria-label',name);const head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}head.append(hr);
    for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const c=el(i?'td':'th');if(!i)c.scope='row';if(v instanceof Node)c.append(v);else c.textContent=String(v??'Not reviewed');tr.append(c);});body.append(tr);}t.append(head,body);wrap.append(t);return wrap;
  }
  const linkDate=(from,previous=null)=>{const n=button('Inspect '+from);n.onclick=()=>{if(report&&isCurrent())openDate(from,previous);};return n;};
  const filter=el('input');filter.type='search';filter.setAttribute('aria-label','Filter generated policy questions');
  const qhost=el('div'),pageStatus=el('p'),prev=button('Previous questions'),next=button('Next questions');let offset=0;
  function showQuestions(){
    if(!report)return;
    const term=filter.value.trim().toLowerCase(),rows=report.questions.filter(q=>[q.question_id,q.kind,q.target_id].some(x=>x.toLowerCase().includes(term)));
    const page=rows.slice(offset,offset+25);
    qhost.replaceChildren(table('Question persistence',['Question ID / kind / target','Generated days','Not generated in resolved days','Incomplete coverage days','Distinct evidence bases','Evidence intervals'],page.map(q=>{
      const d=el('details');d.append(el('summary',q.intervals.length+' intervals'));
      for(const i of q.intervals){const p=el('div');p.append(el('p',i.start_date+' – '+i.end_date_exclusive+' · '+i.days+' days'),linkDate(i.start_date));d.append(p);}
      return [q.question_id+' · '+q.kind+' · '+q.target_id,q.generated_days,q.not_generated_in_resolved_days,q.coverage_incomplete_days,q.distinct_evidence_count,d];
    })));
    pageStatus.textContent=rows.length+' matching questions · offset '+offset;prev.disabled=offset===0;next.disabled=offset+25>=rows.length;
  }
  filter.oninput=()=>{offset=0;showQuestions();};prev.onclick=()=>{offset=Math.max(0,offset-25);showQuestions();};next.onclick=()=>{offset+=25;showQuestions();};
  function render(r){
    result.replaceChildren(el('h5','Whole-window clarification review'),el('p',r.distinct_bundle_count+' distinct source bundles · '+r.resolved_days+' resolved days · '+r.unresolved_days+' unresolved days · '+r.resolved_days_with_incomplete_scan+' resolved days with incomplete scans.'),
      table('Policy question timeline',['From','Until exclusive','Days','Selected versions','Generated questions','Complete scan','Evidence'],r.intervals.map(i=>[i.start_date,i.end_date_exclusive,i.days,i.selected_version_ids.join(', '),i.question_count,i.coverage_complete===null?null:i.coverage_complete?'Yes':'No',i.status==='resolved_under_declared_assumptions'?linkDate(i.start_date):'Unresolved'])),
      table('Adjacent source evidence changes',['Date','Comparison','New','No longer generated','Changed evidence','Same evidence','Inspect'],r.transitions.map((t,i)=>[t.date,t.status,t.new?.length,t.no_longer_generated?.length,t.changed_evidence?.length,t.same_evidence?.length,t.status==='compared'?linkDate(t.date,r.intervals[i].start_date):'Not compared'])),
      el('h5','Generated question persistence'),filter,qhost,pageStatus,prev,next,el('p',r.interpretation));offset=0;filter.value='';showQuestions();
  }
  run.onclick=async()=>{if(!isCurrent())return;const ticket=++serial;report=null;run.disabled=save.disabled=true;result.replaceChildren();status.textContent='Reviewing distinct prepared source bundles…';
    try{const r=await request();if(ticket!==serial||!isCurrent()||!host.isConnected)return;if(r.format!=='openworkgraph.effective-policy-window-review.v1'||r.execution_authorized!==false)throw Error('Unexpected window review response.');
      report=r;render(r);save.disabled=false;status.textContent='Whole-window review complete; no policy decisions recorded.';}
    catch(e){if(ticket===serial&&host.isConnected&&isCurrent()){report=null;result.replaceChildren();status.textContent=e.message;}}
    finally{if(ticket===serial&&host.isConnected&&isCurrent())run.disabled=false;}
  };
  save.onclick=()=>{if(!report||!isCurrent())return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-policy-window-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  panel.append(run,save,status,result);host.replaceChildren(panel);
}
