/* Prospective triage review: explicit snapshot, immutable policy, no business action. */
(() => {
  'use strict';
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);if(cls)n.className=cls;return n;};
  const button=(name,id)=>{const n=el('button',name,'secondary');n.type='button';n.id=id;return n;};
  const input=(id,type,value)=>{const n=el('input');n.id=id;n.type=type;if(value!==undefined)n.value=value;return n;};
  const field=(name,node)=>{const wrap=el('div');const label=el('label',name);label.htmlFor=node.id;wrap.append(label,node);return wrap;};
  const option=(value,label)=>{const n=el('option',label);n.value=value;return n;};
  const select=(id,options)=>{const n=el('select');n.id=id;for(const [v,l] of options)n.append(option(v,l));return n;};
  const numeric=(id,min,max,value)=>{const n=input(id,'number',value);n.min=min;n.max=max;n.step='1';return n;};
  const range=(values)=>values?values.map(v=>v===null?'undefined':String(v)).join(' – '):'—';
  const pct=(values)=>values?values.map(v=>v===null?'undefined':(v*100).toFixed(1)+'%').join(' – '):'—';
  async function call(path,payload){
    await window.__owgAuthReady;
    const r=await fetch(path,{cache:'no-store',...(payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Local triage request refused ('+r.status+').');
    return data;
  }
  function table(headers,rows){
    const wrapper=el('div',null,'table-wrap'),t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');
    for(const text of headers){const n=el('th',text);n.scope='col';hr.append(n);}
    h.append(hr);
    for(const row of rows){const tr=el('tr');for(const v of row)tr.append(el('td',v??'—'));b.append(tr);}
    t.append(h,b);wrapper.append(t);return wrapper;
  }
  function download(report){
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
    const a=el('a');a.href=url;a.download='owg-prospective-triage-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function mount(anchor){
    const root=el('section');root.id='owgTriagePolicyWorkbench';
    const details=el('details');details.append(el('summary','Prospective triage policy review'));
    const log=select('owgTriageLog',[['','Choose a retained log']]);
    const snapshot=select('owgTriageSnapshot',[['','Choose a saved prediction snapshot']]);
    const saved=select('owgTriageSaved',[['','Choose a frozen policy']]);
    const load=button('Load retained logs','owgTriageLoad');
    const refresh=button('Refresh snapshots and policies','owgTriageRefresh');
    const preview=button('Preview triage allocation','owgTriagePreview');
    const freeze=button('Freeze reviewed policy','owgTriageFreeze');
    const score=button('Score frozen policy','owgTriageScore');
    const erase=button('Delete frozen policy','owgTriageDelete');
    const save=button('Download aggregate report','owgTriageExport');
    const status=el('p');status.id='owgTriageStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const previewOut=el('div');previewOut.id='owgTriagePreviewResult';
    const reportOut=el('div');reportOut.id='owgTriageReport';
    const evidenceOut=el('div');evidenceOut.id='owgTriageEvidence';
    const title=input('owgTriageLabel','text');title.maxLength=80;
    const target=select('owgTriageTarget',[['remaining_time_over','Remaining time exceeds a threshold'],['frozen_outcome','Saved outcome risk']]);
    const hours=numeric('owgTriageHours',1,277777,'24');
    const cap=numeric('owgTriageCapacity',1,5000,'10');
    const seed=numeric('owgTriageSeed',0,2147483647,'17');
    const fp=numeric('owgTriageFP',0,1000000,'1');
    const fn=numeric('owgTriageFN',0,1000000,'1');
    const minimum=numeric('owgTriageMinimum',5,500,'10');
    const hoursField=field('Remaining time threshold (hours)',hours);
    let version=0,previewed=null,report=null,policies=[],busy=false,evidenceGeneration=0;
    const logPath=()=>'/v1/event-logs/'+encodeURIComponent(log.value)+'/prediction-snapshots';
    const base=()=>logPath()+'/'+encodeURIComponent(snapshot.value)+'/triage-policies';
    const policyPath=()=>base()+'/'+encodeURIComponent(saved.value);
    const active=n=>root.isConnected&&n===version;
    function clearReport(){report=null;reportOut.replaceChildren();evidenceOut.replaceChildren();++evidenceGeneration;save.disabled=true;}
    function clearPreview(){previewed=null;previewOut.replaceChildren();freeze.disabled=true;}
    function invalidate(){++version;clearPreview();clearReport();busy=false;controls();}
    function controls(){
      const ready=!!(log.value&&snapshot.value);
      load.disabled=busy;refresh.disabled=busy||!log.value;
      preview.disabled=busy||!ready;freeze.disabled=busy||!previewed;
      score.disabled=erase.disabled=busy||!ready||!saved.value;
      save.disabled=busy||!report;
    }
    function start(message){busy=true;controls();status.textContent=message;return ++version;}
    function finish(n){if(active(n)){busy=false;controls();}}
    function readInteger(n){const v=Number(n.value);if(!n.value||!Number.isInteger(v)||v<Number(n.min)||v>Number(n.max))throw Error('Enter valid whole-number policy assumptions.');return v;}
    function spec(){
      if(!title.value.trim())throw Error('Enter a policy label.');
      return {label:title.value.trim(),target:target.value,remaining_seconds:target.value==='remaining_time_over'?readInteger(hours)*3600:null,
        capacity:readInteger(cap),tie_seed:readInteger(seed),false_positive_cost:readInteger(fp),
        false_negative_cost:readInteger(fn),minimum_group_cases:readInteger(minimum)};
    }
    for(const n of [title,target,hours,cap,seed,fp,fn,minimum])n.addEventListener('input',()=>{
      hoursField.hidden=target.value!=='remaining_time_over';invalidate();
      status.textContent='Review changed policy assumptions again before freezing.';
    });
    function setOptions(node,rows,prompt,keep){
      node.replaceChildren(option('',prompt));
      for(const row of rows)node.append(option(row.value,row.label));
      if(rows.some(r=>r.value===keep))node.value=keep;
    }
    async function loadPolicies(n,keep=''){
      if(!snapshot.value)return;
      const data=await call(base());
      if(!active(n))return;
      policies=data.policies;
      setOptions(saved,policies.map(p=>({value:p.policy_id,label:(p.policy?.label||'Unavailable policy')+' · '+p.created_at})),
        'Choose a frozen policy',keep);
      status.textContent=policies.length+' frozen policies in the selected snapshot.';
    }
    async function loadSnapshots(n,keep=''){
      const data=await call(logPath());
      if(!active(n))return;
      setOptions(snapshot,data.snapshots.map(s=>({value:s.snapshot_id,label:s.as_of+' · '+s.predicted_cases+' supported predictions'})),
        'Choose a saved prediction snapshot',keep);
      if(snapshot.value)await loadPolicies(n);
      else status.textContent=data.snapshots.length+' snapshots. Select one to review.';
    }
    load.onclick=async()=>{
      invalidate();const n=start('Reading retained local logs…');const keep=log.value;
      try{
        const data=await call('/v1/event-logs');
        if(!active(n))return;
        setOptions(log,data.logs.map(l=>({value:l.log_id,label:l.name+' · '+l.events+' events'})),'Choose a retained log',keep);
        setOptions(snapshot,[],'Choose a saved prediction snapshot');setOptions(saved,[],'Choose a frozen policy');policies=[];
        if(log.value)await loadSnapshots(n);
        else status.textContent=data.logs.length+' local logs available.';
      }catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    log.onchange=async()=>{
      invalidate();setOptions(snapshot,[],'Choose a saved prediction snapshot');setOptions(saved,[],'Choose a frozen policy');policies=[];
      if(!log.value)return;
      const n=start('Loading saved prediction snapshots…');
      try{await loadSnapshots(n);}catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    refresh.onclick=async()=>{
      const keep=snapshot.value;invalidate();setOptions(snapshot,[],'Choose a saved prediction snapshot');setOptions(saved,[],'Choose a frozen policy');policies=[];
      const n=start('Refreshing current saved snapshot inventory…');
      try{await loadSnapshots(n,keep);}catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    snapshot.onchange=async()=>{
      invalidate();setOptions(saved,[],'Choose a frozen policy');policies=[];
      if(!snapshot.value)return;
      const n=start('Loading frozen policies…');
      try{await loadPolicies(n);}catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    saved.onchange=()=>{invalidate();status.textContent='Selected frozen policy. Score it against the current local evidence.';};
    preview.onclick=async()=>{
      clearPreview();clearReport();let policy;
      try{policy=spec();}catch(e){status.textContent=e.message;return;}
      const n=start('Preparing exact equal-capacity allocations…');
      try{
        const data=await call(base()+'/preview',policy);if(!active(n))return;
        previewed=data;
        previewOut.append(el('h4','Reviewed allocation before future observations'),
          el('p',data.paired_eligible_cases+' paired eligible of '+data.snapshot_open_cases+' frozen open cases; '+data.snapshot_unsupported_open_cases+
            ' unsupported, '+data.paired_estimate_excluded_cases+' invalid or missing paired estimates.'),
          el('p','Capacity '+data.effective_capacity+' per ranking; requested '+data.requested_capacity+'.'),
          table(['Selected by both','Model only','Baseline only','Neither'],[[data.assignment_counts.both,data.assignment_counts.model_only,data.assignment_counts.baseline_only,data.assignment_counts.neither]]),
          el('p','Cutoff ties: model '+data.model_cutoff_ties+', naive '+data.baseline_cutoff_ties+'. Both use the same explicit seed.','muted'),
          el('p','Exact policy digest: '+data.policy_sha256,'muted'));
        status.textContent='Review the allocation and freeze it explicitly before new observations arrive.';
      }catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    freeze.onclick=async()=>{
      if(!previewed||!confirm('Freeze this exact policy before further recorded observations? This records a hypothetical review allocation only and takes no business action.'))return;
      const chosen=previewed,n=start('Freezing reviewed policy…');
      try{
        const data=await call(base(),{policy:chosen.policy,expected_policy_sha256:chosen.policy_sha256,confirm_freeze:true});
        if(!active(n))return;
        clearPreview();await loadPolicies(n,data.policy_id);
        if(active(n))status.textContent='Frozen policy saved. No intervention was executed.';
      }catch(e){if(active(n)){clearPreview();status.textContent=e.message;}}finally{finish(n);}
    };
    function summaryTable(s){
      return table(['Measure','Model ranking','Naive ranking'],[
        ['Selected cases',s.model.selected,s.baseline.selected],
        ['Known true positives',s.model.known_confusion.tp,s.baseline.known_confusion.tp],
        ['Known false positives',s.model.known_confusion.fp,s.baseline.known_confusion.fp],
        ['Known false negatives',s.model.known_confusion.fn,s.baseline.known_confusion.fn],
        ['Known true negatives',s.model.known_confusion.tn,s.baseline.known_confusion.tn],
        ['Unknown selected',s.model.unknown_selected,s.baseline.unknown_selected],
        ['Unknown not selected',s.model.unknown_not_selected,s.baseline.unknown_not_selected],
        ['Eventual precision bounds',pct(s.model.eventual_precision_bounds),pct(s.baseline.eventual_precision_bounds)],
        ['Eventual recall bounds (when defined)',pct(s.model.eventual_recall_bounds_when_defined),pct(s.baseline.eventual_recall_bounds_when_defined)],
        ['Declared error cost bounds',range(s.model.eventual_error_cost_bounds),range(s.baseline.eventual_error_cost_bounds)]
      ]);
    }
    function strata(name,groups){
      const section=el('details');section.append(el('summary',name+' ('+groups.groups.length+' shown)'),
        el('p',groups.withheld_groups+' groups and '+groups.withheld_cases+' cases withheld by minimum support or display limit.','muted'),
        table(['Frozen group','Eligible','Known','Model selected','Naive selected','Paired captured-positive change','Paired error cost change'],
          groups.groups.map(g=>[g.group,g.eligible_cases,g.known_labels,g.model.selected,g.baseline.selected,
            range(g.paired.captured_positive_change_bounds),range(g.paired.model_minus_baseline_error_cost_bounds)])));
      return section;
    }
    function renderEvidence(){
      const filters=select('owgTriageEvidenceFilter',[
        ['all','All paired eligible cases'],['model_only','Model only'],['baseline_only','Naive only'],['both','Both selected'],
        ['neither','Neither selected'],['unknown','Unknown label'],['known_positive','Known positive'],['known_negative','Known negative'],
        ['missing_or_inconsistent','Missing or inconsistent'],['excluded','Invalid paired estimate']]);
      const prev=button('Previous cases','owgTriageEvidencePrev'),next=button('Next cases','owgTriageEvidenceNext'),
        open=button('Load exact case evidence','owgTriageEvidenceLoad');
      const meta=el('p');meta.setAttribute('role','status');const body=el('div');
      let offset=0;
      prev.disabled=next.disabled=true;
      async function page(at){
        const current=report;if(!current)return;
        const g=++evidenceGeneration,n=version;prev.disabled=next.disabled=true;body.replaceChildren();
        meta.textContent='Reading exact pinned case evidence…';
        try{
          const data=await call(policyPath()+'/evidence',{expected_evaluation_sha256:current.evaluation_sha256,filter:filters.value,offset:at,limit:50});
          if(!active(n)||g!==evidenceGeneration||report!==current)return;
          offset=data.offset;
          meta.textContent=data.matched_cases+' matching cases; showing '+data.rows.length+' from offset '+offset+'.';
          if(filters.value==='excluded')body.append(table(['Case','Reason'],data.rows.map(r=>[r.case_id,r.reason])));
          else body.append(table(['Case','Frozen activity','Model rank','Naive rank','Model score','Naive score','Assignment','Observed status','Label'],
            data.rows.map(r=>[r.case_id,r.activity,r.model_rank,r.baseline_rank,r.model_score,r.baseline_score,r.assignment,r.observation,
              r.label===null?'Unknown':r.label===1?'Positive':'Negative'])));
          prev.disabled=offset===0;next.disabled=data.next_offset===null;
          next.onclick=()=>page(data.next_offset);
        }catch(e){if(active(n)&&g===evidenceGeneration)meta.textContent=e.message;}
      }
      open.onclick=()=>page(0);prev.onclick=()=>page(Math.max(0,offset-50));
      filters.onchange=()=>{++evidenceGeneration;offset=0;body.replaceChildren();meta.textContent='Load the selected evidence filter.';prev.disabled=next.disabled=true;};
      const section=el('details');section.append(el('summary','Exact local case evidence'),
        el('p','Case identifiers are displayed only after an explicit evidence request. A changed evaluation invalidates subsequent pages.','muted'),
        field('Evidence filter',filters),open,prev,next,meta,body);evidenceOut.replaceChildren(section);
    }
    score.onclick=async()=>{
      clearPreview();clearReport();const n=start('Scoring frozen allocations against retained observations…');
      try{
        const data=await call(policyPath()+'/evaluation');if(!active(n))return;
        report=data;const s=data.summary;
        reportOut.append(el('h4','Prospective policy evaluation'),
          el('p',s.eligible_cases+' eligible cases · '+s.known_labels+' known labels · '+s.unknown_labels+' unknown labels · '+s.known_positive_labels+' known positives.'),
          el('p','Current observation horizon: '+(data.evaluated_against||'unavailable')+'; frozen at '+data.as_of+'.'),
          el('p','Model minus naive captured positives: '+range(s.paired.captured_positive_change_bounds)+
            '. Declared error cost difference: '+range(s.paired.model_minus_baseline_error_cost_bounds)+'.'),
          summaryTable(s),el('p','Observation states: '+Object.entries(s.observation_counts).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join('; '),'muted'),
          strata('Frozen activity groups',data.by_frozen_activity),strata('Frozen model basis groups',data.by_frozen_model_basis),
          el('p',data.interpretation,'muted'));
        renderEvidence();status.textContent='Frozen policy scored. Bounds are not confidence intervals and no intervention was executed.';
      }catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    save.onclick=()=>{if(report)download(report);};
    erase.onclick=async()=>{
      const item=policies.find(p=>p.policy_id===saved.value);
      if(!item||!confirm('Delete this exact frozen policy? The prediction snapshot and local event log remain unchanged.'))return;
      const path=policyPath()+'/delete';clearPreview();clearReport();const n=start('Deleting exact frozen policy…');
      try{
        await call(path,{expected_policy_sha256:item.policy_sha256,confirm_delete:true});if(!active(n))return;
        await loadPolicies(n);if(active(n))status.textContent='Frozen policy deleted; source snapshot retained.';
      }catch(e){if(active(n))status.textContent=e.message;}finally{finish(n);}
    };
    details.append(el('p','Select a saved prediction snapshot, declare a fixed review capacity and error weights, then freeze before new observations arrive. Later compare model ranking with a same-capacity naive ranking. This is hypothetical triage, not an instruction to intervene.','muted'),
      load,field('Retained log',log),field('Saved prediction snapshot',snapshot),refresh,
      el('h4','Prepare a prospective policy'),field('Policy label',title),field('Target',target),hoursField,
      field('Maximum cases to review',cap),field('Deterministic tie seed',seed),
      field('False-positive error weight',fp),field('False-negative error weight',fn),field('Minimum cases per displayed subgroup',minimum),
      preview,freeze,previewOut,el('h4','Review frozen policies'),field('Frozen policy',saved),score,erase,save,status,reportOut,evidenceOut);
    root.append(details);anchor.append(root);controls();
  }
  function ensure(){
    const anchor=document.querySelector('#owgEventLogPredictions');
    if(anchor&&!anchor.querySelector('#owgTriagePolicyWorkbench'))mount(anchor);
  }
  function start(){
    ensure();
    const host=document.querySelector('main')||document.body;
    new MutationObserver(ensure).observe(host,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
