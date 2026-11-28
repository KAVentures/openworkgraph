/* Portable, explicit campaign membership over the existing local portfolio. */
const FORMAT='openworkgraph.ai-implementation-campaign.v1';
const STATES=['idea','assessing','approved','rejected','on_hold','implemented'];
const ID=/^idea_[a-f0-9]{16}$/;
const MAX=50,MAX_BYTES=256*1024;
function require(ok,msg){if(!ok)throw Error(msg);}
function shape(v,keys){return v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));}
function text(v,n,required=false){require(typeof v==='string'&&v.length<=n&&(!required||v.trim())&&!/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(v),'Invalid campaign text.');return v;}
function stamp(v){require(typeof v==='string'&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString()===v,'Invalid snapshot timestamp.');return v;}
function ids(v,max){require(Array.isArray(v)&&v.length<=max&&v.every(x=>typeof x==='string'&&ID.test(x))&&new Set(v).size===v.length,'Invalid or repeated idea IDs.');return v;}
function inventory(ideas){
  require(Array.isArray(ideas)&&ideas.length<=1000,'Portfolio inventory exceeds campaign bounds.');const map=new Map();
  for(const i of ideas){
    require(i&&typeof i.idea_id==='string'&&ID.test(i.idea_id)&&!map.has(i.idea_id)&&Number.isSafeInteger(i.revision)&&i.revision>=1&&STATES.includes(i.state),'Invalid saved idea identity or state.');
    const p=i.planning??{};
    const row={idea_id:i.idea_id,title:text(i.title,200,true),revision:i.revision,state:i.state,owner:text(p.owner??'',80),team:text(p.team??'',80),target_quarter:text(p.target_quarter??'',7),depends_on:ids(p.depends_on??[],20).slice().sort()};
    require(/^(|\d{4}-Q[1-4])$/.test(row.target_quarter),'Invalid target quarter.');map.set(row.idea_id,row);
  }
  return map;
}
function validate(c){
  require(shape(c,['format','scope','objective','success_criteria','baseline_at','baseline','goal_achievement_verified','execution_authorized'])&&c.format===FORMAT&&c.goal_achievement_verified===false&&c.execution_authorized===false,'Invalid campaign format or claims.');
  text(c.scope,120,true);text(c.objective,2000,true);text(c.success_criteria,2000,true);stamp(c.baseline_at);
  require(Array.isArray(c.baseline)&&c.baseline.length>=1&&c.baseline.length<=MAX,'Select one to fifty ideas.');
  const seen=new Set();for(const r of c.baseline){
    require(shape(r,['idea_id','title','revision','state','owner','team','target_quarter','depends_on'])&&ID.test(r.idea_id)&&typeof r.idea_id==='string'&&!seen.has(r.idea_id)&&Number.isSafeInteger(r.revision)&&r.revision>=1&&STATES.includes(r.state),'Invalid baseline idea.');seen.add(r.idea_id);
    text(r.title,200,true);text(r.owner,80);text(r.team,80);require(typeof r.target_quarter==='string'&&/^(|\d{4}-Q[1-4])$/.test(r.target_quarter),'Invalid target quarter.');ids(r.depends_on,20);
  }
  require(new TextEncoder().encode(JSON.stringify(c)).length<=MAX_BYTES,'Campaign exceeds local file limit.');return c;
}
export function freezeCampaign(ideas,selected,{scope,objective,success_criteria},at){
  const map=inventory(ideas);ids(selected,MAX);require(selected.length>0&&selected.every(id=>map.has(id)),'Select current saved ideas explicitly.');
  return validate({format:FORMAT,scope,objective,success_criteria,baseline_at:at,baseline:selected.map(id=>map.get(id)),goal_achievement_verified:false,execution_authorized:false});
}
export function importCampaign(raw){require(typeof raw==='string'&&new TextEncoder().encode(raw).length<=MAX_BYTES,'Campaign file exceeds 256 KiB.');return validate(JSON.parse(raw));}
export function reviewCampaign(c,ideas,at){
  validate(c);stamp(at);const map=inventory(ideas),members=new Set(c.baseline.map(r=>r.idea_id)),counts=Object.fromEntries(STATES.map(s=>[s,0]));let missing=0,changed=0,blocked=0,revisionConflicts=0;
  const rows=c.baseline.map(b=>{
    const current=map.get(b.idea_id)??null;if(!current){missing++;return {baseline:b,current:null,status:'missing',dependencies:[],changed_fields:[]};}
    counts[current.state]++;
    const fields=['title','state','owner','team','target_quarter','depends_on'].filter(k=>JSON.stringify(b[k])!==JSON.stringify(current[k]));
    const status=current.revision<b.revision||(current.revision===b.revision&&fields.length)?'revision_conflict':current.revision!==b.revision?'revision_changed':'unchanged';
    changed+=Number(status==='revision_changed');revisionConflicts+=Number(status==='revision_conflict');
    const dependencies=current.depends_on.map(id=>({idea_id:id,scope:members.has(id)?'inside_campaign':'outside_campaign',state:map.get(id)?.state??'missing',title:map.get(id)?.title??null}));
    const waiting=!['implemented','rejected'].includes(current.state)&&dependencies.some(d=>d.state!=='implemented');blocked+=Number(waiting);
    return {baseline:b,current,status,dependencies,waiting_on_dependency:waiting,changed_fields:fields};
  });
  return {format:'openworkgraph.ai-implementation-campaign-review.v1',campaign:c,reviewed_at:at,counts,missing_count:missing,changed_revision_count:changed,revision_conflict_count:revisionConflicts,waiting_idea_count:blocked,items:rows,goal_achievement_verified:false,baseline_authenticity_verified:false,portfolio_written:false,execution_authorized:false,interpretation:'Current states are recorded decisions, not deployment or verified results. Imported baseline is a local declaration. Dependencies outside the campaign are retained. Success criteria require separate measurement; no savings or combined benefit inferred.'};
}
export function mountImplementationCampaign(host,{loadIdeas,openIdea,download,loadReadiness,mountOutcomes}){
  const el=(tag,copy)=>{const n=document.createElement(tag);if(copy!==undefined)n.textContent=copy;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.append(node);return n;};
  const button=copy=>{const n=el('button',copy);n.type='button';return n;};
  const scope=el('input'),objective=el('textarea'),criteria=el('textarea'),team=el('select'),search=el('input'),file=el('input');scope.maxLength=120;objective.maxLength=criteria.maxLength=2000;objective.rows=criteria.rows=3;search.maxLength=200;file.type='file';file.accept='.json,application/json';
  const refreshButton=button('Load current portfolio for campaign'),freeze=button('Freeze selected campaign baseline'),load=button('Open saved campaign file'),save=button('Download campaign baseline'),review=button('Compare campaign with current portfolio'),saveReview=button('Download campaign review'),reset=button('Start another campaign');
  const selection=el('div'),result=el('div'),summary=el('p'),status=el('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  let ideas=null,loadedAt=null,selected=new Set(),campaign=null,report=null,busy=false,epoch=0,outcomePanel=null;
  function clearReport(){report=null;result.replaceChildren();outcomePanel?.invalidate();}
  function invalidate(){epoch++;ideas=loadedAt=null;clearReport();status.textContent='Portfolio changed or refreshed. Reload the current inventory to compare again.';render();}
  function controls(){
    refreshButton.disabled=busy;load.disabled=busy||!file.files?.[0];file.disabled=busy;reset.disabled=busy;save.disabled=busy||!campaign;saveReview.disabled=busy||!report;review.disabled=busy||!campaign||!ideas;
    freeze.disabled=busy||!!campaign||!ideas||!selected.size||!scope.value.trim()||!objective.value.trim()||!criteria.value.trim();for(const n of [scope,objective,criteria,team,search])n.disabled=busy||!!campaign||((n===team||n===search)&&!ideas);outcomePanel?.refresh();
  }
  function render(){
    selection.replaceChildren();
    if(campaign){summary.textContent=campaign.scope+' — '+campaign.baseline.length+' selected ideas; baseline '+campaign.baseline_at+'. Objective: '+campaign.objective+' Success criteria: '+campaign.success_criteria;}
    else{summary.textContent=selected.size+' of 50 ideas explicitly selected.';if(ideas){const q=search.value.trim().toLowerCase();for(const i of ideas){if(team.value&&i.team!==team.value||q&&!(i.title+' '+i.idea_id+' '+i.owner).toLowerCase().includes(q))continue;const cb=el('input');cb.type='checkbox';cb.checked=selected.has(i.idea_id);cb.disabled=busy||(!cb.checked&&selected.size>=MAX);cb.onchange=()=>{if(cb.checked)selected.add(i.idea_id);else selected.delete(i.idea_id);render();};selection.append(field(i.title+' · '+i.state+' · '+(i.team||'No team')+' · '+i.idea_id,cb));}}}
    controls();
  }
  function setTeams(){const prior=team.value;team.replaceChildren();const all=el('option','All saved team labels');all.value='';team.append(all);for(const t of [...new Set(ideas.map(i=>i.team).filter(Boolean))].sort()){const o=el('option',t);o.value=t;team.append(o);}if([...team.options].some(o=>o.value===prior))team.value=prior;}
  for(const n of [scope,objective,criteria,file])n.addEventListener('input',controls);team.addEventListener('change',render);search.addEventListener('input',render);
  refreshButton.onclick=async()=>{if(busy)return;const ticket=++epoch;busy=true;ideas=loadedAt=null;clearReport();render();status.textContent='Reading existing local portfolio.';try{const current=await loadIdeas();if(ticket!==epoch)return;ideas=[...inventory(current).values()].sort((a,b)=>a.title.localeCompare(b.title)||a.idea_id.localeCompare(b.idea_id));loadedAt=new Date().toISOString();selected=new Set([...selected].filter(id=>ideas.some(i=>i.idea_id===id)));setTeams();status.textContent=ideas.length+' saved ideas loaded. Select membership explicitly or compare a frozen campaign.';}catch(e){if(ticket===epoch)status.textContent=e.message;}finally{busy=false;render();}};
  freeze.onclick=()=>{if(freeze.disabled)return;try{campaign=freezeCampaign(ideas.map(i=>({...i,planning:i})),[...selected],{scope:scope.value.trim(),objective:objective.value.trim(),success_criteria:criteria.value.trim()},loadedAt);clearReport();status.textContent='Campaign baseline frozen locally. Download it to resume later.';render();}catch(e){status.textContent=e.message;}};
  load.onclick=async()=>{if(load.disabled)return;const ticket=++epoch;busy=true;controls();try{const f=file.files[0];require(f.size>0&&f.size<=MAX_BYTES,'Campaign file exceeds 256 KiB.');const c=importCampaign(new TextDecoder('utf-8',{fatal:true}).decode(await f.arrayBuffer()));if(ticket!==epoch)return;campaign=c;selected.clear();clearReport();status.textContent='Local campaign declaration opened. Its baseline is not independently authenticated.';}catch(e){if(ticket===epoch)status.textContent=e.message;}finally{busy=false;render();}};
  review.onclick=()=>{if(review.disabled)return;clearReport();try{report=reviewCampaign(campaign,ideas.map(i=>({...i,planning:i})),loadedAt);result.replaceChildren(el('p',report.missing_count+' missing; '+report.changed_revision_count+' changed revisions; '+report.revision_conflict_count+' revision conflicts; '+report.waiting_idea_count+' waiting on dependencies.'));
    for(const r of report.items){const section=el('section');section.append(el('h5',r.baseline.title),el('p',r.baseline.idea_id+' — '+r.status.replaceAll('_',' ')));if(r.current){section.append(el('p','Baseline: '+r.baseline.state+' rev '+r.baseline.revision+'; current: '+r.current.state+' rev '+r.current.revision+'. Changed fields: '+(r.changed_fields.join(', ')||'none in this projection')+'.'),el('p','Owner: '+(r.current.owner||'not set')+'; team: '+(r.current.team||'not set')+'; quarter: '+(r.current.target_quarter||'not set')+'.'));for(const d of r.dependencies)section.append(el('p','Dependency '+(d.title??d.idea_id)+' — '+d.scope.replaceAll('_',' ')+'; '+d.state+'.'));const open=button('Open saved idea '+r.current.idea_id);open.onclick=()=>Promise.resolve(openIdea(r.current.idea_id)).catch(e=>{status.textContent=e.message;});section.append(open);}result.append(section);}
    result.append(el('p',report.interpretation));status.textContent='Campaign review prepared from the loaded inventory. Reload for newer changes.';controls();}catch(e){clearReport();status.textContent=e.message;controls();}};
  save.onclick=()=>{if(!save.disabled)download('owg-implementation-campaign.json',JSON.stringify(campaign,null,2));};saveReview.onclick=()=>{if(!saveReview.disabled)download('owg-implementation-campaign-review.json',JSON.stringify(report,null,2));};
  reset.onclick=()=>{if(busy)return;campaign=null;selected.clear();clearReport();scope.value=objective.value=criteria.value='';file.value='';status.textContent='Start a new explicit campaign.';render();};
  const details=el('details');details.id='owgImplementationCampaign';details.append(el('summary','Coordinate a departmental implementation campaign'),el('p','Use existing saved opportunities; declare the objective and measurement criteria. No new portfolio is created, and no idea state or execution permission changes.'),refreshButton,field('Campaign scope or department label',scope),field('Business objective',objective),field('Success criteria and measurement plan',criteria),field('Filter saved team labels',team),field('Search saved ideas',search),summary,selection,freeze,field('Local campaign JSON',file),load,save,review,result,saveReview,reset,status);host.append(details);
  if(typeof mountOutcomes==='function'&&typeof loadReadiness==='function')outcomePanel=mountOutcomes(details,{getReport:()=>busy?null:report,loadReadiness,recheck:async c=>reviewCampaign(c,await loadIdeas(),new Date().toISOString()),download});
  render();return {invalidate};
}
