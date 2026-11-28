/* Explicit forecast review against the existing quality-benefit report. */
export function mountImplementationForecast(host,{getBasis,getBusy,post,lock,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const pick=el('select'),file=el('input'),note=el('textarea');file.type='file';file.accept='.json,application/json';note.rows=3;note.maxLength=2000;
  const keys=[['workload','Workload scope and monthly volume'],['eligible_share','Eligibility definition and population'],['net_human_effort','Net human effort including review and rework'],['incremental_nonlabor_cost','Incremental nonlabor cost scope'],['capacity_valuation','Human capacity valuation basis'],['implementation_cost','One-time implementation cost scope']];
  const checks=Object.fromEntries(keys.map(([k])=>{const n=el('input');n.type='checkbox';return [k,n];}));
  const run=button('Compare forecast with retained quality-benefit scenario'),save=button('Download forecast review');
  const status=el('p','Reconcile an implemented candidate with the saved portfolio first.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');let snapshot=null,result=null,epoch=0;
  const hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
  function clear(){epoch++;result=null;output.replaceChildren();}
  function invalidate(){clear();snapshot=null;file.value='';note.value='';for(const n of Object.values(checks))n.checked=false;status.textContent='Refresh the saved portfolio before comparing outcomes.';refresh();}
  function refresh(){
    const b=getBasis(),busy=getBusy(),pin=b?.report.reconciliation_sha256??null;
    if(snapshot!==pin||!pick.options.length){clear();snapshot=pin;pick.replaceChildren();file.value='';note.value='';for(const n of Object.values(checks))n.checked=false;
      const blank=el('option','Select an implemented matched candidate');blank.value='';pick.append(blank);
      if(b)for(const row of b.report.items){if(row.mapping_status==='matched'&&row.scenario_assessed&&row.saved_idea?.state==='implemented'){
        const o=el('option','Item '+(row.item_index+1)+' · '+row.saved_idea.idea_id+' · revision '+row.saved_idea.revision);o.value=String(row.item_index);pick.append(o);
      }}
    }
    for(const n of [pick,file,note,...Object.values(checks)])n.disabled=busy||!b;
    run.disabled=busy||!b||!pick.value||!file.files?.[0]||!note.value.trim();save.disabled=busy||!result;
  }
  pick.addEventListener('change',()=>{file.value='';note.value='';for(const n of Object.values(checks))n.checked=false;clear();status.textContent='Candidate changed. Select its exact quality-benefit export.';refresh();});
  for(const n of [file,note,...Object.values(checks)])n.addEventListener('input',()=>{clear();status.textContent='Inputs changed. Recompute the forecast review.';refresh();});
  run.onclick=async()=>{
    const b=getBasis(),f=file.files?.[0];if(run.disabled||!b||!f)return;
    clear();const ticket=epoch,index=Number(pick.value),row=b.report.items[index];lock(true);status.textContent='Recomputing the existing retained quality-benefit result.';
    try{
      if(!f.size||f.size>256*1024)throw Error('Choose a quality-benefit export of at most 256 KiB.');
      const raw=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer()),doc=JSON.parse(raw);
      if(doc.format!=='openworkgraph.quality-adjusted-benefit-scenario.v1'||doc.idea_id!==row.saved_idea.idea_id||doc.idea_revision!==row.saved_idea.revision)throw Error('Quality export must match the selected saved idea revision.');
      const comparability={...Object.fromEntries(keys.map(([k])=>[k,checks[k].checked])),basis_note:note.value};
      const r=await post('/v1/ai-opportunity-handoff/planning-forecast-review',{...b.request,reconciliation_sha256:b.report.reconciliation_sha256,item_index:index,quality_report_json:raw,comparability});
      if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-forecast-result.v1'||r.portfolio_written!==false||r.execution_authorized!==false||!hex(r.forecast_review_sha256)||typeof r.report_json!=='string')throw Error('Unexpected forecast result.');
      const q=JSON.parse(r.report_json),names=['monthly_case_volume','eligible_share','monthly_net_human_hours','monthly_incremental_nonlabor_cost','capacity_value_per_hour','one_time_implementation_cost','monthly_conditional_net_value'];
      if(q.format!=='openworkgraph.ai-implementation-forecast-review.v1'||q.forecast_review_sha256!==r.forecast_review_sha256||q.reconciliation_sha256!==b.report.reconciliation_sha256||q.planning_review_sha256!==b.report.planning_review_sha256||q.idea_id!==row.saved_idea.idea_id||q.idea_revision!==row.saved_idea.revision||q.proposal_sha256!==row.proposal_sha256||q.item_index!==index||q.quality_scenario_sha256!==doc.scenario_sha256||q.measurement_snapshot_sha256!==doc.snapshot_sha256||q.forecast_currency!==b.report.currency||q.measurement_scenario_currency!==doc.currency||['forecast_recorded_before_implementation_verified','comparability_independently_verified','portfolio_written','measurement_sources_independently_verified','causal_effect_verified','savings_claimed','financial_return_verified','business_outcomes_verified','execution_authorized'].some(k=>q[k]!==false)||!Array.isArray(q.comparisons)||q.comparisons.length!==names.length||q.comparisons.some((m,i)=>m.metric!==names[i])||!Array.isArray(q.review_gates)||!Array.isArray(q.selected_measurement_ids)||!Array.isArray(q.excluded_measurement_ids))throw Error('Unexpected forecast lineage or claims.');
      output.append(el('h4','Forecast versus reported values'),el('p','Forecast currency: '+q.forecast_currency+'; retained scenario currency: '+q.measurement_scenario_currency+'.'));
      for(const m of q.comparisons){const section=el('section');section.append(el('h4',m.metric.replaceAll('_',' ')),el('p','Forecast: '+(m.forecast_range?m.forecast_range.low+' to '+m.forecast_range.high:'Unknown')+'; reported: '+(m.reported_value??'Unknown')+'; '+m.status.replaceAll('_',' ')+'.'),el('p',m.basis));output.append(section);}
      output.append(el('h4','Quality and adoption gates'),el('p',q.all_declared_gates_met?'All declared gates met; not proof of quality.':'Not all declared gates met.'),el('p','Whole-population quality threshold: '+q.whole_population_quality_threshold_status+'.'));
      for(const g of q.review_gates)output.append(el('p',g.gate+' — '+g.status));
      output.append(el('p',q.selected_measurement_ids.length+' selected and '+q.excluded_measurement_ids.length+' excluded measurement periods. '+q.interpretation));
      result=r;status.textContent='Forecast review complete. Review SHA-256: '+r.forecast_review_sha256+'.';
    }catch(e){if(ticket===epoch){output.replaceChildren();status.textContent=e.message;}}finally{lock(false);refresh();}
  };
  save.onclick=()=>{if(result&&!getBusy())download('owg-forecast-review.json','application/json',result.report_json);};
  const details=el('details');details.id='owgImplementationForecast';details.append(el('summary','Compare implementation forecast with retained results'),el('p','For an implemented idea, first download Scenario JSON from its existing quality and adoption panel. Select the matching original export here. Check only definitions and scope you have independently reviewed as comparable; unchecked comparisons stay explicitly unresolved. No verified financial or business-outcome claim.'),field('Implemented candidate',pick),field('Original quality-benefit scenario JSON',file),...keys.map(([k,label])=>field('Comparable: '+label,checks[k])),field('Comparability basis and uncertainty',note),run,status,output,save);host.append(details);refresh();return {refresh,invalidate};
}
