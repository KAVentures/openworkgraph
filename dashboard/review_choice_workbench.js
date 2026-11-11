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
    const validProjects=(projects,revision)=>Array.isArray(projects)&&projects.length<=64&&
      new Set(projects.map(p=>p.project_id)).size===projects.length&&
      projects.every(p=>typeof p.project_id==='string'&&p.project_id.length>0&&
        /^[a-f0-9]{64}$/i.test(p.spec_sha256)&&/^[a-f0-9]{64}$/i.test(p.archive_sha256)&&
        Number.isSafeInteger(p.idea_revision)&&p.idea_revision>=0&&p.idea_revision<=revision&&
        Array.isArray(p.blockers)&&p.blockers.every(b=>typeof b==='string')&&
        p.execution_authorized===false&&p.review_required===true);
    input.addEventListener('input',()=>{generation++;clear();load.disabled=false;status.textContent='Selection changed. Inspect again before reviewing.';});
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
           !validProjects(data.projects,data.idea?.revision)||!/^[a-f0-9]{64}$/i.test(data.project_set_sha256)||data.projects_truncated||data.execution_authorized!==false||
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
           fresh.projects_truncated||fresh.project_set_sha256!==snapshot.project_set_sha256||fresh.idea?.revision!==snapshot.idea.revision||
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
              body:JSON.stringify({project_id:projectId,project_set_sha256:snapshot.project_set_sha256,idea_revision:snapshot.idea.revision,idea_state:snapshot.idea.state,
                spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256,
                process_status:snapshot.process_status,process_reference:snapshot.process_reference,
                dataset_origins:snapshot.dataset_origins,
                dataset_origin_check_available:snapshot.dataset_origin_check_available,
                blockers:selected.blockers})
            });
            if(token!==generation||choice.value!==projectId||input.value.trim()!==id)return;
            if(!response.ok)throw new Error('Server review preflight refused ('+response.status+').');
            const result=await response.json();
            if(result.format!=='openworkgraph.review-preflight.v1'||result.idea_id!==id||
               result.idea_revision!==snapshot.idea.revision||result.project_id!==projectId||
               result.project_set_sha256!==snapshot.project_set_sha256||
               result.spec_sha256!==selected.spec_sha256||result.archive_sha256!==selected.archive_sha256||
               result.execution_authorized!==false||result.source_read_authorized!==false||
               result.verified_business_outcomes!==false||result.review_required!==true||
               JSON.stringify(result.blockers)!==JSON.stringify(selected.blockers))
              throw new Error('Inconsistent server review evidence.');
            // Continue into the existing authenticated pipeline decision UI, not a
            // second approval implementation. Preflight is non-authorizing; the
            // pipeline requires its own current-revision check on any decision.
            const continueReview=node('button','Open opportunity decision and handoff');continueReview.type='button';continueReview.disabled=true;
            continueReview.onclick=async()=>{
              if(token!==generation||choice.value!==projectId||input.value.trim()!==id||!notesMatchSaved())return;
              const pipelineCard=document.getElementById('owgPipelineCard');
              if(!pipelineCard){status.textContent='Automation pipeline unavailable. Reopen the dashboard and inspect again.';return;}
              // The opportunity can change after the first preflight. Revalidate
              // immediately before crossing into the decision surface, fail closed.
              continueReview.disabled=true;
              status.textContent='Rechecking current evidence before opening the decision…';
              try{
                await window.__owgAuthReady;
                const response=await fetch('/v1/review-choice-workbench/'+encodeURIComponent(id)+'/reviewed-handoff-preflight',{
                  method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({project_id:projectId,project_set_sha256:snapshot.project_set_sha256,idea_revision:snapshot.idea.revision,idea_state:snapshot.idea.state,
                    spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256,
                    process_status:snapshot.process_status,process_reference:snapshot.process_reference,
                    dataset_origins:snapshot.dataset_origins,
                    dataset_origin_check_available:snapshot.dataset_origin_check_available,
                    blockers:selected.blockers})
                });
                if(token!==generation||choice.value!==projectId||input.value.trim()!==id)return;
                if(!response.ok)throw new Error('Decision preflight refused ('+response.status+').');
                const result=await response.json();
                if(result.format!=='openworkgraph.reviewed-handoff-preflight.v1'||
                   result.idea_id!==id||result.project_id!==projectId||
                   result.project_set_sha256!==snapshot.project_set_sha256||
                   result.human_review_approved!==true||result.execution_authorized!==false||
                   result.source_read_authorized!==false||
                   !Number.isSafeInteger(result.review_revision)||result.review_revision!==reviewRevision||
                   currentDecision!=='approved'||!reviewStillSelected()||!notesMatchSaved())
                  throw new Error('Decision evidence changed.');
                window.dispatchEvent(new CustomEvent('owg-pipeline-open',{detail:{idea_id:id,project_id:projectId,idea_revision:snapshot.idea.revision,idea_state:snapshot.idea.state,spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256,review_basis:{project_id:projectId,project_set_sha256:snapshot.project_set_sha256,idea_revision:snapshot.idea.revision,idea_state:snapshot.idea.state,spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256,process_status:snapshot.process_status,process_reference:snapshot.process_reference,dataset_origins:snapshot.dataset_origins,dataset_origin_check_available:snapshot.dataset_origin_check_available,blockers:selected.blockers}}}));
                pipelineCard.scrollIntoView({block:'start',behavior:'auto'});
                // A reviewed navigation is single-use. Re-inspect before another handoff;
                // the pipeline itself still requires independent authorization.
                currentDecision='handoff-completed';
                status.textContent='Opened reviewed opportunity decision. Human approval verified; no agent execution access was granted.';
              }catch(e){
                if(token===generation){currentDecision='unknown';clear();status.textContent='Decision review expired. Inspect again. '+e.message;}
              }finally{
                // A failed or superseded preflight must never resurrect navigation.
                // A new authoritative inspection is required after clear().
                continueReview.disabled=!reviewStillSelected()||currentDecision!=='approved'||!notesMatchSaved();
              }
            };
            // Durable human review is a separate, non-executing decision.
            const notes=node('textarea');notes.maxLength=2000;notes.setAttribute('aria-label','Human review notes');
            const reviewState=node('p','Loading saved review…');reviewState.setAttribute('role','status');
            const saveDraft=node('button','Save review draft');
            const approve=node('button','Approve reviewed choice');
            const revoke=node('button','Revoke reviewed choice');
            for(const b of [saveDraft,approve,revoke])b.type='button';
            const reviewPath='/v1/review-choice-workbench/'+encodeURIComponent(id)+'/review';
            let reviewRevision=0;
            // The next step is only actionable for a current approved review.
            // The server independently rechecks approval and provenance.
            let currentDecision='unreviewed';
            let savedNotes='';
            // Keep navigation closed throughout any in-flight review mutation.
            let reviewMutationPending=false;
            // Unsaved edits must not appear to be part of the approved handoff.
            const notesMatchSaved=()=>notes.value===savedNotes;
            notes.addEventListener('input',()=>{continueReview.disabled=true;updateContinue();});
            const updateContinue=()=>{continueReview.disabled=reviewMutationPending||currentDecision!=='approved'||!notesMatchSaved();};
            // A pending read must never overwrite a later save/revoke result.
            // Disable mutation until the authoritative revision has loaded.
            let reviewRequestGeneration=0;
            for(const b of [saveDraft,approve,revoke])b.disabled=true;
            // Async review state belongs to the inspected choice, not to a later selection.
            const reviewGeneration=token;
            const reviewStillSelected=()=>reviewGeneration===generation&&
              input.value.trim()===id&&choice.value===projectId;
            const basis={project_id:projectId,project_set_sha256:snapshot.project_set_sha256,
              idea_revision:snapshot.idea.revision,idea_state:snapshot.idea.state,
              spec_sha256:selected.spec_sha256,archive_sha256:selected.archive_sha256,
              process_status:snapshot.process_status,process_reference:snapshot.process_reference,
              dataset_origins:snapshot.dataset_origins,
              dataset_origin_check_available:snapshot.dataset_origin_check_available,blockers:selected.blockers};
            async function refreshReview(){
              const requestGeneration=++reviewRequestGeneration;
              const response=await fetch(reviewPath+'/'+encodeURIComponent(projectId),{cache:'no-store'});
              if(!response.ok)throw new Error('Review state unavailable ('+response.status+').');
              const state=await response.json();
              if(!reviewStillSelected()||requestGeneration!==reviewRequestGeneration)return;
              if(state.format!=='openworkgraph.human-review.v1'||state.execution_authorized!==false||
                 state.idea_id!==id||state.project_id!==projectId||
                 !Number.isSafeInteger(state.revision)||state.revision<0||
                 !['unreviewed','draft','approved','revoked'].includes(state.decision)||
                 (state.revision===0)!==(state.decision==='unreviewed')||
                 (state.revision>0&&!/^[a-f0-9]{64}$/i.test(state.basis_sha256||''))||
                 typeof state.reviewer_notes!=='string'||state.reviewer_notes.length>2000)
                throw new Error('Invalid review state');
              // An authoritative refresh after a rejected mutation supersedes that mutation.\n              // Release its pending lock only after the refreshed state validates.\n              reviewMutationPending=false;\n              reviewRevision=state.revision;currentDecision=state.decision;notes.value=state.reviewer_notes||'';savedNotes=notes.value;updateContinue();
              reviewState.textContent='Saved review: '+state.decision+' (revision '+state.revision+'). No agent access granted.';
              for(const b of [saveDraft,approve,revoke])b.disabled=false;
            }
            async function persistReview(decision){
              if(!reviewStillSelected())return;
              const mutationGeneration=++reviewRequestGeneration;
              reviewMutationPending=true;
              // A second mutation or refresh supersedes this response. Never let
              // an older save resurrect approval after a later revoke.
              continueReview.disabled=true;
              for(const b of [saveDraft,approve,revoke])b.disabled=true;
              const submittedNotes=notes.value;
              try{
                const response=await fetch(reviewPath,{method:'POST',cache:'no-store',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({basis,expected_review_revision:reviewRevision,decision,reviewer_notes:submittedNotes})});
                if(!response.ok)throw new Error('Review save refused ('+response.status+'); re-inspect or reload.');
                const state=await response.json();
                if(!reviewStillSelected()||mutationGeneration!==reviewRequestGeneration)return;
                if(state.format!=='openworkgraph.human-review.v1'||state.execution_authorized!==false||
                   state.idea_id!==id||state.project_id!==projectId||state.decision!==decision||
                   !Number.isSafeInteger(state.revision)||state.revision!==reviewRevision+1||
                   !/^[a-f0-9]{64}$/i.test(state.basis_sha256||'')||
                   typeof state.reviewer_notes!=='string'||state.reviewer_notes!==submittedNotes)
                  throw new Error('Invalid saved review response');
                reviewRevision=state.revision;currentDecision=state.decision;savedNotes=submittedNotes;updateContinue();
                reviewState.textContent='Saved '+decision+' at revision '+reviewRevision+'. This does not authorize execution.';
              }catch(e){
                if(reviewStillSelected()){
                  reviewState.textContent=e.message+' Reloading authoritative review revision…';
                  try{await refreshReview();}
                  catch(refreshError){
                    currentDecision='unknown';updateContinue();
                    reviewState.textContent='Review state uncertain. Inspect again before editing: '+refreshError.message;
                    for(const b of [saveDraft,approve,revoke])b.disabled=true;
                  }
                }
              }finally{
                // A failed refresh keeps controls disabled; only a verified state
                // may re-enable them. Do not resurrect a superseded selection.
                if(mutationGeneration===reviewRequestGeneration){reviewMutationPending=false;updateContinue();}
                if(!reviewStillSelected())for(const b of [saveDraft,approve,revoke])b.disabled=true;
                else if(reviewState.textContent.startsWith('Saved ')||reviewState.textContent.startsWith('Review revoked'))
                  for(const b of [saveDraft,approve,revoke])b.disabled=false;
              }
            }
            saveDraft.onclick=()=>persistReview('draft');
            approve.onclick=()=>persistReview('approved');
            revoke.onclick=async()=>{
              if(!reviewStillSelected()||reviewRevision<1)return;
              const mutationGeneration=++reviewRequestGeneration;
              reviewMutationPending=true;
              currentDecision='unknown';continueReview.disabled=true;
              // Revocation must work when evidence has changed since approval.
              // Never depend on the stale-basis preflight for withdrawing consent.
              for(const b of [saveDraft,approve,revoke])b.disabled=true;
              try{
                const response=await fetch(reviewPath+'/'+encodeURIComponent(projectId)+'/revoke',{
                  method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({expected_review_revision:reviewRevision})});
                if(!response.ok)throw new Error('Revocation refused ('+response.status+'); reload review state.');
                const state=await response.json();
                if(!reviewStillSelected()||mutationGeneration!==reviewRequestGeneration)return;
                if(state.format!=='openworkgraph.human-review.v1'||state.decision!=='revoked'||
                   state.execution_authorized!==false||state.idea_id!==id||
                   state.project_id!==projectId||!Number.isSafeInteger(state.revision)||
                   state.revision!==reviewRevision+1||
                   !/^[a-f0-9]{64}$/i.test(state.basis_sha256||'')||
                   typeof state.reviewer_notes!=='string'||state.reviewer_notes.length>2000)
                  throw new Error('Invalid revocation response');
                reviewRevision=state.revision;currentDecision='revoked';updateContinue();
                reviewState.textContent='Review revoked at revision '+reviewRevision+'. No agent access granted.';
              }catch(e){
                if(reviewStillSelected()&&mutationGeneration===reviewRequestGeneration){
                  reviewState.textContent=e.message+' Reloading authoritative review revision…';
                  try{await refreshReview();}
                  catch(refreshError){
                    currentDecision='unknown';updateContinue();
                    reviewState.textContent='Review state uncertain. Inspect again before editing: '+refreshError.message;
                    for(const b of [saveDraft,approve,revoke])b.disabled=true;
                  }
                }
              }finally{
                // Revocation must release the mutation lock only for its own
                // authoritative request; stale responses cannot unlock handoff.
                if(mutationGeneration===reviewRequestGeneration){reviewMutationPending=false;updateContinue();}
                // A failed refresh keeps controls disabled; only a verified state
                // may re-enable them. Do not resurrect a superseded selection.
                if(!reviewStillSelected())for(const b of [saveDraft,approve,revoke])b.disabled=true;
                else if(reviewState.textContent.startsWith('Saved ')||reviewState.textContent.startsWith('Review revoked'))
                  for(const b of [saveDraft,approve,revoke])b.disabled=false;
              }
            };
            details.append(node('h3','Record human review'),notes,saveDraft,approve,revoke,reviewState);
            refreshReview().catch(e=>{if(reviewStillSelected()){reviewState.textContent=e.message;for(const b of [saveDraft,approve,revoke])b.disabled=true;}});
            details.append(continueReview);
            status.textContent='Current export basis confirmed by server. Continue to the existing opportunity decision; agent handoff still requires separate authorization.';
          }catch(e){if(token===generation){clear();status.textContent='Server preflight failed. Inspect again. '+e.message;}}
        };
        details.append(confirm);
        status.textContent='Read-only choice inspected against current local records.';
      }catch(e){if(current===generation){clear();status.textContent='Review failed. Inspect current records again before choosing. '+e.message;}}
      finally{if(current===generation&&snapshot&&choice.value===projectId)review.disabled=false;}
    };
    // Recovery must remain reachable when the original opportunity or project
    // cannot be inspected. It only reads/revokes a previously saved decision.
    const recovery=node('section');recovery.setAttribute('aria-label','Recover saved human review');
    const recoveryHeading=node('h3','Recover or revoke a saved review');
    const recoveryHelp=node('p','If source evidence disappeared, enter the saved opportunity and project IDs to retrieve its review revision and withdraw approval. No source access or execution is granted.');
    const recoveryIdeaLabel=node('label','Saved opportunity ID');
    const recoveryIdea=node('input');recoveryIdea.maxLength=128;recoveryIdeaLabel.append(recoveryIdea);
    const recoveryProjectLabel=node('label','Saved project ID');
    const recoveryProject=node('input');recoveryProject.maxLength=128;recoveryProjectLabel.append(recoveryProject);
    const recoveryLoad=node('button','Load saved review');recoveryLoad.type='button';
    const recoveryRevoke=node('button','Revoke saved review');recoveryRevoke.type='button';recoveryRevoke.disabled=true;
    const recoveryStatus=node('p','No saved review loaded.');recoveryStatus.setAttribute('role','status');recoveryStatus.setAttribute('aria-live','polite');
    let recoveryGeneration=0,recoveryRevision=null,recoveryIdentity=null;
    function resetRecovery(){
      recoveryGeneration++;recoveryRevision=null;recoveryIdentity=null;
      recoveryRevoke.disabled=true;recoveryLoad.disabled=false;
      recoveryStatus.textContent='IDs changed. Load the saved review again.';
    }
    recoveryIdea.addEventListener('input',resetRecovery);
    recoveryProject.addEventListener('input',resetRecovery);
    const recoveryPath=(idea,project)=>'/v1/review-choice-workbench/'+encodeURIComponent(idea)+'/review/'+encodeURIComponent(project);
    // Recovery is deliberately independent of missing source evidence, but
    // only a recognized, revisioned persisted decision can be revoked.
    const validRecoveryState=(state,idea,project)=>state?.format==='openworkgraph.human-review.v1'&&
      state.idea_id===idea&&state.project_id===project&&state.execution_authorized===false&&
      Number.isSafeInteger(state.revision)&&state.revision>=0&&
      ['unreviewed','draft','approved','revoked'].includes(state.decision)&&
      (state.revision===0)===(state.decision==='unreviewed')&&
      (state.revision===0||/^[a-f0-9]{64}$/i.test(state.basis_sha256||''))&&
      typeof state.reviewer_notes==='string'&&state.reviewer_notes.length<=2000;
    recoveryLoad.onclick=async()=>{
      const idea=recoveryIdea.value.trim(),project=recoveryProject.value.trim();
      const generation=++recoveryGeneration;
      recoveryRevision=null;recoveryIdentity=null;recoveryRevoke.disabled=true;
      if(!idea||!project){recoveryStatus.textContent='Both saved IDs are required.';return;}
      recoveryLoad.disabled=true;recoveryStatus.textContent='Reading authenticated saved review…';
      try{
        const state=await request(recoveryPath(idea,project));
        if(generation!==recoveryGeneration)return;
        if(!validRecoveryState(state,idea,project))
          throw new Error('Invalid saved review state.');
        recoveryRevision=state.revision;recoveryIdentity={idea,project};
        recoveryRevoke.disabled=state.revision===0||state.decision==='revoked';
        recoveryStatus.textContent='Saved decision: '+state.decision+' (revision '+state.revision+'). No execution access granted.';
      }catch(e){if(generation===recoveryGeneration)recoveryStatus.textContent=e.message;}
      finally{if(generation===recoveryGeneration)recoveryLoad.disabled=false;}
    };
    recoveryRevoke.onclick=async()=>{
      if(!recoveryIdentity||recoveryRevision===null)return;
      const {idea,project}=recoveryIdentity,revision=recoveryRevision,generation=++recoveryGeneration;
      recoveryRevoke.disabled=true;recoveryLoad.disabled=true;
      recoveryStatus.textContent='Withdrawing saved review…';
      try{
        await window.__owgAuthReady;
        const response=await fetch(recoveryPath(idea,project)+'/revoke',{
          method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({expected_review_revision:revision})
        });
        if(generation!==recoveryGeneration)return;
        if(!response.ok)throw new Error('Revocation refused ('+response.status+'). Reload the saved revision.');
        const state=await response.json();
        if(!validRecoveryState(state,idea,project)||state.decision!=='revoked'||
           state.revision!==revision+1)
          throw new Error('Inconsistent revocation response; reload the saved review.');
        recoveryRevision=state.revision;
        recoveryStatus.textContent='Review revoked (revision '+state.revision+'). No execution access granted.';
      }catch(e){if(generation===recoveryGeneration)recoveryStatus.textContent=e.message;}
      finally{if(generation===recoveryGeneration)recoveryLoad.disabled=false;}
    };
    recovery.append(recoveryHeading,recoveryHelp,recoveryIdeaLabel,recoveryProjectLabel,
      recoveryLoad,recoveryRevoke,recoveryStatus);
    card.append(heading,intro,label,input,load,status,results,details,recovery);panel.append(card);
  }
  document.addEventListener('DOMContentLoaded',mount);
})();
