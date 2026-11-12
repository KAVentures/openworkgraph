/* Human-only review of user-supplied paired process value measurements.
 * Descriptive comparisons are never independently verified realized savings.
 */
(() => {
  'use strict';
  const el=(name,content)=>{
    const node=document.createElement(name);
    if(content!==undefined)node.textContent=String(content);
    return node;
  };
  const label=(name,input)=>{const node=el('label',name+' ');node.append(input);return node;};
  const input=(type,id,value='')=>{
    const node=el(type==='textarea'?'textarea':'input');
    if(type!=='textarea')node.type=type;
    node.id=id;node.value=value;return node;
  };
  const dateValue=iso=>{if(!iso)return '';const d=new Date(iso);if(!Number.isFinite(d.getTime()))return '';return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
  const dateText=local=>{
    if(!local)return '';
    // Explicit local offset avoids silently declaring unspecified UTC in saved evidence.
    const d=new Date(local);
    return Number.isFinite(d.getTime())?d.toISOString():'';
  };
  const digestLines=value=>String(value).trim().split(/\s+/).filter(Boolean);
  const fields=(parent,prefix,defaults)=>{
    const group=el('fieldset');group.append(el('legend',prefix==='baseline'?'Baseline window':'Intervention window'));
    const start=input('datetime-local','value_'+prefix+'_start',dateValue(defaults?.started_at));
    const end=input('datetime-local','value_'+prefix+'_end',dateValue(defaults?.ended_at));
    const count=input('number','value_'+prefix+'_cases',defaults?.case_count??'');
    count.min='1';count.max='1000000';count.step='1';count.required=true;
    const minutes=input('text','value_'+prefix+'_minutes',defaults?.total_handling_minutes??'');
    minutes.maxLength=20;minutes.required=true;
    const receipt=input('textarea','value_'+prefix+'_receipts',(defaults?.receipt_sha256||[]).join('\n'));
    receipt.rows=3;receipt.maxLength=1400;receipt.required=true;
    const coverage=el('select');coverage.id='value_'+prefix+'_coverage';
    for(const value of ['unknown','partial','complete']) {
      const option=el('option',value);option.value=value;coverage.append(option);
    }
    coverage.value=defaults?.coverage||'unknown';
    group.append(label('Window starts (device local time)',start),
      label('Window ends (device local time)',end),
      label('Number of cases',count),
      label('Total handling minutes (decimal)',minutes),
      label('SHA-256 receipt fingerprints (one per line, no source text)',receipt),
      label('Declared completeness',coverage));
    parent.append(group);
    return ()=>({
      started_at:dateText(start.value),ended_at:dateText(end.value),
      case_count:Number(count.value),total_handling_minutes:minutes.value.trim(),
      receipt_sha256:digestLines(receipt.value),coverage:coverage.value,
      provenance:'user_supplied_aggregates_unverified'
    });
  };
  function create({detailHost,call,onDenied}) {
    let epoch=0,selected=null,view=null,busy=false,form=null,status=null,confirm=null,saveButton=null;
    let readBaseline=null,readIntervention=null,readTitle=null,readCurrency=null,readRate=null;
    const host=el('section');host.setAttribute('aria-label','Before and after process value measurements');
    detailHost.append(host);
    function clear(){epoch++;selected=view=null;busy=false;host.replaceChildren();form=status=confirm=saveButton=null;}
    function note(message){if(status)status.textContent=message;}
    async function work(fn) {
      if(busy||!selected)return;
      const observed=epoch;busy=true;if(form)form.disabled=true;
      try{await fn(()=>observed===epoch);}
      catch(err) {
        if(observed!==epoch)return;
        if(err.status===401||err.status===403){clear();onDenied();return;}
        note(err.message||'Value measurement request failed.');
      } finally {if(observed===epoch){busy=false;if(form)form.disabled=false;}}
    }
    function selectedRef(){
      const p=selected.process;
      return {workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256};
    }
    function render() {
      if(!selected||!status)return;
      const area=el('div');
      area.id='valueMeasurementContent';
      const artifact=view?.artifact;
      const immutable=artifact?.measurement;
      if(view){
        area.append(el('p','Measurement status: '+String(view.status||'unknown').replaceAll('_',' ')));
        if(artifact){
          area.append(el('p','Saved measurement revision '+artifact.revision+' · SHA-256 '+artifact.content_sha256));
          const comparison=view.comparison;
          if(comparison?.comparison) {
            const details=el('dl');
            for(const [key,value] of Object.entries(comparison.comparison)){
              details.append(el('dt',key.replaceAll('_',' ')),el('dd',value));
            }
            area.append(details);
          } else if(comparison?.blockers?.length) {
            area.append(el('p','Comparison withheld: '+comparison.blockers.join(', ').replaceAll('_',' ')));
          }
          area.append(el('p','No provider receipt was authenticated. Numbers are human-supplied and descriptive, never proven savings or causal impact.'));
          const download=el('button','Export this saved revision (JSON)');download.type='button';
          download.onclick=()=>work(async still=>{
            const current=selected.process.workflow_id;
            const result=await call('/v1/human/processes/'+encodeURIComponent(current)+'/value-measurements?revision='+artifact.revision);
            if(!still())return;
            const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});
            const url=URL.createObjectURL(blob);
            const link=el('a');link.href=url;link.download='owg-process-value-measurement.json';link.click();
            URL.revokeObjectURL(url);
          });
          area.append(download);
        }
      }
      const isCurrent=selected.process.revision===selected.latest_revision;
      const canEdit=!!selected.permissions?.edit && isCurrent && view?.can_write && view?.integrity_ok;
      if(!canEdit) {
        area.append(el('p','This measurement cannot be changed with the current process role, revision or integrity state.'));
        host.append(area);return;
      }
      form=el('fieldset');form.id='valueMeasurementForm';
      form.append(el('legend','Save a reviewed before/after comparison'),
        el('p','Enter aggregate counts and decimal minutes only. Do not enter names, case IDs, messages or raw provider records. SHA-256 fingerprints refer to independent source receipts but are not verified by OWG.'));
      readTitle=input('text','value_title',immutable?.title||'Before and after comparison');
      readTitle.maxLength=120;readTitle.required=true;
      readCurrency=el('select');
      for(const value of ['USD','EUR','GBP','SEK']){const opt=el('option',value);opt.value=value;readCurrency.append(opt);}
      readCurrency.value=immutable?.currency||'USD';
      readRate=input('number','value_hourly_minor',immutable?.assumed_hourly_cost_minor??'');
      readRate.min='0';readRate.max='100000000';readRate.step='1';readRate.required=true;
      form.append(label('Comparison title',readTitle),label('Currency',readCurrency),
        label('Assumed hourly labor cost in minor currency units (e.g., cents/öre)',readRate));
      readBaseline=fields(form,'baseline',immutable?.baseline);
      readIntervention=fields(form,'intervention',immutable?.intervention);
      const cohort=input('checkbox','value_cohorts');
      cohort.checked=false;
      confirm=input('checkbox','value_confirm');confirm.checked=false;
      form.append(label('I reviewed the cohorts, their definitions and period comparability (not independent verification)',cohort),
        label('I explicitly reviewed and approve sharing these aggregates within the organization',confirm));
      saveButton=el('button',artifact?'Save new measurement revision':'Save first measurement');
      saveButton.type='button';saveButton.disabled=true;
      const update=()=>{saveButton.disabled=!confirm.checked||busy;};
      confirm.onchange=update;
      for(const element of [readTitle,readCurrency,readRate])element.oninput=()=>{confirm.checked=false;update();};
      form.oninput=(event)=>{if(event.target!==confirm){confirm.checked=false;update();}};
      saveButton.onclick=()=>work(async still=>{
        const m={organization_id:selected.organization_id,root_process:selectedRef(),
          title:readTitle.value.trim(),currency:readCurrency.value,
          baseline:readBaseline(),intervention:readIntervention(),
          assumed_hourly_cost_minor:Number(readRate.value),
          periods_and_case_cohorts_reviewed:cohort.checked,
          independent_source_receipts_verified:false,realized_savings_verified:false,
          causal_effect_established:false,execution_authorized:false};
        if(!confirm.checked)throw new Error('Review and confirm organization sharing before saving.');
        const wid=selected.process.workflow_id;
        const result=await call('/v1/human/processes/'+encodeURIComponent(wid)+'/value-measurements',{
          measurement:m,expected_revision:view.latest_revision,
          expected_access_revision:view.access_revision,user_confirmed:true,
          organization_sharing_confirmed:true
        });
        if(!still())return;
        view=await call('/v1/human/processes/'+encodeURIComponent(wid)+'/value-measurements');
        if(!still())return;
        note('Saved measurement revision '+result.revision+'. Comparison is descriptive, not verified savings.');
        form=null;host.querySelector('#valueMeasurementContent')?.remove();host.querySelector('#valueMeasurementForm')?.remove();
        render();
      });
      form.append(saveButton);
      host.append(area,form);
    }
    function select(data){
      clear();selected=data;status=el('p');status.id='valueMeasurementStatus';
      status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      const load=el('button','Load value measurement');
      load.type='button';load.id='valueMeasurementLoad';
      load.onclick=()=>work(async still=>{
        const id=selected.process.workflow_id;
        const data=await call('/v1/human/processes/'+encodeURIComponent(id)+'/value-measurements');
        if(!still())return;
        view=data;
        host.querySelector('#valueMeasurementContent')?.remove();
        host.querySelector('#valueMeasurementForm')?.remove();
        form=null;render();
        note(data.artifact?'Opened saved before/after measurement.':'No measurement saved. You can create a reviewed aggregate comparison.');
      });
      host.append(el('h3','Before and after handling time'),
        el('p','Optional R16 value measurement for this selected process. Descriptive comparison only; not independently verified savings.'),
        load,status);
    }
    return {select,clear};
  }
  window.owgProcessValueMeasurements={create};
})();
