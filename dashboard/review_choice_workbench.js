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
    // A review is bound to the exact choice; switching choices must invalidate it.
    choice.addEventListener('change',()=>{generation++;details.replaceChildren();review.disabled=!snapshot;status.textContent='Project changed. Review the newly selected export before proceeding.';});
    const clear=()=>{snapshot=null;choice.replaceChildren();results.replaceChildren();details.replaceChildren();review.disabled=true;};
    const validProjects=(projects,revision)=>Array.isArray(projects)&&
      new Set(projects.map(p=>p.project_id)).size===projects.length&&
      projects.every(p=>typeof p.project_id==='string'&&p.project_id.length>0&&
        /^[a-f0-9]{64}$/i.test(p.spec_sha256)&&/^[a-f0-9]{64}$/i.test(p.archive_sha256)&&
        Number.isSafeInteger(p.idea_revision)&&p.idea_revision>=0&&p.idea_revision<=revision&&
        Array.isArray(p.blockers)&&p.blockers.every(b=>typeof b==='string')&&
        p.execution_authorized===false&&p.review_required===true);
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
           !validProjects(data.projects,data.idea?.revision)||data.projects_truncated||data.execution_authorized!==false||
           data.source_read_authorized!==false||data.verified_business_outcomes!==false||
           !Number.isSafeInteger(data.idea?.revision)||data.idea.revision<0)
          throw new Error('Incomplete or inconsistent review basis.');
        snapshot=data;
        results.append(node('p','Opportunity revision '+data.idea.revision+' · '+data.idea.state+' · saved process '+data.process_status));
        for(const p of data.projects){const opt=node('option',p.project_id+' · idea revision '+p.idea_revision);opt.value=p.project_id;choice.append(opt);}
        if(!data.projects.length)results.append(node('p','No recorded project export. Export a reviewed project in the automation pipeline first.'));
        else {results.append(choice,review);review.disabled=false;}
        status.textContent='Choose an exact export; review is read-only.';
      }catch(e){if(current===generation)status.textContent=e.message;}
      finally{if(current===generation)load.disabled=false;}
    };
    review.onclick=async()=>{
      const id=input.value.trim(),projectId=choice.value,selected=snapshot?.projects.find(p=>p.project_id===projectId);
      if(!selected)return;
      const current=++generation;review.disabled=true;details.replaceChildren();status.textContent='Rechecking current revisions…';
      try{
        const fresh=await request('/v1/review-choice-workbench/'+encodeURIComponent(id));
        if(current!==generation||choice.value!==projectId||input.value.trim()!==id)return;
        const candidate=fresh.projects?.find(p=>p.project_id===projectId);
        if(fresh.format!=='openworkgraph.review-choices.v1'||fresh.idea?.idea_id!==id||
           fresh.execution_authorized!==false||fresh.source_read_authorized!==false||
           fresh.verified_business_outcomes!==false||!validProjects(fresh.projects,fresh.idea?.revision)||
           fresh.projects_truncated||fresh.idea?.revision!==snapshot.idea.revision||
           fresh.idea?.state!==snapshot.idea.state||
           fresh.process_status!==snapshot.process_status||
           JSON.stringify(fresh.process_reference)!==JSON.stringify(snapshot.process_reference)||
           JSON.stringify(fresh.dataset_origins)!==JSON.stringify(snapshot.dataset_origins)||
           fresh.dataset_origin_check_available!==snapshot.dataset_origin_check_available||!candidate||
           JSON.stringify(candidate)!==JSON.stringify(selected)||
           JSON.stringify(fresh.projects)!==JSON.stringify(snapshot.projects)){
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
        const confirm=node('button','Revalidate review basis on server');confirm.type='button';
        confirm.onclick=async()=>{
          const token=++generation;confirm.disabled=true;
          status.textContent='Checking exact export against current local records…';
          try{
            await window.__owgAuthReady;
            const response=await fetch('/v1/review-choice-workbench/'+encodeURIComponent(id)+'/preflight',{
              method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({project_id:projectId,idea_revision:snapshot.idea.revision,
                spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256})
            });
            if(token!==generation||choice.value!==projectId||input.value.trim()!==id)return;
            if(!response.ok)throw new Error('Server review preflight refused ('+response.status+').');
            const result=await response.json();
            if(result.format!=='openworkgraph.review-preflight.v1'||result.idea_id!==id||
               result.idea_revision!==snapshot.idea.revision||result.project_id!==projectId||
               result.spec_sha256!==selected.spec_sha256||result.archive_sha256!==selected.archive_sha256||
               result.execution_authorized!==false||result.source_read_authorized!==false||
               result.verified_business_outcomes!==false||result.review_required!==true||
               JSON.stringify(result.blockers)!==JSON.stringify(selected.blockers))
              throw new Error('Inconsistent server review evidence.');
            status.textContent='Current export basis confirmed by server. Human approval and agent handoff are still required.';
          }catch(e){if(token===generation){clear();status.textContent='Server preflight failed. Inspect again. '+e.message;}}
        };
        details.append(confirm);
        status.textContent='Read-only choice inspected against current local records.';
      }catch(e){if(current===generation){clear();status.textContent='Review failed. Inspect current records again before choosing. '+e.message;}}
      finally{if(current===generation&&snapshot&&choice.value===projectId)review.disabled=false;}
    };
    card.append(heading,intro,label,input,load,status,results,details);panel.append(card);
  }
  document.addEventListener('DOMContentLoaded',mount);
})();
