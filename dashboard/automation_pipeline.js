/* Automation pipeline: review candidate automations locally. Approval records a decision; it never runs anything. */
(() => {
  'use strict';
  const QUESTIONS=[['rules_based','Decisions follow clear rules'],['structured_inputs','Inputs are structured'],['process_stability','The process rarely changes'],['digital_inputs','Inputs arrive digitally'],['low_exception_rate','Exceptions are rare']];
  const STATE_LABELS={idea:'Idea',assessing:'Assessing',approved:'Approved',rejected:'Rejected',on_hold:'On hold',implemented:'Implemented'};
  const QUADRANTS={quick_win:'Quick win',major_project:'Major project',fill_in:'Fill-in',deprioritize:'Deprioritize'};
  const PLANNING_FILTERS=[['owner','Owner','No owner'],['team','Team','No team'],['target_quarter','Target quarter','No target quarter']];
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};
  const select=(id,options)=>{const node=document.createElement('select');node.id=id;options.forEach(([v,l])=>{const o=el('option',l);o.value=v;node.appendChild(o);});return node;};


  // A portable review artifact, never an execution grant or independently verified outcome.
  // Deliberately omit activity labels, raw cases, record refs, receipts and credentials.
  function handoffReviewPacket(journey,projectId,ideaId,createdAt){
    const project=(journey.projects||[]).find(item=>item.project_id===projectId);
    if(!project)throw new Error('Select a recorded project before preparing its review packet.');
    const process=journey.process_status||{},ref=process.process_reference||null;
    const origins=(journey.dataset_origins||[]).slice(0,64).map(item=>({
      status:item.status||'unknown',source_content_sha256:item.source_content_sha256||null,
      selection_sha256:item.selection_sha256||null
    }));
    const originsTruncated=(journey.dataset_origins||[]).length>64;
    const reportsTruncated=(project.reported_specifications||[]).length>64;
    const reported=(project.reported_specifications||[]).slice(0,64).map(item=>({
      status:item.status||'unknown',reported_run_count:Number.isSafeInteger(item.reported_run_count)&&item.reported_run_count>=0?item.reported_run_count:null
    }));
    const blockers=[];
    if(!ref)blockers.push('no_exact_saved_process');
    if(process.status!=='current')blockers.push('process_not_current');
    if(originsTruncated)blockers.push('dataset_origin_list_truncated');
    if(reportsTruncated)blockers.push('reported_specification_list_truncated');
    if(!origins.length)blockers.push('dataset_origin_not_linked');
    else if(origins.some(item=>item.status!=='current'))blockers.push('dataset_origin_not_current');
    if(!['approved','implemented'].includes(journey.idea?.state))blockers.push('idea_not_approved');
    if(!project.idea_revision_is_current)blockers.push('project_uses_historical_idea_revision');
    if(!reported.length)blockers.push('no_imported_report');
    else if(reported.some(item=>item.status!=='matching_reported_specification'))blockers.push('reported_specification_unverified');
    // A matching reported specification is not a verified execution or business outcome.
    blockers.push('execution_and_business_outcomes_unverified');
    return {
      format:'openworkgraph.evidence-handoff-review.v1',generated_at:createdAt,
      scope:'human_review_only',execution_authorized:false,source_read_authorized:false,
      idea:{idea_id:ideaId,revision:journey.idea?.revision??null,state:journey.idea?.state||'unknown'},
      process:{workflow_id:ref?.workflow_id||null,revision:ref?.revision??null,
        content_sha256:ref?.content_sha256||null,status:process.status||'unknown'},
      dataset_origins:origins,dataset_origins_truncated:originsTruncated,
      project:{project_id:project.project_id,idea_revision:project.idea_revision,
        idea_revision_is_current:!!project.idea_revision_is_current,
        spec_sha256:project.spec_sha256,archive_sha256:project.archive_sha256},
      reported_specifications:reported,reported_specifications_truncated:reportsTruncated,
      source_check_exports:{count:project.source_check_exports?.count||0,
        last_document_sha256:project.source_check_exports?.last_document_sha256||null},
      readiness:{human_review_required:true,blockers,verified_execution:false,verified_business_outcomes:false}
    };
  }


  // Read-only comparison of two pinned project exports in an authenticated journey.
  function compareRecordedProjects(journey,baselineId,candidateId){
    if(!journey||!Array.isArray(journey.projects)||journey.projects_truncated)
      throw new Error('Project history is incomplete; refresh a full journey before comparing.');
    if(typeof baselineId!=='string'||typeof candidateId!=='string'||!baselineId||
      !candidateId||baselineId===candidateId)
      throw new Error('Select two different recorded projects.');
    const a=journey.projects.find(p=>p.project_id===baselineId);
    const b=journey.projects.find(p=>p.project_id===candidateId);
    if(!a||!b)throw new Error('A selected project is unavailable; refresh before comparing.');
    const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
    const summary=p=>{
      if(!Number.isSafeInteger(p.idea_revision)||p.idea_revision<1||
        typeof p.idea_revision_is_current!=='boolean'||!sha(p.spec_sha256)||
        !sha(p.archive_sha256)||!Array.isArray(p.reported_specifications)||
        p.reported_specifications.length>64||!p.source_check_exports||
        !Number.isSafeInteger(p.source_check_exports.count)||p.source_check_exports.count<0||
        (p.source_check_exports.last_document_sha256!=null&&
          !sha(p.source_check_exports.last_document_sha256)))
        throw new Error('Project comparison metadata is invalid or incomplete.');
      const counts=new Map();
      for(const report of p.reported_specifications){
        if(typeof report?.status!=='string'||!/^[a-z_]{1,64}$/.test(report.status))
          throw new Error('A report status is invalid or unavailable.');
        counts.set(report.status,(counts.get(report.status)||0)+1);
      }
      return [String(p.idea_revision),p.idea_revision_is_current?'Yes':'No (historical)',
        p.spec_sha256,p.archive_sha256,
        [...counts].sort(([x],[y])=>x.localeCompare(y)).map(([s,n])=>s+': '+n).join(' · ')||'None recorded',
        String(p.source_check_exports.count),p.source_check_exports.last_document_sha256||'None recorded'];
    };
    const names=['Idea revision','Current idea revision','Specification SHA-256','ZIP SHA-256',
      'Reported specification status counts','Recorded source-check export count',
      'Latest source-check document SHA-256'];
    const left=summary(a),right=summary(b);
    const fields=names.map((label,i)=>({label,baseline:left[i],candidate:right[i],
      changed:left[i]!==right[i]}));
    return {baseline_project_id:baselineId,candidate_project_id:candidateId,
      fields,changed_count:fields.filter(f=>f.changed).length,execution_authorized:false,
      source_read_authorized:false,verified_execution:false,verified_business_outcomes:false};
  }

  // Read-only change in human review requirements between two recorded projects.
  // Disappearing blockers are NOT independently resolved or authorized.
  function compareProjectHandoffReadiness(journey,baselineId,candidateId,ideaId,createdAt){
    const comparison=compareRecordedProjects(journey,baselineId,candidateId);
    if(typeof ideaId!=='string'||!ideaId||ideaId.length>128||
      !Array.isArray(journey.dataset_origins)||journey.dataset_origins.length>64||
      journey.projects.some(p=>typeof p.project_id!=='string'||p.project_id.length>128))
      throw new Error('Review basis is incomplete or invalid. Refresh the journey.');
    const a=handoffDependencyReview(journey,baselineId,ideaId,createdAt);
    const b=handoffDependencyReview(journey,candidateId,ideaId,createdAt);
    if(a.basis.dataset_origins_truncated||b.basis.dataset_origins_truncated||
      a.basis.reports_truncated||b.basis.reports_truncated)
      throw new Error('Review coverage is truncated. Compare only a complete local journey.');
    const oldPacket=handoffReviewPacket(journey,baselineId,ideaId,createdAt);
    const newPacket=handoffReviewPacket(journey,candidateId,ideaId,createdAt);
    const old=new Set(oldPacket.readiness.blockers),next=new Set(newPacket.readiness.blockers);
    const stages=a.stages.map((row,index)=>{
      const newer=b.stages[index];
      if(!newer||newer.id!==row.id)throw new Error('Review stages cannot be compared.');
      return {id:row.id,label:row.label,baseline_status:row.status,
        candidate_status:newer.status,changed:row.status!==newer.status};
    });
    const identity=packet=>({
      project_id:packet.project.project_id,idea_revision:packet.project.idea_revision,
      spec_sha256:packet.project.spec_sha256,archive_sha256:packet.project.archive_sha256
    });
    return {
      format:'openworkgraph.project-handoff-readiness-comparison.v1',
      generated_at:createdAt,scope:'read_only_human_review',idea_id:ideaId,
      baseline:identity(oldPacket),candidate:identity(newPacket),
      artifact_fields_changed:comparison.changed_count,
      blockers:{introduced:[...next].filter(k=>!old.has(k)).sort(),
        no_longer_reported:[...old].filter(k=>!next.has(k)).sort(),
        still_outstanding:[...next].filter(k=>old.has(k)).sort()},
      stages,coverage:{baseline:a.coverage,candidate:b.coverage},
      execution_authorized:false,source_read_authorized:false,
      verified_execution:false,verified_business_outcomes:false,
      handoff_review_complete:false
    };
  }

  // A bounded, read-only dependency review of the exact selected project.
  // It is a reviewer aid, NOT an authoritative cross-store lineage graph.
  // Source status is the locally recorded status, never a provider receipt.
  function handoffDependencyReview(journey,projectId,ideaId,createdAt){
    const packet=handoffReviewPacket(journey,projectId,ideaId,createdAt);
    const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
    const project=packet.project,process=packet.process;
    if(!sha(project.spec_sha256)||!sha(project.archive_sha256)||
      !Number.isSafeInteger(project.idea_revision)||project.idea_revision<1||
      !Number.isSafeInteger(packet.idea.revision)||packet.idea.revision<1||
      !Number.isSafeInteger(packet.source_check_exports.count)||
      packet.source_check_exports.count<0||
      (packet.source_check_exports.last_document_sha256!==null&&
        !sha(packet.source_check_exports.last_document_sha256)))
      throw new Error('Project dependency evidence is incomplete or invalid. Refresh the journey.');
    const originCount=packet.dataset_origins.length;
    const originCurrent=originCount>0&&!packet.dataset_origins_truncated&&
      packet.dataset_origins.every(origin=>origin.status==='current');
    const processCurrent=process.status==='current'&&!!process.workflow_id&&
      Number.isSafeInteger(process.revision)&&process.revision>0&&sha(process.content_sha256);
    const ideaCurrent=['approved','implemented'].includes(packet.idea.state)&&
      project.idea_revision_is_current&&project.idea_revision===packet.idea.revision;
    const reportCount=packet.reported_specifications.length;
    const reportsMatching=reportCount>0&&!packet.reported_specifications_truncated&&
      packet.reported_specifications.every(report=>report.status==='matching_reported_specification');
    const checksRecorded=packet.source_check_exports.count>0&&
      sha(packet.source_check_exports.last_document_sha256);
    const rows=[];
    const add=(id,label,status,basis,note,depends_on=[])=>{
      const affected_by=[...new Set(depends_on.flatMap(dep=>{
        const prior=rows.find(row=>row.id===dep);
        if(!prior)throw new Error('Dependency review stage order is invalid.');
        return [...prior.affected_by,...(['locally_current','human_review_recorded','artifact_recorded'].includes(prior.status)?[]:[dep])];
      }))];
      rows.push({id,label,status,basis,note,depends_on,affected_by});
    };
    add('dataset','Dataset / cohort origin',
      originCurrent?'locally_current':originCount?'needs_review':'missing',
      originCount+' origin checks'+(packet.dataset_origins_truncated?' (truncated)':''),
      'Recorded source status is not independently verified completeness.');
    add('process','Saved process design',
      processCurrent?'locally_current':process.workflow_id?'needs_review':'missing',
      process.revision===null?'No exact revision':('Revision '+process.revision+
        ' · SHA-256 '+(sha(process.content_sha256)?process.content_sha256:'unavailable')),
      'The saved design digest is not proof of observed work.',['dataset']);
    add('opportunity','Human opportunity decision',
      ideaCurrent?'human_review_recorded':'needs_review',
      'Revision '+packet.idea.revision+' · '+packet.idea.state,
      'A recorded approval is not an agent execution grant.',['dataset','process']);
    add('project','Immutable agent project export',
      project.idea_revision_is_current?'artifact_recorded':'historical',
      'Idea revision '+project.idea_revision+' · spec '+project.spec_sha256+
        ' · ZIP '+project.archive_sha256,
      'Artifact hashes identify an export, not a deployed or executed agent.',
      ['dataset','process','opportunity']);
    add('reports','Imported execution reports',
      reportsMatching?'customer_reported_only':reportCount?'needs_review':'missing',
      reportCount+' reported specifications'+
        (packet.reported_specifications_truncated?' (truncated)':''),
      'Even a matching customer-reported specification cannot prove individual execution.',
      ['project']);
    add('source_checks','Recorded source-check exports',
      checksRecorded?'export_hash_only':packet.source_check_exports.count?'needs_review':'missing',
      packet.source_check_exports.count+' export records · latest digest '+
        (packet.source_check_exports.last_document_sha256||'unavailable'),
      'Only export hashes are retained; source receipts and outcomes are not verified.',
      ['project']);
    add('outcomes','Independent execution and business outcomes','unverified',
      'No independently verified run, causality or business outcome',
      'Human validation with authorized source evidence remains required.',
      ['dataset','process','opportunity','project','reports','source_checks']);
    return {
      format:'openworkgraph.handoff-dependency-review.v1',
      generated_at:createdAt,scope:'read_only_human_review',
      idea_id:packet.idea.idea_id,project_id:project.project_id,
      execution_authorized:false,source_read_authorized:false,
      verified_execution:false,verified_business_outcomes:false,
      handoff_review_complete:false,
      basis:{process_revision:process.revision,process_sha256:process.content_sha256,
        idea_revision:packet.idea.revision,project_idea_revision:project.idea_revision,
        project_spec_sha256:project.spec_sha256,project_archive_sha256:project.archive_sha256,
        dataset_origins:packet.dataset_origins.map(origin=>({
          status:origin.status==='current'?'current':'needs_review',
          source_content_sha256:sha(origin.source_content_sha256)?origin.source_content_sha256:null,
          selection_sha256:sha(origin.selection_sha256)?origin.selection_sha256:null
        })),
        dataset_origins_truncated:packet.dataset_origins_truncated,
        reports_truncated:packet.reported_specifications_truncated},
      coverage:{stages:rows.length,
        locally_current_or_recorded:rows.filter(row=>['locally_current','human_review_recorded','artifact_recorded'].includes(row.status)).length,
        needs_human_review:rows.filter(row=>!['locally_current','human_review_recorded','artifact_recorded'].includes(row.status)).length,
        upstream_basis_current:originCurrent&&processCurrent&&ideaCurrent&&project.idea_revision_is_current,
        independent_outcomes_verified:false},
      stages:rows,review_blockers:packet.readiness.blockers
    };
  }


  // A bounded, read-only portfolio review row. Never treat reported runs,
  // source-check export hashes or an approval as a verified outcome.
  function portfolioHandoffRow(idea,journey){
    if(!idea||typeof idea.idea_id!=='string'||!idea.idea_id||
      !['approved','implemented'].includes(idea.state)||
      !journey||journey.idea?.idea_id!==idea.idea_id||
      journey.idea?.revision!==idea.revision||
      !Array.isArray(journey.projects)||journey.projects_truncated||
      journey.projects.length>64||!Array.isArray(journey.dataset_origins)||
      journey.dataset_origins.length>64)
      throw new Error('Incomplete or stale opportunity journey.');
    if(!journey.projects.length)
      return {idea_id:idea.idea_id,project_id:null,status:'no_export',
        blockers:['no_recorded_project'],stage_review_count:null,
        spec_sha256:null,archive_sha256:null};
    const projects=[...journey.projects];
    if(projects.some(p=>typeof p.project_id!=='string'||!p.project_id||
      typeof p.created_at!=='string'||!Number.isSafeInteger(p.idea_revision)||
      typeof p.idea_revision_is_current!=='boolean')||
      new Set(projects.map(p=>p.project_id)).size!==projects.length)
      throw new Error('Project history is incomplete or invalid.');
    projects.sort((a,b)=>b.created_at.localeCompare(a.created_at)||
      a.project_id.localeCompare(b.project_id));
    const project=projects.find(p=>p.idea_revision_is_current) || projects[0];
    const review=handoffDependencyReview(journey,project.project_id,idea.idea_id,new Date().toISOString());
    if(review.basis.dataset_origins_truncated||review.basis.reports_truncated||
      review.review_blockers.length>32)
      throw new Error('Project review coverage is truncated.');
    return {idea_id:idea.idea_id,project_id:project.project_id,
      status:'human_review_required',blockers:[...review.review_blockers],
      stage_review_count:review.coverage.needs_human_review,
      spec_sha256:review.basis.project_spec_sha256,
      archive_sha256:review.basis.project_archive_sha256};
  }

  // Combines only pages that a human explicitly requested and that were read
  // through the authenticated local journey API. It is not a source attestation.
  function combinedPortfolioHandoffReview(eligible,pages,createdAt){
    if(!Array.isArray(eligible)||!(pages instanceof Map)||
      typeof createdAt!=='string')throw new Error('Invalid portfolio review basis.');
    const ids=eligible.map(item=>item.idea_id);
    if(ids.some(id=>typeof id!=='string'||!id||id.length>128)||
      new Set(ids).size!==ids.length)throw new Error('Portfolio identities are incomplete.');
    const pageCount=Math.ceil(ids.length/12),reviewed=[],items=[];
    for(const [pageNumber,page] of [...pages].sort(([a],[b])=>a-b)){
      const start=(pageNumber-1)*12,expected=ids.slice(start,start+12);
      if(!Number.isSafeInteger(pageNumber)||pageNumber<1||pageNumber>pageCount||
        !page||!Array.isArray(page.items)||page.items.length!==expected.length||
        typeof page.reviewed_at!=='string'||page.reviewed_at.length>64||
        page.items.some((item,i)=>item?.idea_id!==expected[i]||
          !['no_export','unavailable','human_review_required'].includes(item.status)))
        throw new Error('A reviewed page no longer matches the loaded opportunities.');
      reviewed.push({page_number:pageNumber,reviewed_at:page.reviewed_at,
        start_index:start+1,end_index:start+expected.length,items:page.items.map(item=>({...item}))});
      items.push(...page.items);
    }
    const reviewedNumbers=reviewed.map(page=>page.page_number);
    const unreviewedNumbers=Array.from({length:pageCount},(_,i)=>i+1)
      .filter(n=>!pages.has(n));
    return {format:'openworkgraph.portfolio-handoff-multipage-review.v1',
      generated_at:createdAt,scope:'read_only_human_review',
      eligible_count:ids.length,page_count:pageCount,
      reviewed_page_numbers:reviewedNumbers,unreviewed_page_numbers:unreviewedNumbers,
      reviewed_count:items.length,unreviewed_count:ids.length-items.length,
      incomplete_evidence_count:items.filter(item=>item.status!=='human_review_required').length,
      complete_coverage:ids.length>0&&unreviewedNumbers.length===0&&
        items.every(item=>item.status==='human_review_required'),
      execution_authorized:false,source_read_authorized:false,
      verified_execution:false,verified_business_outcomes:false,
      pages:reviewed};
  }


  // An actionable *local* triage index of only pages explicitly reviewed by
  // the operator. Unreviewed opportunities are never silently counted as clear.
  const PORTFOLIO_TRIAGE_LABELS={
    no_recorded_project:'No recorded project',
    evidence_unavailable:'Local evidence unavailable',
    no_exact_saved_process:'Exact saved process missing',
    process_not_current:'Saved process not current',
    dataset_origin_list_truncated:'Dataset origins truncated',
    dataset_origin_not_linked:'Dataset origin not linked',
    dataset_origin_not_current:'Dataset origin not current',
    idea_not_approved:'Human approval missing',
    project_uses_historical_idea_revision:'Historical project revision',
    reported_specification_list_truncated:'Report history truncated',
    no_imported_report:'No imported run report',
    reported_specification_unverified:'Reported specification not verified',
    execution_and_business_outcomes_unverified:'Execution and outcomes unverified'
  };
  function portfolioReviewTriage(eligible,pages,filter='all',limit=24,query=''){
    if(typeof query!=='string'||query.length>128)throw new Error('Invalid review search.');
    const needle=query.trim().toLocaleLowerCase();
    if(!Number.isSafeInteger(limit)||limit<1||limit>24)
      throw new Error('Invalid bounded triage limit.');
    const combined=combinedPortfolioHandoffReview(eligible,pages,'local_review');
    const index=new Map(eligible.map(idea=>[idea.idea_id,idea]));
    const counts=new Map(),all=[];
    for(const page of combined.pages){
      for(const item of page.items){
        if(!index.has(item.idea_id)||!Array.isArray(item.blockers)||
          item.blockers.length>32||item.blockers.some(key=>
            typeof key!=='string'||!Object.hasOwn(PORTFOLIO_TRIAGE_LABELS,key)))
          throw new Error('A reviewed opportunity has invalid blocker metadata.');
        const keys=item.status==='unavailable'?['evidence_unavailable']:
          item.status==='no_export'?['no_recorded_project']:item.blockers;
        if(!keys.length||new Set(keys).size!==keys.length)
          throw new Error('A reviewed opportunity has incomplete blocker metadata.');
        for(const key of keys)counts.set(key,(counts.get(key)||0)+1);
        all.push({idea_id:item.idea_id,project_id:item.project_id,
          page_number:page.page_number,blockers:[...keys]});
      }
    }
    if(filter!=='all'&&!counts.has(filter))
      throw new Error('Select a blocker reported in explicitly reviewed pages.');
    const byBlocker=filter==='all'?all:all.filter(item=>item.blockers.includes(filter));
    const matched=needle?byBlocker.filter(item=>{
      const idea=index.get(item.idea_id);
      return item.idea_id.toLocaleLowerCase().includes(needle)||
        (typeof idea?.title==='string'&&idea.title.toLocaleLowerCase().includes(needle));
    }):byBlocker;
    return {reviewed_count:combined.reviewed_count,
      unreviewed_count:combined.unreviewed_count,
      reviewed_page_numbers:combined.reviewed_page_numbers,
      unreviewed_page_numbers:combined.unreviewed_page_numbers,
      filter,query:query.trim(),matching_count:matched.length,omitted_count:Math.max(0,matched.length-limit),
      categories:[...counts].sort(([a],[b])=>a.localeCompare(b))
        .map(([key,count])=>({key,label:PORTFOLIO_TRIAGE_LABELS[key],count})),
      items:matched.slice(0,limit),execution_authorized:false,
      source_read_authorized:false,verified_execution:false,
      verified_business_outcomes:false};
  }

  // Human pilot follow-up classification, not a permission or an execution decision.
  // A disposition is only valid for a locally inspected row on an explicitly reviewed page.
  const PILOT_DISPOSITIONS=['needs_evidence','supervised_trial_candidate','not_suitable'];
  function pilotDispositionWorklist(eligible,pages,inspected,dispositions,createdAt){
    const combined=combinedPortfolioHandoffReview(eligible,pages,createdAt);
    if(!(inspected instanceof Set)||!(dispositions instanceof Map))
      throw new Error('Invalid local pilot inspection state.');
    const reviewedIds=new Set(combined.pages.flatMap(page=>page.items.map(item=>item.idea_id)));
    if([...inspected].some(id=>!reviewedIds.has(id))||
      [...dispositions].some(([id,value])=>!reviewedIds.has(id)||
        !inspected.has(id)||!PILOT_DISPOSITIONS.includes(value)))
      throw new Error('Local pilot dispositions are stale or not inspected.');
    const rows=combined.pages.flatMap(page=>page.items.map(item=>({
      idea_id:item.idea_id,project_id:item.project_id||null,
      reviewed_page:page.page_number,
      inspected_locally:inspected.has(item.idea_id),
      follow_up_disposition:dispositions.get(item.idea_id)||null,
      outstanding_checks:item.status==='unavailable'?['evidence_unavailable']:
        item.status==='no_export'?['no_recorded_project']:[...item.blockers]
    })));
    return {format:'openworkgraph.pilot-handoff-worklist.v2',
      scope:'local_human_review_only',generated_at:combined.generated_at,
      eligible_count:combined.eligible_count,reviewed_count:combined.reviewed_count,
      unreviewed_count:combined.unreviewed_count,
      reviewed_page_numbers:combined.reviewed_page_numbers,
      unreviewed_page_numbers:combined.unreviewed_page_numbers,
      complete_coverage:combined.complete_coverage,
      source_read_authorized:false,human_approval_granted:false,
      execution_authorized:false,verified_execution:false,
      verified_business_outcomes:false,rows};
  }

  // Compare an imported point-in-time review packet against a freshly fetched local journey.
  // Never restore approval or trust imported fields as execution/verification evidence.
  function compareHandoffReviewPackets(saved,current){
    const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
    const sha=v=>v===null||(typeof v==='string'&&/^[a-f0-9]{64}$/.test(v));
    const revision=v=>Number.isSafeInteger(v)&&v>=1;
    const status=v=>typeof v==='string'&&/^[a-z_]{1,64}$/.test(v);
    const fail=()=>{throw new Error('Invalid or unsupported review packet. Select a previously downloaded OWG handoff review JSON file.');};
    const validate=packet=>{
      if(!object(packet)||packet.format!=='openworkgraph.evidence-handoff-review.v1'||
        packet.scope!=='human_review_only'||packet.execution_authorized!==false||
        packet.source_read_authorized!==false||!object(packet.idea)||!object(packet.process)||
        !object(packet.project)||!object(packet.readiness)||!object(packet.source_check_exports)||
        !Array.isArray(packet.dataset_origins)||packet.dataset_origins.length>64||
        !Array.isArray(packet.reported_specifications)||packet.reported_specifications.length>64||
        typeof packet.dataset_origins_truncated!=='boolean'||
        typeof packet.reported_specifications_truncated!=='boolean'||
        !Array.isArray(packet.readiness.blockers)||packet.readiness.blockers.length>32||
        packet.readiness.human_review_required!==true||
        packet.readiness.verified_execution!==false||packet.readiness.verified_business_outcomes!==false||
        typeof packet.idea.idea_id!=='string'||!packet.idea.idea_id||packet.idea.idea_id.length>128||
        !revision(packet.idea.revision)||!status(packet.idea.state)||
        typeof packet.project.project_id!=='string'||!packet.project.project_id||
        packet.project.project_id.length>128||!revision(packet.project.idea_revision)||
        typeof packet.project.idea_revision_is_current!=='boolean'||
        !sha(packet.project.spec_sha256)||packet.project.spec_sha256===null||
        !sha(packet.project.archive_sha256)||packet.project.archive_sha256===null||
        (packet.process.workflow_id!==null&&(typeof packet.process.workflow_id!=='string'||packet.process.workflow_id.length>128))||
        (packet.process.revision!==null&&!revision(packet.process.revision))||
        !sha(packet.process.content_sha256)||!status(packet.process.status)||
        !Number.isSafeInteger(packet.source_check_exports.count)||packet.source_check_exports.count<0||
        !sha(packet.source_check_exports.last_document_sha256)||
        packet.readiness.blockers.some(b=>!status(b))||
        packet.dataset_origins.some(o=>!object(o)||!status(o.status)||!sha(o.source_content_sha256)||!sha(o.selection_sha256))||
        packet.reported_specifications.some(o=>!object(o)||!status(o.status)||
          (o.reported_run_count!==null&&(!Number.isSafeInteger(o.reported_run_count)||o.reported_run_count<0))))fail();
    };
    validate(saved);validate(current);
    if(saved.idea.idea_id!==current.idea.idea_id||saved.project.project_id!==current.project.project_id)
      throw new Error('Review packet belongs to another idea or project. Nothing was imported or approved.');
    const fields=[
      ['Saved process revision and digest',[saved.process.workflow_id,saved.process.revision,saved.process.content_sha256],
        [current.process.workflow_id,current.process.revision,current.process.content_sha256]],
      ['Saved process availability',saved.process.status,current.process.status],
      ['Opportunity revision and state',[saved.idea.revision,saved.idea.state],[current.idea.revision,current.idea.state]],
      ['Project revision and artifact hashes',[saved.project.idea_revision,saved.project.idea_revision_is_current,saved.project.spec_sha256,saved.project.archive_sha256],
        [current.project.idea_revision,current.project.idea_revision_is_current,current.project.spec_sha256,current.project.archive_sha256]],
      ['Dataset and cohort source checks',[saved.dataset_origins,saved.dataset_origins_truncated],
        [current.dataset_origins,current.dataset_origins_truncated]],
      ['Imported report status',[saved.reported_specifications,saved.reported_specifications_truncated],
        [current.reported_specifications,current.reported_specifications_truncated]],
      ['Recorded source-check exports',saved.source_check_exports,current.source_check_exports],
      ['Human review blockers',saved.readiness.blockers,current.readiness.blockers]
    ];
    const changes=fields.filter(([,before,after])=>JSON.stringify(before)!==JSON.stringify(after)).map(([name])=>name);
    return {status:changes.length?'changed':'matching_snapshot',changes,
      execution_authorized:false,verified_execution:false,verified_business_outcomes:false};
  }

  // Portable, minimized human-inspection notes for a freshly compared exact project.
  // Checked means locally inspected, never approved, resolved, source-verified or executed.
  function handoffInspectionWorklist(saved,current,inspectedKeys,createdAt){
    const comparison=compareHandoffReviewPackets(saved,current);
    const entries=[
      ...comparison.changes.map(label=>({kind:'changed_area',key:'change:'+label,label})),
      ...current.readiness.blockers.map(key=>({kind:'outstanding_blocker',key:'blocker:'+key,label:key}))
    ];
    const allowed=new Set(entries.map(item=>item.key));
    if(entries.length>40||!Array.isArray(inspectedKeys)||inspectedKeys.length>40||
      new Set(inspectedKeys).size!==inspectedKeys.length||
      inspectedKeys.some(key=>typeof key!=='string'||!allowed.has(key)))
      throw new Error('Inspection selections do not match this fresh project comparison.');
    const inspected=new Set(inspectedKeys);
    return {
      format:'openworkgraph.handoff-human-inspection.v1',generated_at:createdAt,
      scope:'local_human_inspection_only',execution_authorized:false,
      source_read_authorized:false,human_approval_granted:false,
      verified_execution:false,verified_business_outcomes:false,
      idea_id:current.idea.idea_id,project_id:current.project.project_id,
      saved_basis:{process_revision:saved.process.revision,process_sha256:saved.process.content_sha256,
        idea_revision:saved.idea.revision,project_spec_sha256:saved.project.spec_sha256,
        project_archive_sha256:saved.project.archive_sha256},
      current_basis:{process_revision:current.process.revision,process_sha256:current.process.content_sha256,
        idea_revision:current.idea.revision,project_spec_sha256:current.project.spec_sha256,
        project_archive_sha256:current.project.archive_sha256},
      comparison_status:comparison.status,
      items:entries.map(item=>({kind:item.kind,key:item.key,
        inspected_locally:inspected.has(item.key)})),
      inspected_count:inspected.size,pending_inspection_count:entries.length-inspected.size,
      source_and_outcome_verification:'not_established'
    };
  }


  // Bind an exported inspection checklist to the *whole* saved/current review
  // comparison, not only the process/project hashes. This prevents a later
  // report, source, blocker or coverage change from silently restoring marks.
  // The digest is a change detector, NOT a signature or approval.
  const inspectionStableJSON=value=>JSON.stringify(value,(key,item)=>
    item&&typeof item==='object'&&!Array.isArray(item)?
      Object.fromEntries(Object.keys(item).sort().map(name=>[name,item[name]])):item);
  async function handoffInspectionSnapshotDigest(saved,current){
    compareHandoffReviewPackets(saved,current);
    const withoutTimestamp=packet=>{const {generated_at,...basis}=packet;return basis;};
    const encoded=new TextEncoder().encode(inspectionStableJSON([
      withoutTimestamp(saved),withoutTimestamp(current)
    ]));
    const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',encoded));
    return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
  }

  // Never interpret imported notes as policy, source verification or an
  // execution grant. Reject even valid-looking notes from a changed snapshot.
  function restoreHandoffInspectionKeys(record,saved,current,snapshotDigest){
    const fail=()=>{throw new Error('Inspection worklist does not match this exact fresh comparison. Reinspect and download a new worklist.');};
    if(!record||typeof record!=='object'||Array.isArray(record)||
      typeof record.generated_at!=='string'||record.generated_at.length>64||
      !/^[a-f0-9]{64}$/.test(snapshotDigest)||
      record.comparison_sha256!==snapshotDigest||
      !Array.isArray(record.items)||record.items.length>40||
      record.items.some(item=>!item||typeof item!=='object'||
        typeof item.inspected_locally!=='boolean'))fail();
    const inspected=record.items.filter(item=>item.inspected_locally).map(item=>item.key);
    let expected;
    try{
      expected={...handoffInspectionWorklist(saved,current,inspected,record.generated_at),
        comparison_sha256:snapshotDigest};
    }catch{fail();}
    if(inspectionStableJSON(record)!==inspectionStableJSON(expected))fail();
    return inspected;
  }

  // A single portable local file contains only the previously downloadable
  // review packet and inspection notes. Never trust the wrapper as an approval.
  const REVIEW_BUNDLE_FORMAT='openworkgraph.handoff-human-review-bundle.v1';
  function handoffReviewBundle(saved,inspectionRecord){
    return {format:REVIEW_BUNDLE_FORMAT,scope:'local_human_review_only',
      execution_authorized:false,source_read_authorized:false,
      saved_packet:saved,inspection_worklist:inspectionRecord};
  }
  function unpackHandoffReviewInput(value){
    if(!value||value.format!==REVIEW_BUNDLE_FORMAT)
      return {saved:value,inspectionRecord:null};
    const keys=['execution_authorized','format','inspection_worklist','saved_packet',
      'scope','source_read_authorized'];
    if(!value||typeof value!=='object'||Array.isArray(value)||
      inspectionStableJSON(Object.keys(value).sort())!==inspectionStableJSON(keys)||
      value.scope!=='local_human_review_only'||value.execution_authorized!==false||
      value.source_read_authorized!==false||!value.saved_packet||!value.inspection_worklist)
      throw new Error('Invalid human review bundle. No inspection marks were restored.');
    return {saved:value.saved_packet,inspectionRecord:value.inspection_worklist};
  }

  // Pure helper, exercised by tests: builds an assessment body or throws a readable error.
  function assessmentBody(values,revision){
    const ease={};
    for(const [key] of QUESTIONS){const n=Number(values[key]);if(!(n>=1&&n<=5))throw new Error('Answer every ease question from 1 to 5.');ease[key]=n;}
    const cases=Number(values.cases),minutes=Number(values.minutes),share=Number(values.share);
    if(!(cases>=0)||values.cases==='')throw new Error('Enter cases per month.');
    if(!(minutes>=0)||values.minutes==='')throw new Error('Enter minutes per case.');
    if(!(share>=0&&share<=100)||values.share==='')throw new Error('Enter the eligible share from 0 to 100%.');
    return {ease,cases_per_month:cases,minutes_per_case:minutes,eligible_share:share/100,compliance_review_needed:values.compliance===true,expected_revision:revision};
  }

  // View-only triage: never changes assessments, review state or stored order.
  function portfolioView(ideas,{query='',quadrant='',sort='priority',planning={}}={}){
    const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const view=ideas.filter(idea=>{
      if(quadrant==='unassessed'?!!idea.score:quadrant&&idea.score?.quadrant!==quadrant)return false;
      if(PLANNING_FILTERS.some(([key])=>Object.hasOwn(planning,key)&&planning[key]!== (idea.planning?.[key]||'')))return false;
      const text=[idea.title,idea.description,idea.source?.reference,idea.source?.kind,idea.planning?.owner,idea.planning?.team,idea.planning?.target_quarter]
        .filter(Boolean).join(' ').toLocaleLowerCase();
      return terms.every(term=>text.includes(term));
    });
    const cmp=(a,b)=>a<b?-1:a>b?1:0;
    if(sort!=='priority')view.sort((a,b)=>{
      let result=0;
      if(sort==='impact'||sort==='ease'){
        if(!a.score||!b.score)result=Number(!a.score)-Number(!b.score);
        else result=Number(b.score[sort==='impact'?'modeled_hours_per_month':'ease_score'])-Number(a.score[sort==='impact'?'modeled_hours_per_month':'ease_score']);
      }else if(sort==='recent')result=cmp(b.updated_at||'',a.updated_at||'');
      else if(sort==='title')result=cmp(a.title.toLocaleLowerCase(),b.title.toLocaleLowerCase());
      return result||cmp(a.idea_id,b.idea_id);
    });
    return view;
  }

  function shortlistRecord(ideas,scope,total,createdAt){
    return {format:'openworkgraph.automation-portfolio-shortlist.v1',created_at:createdAt,scope,
      selected_state_ideas:total,shortlist_ideas:ideas.length,
      ideas:ideas.map(i=>({idea_id:i.idea_id,revision:i.revision,title:i.title,state:i.state,updated_at:i.updated_at,
        owner:i.planning?.owner||null,team:i.planning?.team||null,target_quarter:i.planning?.target_quarter||null,
        ease_score:i.score?.ease_score??null,modeled_hours_per_month:i.score?.modeled_hours_per_month??null,quadrant:i.score?.quadrant??null})),
      execution_authorized:false,savings_claimed:false};
  }

  function shortlistCsv(record){
    const columns=['idea_id','revision','title','state','owner','team','target_quarter','ease_score','modeled_hours_per_month','quadrant','updated_at'];
    const cell=value=>{
      const content=String(value??'');
      const safe=typeof value==='string'&&/^(?:[\t\r\n]|[\s\ufeff]*[=+\-@])/.test(content)?"'"+content:content;
      return /[",\r\n]/.test(safe)?'"'+safe.replace(/"/g,'""')+'"':safe;
    };
    return [columns,...record.ideas.map(row=>columns.map(key=>row[key]))].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
  }

  function comparisonRows(ideas){
    const known=value=>value===null||value===undefined?'Not assessed':value;
    return [
      ['State',...ideas.map(i=>STATE_LABELS[i.state]||i.state)],
      ['Saved revision',...ideas.map(i=>i.revision)],
      ['Source',...ideas.map(i=>[i.source?.kind,i.source?.reference].filter(Boolean).join(' · '))],
      ['Modeled hours / month',...ideas.map(i=>known(i.score?.modeled_hours_per_month))],
      ['Ease / 100',...ideas.map(i=>known(i.score?.ease_score))],
      ['Priority',...ideas.map(i=>i.score?QUADRANTS[i.score.quadrant]:'Not assessed')],
      ['Cases / month (entered)',...ideas.map(i=>known(i.assessment?.cases_per_month))],
      ['Minutes / case (entered)',...ideas.map(i=>known(i.assessment?.minutes_per_case))],
      ['Eligible share % (entered)',...ideas.map(i=>i.assessment?Number((i.assessment.eligible_share*100).toFixed(4)):'Not assessed')],
      ['Compliance review declared needed',...ideas.map(i=>!i.assessment?'Not assessed':i.assessment.compliance_review_needed?'Yes':'No')],
      ...QUESTIONS.map(([key,label])=>[label+' / 5',...ideas.map(i=>known(i.assessment?.ease[key]))])
    ];
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Pipeline request failed ('+response.status+').');}
    return response;
  }
  const post=async(path,body)=>(await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgPipelineCard'))return;
    const card=el('div',null,'card');card.id='owgPipelineCard';
    card.appendChild(el('h2','Automation pipeline'));
    card.appendChild(el('div','Collect automation ideas, assess how easy and valuable they look, and record decisions. Approving an idea only records your decision here: nothing is run, built or sent, and modeled hours come from the numbers you enter, not measured savings.','muted'));
    const title=input('owgPipelineTitle','text','For example: Copy approved prices into renewals');title.maxLength=200;
    const description=input('owgPipelineDescription','text','Optional details');description.maxLength=2000;
    const source=select('owgPipelineSource',[['manual','Entered by hand'],['process_catalog','Saved process'],['event_log','Event log'],['repeated_workflow','Repeated workflow'],['communications','Communications mining']]);
    const reference=input('owgPipelineReference','text','Optional reference');reference.maxLength=200;
    const add=button('Add idea');add.id='owgPipelineAdd';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Idea',title),field('Details',description),field('Found in',source),field('Reference',reference),add);
    const filter=select('owgPipelineFilter',[['','All states'],...Object.entries(STATE_LABELS)]);
    const exportLink=button('Download state CSV','secondary');exportLink.id='owgPipelineCsv';
    const shortlistLink=button('Download shortlist CSV','secondary');shortlistLink.id='owgPipelineShortlistCsv';shortlistLink.disabled=true;
    const shortlistJson=button('Download shortlist JSON','secondary');shortlistJson.id='owgPipelineShortlistJson';shortlistJson.disabled=true;
    const tools=el('div',null,'row');tools.style.alignItems='end';const search=input('owgPipelineSearch','search','Title, source or planning labels');search.maxLength=200;
    const quadrant=select('owgPipelineQuadrant',[['','All priorities'],...Object.entries(QUADRANTS),['unassessed','Not assessed']]);
    const sort=select('owgPipelineSort',[['priority','Priority (default)'],['impact','Modeled hours: highest first'],['ease','Ease: highest first'],['recent','Recently updated'],['title','Title A–Z']]);
    const reset=button('Reset view','secondary');reset.id='owgPipelineReset';
    const reload=button('Refresh opportunities','secondary');reload.id='owgPipelineRefresh';
    tools.append(field('Show',filter),field('Search ideas',search),field('Priority',quadrant),field('Sort',sort),reset,reload,shortlistLink,shortlistJson,exportLink);
    exportLink.title='Exports every idea in the selected state.';
    const planningFilters=document.createElement('details');planningFilters.id='owgPipelinePlanningFilters';
    const planningFields=el('div',null,'row');planningFilters.append(el('summary','Planning filters'),planningFields);
    const planning=Object.fromEntries(PLANNING_FILTERS.map(([key,label])=>{
      const control=select('owgPipelineView_'+key,[['','Any']]);planningFields.append(field(label,control));return [key,control];
    }));
    const status=el('div',null,'muted');status.id='owgPipelineStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const counts=el('div',null,'muted');counts.id='owgPipelineCounts';
    const list=el('div');list.id='owgPipelineList';
    const viewCount=el('div',null,'muted');viewCount.id='owgPipelineViewCount';viewCount.setAttribute('role','status');
    const comparison=el('div');comparison.id='owgPipelineComparison';
    const detail=el('div');detail.id='owgPipelineDetail';
    const resumeFile=input('owgPipelineResumeBundleFile','file');
    resumeFile.accept='.json,application/json';
    const resumeButton=button('Resume exact review from saved bundle','secondary');
    resumeButton.id='owgPipelineResumeBundle';
    const resumeStatus=el('div','Choose a previously downloaded OWG review bundle. It stays in this browser; nothing is approved or executed.','muted');
    resumeStatus.id='owgPipelineResumeStatus';
    resumeStatus.setAttribute('role','status');resumeStatus.setAttribute('aria-live','polite');
    const resumeSection=el('section');resumeSection.id='owgPipelineResumeSection';
    resumeSection.append(el('h3','Resume a previous human review'),
      field('Local review bundle (JSON, up to 64 KiB)',resumeFile),resumeButton,resumeStatus);

    // Explicitly requested, bounded review across currently loaded approved ideas.
    // No background polling, provider calls, project generation or decisions.
    const auditButton=button('Review handoff gaps across opportunities','secondary');
    auditButton.id='owgPipelinePortfolioAudit';
    const auditStatus=el('div','No portfolio review has been requested.','muted');
    auditStatus.id='owgPipelinePortfolioAuditStatus';
    auditStatus.setAttribute('role','status');auditStatus.setAttribute('aria-live','polite');
    const auditHost=el('div');auditHost.id='owgPipelinePortfolioAuditResults';
    const auditPrevious=button('Previous 12 opportunities','secondary');
    auditPrevious.id='owgPipelinePortfolioAuditPrevious';auditPrevious.disabled=true;
    const auditNext=button('Next 12 opportunities','secondary');
    auditNext.id='owgPipelinePortfolioAuditNext';auditNext.disabled=true;
    const auditPage=el('div','No eligible opportunities loaded.','muted');
    auditPage.id='owgPipelinePortfolioAuditPage';
    auditPage.setAttribute('role','status');auditPage.setAttribute('aria-live','polite');
    const auditNavigation=el('div',null,'row');
    auditNavigation.append(auditPrevious,auditPage,auditNext);
    const auditCumulative=el('div','No pages explicitly reviewed yet.','muted');
    auditCumulative.id='owgPipelinePortfolioCumulativeStatus';
    auditCumulative.setAttribute('role','status');auditCumulative.setAttribute('aria-live','polite');
    const auditCombinedDownload=button('Download reviewed pages together','secondary');
    auditCombinedDownload.id='owgPipelinePortfolioCombinedDownload';
    auditCombinedDownload.disabled=true;
    const auditSection=el('section');auditSection.id='owgPipelinePortfolioAuditSection';
    const auditTriageFilter=select('owgPipelinePortfolioTriageFilter',[['all','All explicitly reviewed opportunities']]);
    auditTriageFilter.disabled=true;
    const auditTriageSearch=input('owgPipelinePortfolioTriageSearch','search','Search reviewed opportunity title or ID');
    auditTriageSearch.maxLength=128;
    const auditTriageSummary=el('div','Review at least one page to triage its blockers.','muted');
    auditTriageSummary.id='owgPipelinePortfolioTriageSummary';
    auditTriageSummary.setAttribute('role','status');
    auditTriageSummary.setAttribute('aria-live','polite');
    const auditWorklistDownload=button('Download reviewed pilot worklist','secondary');
    auditWorklistDownload.id='owgPipelinePortfolioPilotWorklist';
    auditWorklistDownload.disabled=true;
    const auditTriageHost=el('div');auditTriageHost.id='owgPipelinePortfolioTriageRows';
    const auditTriageSection=el('section');auditTriageSection.id='owgPipelinePortfolioTriageSection';
    auditTriageSection.append(el('h4','Triage outstanding checks from reviewed pages'),
      el('p','Filter only pages you explicitly checked. Unreviewed pages are unknown, not clear. Opening a check only navigates to local human review.','muted'),
      field('Outstanding review check',auditTriageFilter),
      field('Find in explicitly reviewed pages',auditTriageSearch),auditTriageSummary,auditTriageHost,auditWorklistDownload);
    auditSection.append(el('h3','Human handoff review inbox'),
      el('p','Review approved or implemented opportunities in explicit pages of up to 12. Each page is a fresh, point-in-time local read; other pages are not certified, approved or executed.','muted'),
      auditNavigation,auditButton,auditStatus,auditHost,auditCumulative,auditCombinedDownload,auditTriageSection);
    const portfolioButton=button('Show portfolio by team and owner','secondary');portfolioButton.id='owgPipelinePortfolio';
    const portfolioHost=el('div');portfolioHost.id='owgPipelinePortfolioResult';
    portfolioButton.onclick=async()=>{
      portfolioButton.disabled=true;
      try{
        const r=await (await api('/v1/automation-pipeline/portfolio')).json();
        const money=b=>Object.entries(b).map(([k,v])=>{const [cur,basis]=k.split(':');return v.toLocaleString()+' '+cur+(basis==='approved_budget'?' approved':' estimated');}).join('; ')||'—';
        const table=(key,label,rows)=>{
          const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('tr');t.id='owgPipelinePortfolio_'+key;
          [label,'Ideas','Open','Approved','Implemented','Budgets (per currency)','Modeled hours / month'].forEach(h=>{const th=el('th',h);th.scope='col';head.appendChild(th);});
          const thead=el('thead');thead.appendChild(head);const body=el('tbody');
          rows.forEach(row=>{const tr=el('tr');const th=el('th',row[key]||'Not set');th.scope='row';tr.appendChild(th);
            const s=row.states;[row.ideas,s.idea+s.assessing+s.on_hold,s.approved,s.implemented,money(row.budgets),row.modeled_hours_per_month].forEach(v=>tr.appendChild(el('td',String(v))));body.appendChild(tr);});
          t.append(thead,body);wrap.appendChild(t);return wrap;
        };
        const blocked=el('ul');blocked.id='owgPipelineBlocked';
        r.blocked.forEach(b=>blocked.appendChild(el('li',b.title+' waits on '+b.waiting_on.map(w=>(w.title||w.idea_id)+' ('+(STATE_LABELS[w.state]||w.state)+')').join(', '))));
        const quarters=Object.entries(r.open_ideas_by_target_quarter).map(([q,n])=>q+': '+n).join(' · ');
        portfolioHost.replaceChildren(el('h4','Portfolio'),el('h5','By team'),table('team','Team',r.by_team),el('h5','By owner'),table('owner','Owner',r.by_owner),
          el('h5','Waiting on dependencies'),r.blocked.length?blocked:el('div','No open idea waits on another.','muted'),
          el('div',(quarters?'Open ideas by target quarter: '+quarters+'. ':'')+r.unplanned_ideas+' ideas have no planning yet.','muted'),
          el('p',r.interpretation,'muted'));
      }catch(e){status.textContent=e.message;}
      finally{portfolioButton.disabled=false;}
    };
    const budgetScenarioButton=button('Plan a budget-constrained portfolio','secondary');
    budgetScenarioButton.id='owgPipelineBudgetScenario';
    const budgetScenarioHost=el('div');budgetScenarioHost.id='owgPipelineBudgetScenarioResult';
    let budgetGeneration=0;
    budgetScenarioButton.onclick=async()=>{
      const n=++budgetGeneration,root=el('div');budgetScenarioHost.replaceChildren(root);
      const active=()=>n===budgetGeneration&&root.isConnected;
      root.append(el('p','Loading current portfolio revisions…','muted'));
      try{
        const inv=await(await api('/v1/automation-pipeline/budget-scenario/inventory')).json();
        if(!active())return;
        const heading=el('h4','Budget-constrained portfolio planner');
        const description=el('p','Select up to 18 opportunities, assign explicit priority points, and choose a single currency and budget basis. Include unfinished prerequisites; implemented ones do not consume this scenario budget. This is not a delivery plan or approval.','muted');
        const currency=el('input');currency.type='text';currency.maxLength=3;currency.placeholder='SEK';currency.id='owgBudgetCurrency';
        const basis=el('select');basis.id='owgBudgetBasis';
        for(const value of ['estimate','approved_budget']){const opt=el('option',value);opt.value=value;basis.append(opt);}
        const limit=el('input');limit.type='number';limit.min='0';limit.max='1000000000000';limit.step='any';limit.id='owgBudgetLimit';
        const max=el('input');max.type='number';max.min='1';max.max='18';max.value='18';max.id='owgBudgetMax';
        const labelled=(label,node)=>{const wrapper=el('label',label+' ');wrapper.append(node);return wrapper;};
        const controls=el('div',null,'row');controls.append(labelled('Currency',currency),labelled('Budget basis',basis),labelled('Budget limit',limit),labelled('Maximum selected ideas',max));
        const frontierOn=el('input');frontierOn.type='checkbox';frontierOn.id='owgBudgetFrontier';
        const targets=el('input');targets.type='text';targets.id='owgBudgetFrontierTargets';targets.value='50,100,200';targets.maxLength=80;targets.disabled=true;
        const checkpoints=el('input');checkpoints.type='text';checkpoints.id='owgBudgetFrontierCheckpoints';checkpoints.maxLength=200;checkpoints.disabled=true;checkpoints.placeholder='Optional: 10000,50000,100000';
        const profileInput=el('textarea');profileInput.id='owgBudgetPreferenceProfiles';profileInput.rows=6;profileInput.maxLength=8192;profileInput.disabled=true;profileInput.value='[]';profileInput.style.cssText='width:100%;box-sizing:border-box';
        const profileTemplate=button('Create editable alternate preference profile','secondary');profileTemplate.disabled=true;
        const frontierControls=el('div');frontierControls.append(labelled('Include exact budget frontier and candidate sensitivity',frontierOn),
          labelled('Priority targets (0–1800, comma separated)',targets),labelled('Optional budget checkpoints, comma separated',checkpoints),profileTemplate,labelled('Optional preference profiles JSON (up to three, 16 candidates maximum)',profileInput));
        frontierOn.onchange=()=>{targets.disabled=checkpoints.disabled=profileInput.disabled=profileTemplate.disabled=!frontierOn.checked;invalidate();};targets.oninput=checkpoints.oninput=profileInput.oninput=()=>invalidate();
        const options=el('div'),out=el('div'),status=el('p');status.setAttribute('role','status');
        const run=button('Evaluate exact portfolio combinations','secondary'),download=button('Download scenario report','secondary');download.disabled=true;
        let attempt=0,report=null;const controlsById=new Map();
        function invalidate(){attempt++;report=null;download.disabled=true;out.replaceChildren();status.textContent='Review inputs and evaluate again.';run.disabled=false;}
        for(const control of [currency,basis,limit,max])control.addEventListener('input',invalidate);
        const query=el('input');query.type='search';query.placeholder='Find opportunity by name or ID';query.setAttribute('aria-label','Find opportunities');
        function renderChoices(){
          options.replaceChildren();
          const term=query.value.toLowerCase();
          for(const idea of inv.ideas.filter(i=>['idea','assessing','approved','on_hold'].includes(i.state))){
            if(term&&!(idea.title+' '+idea.idea_id).toLowerCase().includes(term))continue;
            let entry=controlsById.get(idea.idea_id);
            if(!entry){
              const selected=el('input');selected.type='checkbox';selected.setAttribute('aria-label','Select '+idea.title);
              const priority=el('input');priority.type='number';priority.min='0';priority.max='100';priority.step='1';priority.value='0';priority.disabled=true;
              priority.setAttribute('aria-label','Priority points for '+idea.title);
              const required=el('input');required.type='checkbox';required.disabled=true;required.setAttribute('aria-label','Require '+idea.title);
              selected.onchange=()=>{priority.disabled=required.disabled=!selected.checked;if(!selected.checked)required.checked=false;invalidate();};
              priority.oninput=invalidate;required.onchange=invalidate;entry={idea,selected,priority,required};controlsById.set(idea.idea_id,entry);
            }
            const row=el('fieldset');row.append(el('legend',idea.title),labelled('Include candidate',entry.selected),
              el('p',idea.idea_id+' · revision '+idea.revision+' · '+idea.state+' · '+(idea.budget?idea.budget.amount+' '+idea.budget.currency+' ('+idea.budget.basis+')':'No budget'),'muted'),
              labelled('Priority points (0–100)',entry.priority),labelled('Required in every scenario',entry.required));
            if(idea.depends_on.length)row.append(el('p','Declared prerequisites: '+idea.depends_on.map(id=>inv.ideas.find(i=>i.idea_id===id)?.title||id).join('; '),'muted'));
            options.append(row);
          }
        }
        query.oninput=renderChoices;renderChoices();
        profileTemplate.onclick=()=>{const selected=[...controlsById.values()].filter(x=>x.selected.checked);if(!selected.length||selected.length>16){status.textContent='Select 1–16 candidates first.';return;}if(profileInput.value.trim()!=='[]'&&!window.confirm('Replace existing alternate preference profiles?'))return;profileInput.value=JSON.stringify([{label:'Alternate stakeholder',priorities:Object.fromEntries(selected.map(x=>[x.idea.idea_id,Number(x.priority.value)]))}],null,2);invalidate();};
        download.onclick=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-portfolio-budget-scenario.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
        run.onclick=async()=>{
          invalidate();const k=attempt;
          const candidates=[...controlsById.values()].filter(c=>c.selected.checked).map(c=>({idea_id:c.idea.idea_id,revision:c.idea.revision,priority:Number(c.priority.value),required:c.required.checked}));
          if(!/^[A-Z]{3}$/.test(currency.value)||!limit.value||!max.value||!candidates.length||candidates.length>18||
            [...controlsById.values()].some(c=>c.selected.checked&&!c.priority.value)){
            status.textContent='Enter an uppercase currency, budget, selection limit and priorities for 1–18 candidates.';return;
          }
          let frontierReview;
          if(frontierOn.checked){
            const values=targets.value.split(',').map(x=>x.trim());
            if(values.length>12||values.some(x=>!/^\d{1,4}$/.test(x)||Number(x)>1800)||new Set(values.map(Number)).size!==values.length){
              status.textContent='Enter one to twelve distinct integer targets from 0 to 1800.';return;
            }
            const budgets=checkpoints.value.trim()?checkpoints.value.split(',').map(x=>x.trim()):[];
            if(budgets.length>12||budgets.some(x=>!/^\d+(?:\.\d+)?$/.test(x)||!Number.isFinite(Number(x))||Number(x)>1e12)||new Set(budgets.map(Number)).size!==budgets.length){
              status.textContent='Enter up to twelve distinct nonnegative budget checkpoints.';return;
            }
            let profiles;
            try{profiles=JSON.parse(profileInput.value);}
            catch(_){status.textContent='Invalid preference profile JSON.';return;}
            if(!Array.isArray(profiles)||profiles.length>3||(profiles.length&&candidates.length>16)){
              status.textContent='Use at most three alternate profiles and sixteen candidates.';return;
            }
            frontierReview={priority_targets:values.map(Number),budget_checkpoints:budgets.map(Number),preference_profiles:profiles};
          }
          run.disabled=true;status.textContent='Evaluating all bounded candidate subsets…';
          try{
            const data=await(await api('/v1/automation-pipeline/budget-scenario',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({snapshot_sha256:inv.snapshot_sha256,currency:currency.value,budget_basis:basis.value,budget_limit:Number(limit.value),max_selected:Number(max.value),candidates,...(frontierReview?{frontier_review:frontierReview}:{})})})).json();
            if(!active()||k!==attempt)return;
            if(data.snapshot_sha256!==inv.snapshot_sha256)throw Error('Unexpected portfolio snapshot.');
            report=data;download.disabled=false;status.textContent=data.feasible_subsets+' feasible subsets of '+data.subsets_considered+' examined.';
            const titles=new Map(data.candidates.map(i=>[i.idea_id,i.title]));
            const show=(row)=>{const item=el('div');item.append(el('strong',row.priority_points+' priority points · '+row.budget_used+' '+data.currency),
              el('p',row.selected_idea_ids.map(id=>titles.get(id)||id).join('; ')||'No ideas selected'),
              el('p',row.budget_remaining+' '+data.currency+' remaining; '+row.selected_count+' ideas','muted'));return item;};
            out.replaceChildren(el('h4',data.status==='feasible'?'Highest-ranked feasible alternatives':'No feasible portfolio under these constraints'),
              ...data.alternatives.map(show));
            if(data.frontier_review){
              const f=data.frontier_review,host=el('div');host.id='owgBudgetFrontierResult';
              const grid=(headers,rows)=>{const wrap=el('div',null,'table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
                const t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');for(const x of headers){const th=el('th',x);th.scope='col';hr.append(th);}h.append(hr);
                for(const row of rows){const tr=el('tr');for(const x of row)tr.append(el('td',String(x??'—')));b.append(tr);}t.append(h,b);wrap.append(t);return wrap;};
              const names=r=>r?r.selected_idea_ids.map(id=>titles.get(id)||id).join('; ')||'Empty portfolio':'Infeasible';
              host.append(el('h4','Exact spending versus priority frontier'),
                el('p',f.structurally_feasible_subsets+' structurally feasible subsets; '+f.current_budget_feasible_subsets+' within budget. '+f.frontier.length+' nondominated cost/priority points.'),
                el('p',f.maximum_priority_portfolios+' portfolios tie for maximum priority within current budget (before cost/count/ID tie-breaks).'),
                grid(['Target points','Minimum budget ('+data.currency+')','Extra budget required','Representative portfolio'],
                  f.priority_targets.map(x=>[x.minimum_priority_points,x.least_cost_portfolio?.budget_used,x.least_cost_portfolio?.additional_budget_required,names(x.least_cost_portfolio)])));
              if(f.budget_checkpoints.length)host.append(el('h4','Budget checkpoint sensitivity'),
                grid(['Budget','Best priority','Used budget','Unused budget','Priority delta vs current budget','Extra to next breakpoint','Best portfolio'],
                  f.budget_checkpoints.map(x=>[x.budget_limit,x.best_portfolio?.priority_points,x.best_portfolio?.budget_used,x.unused_budget,
                    x.priority_delta_vs_current_budget,x.additional_budget_to_next_breakpoint,names(x.best_portfolio)])));
              const view=el('div'),prev=button('Previous frontier page','secondary'),next=button('Next frontier page','secondary');let offset=0;
              const page=()=>{view.replaceChildren(el('p',(offset+1)+'–'+Math.min(offset+30,f.frontier.length)+' of '+f.frontier.length+' frontier points.'),
                grid(['Priority points','Minimum cost','Within budget','Representative portfolio'],f.frontier.slice(offset,offset+30).map(x=>[x.priority_points,x.budget_used,x.within_current_budget?'Yes':'No',names(x)])));
                prev.disabled=offset===0;next.disabled=offset+30>=f.frontier.length;};
              prev.onclick=()=>{offset=Math.max(0,offset-30);page();};next.onclick=()=>{offset+=30;page();};
              if(f.frontier.length){page();host.append(view,prev,next);}else host.append(el('p','No structurally feasible portfolio.'));
              host.append(el('h4','Candidate inclusion and exclusion sensitivity'),
                grid(['Opportunity','In all/some/no maximum-priority portfolios','Feasible with','Feasible without','Priority loss if required','Priority loss if excluded'],
                  f.candidate_sensitivity.map(x=>[titles.get(x.idea_id)||x.idea_id,x.membership_among_max_priority_portfolios,x.feasible_subsets_including,x.feasible_subsets_excluding,
                    x.forced_in.priority_loss_vs_optimum??'Infeasible',x.forced_out.priority_loss_vs_optimum??'Infeasible'])));
              const details=el('details');details.append(el('summary','Exact forced-in and forced-out alternatives'));
              for(const x of f.candidate_sensitivity)details.append(el('h5',titles.get(x.idea_id)||x.idea_id),
                grid(['Constraint','Points','Budget used','Budget delta vs ranked optimum','Selected opportunities'],
                  [['Required',x.forced_in.best_portfolio?.priority_points,x.forced_in.best_portfolio?.budget_used,x.forced_in.budget_delta_vs_ranked_optimum,names(x.forced_in.best_portfolio)],
                   ['Excluded',x.forced_out.best_portfolio?.priority_points,x.forced_out.best_portfolio?.budget_used,x.forced_out.budget_delta_vs_ranked_optimum,names(x.forced_out.best_portfolio)]]),
                grid(['Structural constraint (any budget)','Feasible subsets','Minimum cost','Extra budget required','Least-cost portfolio'],
                  [['Required',x.structural_subsets_including,x.least_cost_portfolio_including?.budget_used,x.least_cost_portfolio_including?.additional_budget_required,names(x.least_cost_portfolio_including)],
                   ['Excluded',x.structural_subsets_excluding,x.least_cost_portfolio_excluding?.budget_used,x.least_cost_portfolio_excluding?.additional_budget_required,names(x.least_cost_portfolio_excluding)]]));
              if(f.preference_review){
                const r=f.preference_review,display=el('details');display.open=true;display.append(el('summary','Stakeholder preference robustness'),
                  el('p',r.feasible_subsets+' identical feasible portfolios evaluated against '+r.profiles.length+' declared profiles.'));
                if(r.status==='feasible'){
                  const showProfile=(label,p)=>[label,p.priority_points,p.worst_profile_priority_points,p.maximum_regret_points,p.total_regret_points,p.budget_used,names(p)];
                  display.append(el('p',r.simultaneously_optimal_portfolios+' portfolios achieve every individual profile optimum.'),
                    grid(['Choice rule','Original points','Worst profile points','Maximum regret','Total regret','Budget used','Selected portfolio'],
                      [...r.individual_optima.map(x=>showProfile('Optimum: '+x.label,x.portfolio)),showProfile('Minimax regret',r.minimax_regret_portfolio),showProfile('Maximin priority',r.maximin_priority_portfolio)]),
                    grid(['Choice rule','Profile','Score','Regret'],[['Minimax regret',r.minimax_regret_portfolio],['Maximin priority',r.maximin_priority_portfolio]].flatMap(([label,p])=>p.profile_scores.map(x=>[label,x.label,x.priority_points,x.regret_points]))),el('p',r.interpretation,'muted'));
                }else display.append(el('p','No feasible portfolio within the original budget.'));
                host.append(display);
              }
              host.append(details,grid(['Selected count','Feasible subsets'],f.feasible_subset_counts_by_size.map(x=>[x.selected_count,x.count])),el('p',f.interpretation,'muted'));out.append(host);
            }
            const details=el('details');details.append(el('summary','Best feasible alternative by selected count'),...data.best_by_selected_count.map(show));out.append(details);
            for(const candidate of data.candidates.filter(c=>c.blockers.length))out.append(el('p',candidate.title+': '+candidate.blockers.map(b=>b.code+(b.dependency_id?' ('+(titles.get(b.dependency_id)||b.dependency_id)+')':'')).join('; ')));
            out.append(el('p','Required ideas and unfinished selected prerequisites: '+data.required_closure_ids.map(id=>titles.get(id)||id).join('; ')));
            out.append(el('p','Priority points are entered by you; no measured or modeled savings are summed. This scenario makes no changes to the portfolio.','muted'));
          }catch(e){if(active()&&k===attempt)status.textContent=e.message;}
          finally{if(active()&&k===attempt)run.disabled=false;}
        };
        root.replaceChildren(heading,description,controls,frontierControls,query,options,run,download,status,out);
      }catch(e){if(active())root.replaceChildren(el('p',e.message,'muted'));}
    };
    const capacityButton=button('Plan resource-constrained implementation','secondary');
    capacityButton.id='owgPipelineResourceSchedule';
    const capacityHost=el('div');capacityHost.id='owgPipelineResourceScheduleResult';
    let capacityGeneration=0;
    capacityButton.onclick=async()=>{
      const generation=++capacityGeneration, root=el('section');capacityHost.replaceChildren(root);
      const active=()=>generation===capacityGeneration&&root.isConnected;
      root.append(el('p','Loading exact opportunity revisions…','muted'));
      try{
        const inv=await(await api('/v1/automation-pipeline/resource-schedule/inventory')).json();
        if(!active())return;
        const chosen=new Map(), options=el('div'), output=el('div'), status=el('p');
        status.setAttribute('role','status');
        const find=input('owgCapacityFind','search','Find opportunities');
        find.setAttribute('aria-label','Find implementation opportunities');
        const pools=el('textarea');pools.id='owgCapacityPools';pools.rows=3;pools.style.width='100%';
        pools.placeholder='{"engineering":2,"operations":1}';
        const alternatives=el('textarea');alternatives.id='owgCapacityAlternatives';alternatives.rows=3;alternatives.style.width='100%';
        alternatives.value='[]';
        const prepare=button('Prepare reviewed capacity input','secondary'),run=button('Evaluate implementation schedules','secondary'),
          save=button('Download schedule report','secondary');
        prepare.id='owgCapacityPrepare';run.id='owgCapacityRun';save.id='owgCapacityDownload';
        const reviewed=el('textarea');reviewed.id='owgCapacityReviewed';reviewed.readOnly=true;reviewed.rows=9;reviewed.style.width='100%';
        run.disabled=save.disabled=true;
        let version=0, draft=null, report=null;
        function invalidate(){
          version++;draft=report=null;reviewed.value='';run.disabled=save.disabled=true;output.replaceChildren();
          status.textContent='Review assumptions and prepare the exact input again.';
        }
        const label=(title,node)=>{const wrapper=el('label',title+' ');wrapper.append(node);return wrapper;};
        const number=(name,min,max,value='')=>{
          const node=el('input');node.type='number';node.min=String(min);node.max=String(max);node.step='1';
          node.value=value;node.setAttribute('aria-label',name);node.oninput=invalidate;return node;
        };
        function drawChoices(){
          options.replaceChildren();
          const term=find.value.toLowerCase(), eligible=inv.ideas.filter(i=>['idea','assessing','approved','on_hold'].includes(i.state));
          const matches=eligible.filter(i=>(i.title+' '+i.idea_id).toLowerCase().includes(term));
          options.append(el('p',matches.length+' matching opportunities; showing up to 100. Selected: '+[...chosen.values()].filter(x=>x.check.checked).length+'.','muted'));
          for(const idea of matches.slice(0,100)){
            let c=chosen.get(idea.idea_id);
            if(!c){
              const check=el('input');check.type='checkbox';check.setAttribute('aria-label','Schedule '+idea.title);
              const pool=el('input');pool.type='text';pool.maxLength=40;pool.placeholder='engineering';
              pool.setAttribute('aria-label','Resource pool for '+idea.title);pool.oninput=invalidate;
              const duration=number('Duration days for '+idea.title,1,180),units=number('Resource units for '+idea.title,1,50),
                release=number('Release day for '+idea.title,0,730,'0'),deadline=number('Deadline day for '+idea.title,1,730);
              const controls=[pool,duration,units,release,deadline];controls.forEach(node=>node.disabled=true);
              check.onchange=()=>{controls.forEach(node=>node.disabled=!check.checked);invalidate();};
              c={idea,check,pool,duration,units,release,deadline};chosen.set(idea.idea_id,c);
            }
            const box=el('fieldset');box.append(el('legend',idea.title),label('Include opportunity',c.check),
              el('p',idea.idea_id+' · revision '+idea.revision+' · '+idea.state+' · declared team '+(idea.team||'none'),'muted'),
              label('Resource pool',c.pool),label('Duration days',c.duration),label('Units throughout task',c.units),
              label('Earliest start day',c.release),label('Deadline day (optional)',c.deadline));
            if(idea.depends_on.length)box.append(el('p','Prerequisites: '+idea.depends_on.map(id=>inv.ideas.find(i=>i.idea_id===id)?.title||id).join('; '),'muted'));
            options.append(box);
          }
        }
        find.oninput=drawChoices;pools.oninput=alternatives.oninput=invalidate;drawChoices();
        prepare.onclick=()=>{
          invalidate();
          try{
            const selected=[...chosen.values()].filter(c=>c.check.checked);
            if(!selected.length||selected.length>30)throw Error('Select 1–30 opportunities and include every unfinished prerequisite.');
            const integer=(node,name,optional=false)=>{
              if(!node.value){if(optional)return null;throw Error('Enter '+name+' for each selected opportunity.');}
              const value=Number(node.value);
              if(!Number.isInteger(value)||value<Number(node.min)||value>Number(node.max))throw Error('Invalid '+name+'.');
              return value;
            };
            const tasks=selected.map(c=>{
              if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/.test(c.pool.value))throw Error('Enter an exact pool identifier for each selected opportunity.');
              return {idea_id:c.idea.idea_id,revision:c.idea.revision,pool:c.pool.value,
                duration_days:integer(c.duration,'duration'),units:integer(c.units,'resource units'),
                release_day:integer(c.release,'release day'),deadline_day:integer(c.deadline,'deadline',true)};
            });
            draft={format:'openworkgraph.portfolio-resource-schedule.v1',snapshot_sha256:inv.snapshot_sha256,
              tasks,capacities:JSON.parse(pools.value),alternatives:JSON.parse(alternatives.value)};
            reviewed.value=JSON.stringify(draft,null,2);run.disabled=false;
            status.textContent='Review the exact assumptions above, then evaluate. No opportunity will be modified.';
          }catch(error){status.textContent=error.message;}
        };
        const table=(headers,rows)=>{
          const wrap=el('div',null,'table-wrap'), table=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
          headers.forEach(h=>{const cell=el('th',h);cell.scope='col';hr.append(cell);});head.append(hr);
          for(const row of rows){const tr=el('tr');row.forEach(value=>tr.append(el('td',value??'—')));body.append(tr);}
          table.append(head,body);wrap.append(table);return wrap;
        };
        const timeline=(scenario,name)=>{
          const chart=el('div');chart.setAttribute('role','group');chart.setAttribute('aria-label','Relative planning-day timeline for '+scenario.label);
          chart.append(el('p','Day 0 to '+scenario.makespan_days+'; bar positions show relative time, not calendar dates. Exact dates are in the table below.','muted'));
          for(const row of scenario.schedule){
            const item=el('div');item.style.cssText='display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:8px;align-items:center;margin:5px 0';
            const title=el('span',name(row.idea_id)+' · '+row.pool);title.style.overflowWrap='anywhere';
            const track=el('div');track.style.cssText='height:22px;border:1px solid var(--line);border-radius:4px;position:relative;background:var(--soft)';
            const bar=el('div');bar.style.cssText='height:100%;position:absolute;border-radius:3px;background:var(--green)';
            bar.style.left=(100*row.start_day/scenario.makespan_days)+'%';
            bar.style.width=(100*row.duration_days/scenario.makespan_days)+'%';
            bar.title='Day '+row.start_day+' to '+row.finish_day_exclusive+' (exclusive)';
            track.setAttribute('aria-hidden','true');track.append(bar);item.append(title,track);chart.append(item);
          }
          return chart;
        };
        run.onclick=async()=>{
          if(!draft)return;
          const n=++version, spec=draft;report=null;save.disabled=true;run.disabled=true;output.replaceChildren();
          status.textContent='Evaluating feasible schedules and makespan bounds…';
          try{
            const data=await(await api('/v1/automation-pipeline/resource-schedule',{method:'POST',
              headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
            if(!active()||n!==version)return;
            if(data.snapshot_sha256!==inv.snapshot_sha256)throw Error('Unexpected portfolio snapshot.');
            report=data;save.disabled=false;
            const titles=new Map(data.selected_ideas.map(i=>[i.idea_id,i.title]));
            const name=id=>titles.get(id)||id;
            status.textContent=data.blockers.length?'Selected portfolio has unresolved prerequisite blockers.':'Schedule scenarios evaluated. No saved plan changed.';
            if(data.blockers.length){
              output.append(el('h4','Resolve prerequisite blockers'));
              for(const b of data.blockers)output.append(el('p',(b.idea_id?name(b.idea_id)+': ':'')+b.code.replaceAll('_',' ')+(b.dependency_id?' · '+name(b.dependency_id):'')));
            }
            if(data.scenarios.length){
              output.append(el('h4','Resource capacity comparison'),table(
                ['Design','Makespan (days)','Optimum bounds','Proof','Deadline misses','Change vs baseline'],
                data.scenarios.map(s=>[s.label,s.makespan_days,s.optimization?s.optimization.lower_bound_days+'–'+s.optimization.upper_bound_days:null,
                  s.optimization?(s.optimization.optimal_makespan_proven?'Proven makespan':'Feasible heuristic'):s.status.replaceAll('_',' '),
                  s.deadline_misses_in_returned_schedule,s.makespan_change_vs_baseline_days])));
              for(const s of data.scenarios){
                const section=el('details');section.append(el('summary',s.label+' schedule and resource load'));
                if(!s.schedule){section.append(el('p','Demand exceeds capacity for: '+s.oversized_task_ids.map(name).join('; ')));output.append(section);continue;}
                section.append(el('p','Resource-free precedence bound '+s.resource_free_precedence_bound_days+
                  ' days; returned makespan '+s.makespan_days+' days. '+s.deadline_misses_even_without_resource_constraints+
                  ' declared deadlines missed even without resource constraints.','muted'),timeline(s,name),
                  table(['Opportunity','Pool','Units','Start day','Finish (exclusive)','Duration','Wait after prerequisites','Deadline','Late days','Precedence slack'],
                    s.schedule.map(t=>[name(t.idea_id),t.pool,t.units,t.start_day,t.finish_day_exclusive,t.duration_days,
                      t.delay_after_dependency_ready_days,t.deadline_day,t.deadline_lateness_days,t.resource_free_total_slack_days])),
                  table(['Pool','Capacity','Peak','Used unit-days','Available unit-days','Utilization','Fully occupied days'],
                    s.resource_load.map(p=>[p.pool,p.capacity_units,p.peak_units,p.used_unit_days,p.available_unit_days,
                      (p.utilization*100).toFixed(1)+'%',p.fully_occupied_days])),
                  el('p','Optimum change bounds versus baseline: '+(s.optimal_makespan_change_bounds_days?s.optimal_makespan_change_bounds_days.join(' to ')+' days.':'Unavailable because the baseline is infeasible.'),'muted'));
                output.append(section);
              }
            }
            output.append(el('p',data.interpretation,'muted'));
          }catch(error){if(active()&&n===version)status.textContent=error.message;}
          finally{if(active()&&n===version)run.disabled=false;}
        };
        save.onclick=()=>{
          if(!report)return;
          const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
          const a=el('a');a.href=url;a.download='owg-portfolio-resource-schedule.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        };
        root.replaceChildren(el('h4','Resource-constrained implementation planning'),
          el('p','Select opportunities and enter explicit uninterrupted planning-day durations and units of one resource pool. Include unfinished prerequisites. No default durations or capacity are assumed. Deadlines are diagnostics; only makespan is optimized.','muted'),
          find,options,field('Baseline pool capacities (JSON object)',pools),
          field('Optional alternatives (JSON array of label and capacities objects)',alternatives),
          el('p','Example alternative: [{"label":"More engineering","capacities":{"engineering":3,"operations":1}}]. Every alternative uses the same pools.','muted'),
          prepare,field('Exact reviewed input',reviewed),run,save,status,output);
      }catch(error){if(active())root.replaceChildren(el('p',error.message,'muted'));}
    };
    const benefitOverlapButton=button('Review overlapping benefit evidence','secondary');
    benefitOverlapButton.id='owgPipelineBenefitOverlap';
    const benefitOverlapHost=el('div');
    benefitOverlapHost.id='owgPipelineBenefitOverlapResult';
    benefitOverlapHost.setAttribute('role','status');
    benefitOverlapHost.setAttribute('aria-live','polite');
    benefitOverlapButton.onclick=async()=>{
      benefitOverlapButton.disabled=true;
      try{
        const r=await (await api('/v1/automation-pipeline/benefit-overlap')).json();
        if(!Array.isArray(r.candidate_pairs)||typeof r.after_measurements!=='number')
          throw new Error('Unexpected benefit-overlap report');
        const count=el('p',r.candidate_pairs.length+
          ' cross-idea review candidates shown from '+r.after_measurements+
          ' recorded after-period measurements. '+r.unlinked_after_measurements+
          ' lack an exact source reference.','muted');
        const title=id=>(latestIdeas.find(idea=>idea.idea_id===id)||{}).title||id;
        const items=el('ul');
        for(const pair of r.candidate_pairs){
          items.appendChild(el('li',title(pair.first_idea_id)+' ('+pair.first_measurement_id+
            ') and '+title(pair.second_idea_id)+' ('+pair.second_measurement_id+
            '): '+pair.overlap_start+' to '+pair.overlap_end+
            ' · '+pair.source_kind+' · '+pair.source_group));
        }
        const note=r.rows_truncated||r.candidate_pairs_truncated||r.unreadable_measurements?
          'Some evidence could not be fully reviewed; do not infer non-overlap from omitted rows.':
          r.candidate_pairs.length?'Review the underlying case populations before attributing or adding benefits.':
          'No exact source overlap was found; different or missing references may still cover the same work.';
        benefitOverlapHost.replaceChildren(el('h4','Potential benefit overlap'),count,
          ...(r.candidate_pairs.length?[items]:[]),el('p',note,'muted'),
          el('p',r.interpretation,'muted'));
      }catch(e){benefitOverlapHost.replaceChildren(el('p',e.message,'muted'));}
      finally{benefitOverlapButton.disabled=false;}
    };
    const benefitReadinessButton=button('Review benefit evidence readiness','secondary');
    benefitReadinessButton.id='owgPipelineBenefitReadiness';
    const benefitReadinessHost=el('div');
    benefitReadinessHost.id='owgPipelineBenefitReadinessResult';
    benefitReadinessHost.setAttribute('role','status');
    benefitReadinessHost.setAttribute('aria-live','polite');
    benefitReadinessButton.onclick=async()=>{
      benefitReadinessButton.disabled=true;
      try{
        const r=await (await api('/v1/automation-pipeline/benefit-readiness')).json();
        if(r.format!=='openworkgraph.automation-portfolio-benefit-readiness.v1'||
           !Array.isArray(r.ideas)||typeof r.implemented_ideas_reviewed!=='number')
          throw new Error('Unexpected portfolio evidence readiness report.');
        const filter=select('owgPipelineBenefitReadinessFilter',[
          ['all','All implemented ideas'],...Object.keys(r.ideas_by_readiness).map(k=>[k,k.replaceAll('_',' ')])
        ]);
        const search=input('owgPipelineBenefitReadinessSearch','search','Find title or idea ID');
        const display=el('div');display.id='owgPipelineBenefitReadinessRows';
        const show=()=>{
          const term=search.value.trim().toLowerCase();
          const matched=r.ideas.filter(i=>(filter.value==='all'||i.readiness===filter.value)&&
            (!term||(i.title+' '+i.idea_id).toLowerCase().includes(term)));
          const t=el('table'),thead=el('thead'),tr=el('tr');
          ['Idea','Evidence readiness','Baseline / after','Source links missing',
            'Reported per-case effort change (hours/month)','Next human review'].forEach(v=>{
            const th=el('th',v);th.scope='col';tr.append(th);
          });thead.append(tr);const body=el('tbody');
          matched.slice(0,150).forEach(i=>{
            const row=el('tr'),th=el('th',i.title+' · '+i.idea_id);th.scope='row';row.append(th);
            const change=i.reported_effort_change_hours_per_month_at_after_volume;
            [i.readiness.replaceAll('_',' '),i.baseline_periods+' / '+i.after_periods,
             i.missing_source_references,
             change==null?'Not comparable':Number(change).toFixed(3),
             i.next_review_action].forEach(v=>row.append(el('td',String(v))));
            body.append(row);
          });t.append(thead,body);
          const wrap=el('div',null,'table-wrap');wrap.append(t);
          display.replaceChildren(el('p',matched.length+' matching ideas; displaying '+Math.min(150,matched.length)+
            '. Unlisted or filtered ideas were not marked complete.','muted'),
            ...(matched.length?[wrap]:[el('p','No matching ideas.','muted')]));
        };
        filter.onchange=search.oninput=show;
        const counts=Object.entries(r.ideas_by_readiness).map(([kind,n])=>n+' '+kind.replaceAll('_',' ')).join(' · ');
        const warning=r.comparison_complete?
          'This inventory reports entered evidence coverage, not independently verified savings or distinct cases.':
          'The evidence is unreadable, truncated or incomplete. No missing claims are certified clear.';
        benefitReadinessHost.replaceChildren(el('h4','Benefit evidence readiness'),
          el('p',r.implemented_ideas_reviewed+' implemented ideas. '+counts+'.'),
          el('p',r.candidate_overlap_pairs+' possible cross-idea overlap pairs; '+warning,'muted'),
          field('Readiness category',filter),field('Search implemented ideas',search),
          display,el('p',r.interpretation,'muted'));
        show();
      }catch(e){benefitReadinessHost.replaceChildren(el('p',e.message,'muted'));}
      finally{benefitReadinessButton.disabled=false;}
    };
    const dependencyScheduleButton=button('Review dependency schedule risks','secondary');
    dependencyScheduleButton.id='owgPipelineDependencySchedule';
    const dependencyScheduleHost=el('div');
    dependencyScheduleHost.id='owgPipelineDependencyScheduleResult';
    dependencyScheduleHost.setAttribute('role','status');
    dependencyScheduleHost.setAttribute('aria-live','polite');
    dependencyScheduleButton.onclick=async()=>{
      dependencyScheduleButton.disabled=true;
      const generation=refreshGeneration;
      try{
        const r=await (await api('/v1/automation-pipeline/dependency-schedule')).json();
        if(generation!==refreshGeneration)return;
        if(!Array.isArray(r.risk_candidates)||typeof r.active_ideas!=='number')
          throw new Error('Unexpected dependency schedule review');
        const name=id=>(latestIdeas.find(idea=>idea.idea_id===id)||{}).title||id;
        const lines=el('ul');
        for(const finding of r.risk_candidates){
          const path=finding.path.map(name).join(' → ');
          const basis='Target '+(finding.idea_target_quarter||'not set')+
            '; prerequisite '+(finding.dependency_target_quarter||'not set')+
            ' ('+finding.dependency_state.replaceAll('_',' ')+')';
          lines.appendChild(el('li',finding.kind.replaceAll('_',' ')+': '+path+'. '+basis+'.'));
        }
        const wavesHost=el('section');wavesHost.append(el('h5','Prerequisite planning waves'));
        const w=r.dependency_waves;
        if(w){
          wavesHost.append(el('p',w.layered_idea_count+' ideas assigned to '+w.wave_count+
            ' precedence waves · '+w.blocked_idea_count+' blocked · '+w.cycle_or_downstream_idea_count+
            ' unresolved solely by cycle precedence. These are not durations, dates or execution approvals.','muted'));
          if(w.cycle_groups?.length){
            const cycles=el('details');cycles.append(el('summary','Exact dependency cycles ('+w.cycle_groups.length+' groups)'));
            cycles.append(el('p',w.exact_cycle_member_count+' cycle members and '+w.exact_cycle_downstream_count+
              ' additional ideas depending on cycles, including ideas with separate state blockers.','muted'));
            const cycleList=el('ul');
            for(const group of w.cycle_groups)cycleList.append(el('li','Cycle group '+group.group+': '+group.idea_ids.map(name).join(', ')));
            cycles.append(cycleList);wavesHost.append(cycles);
          }
          const choose=el('select');choose.setAttribute('aria-label','Dependency planning wave');
          for(const group of w.waves){
            const option=el('option','Wave '+group.wave+' ('+group.idea_ids.length+' ideas)');
            option.value=String(group.wave);choose.append(option);
          }
          if(w.blocked_idea_count||w.cycle_or_downstream_idea_count){
            const option=el('option','Blocked or unresolved ideas');option.value='blocked';choose.append(option);
          }
          const waveRows=el('ul');
          const renderWave=()=>{
            waveRows.replaceChildren();
            const selected=w.ideas.filter(i=>choose.value==='blocked'?i.wave===null:String(i.wave)===choose.value);
            for(const idea of selected){
              const item=el('li',name(idea.idea_id)+' · '+idea.state.replaceAll('_',' ')+
                ' · '+idea.status.replaceAll('_',' ')+'. Target '+(idea.target_quarter||'not set')+
                '; latest declared target in unfinished chain '+(idea.latest_declared_target_in_unfinished_chain||'unknown')+'.');
              if(idea.unfinished_prerequisite_ids.length)item.append(el('p','Unfinished prerequisites: '+idea.unfinished_prerequisite_ids.map(name).join(', '),'muted'));
              if(idea.cycle_group!==null)item.append(el('p','Member of exact cycle group '+idea.cycle_group+'.','muted'));
              if(idea.depends_on_cycle)item.append(el('p','Depends on a cyclic prerequisite.','muted'));
              if(idea.direct_blockers.length)item.append(el('p','Direct blockers: '+idea.direct_blockers.map(b=>name(b.idea_id)+' ('+b.state.replaceAll('_',' ')+')').join(', '),'muted'));
              waveRows.append(item);
            }
          };
          if(choose.options.length){choose.addEventListener('change',renderWave);wavesHost.append(choose,waveRows);renderWave();}
          else wavesHost.append(el('p','No active ideas.'));
          wavesHost.append(el('p','Snapshot basis: '+w.portfolio_basis_sha256.slice(0,12)+'… · implementation states are reviewer declarations.','muted'));
        }
        const caveat=r.risk_candidates_truncated||!r.review_complete?
          'The review has unresolved integrity findings or bounded risk paths; unlisted issues may remain unknown.':
          'No live source was queried. These are plan consistency candidates, not forecast delivery dates.';
        dependencyScheduleHost.replaceChildren(el('h4','Dependency schedule review'),
          el('p',r.active_ideas+' active ideas; '+r.active_ideas_with_dependencies+
            ' declare dependencies, '+r.active_ideas_with_target_quarter+
            ' have target quarters; '+r.risk_candidates.length+' candidates shown.','muted'),
          ...(r.risk_candidates.length?[lines]:[]),
          el('p',caveat,'muted'),el('p',r.interpretation,'muted'),wavesHost);
      }catch(e){if(generation===refreshGeneration)dependencyScheduleHost.replaceChildren(el('p',e.message,'muted'));}
      finally{dependencyScheduleButton.disabled=false;}
    };
    card.append(row,resumeSection,auditSection,tools,planningFilters,status,counts,portfolioButton,portfolioHost,budgetScenarioButton,budgetScenarioHost,benefitOverlapButton,benefitOverlapHost,benefitReadinessButton,benefitReadinessHost,dependencyScheduleButton,dependencyScheduleHost,capacityButton,capacityHost,viewCount,list,comparison,detail);
    panel.appendChild(card);
    const aiHandoffHost=el('div');card.insertBefore(aiHandoffHost,row);
    const nativeSkillHost=el('div');card.insertBefore(nativeSkillHost,row);
    const nativeSkillReady=import('/native-skill-download.js')
      .then(module=>module.mountNativeSkillDownload(nativeSkillHost,api))
      .catch(()=>{nativeSkillHost.textContent='Native shadow skill preparation unavailable.';return null;});
    import('/ai-opportunity-handoff.js').then(module=>module.mountAiOpportunityHandoff(aiHandoffHost,post,
      async id=>{await refresh();await show(id);},async draft=>{
        const target=await nativeSkillReady;
        if(!target)throw Error('Native shadow skill preparation unavailable.');
        target.acceptReviewedProposal(draft);
      })).catch(()=>{aiHandoffHost.textContent='AI proposal handoff unavailable.';});

    let latestIdeas=[],refreshGeneration=0,resumeGeneration=0,auditGeneration=0,auditPageIndex=0,auditAbort=null;
    let loadedState=null,currentView=null;
    const compared=new Set();
    const auditPages=new Map();let auditBasis=null,triageGeneration=0;
    const pilotInspected=new Set(); // Ephemeral local inspection only; never approval or execution.
    const pilotDispositions=new Map(); // Ephemeral follow-up plans, never authorization.
    const auditSnapshot=()=>JSON.stringify(auditEligible().map(i=>
      [i.idea_id,i.revision,i.state,i.updated_at||'']));
    function updateCumulative(){
      const combined=combinedPortfolioHandoffReview(auditEligible(),auditPages,new Date().toISOString());
      auditCombinedDownload.disabled=!auditPages.size;
      auditCumulative.textContent=combined.reviewed_page_numbers.length+' of '+combined.page_count+
        ' pages explicitly reviewed · '+combined.reviewed_count+' of '+combined.eligible_count+
        ' loaded eligible opportunities. '+(combined.unreviewed_page_numbers.length?
          'Unreviewed pages: '+combined.unreviewed_page_numbers.join(', ')+'. ':
          'Every loaded page was checked. ')+
        (combined.incomplete_evidence_count?
          combined.incomplete_evidence_count+' rows have missing/incomplete evidence. ':'')+
        'This does not verify outcomes or authorize handoff.';
      renderTriage();
    }
    function renderTriage(){
      const triageRequest=++triageGeneration;
      auditTriageHost.replaceChildren();
      auditWorklistDownload.disabled=true;
      if(!auditPages.size){
        auditTriageFilter.disabled=true;
        const emptyOption=el('option','All explicitly reviewed opportunities');emptyOption.value='all';
        auditTriageFilter.replaceChildren(emptyOption);
        auditTriageSummary.textContent='Review at least one page to triage its blockers.';
        return;
      }
      if(auditBasis!==auditSnapshot()){
        auditTriageFilter.disabled=true;
        auditTriageSummary.textContent='The loaded opportunity list changed. Refresh and review pages again.';
        return;
      }
      try{
        const overview=portfolioReviewTriage(auditEligible(),auditPages,'all');
        const prior=auditTriageFilter.value;
        const choices=[['all','All explicitly reviewed opportunities'],
          ...overview.categories.map(item=>[item.key,item.label+' ('+item.count+')'])];
        auditTriageFilter.replaceChildren(...choices.map(([key,label])=>{
          const option=el('option',label);option.value=key;return option;
        }));
        auditTriageFilter.value=choices.some(([key])=>key===prior)?prior:'all';
        auditTriageFilter.disabled=false;
        auditWorklistDownload.disabled=false;
        const selected=portfolioReviewTriage(auditEligible(),auditPages,auditTriageFilter.value,24,auditTriageSearch.value);
        auditTriageSummary.textContent=selected.matching_count+' matching opportunities in '+
          selected.reviewed_count+' explicitly reviewed; '+selected.unreviewed_count+
          ' loaded opportunities on unreviewed pages are unknown. Showing '+
          selected.items.length+(selected.omitted_count?' of '+selected.matching_count+
          ' matches; '+selected.omitted_count+' omitted from this bounded view.':'.')+
          ' No independent outcome verification or authorization.';
        const table=el('table'),head=el('tr'),body=el('tbody');
        ['Opportunity','Reviewed page','Outstanding checks','Human review','Pilot inspection','Pilot follow-up'].forEach(name=>
          head.appendChild(el('th',name)));
        for(const item of selected.items){
          const idea=latestIdeas.find(i=>i.idea_id===item.idea_id);
          const tr=el('tr');
          [idea?.title||'Opportunity unavailable',String(item.page_number),
            item.blockers.map(key=>PORTFOLIO_TRIAGE_LABELS[key]).join(' · ')].forEach(value=>
            tr.appendChild(el('td',value)));
          const cell=el('td');
          const action=button(item.project_id?'Open exact project and check':'Open opportunity for review','secondary');
          action.setAttribute('aria-label','Review '+(auditTriageFilter.value==='all'?
            'outstanding checks':PORTFOLIO_TRIAGE_LABELS[auditTriageFilter.value])+
            ' for '+(idea?.title||'selected opportunity'));
          const request=auditGeneration,filterKey=auditTriageFilter.value;
          action.onclick=async()=>{
            if(request!==auditGeneration||triageRequest!==triageGeneration||
              auditBasis!==auditSnapshot()||!auditPages.has(item.page_number))return;
            try{
              const resume=item.project_id?{projectId:item.project_id,
                valid:()=>request===auditGeneration&&triageRequest===triageGeneration&&
                  auditBasis===auditSnapshot()}:null;
              await show(item.idea_id,resume);
              if(request!==auditGeneration||triageRequest!==triageGeneration)return;
              if(item.project_id&&!document.getElementById('owgPipelineJourneyProject')){
                auditStatus.textContent='The exact recorded project is unavailable. No other export was substituted.';
                return;
              }
              const key=filterKey==='all'?item.blockers[0]:filterKey;
              const target=document.getElementById('owgPipelineBlockerItem_'+key)||
                document.querySelector('#owgPipelineDetail h3');
              if(target){target.tabIndex=-1;target.scrollIntoView?.({block:'nearest'});target.focus?.();}
              auditStatus.textContent='Opened a local human-review check for the selected opportunity. No blocker was cleared and no provider or agent action occurred.';
            }catch(e){if(request===auditGeneration)auditStatus.textContent=e.message;}
          };
          cell.appendChild(action);tr.appendChild(cell);
          const inspectedCell=el('td');
          const inspected=el('input');inspected.type='checkbox';
          inspected.checked=pilotInspected.has(item.idea_id);
          inspected.setAttribute('aria-label','Mark locally inspected (not approved) '+(idea?.title||item.idea_id));
          inspected.onchange=()=>{
            if(request!==auditGeneration||triageRequest!==triageGeneration||
              auditBasis!==auditSnapshot()||!auditPages.has(item.page_number)){
              inspected.checked=false;return;
            }
            if(inspected.checked)pilotInspected.add(item.idea_id);
            else {pilotInspected.delete(item.idea_id);pilotDispositions.delete(item.idea_id);}
            disposition.value=pilotDispositions.get(item.idea_id)||'';
            disposition.disabled=!inspected.checked;
            auditStatus.textContent=pilotInspected.size+' opportunities marked inspected locally; no blockers cleared, approvals or agent actions.';
          };
          const inspectedLabel=el('label');
          inspectedLabel.append(inspected,document.createTextNode(' Inspected locally'));
          inspectedCell.appendChild(inspectedLabel);tr.appendChild(inspectedCell);
          const dispositionCell=el('td');
          const disposition=select('owgPilotDisposition_'+item.idea_id,[
            ['','Undecided'],['needs_evidence','Needs evidence'],
            ['supervised_trial_candidate','Candidate for supervised trial'],
            ['not_suitable','Not suitable for trial']]);
          disposition.setAttribute('aria-label','Local pilot follow-up for '+(idea?.title||item.idea_id));
          disposition.value=pilotDispositions.get(item.idea_id)||'';
          disposition.disabled=!inspected.checked;
          disposition.onchange=()=>{
            if(request!==auditGeneration||triageRequest!==triageGeneration||
              auditBasis!==auditSnapshot()||!auditPages.has(item.page_number)||
              !pilotInspected.has(item.idea_id)){
              disposition.value='';disposition.disabled=true;return;
            }
            if(disposition.value)pilotDispositions.set(item.idea_id,disposition.value);
            else pilotDispositions.delete(item.idea_id);
            auditStatus.textContent='Local follow-up plan updated. No consent, approval or agent action.';
          };
          dispositionCell.appendChild(disposition);tr.appendChild(dispositionCell);
          body.appendChild(tr);
        }
        const thead=el('thead');thead.appendChild(head);table.append(thead,body);
        const wrap=el('div',null,'table-wrap');wrap.appendChild(table);
        auditTriageHost.appendChild(wrap);
      }catch(e){
        auditTriageFilter.disabled=true;
        auditTriageSummary.textContent='Cannot triage this review: '+e.message;
      }
    }
    auditWorklistDownload.onclick=()=>{
      if(!auditPages.size||auditBasis!==auditSnapshot()){
        auditWorklistDownload.disabled=true;
        auditStatus.textContent='The reviewed pilot worklist is stale. Explicitly review the loaded pages again.';
        return;
      }
      try{
        // Export every explicitly inspected row, not the 24-row UI preview.
        const combined=combinedPortfolioHandoffReview(auditEligible(),auditPages,new Date().toISOString());
        portfolioReviewTriage(auditEligible(),auditPages,'all');
        const record=pilotDispositionWorklist(auditEligible(),auditPages,
          pilotInspected,pilotDispositions,combined.generated_at);
        const serialized=JSON.stringify(record);
        if(serialized.length>65536)throw new Error('Pilot worklist exceeds 64 KiB; use reviewed-page exports instead.');
        const url=URL.createObjectURL(new Blob([serialized],{type:'application/json'}));
        const link=el('a');link.href=url;link.download='owg-human-pilot-worklist.json';
        document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
        auditStatus.textContent='Downloaded explicitly reviewed human pilot checks only. No consent, provider read, approval or agent action.';
      }catch(e){auditStatus.textContent='Cannot export pilot worklist: '+e.message;}
    };
    auditTriageFilter.onchange=renderTriage;
    auditTriageSearch.oninput=renderTriage;
    function clearCumulative(reason){
      ++auditGeneration;
      if(auditAbort)auditAbort.abort();
      auditAbort=null;auditPages.clear();pilotInspected.clear();pilotDispositions.clear();auditBasis=null;auditPageIndex=0;
      auditHost.replaceChildren();auditButton.disabled=false;
      auditStatus.textContent=reason||'Review cleared. Explicitly check pages again.';
      syncAuditPages();updateCumulative();
    }
    auditCombinedDownload.onclick=()=>{
      if(!auditPages.size)return;
      if(auditBasis!==auditSnapshot()){
        clearCumulative('Loaded opportunities changed; the prior multi-page review was invalidated.');
        return;
      }
      try{
        const record=combinedPortfolioHandoffReview(auditEligible(),auditPages,new Date().toISOString());
        const body=JSON.stringify(record);
        if(body.length>65536)throw new Error('Combined review exceeds 64 KiB. Download individual pages instead.');
        const url=URL.createObjectURL(new Blob([body],{type:'application/json'}));
        const link=el('a');link.href=url;link.download='owg-portfolio-reviewed-pages.json';
        document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
        auditStatus.textContent='Downloaded only explicitly reviewed pages. No source read, approval or agent action occurred.';
      }catch(e){auditStatus.textContent=e.message;}
    };
    function auditEligible(){
      return latestIdeas.filter(idea=>['approved','implemented'].includes(idea.state))
        .sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||''))||
          String(a.idea_id).localeCompare(String(b.idea_id)));
    }
    function syncAuditPages(){
      const count=auditEligible().length,pages=Math.ceil(count/12);
      auditPageIndex=Math.max(0,Math.min(auditPageIndex,Math.max(0,pages-1)));
      const start=auditPageIndex*12,end=Math.min(count,start+12);
      auditPrevious.disabled=auditPageIndex===0;
      auditNext.disabled=!pages||auditPageIndex>=pages-1;
      auditPage.textContent=pages?
        'Page '+(auditPageIndex+1)+' of '+pages+' · opportunities '+(start+1)+'–'+end+
          ' of '+count+' eligible in the loaded state':
        'No eligible opportunities loaded.';
    }
    resumeFile.onchange=()=>{
      resumeGeneration++;
      // Cancel a pending resume if the operator changes its file. Existing
      // authenticated reads may finish, but stale results cannot render.
      if(resumeButton.disabled)detailGeneration++;
      resumeStatus.textContent='File selected. Resume explicitly to recheck the exact saved idea and project.';
    };
    resumeButton.onclick=async()=>{
      const file=resumeFile.files?.[0];
      if(!file||!file.size||file.size>65536){
        resumeStatus.textContent='Select a non-empty OWG review bundle JSON file no larger than 64 KiB.';
        return;
      }
      const request=++resumeGeneration;
      resumeButton.disabled=true;
      resumeStatus.textContent='Validating the local bundle before opening any saved project…';
      try{
        const value=JSON.parse(await file.text());
        if(value?.format!==REVIEW_BUNDLE_FORMAT)
          throw new Error('Choose an OWG resumable human review bundle, not a standalone review packet.');
        const {saved,inspectionRecord}=unpackHandoffReviewInput(value);
        compareHandoffReviewPackets(saved,saved);
        if(!inspectionRecord||inspectionRecord.idea_id!==saved.idea.idea_id||
          inspectionRecord.project_id!==saved.project.project_id||
          inspectionRecord.scope!=='local_human_inspection_only'||
          inspectionRecord.execution_authorized!==false||
          inspectionRecord.source_read_authorized!==false||
          inspectionRecord.human_approval_granted!==false||
          inspectionRecord.verified_execution!==false||
          inspectionRecord.verified_business_outcomes!==false)
          throw new Error('Bundle inspection identity or safety fields do not match the saved review.');
        if(request!==resumeGeneration)return;
        resumeStatus.textContent='Opening the exact saved idea and project for a fresh local comparison…';
        await show(saved.idea.idea_id,{file,projectId:saved.project.project_id,
          valid:()=>request===resumeGeneration});
      }catch(e){
        if(request===resumeGeneration)
          resumeStatus.textContent='Could not resume review: '+(e instanceof SyntaxError?'Invalid JSON.':e.message);
      }finally{resumeButton.disabled=false;}
    };

    auditButton.onclick=async()=>{
      // Cancel superseded page reads instead of allowing stacked page batches.
      // The generation check also guards a response already in flight.
      if(auditAbort)auditAbort.abort();
      const controller=new AbortController();auditAbort=controller;
      const request=++auditGeneration;
      auditButton.disabled=true;
      auditHost.replaceChildren();
      const snapshot=auditSnapshot();
      if(auditBasis!==null&&auditBasis!==snapshot){auditPages.clear();auditBasis=null;}
      const eligible=auditEligible(),start=auditPageIndex*12;
      const selected=eligible.slice(start,start+12);
      const unreviewedBefore=start,unreviewedAfter=eligible.length-start-selected.length;
      const omitted=unreviewedBefore+unreviewedAfter;
      const pageNumber=auditPageIndex+1,pageCount=Math.ceil(eligible.length/12);
      auditPages.delete(pageNumber);updateCumulative();
      syncAuditPages();
      if(!selected.length){
        auditStatus.textContent='No approved or implemented opportunities in the currently loaded state.';
        auditButton.disabled=false;return;
      }
      auditStatus.textContent='Reading '+selected.length+' authenticated local evidence journeys (maximum three at once)…';
      const rows=[];
      try{
        for(let offset=0;offset<selected.length;offset+=3){
          const batch=await Promise.all(selected.slice(offset,offset+3).map(async idea=>{
            try{
              const journey=await (await api('/v1/automation-pipeline/'+
                encodeURIComponent(idea.idea_id)+'/journey',{signal:controller.signal})).json();
              return portfolioHandoffRow(idea,journey);
            }catch(_){
              return {idea_id:idea.idea_id,project_id:null,status:'unavailable',
                blockers:[],stage_review_count:null,spec_sha256:null,archive_sha256:null};
            }
          }));
          if(request!==auditGeneration)return;
          rows.push(...batch);
        }
        if(request!==auditGeneration||snapshot!==auditSnapshot())return;
        if(auditBasis===null)auditBasis=snapshot;
        auditPages.set(pageNumber,{reviewed_at:new Date().toISOString(),items:rows});
        updateCumulative();
        const incomplete=omitted>0||rows.some(r=>r.status!=='human_review_required');
        const table=el('table'),thead=el('thead'),head=el('tr'),tbody=el('tbody');
        ['Opportunity','Coverage','Human review checks','Dependency stages needing review',''].forEach(label=>head.appendChild(el('th',label)));
        thead.appendChild(head);
        for(const row of rows){
          const idea=selected.find(i=>i.idea_id===row.idea_id),tr=el('tr');
          const label=row.status==='human_review_required'?'Review required':
            row.status==='no_export'?'No recorded project':'Evidence unavailable / incomplete';
          const cells=[idea.title,label,
            row.status==='human_review_required'?row.blockers.length+' · '+row.blockers.join(', ').replaceAll('_',' '):
              row.status==='no_export'?'Export a reviewed project first':'Cannot assess',
            row.stage_review_count===null?'Unknown':String(row.stage_review_count)+' of 7'];
          cells.forEach(value=>tr.appendChild(el('td',value)));
          const actionCell=el('td');
          const open=button(row.project_id?'Open exact project review':'Open opportunity','secondary');
          open.setAttribute('aria-label',(row.project_id?'Open exact project review for ':'Open opportunity ')+idea.title);
          open.onclick=()=>{
            if(request!==auditGeneration)return;
            const resume=row.project_id?{projectId:row.project_id,valid:()=>request===auditGeneration}:null;
            show(row.idea_id,resume).catch(e=>{if(request===auditGeneration)auditStatus.textContent=e.message;});
          };
          actionCell.appendChild(open);tr.appendChild(actionCell);tbody.appendChild(tr);
        }
        table.append(thead,tbody);
        const wrap=el('div',null,'table-wrap');wrap.appendChild(table);
        const exportButton=button('Download minimized portfolio handoff review JSON','secondary');
        exportButton.id='owgPipelinePortfolioAuditDownload';
        const record={format:'openworkgraph.portfolio-handoff-review.v1',
          generated_at:new Date().toISOString(),scope:'read_only_human_review',
          reviewed_count:rows.length,eligible_count:eligible.length,omitted_count:omitted,
          page_number:pageNumber,page_count:pageCount,
          page_start_index:start+1,page_end_index:start+rows.length,
          unreviewed_before_count:unreviewedBefore,
          unreviewed_after_count:unreviewedAfter,
          complete_coverage:!incomplete,execution_authorized:false,
          source_read_authorized:false,verified_execution:false,
          verified_business_outcomes:false,
          items:rows.map(r=>({...r}))};
        exportButton.onclick=()=>{
          if(request!==auditGeneration)return;
          const body=JSON.stringify(record);
          if(body.length>65536){auditStatus.textContent='Review exceeds the 64 KiB export limit.';return;}
          const url=URL.createObjectURL(new Blob([body],{type:'application/json'}));
          const link=el('a');link.href=url;link.download='owg-portfolio-handoff-review.json';
          document.body.appendChild(link);link.click();link.remove();
          setTimeout(()=>URL.revokeObjectURL(url),1000);
          auditStatus.textContent='Downloaded a point-in-time review; no approval or source read was granted.';
        };
        auditHost.replaceChildren(wrap,exportButton);
        auditStatus.textContent=rows.length+' reviewed of '+eligible.length+' eligible in the currently loaded state. '+
          'Page '+pageNumber+' of '+pageCount+' ('+(start+1)+'–'+(start+rows.length)+'). '+
          (incomplete?'Partial coverage: '+omitted+' on other pages; unavailable or missing project evidence is not verified.':
            'All loaded eligible journeys checked; execution and business outcomes remain unverified.')+
          ' Refresh after changes; nothing was executed.';
      }finally{if(request===auditGeneration){auditAbort=null;auditButton.disabled=false;}}
    };
    auditPrevious.onclick=()=>{
      if(auditPageIndex===0)return;
      auditPageIndex--;auditButton.onclick();
    };
    auditNext.onclick=()=>{
      if((auditPageIndex+1)*12>=auditEligible().length)return;
      auditPageIndex++;auditButton.onclick();
    };
    function setPlanningOptions(){
      for(const [key,,missing] of PLANNING_FILTERS){
        const control=planning[key],selected=control.value;
        const values=new Set(latestIdeas.map(i=>i.planning?.[key]||''));
        const choices=[['','Any'],[JSON.stringify(''),missing],...[...values].filter(Boolean).sort((a,b)=>a.localeCompare(b)).map(value=>[JSON.stringify(value),value])];
        if(selected&&!choices.some(([key])=>key===selected))choices.push([selected,JSON.parse(selected)+' (none in this state)']);
        control.replaceChildren(...choices.map(([key,text])=>{const option=el('option',text);option.value=key;return option;}));
        control.value=selected;
      }
    }
    function render(){
      const selectedPlanning=Object.fromEntries(Object.entries(planning).filter(([,control])=>control.value!=='')
        .map(([key,control])=>[key,JSON.parse(control.value)]));
      const options={query:search.value.trim(),quadrant:quadrant.value,sort:sort.value,planning:selectedPlanning};
      const ideas=portfolioView(latestIdeas,options);
      currentView=loadedState===filter.value?{ideas,scope:{state:loadedState||null,...options},total:latestIdeas.length}:null;
      shortlistLink.disabled=shortlistJson.disabled=!currentView||!ideas.length;
      viewCount.textContent=currentView?ideas.length+' shown of '+latestIdeas.length+' in selected state. Shortlist downloads follow the current filters and sort.':
        'Shortlist downloads are available after the selected state loads.';
      const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('tr');
      ['Compare','Idea','State','Ease','Modeled hours / month','Priority','Planning',''].forEach(h=>head.appendChild(el('th',h)));
      const thead=el('thead');thead.appendChild(head);const body=el('tbody');
      ideas.forEach(idea=>{
        const tr=el('tr');const open=button('Open','secondary');open.setAttribute('aria-label','Open idea '+idea.title);open.onclick=()=>show(idea.idea_id).catch(e=>{status.textContent=e.message;});
        const choose=input('owgCompare_'+idea.idea_id,'checkbox');choose.checked=compared.has(idea.idea_id);
        choose.disabled=compared.size>=4&&!choose.checked;choose.setAttribute('aria-label','Compare idea '+idea.title);
        choose.onchange=()=>{
          if(choose.checked){if(compared.size>=4)return;compared.add(idea.idea_id);}else compared.delete(idea.idea_id);
          render();document.getElementById('owgCompare_'+idea.idea_id)?.focus();
        };
        const choiceCell=el('td');choiceCell.appendChild(choose);tr.appendChild(choiceCell);
        [idea.title,STATE_LABELS[idea.state],idea.score?idea.score.ease_score:'—',idea.score?idea.score.modeled_hours_per_month:'—',idea.score?QUADRANTS[idea.score.quadrant]:'Not assessed'].forEach(v=>tr.appendChild(el('td',v)));
        const planningLabels=PLANNING_FILTERS.filter(([key])=>idea.planning?.[key]).map(([key,label])=>label+' · '+idea.planning[key]);
        tr.appendChild(el('td',planningLabels.join(' / ')||'—'));
        const cell=el('td');cell.appendChild(open);tr.appendChild(cell);body.appendChild(tr);
      });
      t.append(thead,body);wrap.appendChild(t);list.replaceChildren(ideas.length?wrap:el('div','No ideas in this view.','muted'));
      const selected=[...compared].map(id=>latestIdeas.find(i=>i.idea_id===id)).filter(Boolean);
      const clear=button('Clear comparison','secondary');clear.id='owgPipelineCompareClear';clear.disabled=!selected.length;
      clear.onclick=()=>{compared.clear();render();};
      comparison.replaceChildren(el('h4','Compare opportunities'),
        el('div',selected.length+' of 4 selected. View filters may hide selected rows. Changing state clears ideas unavailable in that state. Values are from the last list refresh.','muted'),clear);
      if(selected.length<2){comparison.appendChild(el('div','Select at least two ideas to compare their saved assessments.','muted'));return;}
      const compareWrap=el('div',null,'table-wrap'),compareTable=el('table');compareTable.id='owgPipelineComparisonTable';
      const compareHead=el('thead'),compareHeader=el('tr');
      ['Assessment basis',...selected.map(i=>i.title)].forEach(title=>{const th=el('th',title);th.scope='col';compareHeader.appendChild(th);});
      compareHead.appendChild(compareHeader);const compareBody=el('tbody');
      for(const [label,...values] of comparisonRows(selected)){
        const tr=el('tr'),th=el('th',label);th.scope='row';tr.appendChild(th);
        values.forEach(value=>tr.appendChild(el('td',value)));compareBody.appendChild(tr);
      }
      compareTable.append(compareHead,compareBody);compareWrap.appendChild(compareTable);
      comparison.append(el('p','Modelled values use each idea’s own entered assumptions. Cases and benefits may overlap; no portfolio savings total or recommendation is inferred. Compliance declarations are not completed reviews.','muted'),compareWrap);
    }

    async function refresh(){
      clearCumulative('Portfolio review cleared; explicitly check again after the list refresh.');
      capacityGeneration++;if(capacityHost.childNodes.length)capacityHost.replaceChildren(el('p','Resource plan cleared; reload the portfolio before preparing another scenario.','muted'));
      if(dependencyScheduleHost.childNodes.length)dependencyScheduleHost.replaceChildren(el('p','Dependency review cleared; run it again after the portfolio refresh.','muted'));
      const generation=++refreshGeneration;
      const requested=filter.value;
      loadedState=currentView=null;shortlistLink.disabled=shortlistJson.disabled=true;
      viewCount.textContent='Shortlist downloads are available after the selected state loads.';
      const q=requested?'?state='+encodeURIComponent(requested):'';
      const r=await (await api('/v1/automation-pipeline'+q)).json();
      if(generation!==refreshGeneration)return false;
      counts.textContent=Object.entries(r.counts).filter(([,n])=>n).map(([s,n])=>STATE_LABELS[s]+': '+n).join(' · ')||'No ideas yet.';
      latestIdeas=r.ideas;loadedState=requested;setPlanningOptions();
      for(const id of compared)if(!latestIdeas.some(i=>i.idea_id===id))compared.delete(id);
      syncAuditPages();updateCumulative();render();return true;
    }

    let detailGeneration=0;
    // Unsaved planning per idea, kept in memory so any re-render of the same idea at the same revision
    // (a list refresh, another Open click, a late response) restores what the person is still typing.
    const planningDrafts=new Map();
    async function show(ideaId,resume=null){
      if(!resume)resumeGeneration++;
      const generation=++detailGeneration;
      const idea=await (await api('/v1/automation-pipeline/'+encodeURIComponent(ideaId))).json();
      if(generation!==detailGeneration)return;
      const box=el('div');box.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin-top:8px';
      box.appendChild(el('h3',idea.title));
      if(idea.description)box.appendChild(el('div',idea.description));
      box.appendChild(el('div','State: '+STATE_LABELS[idea.state]+' · revision '+idea.revision+(idea.source.reference?' · from '+idea.source.reference:''),'muted'));
      {
        const p=idea.planning||{};
        const section=el('section');section.id='owgPipelinePlanning';section.setAttribute('aria-label','Planning');
        section.dataset.ideaId=idea.idea_id;section.dataset.revision=String(idea.revision);
        const remember=()=>{section.dataset.dirty='true';const values={};
          section.querySelectorAll('input,select').forEach(node=>{if(node.id)values[node.id]=node.type==='checkbox'?node.checked:node.value;});
          planningDrafts.set(idea.idea_id,{revision:idea.revision,values});};
        section.addEventListener('input',remember);section.addEventListener('change',remember);
        section.appendChild(el('h4','Planning'));
        const owner=input('owgPlanOwner','text','Who looks after it');owner.maxLength=80;owner.value=p.owner||'';
        const team=input('owgPlanTeam','text','Team');team.maxLength=80;team.value=p.team||'';
        const quarter=input('owgPlanQuarter','text','2027-Q1');quarter.value=p.target_quarter||'';quarter.pattern='\\d{4}-Q[1-4]';
        const amount=input('owgPlanBudget','number','Amount');amount.min='0';amount.step='any';amount.value=p.budget?String(p.budget.amount):'';
        const currency=input('owgPlanCurrency','text','EUR');currency.maxLength=3;currency.value=p.budget?p.budget.currency:'';
        const basis=select('owgPlanBasis',[['estimate','Estimate'],['approved_budget','Approved budget']]);basis.value=p.budget?p.budget.basis:'estimate';
        const deps=el('fieldset');deps.id='owgPlanDepends';deps.appendChild(el('legend','Depends on'));
        const others=latestIdeas.filter(i=>i.idea_id!==idea.idea_id);
        others.forEach(o=>{const box2=input('owgPlanDep_'+o.idea_id,'checkbox');box2.value=o.idea_id;box2.checked=(p.depends_on||[]).includes(o.idea_id);
          const label=el('label',' '+o.title+' ('+STATE_LABELS[o.state]+')');label.htmlFor=box2.id;const line=el('div');line.append(box2,label);deps.appendChild(line);});
        (p.depends_on||[]).filter(d=>!others.some(o=>o.idea_id===d)).forEach(d=>{const box2=input('owgPlanDep_'+d,'checkbox');box2.value=d;box2.checked=true;
          const label=el('label',' '+d+' (not in this list view)');label.htmlFor=box2.id;const line=el('div');line.append(box2,label);deps.appendChild(line);});
        if(!deps.querySelector('input'))deps.appendChild(el('div','No other ideas to depend on.','muted'));
        const save=button('Save planning','secondary');save.id='owgPlanSave';
        const planStatus=el('div',null,'muted');planStatus.id='owgPlanStatus';planStatus.setAttribute('role','status');planStatus.setAttribute('aria-live','polite');
        save.onclick=async()=>{
          const body={owner:owner.value,team:team.value,target_quarter:quarter.value.trim(),expected_revision:idea.revision,
            depends_on:[...deps.querySelectorAll('input:checked')].map(i=>i.value)};
          if(amount.value!=='')body.budget={amount:Number(amount.value),currency:currency.value.trim().toUpperCase(),basis:basis.value};
          save.disabled=true;
          try{await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/planning',body);planningDrafts.delete(idea.idea_id);await refresh();await show(idea.idea_id);
            document.getElementById('owgPlanStatus').textContent='Planning saved. Owners and teams are labels; they grant no access.';}
          catch(e){planStatus.textContent=e.message;save.disabled=false;}
        };
        const grid=el('div',null,'row');grid.style.alignItems='end';
        grid.append(field('Owner',owner),field('Team',team),field('Target quarter',quarter),field('Budget',amount),field('Currency (ISO code)',currency),field('Budget basis',basis));
        section.append(grid,deps,save,planStatus,el('div','Budgets keep their currency and are never converted. An idea can be marked implemented only after the ideas it depends on.','muted'));
        box.appendChild(section);
      }
      if(idea.process_reference){
        const ref=idea.process_reference;
        const source=el('div');source.id='owgPipelineProcessStatus';
        const labels={current:'Current saved revision',changed:'Process changed since this idea was created',
          unavailable:'Linked revision is unavailable',reference_mismatch:'Linked reference no longer matches',
          check_failed:'Process status could not be checked'};
        source.appendChild(el('div','Linked process: '+ref.workflow_id+' · revision '+ref.revision));
        const message=el('div','Checking linked process…','muted');source.appendChild(message);
        const check=button('Refresh process status','secondary');
        const open=button('Open linked saved process','secondary');open.disabled=true;
        open.onclick=()=>Promise.resolve(window.owgProcessCatalog?.openSaved(ref)).then(()=>{
          document.getElementById('owgCatalogEditor')?.scrollIntoView({block:'start'});
        }).catch(e=>{status.textContent=e.message;});
        const update=async()=>{
          check.disabled=true;
          try{
            const result=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/process-status')).json();
            if(generation!==detailGeneration)return;
            message.textContent=(labels[result.status]||'Process status is unknown')+(result.latest_revision?' · latest revision '+result.latest_revision:'')+'. Checked now; decisions recheck the source.';
            open.disabled=!['current','changed'].includes(result.status);
          }catch(e){if(generation===detailGeneration){message.textContent='Process status could not be checked.';open.disabled=true;}}
          finally{check.disabled=false;}
        };
        check.onclick=()=>update();source.append(check,open);box.appendChild(source);update();
      }else if(idea.source.kind==='process_catalog'){
        box.appendChild(el('div','This source is a descriptive reference only; no saved process revision is linked.','muted'));
      }
      if(['approved','implemented'].includes(idea.state)){
        const value=el('section');value.id='owgPipelineRealized';value.setAttribute('aria-label','Realized value');
        value.appendChild(el('h4','Realized value'));
        const host=el('div');host.id='owgRealizedReport';
        const kind=select('owgMeasPeriod',[['baseline','Baseline (before)'],['after','After implementation']]);
        const start=input('owgMeasStart','date'),end=input('owgMeasEnd','date');
        const cases=input('owgMeasCases','number','Cases');cases.min='0';cases.step='1';
        const minutes=input('owgMeasMinutes','number','Minutes per case');minutes.min='0';minutes.step='any';
        const source=select('owgMeasSource',[['entered','Entered by hand'],['time_tracking','Time tracking'],['system_report','System report'],['event_log','Event log']]);
        const ref=input('owgMeasRef','text','Optional reference');ref.maxLength=200;
        const addMeasure=button('Record measurement','secondary');addMeasure.id='owgMeasAdd';
        const measStatus=el('div',null,'muted');measStatus.id='owgMeasStatus';measStatus.setAttribute('role','status');measStatus.setAttribute('aria-live','polite');
        const fmt=v=>v===null||v===undefined?'—':String(v);
        const draw=r=>{
          const parts=[];
          if(r.measurements.length){
            const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('tr');t.id='owgRealizedTable';
            ['Period','Dates','Cases','Hours','Hours / month','Minutes / case','Source',''].forEach(h=>{const th=el('th',h);th.scope='col';head.appendChild(th);});
            const thead=el('thead');thead.appendChild(head);const body=el('tbody');
            r.measurements.forEach(m=>{const tr=el('tr');
              [m.period==='after'?'After':'Baseline',m.start+' – '+m.end,m.cases,m.hours,m.hours_per_month,fmt(m.minutes_per_case_observed),m.source.replace('_',' ')+(m.source_reference?' · '+m.source_reference:'')].forEach(v=>tr.appendChild(el('td',String(v))));
              const del=button('Remove','secondary');del.setAttribute('aria-label','Remove '+m.period+' measurement from '+m.start);
              del.onclick=async()=>{try{draw(await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/measurements/'+encodeURIComponent(m.measurement_id)+'/delete',{}));}catch(e){measStatus.textContent=e.message;}};
              const cell=el('td');cell.appendChild(del);tr.appendChild(cell);body.appendChild(tr);});
            t.append(thead,body);wrap.appendChild(t);parts.push(wrap);
          }else parts.push(el('div','No measurements yet. Record a baseline from before the change, then periods after it.','muted'));
          const adjusted=r.coverage_adjusted;
          if(adjusted){
            const analysis=el('section');analysis.id='owgRealizedCoverageAdjusted';
            analysis.setAttribute('aria-label','Coverage-aware benefit comparison');
            analysis.appendChild(el('h5','Coverage-aware comparison'));
            if(adjusted.status==='ready'&&adjusted.comparison){
              const cc=adjusted.comparison,stats=el('ul');stats.id='owgRealizedWeightedComparison';
              stats.append(
                el('li','Pooled baseline: '+fmt(cc.baseline_minutes_per_case)+' min/case across '+
                  fmt(adjusted.coverage.baseline.calendar_days_reported)+' days; after: '+
                  fmt(cc.after_minutes_per_case)+' min/case across '+
                  fmt(adjusted.coverage.after.calendar_days_reported)+' days.'),
                el('li','Effort change at after-volume: '+
                  fmt(cc.hours_per_month_from_per_case_change_at_after_volume)+
                  ' hours/month (entered model: '+fmt(cc.modeled_hours_per_month)+').'),
                el('li','Duration-weighted total hours/month: baseline '+fmt(cc.baseline_hours_per_month)+
                  ', after '+fmt(cc.after_hours_per_month)+'; monthly case-volume change '+
                  fmt(cc.case_volume_change_per_month)+'.')
              );
              analysis.appendChild(stats);
              const sensitivity=adjusted.sensitivity;
              if(sensitivity){
                const decomposition=sensitivity.monthly_hours_change_decomposition;
                const coverage=sensitivity.calendar_coverage;
                const omitted=sensitivity.leave_one_period_out;
                analysis.append(el('h5','Measurement sensitivity and volume effects'),
                  el('p','Reported baseline minus after hours/month: '+fmt(decomposition.reported_baseline_minus_after_hours)+
                    ' = effort per case '+fmt(decomposition.effort_per_case_component_hours)+
                    ' + case volume '+fmt(decomposition.case_volume_component_hours)+' (rounded).'),
                  el('p','Measured days: baseline '+coverage.baseline.measured_days+' of '+coverage.baseline.span_days+
                    '; after '+coverage.after.measured_days+' of '+coverage.after.span_days+
                    '. Unmeasured days are not treated as zero activity.'),
                  el('p','Declared measurement sources: baseline '+Object.keys(coverage.baseline.source_kind_exposure).join(', ')+
                    '; after '+Object.keys(coverage.after.source_kind_exposure).join(', ')+
                    (sensitivity.declared_source_kind_sets_match?'.':'. Source composition differs; review comparability.'),'muted'));
                if(omitted.scenario_count){
                  analysis.append(el('p','Leave-one-period-out: '+omitted.scenario_count+' comparisons, effort change at after-volume '+
                    omitted.scenario_range_hours_per_month.map(fmt).join(' to ')+' hours/month.'+
                    (omitted.direction_differs_from_full_comparison?' Direction differs from the full comparison; review influential periods.':''),
                    'muted'));
                  const details=el('details');details.append(el('summary','Inspect omitted-period scenarios'));
                  const list=el('ul');
                  for(const scenario of omitted.scenarios){
                    const measurement=r.measurements.find(m=>m.measurement_id===scenario.measurement_id);
                    list.append(el('li',scenario.omitted_period+' '+(measurement?measurement.start+' – '+measurement.end:scenario.measurement_id)+
                      ': '+fmt(scenario.effort_change_at_after_volume_hours_per_month)+' hours/month · '+scenario.direction.replaceAll('_',' ')));
                  }
                  details.append(list);analysis.append(details);
                }
                if(!omitted.baseline_period_omission_supported||!omitted.after_period_omission_supported)
                  analysis.append(el('p','At least two periods on each side are needed for omission sensitivity on both sides.','muted'));
              }
            }else{
              const reasons={
                missing_periods:'Record both a baseline and an after period to compare.',
                overlapping_periods:'Overlapping dates within or across period types. Correct duplicate calendar coverage before interpreting adjusted results.',
                zero_case_period:'At least one measurement has zero cases. Verify the case-exposure denominator before comparing.'
              };
              const warning=el('p',reasons[adjusted.status]||'Coverage-adjusted comparison unavailable.','muted');
              warning.id='owgRealizedCoverageWarning';warning.setAttribute('role','status');
              analysis.appendChild(warning);
              if(adjusted.status==='overlapping_periods')analysis.appendChild(el('p',
                fmt(adjusted.overlap_pairs.length)+' overlapping measurement pair(s); remove or replace conflicting rows.','muted'));
            }
            analysis.appendChild(el('p','Coverage-aware figures pool entered hours and cases across non-overlapping days. Source labels are not independently verified; this is not causal impact, certified savings or a confidence interval.','muted'));
            parts.push(analysis);
          }
          const c=r.comparison;
          if(c){
            parts.push(el('h5','Legacy equal-weight-period comparison'));
            const list=el('ul');list.id='owgRealizedComparison';
            list.append(el('li','Minutes per case: '+fmt(c.minutes_per_case_change)+' fewer after (baseline '+fmt(r.baseline.minutes_per_case)+', after '+fmt(r.after.minutes_per_case)+').'),
              el('li','At the after volume that is '+fmt(c.hours_per_month_from_per_case_change_at_after_volume)+' hours a month; the assessment modeled '+fmt(c.modeled_hours_per_month)+(c.observed_vs_modeled_ratio!==null?' (ratio '+c.observed_vs_modeled_ratio+').':'.')),
              el('li','Total hours a month changed by '+fmt(c.observed_hours_per_month_change)+' (range '+c.observed_change_range.join(' to ')+'); case volume changed by '+fmt(c.case_volume_change_per_month)+' a month.'));
            parts.push(list);
            if(c.single_period_each)parts.push(el('div','Only one baseline and one after period: no range of variation yet.','muted'));
          }else if(r.measurements.length)parts.push(el('div','Record at least one baseline and one after period to compare.','muted'));
          if(idea.state==='implemented'){
            const launch=button('Review quality, adoption and cost','secondary');
            launch.id='owgQualityBenefitOpen';
            const scenarioHost=el('div');scenarioHost.id='owgQualityBenefitResult';
            let invocation=0;
            launch.onclick=async()=>{
              const current=++invocation;scenarioHost.replaceChildren(el('p','Reading retained measurement snapshot…','muted'));
              const active=()=>current===invocation&&generation===detailGeneration&&scenarioHost.isConnected;
              const path='/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/quality-benefit';
              try{
                const inv=await(await api(path+'/inventory')).json();
                if(!active())return;
                if(inv.idea_revision!==idea.revision)throw Error('Idea changed; reopen it before comparing.');
                const output=el('div'),message=el('p');message.setAttribute('role','status');
                const numeric=(label,max,step='any')=>{const i=el('input');i.type='number';i.min='0';i.max=String(max);i.step=step;i.required=true;i.setAttribute('aria-label',label);return i;};
                const label=(name,node)=>{const l=el('label',name+' ');l.appendChild(node);return l;};
                const currency=el('input');currency.maxLength=3;currency.placeholder='SEK';currency.setAttribute('aria-label','Currency');
                const hourly=numeric('Loaded labor cost per hour',1e12),investment=numeric('One-time cost',1e12),
                  coverage=numeric('Minimum quality coverage percent',100),adoption=numeric('Minimum attempted adoption percent',100),
                  failure=numeric('Maximum failure-rate increase percentage points',100);
                const fields=el('div',null,'row');
                fields.append(label('Currency',currency),label('Loaded labor cost per hour',hourly),label('One-time cost',investment),
                  label('Minimum quality coverage (%)',coverage),label('Minimum after attempted adoption (%)',adoption),
                  label('Maximum failure rate increase (percentage points)',failure));
                const selections=[];
                const periodHost=el('div');
                for(const period of inv.measurements){
                  const group=el('fieldset');group.appendChild(el('legend',period.period+' · '+period.start+' – '+period.end+' · '+period.cases+' cases'));
                  const include=el('input');include.type='checkbox';
                  const names=[['eligible_cases','Eligible cases'],['automation_attempted_cases','Cases with automation attempted'],
                    ['automation_handoff_cases','Attempted cases handed to a human'],['quality_checked_cases','Quality checked cases'],
                    ['quality_failed_cases','Checked cases failing quality'],['extra_review_hours','Additional review hours'],['nonlabor_cost','Nonlabor operating cost for period']];
                  const inputs={};const controls=el('div');
                  for(const [key,title] of names){const isCount=key.endsWith('_cases');const node=numeric(title,isCount?period.cases:1e12,isCount?'1':'any');node.disabled=true;inputs[key]=node;controls.appendChild(label(title,node));}
                  const basis=el('select');basis.disabled=true;basis.setAttribute('aria-label','Review effort accounting basis');
                  for(const [v,t] of [['','Choose basis'],['already_in_retained_effort','Review already included in retained effort'],['additional_to_retained_effort','Review effort additional to retained effort']]){
                    const o=el('option',t);o.value=v;basis.appendChild(o);}
                  include.onchange=()=>{Object.values(inputs).forEach(n=>n.disabled=!include.checked);basis.disabled=!include.checked;};
                  group.append(label('Include measurement',include),controls,label('Review effort accounting basis',basis));periodHost.appendChild(group);
                  selections.push({period,include,inputs,basis});
                }
                const run=button('Calculate quality and adoption scenario','secondary'),download=button('Download scenario JSON','secondary');download.disabled=true;
                let serial=0,result=null;
                const invalidate=()=>{serial++;result=null;output.replaceChildren();download.disabled=true;run.disabled=false;message.textContent='Inputs changed; calculate again.';};
                for(const n of [fields,periodHost])n.addEventListener('input',invalidate);
                for(const n of [fields,periodHost])n.addEventListener('change',invalidate);
                download.onclick=()=>{if(!active()||!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-quality-benefit-scenario.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
                run.onclick=async()=>{
                  invalidate();const ticket=serial;
                  const selected=selections.filter(p=>p.include.checked);
                  const required=[hourly,investment,coverage,adoption,failure,...selected.flatMap(p=>Object.values(p.inputs))];
                  if(!/^[A-Z]{3}$/.test(currency.value)||required.some(n=>n.value===''||!n.checkValidity())||
                      selected.some(p=>!p.basis.value)||!selected.some(p=>p.period.period==='baseline')||!selected.some(p=>p.period.period==='after')){
                    message.textContent='Enter all values explicitly, including zeros, and select both a baseline and after period.';return;
                  }
                  const body={snapshot_sha256:inv.snapshot_sha256,currency:currency.value,labor_cost_per_hour:Number(hourly.value),
                    one_time_cost:Number(investment.value),minimum_quality_coverage:Number(coverage.value)/100,
                    minimum_after_adoption:Number(adoption.value)/100,maximum_quality_failure_rate_increase:Number(failure.value)/100,
                    periods:selected.map(p=>({measurement_id:p.period.measurement_id,review_effort_basis:p.basis.value,
                      ...Object.fromEntries(Object.entries(p.inputs).map(([k,v])=>[k,Number(v.value)]))}))};
                  run.disabled=true;message.textContent='Calculating against selected measurement snapshot…';
                  try{
                    const data=await post(path,body);
                    if(!active()||ticket!==serial)return;
                    if(data.snapshot_sha256!==inv.snapshot_sha256||data.idea_id!==idea.idea_id)throw Error('Unexpected measurement snapshot.');
                    result=data;download.disabled=false;message.textContent=data.all_declared_gates_met?'Entered evidence meets all selected review gates.':'One or more selected review gates are unmet or unknown.';
                    const pct=v=>v===null?'Unknown':(v*100).toFixed(2)+'%';
                    const table=el('table'),thead=el('thead'),tr=el('tr');['Metric','Baseline','After'].forEach(x=>tr.appendChild(el('th',x)));thead.appendChild(tr);table.appendChild(thead);
                    const tbody=el('tbody');for(const [key,name] of [['cases','Cases'],['eligible','Eligible'],['attempts','Attempted'],['handoffs','Handoffs'],['checked','Quality checked'],['failed','Quality failures'],['quality_coverage','Quality coverage'],['quality_failure_rate','Failure among checked'],['attempted_share_of_eligible','Attempted adoption'],['handoff_share_of_attempts','Handoff share'],['non_handoff_share_of_eligible','Non-handoff share of eligible']]){
                      const row=el('tr');const ratio=['quality_coverage','quality_failure_rate','attempted_share_of_eligible','handoff_share_of_attempts','non_handoff_share_of_eligible'].includes(key);
                      for(const v of [name,ratio?pct(data.baseline[key]):data.baseline[key],ratio?pct(data.after[key]):data.after[key]])row.appendChild(el('td',String(v)));tbody.appendChild(row);
                    }table.appendChild(tbody);
                    const wrap=el('div',null,'table-wrap');wrap.appendChild(table);
                    const money=data.financial_scenario;output.replaceChildren(el('h5','Pooled period evidence'),wrap,el('h5','Review gates'));
                    for(const g of data.review_gates)output.appendChild(el('p',g.gate+' · '+g.status+' · observed '+pct(g.observed)+' · threshold '+pct(g.threshold)));
                    const population=data.whole_population_quality;
                    if(population){
                      const range=(lo,hi)=>pct(lo)+' to '+pct(hi);
                      output.append(el('h5','Whole-population quality bounds'),
                        el('p','Baseline failure rate: '+range(population.baseline.failure_rate_lower,population.baseline.failure_rate_upper)+'; '+population.baseline.unchecked_cases+' unchecked cases.'),
                        el('p','After failure rate: '+range(population.after.failure_rate_lower,population.after.failure_rate_upper)+'; '+population.after.unchecked_cases+' unchecked cases.'),
                        el('p','After minus baseline failure-rate change: '+range(population.failure_rate_change_lower,population.failure_rate_change_upper)+' (percentage points).'),
                        el('p','Whole-population threshold: '+population.threshold_status.replaceAll('_',' ')+' · maximum increase '+pct(population.maximum_declared_increase)+'. Separate from the checked-case gates above.'),
                        el('p','After cases both attempted without handoff and passing quality: '+population.after.nonhandoff_and_quality_pass_count_lower+'–'+population.after.nonhandoff_and_quality_pass_count_upper+'; share of eligible '+range(population.after.nonhandoff_and_quality_pass_share_of_eligible_lower,population.after.nonhandoff_and_quality_pass_share_of_eligible_upper)+'. No case-level linkage or autonomous success proven.'),
                        el('p','Best-case additional checks to establish within threshold: '+(population.minimum_favorable_new_checks_to_establish_within??'not attainable')+'; to establish above threshold under adverse outcomes: '+(population.minimum_adverse_new_checks_to_establish_above??'not attainable')+'.'),
                        el('p',population.interpretation,'muted'));
                    }
                    output.append(el('h5','Conditional cost and effort scenario'),
                      el('p','Retained effort change at after volume: '+money.retained_effort_change_hours_at_after_volume+' hours/month'),
                      el('p','Including additional review effort: '+money.effort_change_with_extra_review_hours_at_after_volume+' hours/month'),
                      el('p','Modeled net value change: '+money.modeled_net_monthly_value_change+' '+data.currency+'/month'),
                      el('p','Modeled simple payback: '+(money.modeled_simple_payback_months===null?'No positive modeled payback':money.modeled_simple_payback_months+' months')),
                      el('p',data.interpretation,'muted'));
                  }catch(e){if(active()&&ticket===serial)message.textContent=e.message;}
                  finally{if(active()&&ticket===serial)run.disabled=false;}
                };
                scenarioHost.replaceChildren(el('h5','Quality and adoption review'),
                  el('p','Use comparable periods and consistent quality criteria. Enter every count and cost explicitly. Non-handoff does not prove successful completion. Financial arithmetic does not monetize quality.','muted'),fields,periodHost,run,download,message,output);
              }catch(e){if(active())scenarioHost.replaceChildren(el('p',e.message));}
            };
            parts.push(launch,scenarioHost);
            const investmentLaunch=button('Compare phased investment business cases','secondary');
            investmentLaunch.id='owgInvestmentOpen';
            const investmentHost=el('div');investmentHost.id='owgInvestmentResult';
            let investmentGeneration=0;
            investmentLaunch.onclick=async()=>{
              const generationId=++investmentGeneration;
              investmentHost.replaceChildren(el('p','Reading exact retained measurement inventory…','muted'));
              const active=()=>generationId===investmentGeneration&&generation===detailGeneration&&investmentHost.isConnected;
              const path='/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/investment-case';
              try{
                const inv=await(await api(path+'/inventory')).json();
                if(!active())return;
                if(inv.idea_revision!==idea.revision)throw Error('Idea changed; reopen before evaluating.');
                const status=el('p');status.id='owgInvestmentStatus';status.setAttribute('role','status');
                const resultHost=el('div'),selectionHost=el('div'),designHost=el('div'),review=el('textarea');
                resultHost.id='owgInvestmentReport';review.id='owgInvestmentReviewed';review.readOnly=true;review.rows=8;review.style.width='100%';
                const labelled=(text,node)=>{const label=el('label',text+' ');label.append(node);return label;};
                const numeric=(label,min,max,step='any')=>{const n=el('input');n.type='number';n.min=String(min);n.max=String(max);n.step=step;
                  n.required=true;n.setAttribute('aria-label',label);return n;};
                const currency=el('input');currency.maxLength=3;currency.placeholder='SEK';currency.setAttribute('aria-label','Investment currency');
                const hourly=numeric('Declared labor hour value',0,1e12),discount=numeric('Monthly discount rate percent',0,100),
                  horizon=numeric('Investment horizon months',1,60,'1');
                const sensitivity=el('input');sensitivity.value='0,25,50,75,100';sensitivity.setAttribute('aria-label','Cash conversion sensitivity percentages');
                const prepare=button('Prepare exact investment assumptions','secondary'),run=button('Evaluate phased business cases','secondary'),
                  download=button('Download investment scenario JSON','secondary'),csv=button('Download monthly cash flows CSV','secondary');
                prepare.id='owgInvestmentPrepare';run.id='owgInvestmentRun';download.id='owgInvestmentDownload';csv.id='owgInvestmentCsv';
                let serial=0,draft=null,report=null;
                const invalidate=()=>{serial++;draft=report=null;review.value='';run.disabled=download.disabled=csv.disabled=true;
                  resultHost.replaceChildren();status.textContent='Review assumptions and prepare again.';};
                const inputFields=el('div',null,'row');
                inputFields.append(labelled('Currency',currency),labelled('Labor hour value',hourly),
                  labelled('Monthly discount rate (%)',discount),labelled('Horizon months',horizon),
                  labelled('Cash conversion sensitivity (%)',sensitivity));
                const choices=[];
                for(const m of inv.measurements){
                  const check=el('input');check.type='checkbox';check.setAttribute('aria-label','Include '+m.period+' measurement '+m.start+' '+m.end);
                  selectionHost.append(labelled(m.period+' · '+m.start+' to '+m.end+' · '+m.cases+' cases · '+m.source,check));
                  choices.push({m,check});
                }
                const designs=[];
                function addDesign(){
                  if(designs.length>=4){status.textContent='At most four designs.';return;}
                  const group=el('fieldset');group.append(el('legend','Investment design'));
                  const name=el('input');name.maxLength=80;name.setAttribute('aria-label','Investment design name');
                  const conversion=numeric('Cash conversion percent',0,100),initial=numeric('Initial cash cost',0,1e12);
                  const phaseHost=el('div'),phases=[];
                  const design={group,name,conversion,initial,phases};
                  function addPhase(){
                    if(phases.length>=12){status.textContent='At most 12 phases per design.';return;}
                    const panel=el('fieldset');panel.append(el('legend','Monthly phase'));
                    const start=numeric('Phase start month',1,60,'1'),end=numeric('Phase end month',1,60,'1'),
                      cases=numeric('Phase monthly cases',0,1e8,'1'),effect=numeric('Retained effort effect percent',0,100),
                      nonlabor=numeric('Nonlabor cash change per case',-1e12,1e12),fixed=numeric('Recurring fixed cash cost',0,1e12),
                      oneTime=numeric('One-time cost at phase start',0,1e12);
                    const row={panel,start,end,cases,effect,nonlabor,fixed,oneTime};
                    const remove=button('Remove phase','secondary');remove.onclick=()=>{phases.splice(phases.indexOf(row),1);panel.remove();invalidate();};
                    panel.append(labelled('Start month',start),labelled('End month (inclusive)',end),labelled('Monthly cases',cases),
                      labelled('Retained effort effect (%)',effect),labelled('Nonlabor cash change per case (positive = reduction)',nonlabor),
                      labelled('Recurring fixed cost per month',fixed),labelled('One-time cost at phase start',oneTime),remove);
                    phases.push(row);phaseHost.append(panel);invalidate();
                  }
                  const add=button('Add monthly phase','secondary');add.onclick=addPhase;
                  const remove=button('Remove design','secondary');remove.onclick=()=>{designs.splice(designs.indexOf(design),1);group.remove();invalidate();};
                  group.append(labelled('Design name',name),labelled('Cash conversion of positive capacity value (%)',conversion),
                    labelled('Initial cash cost at month zero',initial),phaseHost,add,remove);
                  designs.push(design);designHost.append(group);addPhase();
                }
                const add=button('Add alternative investment design','secondary');add.onclick=addDesign;
                for(const node of [inputFields,selectionHost,designHost]){
                  node.addEventListener('input',invalidate);node.addEventListener('change',invalidate);
                }
                function read(n){
                  if(n.value===''||!n.checkValidity()||!Number.isFinite(Number(n.value)))throw Error('Enter every numerical assumption explicitly, including zeros.');
                  return Number(n.value);
                }
                prepare.onclick=()=>{
                  invalidate();
                  try{
                    const measurement_ids=choices.filter(c=>c.check.checked).map(c=>c.m.measurement_id);
                    if(!choices.some(c=>c.check.checked&&c.m.period==='baseline')||!choices.some(c=>c.check.checked&&c.m.period==='after'))throw Error('Select comparable baseline and after measurements.');
                    if(!/^[A-Z]{3}$/.test(currency.value)||!designs.length)throw Error('Enter one uppercase currency and at least one design.');
                    const shares=sensitivity.value.split(',').map(x=>{if(!x.trim()||!Number.isFinite(Number(x))||Number(x)<0||Number(x)>100)throw Error('Enter valid comma-separated percentages.');return Number(x)/100;});
                    draft={snapshot_sha256:inv.snapshot_sha256,measurement_ids,currency:currency.value,
                      labor_hour_value:read(hourly),monthly_discount_rate:read(discount)/100,horizon_months:read(horizon),
                      conversion_sensitivity:shares,designs:designs.map(d=>{
                        if(!d.name.value.trim()||!d.phases.length)throw Error('Every design needs a name and explicit monthly phases.');
                        return {label:d.name.value.trim(),cash_conversion_share:read(d.conversion)/100,initial_cash_cost:read(d.initial),
                          phases:d.phases.map(p=>({start_month:read(p.start),end_month:read(p.end),monthly_cases:read(p.cases),
                            retained_effort_effect_share:read(p.effect)/100,nonlabor_cash_change_per_case:read(p.nonlabor),
                            recurring_fixed_cash_cost:read(p.fixed),one_time_cash_cost_at_phase_start:read(p.oneTime)}))};
                      })};
                    review.value=JSON.stringify(draft,null,2);run.disabled=false;
                    status.textContent='Review the exact assumptions, then evaluate. No financial record or implementation state will change.';
                  }catch(error){status.textContent=error.message;}
                };
                function table(headers,rows){
                  const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
                  const t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');
                  headers.forEach(x=>{const th=el('th',x);th.scope='col';hr.append(th);});h.append(hr);
                  for(const row of rows){const tr=el('tr');row.forEach(x=>tr.append(el('td',x??'Not within horizon')));b.append(tr);}
                  t.append(h,b);wrap.append(t);return wrap;
                }
                function cashChart(d){
                  const section=el('div');section.setAttribute('role','group');section.setAttribute('aria-label','Discounted cash balance by month for '+d.label);
                  const values=d.months.map(m=>Number(m.cumulative_discounted_cash_flow));
                  const scale=Math.max(1,...values.map(Math.abs));
                  section.append(el('p','Discounted cumulative cash balance; zero is the center line. Exact values are in the monthly table.','muted'));
                  for(const [i,v] of values.entries()){
                    const row=el('div');row.style.cssText='display:grid;grid-template-columns:52px minmax(0,1fr);align-items:center;gap:8px;margin:3px 0';
                    row.append(el('span','M'+i));
                    const track=el('div');track.style.cssText='position:relative;height:12px;border:1px solid currentColor;border-radius:3px;opacity:.85';
                    const zero=el('div');zero.style.cssText='position:absolute;left:50%;height:100%;border-left:1px solid currentColor';
                    const bar=el('div');bar.style.cssText='position:absolute;height:100%;background:currentColor';
                    const width=50*Math.abs(v)/scale;
                    bar.style.left=(v<0?50-width:50)+'%';bar.style.width=width+'%';
                    track.title=d.months[i].cumulative_discounted_cash_flow;track.setAttribute('aria-hidden','true');track.append(zero,bar);row.append(track);section.append(row);
                  }
                  return section;
                }
                function exportFile(value,type,name){
                  const url=URL.createObjectURL(new Blob([value],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
                }
                download.onclick=()=>{if(report&&active())exportFile(JSON.stringify(report,null,2),'application/json','owg-phased-investment.json');};
                csv.onclick=()=>{
                  if(!report||!active())return;
                  const fields=['month','cases','released_hours','additional_hours','converted_positive_labor_value','unconverted_capacity_value',
                    'additional_labor_cash_cost','nonlabor_cash_change','fixed_cash_cost','one_time_cash_cost','net_cash_flow','net_economic_value',
                    'discounted_cash_flow','discounted_economic_value','cumulative_cash_flow','cumulative_discounted_cash_flow','cumulative_economic_value'];
                  const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';
                  const safeLabel=v=>/^[=+\-@\t\r\n]/.test(v)?"'"+v:v;
                  const rows=[['design','currency',...fields],...report.designs.flatMap(d=>d.months.map(m=>[safeLabel(d.label),report.currency,...fields.map(k=>m[k])]))];
                  exportFile(rows.map(r=>r.map(quote).join(',')).join('\r\n'),'text/csv','owg-phased-investment-months.csv');
                };
                run.onclick=async()=>{
                  if(!draft)return;
                  const n=++serial,body=draft;report=null;run.disabled=download.disabled=csv.disabled=true;resultHost.replaceChildren();
                  status.textContent='Evaluating exact phased cash-flow scenarios…';
                  try{
                    const data=await post(path,body);
                    if(!active()||n!==serial)return;
                    if(data.snapshot_sha256!==inv.snapshot_sha256||data.idea_id!==idea.idea_id)throw Error('Unexpected measurement basis.');
                    report=data;download.disabled=csv.disabled=false;
                    const b=data.retained_effort_basis;
                    resultHost.append(el('h5','Phased investment comparison'),
                      el('p','Reported effort: '+b.baseline.minutes_per_case+' → '+b.after.minutes_per_case+
                        ' minutes/case. '+data.selected_measurement_ids.length+' measurements included; '+data.excluded_measurement_ids.length+' excluded.'),
                      el('p','Currency '+data.currency+' · first design is the explicit comparison baseline. Economic value includes released capacity; cash flow includes only declared cash conversion.','muted'),
                      table(['Design','Cash NPV','Economic NPV','Peak cash funding','Released hours','Discounted cash sustained break-even month'],
                        data.designs.map(d=>[d.label,d.cash_npv,d.economic_npv,d.peak_undiscounted_cash_funding_requirement,d.released_hours,
                          d.discounted_cash_break_even.sustained_nonnegative_from_month])));
                    for(const d of data.designs){
                      const details=el('details');details.append(el('summary',d.label+' · monthly cash flow and sensitivity'));
                      const s=d.cash_conversion_review;
                      details.append(el('p','Cash NPV change vs first design: '+d.change_vs_first_design.cash_npv+
                        ' · minimum conversion for nonnegative cash NPV: '+(s.minimum_cash_conversion_share_for_nonnegative_npv??'Not attainable')+
                        ' (fraction). '+s.status.replaceAll('_',' ')+'.'),
                        el('p','Unconverted released hours: '+d.unconverted_released_hours+'; additional hours: '+d.additional_hours+
                          '. Initial investment and additional phase costs are included.'),
                        table(['Cash conversion fraction','Cash NPV','Nonnegative'],s.sensitivity.map(x=>[x.cash_conversion_share,x.cash_npv,x.cash_npv_nonnegative?'Yes':'No'])),
                        cashChart(d),table(['Month','Cases','Released hours','Extra hours','Cash flow','Economic value','Cumulative cash','Discounted cumulative cash'],
                          d.months.map(m=>[m.month,m.cases,m.released_hours,m.additional_hours,m.net_cash_flow,m.net_economic_value,
                            m.cumulative_cash_flow,m.cumulative_discounted_cash_flow])),
                        el('p','First discounted cash crossing: '+(d.discounted_cash_break_even.first_nonnegative_month??'none')+
                          '; sustained crossing within horizon: '+(d.discounted_cash_break_even.sustained_nonnegative_from_month??'none')+
                          '; later reversal: '+String(d.discounted_cash_break_even.returns_below_zero_after_first_crossing)+'.','muted'));
                      resultHost.append(details);
                    }
                    resultHost.append(el('p',data.interpretation,'muted'));status.textContent='Conditional business cases evaluated; no actual cash savings verified.';
                  }catch(error){if(active()&&n===serial)status.textContent=error.message;}
                  finally{if(active()&&n===serial)run.disabled=false;}
                };
                addDesign();invalidate();
                investmentHost.replaceChildren(el('h5','Phased investment business cases'),
                  el('p','Select exact before/after measurements. Enter every monthly phase and alternative explicitly. The retained effort difference is not causal and no quality/adoption assessment is inferred. Cash flow and economic capacity value are separate.','muted'),
                  selectionHost,inputFields,designHost,add,prepare,field('Exact reviewed investment assumptions',review),run,download,csv,status,resultHost);
              }catch(error){if(active())investmentHost.replaceChildren(el('p',error.message));}
            };
            parts.push(investmentLaunch,investmentHost);

          }
          parts.push(el('p',r.interpretation,'muted'));host.replaceChildren(...parts);
        };
        addMeasure.onclick=async()=>{
          const body={period:kind.value,start:start.value,end:end.value,cases:Number(cases.value),minutes_per_case:Number(minutes.value),source:source.value,source_reference:ref.value};
          addMeasure.disabled=true;
          try{draw(await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/measurements',body));measStatus.textContent='Measurement recorded.';}
          catch(e){measStatus.textContent=e.message;}finally{addMeasure.disabled=false;}
        };
        const grid=el('div',null,'row');grid.style.alignItems='end';
        grid.append(field('Period',kind),field('From',start),field('To',end),field('Cases handled',cases),field('Minutes per case',minutes),field('Source',source),field('Reference',ref),addMeasure);
        const effortPanel=el('details');effortPanel.append(el('summary','Derive effort from a retained event log'),
          el('p','Use a numeric event attribute that records additive work effort. Never use elapsed case time. All cases starting during the selected UTC dates must finish during the period with the exact completion activity; missing or shared effort evidence blocks recording.','muted'));
        const effortLog=select('owgRetainedEffortLog',[['','Choose retained log']]);
        const loadLogs=button('Load local logs','secondary');
        const notion=input('owgRetainedEffortType','text','case');notion.value='case';notion.maxLength=200;
        const terminal=input('owgRetainedEffortCompletion','text','Exact stored activity');terminal.maxLength=200;
        const effortActivity=input('owgRetainedEffortActivity','text','Exact stored effort activity');effortActivity.maxLength=200;
        const attr=input('owgRetainedEffortAttribute','text','Exact event attribute');attr.maxLength=120;
        const unit=select('owgRetainedEffortUnit',[['seconds','Seconds'],['minutes','Minutes'],['hours','Hours']]);
        const confirm=input('owgRetainedEffortConsent','checkbox');
        const effortStatus=el('div',null,'muted');effortStatus.setAttribute('role','status');
        const effortPreview=button('Preview retained effort','secondary'),effortSave=button('Record reviewed measurement','secondary');
        effortSave.disabled=true;
        let effortSnapshot=null,effortTurn=0;
        const currentEffort=()=>generation===detailGeneration&&effortPanel.isConnected;
        const clearEffort=()=>{effortTurn++;effortSnapshot=null;effortSave.disabled=true;effortStatus.replaceChildren();};
        for(const control of [kind,start,end,effortLog,notion,terminal,effortActivity,attr,unit]){
          control.addEventListener('input',()=>{confirm.checked=false;clearEffort();});
          control.addEventListener('change',()=>{confirm.checked=false;clearEffort();});
        }
        confirm.addEventListener('change',clearEffort);
        loadLogs.onclick=async()=>{
          clearEffort();const turn=effortTurn;loadLogs.disabled=true;
          try{
            const data=await (await api('/v1/event-logs')).json();
            if(!currentEffort()||turn!==effortTurn)return;
            effortLog.replaceChildren(el('option','Choose retained log'));effortLog.options[0].value='';
            for(const log of data.logs||[]){const opt=el('option',log.name+' · '+log.events+' events');opt.value=log.log_id;effortLog.appendChild(opt);}
            confirm.checked=false;
          }catch(e){if(currentEffort()&&turn===effortTurn)effortStatus.textContent=e.message;}
          finally{loadLogs.disabled=false;}
        };
        const effortSpec=()=>({expected_revision:idea.revision,log_id:effortLog.value,case_type:notion.value,
          period:kind.value,start:start.value,end:end.value,completion_activity:terminal.value,
          effort_activity:effortActivity.value,effort_attribute:attr.value,unit:unit.value,effort_semantics_confirmed:confirm.checked});
        const effortBase='/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/retained-effort';
        effortPreview.onclick=async()=>{
          clearEffort();const turn=effortTurn;const spec=effortSpec();
          if(!spec.effort_semantics_confirmed){effortStatus.textContent='Confirm the attribute measures additive work effort.';return;}
          effortPreview.disabled=true;effortStatus.textContent='Checking complete retained cohort…';
          try{
            const result=await post(effortBase+'/preview',spec);
            if(!currentEffort()||turn!==effortTurn)return;
            effortStatus.replaceChildren(el('p',result.counts.started_cases+' started cases; '+result.counts.complete_cases+' complete. '+result.counts.effort_events+' effort-event links.'),
              el('p',Object.entries(result.counts).map(([key,count])=>key.replaceAll('_',' ')+': '+count).join(' · ')),
              el('p',result.recordable?result.total_hours+' reported effort hours · '+result.minutes_per_case+' minutes/case':('Blocked: '+result.blockers.join(', '))),
              el('p','Retained log revision '+result.log_revision+' · '+result.preview_sha256),el('p',result.interpretation));
            if(result.recordable){effortSnapshot={spec,digest:result.preview_sha256};effortSave.disabled=false;}
          }catch(e){if(currentEffort()&&turn===effortTurn)effortStatus.textContent=e.message;}
          finally{effortPreview.disabled=false;}
        };
        effortSave.onclick=async()=>{
          const snap=effortSnapshot;
          if(!snap||!currentEffort()||JSON.stringify(effortSpec())!==JSON.stringify(snap.spec))return;
          const turn=++effortTurn;effortSave.disabled=true;effortSnapshot=null;
          try{const result=await post(effortBase+'/record',{...snap.spec,expected_preview:snap.digest});
            if(currentEffort()){draw(result);if(turn===effortTurn)effortStatus.textContent='Retained effort measurement recorded. Preview again before another save.';}
          }catch(e){if(currentEffort()&&turn===effortTurn)effortStatus.textContent=e.message;}
        };
        const effortFields=el('div',null,'row');effortFields.append(loadLogs,field('Retained log',effortLog),field('Case type',notion),
          field('Final completion activity',terminal),field('Effort-bearing activity',effortActivity),field('Numeric event attribute',attr),field('Effort unit',unit));
        effortPanel.append(effortFields,el('p','Uses the Period, From and To controls above.'),
          field('I confirm the selected attribute measures additive effort, not elapsed or waiting time',confirm),effortPreview,effortSave,effortStatus);
        value.append(grid,measStatus,effortPanel,host);box.appendChild(value);
        api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/realized-value').then(r=>r.json()).then(r=>{if(generation===detailGeneration)draw(r);}).catch(e=>{measStatus.textContent=e.message;});
      }
      // These selections are navigation hints only, never an authorization or a source read.
      let preferredReportProjectId=null,preferredSourceProjectId=null;
      const journeyButton=button('View evidence journey','secondary');journeyButton.id='owgPipelineJourney';
      const journeyHost=el('div');journeyHost.id='owgPipelineJourneyResult';journeyHost.style.overflowWrap='anywhere';
      journeyButton.onclick=async()=>{
        journeyButton.disabled=true;const progress=el('div','Checking local references…','muted');progress.setAttribute('role','status');journeyHost.replaceChildren(progress);
        try{
          const data=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/journey')).json();
          if(generation!==detailGeneration||(resume&&!resume.valid()))return;
          journeyHost.replaceChildren(el('h4','Evidence journey'));
          const stages=el('ol');
          if(data.dataset_origins?.length){
            data.dataset_origins.forEach(item=>{
              const labels={current:'source data unchanged',source_advanced:'source data changed since snapshot',unavailable:'snapshot unavailable',check_failed:'source could not be checked',reference_mismatch:'snapshot metadata no longer matches its reference',not_checked_work_budget:'source not checked: fingerprint work budget exceeded'};
              stages.appendChild(el('li','Dataset and cohort → process: '+(labels[item.status]||'unknown status')+
                (item.source_content_sha256?' · source SHA-256 '+item.source_content_sha256+' · selection SHA-256 '+item.selection_sha256:'')+'. User-selected reference; derivation and business rules are not independently verified.'));
            });
          }else stages.appendChild(el('li',data.dataset_origin_check_available===false?'Dataset and cohort → process: source references could not be checked.':'Dataset and cohort → process: origin is not linked.'));
          const process=data.process_status,ref=process.process_reference;
          const sourceLabels={current:'current',changed:'changed since this idea',unavailable:'unavailable',reference_mismatch:'reference no longer matches',check_failed:'could not be checked'};
          stages.appendChild(el('li',ref?'Saved process '+ref.workflow_id+' · revision '+ref.revision+' · '+(sourceLabels[process.status]||'unknown status')+' · SHA-256 '+ref.content_sha256:'Saved process: no exact revision linked.'));
          stages.appendChild(el('li','Opportunity revision '+data.idea.revision+' · '+(STATE_LABELS[data.idea.state]||data.idea.state)+'. Review state does not grant execution authority.'));
          journeyHost.append(stages,el('div','Checked local references now, across separate stores. Refresh after changes. Reported specifications do not prove which artifact ran; source-check results are not retained here.','muted'));
          // Live, read-only pilot readiness: a status from the current authenticated
          // journey, not a consent grant or an authorization to execute.
          const readiness=el('section');readiness.id='owgPipelinePilotReadiness';
          readiness.setAttribute('aria-label','Pilot journey readiness');
          readiness.appendChild(el('h5','Pilot journey readiness (review only)'));
          const origins=Array.isArray(data.dataset_origins)?data.dataset_origins:[];
          const projects=Array.isArray(data.projects)?data.projects:[];
          const checks=[
            ['Dataset reference',origins.length>0&&origins.every(x=>x.status==='current'),
              origins.length?'Every linked origin must be current.':'No dataset origin is linked.'],
            ['Saved process',!!ref&&process.status==='current',
              ref?'Pinned revision must still be current.':'An exact saved process revision is required.'],
            ['Human opportunity decision',['approved','implemented'].includes(data.idea?.state),
              'Approval is a review decision, never execution authorization.'],
            ['Implementation evidence',projects.some(p=>p.idea_revision_is_current&&
              Array.isArray(p.reported_specifications)&&p.reported_specifications.length>0&&
              p.reported_specifications.every(r=>r.status==='matching_reported_specification')),
              'A current project and matching reported specification are required; reports are not verified runs.']
          ];
          const readinessList=el('ol');readinessList.id='owgPipelinePilotReadinessChecks';
          checks.forEach(([name,ok,explanation])=>{
            const item=el('li',(ok?'Reviewable: ':'Blocked / unknown: ')+name+'. '+explanation);
            item.dataset.reviewable=String(ok);readinessList.appendChild(item);
          });
          const readyCount=checks.filter(x=>x[1]).length;
          const readinessStatus=el('p',readyCount+' of '+checks.length+
            ' prerequisites reviewable. Agent handoff remains unauthorized; independently verify consent, scope, source checks and actual outcomes.','muted');
          readinessStatus.setAttribute('role','status');
          readiness.append(readinessList,readinessStatus);
          journeyHost.appendChild(readiness);
          const guide=el('section');guide.id='owgPipelineJourneyGuide';
          guide.appendChild(el('h5','Resume this evidence-to-implementation journey'));
          const guideStatus=el('div',null,'muted');guideStatus.id='owgPipelineJourneyGuideStatus';
          guideStatus.setAttribute('role','status');guideStatus.setAttribute('aria-live','polite');
          const navigate=(id,description,activate=false)=>{
            if(generation!==detailGeneration)return;
            const target=box.querySelector('#'+id);
            if(!target||target.disabled){guideStatus.textContent=description+' is unavailable for this idea or revision.';return;}
            if(activate)target.click();
            target.scrollIntoView?.({block:'nearest'});
            target.focus?.();
            guideStatus.textContent=description+' opened for this selected idea. No decision or business action was made.';
          };
          const processAction=button('Open exact saved process revision','secondary');
          processAction.id='owgPipelineJourneyOpenProcess';
          processAction.disabled=!ref||!['current','changed'].includes(process.status)||
            typeof ref.content_sha256!=='string'||!ref.content_sha256;
          processAction.onclick=async()=>{
            if(generation!==detailGeneration||processAction.disabled)return;
            processAction.disabled=true;
            try{
              if(!window.owgProcessCatalog?.openSaved)throw new Error('Process catalog unavailable.');
              const opened=await window.owgProcessCatalog.openSaved(ref);
              if(generation!==detailGeneration)return;
              document.getElementById('owgCatalogEditor')?.scrollIntoView?.({block:'start'});
              guideStatus.textContent='Requested exact linked revision '+ref.revision+'. Verify the catalog status; declining its unsaved-edit prompt keeps the previous draft. '+(process.status==='changed'?
                'A newer process exists; the linked revision is historical.':'Fresh review is still required for any further decision.');
            }catch(e){if(generation===detailGeneration)guideStatus.textContent='Could not reopen pinned revision: '+e.message;}
            finally{if(generation===detailGeneration)processAction.disabled=false;}
          };
          // Source inspection is a separate opt-in step, never a source verification.
          const sourceWorkspaceAction=button('Inspect local source workspace','secondary');
          sourceWorkspaceAction.id='owgPipelineJourneyOpenSourceWorkspace';
          sourceWorkspaceAction.onclick=()=>{
            if(generation!==detailGeneration)return;
            const panel=document.getElementById('panel-evidence');
            const workspace=document.getElementById('owgEventLogCard');
            if(!panel||!workspace||!workspace.isConnected){
              guideStatus.textContent='Local event-log workspace is unavailable. No source was inspected or verified.';
              return;
            }
            const status=origins.length&&origins.every(origin=>origin.status==='current')?
              'Linked source fingerprints were current at the last journey check; inspect the actual dataset and cohort separately.':
              'Linked source is absent, changed or unverified. Inspect the dataset and cohort; do not treat this as a fresh source check.';
            workspace.scrollIntoView?.({block:'start'});
            workspace.setAttribute('tabindex','-1');
            workspace.focus();
            guideStatus.textContent='Opened local event-log workspace. '+status+' No source read, approval or execution was authorized.';
          };
          guide.appendChild(sourceWorkspaceAction);
          guide.appendChild(processAction);
          if(process.status!=='current')guide.appendChild(el('p',
            'Process basis is '+String(process.status).replaceAll('_',' ')+'. Historical references are not silently replaced; review changes before any new project.','muted'));
          const ideaAction=button('Continue at idea assessment or decision','secondary');
          ideaAction.id='owgPipelineJourneyReviewIdea';
          ideaAction.onclick=()=>navigate(box.querySelector('#owgPipelineAssess')?'owgPipelineAssess':'owgPipelineNote',
            'Idea assessment or decision');
          guide.appendChild(ideaAction);
          if(data.dataset_origins?.some(origin=>origin.status!=='current')||!data.dataset_origins?.length)
            guide.appendChild(el('p','Source snapshot coverage is missing, changed or unverified. This journey cannot certify original data completeness.','muted'));
          guide.appendChild(guideStatus);journeyHost.appendChild(guide);
          if(resume&&!data.projects.some(project=>project.project_id===resume.projectId)){
            resumeStatus.textContent='Exact saved project is unavailable or deleted. No other project was selected, and no inspection marks were restored.';
            return;
          }
          if(!data.projects.length){
            journeyHost.appendChild(el('p','No project artifacts recorded.'));
            const next=button('Continue at agent project export','secondary');next.id='owgPipelineJourneyExport';
            next.disabled=!['approved','implemented'].includes(idea.state)||!ref||process.status!=='current';
            next.onclick=()=>navigate('owgPipelineProjectDownload','Agent project export');
            guide.insertBefore(next,guideStatus);
            return;
          }
          const choice=select('owgPipelineJourneyProject',data.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at]));
          if(resume)choice.value=resume.projectId;
          // Compare immutable exports without creating a project or contacting providers.
          const projectCompare=el('section');projectCompare.id='owgPipelineProjectComparison';
          projectCompare.append(el('h5','Compare two recorded project exports'),
            el('p','Review pinned artifact hashes and report/source-check metadata from this local snapshot. This does not verify execution or business outcomes.','muted'));
          const projectOptions=[['','Choose a recorded project'],...data.projects.map(p=>[
            p.project_id,'Idea revision '+p.idea_revision+' · '+p.project_id])];
          const baselineProject=select('owgPipelineCompareBaseline',projectOptions);
          const candidateProject=select('owgPipelineCompareCandidate',projectOptions);
          const compareProjects=button('Compare recorded project exports','secondary');
          compareProjects.id='owgPipelineCompareProjects';
          compareProjects.disabled=data.projects.length<2||!!data.projects_truncated;
          const comparisonView=el('div','Choose two different projects, then compare explicitly.','muted');
          comparisonView.id='owgPipelineProjectComparisonResult';
          comparisonView.setAttribute('role','status');comparisonView.setAttribute('aria-live','polite');
          const invalidateProjectComparison=()=>{
            comparisonView.textContent='Selection changed. Compare explicitly again to review pinned records.';
          };
          baselineProject.onchange=invalidateProjectComparison;
          candidateProject.onchange=invalidateProjectComparison;
          compareProjects.onclick=()=>{
            if(generation!==detailGeneration||!journeyHost.contains(projectCompare))return;
            try{
              const comparison=compareRecordedProjects(data,baselineProject.value,candidateProject.value);
              comparisonView.replaceChildren(el('p',comparison.changed_count?
                comparison.changed_count+' recorded artifact/metadata differences. Review the pinned projects before a new handoff. Execution and business outcomes remain unverified.':
                'No difference in compared recorded fields. Execution and business outcomes remain unverified.'));
              const table=el('table'),head=el('tr');
              for(const title of ['Recorded field','Baseline project','Candidate project','Comparison']){
                const th=el('th',title);th.scope='col';head.appendChild(th);
              }
              const thead=el('thead');thead.appendChild(head);
              const tbody=el('tbody');
              for(const item of comparison.fields){
                const tr=el('tr'),th=el('th',item.label);th.scope='row';
                tr.append(th,el('td',item.baseline),el('td',item.candidate),
                  el('td',item.changed?'Changed':'Matching recorded field'));
                tbody.appendChild(tr);
              }
              table.append(thead,tbody);
              const wrap=el('div',null,'table-wrap');wrap.style.overflowWrap='anywhere';
              wrap.appendChild(table);comparisonView.appendChild(wrap);
              // Review changes across the seven dependency stages, not just hashes.
              const readiness=compareProjectHandoffReadiness(data,
                comparison.baseline_project_id,comparison.candidate_project_id,
                idea.idea_id,new Date().toISOString());
              const readinessSection=el('section');readinessSection.id='owgPipelineProjectReadinessDelta';
              readinessSection.append(el('h6','Human handoff review changes'),
                el('p','Recorded review differences only; disappearing checks are not verified as resolved. No execution permission.','muted'));
              const blockerList=el('ul');
              for(const [label,keys] of [
                ['Newly reported checks',readiness.blockers.introduced],
                ['No longer reported (not independently resolved)',readiness.blockers.no_longer_reported],
                ['Still outstanding',readiness.blockers.still_outstanding]])
                blockerList.appendChild(el('li',label+': '+(keys.length?keys.join(', ').replaceAll('_',' '):'None')));
              const stageTable=el('table'),stageHead=el('tr');
              for(const label of ['Evidence stage','Baseline status','Candidate status','Review change']){
                const th=el('th',label);th.scope='col';stageHead.appendChild(th);
              }
              const stageThead=el('thead');stageThead.appendChild(stageHead);
              const stageBody=el('tbody');
              readiness.stages.forEach(stage=>{
                const tr=el('tr'),th=el('th',stage.label);th.scope='row';
                tr.append(th,el('td',stage.baseline_status.replaceAll('_',' ')),
                  el('td',stage.candidate_status.replaceAll('_',' ')),
                  el('td',stage.changed?'Changed; re-review':'Same recorded status'));
                stageBody.appendChild(tr);
              });
              stageTable.append(stageThead,stageBody);
              const stageWrap=el('div',null,'table-wrap');
              stageWrap.style.overflowWrap='anywhere';stageWrap.appendChild(stageTable);
              const exportReadiness=button('Download minimized handoff readiness comparison JSON','secondary');
              exportReadiness.id='owgPipelineProjectReadinessDownload';
              const readinessStatus=el('p','Comparison is local and read-only.','muted');
              readinessStatus.setAttribute('role','status');readinessStatus.setAttribute('aria-live','polite');
              exportReadiness.onclick=()=>{
                if(generation!==detailGeneration||!comparisonView.contains(exportReadiness)||
                  baselineProject.value!==comparison.baseline_project_id||
                  candidateProject.value!==comparison.candidate_project_id)return;
                try{
                  const payload=JSON.stringify(readiness,null,2)+String.fromCharCode(10);
                  const blob=new Blob([payload],{type:'application/json'});
                  if(blob.size>65536)throw new Error('Comparison exceeds 64 KiB.');
                  const url=URL.createObjectURL(blob),link=el('a');
                  link.href=url;link.download='owg-project-handoff-readiness-comparison.json';
                  document.body.appendChild(link);link.click();link.remove();
                  setTimeout(()=>URL.revokeObjectURL(url),1000);
                  readinessStatus.textContent='Downloaded review differences only. No consent, approval or execution was granted.';
                }catch(e){readinessStatus.textContent=e.message;}
              };
              readinessSection.append(blockerList,stageWrap,exportReadiness,readinessStatus);
              comparisonView.appendChild(readinessSection);
              const inspect=(label,id)=>{
                const action=button(label,'secondary');
                action.onclick=()=>{
                  if(generation!==detailGeneration||!journeyHost.contains(projectCompare)||
                    !data.projects.some(p=>p.project_id===id))return;
                  choice.value=id;choice.onchange();choice.focus();
                };
                comparisonView.appendChild(action);
              };
              inspect('Inspect baseline exact project',comparison.baseline_project_id);
              inspect('Inspect candidate exact project',comparison.candidate_project_id);
              comparisonView.appendChild(el('p','Point-in-time local comparison only. No approval, source access, run attribution, execution or business outcome verification. Refresh the journey to check newer records.','muted'));
            }catch(e){comparisonView.textContent=e.message;}
          };
          projectCompare.append(field('Baseline recorded project',baselineProject),
            field('Candidate recorded project',candidateProject),compareProjects,comparisonView);
          if(data.projects_truncated)projectCompare.appendChild(el('p',
            'Project list truncated; compare only after complete history is available.','muted'));
          const artifact=el('div');artifact.id='owgPipelineJourneyArtifact';
          let resumeCompare=null;
          const render=()=>{
            const p=data.projects.find(item=>item.project_id===choice.value);if(!p)return;
            artifact.replaceChildren();const list=el('dl');
            const add=(label,value)=>list.append(el('dt',label),el('dd',value));
            add('Project artifact',p.project_id+' · '+(p.idea_revision_is_current?'current idea revision':'historical idea revision'));
            add('Specification SHA-256',p.spec_sha256);add('ZIP SHA-256',p.archive_sha256);
            artifact.appendChild(list);
            const linked=el('ul');
            const labels={matching_reported_specification:'Matching reported specification',unavailable:'Reported deployment unavailable',specification_mismatch:'Reported specification changed',unbound_specification:'Reported specification unbound',check_failed:'Reported specification could not be checked'};
            p.reported_specifications.forEach(r=>linked.appendChild(el('li',r.deployment_id+' · '+(labels[r.status]||'Unknown report status')+(r.reported_run_count===null?'':' · '+r.reported_run_count+' reported runs')+(Number.isSafeInteger(r.runs_reporting_specification_count)?' · '+r.runs_reporting_specification_count+' runs reported this exact specification, '+r.unhashed_run_count+' without a hash':'')+'. Artifact execution and individual run/case attribution are unverified.')));
            artifact.append(el('h5','Reported specifications'),p.reported_specifications.length?linked:el('p','No reported specifications linked.'));
            const checks=p.source_check_exports;
            artifact.append(el('h5','Source-check exports'),el('p',checks.count?checks.count+' recorded · latest '+checks.last_export_at+' · document SHA-256 '+checks.last_document_sha256+'. Receipt and result are not retained; no outcome can be inferred from this record.':'No source-check exports recorded.'));
            artifact.appendChild(el('p','Business outcomes and agent causality remain unverified.'));
            const actions=el('div',null,'row');
            const reportsAction=button('Review reports for this exact project','secondary');
            reportsAction.id='owgPipelineJourneyReports';
            reportsAction.onclick=()=>{
              if(generation!==detailGeneration)return;
              preferredReportProjectId=p.project_id;
              navigate('owgPipelineReviewReports','Report-link review',true);
            };
            const sourceAction=button('Prepare source check for this exact project','secondary');
            sourceAction.id='owgPipelineJourneySource';
            sourceAction.disabled=!p.idea_revision_is_current;
            sourceAction.onclick=()=>{
              if(generation!==detailGeneration)return;
              preferredSourceProjectId=p.project_id;
              navigate('owgPipelineSourceChecks','Source-check form',true);
            };

            const packet=handoffReviewPacket(data,p.project_id,idea.idea_id,new Date().toISOString());
            const review=el('div',null,'muted');review.id='owgPipelineJourneyReadiness';review.tabIndex=-1;
            review.appendChild(el('div','Handoff review: '+packet.readiness.blockers.length+' outstanding checks. This is not permission to execute an agent.'));
            const issues=el('ul');
            const names={no_exact_saved_process:'Exact saved process missing',process_not_current:'Process revision changed or unavailable',
              dataset_origin_not_linked:'Source dataset/cohort not linked',dataset_origin_not_current:'Source dataset/cohort changed or unavailable',
              dataset_origin_list_truncated:'Source dataset/cohort checks truncated to 64',reported_specification_list_truncated:'Reported specification checks truncated to 64',
              idea_not_approved:'Human opportunity approval missing',project_uses_historical_idea_revision:'Project based on historical idea revision',
              no_imported_report:'No imported run report',reported_specification_unverified:'Reported specification unavailable or mismatched',
              execution_and_business_outcomes_unverified:'Execution, causality and business outcomes not independently verified'};
            // First-view blocker triage: navigate to the existing human-review
            // controls for this *exact* project, never clear a blocker by clicking.
            // Source coverage and independent outcomes require manual evidence;
            // no dashboard shortcut can attest to either.
            const blockerStatus=el('div','Review actions only open existing controls. Checks remain outstanding.','muted');
            blockerStatus.id='owgPipelineJourneyBlockerStatus';
            blockerStatus.setAttribute('role','status');blockerStatus.setAttribute('aria-live','polite');
            const blockerRoutes={
              process_not_current:['Inspect exact saved process revision',processAction],
              idea_not_approved:['Review opportunity assessment or decision',ideaAction],
              project_uses_historical_idea_revision:['Review current opportunity before new handoff',ideaAction],
              no_imported_report:['Review imported reports for this exact project',reportsAction],
              reported_specification_unverified:['Inspect mismatched or unavailable reports',reportsAction],
              reported_specification_list_truncated:['Inspect full imported report links',reportsAction],
              execution_and_business_outcomes_unverified:['Prepare separately consented source-state review',sourceAction]
            };
            const blockerHelp={
              no_exact_saved_process:'No pinned design is available. Return to the process catalog, explicitly review and save a revision, then create a new linked opportunity.',
              process_not_current:'Only the pinned revision may be opened. If unavailable, return to the catalog; a newer design cannot be substituted.',
              dataset_origin_not_linked:'Return to the originating event-log workspace, confirm opt-in and source/cohort selection, then create a newly linked design and opportunity.',
              dataset_origin_not_current:'Recheck consent, retention, dataset availability and completeness in the originating workspace; do not assume the saved snapshot is current.',
              dataset_origin_list_truncated:'Only 64 source/cohort checks are included. Inspect the original workspace; this summary cannot certify full coverage.',
              reported_specification_list_truncated:'Only 64 reported specifications are summarized. Review the full report-link list before drawing conclusions.',
              project_uses_historical_idea_revision:'This export is historical. A new reviewed idea revision requires its own explicitly requested project.',
              execution_and_business_outcomes_unverified:'A source-state check alone cannot verify execution, causality, customer benefit or agent authorization. Independent case/outcome validation remains required.'
            };
            packet.readiness.blockers.forEach(key=>{
              const item=el('li');item.id='owgPipelineBlockerItem_'+key;
              item.appendChild(el('span',names[key]||'Unresolved review requirement'));
              const route=blockerRoutes[key];
              if(route){
                const [label,target]=route,action=button(label,'secondary');
                action.id='owgPipelineBlocker_'+key;
                action.disabled=target.disabled;
                action.onclick=()=>{
                  if(generation!==detailGeneration||choice.value!==p.project_id||
                    !artifact.contains(review)||action.disabled||target.disabled)return;
                  target.click();
                  blockerStatus.textContent='Opened '+label.toLowerCase()+
                    ' for this exact selection. The blocker remains outstanding; no approval, provider read or execution occurred.';
                };
                item.appendChild(action);
                if(action.disabled)item.appendChild(el('p','Unavailable for this historical or uncheckable reference; no newer artifact will be substituted.','muted'));
              }
              if(blockerHelp[key])item.appendChild(el('p',blockerHelp[key],'muted'));
              issues.appendChild(item);
            });
            review.append(issues,blockerStatus);
            const downloadPacket=button('Download review packet for this exact project','secondary');
            downloadPacket.id='owgPipelineJourneyReviewPacket';
            // A handoff packet is an explicit fresh review of the selected immutable
            // project, not a snapshot silently exported after the underlying state moves.
            downloadPacket.onclick=async()=>{
              if(downloadPacket.disabled||generation!==detailGeneration||
                choice.value!==p.project_id||!journeyHost.contains(downloadPacket))return;
              const requestGeneration=generation,selectedProject=p.project_id;
              downloadPacket.disabled=true;
              guideStatus.textContent='Rechecking the exact project and current evidence before download…';
              try{
                const fresh=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/journey')).json();
                if(requestGeneration!==detailGeneration||choice.value!==selectedProject||
                  !journeyHost.contains(downloadPacket))return;
                const original=handoffReviewPacket(data,selectedProject,idea.idea_id,'');
                const current=handoffReviewPacket(fresh,selectedProject,idea.idea_id,'');
                if(JSON.stringify(original)!==JSON.stringify(current)){
                  guideStatus.textContent='Evidence or review state changed. Packet withheld. Refresh the evidence journey and inspect the exact project again.';
                  return;
                }
                current.generated_at=new Date().toISOString();
                const blob=new Blob([JSON.stringify(current,null,2)+'\n'],{type:'application/json'});
                const url=URL.createObjectURL(blob),link=el('a');
                link.href=url;link.download='owg-evidence-handoff-review.json';
                document.body.appendChild(link);link.click();link.remove();
                setTimeout(()=>URL.revokeObjectURL(url),1000);
                guideStatus.textContent='Fresh selected-project review packet downloaded. Outstanding checks remain; no approval or execution permission was granted.';
              }catch(e){
                if(requestGeneration===detailGeneration&&journeyHost.contains(downloadPacket))
                  guideStatus.textContent='Packet withheld: '+e.message+'. Refresh evidence and retry.';
              }finally{downloadPacket.disabled=false;}
            };

            // Local file read + explicit fresh authenticated journey fetch. No packet upload.
            const priorPacket=input('owgPipelineJourneyPriorPacket','file');
            priorPacket.accept='.json,application/json';
            const comparePacket=button('Compare saved packet with current local evidence','secondary');
            comparePacket.id='owgPipelineJourneyComparePacket';
            const compareResult=el('div','Select a previously downloaded packet to check for drift.','muted');
            compareResult.id='owgPipelineJourneyCompareResult';
            compareResult.setAttribute('role','status');compareResult.setAttribute('aria-live','polite');
            let comparisonSequence=0;
            priorPacket.onchange=()=>{comparisonSequence++;compareResult.textContent='File selected. Compare explicitly to refresh local references.';};
            comparePacket.onclick=async()=>{
              if(generation!==detailGeneration||choice.value!==p.project_id||
                !artifact.contains(compareResult))return;
              const file=priorPacket.files?.[0];
              if(!file){compareResult.textContent='Select a review packet JSON file first.';return;}
              if(!file.size||file.size>65536){compareResult.textContent='Review packet must be a non-empty JSON file no larger than 64 KiB.';return;}
              const requestSequence=++comparisonSequence;
              comparePacket.disabled=true;
              compareResult.textContent='Checking the saved packet against freshly fetched local references…';
              try{
                const {saved,inspectionRecord:bundledInspection}=unpackHandoffReviewInput(JSON.parse(await file.text()));
                if(generation!==detailGeneration||choice.value!==p.project_id||!artifact.contains(compareResult)||requestSequence!==comparisonSequence)return;
                const fresh=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/journey')).json();
                if(generation!==detailGeneration||choice.value!==p.project_id||!artifact.contains(compareResult)||requestSequence!==comparisonSequence)return;
                const latest=handoffReviewPacket(fresh,p.project_id,idea.idea_id,new Date().toISOString());
                const comparison=compareHandoffReviewPackets(saved,latest);
                compareResult.replaceChildren(el('p',comparison.status==='matching_snapshot'?
                  'Recorded fields match the current local references. Execution, causality and business outcomes remain unverified.':
                  'Recorded fields changed since the saved packet. Re-review these areas before any further decision.'));
                // Explicit, ephemeral human inspection record: only hard-coded
                // categories and current blocker keys, never imported free text.
                const inspection=el('section');inspection.id='owgPipelineJourneyInspectionWorklist';
                inspection.appendChild(el('h6','Human inspection worklist'));
                inspection.appendChild(el('p','Mark items you inspected locally. This is not approval, resolution, source verification or permission to run an agent.','muted'));
                const inspectionItems=[
                  ...comparison.changes.map(label=>({key:'change:'+label,label:'Changed: '+label})),
                  ...latest.readiness.blockers.map(key=>({key:'blocker:'+key,label:'Outstanding: '+(names[key]||'Additional human review requirement')}))
                ];
                const inspected=new Set(),inspectionList=el('ul'),inspectionChecksByKey=new Map();
                const inspectionStatus=el('div',null,'muted');
                inspectionStatus.setAttribute('role','status');
                const updateInspectionStatus=()=>{inspectionStatus.textContent=inspected.size+' of '+inspectionItems.length+
                  ' items inspected locally. All approval and verification gates remain open.';};
                inspectionItems.forEach((item,index)=>{
                  const check=input('owgPipelineInspection'+index,'checkbox');
                  inspectionChecksByKey.set(item.key,check);
                  const label=el('label',item.label);label.prepend(check);
                  const line=el('li');line.appendChild(label);inspectionList.appendChild(line);
                  check.onchange=()=>{
                    if(check.checked)inspected.add(item.key);else inspected.delete(item.key);
                    updateInspectionStatus();
                  };
                });
                updateInspectionStatus();
                const exportInspection=button('Download human inspection worklist','secondary');
                exportInspection.id='owgPipelineJourneyInspectionDownload';
                exportInspection.onclick=async()=>{
                  if(generation!==detailGeneration||choice.value!==p.project_id||
                    requestSequence!==comparisonSequence||!compareResult.contains(exportInspection))return;
                  exportInspection.disabled=true;
                  try{
                    const record=handoffInspectionWorklist(saved,latest,[...inspected],new Date().toISOString());
                    record.comparison_sha256=await handoffInspectionSnapshotDigest(saved,latest);
                    if(generation!==detailGeneration||choice.value!==p.project_id||
                      requestSequence!==comparisonSequence||!compareResult.contains(exportInspection))return;
                    const blob=new Blob([JSON.stringify(record,null,2)+'\n'],{type:'application/json'});
                    const url=URL.createObjectURL(blob),link=el('a');
                    link.href=url;link.download='owg-human-inspection-worklist.json';
                    document.body.appendChild(link);link.click();link.remove();
                    setTimeout(()=>URL.revokeObjectURL(url),1000);
                    guideStatus.textContent='Downloaded local inspection notes for this exact comparison. No approval or execution permission was granted.';
                  }catch(e){inspectionStatus.textContent='Could not export local inspection notes: '+e.message;}
                  finally{exportInspection.disabled=false;}
                };
                const exportBundle=button('Download resumable review bundle (one file)','secondary');
                exportBundle.id='owgPipelineJourneyBundleDownload';
                exportBundle.onclick=async()=>{
                  if(generation!==detailGeneration||choice.value!==p.project_id||
                    requestSequence!==comparisonSequence||!compareResult.contains(exportBundle))return;
                  exportBundle.disabled=true;
                  try{
                    const record=handoffInspectionWorklist(saved,latest,[...inspected],new Date().toISOString());
                    record.comparison_sha256=await handoffInspectionSnapshotDigest(saved,latest);
                    if(generation!==detailGeneration||choice.value!==p.project_id||
                      requestSequence!==comparisonSequence||!compareResult.contains(exportBundle))return;
                    const bundle=handoffReviewBundle(saved,record);
                    const payload=JSON.stringify(bundle,null,2)+'\n';
                    const blob=new Blob([payload],{type:'application/json'});
                    if(blob.size>65536)throw new Error('Bundle exceeds 64 KiB. Use separate review and inspection files.');
                    const url=URL.createObjectURL(blob),link=el('a');
                    link.href=url;link.download='owg-resumable-human-review.json';
                    document.body.appendChild(link);link.click();link.remove();
                    setTimeout(()=>URL.revokeObjectURL(url),1000);
                    guideStatus.textContent='Downloaded one resumable local review file. Inspection is not approval or verification.';
                  }catch(e){inspectionStatus.textContent='Could not export review bundle: '+e.message;}
                  finally{exportBundle.disabled=false;}
                };
                const importInspection=input('owgPipelineJourneyInspectionImportFile','file');
                importInspection.accept='.json,application/json';
                const restoreInspection=button('Restore local inspection marks for this exact comparison','secondary');
                restoreInspection.id='owgPipelineJourneyInspectionRestore';
                const restoreStatus=el('div','Only an exact matching, explicitly selected worklist can restore local checkmarks.','muted');
                restoreStatus.id='owgPipelineJourneyInspectionRestoreStatus';
                restoreStatus.setAttribute('role','status');restoreStatus.setAttribute('aria-live','polite');
                let restoreSequence=0;
                importInspection.onchange=()=>{
                  restoreSequence++;
                  restoreStatus.textContent='Inspection file selected. Restore explicitly; no approval is imported.';
                };
                restoreInspection.onclick=async()=>{
                  if(generation!==detailGeneration||choice.value!==p.project_id||
                    requestSequence!==comparisonSequence||!compareResult.contains(restoreInspection))return;
                  const file=importInspection.files?.[0];
                  if(!file||!file.size||file.size>65536){
                    restoreStatus.textContent='Select a non-empty inspection JSON file no larger than 64 KiB.';return;
                  }
                  const seq=++restoreSequence;
                  restoreInspection.disabled=true;
                  restoreStatus.textContent='Checking inspection notes against the exact fresh comparison…';
                  try{
                    const record=JSON.parse(await file.text());
                    const digest=await handoffInspectionSnapshotDigest(saved,latest);
                    if(generation!==detailGeneration||choice.value!==p.project_id||
                      requestSequence!==comparisonSequence||seq!==restoreSequence||
                      importInspection.files?.[0]!==file||!compareResult.contains(restoreInspection))return;
                    const keys=restoreHandoffInspectionKeys(record,saved,latest,digest);
                    const allowed=new Set(keys);
                    inspected.clear();
                    inspectionChecksByKey.forEach((check,key)=>{
                      check.checked=allowed.has(key);
                      if(check.checked)inspected.add(key);
                    });
                    updateInspectionStatus();
                    restoreStatus.textContent='Restored '+inspected.size+
                      ' local inspection marks for this exact comparison. No approvals or verification were restored.';
                  }catch(e){
                    restoreStatus.textContent='Inspection marks not restored: '+
                      (e instanceof SyntaxError?'Invalid JSON.':e.message);
                  }finally{restoreInspection.disabled=false;}
                };
                inspection.append(inspectionList,inspectionStatus,exportInspection,exportBundle,
                  field('Previously downloaded human inspection worklist (local JSON only)',importInspection),
                  restoreInspection,restoreStatus);
                compareResult.appendChild(inspection);
                if(comparison.changes.length){
                  const differences=el('ul');
                  comparison.changes.forEach(name=>differences.appendChild(el('li',name)));
                  compareResult.appendChild(differences);
                  // Drift review is navigation only. Never infer authorization from a
                  // matching packet, and never substitute a newer process revision.
                  const followups=el('section');followups.id='owgPipelineJourneyDriftActions';
                  followups.appendChild(el('h6','Review changed areas'));
                  const changed=new Set(comparison.changes);
                  const offer=(label,action,disabled=false,explanation='')=>{
                    const control=button(label,'secondary');
                    control.disabled=disabled;
                    control.onclick=()=>{
                      if(generation!==detailGeneration||choice.value!==p.project_id||
                        !artifact.contains(followups)||control.disabled)return;
                      action();
                    };
                    followups.appendChild(control);
                    if(disabled&&explanation)followups.appendChild(el('p',explanation,'muted'));
                  };
                  const freshExactProcess=!!ref&&['current','changed'].includes(latest.process.status)&&
                    latest.process.workflow_id===ref.workflow_id&&latest.process.revision===ref.revision&&
                    latest.process.content_sha256===ref.content_sha256;
                  if(changed.has('Saved process revision and digest')||changed.has('Saved process availability'))
                    offer('Inspect exact saved process revision',()=>processAction.click(),
                      processAction.disabled||!freshExactProcess,'The linked saved revision is unavailable or uncheckable on the fresh read. No newer revision will be substituted.');
                  if(changed.has('Opportunity revision and state'))
                    offer('Review opportunity assessment and decision',()=>ideaAction.click());
                  if(changed.has('Project revision and artifact hashes'))
                    offer('Inspect selected immutable project',()=>{
                      choice.focus();guideStatus.textContent='Review the selected project ID and recorded hashes. Changing project requires an explicit selection; no export was started.';
                    });
                  if(changed.has('Imported report status'))
                    offer('Review imported reports for this project',()=>reportsAction.click());
                  if(changed.has('Recorded source-check exports'))
                    offer('Prepare separate source review for this project',()=>sourceAction.click(),
                      sourceAction.disabled||!latest.project.idea_revision_is_current,
                      'Historical projects cannot prepare a source read. Review the current idea and project first.');
                  if(changed.has('Dataset and cohort source checks'))
                    followups.appendChild(el('p','Dataset or cohort evidence changed. Review the source origin and consent in its original workspace; this journey cannot repair or independently verify it.','muted'));
                  if(changed.has('Human review blockers'))
                    offer('Inspect outstanding human review checks',()=>{
                      review.scrollIntoView?.({block:'nearest'});review.focus?.();
                      guideStatus.textContent='Review the outstanding checks for this exact project. No approval or execution permission was granted.';
                    });
                  compareResult.appendChild(followups);
                }
                // Importing a bundle is one explicit action: compare the saved
                // packet against a fresh authenticated read, then restore only
                // exact-matching inspection marks. Drift remains visible even
                // when marks are refused; nothing is uploaded or authorized.
                if(bundledInspection){
                  try{
                    const digest=await handoffInspectionSnapshotDigest(saved,latest);
                    if(generation!==detailGeneration||choice.value!==p.project_id||
                      requestSequence!==comparisonSequence||!compareResult.contains(inspection))return;
                    const keys=restoreHandoffInspectionKeys(bundledInspection,saved,latest,digest);
                    const allowed=new Set(keys);
                    inspected.clear();
                    inspectionChecksByKey.forEach((check,key)=>{
                      check.checked=allowed.has(key);
                      if(check.checked)inspected.add(key);
                    });
                    updateInspectionStatus();
                    restoreStatus.textContent='Resumed '+inspected.size+
                      ' local inspection marks from one review bundle after a fresh comparison. No approvals were restored.';
                  }catch(e){
                    if(generation===detailGeneration&&choice.value===p.project_id&&
                      requestSequence===comparisonSequence&&compareResult.contains(inspection))
                      restoreStatus.textContent='Bundle inspection marks not restored: '+
                        e.message+' Review changed areas and inspect again.';
                  }
                }
              }catch(e){
                if(generation===detailGeneration&&choice.value===p.project_id&&artifact.contains(compareResult)&&requestSequence===comparisonSequence)
                  compareResult.textContent='Could not compare review packet: '+(e instanceof SyntaxError?'Invalid JSON.':e.message);
              }finally{
                if(generation===detailGeneration&&choice.value===p.project_id&&artifact.contains(compareResult))
                  comparePacket.disabled=false;
              }
            };
            // Only a saved review bundle carries a file to re-compare; opening an exact
            // project from the portfolio inbox just selects it.
            if(resume&&resume.file instanceof File&&p.project_id===resume.projectId)resumeCompare=async()=>{
              // Reuse the exact same fresh authenticated comparison and digest
              // validation as manual import; the file never leaves the browser.
              const transfer=new DataTransfer();
              transfer.items.add(resume.file);
              priorPacket.files=transfer.files;
              await comparePacket.onclick();
            };
            actions.append(reportsAction,sourceAction,downloadPacket);
            const packetImport=el('section');packetImport.append(
              field('Previously downloaded review packet or resumable review bundle (local JSON only)',priorPacket),
              comparePacket,compareResult);
            // Make the dependency chain inspectable without treating it as
            // authoritative lineage or a handoff authorization. Rebuilt on each
            // exact-project selection and discarded on journey refresh.
            const dependencyButton=button('Review evidence dependencies for this project','secondary');
            dependencyButton.id='owgPipelineDependencyReview';
            const dependencyHost=el('section');dependencyHost.id='owgPipelineDependencyResult';
            dependencyHost.setAttribute('role','status');
            dependencyHost.setAttribute('aria-live','polite');
            dependencyButton.onclick=()=>{
              if(generation!==detailGeneration||choice.value!==p.project_id||
                !artifact.contains(dependencyButton))return;
              try{
                const audit=handoffDependencyReview(data,p.project_id,idea.idea_id,new Date().toISOString());
                const summary=el('p',audit.coverage.locally_current_or_recorded+' of '+
                  audit.coverage.stages+' recorded stages have locally current/reviewed/artifact status; '+
                  audit.coverage.needs_human_review+' require further review. '+
                  'All independent outcome and execution gates remain unverified.');
                const table=el('table'),head=el('tr');
                for(const label of ['Dependency stage','Recorded status','Pinned basis / coverage','Review impact']){
                  const th=el('th',label);th.scope='col';head.appendChild(th);
                }
                const thead=el('thead');thead.appendChild(head);
                const tbody=el('tbody');
                audit.stages.forEach(stage=>{
                  const tr=el('tr'),th=el('th',stage.label);th.scope='row';
                  tr.append(th,el('td',stage.status.replaceAll('_',' ')),
                    el('td',stage.basis),
                    el('td',(stage.affected_by.length?
                      'Earlier unresolved basis: '+stage.affected_by.join(', ')+'. ':'')+stage.note));
                  tbody.appendChild(tr);
                });
                table.append(thead,tbody);
                const wrap=el('div',null,'table-wrap');wrap.style.overflowWrap='anywhere';
                wrap.appendChild(table);
                const download=button('Download minimized dependency review JSON','secondary');
                download.id='owgPipelineDependencyDownload';
                download.onclick=()=>{
                  if(generation!==detailGeneration||choice.value!==p.project_id||
                    !dependencyHost.contains(download))return;
                  const current=handoffDependencyReview(data,p.project_id,idea.idea_id,new Date().toISOString());
                  const body=JSON.stringify(current,null,2)+'\n';
                  if(body.length>65536){dependencyHost.appendChild(el('p','Dependency review exceeds the 64 KiB export limit.','muted'));return;}
                  const url=URL.createObjectURL(new Blob([body],{type:'application/json'}));
                  const link=el('a');link.href=url;link.download='owg-handoff-dependency-review.json';
                  document.body.appendChild(link);link.click();link.remove();
                  setTimeout(()=>URL.revokeObjectURL(url),1000);
                  guideStatus.textContent='Downloaded the exact-project local dependency review. No source read, approval or agent action occurred.';
                };
                dependencyHost.replaceChildren(el('h6','Evidence dependency and coverage review'),
                  summary,wrap,el('p','This is a point-in-time local review map, not authoritative cross-store lineage. '+
                  'A stale upstream basis affects downstream handoff review but does not change immutable artifact hashes.','muted'),download);
              }catch(e){dependencyHost.textContent='Cannot review dependency coverage: '+e.message;}
            };
            artifact.append(review,dependencyButton,dependencyHost,actions,packetImport);
            if(!p.idea_revision_is_current)artifact.appendChild(el('p',
              'Historical idea revision: reopen and review the current idea before attempting new checks or report associations.','muted'));
          };
          choice.onchange=()=>{invalidateProjectComparison();render();};journeyHost.append(field('Recorded project',choice),projectCompare,artifact);render();
          if(resume&&resume.valid()&&generation===detailGeneration&&resumeCompare){
            await resumeCompare();
            if(resume.valid()&&generation===detailGeneration)
              resumeStatus.textContent='Opened the exact saved idea and project. Fresh comparison completed; inspect its drift and restoration status below. No approval, consent or execution was granted.';
          }
          if(data.projects_truncated)journeyHost.appendChild(el('p','Only the first '+data.max_projects+' projects are shown.'));
        }catch(e){if(generation===detailGeneration){
          journeyHost.replaceChildren(el('div',e.message,'muted'));
          if(resume&&resume.valid())resumeStatus.textContent='Could not resume the exact project: '+e.message;
        }}
        finally{journeyButton.disabled=false;journeyButton.textContent='Refresh evidence journey';}
      };
      box.append(journeyButton,journeyHost);
      if(idea.process_reference){
        const projects=el('div');projects.id='owgPipelineProjects';
        projects.appendChild(el('h4','Agent project exports'));
        projects.appendChild(el('div','Download a starter for this exact idea and saved design. Tool mappings and execution remain disabled. Export records show what was generated, not a deployment or verified outcome.','muted'));
        const exports=el('div');const message=el('div',null,'muted');message.setAttribute('role','status');
        const refreshProjects=async()=>{
          const result=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects')).json();
          if(generation!==detailGeneration)return;
          const list=el('ul');
          result.projects.forEach(p=>list.appendChild(el('li','Idea revision '+p.idea_revision+' · '+p.created_at+' · '+(p.idea_revision_is_current?'current idea revision':'historical idea revision')+' · specification '+p.spec_sha256.slice(0,12)+'…')));
          exports.replaceChildren(result.projects.length?list:el('div','No project exports recorded.','muted'));
        };
        const download=button('Download tracked agent project','secondary');download.id='owgPipelineProjectDownload';
        download.disabled=!['approved','implemented'].includes(idea.state);
        download.onclick=async()=>{
          download.disabled=true;
          try{
            const response=await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/agent-project',{
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:idea.revision})});
            const blob=await response.blob();const url=URL.createObjectURL(blob);const link=el('a');
            link.href=url;link.download='owg-opportunity-project.zip';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            if(generation===detailGeneration){message.textContent='Project downloaded. Its specification and archive hashes are recorded; execution remains disabled.';await refreshProjects();}
          }catch(e){if(generation===detailGeneration)message.textContent=e.message;}
          finally{download.disabled=!['approved','implemented'].includes(idea.state);}
        };
        const archiveFile=input('owgPipelineArchiveReviewFile','file');
        archiveFile.accept='.zip,application/zip';
        const archiveReview=button('Verify original project ZIP','secondary');
        const archiveStatus=el('div');archiveStatus.setAttribute('role','status');
        let archiveVersion=0;
        archiveFile.onchange=()=>{archiveVersion++;archiveStatus.replaceChildren();};
        archiveReview.onclick=async()=>{
          const file=archiveFile.files?.[0],version=++archiveVersion;
          if(!file||!file.size||file.size>16*1024*1024){
            archiveStatus.textContent='Choose the original project ZIP of at most 16 MiB.';return;
          }
          archiveReview.disabled=true;
          try{
            const result=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-archive-review',{
              method:'POST',headers:{'Content-Type':'application/zip'},body:file})).json();
            if(generation!==detailGeneration||version!==archiveVersion)return;
            archiveStatus.replaceChildren(
              el('p','Exact retained project ZIP matches · '+result.exported_at),
              el('p',result.generated_file_inventory_status==='complete_generated_file_inventory_matches'?
                result.verified_generated_file_count+' generated files match the complete release inventory.':
                'Historical ZIP without a generated-file manifest; original archive digest matches.'),
              el('p','Idea revision '+result.idea_revision_at_export+' (current '+result.current_idea_revision+
                ') · saved design '+result.process_reference_status.replaceAll('_',' ')+'.'),
              el('p','No extraction, execution, remote deployment or business outcome was verified.','muted'));
          }catch(e){if(generation===detailGeneration&&version===archiveVersion)archiveStatus.textContent=e.message;}
          finally{archiveReview.disabled=false;if(version===archiveVersion)archiveFile.value='';}
        };
        projects.append(download,message,exports,el('h5','Verify a previous project export'),
          el('p','Select the unchanged original ZIP. OWG compares the retained archive hash and complete generated-file inventory without extracting or running its contents.','muted'),
          field('Original tracked project ZIP',archiveFile),archiveReview,archiveStatus);
        const reviewReports=button('Review imported run reports','secondary');reviewReports.id='owgPipelineReviewReports';
        const reports=el('div');reports.id='owgPipelineReportLinks';let reportVersion=0;
        const renderReports=async()=>{
          const version=++reportVersion;
          const data=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports')).json();
          if(generation!==detailGeneration||version!==reportVersion)return;
          const note=el('div');note.setAttribute('role','status');
          const help=el('div','Link a selected imported report only when its reported specification and attached design both match this exported project. These remain customer reports: individual runs, execution and outcomes are not independently verified.','muted');
          const artifact=select('owgPipelineReportProject',data.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at+' · '+p.spec_sha256.slice(0,12)+'…']));
          if(preferredReportProjectId&&data.projects.some(p=>p.project_id===preferredReportProjectId))
            artifact.value=preferredReportProjectId;
          const deployment=select('owgPipelineReportDeployment',data.deployments.map(d=>[d.deployment_id,d.name+' · '+d.runtime+' · '+d.reported_run_count+' reported runs']));
          const add=button('Link selected report','secondary');add.id='owgPipelineLinkReport';
          add.disabled=!data.monitoring_metadata_available||!data.projects.length||!data.deployments.length;
          add.onclick=async()=>{
            add.disabled=true;
            try{
              await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports',{
                project_id:artifact.value,deployment_id:deployment.value,expected_revision:idea.revision});
              if(generation===detailGeneration){await renderReports();status.textContent='Report link saved. Matching hashes are reported metadata, not independently verified execution or outcomes.';}
            }catch(e){if(generation===detailGeneration)note.textContent=e.message;}
            finally{add.disabled=!data.monitoring_metadata_available||!data.projects.length||!data.deployments.length;}
          };
          const rows=el('ul');
          const labels={matching_reported_specification:'Matching reported specification',unbound_specification:'Specification is not bound',
            specification_mismatch:'Specification changed or does not match',unavailable:'Reported deployment unavailable',check_failed:'Report source could not be checked'};
          data.links.forEach(r=>{
            const source=data.deployments.find(d=>d.deployment_id===r.deployment_id);
            const item=el('li',(source?source.name:r.deployment_id)+' · '+(labels[r.status]||'Unknown status')+(Number.isSafeInteger(r.runs_reporting_specification_count)?' · '+r.runs_reporting_specification_count+' runs reported this exact specification, '+r.unhashed_run_count+' without a hash':''));
            const remove=button('Remove report link','secondary');remove.setAttribute('aria-label','Remove report link '+r.deployment_id);
            remove.onclick=async()=>{
              try{await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/project-reports/unlink',{
                project_id:r.project_id,deployment_id:r.deployment_id,expected_revision:idea.revision});
                if(generation===detailGeneration)await renderReports();
              }catch(e){if(generation===detailGeneration)note.textContent=e.message;}
            };
            item.appendChild(remove);rows.appendChild(item);
          });
          if(!data.monitoring_metadata_available)note.textContent='Monitoring metadata is unavailable; report links cannot be validated.';
          reports.replaceChildren(el('h4','Imported report links'),help,field('Exported project',artifact),field('Imported report',deployment),add,note,
            data.links.length?rows:el('div','No report links recorded.','muted'));
        };
        reviewReports.onclick=()=>renderReports().catch(e=>{if(generation===detailGeneration)status.textContent=e.message;});
        projects.append(reviewReports,reports);
        const sourceChecks=button('Check selected source state','secondary');sourceChecks.id='owgPipelineSourceChecks';
        const checkHost=el('div');checkHost.id='owgPipelineSourceCheckForm';let sourceView=0;
        sourceChecks.onclick=async()=>{
          const view=++sourceView;
          try{
            const saved=await (await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects')).json();
            if(generation!==detailGeneration||view!==sourceView)return;
            const selected=select('owgPipelineSourceProject',saved.projects.map(p=>[p.project_id,'Idea revision '+p.idea_revision+' · '+p.created_at]));
            if(preferredSourceProjectId&&saved.projects.some(p=>p.project_id===preferredSourceProjectId))
              selected.value=preferredSourceProjectId;
            const expectation=select('owgPipelineSourceExpectation',[]);
            const runChoice=select('owgPipelineSourceRun',[]);let runs=[];
            const sourceRef=input('owgPipelineSourceRecord','text','owg:r:…');sourceRef.maxLength=38;sourceRef.autocomplete='off';sourceRef.spellcheck=false;
            const confirmed=input('owgPipelineSourceConsent','checkbox');const consentText=el('label');consentText.append(confirmed,document.createTextNode(' Read this exact observed record and download the minimized source-check document. Existing provider opt-ins still apply.'));
            const run=button('Read source and download check','secondary');run.id='owgPipelineSourceExport';run.disabled=true;
            const note=el('div',null,'muted');note.setAttribute('role','status');const history=el('div');
            const reviewFile=input('owgPipelineSourceReviewFile','file');reviewFile.accept='.json,application/json';
            const reviewButton=button('Reconcile original downloaded check','secondary');
            const reviewResult=el('div');reviewResult.setAttribute('role','status');
            let reviewVersion=0;
            reviewFile.onchange=()=>{reviewVersion++;reviewResult.replaceChildren();};
            reviewButton.onclick=async()=>{
              const file=reviewFile.files?.[0],path=base(),version=++reviewVersion;
              if(!file||!file.size||file.size>65536){reviewResult.textContent='Choose the original JSON download of at most 64 KiB.';return;}
              if(!selected.value){reviewResult.textContent='Choose the original exported project first.';return;}
              reviewButton.disabled=true;
              try{
                const response=await api(path+'/source-check-review',{method:'POST',
                  headers:{'Content-Type':'application/json'},body:file});
                const value=await response.json();
                if(generation!==detailGeneration||view!==sourceView||version!==reviewVersion)return;
                reviewResult.replaceChildren(
                  el('p','Exact retained export bytes match · exported '+value.exported_at),
                  el('p','Historical source operation: '+(value.historical_source_operation_reported_verified?'reported verified':'not established')+
                    ' · '+(value.historical_source_availability||'unknown')),
                  el('p','Saved design: '+value.current_process_reference_status.replaceAll('_',' ')+
                    (value.latest_process_revision!==null?' · latest revision '+value.latest_process_revision:'')),
                  el('p','Historical expectation: '+value.process_expectation_status.replaceAll('_',' ')+
                    ' · Run association: '+value.run_attribution_status.replaceAll('_',' ')),
                  el('p','Idea revision at export '+value.idea_revision_at_export+'; current '+value.current_idea_revision+
                    '. No fresh source read, agent causality or business outcome verified.','muted'));
              }catch(e){if(generation===detailGeneration&&view===sourceView&&version===reviewVersion)reviewResult.textContent=e.message;}
              finally{reviewButton.disabled=false;if(version===reviewVersion)reviewFile.value='';}
            };
            let entries=[],formVersion=0,optionsVersion=0,pending=false;
            const ready=()=>{run.disabled=pending||!entries.length||!confirmed.checked||!/^owg:r:[0-9a-f]{32}$/.test(sourceRef.value.trim());confirmed.disabled=pending||!entries.length;};
            const invalidate=()=>{formVersion++;confirmed.checked=false;ready();};
            sourceRef.oninput=invalidate;expectation.onchange=invalidate;runChoice.onchange=invalidate;confirmed.onchange=ready;
            const base=()=>'/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects/'+encodeURIComponent(selected.value);
            const loadOptions=async()=>{
              const version=++optionsVersion;invalidate();entries=[];runs=[];expectation.replaceChildren();
              const none=el('option','No specific run');none.value='';runChoice.replaceChildren(none);ready();
              if(!selected.value){note.textContent='Export a linked project before checking source state.';return;}
              const path=base();
              const results=await Promise.allSettled([api(path+'/source-check-options').then(r=>r.json()),api(path+'/source-check-exports').then(r=>r.json())]);
              if(generation!==detailGeneration||view!==sourceView||version!==optionsVersion)return;
              const options=results[0],records=results[1];
              if(options.status==='fulfilled'&&options.value.current_idea_revision===idea.revision){
                entries=options.value.expectations;runs=options.value.attributable_runs||[];
                runs.forEach((r,i)=>{const opt=el('option',r.run_id+' · '+r.run_status+' · started '+r.run_started_at);opt.value=String(i);runChoice.appendChild(opt);});
                entries.forEach((e,i)=>{const opt=el('option',e.activity_label+' · '+e.operation.replaceAll('_',' '));opt.value=String(i);expectation.appendChild(opt);});
                note.textContent=entries.length?'Select the exact observed record. Source state does not prove agent causality, run/case identity or customer outcomes.':'This saved process has no confirmed source-state expectation. Review one in a new saved revision before creating its idea/project.';
              }else note.textContent=options.status==='rejected'?options.reason.message:'The idea changed; reopen it before checking a source.';
              if(records.status==='fulfilled'){
                const list=el('ul');records.value.exports.forEach(r=>list.appendChild(el('li','Check document exported '+r.created_at+' · hash '+r.document_sha256.slice(0,12)+'…')));
                history.replaceChildren(el('div','Only export hashes and times are retained here; receipt bodies and observed-record references are not stored.','muted'),records.value.exports.length?list:el('div','No source-check exports recorded.','muted'));
              }
              ready();
            };
            selected.onchange=()=>{sourceRef.value='';reviewVersion++;reviewFile.value='';reviewResult.replaceChildren();loadOptions().catch(e=>{if(generation===detailGeneration)note.textContent=e.message;});};
            run.onclick=async()=>{
              if(run.disabled)return;
              const entry=entries[Number(expectation.value)],path=base(),version=formVersion;
              const payload={expected_revision:idea.revision,terminal_activity_id:entry.terminal_activity_id,expected_operation:entry.operation,
                resource_ref:sourceRef.value.trim(),source_read_and_export_confirmed:confirmed.checked};
              const chosen=runChoice.value===''?null:runs[Number(runChoice.value)];
              if(chosen)payload.run_reference={deployment_id:chosen.deployment_id,run_id:chosen.run_id};
              confirmed.checked=false;pending=true;ready();
              try{
                const response=await api(path+'/source-check-export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const blob=await response.blob();
                if(generation!==detailGeneration||view!==sourceView||version!==formVersion)return;
                const url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='owg-project-source-check.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
                sourceRef.value='';await loadOptions();note.textContent='Source-check document downloaded. Unknown results remain unknown; no agent causality or business outcome is established.';
              }catch(e){if(generation===detailGeneration&&view===sourceView&&version===formVersion)note.textContent=e.message;}
              finally{pending=false;ready();}
            };
            checkHost.replaceChildren(el('h4','Selected source-state check'),el('div','This performs an explicitly authorized read through existing source adapters. The receipt is downloaded only; OWG retains a checksum, not the receipt or a verified-state value.','muted'),
              field('Exported project for source check',selected),field('Confirmed source-state expectation',expectation),field('Reported run (only runs that reported this project\'s exact specification, from linked reports)',runChoice),field('Observed record reference (owg:r:…)',sourceRef),consentText,run,note,history,
              el('h5','Review a previous download'),el('p','Select the unchanged original file to compare its bytes with the retained export hash. This local review does not retain the file or contact a provider.','muted'),
              field('Original source-check JSON file',reviewFile),reviewButton,reviewResult);
            await loadOptions();
          }catch(e){if(generation===detailGeneration&&view===sourceView)status.textContent=e.message;}
        };
        projects.append(sourceChecks,checkHost);box.appendChild(projects);
        refreshProjects().catch(e=>{if(generation===detailGeneration)message.textContent=e.message;});
      }
      if(idea.score)box.appendChild(el('div','Ease '+idea.score.ease_score+'/100 · '+idea.score.modeled_hours_per_month+' modeled hours per month · '+QUADRANTS[idea.score.quadrant]+(idea.assessment.compliance_review_needed?' · needs compliance review':''),'muted'));
      if(!['implemented','rejected'].includes(idea.state)){
        const answers={};const qrow=el('div',null,'row');qrow.style.alignItems='end';
        QUESTIONS.forEach(([key,label])=>{const s=select('owgPipelineQ_'+key,[['','—'],['1','1 · not at all'],['2','2'],['3','3'],['4','4'],['5','5 · fully']]);if(idea.assessment)s.value=String(idea.assessment.ease[key]);answers[key]=s;qrow.appendChild(field(label,s));});
        const cases=input('owgPipelineCases','number');cases.min='0';const minutes=input('owgPipelineMinutes','number');minutes.min='0';const share=input('owgPipelineShare','number');share.min='0';share.max='100';
        const compliance=input('owgPipelineCompliance','checkbox');const complianceLabel=el('label');complianceLabel.style.cssText='display:flex;gap:6px;align-items:center';complianceLabel.append(compliance,document.createTextNode(' Needs compliance review'));
        if(idea.assessment){cases.value=idea.assessment.cases_per_month;minutes.value=idea.assessment.minutes_per_case;share.value=Math.round(idea.assessment.eligible_share*100);compliance.checked=idea.assessment.compliance_review_needed;}
        const save=button('Save assessment','secondary');save.id='owgPipelineAssess';
        const arow=el('div',null,'row');arow.style.alignItems='end';arow.append(field('Cases per month (your estimate)',cases),field('Minutes per case',minutes),field('Eligible share (%)',share),complianceLabel,save);
        save.onclick=async()=>{try{
          const values={cases:cases.value,minutes:minutes.value,share:share.value,compliance:compliance.checked};
          for(const [key] of QUESTIONS)values[key]=answers[key].value;
          const saved=await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/assess',assessmentBody(values,idea.revision));
          await refresh();await show(idea.idea_id);status.textContent=idea.state==='approved'&&saved.state==='assessing'?'Assessment saved. Approval cleared; review the new assessment.':'Assessment saved.';
        }catch(e){status.textContent=e.message;}};
        box.append(el('h4','Assessment'),qrow,arow);
        if(idea.state==='approved')box.appendChild(el('div','Saving an assessment clears this approval and returns the idea to Assessing for a fresh review.','muted'));
      }
      if(idea.allowed_transitions.length){
        const note=input('owgPipelineNote','text','Reason or note (optional)');note.maxLength=1000;
        const moves=el('div',null,'row');moves.style.alignItems='end';moves.appendChild(field('Note',note));
        idea.allowed_transitions.forEach(to=>{const b=button('Move to '+STATE_LABELS[to],'secondary');b.dataset.state=to;
          b.onclick=async()=>{try{await post('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/transition',{to,note:note.value,expected_revision:idea.revision});await refresh();await show(idea.idea_id);status.textContent='Moved to '+STATE_LABELS[to]+'.';}catch(e){status.textContent=e.message;}};
          moves.appendChild(b);});
        box.append(el('h4','Decision'),moves);
      }
      const history=el('ol');idea.history.forEach(h=>{
        const item=el('li',(h.action==='assessment_saved'?'Assessment saved · ':'')+STATE_LABELS[h.state]+' · '+h.at+(h.note?' · '+h.note:''));
        if(h.assessment){
          const details=el('details');details.appendChild(el('summary','Assessment at this decision'));
          const appendAssessment=(a,label)=>{
            details.appendChild(el('div',label+': '+a.cases_per_month+' cases/month × '+a.minutes_per_case+' minutes × '+Number((a.eligible_share*100).toFixed(4))+'% eligible.'));
            const answers=QUESTIONS.map(([key,text])=>text+': '+a.ease[key]+'/5').join(' · ');
            details.appendChild(el('div',answers,'muted'));
            details.appendChild(el('div','Compliance review needed: '+(a.compliance_review_needed?'Yes':'No')));
            if(a.notes)details.appendChild(el('div',a.notes));
          };
          appendAssessment(h.assessment,'Saved assessment');
          if(h.previous_assessment)appendAssessment(h.previous_assessment,'Previous assessment');
          item.appendChild(details);
        }
        history.appendChild(item);
      });
      const remove=button('Delete idea','secondary');remove.id='owgPipelineDelete';
      remove.onclick=async()=>{if(!window.confirm('Delete the idea "'+idea.title+'"? This cannot be undone.'))return;try{await api('/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/delete',{method:'POST'});detail.replaceChildren();await refresh();status.textContent='Idea deleted.';}catch(e){status.textContent=e.message;}};
      box.append(el('h4','History'),history,remove);
      // A re-render of the same idea at the same revision (for example after a list refresh)
      // must not throw away planning the person is still typing.
      const fresh=box.querySelector('#owgPipelinePlanning');
      const draft=planningDrafts.get(idea.idea_id);
      if(draft&&draft.revision!==idea.revision)planningDrafts.delete(idea.idea_id);  // saved elsewhere: the server's values win
      else if(draft&&fresh){
        for(const [id,value] of Object.entries(draft.values)){
          const twin=fresh.querySelector('#'+CSS.escape(id));if(!twin)continue;
          if(twin.type==='checkbox')twin.checked=Boolean(value);else twin.value=String(value);
        }
        fresh.dataset.dirty='true';
      }
      detail.replaceChildren(box);
      if(resume&&resume.valid()&&generation===detailGeneration)
        await journeyButton.onclick();
    }

    // A later navigation invalidates every earlier asynchronous handoff attempt.
    // Never let an obsolete response open a decision surface or overwrite status.
    let handoffGeneration=0;
    window.addEventListener('owg-pipeline-open',event=>{
      const generation=++handoffGeneration;
      const current=()=>generation===handoffGeneration;
      const handoff=event.detail||{},id=handoff.idea_id;
      // Reject incomplete or synthetic navigation before any authenticated read.
      // This client guard is not execution authorization; server preflight remains authoritative.
      detail.replaceChildren();
      // A saved process emits an opportunity-only navigation event. It is NOT
      // a reviewed project handoff. Partial approval metadata must not bypass
      // the authenticated reviewed-handoff destination preflight.
      const discoveryOnly=Object.keys(handoff).length===1&&
        Object.prototype.hasOwnProperty.call(handoff,'idea_id');
      if(typeof id!=='string'||!id||id.length>128||
         (!discoveryOnly&&(typeof handoff.project_id!=='string'||!handoff.project_id||handoff.project_id.length>128||
         !Number.isSafeInteger(handoff.review_revision)||handoff.review_revision<1))){
        status.textContent='Incomplete reviewed handoff. Inspect the approved project again.';
        return;
      }
      // Carry the exact inspected export as a navigation hint, never as approval.
      // Recheck it against the authenticated journey before opening the evidence UI.
      // Clear any previous decision surface before an asynchronous verification.
      // A rejected handoff must never leave an unrelated approval visible.
      refresh().then(async()=>{
        if(!current())return;
        if(discoveryOnly){
          // A newly saved process has an opportunity but no agent project yet.
          // Open its authenticated evidence journey directly so the person can
          // inspect source/revision status and decide what review is needed.
          await show(id);
          if(!current())return;
          const journeyButton=document.getElementById('owgPipelineJourney');
          if(!journeyButton)throw new Error('Evidence journey control unavailable.');
          // The journey handler owns its authenticated load/error status. Do not
          // overwrite a denied, revoked, or stale-source response with success.
          if(current())status.textContent='Inspecting saved process evidence. No project or execution was authorized.';
          await journeyButton.onclick();
          return;
        }
        const journeyResponse=await api('/v1/automation-pipeline/'+encodeURIComponent(id)+'/journey');
        if(!journeyResponse.ok)throw new Error('Authenticated journey refused ('+journeyResponse.status+').');
        const journey=await journeyResponse.json();
        if(!current())return;
        const matchingProjects=Array.isArray(journey.projects)
          ? journey.projects.filter(p=>p.project_id===handoff.project_id) : [];
        // Truncated or ambiguous project history cannot establish an exact handoff.
        if(journey.projects_truncated||matchingProjects.length!==1){
          status.textContent='Project evidence is incomplete or ambiguous. Inspect again.';
          return;
        }
        const project=matchingProjects[0];
        if(journey.idea?.revision!==handoff.idea_revision||
           journey.idea?.state!==handoff.idea_state||
           project.idea_revision!==handoff.idea_revision||
           project.spec_sha256!==handoff.spec_sha256||project.archive_sha256!==handoff.archive_sha256){
          status.textContent='Selected export changed. Return to the review workbench and inspect again.';
          return;
        }
        // The destination independently revalidates the complete inspected basis.
        // A browser-side preflight is not an authorization or a durable snapshot.
        const basis=handoff.review_basis;
        if(!basis||basis.project_id!==handoff.project_id||
           basis.idea_revision!==handoff.idea_revision||basis.idea_state!==handoff.idea_state||
           basis.spec_sha256!==handoff.spec_sha256||basis.archive_sha256!==handoff.archive_sha256||
           !/^[a-f0-9]{64}$/i.test(basis.project_set_sha256||'')){
          throw new Error('Complete inspected review basis missing. Inspect again.');
        }
        const verified=await api('/v1/review-choice-workbench/'+encodeURIComponent(id)+'/preflight',{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(basis)
        });
        if(!verified.ok)throw new Error('Destination review preflight refused ('+verified.status+').');
        const attestation=await verified.json();
        if(!current())return;
        if(attestation.format!=='openworkgraph.review-preflight.v1'||
           attestation.idea_id!==id||attestation.project_id!==handoff.project_id||
           attestation.idea_revision!==handoff.idea_revision||
           attestation.project_set_sha256!==basis.project_set_sha256||
           attestation.spec_sha256!==handoff.spec_sha256||
           attestation.archive_sha256!==handoff.archive_sha256||
           attestation.execution_authorized!==false||attestation.review_required!==true||
           attestation.source_read_authorized!==false||
           attestation.verified_business_outcomes!==false||
           JSON.stringify(attestation.blockers)!==JSON.stringify(basis.blockers)){
          throw new Error('Destination review attestation inconsistent. Inspect again.');
        }
        // Recheck durable human approval at the destination, independently of the
        // originating workbench. A review-basis preflight alone is not approval.
        if(!Number.isSafeInteger(handoff.review_revision)||handoff.review_revision<1)
          throw new Error('Saved approval revision missing. Inspect again.');
        const approvedResponse=await api('/v1/review-choice-workbench/'+encodeURIComponent(id)+'/reviewed-handoff-preflight',{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(basis)
        });
        if(!approvedResponse.ok)throw new Error('Destination saved approval refused ('+approvedResponse.status+').');
        const approved=await approvedResponse.json();
        if(!current())return;
        if(approved.format!=='openworkgraph.reviewed-handoff-preflight.v1'||
           approved.idea_id!==id||approved.project_id!==handoff.project_id||
           approved.idea_revision!==handoff.idea_revision||
           approved.project_set_sha256!==basis.project_set_sha256||
           approved.spec_sha256!==handoff.spec_sha256||
           approved.archive_sha256!==handoff.archive_sha256||
           approved.review_revision!==handoff.review_revision||
           approved.human_review_approved!==true||approved.execution_authorized!==false||
           approved.source_read_authorized!==false)
          throw new Error('Saved approval changed before destination opened. Inspect again.');
        // Only reveal the decision surface after authenticated exact-export checks.
        await show(id);
        if(!current())return;
        const button=document.getElementById('owgPipelineJourney');
        if(!button)throw new Error('Evidence journey control unavailable.');
        await button.onclick();
        if(current())status.textContent='Opened evidence journey for inspected project '+handoff.project_id+'. Review and approval remain separate.';
      }).catch(e=>{if(current()){detail.replaceChildren();status.textContent='Unable to open reviewed evidence: '+e.message;}});
    });

    add.onclick=async()=>{try{
      if(!title.value.trim())throw new Error('Name the idea.');
      const idea=await post('/v1/automation-pipeline',{title:title.value.trim(),description:description.value.trim(),source:{kind:source.value,reference:reference.value.trim()}});
      title.value='';description.value='';reference.value='';await refresh();await show(idea.idea_id);status.textContent='Idea added.';
    }catch(e){status.textContent=e.message;}};
    search.oninput=()=>{clearCumulative('Search changed; review pages again.');render();};
    quadrant.onchange=()=>{clearCumulative('Priority filter changed; review pages again.');render();};
    sort.onchange=()=>{clearCumulative('Sort changed; review pages again.');render();};
    for(const control of Object.values(planning))control.onchange=render;
    reset.onclick=()=>{search.value='';quadrant.value='';sort.value='priority';filter.value='';
      for(const control of Object.values(planning))control.value='';
      refresh().catch(e=>{status.textContent=e.message;});};
    reload.onclick=()=>{
      status.textContent='Refreshing opportunities…';
      refresh().then(current=>{if(current)status.textContent='Opportunities refreshed.';}).catch(e=>{status.textContent=e.message;});
    };
    filter.onchange=()=>refresh().catch(e=>{status.textContent=e.message;});
    function saveBlob(blob,name){
      const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download=name;
      document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function saveShortlist(format){
      if(!currentView||currentView.scope.state!==(filter.value||null)||!currentView.ideas.length)return;
      const record=shortlistRecord(currentView.ideas,currentView.scope,currentView.total,new Date().toISOString());
      const json=format==='json';
      saveBlob(new Blob([json?JSON.stringify(record,null,2):shortlistCsv(record)],{type:json?'application/json':'text/csv'}),'automation-shortlist.'+format);
    }
    shortlistLink.onclick=()=>saveShortlist('csv');shortlistJson.onclick=()=>saveShortlist('json');
    exportLink.onclick=async()=>{try{
      const blob=await (await api('/v1/automation-pipeline/export.csv'+(filter.value?'?state='+encodeURIComponent(filter.value):''))).blob();
      saveBlob(blob,'automation-pipeline.csv');
    }catch(e){status.textContent=e.message;}};
    refresh().catch(e=>{status.textContent=e.message;});
  }

  window.__owgPipelineModel={assessmentBody,portfolioView,shortlistRecord,shortlistCsv,comparisonRows,handoffReviewPacket,handoffDependencyReview,compareHandoffReviewPackets,compareRecordedProjects,compareProjectHandoffReadiness,portfolioHandoffRow,combinedPortfolioHandoffReview,portfolioReviewTriage,handoffInspectionWorklist,handoffInspectionSnapshotDigest,restoreHandoffInspectionKeys,pilotDispositionWorklist};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
