/* Explicit scenario assumptions and separately confirmed single-idea planning handoff. */
export function mountImplementationPlanning(host,{getBasis,getBusy,post,lock,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const choice=values=>{const n=el('select');for(const v of values){const o=el('option',v.replaceAll('_',' '));o.value=v;n.append(o);}return n;};
  const keys=[['cases_per_month','Cases per month'],['eligible_share','Eligible share (0–1)'],['net_human_minutes_saved_per_eligible_case','Net human minutes saved per eligible case'],['human_capacity_value_per_hour','Human capacity value per hour'],['incremental_operating_cost_per_month','Incremental operating cost per month'],['one_time_implementation_cost','One-time implementation cost']];
  const metrics=[['modeled_net_human_hours_per_month','Modeled net human hours per month'],['modeled_capacity_value_per_month','Modeled monthly capacity value'],['modeled_capacity_value_less_operating_cost_per_month','Monthly capacity value less operating cost'],['modeled_first_year_capacity_value_less_incremental_costs','First-year capacity value less incremental costs']];
  const currency=el('input');currency.maxLength=3;currency.placeholder='SEK';
  const prepare=button('Prepare planning worklist'),saveTemplate=button('Download blank planning worklist'),compare=button('Compare reviewed scenarios'),savePlan=button('Download reviewed assumptions'),saveReport=button('Download planning review');
  const confirm=el('input');confirm.type='checkbox';
  const status=el('p','Review a complete proposal batch before planning.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const editor=el('div'),output=el('div'),portfolioOutput=el('div');let template=null,result=null,rows=[],epoch=0,portfolioResult=null,handoffPanel=null;
  const reconcilePortfolio=button('Reconcile with saved portfolio'),savePortfolio=button('Download portfolio reconciliation');
  const portfolioStatus=el('p','Compare reviewed scenarios first.');portfolioStatus.setAttribute('role','status');portfolioStatus.setAttribute('aria-live','polite');
  function clearPortfolio(){portfolioResult=null;portfolioOutput.replaceChildren();portfolioStatus.textContent='Refresh against the saved portfolio after scenario comparison.';handoffPanel?.invalidate();}
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  function refresh(){
    const busy=getBusy(),b=getBasis();currency.disabled=busy;prepare.disabled=busy||!b||! /^[A-Z]{3}$/.test(currency.value.trim());
    saveTemplate.disabled=busy||!template;confirm.disabled=busy||!template;compare.disabled=busy||!template||!confirm.checked;
    savePlan.disabled=saveReport.disabled=busy||!result;reconcilePortfolio.disabled=busy||!result;savePortfolio.disabled=busy||!portfolioResult;
    for(const row of rows){row.enabled.disabled=busy;for(const n of row.controls)n.disabled=busy||!row.enabled.checked;}
    handoffPanel?.refresh();
  }
  function invalidate(){epoch++;template=result=null;clearPortfolio();rows=[];confirm.checked=false;editor.replaceChildren();output.replaceChildren();status.textContent='Prepare a planning worklist from the current reviewed batch.';refresh();}
  function edit(){epoch++;result=null;clearPortfolio();confirm.checked=false;output.replaceChildren();status.textContent='Assumptions changed. Review and confirm again.';refresh();}
  currency.addEventListener('input',invalidate);confirm.addEventListener('change',refresh);
  function render(work,b){
    const original=JSON.parse(b.request.proposals_json);
    for(const item of work.candidates){
      const section=el('fieldset');section.append(el('legend','Item '+(item.item_index+1)+' — '+String(original[item.item_index]?.title??'').slice(0,240)));
      const enabled=el('input');enabled.type='checkbox';section.append(field('Assess item '+(item.item_index+1),enabled));
      const ranges={},controls=[];
      for(const [key,label] of keys){
        const lo=el('input'),hi=el('input');for(const n of [lo,hi]){n.maxLength=20;n.inputMode='decimal';n.placeholder='Unknown';controls.push(n);}
        ranges[key]=[lo,hi];section.append(field(label+' — low (item '+(item.item_index+1)+')',lo),field(label+' — high (item '+(item.item_index+1)+')',hi));
      }
      const feasibility=choice(['unknown','plausible','needs_review','blocked']),risk=choice(['unknown','low','medium','high']),deps=el('input'),note=el('textarea');note.maxLength=2000;note.rows=3;deps.maxLength=100;
      controls.push(feasibility,risk,deps,note);section.append(field('Feasibility (item '+(item.item_index+1)+')',feasibility),field('Risk (item '+(item.item_index+1)+')',risk),field('Dependency item numbers, comma separated (item '+(item.item_index+1)+')',deps),field('Reviewed basis and uncertainties (item '+(item.item_index+1)+')',note));
      for(const n of [enabled,...controls])n.addEventListener('input',edit);
      rows.push({item,enabled,ranges,controls,feasibility,risk,deps,note});editor.append(section);
    }
  }
  function assumptions(row){
    if(!row.enabled.checked)return null;
    const ranges={};for(const [k,pair] of Object.entries(row.ranges)){const v=pair.map(n=>n.value.trim());if(v.every(x=>!x))ranges[k]=null;else{if(v.some(x=>!x))throw Error('Enter both bounds or leave both blank for unknown.');ranges[k]=v;}}
    const text=row.deps.value.trim();let deps=[];if(text){const tokens=text.split(',').map(s=>s.trim());if(tokens.some(s=>! /^(?:[1-9]|1\d|20)$/.test(s)))throw Error('Use displayed positive item numbers for dependencies.');deps=tokens.map(s=>Number(s)-1);}
    return {ranges,feasibility:row.feasibility.value,risk:row.risk.value,depends_on_item_indexes:deps,basis_note:row.note.value};
  }
  prepare.onclick=async()=>{
    const b=getBasis();if(prepare.disabled||!b)return;invalidate();const ticket=epoch,code=currency.value.trim();lock(true);
    try{
      const request={...b.request,batch_review_sha256:b.report.batch_review_sha256};
      const r=await post('/v1/ai-opportunity-handoff/planning-worklist',{...request,currency:code});if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-planning-template.v1'||r.portfolio_written!==false||r.execution_authorized!==false||typeof r.worklist_json!=='string')throw Error('Unexpected planning worklist response.');
      const w=JSON.parse(r.worklist_json),expected=b.report.items.filter(x=>x.status==='structurally_reviewed');
      if(w.format!=='openworkgraph.ai-implementation-planning-worklist.v1'||w.packet_sha256!==b.report.packet_sha256||w.batch_review_sha256!==b.report.batch_review_sha256||w.currency!==code||w.confirm_human_reviewed_assumptions!==false||!Array.isArray(w.candidates)||w.candidates.length!==expected.length||w.candidates.some((x,i)=>x.item_index!==expected[i].item_index||x.proposal_sha256!==expected[i].proposal_sha256||x.assessment!==null))throw Error('Unexpected planning candidate population.');
      template={request,work:w,raw:r.worklist_json,b};render(w,b);status.textContent='Planning worklist ready: '+w.candidates.length+' distinct reviewed candidates. Leave unassessed candidates or unknown bounds blank.';
    }catch(e){if(ticket===epoch){template=null;rows=[];editor.replaceChildren();status.textContent=e.message;}}finally{lock(false);refresh();}
  };
  compare.onclick=async()=>{
    if(compare.disabled||!template)return;const t=template,ticket=++epoch;result=null;clearPortfolio();output.replaceChildren();confirm.checked=false;lock(true);
    try{
      const plan={...t.work,confirm_human_reviewed_assumptions:true,candidates:rows.map(row=>({...row.item,assessment:assumptions(row)}))};
      const r=await post('/v1/ai-opportunity-handoff/planning-compare',{...t.request,plan_json:JSON.stringify(plan)});if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-planning-result.v1'||r.execution_authorized!==false||r.portfolio_written!==false||!hex(r.planning_review_sha256)||typeof r.report_json!=='string'||typeof r.plan_json!=='string')throw Error('Unexpected planning report.');
      const q=JSON.parse(r.report_json),p=JSON.parse(r.plan_json),expected=t.b.report.items;
      if(q.format!=='openworkgraph.ai-implementation-planning-review.v1'||q.packet_sha256!==plan.packet_sha256||q.batch_review_sha256!==plan.batch_review_sha256||q.currency!==plan.currency||q.planning_review_sha256!==r.planning_review_sha256||!hex(q.planning_input_sha256)||q.portfolio_total!==null||['observed_workload_verified','actual_cost_verified','realized_savings_verified','representative_coverage_verified','feasibility_verified','opportunity_ranking_performed','portfolio_written','execution_authorized'].some(k=>q[k]!==false)||!Array.isArray(q.candidates)||q.candidates.length!==expected.length||q.item_count!==expected.length||q.distinct_reviewed_count!==plan.candidates.length||q.assessed_count!==plan.candidates.filter(x=>x.assessment!==null).length||!Array.isArray(q.shared_scoped_citations)||!Array.isArray(q.coverage_gaps)||!Array.isArray(q.uncited_run_refs)||p.batch_review_sha256!==plan.batch_review_sha256||p.confirm_human_reviewed_assumptions!==true||p.currency!==plan.currency)throw Error('Unexpected planning population or claims.');
      const fmt=v=>v===null?'Unknown':v.low+' to '+v.high;
      for(const [i,row] of q.candidates.entries()){
        if(row.item_index!==expected[i].item_index||row.proposal_sha256!==expected[i].proposal_sha256||row.structural_status!==expected[i].status)throw Error('Planning item mismatch.');
        const section=el('section');section.append(el('h4','Item '+(row.item_index+1)+' — '+row.structural_status.replaceAll('_',' ')));
        if(row.assessment){const list=el('ul');for(const [k,label] of metrics)list.append(el('li',label+': '+fmt(row.assessment[k])+(k.includes('hours')?' hours':' '+q.currency)));section.append(list,el('p','Declared feasibility: '+row.assessment.declared_assessment.feasibility+'; risk: '+row.assessment.declared_assessment.risk+'.'),el('p','Dependencies without plausible assessment: '+(row.dependencies_without_plausible_assessment.map(x=>x+1).join(', ')||'none declared')+'.'));}
        else section.append(el('p','No assessment.'));output.append(section);
      }
      for(const x of q.shared_scoped_citations)output.append(el('p','Items '+(x.left_item_index+1)+' and '+(x.right_item_index+1)+' share '+x.shared_scoped_citation_count+' scoped citations; potential overlap.'));
      output.append(el('p',q.portfolio_total_reason),el('p',q.interpretation));result=r;status.textContent='Planning comparison complete: '+q.assessed_count+' assessed of '+q.distinct_reviewed_count+' distinct candidates; '+q.item_count+' returned items retained. Review SHA-256: '+q.planning_review_sha256+'.';
    }catch(e){if(ticket===epoch){output.replaceChildren();status.textContent=e.message;}}finally{lock(false);refresh();}
  };
  reconcilePortfolio.onclick=async()=>{
    if(reconcilePortfolio.disabled||!result||!template)return;
    const t=template,selected=result,ticket=++epoch;clearPortfolio();lock(true);
    try{
      const r=await post('/v1/ai-opportunity-handoff/planning-portfolio',{...t.request,plan_json:selected.plan_json,planning_review_sha256:selected.planning_review_sha256});
      if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-portfolio-result.v1'||r.portfolio_written!==false||r.execution_authorized!==false||!hex(r.reconciliation_sha256)||typeof r.report_json!=='string')throw Error('Unexpected portfolio reconciliation response.');
      const q=JSON.parse(r.report_json),expected=t.b.report.items;
      if(q.format!=='openworkgraph.ai-implementation-portfolio-link.v1'||q.packet_sha256!==t.work.packet_sha256||q.batch_review_sha256!==t.work.batch_review_sha256||q.planning_review_sha256!==selected.planning_review_sha256||q.currency!==t.work.currency||q.reconciliation_sha256!==r.reconciliation_sha256||!hex(q.portfolio_lineage_snapshot_sha256)||q.portfolio_written!==false||q.execution_authorized!==false||q.savings_verified!==false||!Array.isArray(q.items)||q.items.length!==expected.length||q.item_count!==expected.length||q.matched_count!==q.items.filter(x=>x.mapping_status==='matched').length)throw Error('Unexpected portfolio reconciliation population.');
      for(const [i,row] of q.items.entries()){
        if(row.item_index!==expected[i].item_index||row.proposal_sha256!==expected[i].proposal_sha256||row.structural_status!==expected[i].status)throw Error('Portfolio item mismatch.');
        const section=el('section');section.append(el('h4','Item '+(row.item_index+1)+' — '+row.mapping_status.replaceAll('_',' ')));
        if(row.saved_idea){const a=row.saved_idea,d=row.dependency_comparison;section.append(el('p',a.idea_id+' · revision '+a.revision+' · '+a.state),el('p','Saved budget: '+(a.budget?String(a.budget.amount)+' '+a.budget.currency+' ('+a.budget.basis+')':'not recorded')+'; '+row.budget_comparison.replaceAll('_',' ')+'.'));
          if(d)section.append(el('p','Missing links: '+(d.missing_saved_links.join(', ')||'none')+'; outside scenario: '+(d.saved_links_outside_scenario.join(', ')||'none')+'; unresolved items: '+(d.unresolved_candidate_indexes.map(x=>x+1).join(', ')||'none')+'.'));
        }portfolioOutput.append(section);
      }
      portfolioOutput.append(el('p',q.interpretation));portfolioResult=r;
      portfolioStatus.textContent='Portfolio reconciliation: '+q.matched_count+' matched saved ideas; '+q.item_count+' returned items retained. Snapshot only — refresh before separate decisions.';
    }catch(e){if(ticket===epoch){clearPortfolio();portfolioStatus.textContent=e.message;}}finally{lock(false);refresh();}
  };
  savePortfolio.onclick=()=>{if(portfolioResult&&!getBusy())download('owg-portfolio-reconciliation.json','application/json',portfolioResult.report_json);};
  saveTemplate.onclick=()=>{if(template&&!getBusy())download('owg-planning-worklist.json','application/json',template.raw);};
  savePlan.onclick=()=>{if(result&&!getBusy())download('owg-planning-assumptions.json','application/json',result.plan_json);};
  saveReport.onclick=()=>{if(result&&!getBusy())download('owg-planning-review.json','application/json',result.report_json);};
  const handoffHost=el('div');
  const details=el('details');details.id='owgImplementationPlanning';details.append(el('summary','Compare implementation economics and dependencies'),el('p','Enter reviewed assumptions, not measured outcomes. Net minutes include review, supervision, rework and escalation; negative is allowed. Blank pairs are unknown. All costs use the same currency. First-year values assume twelve identical operating months. No rankings or combined savings are inferred.'),field('Planning currency code',currency),prepare,saveTemplate,editor,field('I reviewed these assumptions and uncertainties',confirm),compare,status,output,savePlan,saveReport,reconcilePortfolio,portfolioStatus,portfolioOutput,savePortfolio,handoffHost);host.append(details);refresh();
  import('/ai-implementation-portfolio-handoff.js').then(module=>{
    handoffPanel=module.mountPortfolioPlanningHandoff(handoffHost,{
      getBasis:()=>portfolioResult&&result&&template?{
        request:{...template.request,plan_json:result.plan_json,planning_review_sha256:result.planning_review_sha256},
        report:JSON.parse(portfolioResult.report_json)
      }:null,getBusy,post,lock,
      onSaved:()=>{clearPortfolio();portfolioStatus.textContent='Planning saved. Reconcile again to inspect the new revision; refresh the existing portfolio view before further decisions.';refresh();}
    });
  }).catch(()=>{handoffHost.textContent='Planning handoff unavailable; existing portfolio controls remain available.';});
  return {refresh,invalidate};
}
