/* Explicit pre-test roster and post-test review; no invocation or rollout. */
export function mountNativeShadowRoster(host){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.style.margin='0.5rem 0';n.append(node);return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const pin=()=>{const n=el('input');n.maxLength=64;n.placeholder='Independent SHA-256';return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  const manifest=file(),packagePin=pin(),cases=el('textarea'),confirm=el('input');
  cases.rows=8;cases.style.width='100%';cases.value=JSON.stringify([{case_id:'case_1',expected_framework:'',reviewed_tool_labels:[]}],null,2);
  confirm.type='checkbox';
  const freeze=button('Freeze test roster'),savePlan=button('Download frozen roster'),saveWorklist=button('Download blank assignment worklist');
  const planFile=file(),planPin=pin(),traceFile=file(),tracePin=pin(),assignments=file();
  const review=button('Reconcile frozen test roster'),saveReport=button('Download complete roster review');
  const before=el('p','Declare all cases before running tests.'),after=el('p','Supply the independently retained plan and trace pins.');
  for(const n of [before,after]){n.setAttribute('role','status');n.setAttribute('aria-live','polite');n.style.overflowWrap='anywhere';}
  const result=el('div');let frozen=null,report=null,busy=false;
  const preInputs=[manifest,packagePin,cases],postInputs=[planFile,planPin,traceFile,tracePin,assignments];
  function refresh(){
    for(const n of [...preInputs,...postInputs,confirm])n.disabled=busy;
    freeze.disabled=busy||!manifest.files?.[0]||!hex(packagePin.value.trim())||!confirm.checked;
    savePlan.disabled=saveWorklist.disabled=busy||!frozen;
    review.disabled=busy||!planFile.files?.[0]||!traceFile.files?.[0]||!assignments.files?.[0]||!hex(planPin.value.trim())||!hex(tracePin.value.trim());
    saveReport.disabled=busy||!report;
  }
  function invalidateFreeze(){frozen=null;confirm.checked=false;before.textContent='Roster inputs changed. Confirm the full population again.';refresh();}
  function invalidateReview(){report=null;result.replaceChildren();after.textContent='Review inputs changed. Reconcile the complete roster again.';refresh();}
  for(const n of preInputs)n.addEventListener(n.type==='file'?'change':'input',invalidateFreeze);
  for(const n of postInputs)n.addEventListener(n.type==='file'?'change':'input',invalidateReview);
  confirm.addEventListener('change',refresh);
  async function read(input,limit){
    const f=input.files?.[0];if(!f||!f.size||f.size>limit)throw Error('Choose an original JSON document within the indicated byte limit.');
    return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());
  }
  async function post(path,payload){
    await window.__owgAuthReady;
    const response=await fetch(path,{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch(_){}throw Error(typeof detail==='string'?detail:'Request refused ('+response.status+').');}
    return response.json();
  }
  function download(name,text){
    const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),a=el('a');
    a.href=url;a.download=name;document.body.append(a);try{a.click();}finally{a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  }
  freeze.onclick=async()=>{
    if(freeze.disabled)return;
    frozen=null;busy=true;confirm.checked=false;refresh();before.textContent='Validating the full planned case population.';
    try{
      const p=packagePin.value.trim();
      if(new TextEncoder().encode(cases.value).length>256*1024)throw Error('Case declaration exceeds 256 KiB.');
      const r=await post('/v1/native-shadow-roster/freeze',{manifest_json:await read(manifest,256*1024),package_sha256:p,cases_json:cases.value,confirm_freeze:true});
      if(r.format!=='openworkgraph.native-shadow-roster-preview.v1'||r.package_sha256!==p||!hex(r.plan_sha256)||r.execution_authorized!==false||r.prospective_enrollment_verified!==false||typeof r.plan_json!=='string'||typeof r.worklist_json!=='string')throw Error('Unexpected frozen roster response.');
      const plan=JSON.parse(r.plan_json),work=JSON.parse(r.worklist_json);
      if(plan.format!=='openworkgraph.native-shadow-roster.v1'||plan.execution_authorized!==false||plan.plan_sha256!==r.plan_sha256||plan.package_sha256!==p||!Array.isArray(plan.cases)||!Array.isArray(work)||!Number.isInteger(r.case_count)||r.case_count<1||r.case_count>50||plan.cases.length!==r.case_count||work.length!==r.case_count||work.some((x,i)=>x.case_id!==plan.cases[i].case_id||x.root_execution_id!==null))throw Error('Unexpected roster population.');
      frozen=r;before.textContent='Frozen '+r.case_count+' planned cases. Plan SHA-256: '+r.plan_sha256+'. Download both files and record the pin independently before testing.';
    }catch(e){before.textContent=e.message;}finally{busy=false;refresh();}
  };
  savePlan.onclick=()=>{if(frozen&&!busy)download('native-shadow-plan.json',frozen.plan_json);};
  saveWorklist.onclick=()=>{if(frozen&&!busy)download('native-shadow-assignments.json',frozen.worklist_json);};
  review.onclick=async()=>{
    if(review.disabled)return;
    invalidateReview();busy=true;refresh();after.textContent='Reconciling all planned cases against the exact trace.';
    try{
      const pp=planPin.value.trim(),tp=tracePin.value.trim();
      const r=await post('/v1/native-shadow-roster/review',{plan_json:await read(planFile,256*1024),plan_sha256:pp,trace_json:await read(traceFile,8*1024*1024),trace_sha256:tp,assignments_json:await read(assignments,256*1024)});
      if(r.execution_authorized!==false||!hex(r.review_sha256)||typeof r.report_json!=='string')throw Error('Unexpected roster review response.');
      const q=JSON.parse(r.report_json),statuses=['not_assigned','root_not_returned','incomplete_observation','observed_deviation_or_failure','no_deviation_in_returned_events'];
      if(q.format!=='openworkgraph.native-shadow-roster-review.v1'||q.plan_sha256!==pp||q.trace_sha256!==tp||q.review_sha256!==r.review_sha256||q.execution_authorized!==false||q.business_outcomes_verified!==false||q.skill_invocation_verified!==false||!Array.isArray(q.cases)||q.cases.length!==q.case_count||!Number.isInteger(q.case_count)||q.case_count<1||q.case_count>50||!q.counts||!Array.isArray(q.unassigned_returned_execution_ids)||q.cases.some(x=>!statuses.includes(x.status))||statuses.some(s=>q.counts[s]!==q.cases.filter(x=>x.status===s).length))throw Error('Unexpected review population.');
      result.append(el('h4',q.case_count+' planned cases — structural trace review only'));
      const totals=el('ul');for(const s of statuses)totals.append(el('li',s.replaceAll('_',' ')+': '+q.counts[s]));result.append(totals);
      for(const row of q.cases){
        const section=el('section');section.append(el('h4',row.case_id+' — '+row.status.replaceAll('_',' ')),el('p',row.root_execution_id||'No assigned root execution.'));
        const o=row.trace_observation;if(o){const findings=el('ul');for(const f of [...o.coverage_gaps,...o.deviations,...o.failure_signals])findings.append(el('li',f.code+' · '+f.execution_id+(f.event_ref?' · '+f.event_ref:'')));section.append(findings);}
        result.append(section);
      }
      result.append(el('p',q.unassigned_returned_execution_ids.length+' unassigned returned runs. '+(q.response_run_limit_reached?'Response run limit reached. ':'')));
      report=r;after.textContent='Complete roster reconciled. Review SHA-256: '+r.review_sha256+'. No business outcomes, skill invocation or permissions verified.';
    }catch(e){result.replaceChildren();after.textContent=e.message;}finally{busy=false;refresh();}
  };
  saveReport.onclick=()=>{if(report&&!busy)download('native-shadow-roster-review.json',report.report_json);};
  const details=el('details');details.id='owgNativeShadowRoster';
  details.append(el('summary','Plan and reconcile a complete native AI shadow test'),el('h3','Before testing'),
    el('p','Verify the extracted skill package separately. Declare the held-out case IDs and independently reviewed framework and tool labels; these are not permissions. Keep the plan pin independently before running the cases.'),
    field('Original staged skill manifest JSON (256 KiB max)',manifest),field('Independently retained package SHA-256',packagePin),field('Planned cases JSON (256 KiB max)',cases),field('I confirm this is the full planned test roster',confirm),freeze,before,savePlan,saveWorklist,
    el('h3','After testing'),el('p','Use the exact original roster and trace downloaded from the native shadow evidence panel. Fill each worklist entry with the actual root ID or leave null; never delete cases. This does not verify enrollment timing, live outcomes, skill invocation or permission compliance.'),
    field('Original frozen plan JSON (256 KiB max)',planFile),field('Independently retained plan SHA-256',planPin),field('Exact trace snapshot JSON (8 MiB max)',traceFile),field('Independently retained trace SHA-256',tracePin),field('Completed assignment worklist JSON (256 KiB max)',assignments),review,after,result,saveReport);
  host.append(details);refresh();
}
