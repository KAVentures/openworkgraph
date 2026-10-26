/* Explicit, versioned scenario links. No cached evidence, execution or realized-value claims. */
(() => {
  'use strict';
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const label=(text,node)=>{const n=el('label',text+' ');n.append(node);return n;};
  const input=id=>{const n=el('input');n.id=id;n.maxLength=120;return n;};
  const button=(text,id,fn)=>{const n=el('button',text);n.type='button';n.id=id;n.onclick=fn;return n;};
  const options=(n,rows)=>n.replaceChildren(...rows.map(([id,text])=>{const o=el('option',text);o.value=id;return o;}));
  const ref=p=>({workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
  const key=r=>r.workflow_id+':'+r.revision+':'+r.content_sha256;
  const checkbox=id=>{const n=input(id);n.type='checkbox';return n;};
  const totals=(host,result)=>{
    host.replaceChildren(el('p','Status: '+result.status.replaceAll('_',' ')));
    if(result.totals){const dl=el('dl');for(const [k,v] of Object.entries(result.totals))if(typeof v==='string'||v===null)dl.append(el('dt',k.replaceAll('_',' ')),el('dd',v??'not positive'));host.append(dl);}
    for(const b of result.blockers||[])host.append(el('p','Withheld: '+b.code.replaceAll('_',' ')+(b.link_id?' · '+b.link_id:'')+(b.root_workflow_id?' · '+b.root_workflow_id:'')));
    host.append(el('p','Modeled assumptions only. Scope independence, labor savings, source freshness and realized value are not independently verified. No execution is authorized.'));
  };
  function create({inventoryHost,detailHost,call,open,onDenied}){
    let epoch=0,busy=false,selected=null,view=null,draft=null,dirty=false,choices=[],choiceAfter='',portfolio=null;
    const company=el('fieldset'),companyOutput=el('div'),currency=el('select'),review=checkbox('benefitPortfolioConfirm');companyOutput.id='benefitPortfolioResult';companyOutput.setAttribute('role','status');
    options(currency,['USD','EUR','GBP','SEK'].map(s=>[s,s]));currency.id='benefitPortfolioCurrency';
    const estimate=button('Calculate reviewed organization estimate','benefitPortfolioEstimate',()=>work(async ok=>{
      if(!portfolio||!review.checked)throw new Error('Load and review the current complete portfolio first.');
      const result=await call('/v1/human/process-benefit-portfolio/estimate',{currency:currency.value,expected_portfolio_sha256:portfolio.portfolio_sha256,user_confirmed:true,disjoint_scopes_confirmed:true});
      if(!ok())return;portfolio=result;paintPortfolio();review.checked=false;estimate.disabled=true;
    }));estimate.disabled=true;
    const download=button('Download current portfolio report','benefitPortfolioExport',()=>work(async ok=>{if(!portfolio)return;const r=await call('/v1/human/process-benefit-portfolio/export',{currency:currency.value,expected_portfolio_sha256:portfolio.portfolio_sha256,user_confirmed:true,disjoint_scopes_confirmed:portfolio.status==='modeled_estimate'});if(ok())saveFile(r,'owg-modeled-benefit-portfolio.json');}));download.disabled=true;
    const notice=el('p');notice.id='benefitPortfolioStatus';notice.setAttribute('role','status');
    company.append(el('legend','Organization scenario contributions'),el('p','Review explicit contribution links across saved process plans. Reused scenarios count once. Incomplete, stale, conflicting or overlapping contributions withhold the entire organization total; distinct scope keys alone are not evidence of non-overlap.'),
      label('Organization estimate currency',currency),button('Load current benefit portfolio','benefitPortfolioLoad',()=>work(async ok=>{clearPortfolio();const data=await call('/v1/human/process-benefit-portfolio?currency='+currency.value);if(!ok())return;portfolio=data;paintPortfolio();notice.textContent='Portfolio loaded. Inspect every contributing scope before confirming.';})),
      label('I reviewed this complete current portfolio, reused scenarios and all included benefit scopes; I declare that distinct included scopes do not overlap',review),estimate,download,notice,companyOutput);
    inventoryHost.append(company);
    const host=el('div');detailHost.append(host);let form=null,status=null,confirm=null,save=null,exportPlan=null;
    const note=text=>{if(status)status.textContent=text;notice.textContent=text;};
    function clearPortfolio(){portfolio=null;companyOutput.replaceChildren();review.checked=false;estimate.disabled=true;download.disabled=true;notice.textContent='';}
    function clear(){epoch++;busy=false;selected=view=draft=null;dirty=false;choices=[];choiceAfter='';host.replaceChildren();form=status=null;company.disabled=false;clearPortfolio();currency.value='USD';}
    async function work(fn){if(busy)return;const e=epoch;busy=true;company.disabled=true;if(form)form.disabled=true;
      try{await fn(()=>e===epoch);}catch(error){if(e!==epoch)return;clearPortfolio();if([401,403].includes(error.status)){clear();onDenied();return;}note(error.message||'Benefit request failed.');}
      finally{if(e===epoch){busy=false;company.disabled=false;if(form)form.disabled=false;}}
    }
    function select(data){epoch++;busy=false;selected=data;view=draft=null;dirty=false;choices=[];choiceAfter='';host.replaceChildren();form=null;clearPortfolio();company.disabled=false;
      status=el('p');status.id='benefitStatus';status.setAttribute('role','status');host.append(el('h3','Scenario contribution plan'),el('p','Separate from repository hierarchy and implementation exports. Link existing scenario-bearing process revisions, explicitly include each contribution, and give it a stable benefit-scope key. Keys and review are human declarations, not measured case disjointness.'),button('Load process benefit plan','benefitLoad',()=>work(load)),status);
    }
    function paintPortfolio(){
      totals(companyOutput,portfolio);companyOutput.prepend(el('p','As of '+portfolio.generated_at+' · fingerprint '+portfolio.portfolio_sha256+' · '+portfolio.unique_scenario_count+' unique scenario processes in scanned plans · '+portfolio.other_currency_plan_count+' scanned plans in other currencies. Not live monitoring.'));
      for(const item of portfolio.plans){const d=el('details');d.append(el('summary',(item.artifact?.plan.title||'Unavailable plan')+' · '+item.root_workflow_id+' · '+item.status.replaceAll('_',' ')),button('Open benefit root '+item.root_workflow_id,'benefitOpen_'+item.root_workflow_id,()=>work(()=>open(item.root_workflow_id))));companyOutput.append(d);}
      const list=el('ul');for(const item of portfolio.contributors)list.append(el('li',item.workflow_id+' · r'+item.revision+' · scope '+item.benefit_scope_id+' · linked from '+item.linked_root_ids.length+' roots · '+(item.title||item.state)));companyOutput.append(list);
      review.checked=false;review.disabled=portfolio.status!=='ready_for_review';estimate.disabled=true;download.disabled=false;
    }
    currency.onchange=clearPortfolio;review.onchange=()=>{estimate.disabled=!portfolio||portfolio.status!=='ready_for_review'||!review.checked;};
    function initial(){return {organization_id:selected.organization_id,root_process:ref(selected.process),title:'Scenario contributions',currency:'USD',links:[],disjoint_scopes_reviewed:false};}
    const writable=()=>view?.can_write&&selected.permissions.edit&&(!view.artifact||view.artifact.revision===view.latest_revision);
    function changed(){dirty=true;draft.disjoint_scopes_reviewed=false;if(confirm)confirm.checked=false;clearPortfolio();const c=document.getElementById('benefitScopes');if(c)c.checked=false;update();}
    function update(){if(save)save.disabled=!writable()||!confirm.checked;if(exportPlan)exportPlan.disabled=dirty||!view?.artifact||!view.integrity_ok;}
    async function load(ok,revision){const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/benefits'+(revision?'?revision='+revision:''));if(!ok())return;view=r;draft=structuredClone(r.artifact?.plan||initial());dirty=false;render();note(!r.integrity_ok?'Benefit integrity failed. An owner may erase history; no content is displayed.':r.artifact?'Opened benefit plan revision '+r.artifact.revision+'.':'No benefit plan saved. Nothing is shared until you save it.');}
    async function loadChoices(ok,after=''){const r=await call('/v1/human/processes?limit=100&after='+encodeURIComponent(after));if(!ok())return;choices=r.processes;choiceAfter=r.next_after||'';const picker=document.getElementById('benefitChoice');options(picker,choices.map(p=>[p.workflow_id,p.title+' · r'+p.revision]));document.getElementById('benefitChoiceNext').disabled=!r.has_more;note('Loaded this process page. A chosen process must have a saved scenario; no assumptions are copied into the plan.');}
    function saveFile(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'}));const a=el('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url);}
    function render(){
      if(form)form.remove();form=el('fieldset');form.id='benefitEditor';form.append(el('legend','Versioned benefit plan'));
      if(!view.integrity_ok){const erase=button('Erase all benefit history','benefitDelete',()=>work(remove));erase.disabled=!view.can_delete;form.append(erase);host.append(form);return;}
      const title=input('benefitTitle');title.value=draft.title;title.disabled=!writable();title.oninput=()=>{draft.title=title.value;changed();};
      const money=el('select');money.id='benefitCurrency';options(money,['USD','EUR','GBP','SEK'].map(s=>[s,s]));money.value=draft.currency;money.disabled=!writable();money.onchange=()=>{draft.currency=money.value;changed();};
      form.append(label('Benefit plan title',title),label('Benefit plan currency',money),el('p','Root pin: r'+draft.root_process.revision+' · '+draft.root_process.content_sha256));
      if(writable())form.append(button('Refresh benefit root reference','benefitRefreshRoot',()=>{draft.root_process=ref(selected.process);changed();render();}));
      const list=el('ol');list.id='benefitLinks';
      for(const link of draft.links){const item=el('li'),info=view.resolutions.find(r=>r.link_id===link.id&&key(r)===key(link.scenario_process));item.dataset.linkId=link.id;
        item.append(el('p',link.id+' · '+(info?.title||link.scenario_process.workflow_id)+' · r'+link.scenario_process.revision+' · '+(info?info.state+' · review '+(info.independent_model_review||'unknown'):'edited reference')),
          button('Open scenario '+link.id,'benefitOpenLink_'+link.id,()=>work(()=>open(link.scenario_process.workflow_id,link.scenario_process.revision))));
        const include=checkbox('benefitInclude_'+link.id);include.checked=link.include_in_estimate;include.disabled=!writable();include.onchange=()=>{link.include_in_estimate=include.checked;changed();};
        const scope=input('benefitScope_'+link.id);scope.maxLength=80;scope.value=link.benefit_scope_id;scope.disabled=!writable();scope.oninput=()=>{link.benefit_scope_id=scope.value;changed();};item.append(label('Include scenario '+link.id+' in estimate',include),label('Benefit scope key '+link.id,scope));
        if(writable())item.append(button('Remove contribution '+link.id,'benefitRemove_'+link.id,()=>{draft.links=draft.links.filter(r=>r.id!==link.id);changed();render();}),button('Refresh scenario reference '+link.id,'benefitRefresh_'+link.id,()=>work(async ok=>{const r=await call('/v1/human/processes/'+encodeURIComponent(link.scenario_process.workflow_id));if(!ok())return;if(r.archived||!r.process.process_model.opportunity_scenario)throw new Error('Choose an active process with a saved scenario.');link.scenario_process=ref(r.process);changed();render();})));
        list.append(item);
      }form.append(list);
      if(writable()){
        const picker=el('select');picker.id='benefitChoice';options(picker,choices.map(p=>[p.workflow_id,p.title+' · r'+p.revision]));const next=button('Next benefit process page','benefitChoiceNext',()=>work(ok=>loadChoices(ok,choiceAfter)));next.disabled=!choiceAfter;
        form.append(button('Load scenario process choices','benefitChoices',()=>work(loadChoices)),next,label('Scenario process to link',picker),button('Add scenario contribution','benefitAdd',()=>work(async ok=>{if(draft.links.length>=20)throw new Error('Use at most twenty links.');const choice=choices.find(p=>p.workflow_id===picker.value);if(!choice)throw new Error('Load and choose a process.');if(draft.links.some(l=>l.scenario_process.workflow_id===choice.workflow_id))throw new Error('This scenario process is already linked.');const r=await call('/v1/human/processes/'+encodeURIComponent(choice.workflow_id));if(!ok())return;if(r.archived||!r.process.process_model.opportunity_scenario)throw new Error('Choose an active process with a saved scenario.');let i=1;while(draft.links.some(l=>l.id==='link_'+i))i++;draft.links.push({id:'link_'+i,scenario_process:ref(r.process),include_in_estimate:false,benefit_scope_id:'scope_'+i});changed();render();})));
      }
      const scopes=checkbox('benefitScopes');scopes.checked=draft.disjoint_scopes_reviewed;scopes.disabled=!writable();
      // Preserve explicit unchecking as well as confirmation; no programmatic approval.
      scopes.onchange=()=>{const checked=scopes.checked;changed();draft.disjoint_scopes_reviewed=checked;scopes.checked=checked;};
      confirm=checkbox('benefitConfirm');confirm.disabled=!writable();confirm.onchange=update;
      save=button('Save reviewed contribution plan','benefitSave',()=>work(async ok=>{await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/benefits',{plan:structuredClone(draft),expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:confirm.checked,organization_sharing_confirmed:confirm.checked});if(!ok())return;clearPortfolio();await load(ok);if(ok())note('Benefit plan saved. Process models, review and execution authority were not changed.');}));
      exportPlan=button('Download saved benefit manifest','benefitExport',()=>work(async ok=>{if(dirty||!view.artifact)return;const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/benefits/export?revision='+view.artifact.revision);if(ok())saveFile(r,'owg-process-benefit-plan.json');}));
      form.append(label('I reviewed the distinct included scope keys and declare those benefit scopes disjoint',scopes),label('I reviewed this exact plan and explicitly confirm sharing its title, scope keys and pinned scenario links with organization process readers',confirm),save,exportPlan);
      const history=el('select');history.id='benefitRevision';options(history,Array.from({length:view.latest_revision},(_,i)=>[String(i+1),'Benefit revision '+(i+1)]));if(view.artifact)history.value=String(view.artifact.revision);
      const erase=button('Erase all benefit history','benefitDelete',()=>work(remove));erase.disabled=!view.can_delete||!view.latest_revision;
      form.append(label('Saved benefit plan history',history),button('Open benefit plan revision','benefitHistory',()=>work(ok=>load(ok,Number(history.value)))),erase);
      if(view.artifact){const result=el('div');result.id='benefitEstimate';result.setAttribute('role','status');totals(result,view);result.prepend(el('p','Saved plan revision '+view.artifact.revision+' only; unsaved edits are excluded. References checked at '+view.checked_at+'. Refresh after source-design changes.'));form.append(result);}
      host.append(form);update();if(busy)form.disabled=true;
    }
    async function remove(ok){if(!window.confirm('Erase every benefit plan revision for this root? Process models remain.'))return;await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/benefits/delete',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(ok()){clearPortfolio();await load(ok);if(ok())note('Benefit history erased. Process models remain.');}}
    return {clear,select};
  }
  window.owgProcessBenefits=Object.freeze({create});
})();
