/* Shared human procurement review. Raw bundles are explicitly submitted only. */
(()=>{
 'use strict';
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
 const button=(text,fn)=>{const n=el('button',text);n.type='button';n.onclick=fn;return n;};
 const label=(text,input)=>{const n=el('label',text+' ');n.append(input);return n;};
 const words=v=>String(v).replaceAll('_',' ');
 function create({inventoryHost,detailHost,call,open,onDenied}){
  let generation=0,selected=null,busy=false,view=null,after='',portfolioEpoch=0;
  const list=el('div'),status=el('p'),next=button('Next procurement page',()=>portfolio(after));
  next.disabled=true;status.setAttribute('role','status');
  inventoryHost.append(el('h2','Shared AI workforce procurement'),el('p','Tenant-scoped, evidence-pinned rehearsal decisions. Not production approval or actual savings.'),
    button('Load procurement portfolio',()=>portfolio('')),next,status,list);
  async function portfolio(cursor){
   const ticket=++portfolioEpoch;list.replaceChildren();next.disabled=true;
   try{
    const result=await call('/v1/human/process-procurement-portfolio?'+new URLSearchParams({after:cursor,limit:'50'}));
    if(ticket!==portfolioEpoch)return;
    for(const item of result.items){
     const row=el('p');row.append(button(item.workflow_id,()=>open(item.workflow_id)),el('span',' · '+words(item.state)+(item.candidate_id?' · '+item.candidate_id:'')+
        (item.blockers?.length?' · '+item.blockers.map(words).join(', '):'')));list.append(row);
    }
    if(!result.items.length)list.append(el('p','No procurement summaries in this page.'));
    after=result.next_after||'';next.disabled=!result.has_more;status.textContent='Loaded '+result.items.length+' records.';
   }catch(error){if(ticket===portfolioEpoch&&!error.staleIdentity){status.textContent=error.message;if(error.status===403)onDenied();}}
  }
  function clear(){generation++;portfolioEpoch++;selected=null;view=null;busy=false;after='';list.replaceChildren();detailHost.replaceChildren();status.textContent='';next.disabled=true;}
  function select(data){generation++;selected=data;view=null;busy=false;render();}
  const path=()=>'/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/procurement';
  async function work(fn){
   if(!selected||busy)return;
   const ticket=generation;busy=true;
   const current=()=>ticket===generation;
   try{await fn(current);}catch(error){if(current()&&!error.staleIdentity){const n=detailHost.querySelector('[role=status]');if(n)n.textContent=error.message;if(error.status===403)onDenied();}}
   finally{if(current())busy=false;}
  }
  async function reload(current){const result=await call(path());if(current()){view=result;render();}}
  function render(){
   detailHost.replaceChildren();if(!selected)return;
   const notice=el('p');notice.setAttribute('role','status');
   detailHost.append(el('h3','Shared candidate procurement'),el('p','Only minimized assessment summaries are stored. Reviewers must resubmit the exact assessment bundle; actor permissions are checked on the server. No execution authority.'),button('Load shared procurement review',()=>work(reload)),notice);
   if(!view)return;
   const item=view.artifact;
   if(item){
    const s=item.summary;detailHost.append(el('p','Revision '+item.revision+' · '+words(item.state)+' · candidate '+s.candidate.candidate_id),
      el('p','Submitted by '+item.submitted_by_actor_id+(item.decision_actor_id?' · decision by '+item.decision_actor_id:'')),
      el('p',view.blockers.length?'Blocked: '+view.blockers.map(words).join(', '):'No current design/access blocker.'));
    for(const key of ['discovery','holdout']){
     const data=s[key],section=el('section');section.append(el('h4',words(key)+' · '+data.distinct_cases+' distinct cases'),el('p',data.paired_attempts+' paired attempts'));
     const checks=el('ul');for(const [k,v] of Object.entries(data.checks))checks.append(el('li',words(k)+': '+v));section.append(checks);detailHost.append(section);
    }
    detailHost.append(button('Download minimized shared summary',()=>{
     const url=URL.createObjectURL(new Blob([JSON.stringify(item,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-shared-procurement.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));
   }
   const currentDesign=!selected.archived&&!selected.viewing_historical_revision&&selected.review.status==='reviewed_design';
   if((view.permissions.submit&&currentDesign)||view.may_decide){
    const file=el('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Exact assessment bundle');
    const confirm=el('input');confirm.type='checkbox';
    file.onchange=()=>{confirm.checked=false;};
    detailHost.append(label('Exact assessment bundle',file),label('I explicitly confirm this evidence review and organization summary sharing',confirm));
    async function read(current){
     if(!confirm.checked||!file.files?.[0]||file.files[0].size>8*1024*1024)throw Error('Choose the exact assessment bundle (up to 8 MiB) and confirm.');
     const text=await file.files[0].text();return current()?text:null;
    }
    if(view.permissions.submit&&currentDesign)detailHost.append(button('Submit shared assessment',()=>work(async current=>{
     const bundle=await read(current);if(bundle===null)return;
     await call(path(),{process_revision:selected.process.revision,expected_procurement_revision:view.latest_revision,expected_access_revision:view.access_revision,
        bundle,user_confirmed:true,organization_sharing_confirmed:true});if(current())await reload(current);
    })));
    if(view.may_decide){
     const disposition=el('select');disposition.setAttribute('aria-label','Procurement decision');
     for(const [v,t] of [['defer','Defer'],['retain_baseline','Retain baseline'],['approve_rehearsal_review','Approve for rehearsal review only']]){
      const opt=el('option',t);opt.value=v;if(v==='approve_rehearsal_review'&&!item.summary.eligible_for_rehearsal_review)opt.disabled=true;disposition.append(opt);
     }
     detailHost.append(label('Procurement decision',disposition),button('Record independent actor decision',()=>work(async current=>{
      const bundle=await read(current);if(bundle===null)return;
      await call(path()+'/decision',{expected_procurement_revision:view.latest_revision,expected_access_revision:view.access_revision,
         expected_assessment_sha256:item.summary.assessment_sha256,decision:disposition.value,bundle,user_confirmed:true});if(current())await reload(current);
     })));
    }
   }
   if(item&&view.current_approved_rehearsal_review){
    const file=el('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Exact local selection decision bundle');
    const pin=el('input');pin.type='text';pin.maxLength=64;pin.setAttribute('aria-label','Local selection receipt SHA-256');
    const consent=el('input');consent.type='checkbox';const resultHost=el('div');
    const reset=()=>{consent.checked=false;resultHost.replaceChildren();};file.onchange=reset;pin.oninput=reset;
    detailHost.append(el('h4','Check approved local substitution handoff'),
      el('p','Checks the exact local selection against the current authenticated shared decision. This is a point-in-time check, not a transferable approval or execution grant.'),
      label('Exact local selection decision bundle',file),label('Local selection receipt SHA-256',pin),
      label('I confirm this exact bundle may be inspected by the organization Gateway',consent),
      button('Check current shared approval for this selection',()=>work(async current=>{
       resultHost.replaceChildren();
       if(!consent.checked||!file.files?.[0]||file.files[0].size>8*1024*1024||!/^[a-f0-9]{64}$/.test(pin.value))throw Error('Choose the exact decision bundle, receipt hash and confirm.');
       const expectedPin=pin.value;const selectedFile=file.files[0];const bundle=await selectedFile.text();
       if(!current()||file.files?.[0]!==selectedFile||pin.value!==expectedPin||!consent.checked)return;
       const result=await call(path()+'/check-handoff',{expected_procurement_revision:item.revision,
          expected_access_revision:view.access_revision,expected_approval_sha256:item.content_sha256,
          expected_selection_receipt_sha256:expectedPin,bundle,user_confirmed:true});
       if(!current()||file.files?.[0]!==selectedFile||pin.value!==expectedPin||!consent.checked)return;
       resultHost.append(el('p','Current shared rehearsal approval matched at '+result.checked_at+'. No execution authorized.'),
        button('Download point-in-time handoff check',()=>{
         const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
         const a=el('a');a.href=url;a.download='owg-shared-procurement-handoff.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        }));consent.checked=false;
      })),resultHost);
   }
   if(item&&view.permissions.delete){
    const confirm=el('input');confirm.type='checkbox';
    detailHost.append(label('Confirm deletion of all shared procurement revisions',confirm),button('Delete procurement history',()=>work(async current=>{
     if(!confirm.checked)throw Error('Confirm deletion first.');
     await call(path()+'/delete',{expected_procurement_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(current())await reload(current);
    })));
   }
  }
  return {select,clear};
 }
 window.owgSharedProcurement={create};
})();
