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
  let packetGeneration=0,preparedPacket=null;
  const budget=el('select');
  for(const [value,label] of [[131072,'128 KiB'],[524288,'512 KiB'],[2097152,'2 MiB'],[8388608,'8 MiB']]){
    const option=el('option',label);option.value=String(value);budget.append(option);
  }
  budget.value='524288';
  const prepare=el('button','Prepare evidence packet'),downloadPacket=el('button','Download evidence packet'),downloadGuide=el('button','Download AI instructions');
  prepare.type=downloadPacket.type=downloadGuide.type='button';
  downloadPacket.disabled=downloadGuide.disabled=true;
  const packetStatus=el('div','Choose original evidence files and a context byte budget. Nothing is sent to an AI.');
  packetStatus.setAttribute('role','status');packetStatus.setAttribute('aria-live','polite');
  function invalidatePacket(){
    packetGeneration++;preparedPacket=null;downloadPacket.disabled=downloadGuide.disabled=true;
    packetStatus.textContent='Evidence selection or budget changed. Prepare a new packet.';
  }
  budget.addEventListener('change',invalidatePacket);
  function downloadLocal(name,mime,contents){
    const url=URL.createObjectURL(new Blob([contents],{type:mime}));
    const anchor=el('a');anchor.href=url;anchor.download=name;
    document.body.append(anchor);
    try{anchor.click();}finally{anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  }
  downloadPacket.onclick=()=>{
    if(!busy&&preparedPacket)downloadLocal('owg-implementation-evidence.json','application/json',preparedPacket.packet_json);
  };
  downloadGuide.onclick=()=>{
    if(!busy&&preparedPacket)downloadLocal('owg-ai-implementation-instructions.txt','text/plain;charset=utf-8',preparedPacket.instructions);
  };
  prepare.onclick=async()=>{
    if(busy)return;
    invalidatePacket();const ticket=packetGeneration;lock(true);
    packetStatus.textContent='Preparing selected evidence locally…';
    try{
      const files=orderedSources.slice(),maxBytes=Number(budget.value);
      if(files.length<1||files.length>8)throw Error('Choose one to eight original evidence JSON files.');
      if(files.reduce((n,f)=>n+f.size,0)>8*1024*1024)throw Error('Selected evidence exceeds 8 MiB.');
      const response=await post('/v1/ai-opportunity-handoff/prepare-packet',{sources:await Promise.all(files.map(f=>f.text())),max_bytes:maxBytes});
      if(ticket!==packetGeneration)return;
      if(response.format!=='openworkgraph.ai-implementation-packet-preview.v1'||
        response.execution_authorized!==false||response.external_model_invoked!==false||
        response.external_sharing_authorized!==false||response.source_count!==files.length||
        response.max_packet_bytes!==maxBytes||!Number.isSafeInteger(response.packet_bytes)||
        response.packet_bytes<1||response.packet_bytes>maxBytes||
        typeof response.packet_json!=='string'||new TextEncoder().encode(response.packet_json).length!==response.packet_bytes||
        typeof response.instructions!=='string'||!response.instructions||response.instructions.length>16000||
        !Number.isSafeInteger(response.event_count)||response.event_count<1||
        !Number.isSafeInteger(response.coverage_gap_count)||response.coverage_gap_count<0||
        !/^[0-9a-f]{64}$/.test(response.packet_sha256))throw Error('Unexpected packet preparation response.');
      const packet=JSON.parse(response.packet_json);
      if(packet.format!=='openworkgraph.ai-implementation-evidence.v1'||packet.execution_authorized!==false||
        packet.packet_sha256!==response.packet_sha256||packet.event_count!==response.event_count||
        !Array.isArray(packet.coverage_gaps)||packet.coverage_gaps.length!==response.coverage_gap_count)
        throw Error('Unexpected evidence packet.');
      preparedPacket=response;
      packetStatus.textContent='Packet ready: '+response.event_count+' events, '+response.coverage_gap_count+' coverage gaps, '+response.packet_bytes+' bytes. SHA-256: '+response.packet_sha256+'. Review before sharing with your chosen AI. No upload or inference occurred.';
    }catch(error){if(ticket===packetGeneration)packetStatus.textContent=error.message;}
    finally{lock(false);}
  };
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
        invalidate();invalidatePacket();renderOrder();
      };
      up.onclick=()=>move(-1);down.onclick=()=>move(1);row.append(up,down);order.append(row);
    });
  }
  function invalidate(){
    generation++;basis=null;confirmation.checked=false;confirmation.disabled=true;commit.disabled=true;
    summary.textContent='';status.textContent='Inputs changed. Review the exact proposal again.';
  }
  sources.addEventListener('change',()=>{orderedSources=Array.from(sources.files||[]);invalidate();invalidatePacket();renderOrder();});
  for(const n of [proposal,title,description])n.addEventListener('input',invalidate);
  confirmation.addEventListener('change',()=>{commit.disabled=busy||!basis||!confirmation.checked;});
  function lock(value){
    busy=value;for(const n of controls)n.disabled=value;preview.disabled=value;
    budget.disabled=prepare.disabled=value;downloadPacket.disabled=downloadGuide.disabled=value||!preparedPacket;
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
    el('p','Prepare a packet using the same ordered evidence below, or use an existing exact packet. The byte budget is not a token count. A too-small budget refuses rather than drops events. Download and inspect the files, then share only through an authorized local or cloud AI environment.'),
    field('Context byte budget',budget),prepare,packetStatus,downloadPacket,downloadGuide,
    field('External AI proposal JSON',proposal),field('Human-reviewed opportunity title',title),
    field('Human-reviewed opportunity details',description),preview,status,summary,
    field('I reviewed the original proposal and want to create an unassessed idea only',confirmation),commit);
  host.append(details);
}
