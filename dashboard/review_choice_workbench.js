/* Local authenticated reviewer choice workbench. No packet uploads or action grants. */
(() => {
  'use strict';
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  function mount(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgReviewChoiceWorkbench'))return;
    const card=node('section');card.className='card';card.id='owgReviewChoiceWorkbench';
    const heading=node('h2','Review a handoff choice');
    const intro=node('p','Inspect a recorded opportunity and choose an exact project for human review. This does not authorize an agent, read a provider, or verify a business outcome.');
    intro.className='muted';
    const label=node('label','Opportunity ID');const input=node('input');input.id='owgReviewChoiceId';input.maxLength=128;label.htmlFor=input.id;
    const load=node('button','Inspect choices');load.type='button';
    const status=node('p','Enter an existing opportunity ID.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const results=node('div');const choice=node('select');choice.setAttribute('aria-label','Exact project for human review');
    const review=node('button','Review selected project');review.type='button';review.disabled=true;
    const details=node('div');let snapshot=null;let generation=0;
    const clear=()=>{snapshot=null;choice.replaceChildren();results.replaceChildren();details.replaceChildren();review.disabled=true;};
    input.addEventListener('input',()=>{generation++;clear();status.textContent='Selection changed. Inspect again before reviewing.';});
    async function request(path){
      await window.__owgAuthReady;
      const response=await fetch(path,{cache:'no-store'});
      if(!response.ok)throw new Error('Review inspection unavailable ('+response.status+').');
      return response.json();
    }
    load.onclick=async()=>{
      const id=input.value.trim();const current=++generation;clear();
      if(!id){status.textContent='Enter an opportunity ID.';return;}
      load.disabled=true;status.textContent='Inspecting current local records…';
      try{
        const data=await request('/v1/review-choice-workbench/'+encodeURIComponent(id));
        if(current!==generation)return;
        if(data.format!=='openworkgraph.review-choices.v1'||data.idea?.idea_id!==id||
           !Array.isArray(data.projects)||data.projects_truncated||data.execution_authorized!==false||
           data.source_read_authorized!==false||data.verified_business_outcomes!==false||
           !data.projects.every(p=>typeof p.project_id==='string'&&p.project_id.length>0&&
             Array.isArray(p.blockers)&&p.execution_authorized===false&&p.review_required===true))
          throw new Error('Incomplete or inconsistent review basis.');
        snapshot=data;
        results.append(node('p','Opportunity revision '+data.idea.revision+' · '+data.idea.state+' · saved process '+data.process_status));
        for(const p of data.projects){const opt=node('option',p.project_id+' · idea revision '+p.idea_revision);opt.value=p.project_id;choice.append(opt);}
        if(!data.projects.length)results.append(node('p','No recorded project export. Export a reviewed project in the automation pipeline first.'));
        else {results.append(choice,review);review.disabled=false;}
        status.textContent='Choose an exact export; review is read-only.';
      }catch(e){if(current===generation)status.textContent=e.message;}
      finally{load.disabled=false;}
    };
    review.onclick=async()=>{
      const id=input.value.trim(),projectId=choice.value,selected=snapshot?.projects.find(p=>p.project_id===projectId);
      if(!selected)return;
      const current=++generation;review.disabled=true;details.replaceChildren();status.textContent='Rechecking current revisions…';
      try{
        const fresh=await request('/v1/review-choice-workbench/'+encodeURIComponent(id));
        if(current!==generation)return;
        const candidate=fresh.projects?.find(p=>p.project_id===projectId);
        if(fresh.format!=='openworkgraph.review-choices.v1'||fresh.idea?.idea_id!==id||
           fresh.execution_authorized!==false||fresh.source_read_authorized!==false||
           fresh.verified_business_outcomes!==false||!Array.isArray(fresh.projects)||
           fresh.projects_truncated||fresh.idea?.revision!==snapshot.idea.revision||
           fresh.process_status!==snapshot.process_status||
           JSON.stringify(fresh.process_reference)!==JSON.stringify(snapshot.process_reference)||
           JSON.stringify(fresh.dataset_origins)!==JSON.stringify(snapshot.dataset_origins)||
           fresh.dataset_origin_check_available!==snapshot.dataset_origin_check_available||!candidate||
           JSON.stringify(candidate)!==JSON.stringify(selected)){
          clear();status.textContent='Review basis changed. Inspect again before choosing.';return;
        }
        details.append(node('h3','Human review · exact recorded project'),node('p','Project '+projectId),
          node('p','Specification SHA-256: '+selected.spec_sha256),
          node('p','Archive SHA-256: '+selected.archive_sha256));
        const blockers=node('ul');
        for(const blocker of selected.blockers)blockers.append(node('li',blocker.replaceAll('_',' ')));
        blockers.append(node('li','Independent execution and business outcomes remain unverified'));
        details.append(node('p','Outstanding review requirements:'),blockers,
          node('p','This selection is not an execution approval or a provider source read.'));
        status.textContent='Read-only choice inspected against current local records.';
      }catch(e){if(current===generation){clear();status.textContent='Review failed. Inspect current records again before choosing. '+e.message;}}
      finally{if(current===generation&&snapshot)review.disabled=false;}
    };
    card.append(heading,intro,label,input,load,status,results,details);panel.append(card);
  }
  document.addEventListener('DOMContentLoaded',mount);
})();
