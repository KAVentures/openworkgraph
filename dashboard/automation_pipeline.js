/* Automation pipeline: review candidate automations locally. Approval records a decision; it never runs anything. */
(() => {
  'use strict';
  const QUESTIONS=[['rules_based','Decisions follow clear rules'],['structured_inputs','Inputs are structured'],['process_stability','The process rarely changes'],['digital_inputs','Inputs arrive digitally'],['low_exception_rate','Exceptions are rare']];
  const STATE_LABELS={idea:'Idea',assessing:'Assessing',approved:'Approved',rejected:'Rejected',on_hold:'On hold',implemented:'Implemented'};
  const QUADRANTS={quick_win:'Quick win',major_project:'Major project',fill_in:'Fill-in',deprioritize:'Deprioritize'};
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
  function portfolioView(ideas,{query='',quadrant='',sort='priority'}={}){
    const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const view=ideas.filter(idea=>{
      if(quadrant==='unassessed'?!!idea.score:quadrant&&idea.score?.quadrant!==quadrant)return false;
      const text=[idea.title,idea.description,idea.source?.reference,idea.source?.kind].filter(Boolean).join(' ').toLocaleLowerCase();
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
    const tools=el('div',null,'row');tools.style.alignItems='end';const search=input('owgPipelineSearch','search','Title, details or source reference');search.maxLength=200;
    const quadrant=select('owgPipelineQuadrant',[['','All priorities'],...Object.entries(QUADRANTS),['unassessed','Not assessed']]);
    const sort=select('owgPipelineSort',[['priority','Priority (default)'],['impact','Modeled hours: highest first'],['ease','Ease: highest first'],['recent','Recently updated'],['title','Title A–Z']]);
    const reset=button('Reset view','secondary');reset.id='owgPipelineReset';
    tools.append(field('Show',filter),field('Search ideas',search),field('Priority',quadrant),field('Sort',sort),reset,exportLink);
    exportLink.title='Exports every idea in the selected state; search and priority filters apply only to the table.';
    const status=el('div',null,'muted');status.id='owgPipelineStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const counts=el('div',null,'muted');counts.id='owgPipelineCounts';
    const list=el('div');list.id='owgPipelineList';
    const viewCount=el('div',null,'muted');viewCount.id='owgPipelineViewCount';viewCount.setAttribute('role','status');
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
    card.append(row,resumeSection,auditSection,tools,status,counts,viewCount,list,detail);
    panel.appendChild(card);

    let latestIdeas=[],refreshGeneration=0,resumeGeneration=0,auditGeneration=0,auditPageIndex=0,auditAbort=null;
    const auditPages=new Map();let auditBasis=null,triageGeneration=0;
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
        ['Opportunity','Reviewed page','Outstanding checks','Human review'].forEach(name=>
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
          cell.appendChild(action);tr.appendChild(cell);body.appendChild(tr);
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
        const rows=combined.pages.flatMap(page=>page.items.map(item=>({
          idea_id:item.idea_id,project_id:item.project_id||null,
          reviewed_page:page.page_number,
          outstanding_checks:item.status==='unavailable'?['evidence_unavailable']:
            item.status==='no_export'?['no_recorded_project']:[...item.blockers]
        })));
        const record={format:'openworkgraph.pilot-handoff-worklist.v1',
          scope:'local_human_review_only',generated_at:combined.generated_at,
          eligible_count:combined.eligible_count,reviewed_count:combined.reviewed_count,
          unreviewed_count:combined.unreviewed_count,
          reviewed_page_numbers:combined.reviewed_page_numbers,
          unreviewed_page_numbers:combined.unreviewed_page_numbers,
          complete_coverage:combined.complete_coverage,
          source_read_authorized:false,execution_authorized:false,
          verified_business_outcomes:false,rows};
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
      auditAbort=null;auditPages.clear();auditBasis=null;auditPageIndex=0;
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
    function render(){
      const ideas=portfolioView(latestIdeas,{query:search.value,quadrant:quadrant.value,sort:sort.value});
      viewCount.textContent=ideas.length+' shown of '+latestIdeas.length+' in selected state. CSV includes the entire selected state.';
      const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('tr');
      ['Idea','State','Ease','Modeled hours / month','Priority',''].forEach(h=>head.appendChild(el('th',h)));
      const thead=el('thead');thead.appendChild(head);const body=el('tbody');
      ideas.forEach(idea=>{
        const tr=el('tr');const open=button('Open','secondary');open.setAttribute('aria-label','Open idea '+idea.title);open.onclick=()=>show(idea.idea_id).catch(e=>{status.textContent=e.message;});
        [idea.title,STATE_LABELS[idea.state],idea.score?idea.score.ease_score:'—',idea.score?idea.score.modeled_hours_per_month:'—',idea.score?QUADRANTS[idea.score.quadrant]:'Not assessed'].forEach(v=>tr.appendChild(el('td',v)));
        const cell=el('td');cell.appendChild(open);tr.appendChild(cell);body.appendChild(tr);
      });
      t.append(thead,body);wrap.appendChild(t);list.replaceChildren(ideas.length?wrap:el('div','No ideas in this view.','muted'));
    }

    async function refresh(){
      clearCumulative('Portfolio review cleared; explicitly check again after the list refresh.');
      const generation=++refreshGeneration;
      const q=filter.value?'?state='+encodeURIComponent(filter.value):'';
      const r=await (await api('/v1/automation-pipeline'+q)).json();
      if(generation!==refreshGeneration)return;
      counts.textContent=Object.entries(r.counts).filter(([,n])=>n).map(([s,n])=>STATE_LABELS[s]+': '+n).join(' · ')||'No ideas yet.';
      latestIdeas=r.ideas;syncAuditPages();updateCumulative();render();
    }

    let detailGeneration=0;
    async function show(ideaId,resume=null){
      if(!resume)resumeGeneration++;
      const generation=++detailGeneration;
      const idea=await (await api('/v1/automation-pipeline/'+encodeURIComponent(ideaId))).json();
      if(generation!==detailGeneration)return;
      const box=el('div');box.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin-top:8px';
      box.appendChild(el('h3',idea.title));
      if(idea.description)box.appendChild(el('div',idea.description));
      box.appendChild(el('div','State: '+STATE_LABELS[idea.state]+' · revision '+idea.revision+(idea.source.reference?' · from '+idea.source.reference:''),'muted'));
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
            p.reported_specifications.forEach(r=>linked.appendChild(el('li',r.deployment_id+' · '+(labels[r.status]||'Unknown report status')+(r.reported_run_count===null?'':' · '+r.reported_run_count+' reported runs')+'. Artifact execution and individual run/case attribution are unverified.')));
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
            downloadPacket.onclick=()=>{
              if(generation!==detailGeneration||choice.value!==p.project_id||!journeyHost.contains(downloadPacket))return;
              const current=handoffReviewPacket(data,p.project_id,idea.idea_id,new Date().toISOString());
              const blob=new Blob([JSON.stringify(current,null,2)+'\n'],{type:'application/json'});
              const url=URL.createObjectURL(blob),link=el('a');
              link.href=url;link.download='owg-evidence-handoff-review.json';
              document.body.appendChild(link);link.click();link.remove();
              setTimeout(()=>URL.revokeObjectURL(url),1000);
              guideStatus.textContent='Downloaded the selected project review packet. Outstanding checks remain; no approval or execution permission was granted.';
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
        projects.append(download,message,exports);
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
            const item=el('li',(source?source.name:r.deployment_id)+' · '+(labels[r.status]||'Unknown status'));
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
            const sourceRef=input('owgPipelineSourceRecord','text','owg:r:…');sourceRef.maxLength=38;sourceRef.autocomplete='off';sourceRef.spellcheck=false;
            const confirmed=input('owgPipelineSourceConsent','checkbox');const consentText=el('label');consentText.append(confirmed,document.createTextNode(' Read this exact observed record and download the minimized source-check document. Existing provider opt-ins still apply.'));
            const run=button('Read source and download check','secondary');run.id='owgPipelineSourceExport';run.disabled=true;
            const note=el('div',null,'muted');note.setAttribute('role','status');const history=el('div');
            let entries=[],formVersion=0,optionsVersion=0,pending=false;
            const ready=()=>{run.disabled=pending||!entries.length||!confirmed.checked||!/^owg:r:[0-9a-f]{32}$/.test(sourceRef.value.trim());confirmed.disabled=pending||!entries.length;};
            const invalidate=()=>{formVersion++;confirmed.checked=false;ready();};
            sourceRef.oninput=invalidate;expectation.onchange=invalidate;confirmed.onchange=ready;
            const base=()=>'/v1/automation-pipeline/'+encodeURIComponent(idea.idea_id)+'/projects/'+encodeURIComponent(selected.value);
            const loadOptions=async()=>{
              const version=++optionsVersion;invalidate();entries=[];expectation.replaceChildren();ready();
              if(!selected.value){note.textContent='Export a linked project before checking source state.';return;}
              const path=base();
              const results=await Promise.allSettled([api(path+'/source-check-options').then(r=>r.json()),api(path+'/source-check-exports').then(r=>r.json())]);
              if(generation!==detailGeneration||view!==sourceView||version!==optionsVersion)return;
              const options=results[0],records=results[1];
              if(options.status==='fulfilled'&&options.value.current_idea_revision===idea.revision){
                entries=options.value.expectations;
                entries.forEach((e,i)=>{const opt=el('option',e.activity_label+' · '+e.operation.replaceAll('_',' '));opt.value=String(i);expectation.appendChild(opt);});
                note.textContent=entries.length?'Select the exact observed record. Source state does not prove agent causality, run/case identity or customer outcomes.':'This saved process has no confirmed source-state expectation. Review one in a new saved revision before creating its idea/project.';
              }else note.textContent=options.status==='rejected'?options.reason.message:'The idea changed; reopen it before checking a source.';
              if(records.status==='fulfilled'){
                const list=el('ul');records.value.exports.forEach(r=>list.appendChild(el('li','Check document exported '+r.created_at+' · hash '+r.document_sha256.slice(0,12)+'…')));
                history.replaceChildren(el('div','Only export hashes and times are retained here; receipt bodies and observed-record references are not stored.','muted'),records.value.exports.length?list:el('div','No source-check exports recorded.','muted'));
              }
              ready();
            };
            selected.onchange=()=>{sourceRef.value='';loadOptions().catch(e=>{if(generation===detailGeneration)note.textContent=e.message;});};
            run.onclick=async()=>{
              if(run.disabled)return;
              const entry=entries[Number(expectation.value)],path=base(),version=formVersion;
              const payload={expected_revision:idea.revision,terminal_activity_id:entry.terminal_activity_id,expected_operation:entry.operation,
                resource_ref:sourceRef.value.trim(),source_read_and_export_confirmed:confirmed.checked};
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
              field('Exported project for source check',selected),field('Confirmed source-state expectation',expectation),field('Observed record reference (owg:r:…)',sourceRef),consentText,run,note,history);
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
      detail.replaceChildren(box);
      if(resume&&resume.valid()&&generation===detailGeneration)
        await journeyButton.onclick();
    }

    window.addEventListener('owg-pipeline-open',event=>{
      const id=event.detail?.idea_id;if(typeof id!=='string')return;
      refresh().then(()=>show(id)).catch(e=>{status.textContent=e.message;});
    });

    add.onclick=async()=>{try{
      if(!title.value.trim())throw new Error('Name the idea.');
      const idea=await post('/v1/automation-pipeline',{title:title.value.trim(),description:description.value.trim(),source:{kind:source.value,reference:reference.value.trim()}});
      title.value='';description.value='';reference.value='';await refresh();await show(idea.idea_id);status.textContent='Idea added.';
    }catch(e){status.textContent=e.message;}};
    search.oninput=()=>{clearCumulative('Search changed; review pages again.');render();};
    quadrant.onchange=()=>{clearCumulative('Priority filter changed; review pages again.');render();};
    sort.onchange=()=>{clearCumulative('Sort changed; review pages again.');render();};
    reset.onclick=()=>{search.value='';quadrant.value='';sort.value='priority';filter.value='';refresh().catch(e=>{status.textContent=e.message;});};
    filter.onchange=()=>refresh().catch(e=>{status.textContent=e.message;});
    exportLink.onclick=async()=>{try{
      const blob=await (await api('/v1/automation-pipeline/export.csv'+(filter.value?'?state='+encodeURIComponent(filter.value):''))).blob();
      const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='automation-pipeline.csv';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){status.textContent=e.message;}};
    refresh().catch(e=>{status.textContent=e.message;});
  }

  window.__owgPipelineModel={assessmentBody,portfolioView,handoffReviewPacket,handoffDependencyReview,compareHandoffReviewPackets,compareRecordedProjects,compareProjectHandoffReadiness,portfolioHandoffRow,combinedPortfolioHandoffReview,portfolioReviewTriage,handoffInspectionWorklist,handoffInspectionSnapshotDigest,restoreHandoffInspectionKeys};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
