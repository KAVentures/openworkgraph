/* Explicit native shadow snapshot and human-declared case review. */
export function mountNativeShadowReview(host) {
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.style.margin='0.5rem 0';n.append(node);return n;};
  const input=(placeholder)=>{const n=el('input');n.placeholder=placeholder;return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const since=input('2026-11-28T09:00:00Z'),until=input('2026-11-28T10:00:00Z');
  const include=el('input');include.type='checkbox';
  const pin=input('64-character package SHA-256');pin.maxLength=64;
  const root=el('select'),caseId=input('case_1'),framework=input('Exact framework label'),tools=el('textarea');
  caseId.maxLength=69;framework.maxLength=200;tools.rows=3;
  const capture=button('Capture exact trace snapshot'),add=button('Add declared case'),review=button('Review declared shadow cases');
  const saveTrace=button('Download exact trace JSON'),saveDeclaration=button('Download declaration JSON'),saveReport=button('Download review JSON');
  const status=el('p','Select the explicit test window. No capture is requested automatically.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const snapshotInfo=el('p'),caseList=el('ul'),result=el('div');
  snapshotInfo.style.overflowWrap='anywhere';
  let snapshot=null,runs=[],cases=[],receipt=null,busy=false;
  const controls=[since,until,include,pin,root,caseId,framework,tools];
  function download(name,text){
    const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),a=el('a');
    a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const declaration=()=>({package_sha256:pin.value.trim(),cases:cases.map(c=>({...c,reviewed_tool_labels:[...c.reviewed_tool_labels]}))});
  const validDeclaration=()=>!!snapshot&&/^[a-f0-9]{64}$/.test(pin.value.trim())&&cases.length>0;
  function refreshControls(){
    controls.forEach(n=>{n.disabled=busy;});
    capture.disabled=busy;add.disabled=busy||!snapshot||!runs.length||cases.length>=50;
    review.disabled=busy||!validDeclaration();saveTrace.disabled=busy||!snapshot;
    saveDeclaration.disabled=busy||!validDeclaration();saveReport.disabled=busy||!receipt;
    caseList.replaceChildren();
    for(const c of cases){
      const row=el('li',c.case_id+' · '+c.root_execution_id+' · '+c.expected_framework+' · '+c.reviewed_tool_labels.length+' reviewed labels ');
      const remove=button('Remove '+c.case_id);remove.disabled=busy;
      remove.onclick=()=>{cases=cases.filter(x=>x!==c);invalidateReview();refreshControls();};
      row.append(remove);caseList.append(row);
    }
  }
  function invalidateReview(){receipt=null;result.replaceChildren();status.textContent='Review changed declarations before using a report.';}
  function clearSnapshot(){snapshot=null;runs=[];cases=[];root.replaceChildren();framework.value='';snapshotInfo.textContent='';invalidateReview();refreshControls();}
  for(const n of [since,until,include])n.addEventListener('change',clearSnapshot);
  for(const n of [since,until])n.addEventListener('input',clearSnapshot);
  pin.addEventListener('input',()=>{invalidateReview();refreshControls();});
  root.onchange=()=>{framework.value='';};
  async function request(path,init){
    await window.__owgAuthReady;
    const r=await fetch(path,{cache:'no-store',...init});
    if(!r.ok){let detail='';try{detail=(await r.json()).detail;}catch(_){}throw Error(typeof detail==='string'?detail:'Request refused ('+r.status+').');}
    return r.json();
  }
  capture.onclick=async()=>{
    if(busy)return;
    clearSnapshot();busy=true;refreshControls();status.textContent='Retrieving bounded local trace evidence.';
    try{
      const query=new URLSearchParams({since:since.value.trim(),until:until.value.trim(),include_disconnected:String(include.checked)});
      const s=await request('/v1/native-shadow-review/snapshot?'+query);
      if(s.format!=='openworkgraph.native-shadow-snapshot.v1'||s.execution_authorized!==false||
        !/^[a-f0-9]{64}$/.test(s.trace_sha256)||typeof s.trace_json!=='string')throw Error('Unexpected snapshot response.');
      const parsed=JSON.parse(s.trace_json);
      if(!Array.isArray(parsed.executions)||parsed.executions.length!==s.returned)throw Error('Unexpected run inventory.');
      snapshot=s;runs=parsed.executions.filter(r=>r.parent_execution_id===null);
      for(const r of runs){const option=el('option',r.execution_id+' · '+(r.agent?.framework||'unknown framework'));option.value=r.execution_id;root.append(option);}
      root.onchange();
      snapshotInfo.textContent='Snapshot '+s.trace_sha256+' · '+s.returned+' returned executions'+
        (s.source_row_limit_may_have_been_reached?' · source row limit may have been reached':'')+
        (parsed.agent_execution_count_considered>s.returned?' · response run limit reached':'');
      status.textContent=runs.length?'Snapshot captured. Declare the actual case associations and reviewed tool display labels.':'No root executions returned; choose another window.';
    }catch(e){status.textContent=e.message;}finally{busy=false;refreshControls();}
  };
  add.onclick=()=>{
    if(busy||!snapshot)return;
    const id=caseId.value.trim(),rid=root.value,fw=framework.value.trim(),labels=tools.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    if(!/^case_[a-zA-Z0-9_-]{1,64}$/.test(id)||!runs.some(r=>r.execution_id===rid)||!fw||
       fw.length>200||labels.length>100||labels.some(x=>x.length>200)||new Set(labels).size!==labels.length||
       cases.some(c=>c.case_id===id||c.root_execution_id===rid)||cases.length>=50){
      status.textContent='Use a unique case ID and root, expected framework and up to 100 unique reviewed labels.';return;
    }
    cases.push({case_id:id,root_execution_id:rid,expected_framework:fw,reviewed_tool_labels:labels});
    caseId.value='';invalidateReview();refreshControls();
  };
  review.onclick=async()=>{
    if(busy||!validDeclaration())return;
    const d=declaration(),s=snapshot;invalidateReview();busy=true;refreshControls();
    try{
      const r=await request('/v1/native-shadow-review/review',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({trace_json:s.trace_json,trace_sha256:s.trace_sha256,declaration:d})});
      if(r.format!=='openworkgraph.native-shadow-trace-review.v1'||r.trace_sha256!==s.trace_sha256||
        r.declared_package_sha256!==d.package_sha256||r.execution_authorized!==false||r.skill_invocation_verified!==false||
        r.business_outcome_verified!==false||!Array.isArray(r.cases)||r.cases.length!==d.cases.length)throw Error('Unexpected review response.');
      receipt=r;
      for(const c of r.cases){
        const section=el('section');section.append(el('h4',c.case_id+' — '+c.status.replaceAll('_',' ')),
          el('p',c.observed_tool_call_count+' returned tool calls; '+c.coverage_gaps.length+' coverage gaps; '+c.deviations.length+' deviations; '+c.failure_signals.length+' failure signals.'));
        const findings=el('ul');
        for(const f of [...c.coverage_gaps,...c.deviations,...c.failure_signals])findings.append(el('li',f.code+' · '+f.execution_id+(f.event_ref?' · '+f.event_ref:'')));
        section.append(findings);result.append(section);
      }
      result.append(el('p',r.unassigned_returned_execution_ids.length+' unassigned returned runs. '+(r.response_run_limit_reached?'Response run limit reached. ':'')));
      status.textContent='Review complete for returned evidence only. No skill invocation, permission compliance or business outcome verified.';
    }catch(e){status.textContent=e.message;}finally{busy=false;refreshControls();}
  };
  saveTrace.onclick=()=>{if(snapshot&&!busy)download('native-shadow-trace.json',snapshot.trace_json);};
  saveDeclaration.onclick=()=>{if(validDeclaration()&&!busy)download('native-shadow-declaration.json',JSON.stringify(declaration(),null,2));};
  saveReport.onclick=()=>{if(receipt&&!busy)download('native-shadow-review.json',JSON.stringify(receipt,null,2));};
  const details=el('details');details.id='owgNativeShadowReview';
  details.append(el('summary','Review native shadow-test evidence'),
    el('p','Read-only local snapshot. Associations are your declarations; reported tool labels are not permissions. Missing activity is not proof of no action. The existing source read is bounded before filtering, so a smaller returned count does not prove complete coverage.'),
    field('Test window start (ISO 8601 with offset)',since),field('Test window end (ISO 8601 with offset)',until),
    field('Include disconnected integrations in retained history',include),capture,snapshotInfo,saveTrace,
    field('Staged package SHA-256',pin),field('Case ID',caseId),field('Root execution',root),
    field('Expected framework (declare independently of observed label)',framework),field('Reviewed exact tool display labels (one per line; empty means none)',tools),
    add,caseList,saveDeclaration,review,status,result,saveReport);
  host.append(details);refreshControls();
}
