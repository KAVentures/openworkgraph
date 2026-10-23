/* Optional process improvement review; observations and cost assumptions stay separate. */
(() => {
  'use strict';
  const fmt='openworkgraph.automation-opportunity-scenario.v1';
  const currencies=['SEK','USD','EUR','GBP'];
  let report=null;
  let scenario=null;

  const el=(tag,value,cls)=>{
    const node=document.createElement(tag);
    if(value!==undefined&&value!==null)node.textContent=String(value);
    if(cls)node.className=cls;
    return node;
  };
  function calc(values) {
    if(!values||typeof values!=='object')throw new Error('Enter an explicit business scenario.');
    const numeric=(field,lower,upper)=>{
      const raw=values[field];
      if(raw===null||raw===undefined||String(raw).trim()==='')throw new Error('Missing assumption: '+field);
      const value=Number(raw);
      if(!Number.isFinite(value)||value<lower||value>upper)throw new Error('Invalid assumption: '+field);
      return value;
    };
    const currency=String(values.currency||'');
    if(!currencies.includes(currency))throw new Error('Choose a currency.');
    const monthlyCases=numeric('monthly_cases',1,10000000);
    const minutes=numeric('minutes_saved_per_case',0,10080);
    const coverage=numeric('eligible_fraction_percent',0,100);
    const hourly=numeric('loaded_hourly_cost',0,1000000);
    const recurring=numeric('recurring_monthly_cost',0,1000000000);
    const implementation=numeric('one_time_implementation_cost',0,1000000000);
    const eligibleCases=monthlyCases*coverage/100;
    const hours=eligibleCases*minutes/60;
    const gross=hours*hourly;
    const net=gross-recurring;
    return {
      currency,
      inputs: {
        monthly_cases:monthlyCases,
        minutes_saved_per_case:minutes,
        eligible_fraction_percent:coverage,
        loaded_hourly_cost:hourly,
        recurring_monthly_cost:recurring,
        one_time_implementation_cost:implementation
      },
      projection:{
        eligible_cases_per_month:Number(eligibleCases.toFixed(2)),
        assumed_hours_saved_per_month:Number(hours.toFixed(2)),
        gross_labor_value_per_month:Number(gross.toFixed(2)),
        net_monthly_value_before_other_costs:Number(net.toFixed(2)),
        simple_payback_months:net>0?Number((implementation/net).toFixed(2)):null
      },
      basis:'user_supplied_assumptions_not_derived_from_observed_event_gaps',
      guaranteed_savings:false,
      includes_quality_risk_regulatory_compliance:false
    };
  }
  // Each alternative carries its own assumptions; none is a recommendation or an approval.
  const ALTERNATIVES=[
    {id:'eliminate_step',label:'Eliminate the step',autonomous_actions:false,needs_rules:true},
    {id:'deterministic_automation',label:'Deterministic automation (API or rules)',autonomous_actions:true,needs_rules:true},
    {id:'ai_agent',label:'AI agent',autonomous_actions:true,needs_rules:true},
    {id:'ai_agent_with_approval',label:'AI agent with human approval',autonomous_actions:false,needs_rules:true},
    {id:'human_only',label:'Keep human-only (baseline)',autonomous_actions:false,needs_rules:false}
  ];
  function compare(shared,rows){
    if(!shared||typeof shared!=='object'||!rows||typeof rows!=='object')throw new Error('Enter assumptions for each alternative.');
    const currency=String(shared.currency||'');
    if(!currencies.includes(currency))throw new Error('Choose a currency.');
    const number=(source,field,lower,upper,label)=>{
      const raw=source[field];
      if(raw===null||raw===undefined||String(raw).trim()==='')throw new Error('Missing assumption: '+label);
      const value=Number(raw);
      if(!Number.isFinite(value)||value<lower||value>upper)throw new Error('Invalid assumption: '+label);
      return value;
    };
    const monthlyCases=number(shared,'monthly_cases',1,10000000,'monthly_cases');
    const hourly=number(shared,'loaded_hourly_cost',0,1000000,'loaded_hourly_cost');
    const results=ALTERNATIVES.map(alt=>{
      if(alt.id==='human_only')return {id:alt.id,label:alt.label,baseline:true,eligible_cases_per_month:0,
        net_minutes_saved_per_case:0,assumed_hours_saved_per_month:0,gross_labor_value_per_month:0,
        net_monthly_value_before_other_costs:0,simple_payback_months:null,
        autonomous_business_actions:false,human_approval_required:false,requires_confirmed_business_rules:false};
      const row=rows[alt.id]||{};
      const tag=field=>alt.id+'.'+field;
      const minutes=number(row,'minutes_saved_per_case',0,10080,tag('minutes_saved_per_case'));
      const review=alt.id==='ai_agent_with_approval'?number(row,'review_minutes_per_case',0,10080,tag('review_minutes_per_case')):0;
      const coverage=number(row,'eligible_fraction_percent',0,100,tag('eligible_fraction_percent'));
      const recurring=number(row,'recurring_monthly_cost',0,1000000000,tag('recurring_monthly_cost'));
      const oneTime=number(row,'one_time_implementation_cost',0,1000000000,tag('one_time_implementation_cost'));
      const eligible=monthlyCases*coverage/100;
      const netMinutes=minutes-review;
      const hours=eligible*netMinutes/60;
      const gross=hours*hourly;
      const net=gross-recurring;
      return {id:alt.id,label:alt.label,baseline:false,eligible_cases_per_month:Number(eligible.toFixed(2)),
        net_minutes_saved_per_case:Number(netMinutes.toFixed(2)),assumed_hours_saved_per_month:Number(hours.toFixed(2)),
        gross_labor_value_per_month:Number(gross.toFixed(2)),net_monthly_value_before_other_costs:Number(net.toFixed(2)),
        simple_payback_months:net>0?Number((oneTime/net).toFixed(2)):null,
        autonomous_business_actions:alt.autonomous_actions,human_approval_required:alt.id==='ai_agent_with_approval',
        requires_confirmed_business_rules:alt.needs_rules};
    });
    const ordered=[...results].sort((a,b)=>b.net_monthly_value_before_other_costs-a.net_monthly_value_before_other_costs||a.id.localeCompare(b.id));
    return {format:'openworkgraph.implementation-alternatives-comparison.v1',currency,
      shared_inputs:{monthly_cases:monthlyCases,loaded_hourly_cost:hourly},alternatives:results,
      ordered_by_modeled_net_value:ordered.map(item=>item.id),
      basis:'user_supplied_assumptions_per_alternative_not_observed_labor_or_verified_savings',
      recommendation:null,approved_alternative:null,authorized_agent_actions:false,
      includes_quality_risk_regulatory_compliance:false,guaranteed_savings:false};
  }
  function input(label,key,defaultValue,step){
    const wrap=el('label');
    wrap.appendChild(el('span',label));
    const field=el('input');field.type='number';field.inputMode='decimal';
    field.min='0';field.max='1000000000';field.step=step||'any';
    field.value=String(defaultValue);
    field.dataset.opportunityField=key;
    wrap.appendChild(field);return wrap;
  }
  function render(result) {
    report=result?.format==='openworkgraph.process-mining-csv-preview.v1'?result:null;
    scenario=null;
    const host=document.getElementById('owgProcessResults');
    if(!host||!report)return;
    document.getElementById('owgOpportunityReview')?.remove();
    const panel=el('section',null,'owg-opportunity-review');panel.id='owgOpportunityReview';
    panel.appendChild(el('h3','Process improvement signals'));
    panel.appendChild(el('p','These are prompts to investigate. A long event gap is not verified waiting time; a repeated activity is not necessarily rework. Neither proves a viable automation.','muted'));
    const signals=Array.isArray(report.review_signals)?report.review_signals:[];
    if(signals.length){
      const list=el('ul');
      signals.slice(0,12).forEach(signal=>{
        const text=signal.type==='long_observed_transition_gap'
          ?String(signal.from)+' → '+String(signal.to)+': median '+Math.round(Number(signal.median_elapsed_gap_seconds||0)/60)+' minutes between observed events ('+Number(signal.observed_case_count||0)+' cases)'
          :String(signal.activity)+': '+Number(signal.additional_observed_events||0)+' extra occurrences across '+Number(signal.observed_case_count||0)+' cases';
        list.appendChild(el('li',text));
      });
      panel.appendChild(list);
    }else panel.appendChild(el('p','No repeated-activity or positive-gap review signals in this limited export.','muted'));
    panel.appendChild(el('h3','Estimate a possible automation benefit'));
    panel.appendChild(el('p','Enter your own operating assumptions. The importer cannot infer monthly volume, labor minutes saved, cost, eligible work, or implementation cost from event logs. Leave the defaults only if they actually match your business.','muted'));
    const grid=el('div',null,'owg-opportunity-grid');
    const currency=el('label');currency.appendChild(el('span','Currency'));
    const currencySelect=el('select');currencySelect.dataset.opportunityField='currency';
    currencies.forEach(symbol=>{const opt=el('option',symbol);opt.value=symbol;currencySelect.appendChild(opt)});
    currencySelect.value='SEK';currency.appendChild(currencySelect);
    grid.append(
      currency,
      input('Cases per month','monthly_cases','',1),
      input('Minutes saved per eligible case','minutes_saved_per_case','',0.5),
      input('Cases eligible (%)','eligible_fraction_percent','',1),
      input('Loaded labor cost per hour','loaded_hourly_cost','',1),
      input('Recurring monthly automation costs','recurring_monthly_cost','0',1),
      input('One-time implementation cost','one_time_implementation_cost','0',1)
    );
    panel.appendChild(grid);
    const review=el('label',null,'owg-opportunity-check');
    const box=el('input');box.type='checkbox';box.id='owgOpportunityReviewed';
    review.append(box,document.createTextNode(' These are my assumptions, not measured savings or approved automations.'));
    panel.appendChild(review);
    const actions=el('div',null,'owg-opportunity-actions');
    const computeButton=el('button','Calculate scenario','secondary');
    computeButton.type='button';
    const exportButton=el('button','Export reviewed scenario','secondary');exportButton.type='button';exportButton.disabled=true;
    actions.append(computeButton,exportButton);panel.appendChild(actions);
    const resultHost=el('div');resultHost.id='owgOpportunityResult';resultHost.setAttribute('role','status');panel.appendChild(resultHost);
    let comparison=null;
    const altSection=el('section');altSection.id='owgOpportunityAlternatives';
    altSection.appendChild(el('h3','Compare implementation alternatives'));
    altSection.appendChild(el('p','Enter separate assumptions for each way of handling this work. Cases per month, labor cost and currency come from the scenario above. The comparison orders your own assumptions; it does not recommend, approve or authorize any alternative.','muted'));
    const altTable=el('table');altTable.className='owg-opportunity-alternatives';
    const headRow=el('tr');['Alternative','Minutes saved per eligible case','Human review minutes per case','Cases eligible (%)','Recurring monthly cost','One-time cost'].forEach(h=>headRow.appendChild(el('th',h)));
    const thead=el('thead');thead.appendChild(headRow);altTable.appendChild(thead);
    const tbody=el('tbody');
    const altField=(id,key,label)=>{
      const cell=el('td');const field=el('input');field.type='number';field.inputMode='decimal';field.min='0';field.step='any';
      field.dataset.alternative=id;field.dataset.alternativeField=key;field.setAttribute('aria-label',label);cell.appendChild(field);return cell;
    };
    ALTERNATIVES.forEach(alt=>{
      const row=el('tr');row.appendChild(el('th',alt.label));
      if(alt.id==='human_only'){const cell=el('td','No change: every figure is zero.','muted');cell.colSpan=5;row.appendChild(cell);}
      else{
        row.appendChild(altField(alt.id,'minutes_saved_per_case',alt.label+' minutes saved per eligible case'));
        row.appendChild(alt.id==='ai_agent_with_approval'?altField(alt.id,'review_minutes_per_case',alt.label+' human review minutes per case'):el('td','—','muted'));
        row.appendChild(altField(alt.id,'eligible_fraction_percent',alt.label+' cases eligible percent'));
        row.appendChild(altField(alt.id,'recurring_monthly_cost',alt.label+' recurring monthly cost'));
        row.appendChild(altField(alt.id,'one_time_implementation_cost',alt.label+' one-time cost'));
      }
      tbody.appendChild(row);
    });
    altTable.appendChild(tbody);altSection.appendChild(altTable);
    const compareButton=el('button','Compare alternatives','secondary');compareButton.type='button';
    altSection.appendChild(compareButton);
    const altResult=el('div');altResult.id='owgOpportunityAlternativesResult';altResult.setAttribute('role','status');
    altSection.appendChild(altResult);panel.appendChild(altSection);
    const resetComparison=()=>{comparison=null;altResult.replaceChildren();};
    altTable.addEventListener('input',resetComparison);
    compareButton.addEventListener('click',()=>{
      try{
        const values=readValues();const rows={};
        altTable.querySelectorAll('[data-alternative]').forEach(field=>{
          (rows[field.dataset.alternative]=rows[field.dataset.alternative]||{})[field.dataset.alternativeField]=field.value;
        });
        comparison=compare({currency:values.currency,monthly_cases:values.monthly_cases,loaded_hourly_cost:values.loaded_hourly_cost},rows);
        const table=el('table');const hr=el('tr');
        ['Alternative','Net minutes saved per case','Modeled net value / month','Simple payback (months)','Acts without per-case approval'].forEach(h=>hr.appendChild(el('th',h)));
        table.appendChild(hr);
        comparison.ordered_by_modeled_net_value.forEach(id=>{
          const item=comparison.alternatives.find(x=>x.id===id);const tr=el('tr');
          tr.append(el('td',item.label),el('td',String(item.net_minutes_saved_per_case)),
            el('td',comparison.currency+' '+item.net_monthly_value_before_other_costs),
            el('td',item.simple_payback_months===null?'—':String(item.simple_payback_months)),
            el('td',item.autonomous_business_actions?'Yes':'No'));
          table.appendChild(tr);
        });
        altResult.replaceChildren(table,el('p','Ordered by your modeled net value only. Every alternative except human-only still needs confirmed business rules, and none is authorized by this comparison.','muted'));
      }catch(error){comparison=null;altResult.replaceChildren(el('p',String(error.message||error),'muted'));}
    });
    const style=el('style');style.textContent=[
      '#owgOpportunityReview{border-top:1px solid #d9dfd4;margin-top:18px;padding-top:18px}',
      '#owgOpportunityReview .owg-opportunity-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:12px 0}',
      '#owgOpportunityReview .owg-opportunity-grid label{display:grid;gap:4px}',
      '#owgOpportunityReview .owg-opportunity-grid input,#owgOpportunityReview .owg-opportunity-grid select{width:100%;max-width:250px}',
      '#owgOpportunityReview .owg-opportunity-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}',
      '#owgOpportunityReview .owg-opportunity-check{display:block;margin:12px 0}',
      '#owgOpportunityAlternatives{margin-top:18px;overflow-x:auto}',
      '#owgOpportunityAlternatives table{border-collapse:collapse;min-width:640px}',
      '#owgOpportunityAlternatives th,#owgOpportunityAlternatives td{padding:4px 6px;text-align:left;vertical-align:top}',
      '#owgOpportunityAlternatives input{width:100%;max-width:120px}'
    ].join('\n');panel.prepend(style);
    const readValues=()=>{
      const obj={};
      panel.querySelectorAll('[data-opportunity-field]').forEach(field=>{
        obj[field.dataset.opportunityField]=field.value;
      });
      return obj;
    };
    const update=()=>{
      scenario=null;exportButton.disabled=true;box.checked=false;resultHost.replaceChildren();resetComparison();
    };
    grid.addEventListener('change',update);
    grid.addEventListener('input',update);
    box.addEventListener('change',()=>{exportButton.disabled=!box.checked||!scenario;});
    computeButton.addEventListener('click',()=>{
      try{
        const output=calc(readValues());
        scenario=output;box.checked=false;exportButton.disabled=true;
        const x=output.projection;
        resultHost.replaceChildren(
          el('p','Estimated gross labor value: '+output.currency+' '+x.gross_labor_value_per_month+
            '/month; net after specified recurring costs: '+output.currency+' '+x.net_monthly_value_before_other_costs+
            '/month. '+(x.simple_payback_months===null?'No positive modeled payback.':
              'Simple modeled payback: '+x.simple_payback_months+' months.'),'muted'),
          el('p','Based entirely on your entered assumptions. It excludes quality, errors, deployment friction, taxation, risk, and unmeasured effort.','muted')
        );
      }catch(error){scenario=null;box.checked=false;exportButton.disabled=true;resultHost.replaceChildren(el('p',String(error.message||error),'muted'));}
    });
    exportButton.addEventListener('click',()=>{
      if(!scenario||!box.checked)return;
      const packet={
        format:fmt,scenario,
        evidence_summary:{
          input_rows:Number(report.input_rows||0),
          case_count:Number(report.case_count||0),
          observed_variant_count:Number(report.variant_count||0),
          event_gaps_not_labor_time:true,
          local_csv_source_not_independently_attested:true
        },
        ...(comparison?{alternatives_comparison:comparison}:{}),
        business_approval_granted:false,
        authorized_agent_actions:false,
        reviewed_assumptions:true,
        saved_to_owg:false
      };
      const url=URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)+'\n'],{type:'application/json'}));
      const anchor=el('a');anchor.href=url;anchor.download='openworkgraph-opportunity-scenario.json';
      document.body.appendChild(anchor);anchor.click();anchor.remove();
      setTimeout(()=>URL.revokeObjectURL(url),3000);
    });
    host.appendChild(panel);
  }
  function reset(){
    report=null;scenario=null;document.getElementById('owgOpportunityReview')?.remove();
  }
  window.owgOpportunityReview={render,reset};
  window.__owgOpportunityModel={calculate:calc,compare,alternatives:ALTERNATIVES.map(a=>a.id)};
})();