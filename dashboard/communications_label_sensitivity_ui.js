/* Aggregate-only local annotation sensitivity, no model or threshold changes. */
import '/communications-label-sensitivity.js';
export function mount(host,{data,subset,language,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const panel=el('details');panel.id='owgCommsLabelSensitivity';
  panel.append(el('summary','Reviewer-label error sensitivity'),el('p','Keep both classifiers and their abstentions fixed. Change hypothetical binary reviewer labels within an explicit budget. This is exact adversarial sensitivity, not an estimate of annotation error, a confidence interval, or a recommendation to tune the holdout.'));
  const budget=el('input');budget.type='number';budget.min='0';budget.max='10000';budget.step='1';budget.id='owgCommsLabelBudget';budget.setAttribute('aria-label','Label error budget in basis points');
  const label=el('label');label.append(el('span','Maximum label flips (basis points; 100 = 1%) '),budget);
  const run=button('Review annotation sensitivity'),save=button('Download label sensitivity JSON');
  run.id='owgCommsLabelRun';save.id='owgCommsLabelDownload';
  const consent=el('input');consent.type='checkbox';consent.id='owgCommsLabelConfirm';const c=el('label');
  c.append(consent,el('span','I reviewed this hypothetical error budget. It is not measured annotation error.'));
  const status=el('p');status.id='owgCommsLabelStatus';status.setAttribute('role','status');
  const output=el('div');output.id='owgCommsLabelResult';let report=null;
  function controls(){run.disabled=!consent.checked||!budget.value||!budget.checkValidity();save.disabled=!report;}
  function invalidate(){report=null;consent.checked=false;output.replaceChildren();status.textContent='Review and confirm the current budget.';controls();}
  budget.oninput=invalidate;consent.onchange=()=>{if(!consent.checked)invalidate();else controls();};
  function table(name,head,rows){
    const wrap=el('div');wrap.style.cssText='max-width:100%;overflow-x:auto';
    const t=el('table');t.setAttribute('aria-label',name);const h=el('thead'),hr=el('tr'),b=el('tbody');
    for(const text of head){const th=el('th',text);th.scope='col';hr.append(th);}h.append(hr);
    for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const td=el(i?'td':'th',v??'Not attainable');if(!i)td.scope='row';tr.append(td);});b.append(tr);}t.append(h,b);wrap.append(t);return wrap;
  }
  const pct=x=>x===null?'No data':(100*x).toFixed(2)+' pp';
  const values=(name,r)=>[name,r.label_decisions,r.max_label_flips,pct(r.observed_difference),
    pct(r.lower_difference)+' to '+pct(r.upper_difference),r.lower_net_correct+' to '+r.upper_net_correct,
    r.minimum_flips_to_erase_observed_lead,r.minimum_flips_to_reverse_observed_lead,
    ({learned:'Learned',keyword:'Keyword',not_robust:'Neither proven',no_data:'No data'})[r.strict_lead_under_budget]];
  function render(r){
    const headers=['Scope','Label decisions','Max flips','Observed difference','Attainable difference bounds','Net correct bounds','Flips to erase lead','Flips to reverse lead','Strict lead throughout budget'];
    output.replaceChildren(el('h4','Exact annotation sensitivity'),el('p',r.reviewed_messages+' reviewed messages · '+r.declared_intents+' declared intents · '+(r.language||'all languages')+' · '+r.subset.replaceAll('_',' ')),
      table('Per-intent annotation sensitivity',headers,r.per_intent.map(x=>values(x.intent,x))),
      el('h5','Pooled independent intent-label cells'),table('Pooled label sensitivity',headers,[values('All intent-label cells',r.pooled_intent_label_cells)]),
      el('p',r.interpretation));
    const details=el('details');details.append(el('summary','Exact flip-effect populations'),
      table('Flip-effect counts',['Intent','−2','−1','0','+1','+2'],r.per_intent.map(x=>[x.intent,...[-2,-1,0,1,2].map(k=>x.flip_effect_counts[k])])));output.append(details);
  }
  run.onclick=()=>{if(!isCurrent()||run.disabled)return;report=null;output.replaceChildren();save.disabled=true;
    try{const r=globalThis.OWGLabelSensitivity.review(data,subset,language,Number(budget.value));report=r;render(r);status.textContent='Exact label sensitivity reviewed. No classifier changed.';}
    catch(e){report=null;status.textContent=e.message;}controls();};
  save.onclick=()=>{if(!report||!isCurrent())return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-communications-label-sensitivity.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  panel.append(label,c,run,save,status,output);host.replaceChildren(panel);invalidate();
}
