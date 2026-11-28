/* Human-reviewed external AI handoff: only create an unassessed local idea. */
export function mountAiOpportunityHandoff(host, post, onCreated){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,control)=>{const n=el('label',label+' ');n.append(control);return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const sources=file();sources.multiple=true;
  const proposal=file(),title=el('input'),description=el('textarea');
  title.maxLength=200;description.maxLength=2000;description.rows=3;
  const preview=el('button','Review exact proposal'),commit=el('button','Create unassessed idea');
  preview.type=commit.type='button';
  const confirmation=el('input');confirmation.type='checkbox';confirmation.disabled=true;commit.disabled=true;
  const status=el('div','Choose the original evidence files and AI proposal; enter a human-reviewed idea title and details.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const summary=el('pre');summary.style.whiteSpace='pre-wrap';summary.style.overflowWrap='anywhere';
  const controls=[sources,proposal,title,description],order=el('ol');
  let generation=0,basis=null,busy=false,orderedSources=[];
  function renderOrder(){
    order.replaceChildren();
    orderedSources.forEach((f,i)=>{
      const row=el('li',f.name+' '),up=el('button','Move earlier'),down=el('button','Move later');
      up.type=down.type='button';up.disabled=busy||i===0;down.disabled=busy||i===orderedSources.length-1;
      up.setAttribute('aria-label','Move evidence file '+(i+1)+' earlier');
      down.setAttribute('aria-label','Move evidence file '+(i+1)+' later');
      const move=delta=>{
        if(busy)return;
        [orderedSources[i],orderedSources[i+delta]]=[orderedSources[i+delta],orderedSources[i]];
        invalidate();renderOrder();
      };
      up.onclick=()=>move(-1);down.onclick=()=>move(1);row.append(up,down);order.append(row);
    });
  }
  function invalidate(){
    generation++;basis=null;confirmation.checked=false;confirmation.disabled=true;commit.disabled=true;
    summary.textContent='';status.textContent='Inputs changed. Review the exact proposal again.';
  }
  sources.addEventListener('change',()=>{orderedSources=Array.from(sources.files||[]);invalidate();renderOrder();});
  for(const n of [proposal,title,description])n.addEventListener('input',invalidate);
  confirmation.addEventListener('change',()=>{commit.disabled=busy||!basis||!confirmation.checked;});
  function lock(value){
    busy=value;for(const n of controls)n.disabled=value;preview.disabled=value;
    confirmation.disabled=value||!basis;commit.disabled=value||!basis||!confirmation.checked;renderOrder();
  }
  preview.onclick=async()=>{
    if(busy)return;
    invalidate();const ticket=generation;lock(true);
    try{
      const files=orderedSources.slice(),proposed=proposal.files?.[0];
      if(files.length<1||files.length>8||!proposed||!title.value.trim())throw Error('Choose one to eight evidence JSON files, one proposal and a title.');
      if(files.reduce((n,f)=>n+f.size,proposed.size)>8*1024*1024)throw Error('Selected files exceed 8 MiB.');
      const draft={sources:await Promise.all(files.map(f=>f.text())),proposal:await proposed.text(),title:title.value,description:description.value};
      const response=await post('/v1/ai-opportunity-handoff/preview',draft);
      if(ticket!==generation)return;
      if(response.format!=='openworkgraph.ai-opportunity-handoff.v1'||response.execution_authorized!==false||
        response.state_on_create!=='idea'||response.assessment_on_create!==null||
        !/^[0-9a-f]{64}$/.test(response.preview_sha256))throw Error('Unexpected review response.');
      basis={draft,pin:response.preview_sha256};
      summary.textContent='Candidate: '+response.idea.title+'\nCited events: '+response.review.cited_event_count+
        ' / '+response.review.selected_event_count+'\nUnresolved questions: '+response.review.unresolved_question_count+
        '\nCoverage gaps: '+response.review.coverage_gaps.length+'\nCandidate environments: '+response.review.environments.join(', ')+
        '\nProposal SHA-256: '+response.lineage.proposal_sha256+'\nPreview SHA-256: '+response.preview_sha256;
      status.textContent='Structural review passed. Review the original proposal and evidence yourself before creating an idea; no semantic verification or permission.';
    }catch(error){if(ticket===generation)status.textContent=error.message;}
    finally{lock(false);}
  };
  commit.onclick=async()=>{
    if(busy||!basis||!confirmation.checked)return;
    const selected=basis;lock(true);
    try{
      const made=await post('/v1/ai-opportunity-handoff/commit',{draft:selected.draft,expected_preview_sha256:selected.pin,confirm_create_idea:true});
      if(made.state!=='idea'||made.execution_authorized!==false||made.assessment!==null)throw Error('Unexpected response; refresh the pipeline before retrying.');
      invalidate();status.textContent='Unassessed idea created: '+made.idea_id+'. No execution is authorized.';
      try{await onCreated(made.idea_id);}catch(_){status.textContent='Idea created: '+made.idea_id+'. Refresh the pipeline to inspect it.';}
    }catch(error){status.textContent=error.message;}
    finally{lock(false);}
  };
  const details=el('details');details.id='owgAiOpportunityHandoff';
  details.append(el('summary','Add an external AI proposal to the implementation portfolio'),
    el('p','Files are reviewed by the local OWG server, not sent to a model. Only your entered idea and structural lineage are retained. Nothing is approved or executed.'),
    field('Original workflow evidence JSON files (ordered as in the portable packet)',sources),order,
    field('External AI proposal JSON',proposal),field('Human-reviewed opportunity title',title),
    field('Human-reviewed opportunity details',description),preview,status,summary,
    field('I reviewed the original proposal and want to create an unassessed idea only',confirmation),commit);
  host.append(details);
}
