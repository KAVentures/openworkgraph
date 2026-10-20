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
    const style=el('style');style.textContent=[
      '#owgOpportunityReview{border-top:1px solid #d9dfd4;margin-top:18px;padding-top:18px}',
      '#owgOpportunityReview .owg-opportunity-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:12px 0}',
      '#owgOpportunityReview .owg-opportunity-grid label{display:grid;gap:4px}',
      '#owgOpportunityReview .owg-opportunity-grid input,#owgOpportunityReview .owg-opportunity-grid select{width:100%;max-width:250px}',
      '#owgOpportunityReview .owg-opportunity-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}',
      '#owgOpportunityReview .owg-opportunity-check{display:block;margin:12px 0}'
    ].join('\n');panel.prepend(style);
    const readValues=()=>{
      const obj={};
      panel.querySelectorAll('[data-opportunity-field]').forEach(field=>{
        obj[field.dataset.opportunityField]=field.value;
      });
      return obj;
    };
    const update=()=>{
      scenario=null;exportButton.disabled=true;box.checked=false;resultHost.replaceChildren();
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
  window.__owgOpportunityModel={calculate:calc};
})();