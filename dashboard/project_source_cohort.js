/* Reconcile original retained source checks without a fresh provider read. */
export function mountProjectSourceCohort(host,{api,base,revision,expectations,isCurrent,projectSelect}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.style.margin='0.5rem 0';n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const caseId=el('input');caseId.placeholder='case_1';caseId.maxLength=69;
  const expectation=el('select'),resource=el('input'),file=el('input');
  resource.placeholder='owg:r:… or empty when unknown';resource.maxLength=38;resource.autocomplete='off';
  file.type='file';file.accept='.json,application/json';
  const add=button('Add declared source-check case'),run=button('Reconcile declared cohort'),save=button('Download cohort review JSON');
  const list=el('ul'),result=el('div'),status=el('p','Declare all intended cases, including missing documents.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  let cases=[],report=null,busy=false,generation=0;
  const details=el('details');details.id='owgProjectSourceCohort';
  function invalidate(){generation++;report=null;result.replaceChildren();save.disabled=true;}
  function reset(){invalidate();cases=[];caseId.value='';resource.value='';file.value='';status.textContent='Select the project and declare the full cohort.';refresh();}
  function refresh(){
    caseId.disabled=resource.disabled=file.disabled=expectation.disabled=busy;
    add.disabled=busy||!projectSelect.value||!expectation.options.length||cases.length>=25;
    run.disabled=busy||!projectSelect.value||!cases.length;save.disabled=busy||!report;
    list.replaceChildren();
    for(const entry of cases){
      const row=el('li',entry.case.case_id+' · '+entry.case.expected_operation+' · '+(entry.case.document_sha256?'original document selected':'missing document')+' ');
      const remove=button('Remove '+entry.case.case_id);remove.disabled=busy;
      remove.onclick=()=>{cases=cases.filter(x=>x!==entry);invalidate();refresh();};row.append(remove);list.append(row);
    }
  }
  function updateExpectations(){
    expectation.replaceChildren();
    for(const e of expectations()){
      const opt=el('option',e.activity_label+' · '+e.operation);opt.value=JSON.stringify([e.terminal_activity_id,e.operation]);expectation.append(opt);
    }
    refresh();
  }
  projectSelect.addEventListener('change',()=>{reset();expectation.replaceChildren();refresh();});
  details.addEventListener('toggle',()=>{if(details.open)updateExpectations();});
  const update=button('Load confirmed project expectations');update.onclick=updateExpectations;
  add.onclick=async()=>{
    if(busy||!isCurrent()||!expectation.value)return;
    const project=base(),ticket=generation,id=caseId.value.trim(),ref=resource.value.trim()||null,chosen=file.files?.[0];
    const [terminal,op]=JSON.parse(expectation.value);
    if(!/^case_[A-Za-z0-9_-]{1,64}$/.test(id)||cases.some(e=>e.case.case_id===id)||
      (ref!==null&&!/^owg:r:[a-f0-9]{32}$/.test(ref))||(chosen&&!ref)||
      (ref&&cases.some(e=>e.case.resource_ref===ref&&e.case.expected_operation===op))){
      status.textContent='Use a unique case ID and distinct record/operation; a supplied file requires its exact record reference.';return;
    }
    busy=true;refresh();
    try{
      let raw=null,pin=null;
      if(chosen){
        if(!chosen.size||chosen.size>65536)throw Error('Choose an original JSON file of at most 64 KiB.');
        const bytes=await chosen.arrayBuffer();
        raw=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
        pin=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
        if(cases.some(e=>e.case.document_sha256===pin))throw Error('Original document already selected.');
      }
      if(!isCurrent()||ticket!==generation||project!==base())return;
      cases.push({case:{case_id:id,terminal_activity_id:terminal,expected_operation:op,resource_ref:ref,document_sha256:pin},raw});
      invalidate();caseId.value='';resource.value='';file.value='';status.textContent='Case added; include every intended case before reviewing.';
    }catch(e){if(ticket===generation&&isCurrent())status.textContent=e.message;}finally{busy=false;refresh();}
  };
  run.onclick=async()=>{
    if(busy||!cases.length||!isCurrent())return;
    invalidate();const ticket=generation,project=base(),payload={expected_idea_revision:revision,
      cases:cases.map(x=>x.case),documents:cases.filter(x=>x.raw!==null).map(x=>x.raw)};
    busy=true;refresh();
    try{
      const response=await api(project+'/source-check-cohort-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const value=await response.json();
      if(ticket!==generation||project!==base()||!isCurrent())return;
      if(value.format!=='openworkgraph.project-source-cohort-review.v1'||value.idea_revision!==revision||
        value.execution_authorized!==false||value.business_outcomes_verified!==false||value.case_association_verified!==false||
        !Array.isArray(value.cases)||value.cases.length!==payload.cases.length||
        value.cases.some((x,i)=>x.case_id!==payload.cases[i].case_id||x.document_sha256!==payload.cases[i].document_sha256))
        throw Error('Unexpected cohort review response.');
      const rows=el('ul');
      for(const row of value.cases)rows.append(el('li',row.case_id+' · '+row.status.replaceAll('_',' ')+(row.local_reference_needs_review?' · local reference needs review':'')));
      result.append(el('p',value.case_count+' declared cases · '+value.historical_operation_reported_verified_count+' historical operations reported verified · '+value.historical_operation_not_established_count+' not established · '+value.missing_document_count+' missing documents'),rows,
        el('p',value.local_reference_needs_review_count+' historical document references need local review. No fresh source read or business outcome verified.'));
      report=value;status.textContent='Cohort reviewed. Historical source state is not a verified business outcome.';
    }catch(e){if(ticket===generation&&isCurrent())status.textContent=e.message;}finally{busy=false;refresh();}
  };
  save.onclick=()=>{
    if(!report||busy||!isCurrent())return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');
    a.href=url;a.download='owg-source-check-cohort-review.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  details.append(el('summary','Reconcile source checks for a declared test cohort'),
    el('p','One confirmed terminal operation per case, up to 25 cases. Add missing cases too. Original files are checked against retained export hashes; no provider is contacted. This does not establish cohort completeness, case association or business outcomes.'),
    update,field('Declared case ID',caseId),field('Case terminal expectation',expectation),
    field('Case observed record reference',resource),field('Original case source-check document (optional)',file),
    add,list,run,status,result,save);
  host.append(details);refresh();
}
