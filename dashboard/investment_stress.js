/* Common-scenario stress workbench; existing snapshot is revalidated by server. */
export function mount(host,{investment,post,snapshot}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const field=(label,node)=>{const n=el('label');n.append(el('span',label),node);return n;};
  const panel=el('details');panel.id='owgInvestmentStress';panel.append(el('summary','Stress-test cash flows across common scenarios'),el('p','Declare 1–12 common scenarios. Every design faces the same assumptions, and the original scenario is always included. Results are conditional, not forecasts or probabilities. Costs and volume may rise or fall; effect and cash conversion may only be retained or reduced. Nonlabor factor scales both positive and negative changes.'));
  const rows=el('div'),fields=[];
  const budget=el('input');budget.type='number';budget.min='0';budget.max='1e18';budget.step='any';budget.setAttribute('aria-label','Maximum peak funding');budget.placeholder='No funding gate';
  const review=el('textarea');review.readOnly=true;review.rows=8;review.style.width='100%';review.setAttribute('aria-label','Exact reviewed stress assumptions');
  const prepare=button('Prepare common scenario review'),run=button('Evaluate stress scenarios'),download=button('Download stress review JSON'),add=button('Add common scenario');
  prepare.id='owgStressPrepare';run.id='owgStressRun';download.id='owgStressDownload';
  const confirm=el('input');confirm.type='checkbox';confirm.id='owgStressConfirm';
  const status=el('p');status.setAttribute('role','status');status.id='owgStressStatus';const resultHost=el('div');resultHost.id='owgStressResult';
  let generation=0,draft=null,report=null,busy=false;
  const buttons=()=>{run.disabled=busy||!draft||!confirm.checked;download.disabled=busy||!report;confirm.disabled=busy||!draft;add.disabled=fields.length>=12;};
  function invalidate(){generation++;draft=report=null;busy=false;review.value='';confirm.checked=false;resultHost.replaceChildren();status.textContent='Review assumptions before running.';buttons();}
  const definitions=[['volume_factor','Volume factor',3],['effect_factor','Retained effort effect factor',1],['conversion_factor','Cash conversion factor',1],['cost_factor','Fixed and investment cost factor',3],['nonlabor_factor','Nonlabor cash change factor',3],['discount_rate','Monthly discount rate (fraction)',1]];
  function addScenario(){
    if(fields.length>=12)return;
    const group=el('fieldset');group.append(el('legend','Common stress scenario'));
    const name=el('input');name.maxLength=80;name.setAttribute('aria-label','Stress scenario name');
    const inputs={};
    for(const [key,title,max] of definitions){
      const n=el('input');n.type='number';n.min='0';n.max=String(max);n.step='any';n.setAttribute('aria-label',title);inputs[key]=n;
    }
    const row={group,name,inputs};const remove=button('Remove common scenario');
    remove.onclick=()=>{if(fields.length===1)return;fields.splice(fields.indexOf(row),1);group.remove();invalidate();};
    group.append(field('Scenario name',name),...definitions.map(([key,title])=>field(title,inputs[key])),remove);
    fields.push(row);rows.append(group);invalidate();
  }
  add.onclick=addScenario;rows.addEventListener('input',invalidate);budget.oninput=invalidate;confirm.onchange=buttons;
  function number(n){if(!n.value.trim()||!n.checkValidity()||!Number.isFinite(Number(n.value)))throw Error('Enter every factor explicitly within its displayed range.');return Number(n.value);}
  prepare.onclick=()=>{
    invalidate();
    try{
      const scenarios=fields.map(row=>{if(!row.name.value.trim())throw Error('Name every stress scenario.');return {label:row.name.value.trim(),...Object.fromEntries(definitions.map(([key])=>[key,number(row.inputs[key])]))};});
      const funding_limit=budget.value.trim()?number(budget):null;
      draft={investment,scenarios,funding_limit,confirm:true};review.value=JSON.stringify(draft,null,2);status.textContent='Review the exact original investment and common scenarios; then confirm.';buttons();
    }catch(error){status.textContent=error.message;}
  };
  function table(title,headers,rows){
    const wrap=el('div');wrap.className='table-wrap';wrap.style.maxWidth='100%';wrap.style.overflowX='auto';const t=el('table');t.setAttribute('aria-label',title);
    const head=el('thead'),hr=el('tr');for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}head.append(hr);
    const body=el('tbody');for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const cell=el(i?'td':'th',v??'Not specified');if(!i)cell.scope='row';tr.append(cell);});body.append(tr);}t.append(head,body);wrap.append(t);return wrap;
  }
  const exact=x=>x.decimal,yes=x=>x===null?'Not specified':x?'Yes':'No',names=x=>x.join(', ')||'None';
  function render(r){
    resultHost.replaceChildren(el('h5','Common-scenario investment stress review'),el('p',r.scenario_count+' scenarios including reference · '+r.currency+' · funding limit '+(r.funding_limit?exact(r.funding_limit):'not specified')+'.'),
      table('Stress design summary',['Design','Worst cash NPV','Best cash NPV','Maximum peak funding','Maximum cash regret','Passes gates'],
        r.designs.map(d=>[d.design,exact(d.worst_cash_npv),exact(d.best_cash_npv),exact(d.maximum_peak_funding),exact(d.maximum_cash_regret),yes(d.passes_declared_gates)])),
      el('p','Passing designs: '+names(r.passing_designs)+'. Maximin cash NPV among passing designs: '+names(r.maximin_cash_npv_among_passing_designs)+'. Minimax cash regret among passing designs: '+names(r.minimax_cash_regret_among_passing_designs)+'. These are finite mathematical comparisons, not recommendations.'),
      table('Robust dominance',['Dominant design','Dominated design','Minimum cash NPV advantage','Minimum peak funding advantage'],r.robust_dominance.map(x=>[x.dominant,x.dominated,exact(x.minimum_cash_advantage),exact(x.minimum_funding_advantage)])));
    for(const d of r.designs){
      const details=el('details');details.append(el('summary',d.design+' · scenario evidence'),el('p','Worst cash NPV: '+names(d.worst_cash_scenarios)+'; peak funding: '+names(d.peak_funding_scenarios)+'; maximum regret: '+names(d.regret_scenarios)+'.'),
        table('Scenario evidence for '+d.design,['Scenario','Cash NPV','Economic NPV','Peak funding','Closing cash','Cash regret','Funding limit met','Sustained cash break-even month'],
          d.scenarios.map(s=>[s.label,exact(s.cash_npv),exact(s.economic_npv),exact(s.peak_funding),exact(s.closing_cash),exact(s.cash_regret),yes(s.within_funding_limit),s.undiscounted_cash_break_even.sustained_nonnegative_from_month??'None within horizon'])));resultHost.append(details);
    }
    resultHost.append(el('p',r.interpretation));
  }
  run.onclick=async()=>{
    if(run.disabled)return;const n=++generation,p=draft;busy=true;report=null;resultHost.replaceChildren();buttons();status.textContent='Evaluating common scenarios against the retained snapshot…';
    try{const data=await post(p);if(n!==generation||!host.isConnected)return;if(data.snapshot_sha256!==snapshot||data.format!=='openworkgraph.investment-stress-review.v1'||data.execution_authorized!==false)throw Error('Unexpected stress review basis.');render(data);report=data;status.textContent='Finite stress review complete. No savings or return verified.';}
    catch(error){if(n===generation&&host.isConnected)status.textContent=error.message;}
    finally{if(n===generation&&host.isConnected){busy=false;buttons();}}
  };
  download.onclick=()=>{if(!report||download.disabled)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-investment-stress.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  panel.append(field('Maximum undiscounted peak cash funding (optional)',budget),rows,add,prepare,field('Exact reviewed assumptions',review),field('I reviewed the exact common assumptions and retained basis; these are not forecasts.',confirm),run,download,status,resultHost);
  host.replaceChildren(panel);addScenario();
}
