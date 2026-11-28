/* Join a frozen native roster with the existing declared project source cohort. */
export function mountJointShadowSourceReview(host,{api,context,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.style.margin='0.5rem 0';n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const plan=file(),trace=file(),assignments=file(),planPin=el('input'),tracePin=el('input');
  for(const n of [planPin,tracePin]){n.maxLength=64;n.autocomplete='off';n.placeholder='Independently retained SHA-256';}
  const run=button('Review native and source evidence together'),save=button('Download joint evidence review JSON');
  const status=el('p','Declare every frozen case in the source cohort above, including missing documents.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const result=el('div'),controls=[plan,trace,assignments,planPin,tracePin];
  let generation=0,busy=false,report=null,basis=null;
  function refresh(){controls.forEach(n=>n.disabled=busy);run.disabled=busy;save.disabled=busy||!report;}
  function invalidate(){generation++;report=null;basis=null;result.replaceChildren();status.textContent='Review changed inputs before using a joint report.';refresh();}
  for(const n of controls)n.addEventListener(n.type==='file'?'change':'input',invalidate);
  async function original(node,limit){
    const f=node.files?.[0];
    if(!f||!f.size||f.size>limit)throw Error('Choose original JSON files within the stated size limits.');
    return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());
  }
  run.onclick=async()=>{
    if(busy)return;
    const c=context(),pp=planPin.value.trim(),tp=tracePin.value.trim();
    if(!c||!c.payload.cases.length){status.textContent='Declare the complete source cohort for the selected project first.';return;}
    if(!/^[a-f0-9]{64}$/.test(pp)||!/^[a-f0-9]{64}$/.test(tp)){status.textContent='Enter both independently retained exact SHA-256 pins.';return;}
    invalidate();const ticket=generation;busy=true;refresh();
    try{
      const data={plan_json:await original(plan,256*1024),plan_sha256:pp,
        trace_json:await original(trace,8*1024*1024),trace_sha256:tp,
        assignments_json:await original(assignments,256*1024),source_cohort:c.payload};
      if(ticket!==generation||!isCurrent(c))return;
      const response=await api(c.project+'/joint-shadow-source-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      const value=await response.json();
      if(ticket!==generation||!isCurrent(c))return;
      const expected=c.payload.cases.map(x=>x.case_id).sort();
      if(value.format!=='openworkgraph.joint-shadow-source-review.v1'||value.plan_sha256!==pp||value.trace_sha256!==tp||
        value.idea_revision!==c.payload.expected_idea_revision||value.exact_declared_case_ids_match!==true||
        value.execution_authorized!==false||value.business_outcomes_verified!==false||value.case_association_verified!==false||
        !Array.isArray(value.cases)||JSON.stringify(value.cases.map(x=>x.case_id).sort())!==JSON.stringify(expected))
        throw Error('Unexpected joint review response.');
      const table=el('table'),head=el('thead'),header=el('tr'),body=el('tbody');
      for(const label of ['Declared case','Returned native observation','Historical source evidence','Local source reference']){const th=el('th',label);th.scope='col';header.append(th);}
      head.append(header);table.append(head,body);
      for(const row of value.cases){
        const tr=el('tr');
        for(const text of [row.case_id,row.native_observation_status.replaceAll('_',' '),
          row.historical_source_status.replaceAll('_',' '),row.source_reference_needs_review===null?'No document':row.source_reference_needs_review?'Needs review':'No flagged change'])tr.append(el('td',text));
        body.append(tr);
      }
      result.append(el('p',value.case_count+' exact declared cases · '+value.unassigned_returned_execution_count+' unassigned returned executions.'),table,
        el('p',(value.response_run_limit_reached?'Response run limit reached. ':'')+'No business outcome, skill invocation, case association or authorization verified.'));
      report=value;basis=c;status.textContent='Joint review complete. Evidence dimensions remain separate; this is not an outcome grade.';
    }catch(e){if(ticket===generation&&isCurrent(c))status.textContent=e.message;}finally{busy=false;refresh();}
  };
  save.onclick=()=>{
    if(!report||busy||!basis||!isCurrent(basis))return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');
    a.href=url;a.download='owg-joint-shadow-source-review.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const details=el('details');details.id='owgJointShadowSourceReview';
  details.append(el('summary','Compare native shadow and source-state evidence for the frozen cohort'),
    el('p','Recomputes both existing reviews on the exact same declared case IDs (maximum 25). Matching IDs do not prove case association. No new provider read or business action. Keep the original artifacts independently.'),
    field('Original frozen shadow roster JSON (max 256 KiB)',plan),field('Frozen roster SHA-256',planPin),
    field('Original native trace JSON (max 8 MiB)',trace),field('Native trace SHA-256',tracePin),
    field('Original completed assignment worklist JSON (max 256 KiB)',assignments),run,status,result,save);
  host.append(details);refresh();
  return invalidate;
}
