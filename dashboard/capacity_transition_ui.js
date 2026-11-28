/* Offline workbench. Uploaded bytes never reach a server. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const el=(tag,value)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=String(value);return n;};
const opt=(value,name)=>{const n=el('option',name);n.value=value;return n;};
const btn=(value,fn)=>{const n=el('button',value);n.type='button';n.onclick=fn;return n;};
const field=(name,input)=>{const n=el('label',name+' ');n.append(input);return n;};
const number=(min,max)=>{const n=el('input');n.type='number';n.min=min;n.max=max;n.step='1';return n;};
const pct=x=>(x*100).toFixed(2)+'%';
const yes=x=>x===null?'Not specified':x?'Yes':'No';
const cost=x=>{const n=BigInt(x.minor_units_numerator),whole=n/60n,rem=n%60n;return whole.toString()+(rem?' + '+rem+'/60':'')+' minor units';};
const direction={
 finite_plan_found:'A qualifying finite plan was found.',
 no_service_eligible_design_in_one_or_more_periods:'At least one period has no service-eligible tested design.',
 switch_cap_prevents_any_service_eligible_plan:'The switch cap prevents any service-eligible sequence.',
 budget_prevents_any_service_eligible_plan:'The budget is below every service-eligible sequence within the switch cap.'
};
function table(name,headers,rows){
 const wrap=el('div');wrap.className='table-wrap';const t=el('table');t.setAttribute('aria-label',name);
 const head=el('thead'),hr=el('tr');for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}head.append(hr);
 const body=el('tbody');for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const cell=el(i?'td':'th',v);if(!i)cell.scope='row';tr.append(cell);});body.append(tr);}t.append(head,body);wrap.append(t);return wrap;
}
let generation=0,source=null,grid=null,sha=null,result=null,costFields=[],periodFields=[];
function buttons(){$('run').disabled=!source||!$('confirm').checked;$('download').disabled=!result||!$('confirm').checked;$('add').disabled=periodFields.length>=OWGCapacityTransition.MAX_PERIODS;}
function invalidate(clear=true){result=null;$('results').replaceChildren();if(clear)$('confirm').checked=false;buttons();}
function clear(){generation++;source=grid=sha=null;costFields=[];periodFields=[];$('basis').replaceChildren();$('costs').replaceChildren();$('periods').replaceChildren();$('settings').hidden=true;for(const id of ['currency','target','cap','fee','budget'])$(id).value='';$('status').textContent='';invalidate();}
function renumber(){periodFields.forEach((p,i)=>{p.title.textContent='Period '+(i+1);p.rate.setAttribute('aria-label','Period '+(i+1)+' demand rate');p.minutes.setAttribute('aria-label','Period '+(i+1)+' duration minutes');p.up.disabled=i===0;p.down.disabled=i===periodFields.length-1;p.remove.disabled=periodFields.length===1;});buttons();}
function reorder(index,delta){const other=index+delta;if(other<0||other>=periodFields.length)return;[periodFields[index],periodFields[other]]=[periodFields[other],periodFields[index]];$('periods').replaceChildren(...periodFields.map(p=>p.row));renumber();invalidate();}
function addPeriod(){
 if(!grid||periodFields.length>=OWGCapacityTransition.MAX_PERIODS)return;
 const row=el('div');row.className='period';const title=el('strong'),rate=el('select');rate.append(opt('','Choose rate'),...grid.rates.map(v=>opt(v,v+' arrivals/hour')));
 const minutes=number(1,10080),p={row,title,rate,minutes};
 p.up=btn('Move earlier',()=>reorder(periodFields.indexOf(p),-1));p.down=btn('Move later',()=>reorder(periodFields.indexOf(p),1));
 p.remove=btn('Remove period',()=>{if(periodFields.length===1)return;periodFields.splice(periodFields.indexOf(p),1);row.remove();renumber();invalidate();});
 row.append(title,field('Tested demand',rate),field('Duration (minutes)',minutes),p.up,p.down,p.remove);
 periodFields.push(p);$('periods').append(row);renumber();invalidate();
}
function renderBasis(){
 $('basis').append(el('p','Exact tested rates: '+grid.rates.join(', ')+' arrivals/hour. Seeds: '+grid.seeds.join(', ')+'. Original elapsed deadline: '+grid.deadline+' minutes; original target: '+pct(grid.originalShare)+'.'));
 const digest=el('p','Selected file SHA-256: ');digest.append(el('code',sha));$('basis').append(digest);
 $('initial').replaceChildren(opt('','Unspecified; no first-period switch'),...grid.designs.map((d,i)=>opt(i,d)));
 for(const design of grid.designs){const n=number(0,1000000000);n.setAttribute('aria-label',design+' hourly minor-unit cost');costFields.push({design,n});$('costs').append(field(design+' cost per staffed hour (minor units)',n));}
 // Leave all operator assumptions blank except the exact source threshold
 // when it is representable at the supported basis-point precision.
 const bps=Math.round(grid.originalShare*10000);if(Math.abs(bps/10000-grid.originalShare)<1e-12)$('target').value=(bps/100).toFixed(2);
 $('settings').hidden=false;addPeriod();
}
$('source').onchange=async()=>{
 clear();const run=generation,file=$('source').files[0];if(!file)return;
 $('status').textContent='Reading exact selected report…';
 try{
  if(!file.size||file.size>OWGCapacityTransition.MAX_BYTES)throw Error('Choose a nonempty capacity JSON file of at most 8 MiB.');
  if(!globalThis.crypto?.subtle)throw Error('SHA-256 requires a local secure browser context.');
  const bytes=await file.arrayBuffer();if(bytes.byteLength>OWGCapacityTransition.MAX_BYTES)throw Error('Report exceeds file limit.');
  const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const parsed=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));const checked=OWGCapacityTransition.readReport(parsed);
  if(run!==generation)return;source=parsed;grid=checked;sha=digest;renderBasis();$('status').textContent='Source grid loaded. Enter all planning assumptions.';
 }catch(error){if(run===generation){source=grid=sha=null;$('status').textContent=error instanceof Error?error.message:'Invalid report.';buttons();}}
};
$('clear').onclick=()=>{$('source').value='';clear();};
$('add').onclick=addPeriod;
$('settings').addEventListener('input',e=>{if(e.target!==$('confirm'))invalidate();});
$('settings').addEventListener('change',e=>{if(e.target!==$('confirm'))invalidate();});
$('confirm').onchange=()=>invalidate(false);
function integer(n,optional=false){if(optional&&!n.value.trim())return null;if(!n.value.trim()||!n.checkValidity()||!Number.isSafeInteger(Number(n.value)))throw Error('Enter all required whole-number assumptions.');return Number(n.value);}
function proposal(){
 const target=$('target');if(!target.value||!target.checkValidity())throw Error('Enter an explicit completion share at basis-point precision.');
 const pctValue=Number(target.value),bps=Math.round(pctValue*100);if(Math.abs(pctValue*100-bps)>1e-7)throw Error('Completion share must use at most two decimal places.');
 return {confirmScenario:$('confirm').checked,sourceSha256:sha,currency:$('currency').value.trim().toUpperCase(),targetBasisPoints:bps,
   costs:costFields.map(c=>({design:c.design,hourlyMinorUnits:integer(c.n)})),
   periods:periodFields.map(p=>{if(!p.rate.value)throw Error('Choose a tested demand rate for every period.');return {rate:Number(p.rate.value),minutes:integer(p.minutes)};}),
   initialDesign:$('initial').value===''?null:grid.designs[Number($('initial').value)],maxSwitches:integer($('cap')),switchMinorUnits:integer($('fee')),budgetMinorUnits:integer($('budget'),true)};
}
function render(r){
 const root=$('results'),best=r.minimum_cost_tested_witness;
 root.replaceChildren(el('h2','Finite planning result'),el('p',direction[r.status]),el('p',r.assumptions.currency+' · '+(r.reviewed_target_basis_points/100).toFixed(2)+'% within '+r.deadline_minutes+' minutes in every original seed.'));
 if(best)root.append(el('p','Minimum declared cost: '+cost(best.total_cost)+'; switches: '+best.switches+'; equally low-cost allowed sequences: '+best.tied_minimum_paths_across_allowed_switch_counts+'. One deterministic witness is shown.'),
   table('Minimum cost witness',['Period','Demand/hour','Minutes','Tested design','Switch','Staffed cost','Switch cost','Cumulative cost','Worst seed lower share'],best.steps.map(x=>[x.period,x.rate,x.minutes,x.design,yes(x.switch_from_previous),cost(x.staffed_cost),cost(x.switch_cost),cost(x.cumulative_cost),pct(x.evidence.worst_seed_lower_share)])));
 root.append(el('h2','Exact cost frontier by switch count'),table('Switch frontier',['Switches','Minimum cost','Within switch cap','Within budget','Tied minimum sequences','One witness'],r.exact_switch_cost_frontier.map(x=>[x.switches,cost(x.cost),yes(x.within_switch_cap),yes(x.within_budget),x.tied_minimum_paths,x.witness_designs.join(' → ')])),
 el('h2','Fixed-design comparison'),table('Fixed designs',['Design','Eligible every period','Switches','Cost','Within cap','Within budget','Qualifies'],r.fixed_design_comparators.map(x=>[x.design,yes(x.service_eligible_every_period),x.switches,cost(x.cost),yes(x.within_switch_cap),yes(x.within_budget),yes(x.qualifies)])),
 el('h2','Period-by-design service evidence'));
 for(const p of r.period_eligibility){const detail=el('details');detail.append(el('summary','Period '+p.period+' · '+p.rate+' arrivals/hour · '+p.minutes+' minutes'),
 table('Period '+p.period+' eligibility',['Design','Eligible','Worst lower','Worst upper','Overloaded seeds','Known-miss seeds','Indeterminate seeds'],p.designs.map(x=>[x.design,yes(x.eligible),pct(x.worst_seed_lower_share),pct(x.worst_seed_upper_share),x.overloaded_seeds.join(', ')||'None',x.known_miss_seeds.join(', ')||'None',x.indeterminate_seeds.join(', ')||'None'])));root.append(detail);}
 root.append(el('p',r.interpretation));
}
$('run').onclick=()=>{invalidate(false);try{const next=OWGCapacityTransition.solve(source,proposal());render(next);result=next;$('status').textContent='Finite planning review complete. No staffing action is authorized.';}catch(error){$('status').textContent=error instanceof Error?error.message:'Calculation failed.';}buttons();};
$('download').onclick=()=>{if($('download').disabled)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-capacity-transition-plan.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
buttons();
})();
