/* Campaign-specific view of the existing portfolio benefit readiness report. */
const FORMAT='openworkgraph.automation-portfolio-benefit-readiness.v1';
const ID=/^idea_[a-f0-9]{16}$/;
const STATUSES=['unreadable_evidence','missing_baseline','missing_after','overlapping_periods','zero_case_period','cross_idea_overlap_candidate','missing_source_reference','ready_for_noncausal_comparison','invalid_periods'];
const FLAGS=['reported_source_identity_verified','same_cases_verified','causal_effect_verified','realized_savings_verified','portfolio_savings_summed','execution_authorized','source_reference_values_returned'];
function require(ok,msg){if(!ok)throw Error(msg);}
const count=n=>Number.isSafeInteger(n)&&n>=0;
export function campaignOutcomes(previous,current,readiness,readAt){
  require(previous?.format==='openworkgraph.ai-implementation-campaign-review.v1'&&current?.format===previous.format&&JSON.stringify(previous.campaign)===JSON.stringify(current.campaign)&&JSON.stringify(previous.items)===JSON.stringify(current.items),'Campaign membership, saved revision or dependencies changed. Recompare the campaign first.');
  require(typeof readAt==='string'&&Number.isFinite(Date.parse(readAt)),'Invalid readiness read time.');
  require(readiness?.format===FORMAT&&Array.isArray(readiness.ideas)&&readiness.ideas.length<=1000&&readiness.implemented_ideas_reviewed===readiness.ideas.length&&FLAGS.every(k=>readiness[k]===false),'Unexpected portfolio readiness response.');
  const scan={};for(const k of ['candidate_overlap_pairs_truncated','rows_truncated','scan_complete','comparison_complete']){require(typeof readiness[k]==='boolean','Invalid scan status.');scan[k]=readiness[k];}
  for(const k of ['candidate_overlap_pairs','measurements_scanned','unreadable_measurements_in_overlap_scan']){require(count(readiness[k]),'Invalid scan count.');scan[k]=readiness[k];}
  const index=new Map();for(const r of readiness.ideas){
    require(typeof r.idea_id==='string'&&ID.test(r.idea_id)&&!index.has(r.idea_id)&&r.implemented_state==='implemented'&&STATUSES.includes(r.readiness)&&r.causal_effect_verified===false&&r.reported_change_is_savings===false,'Unexpected measurement readiness row.');
    require(['baseline_periods','after_periods','unreadable_measurements','missing_source_references'].every(k=>count(r[k]))&&typeof r.cross_idea_overlap_candidate==='boolean'&&typeof r.next_review_action==='string'&&r.next_review_action.length<=500,'Invalid readiness details.');
    const v=r.reported_effort_change_hours_per_month_at_after_volume;require(v===null||(typeof v==='number'&&Number.isFinite(v)&&r.readiness==='ready_for_noncausal_comparison'),'Invalid reported effort change.');
    index.set(r.idea_id,r);
  }
  const counts={};const items=current.items.map(item=>{
    const id=item.baseline.idea_id,live=item.current,r=index.get(id);let status;
    if(!live)status='missing_idea';else if(item.status==='revision_conflict')status='revision_conflict';else if(live.state!=='implemented')status='not_recorded_implemented';else if(!r||!scan.scan_complete&&r.readiness==='ready_for_noncausal_comparison')status='readiness_unresolved';else status=r.readiness;
    // A row for a currently nonimplemented member could indicate a race.
    if(r&&(!live||live.state!=='implemented'))throw Error('Portfolio changed during readiness read. Recompare and reload.');
    counts[status]=(counts[status]??0)+1;
    const detail=r?{baseline_periods:r.baseline_periods,after_periods:r.after_periods,unreadable_measurements:r.unreadable_measurements,missing_source_references:r.missing_source_references,cross_idea_overlap_candidate:r.cross_idea_overlap_candidate,readiness:r.readiness,next_review_action:r.next_review_action,reported_effort_change_hours_per_month_at_after_volume:status==='ready_for_noncausal_comparison'?r.reported_effort_change_hours_per_month_at_after_volume:null}:null;
    return {idea_id:id,title:live?.title??item.baseline.title,current_revision:live?.revision??null,current_state:live?.state??null,status,measurement_readiness:detail,quality_verified:false,goal_achievement_verified:false};
  });
  return {format:'openworkgraph.ai-implementation-campaign-outcomes.v1',campaign:current.campaign,campaign_reviewed_at:previous.reviewed_at,readiness_read_at:readAt,portfolio_rechecked_at:current.reviewed_at,counts,items,portfolio_scan:scan,atomic_snapshot_verified:false,source_identity_verified:false,quality_verified:false,causal_effect_verified:false,goal_achievement_verified:false,portfolio_savings_summed:false,execution_authorized:false,interpretation:'Existing measurement readiness, not a quality or outcome certification. Member revisions and dependencies were rechecked after a separate portfolio readiness read; measurements may have changed, and no atomic snapshot is established. Global scan limitations and possible cross-idea overlap are retained. Individual effort changes are noncausal reported comparisons, not savings. No totals are calculated.'};
}
export function mountCampaignOutcomes(host,{getReport,loadReadiness,recheck,download}){
  const el=(tag,copy)=>{const n=document.createElement(tag);if(copy!==undefined)n.textContent=copy;return n;};
  const button=copy=>{const n=el('button',copy);n.type='button';return n;};
  const run=button('Review campaign measurement readiness'),save=button('Download campaign outcome readiness');
  const status=el('p','Compare the campaign with current portfolio first.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const output=el('div');let report=null,busy=false,epoch=0;
  function refresh(){run.disabled=busy||!getReport();save.disabled=busy||!report;}
  function invalidate(){epoch++;report=null;output.replaceChildren();status.textContent='Review current campaign membership before checking measurement readiness.';refresh();}
  run.onclick=async()=>{if(run.disabled)return;invalidate();const basis=getReport(),ticket=epoch;busy=true;refresh();status.textContent='Reading existing measurement readiness and rechecking saved member revisions.';
    try{const raw=await loadReadiness(),readAt=new Date().toISOString();const current=await recheck(basis.campaign);if(ticket!==epoch||getReport()!==basis)return;report=campaignOutcomes(basis,current,raw,readAt);
      output.append(el('p',Object.entries(report.counts).map(([k,n])=>n+' '+k.replaceAll('_',' ')).join(' · ')),el('p',report.portfolio_scan.scan_complete?'Portfolio readiness scan complete within its declared bounds.':'Portfolio readiness scan incomplete; comparisons may be suppressed.'));
      for(const row of report.items){const block=el('section');block.append(el('h5',row.title),el('p',row.status.replaceAll('_',' ')+' · '+row.idea_id));const r=row.measurement_readiness;if(r){block.append(el('p',r.baseline_periods+' baseline and '+r.after_periods+' after periods; '+r.missing_source_references+' missing source references; '+r.unreadable_measurements+' unreadable.'),el('p',r.next_review_action));if(r.cross_idea_overlap_candidate)block.append(el('p','Possible overlap with another portfolio idea.'));if(r.reported_effort_change_hours_per_month_at_after_volume!==null)block.append(el('p','Reported effort change at after volume: '+r.reported_effort_change_hours_per_month_at_after_volume+' hours/month (noncausal, not savings).'));}output.append(block);}
      output.append(el('p',report.interpretation));status.textContent='Campaign outcome readiness reviewed.';
    }catch(e){if(ticket===epoch){report=null;output.replaceChildren();status.textContent=e.message;}}finally{busy=false;refresh();}};
  save.onclick=()=>{if(!save.disabled)download('owg-campaign-outcome-readiness.json',JSON.stringify(report,null,2));};
  const details=el('details');details.id='owgCampaignOutcomes';details.append(el('summary','Leadership measurement readiness'),el('p','Inspect retained before/after coverage for campaign members and possible overlap across the portfolio. Quality and success criteria still require separate review.'),run,status,output,save);host.append(details);refresh();return {refresh,invalidate};
}
