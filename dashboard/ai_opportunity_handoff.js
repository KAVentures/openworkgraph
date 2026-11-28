/* Human-reviewed external AI handoff: only create an unassessed local idea. */
export function mountAiOpportunityHandoff(host, post, onCreated, onStage){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,control)=>{const n=el('label',label+' ');n.append(control);return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const sources=file();sources.multiple=true;
  const proposal=file(),title=el('input'),description=el('textarea');
  title.maxLength=200;description.maxLength=2000;description.rows=3;
  const preview=el('button','Review exact proposal'),commit=el('button','Create unassessed idea');
  const stage=el('button','Start new shadow skill draft from reviewed proposal');
  preview.type=commit.type=stage.type='button';stage.disabled=true;stage.hidden=typeof onStage!=='function';
  const confirmation=el('input');confirmation.type='checkbox';confirmation.disabled=true;commit.disabled=true;
  const status=el('div','Choose the original evidence files and AI proposal; enter a human-reviewed idea title and details.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const summary=el('pre');summary.style.whiteSpace='pre-wrap';summary.style.overflowWrap='anywhere';
  const controls=[sources,proposal,title,description],order=el('ol');
  let generation=0,basis=null,busy=false,orderedSources=[];
  let packetGeneration=0,preparedPacket=null;
  let generatedResult=null,selectedGenerated=false;
  const budget=el('select');
  for(const [value,label] of [[131072,'128 KiB'],[524288,'512 KiB'],[2097152,'2 MiB'],[8388608,'8 MiB']]){
    const option=el('option',label);option.value=String(value);budget.append(option);
  }
  budget.value='524288';
  const prepare=el('button','Prepare evidence packet'),downloadPacket=el('button','Download evidence packet'),downloadGuide=el('button','Download AI instructions');
  const downloadCompact=el('button','Download compact evidence packet'),downloadCompactGuide=el('button','Download compact AI instructions');
  prepare.type=downloadPacket.type=downloadGuide.type=downloadCompact.type=downloadCompactGuide.type='button';
  downloadPacket.disabled=downloadGuide.disabled=downloadCompact.disabled=downloadCompactGuide.disabled=true;
  const packetStatus=el('div','Choose original evidence files and a context byte budget. Nothing is sent to an AI.');
  packetStatus.setAttribute('role','status');packetStatus.setAttribute('aria-live','polite');
  function invalidatePacket(){
    packetGeneration++;preparedPacket=null;downloadPacket.disabled=downloadGuide.disabled=downloadCompact.disabled=downloadCompactGuide.disabled=true;
    contextMode.value='original';clearLocal();
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
  downloadCompact.onclick=()=>{
    if(!busy&&preparedPacket?.compact_download)downloadLocal('owg-implementation-evidence-compact.json','application/json',preparedPacket.compact_download.packet_json);
  };
  downloadCompactGuide.onclick=()=>{
    if(!busy&&preparedPacket?.compact_download)downloadLocal('owg-ai-implementation-compact-instructions.txt','text/plain;charset=utf-8',preparedPacket.compact_download.instructions);
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
      const compact=response.compaction;
      if(maxBytes<=524288){
        if(!compact||!/^[0-9a-f]{64}$/.test(compact.compact_sha256)||
          !Number.isSafeInteger(compact.compact_bytes)||compact.compact_bytes<1||
          !Number.isSafeInteger(compact.bytes_saved)||compact.bytes_saved!==response.packet_bytes-compact.compact_bytes||
          compact.available!==(compact.bytes_saved>0)||compact.token_savings_measured!==false)
          throw Error('Unexpected compaction preview.');
      }else if(compact!==null)throw Error('Unexpected compaction scope.');
      const compactDownload=response.compact_download;
      if(compactDownload!==null){
        const bytes=s=>new TextEncoder().encode(s).length;
        if(!compact?.available||!compactDownload||typeof compactDownload.packet_json!=='string'||
          typeof compactDownload.instructions!=='string'||compactDownload.instructions.length>16000||
          compactDownload.token_savings_verified!==false||compactDownload.model_quality_equivalence_verified!==false||
          compactDownload.external_sharing_authorized!==false||
          bytes(compactDownload.packet_json)!==compact.compact_bytes||
          compactDownload.original_total_bytes!==response.packet_bytes+bytes(response.instructions)||
          compactDownload.compact_total_bytes!==compact.compact_bytes+bytes(compactDownload.instructions)||
          compactDownload.total_bytes_saved!==compactDownload.original_total_bytes-compactDownload.compact_total_bytes||
          compactDownload.total_bytes_saved<=0)throw Error('Unexpected compact download.');
        const decoded=JSON.parse(compactDownload.packet_json);
        if(decoded.format!=='openworkgraph.ai-implementation-evidence-compact.v1'||
          decoded.original_packet_sha256!==response.packet_sha256||decoded.compact_sha256!==compact.compact_sha256)
          throw Error('Compact download does not match the original packet.');
      }
      preparedPacket=response;
      packetStatus.textContent='Packet ready: '+response.event_count+' events, '+response.coverage_gap_count+' coverage gaps, '+response.packet_bytes+' bytes. SHA-256: '+response.packet_sha256+'. Review before sharing with your chosen AI. No upload or inference occurred.'+(compact?(compact.available?' Optional compact representation saves '+compact.bytes_saved+' evidence bytes; token savings unmeasured.':' Compaction does not reduce evidence bytes.'):'')+(compactDownload?' Compact external handoff saves '+compactDownload.total_bytes_saved+' combined evidence/instruction bytes. Use the matching compact instructions; token savings and model quality remain unverified.':'');
    }catch(error){if(ticket===packetGeneration)packetStatus.textContent=error.message;}
    finally{lock(false);}
  };
  const localModel=el('input'),localPort=el('input'),localTokens=el('input'),localJsonMode=el('input'),localConfirm=el('input');
  const contextMode=el('select');
  for(const [value,label] of [['original','Original evidence (default)'],['compact','Lossless compact evidence (experimental)']]){
    const option=el('option',label);option.value=value;contextMode.append(option);
  }
  contextMode.value='original';
  localModel.maxLength=120;localModel.placeholder='Exact configured model ID';
  localPort.type=localTokens.type='number';localPort.min='1';localPort.max='65535';localPort.value='11434';
  localTokens.min='128';localTokens.max='16384';localTokens.value='4096';
  localJsonMode.type=localConfirm.type='checkbox';
  const generate=el('button','Generate with local model'),useGenerated=el('button','Use generated proposal for review'),
    saveProposal=el('button','Download generated proposal'),saveReceipt=el('button','Download local model receipt');
  for(const n of [generate,useGenerated,saveProposal,saveReceipt]){n.type='button';n.disabled=true;}
  const localStatus=el('div','Prepare an evidence packet to enable the optional local model route.');
  localStatus.setAttribute('role','status');localStatus.setAttribute('aria-live','polite');
  const generatedText=el('textarea');generatedText.readOnly=true;generatedText.rows=8;
  generatedText.hidden=true;
  const localControls=[localModel,localPort,localTokens,localJsonMode,localConfirm,contextMode];
  function localReady(){
    return !!preparedPacket&&preparedPacket.max_packet_bytes<=524288&&localConfirm.checked&&
      (contextMode.value==='original'||(contextMode.value==='compact'&&preparedPacket.compaction?.available===true))&&
      /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$/.test(localModel.value)&&
      Number.isInteger(Number(localPort.value))&&Number(localPort.value)>=1&&Number(localPort.value)<=65535&&
      Number.isInteger(Number(localTokens.value))&&Number(localTokens.value)>=128&&Number(localTokens.value)<=16384;
  }
  function refreshLocal(){
    for(const n of localControls)n.disabled=busy||!preparedPacket||preparedPacket.max_packet_bytes>524288;
    contextMode.options[1].disabled=preparedPacket?.compaction?.available!==true;
    generate.disabled=busy||!localReady();
    useGenerated.disabled=saveProposal.disabled=saveReceipt.disabled=busy||!generatedResult;
  }
  function clearLocal(){
    const wasSelected=selectedGenerated;
    generatedResult=null;selectedGenerated=false;localConfirm.checked=false;
    generatedText.value='';generatedText.hidden=true;
    localStatus.textContent='Local draft cleared. Prepare the evidence and explicitly confirm the next request.';
    if(wasSelected)invalidate();
    refreshLocal();
  }
  for(const n of [localModel,localPort,localTokens,localJsonMode])n.addEventListener('input',clearLocal);
  contextMode.addEventListener('change',clearLocal);
  localConfirm.addEventListener('change',refreshLocal);
  useGenerated.onclick=()=>{
    if(busy||!generatedResult)return;
    proposal.value='';selectedGenerated=true;invalidate();
    status.textContent='Generated proposal selected. Enter human-reviewed idea details and review exact proposal.';
  };
  saveProposal.onclick=()=>{if(!busy&&generatedResult)downloadLocal('owg-local-model-proposal.json','application/json',generatedResult.proposal_json);};
  saveReceipt.onclick=()=>{if(!busy&&generatedResult)downloadLocal('owg-local-model-receipt.json','application/json',JSON.stringify(generatedResult.receipt,null,2));};
  generate.onclick=async()=>{
    if(busy||!localReady())return;
    const packet=preparedPacket,ticket=packetGeneration,files=orderedSources.slice();
    const settings={model:localModel.value,port:Number(localPort.value),max_output_tokens:Number(localTokens.value),json_mode:localJsonMode.checked,context_representation:contextMode.value};
    clearLocal();lock(true);localStatus.textContent='Requesting one draft from the confirmed loopback model service…';
    try{
      const response=await post('/v1/ai-opportunity-handoff/generate-local',{
        sources:await Promise.all(files.map(f=>f.text())),expected_packet_sha256:packet.packet_sha256,
        max_bytes:packet.max_packet_bytes,...settings,confirm_local_inference:true});
      if(ticket!==packetGeneration)return;
      const receipt=response.receipt;
      if(response.format!=='openworkgraph.ai-implementation-local-handoff.v1'||response.packet_sha256!==packet.packet_sha256||
        response.execution_authorized!==false||response.semantic_correctness_verified!==false||response.opportunity_created!==false||
        response.human_review_required!==true||typeof response.proposal_json!=='string'||
        new TextEncoder().encode(response.proposal_json).length>1048576||
        !receipt||receipt.format!=='openworkgraph.ai-implementation-local-model-receipt.v1'||
        receipt.packet_sha256!==packet.packet_sha256||receipt.requested_model!==settings.model||receipt.endpoint_reported_model!==settings.model||
        receipt.endpoint_is_loopback!==true||receipt.inference_location_verified!==false||
        receipt.execution_authorized!==false||receipt.actual_cost_verified!==false||receipt.semantic_correctness_verified!==false||
        receipt.context_representation!==settings.context_representation||receipt.original_context_bytes!==packet.packet_bytes||
        receipt.requested_output_tokens!==settings.max_output_tokens||receipt.json_mode!==settings.json_mode||
        receipt.token_savings_verified!==false||receipt.model_quality_equivalence_verified!==false||
        receipt.transmitted_context_bytes!==(settings.context_representation==='compact'?packet.compaction?.compact_bytes:packet.packet_bytes)||
        receipt.compact_sha256!==(settings.context_representation==='compact'?packet.compaction?.compact_sha256:null)||
        receipt.context_bytes_saved!==receipt.original_context_bytes-receipt.transmitted_context_bytes||
        !/^[0-9a-f]{64}$/.test(receipt.proposal_sha256)||receipt.review_status!=='structural_review_only'||
        !Number.isSafeInteger(receipt.elapsed_ms)||receipt.elapsed_ms<0||!receipt.usage||
        !['prompt_tokens','completion_tokens','total_tokens'].every(k=>receipt.usage[k]===null||
          (Number.isSafeInteger(receipt.usage[k])&&receipt.usage[k]>=0)))
        throw Error('Unexpected local proposal response.');
      const parsed=JSON.parse(response.proposal_json);
      if(parsed.format!=='openworkgraph.ai-implementation-proposal.v1'||parsed.packet_sha256!==packet.packet_sha256||parsed.author_model!==settings.model)
        throw Error('Local proposal does not match the selected packet and model.');
      generatedResult=response;generatedText.value=JSON.stringify(parsed,null,2);generatedText.hidden=false;
      const usage=receipt.usage,display=value=>value===null?'unknown':String(value);
      localStatus.textContent='Local model draft ready: '+display(usage.prompt_tokens)+' input, '+display(usage.completion_tokens)+
        ' output, '+display(usage.total_tokens)+' total reported tokens; '+receipt.elapsed_ms+' ms. '+receipt.context_representation+' context: '+receipt.transmitted_context_bytes+' bytes, '+receipt.context_bytes_saved+' bytes saved. Token savings, costs, quality equivalence and inference location are unverified. Inspect the proposal before using it.';
    }catch(error){if(ticket===packetGeneration)localStatus.textContent=error.message;}
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
    generation++;basis=null;confirmation.checked=false;confirmation.disabled=true;commit.disabled=true;stage.disabled=true;
    summary.textContent='';status.textContent='Inputs changed. Review the exact proposal again.';
  }
  sources.addEventListener('change',()=>{orderedSources=Array.from(sources.files||[]);invalidate();invalidatePacket();renderOrder();});
  proposal.addEventListener('input',()=>{selectedGenerated=false;invalidate();});
  for(const n of [title,description])n.addEventListener('input',invalidate);
  confirmation.addEventListener('change',()=>{commit.disabled=busy||!basis||!confirmation.checked;});
  function lock(value){
    busy=value;for(const n of controls)n.disabled=value;preview.disabled=value;
    budget.disabled=prepare.disabled=value;downloadPacket.disabled=downloadGuide.disabled=value||!preparedPacket;
    downloadCompact.disabled=downloadCompactGuide.disabled=value||!preparedPacket?.compact_download;
    confirmation.disabled=value||!basis;commit.disabled=value||!basis||!confirmation.checked;stage.disabled=value||!basis||typeof onStage!=='function';renderOrder();refreshLocal();
  }
  preview.onclick=async()=>{
    if(busy)return;
    invalidate();const ticket=generation;lock(true);
    try{
      const files=orderedSources.slice(),proposed=proposal.files?.[0],localText=selectedGenerated&&generatedResult?generatedResult.proposal_json:null;
      if(files.length<1||files.length>8||(!proposed&&!localText)||!title.value.trim())throw Error('Choose one to eight evidence JSON files, one proposal and a title.');
      const proposalSize=localText?new TextEncoder().encode(localText).length:proposed.size;
      if(files.reduce((n,f)=>n+f.size,proposalSize)>8*1024*1024)throw Error('Selected files exceed 8 MiB.');
      const draft={sources:await Promise.all(files.map(f=>f.text())),proposal:localText||await proposed.text(),title:title.value,description:description.value};
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
  stage.onclick=async()=>{
    if(busy||!basis||typeof onStage!=='function')return;
    const selected=basis;lock(true);
    try{
      await onStage({sources:selected.draft.sources.slice(),proposal:selected.draft.proposal});
      status.textContent='Transferred the exact reviewed inputs into a new shadow skill draft. No approval or execution transferred.';
    }catch(error){status.textContent=error.message;}
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
  const localSection=el('details');localSection.id='owgLocalImplementationModel';
  localSection.append(el('summary','Optional: generate a draft with your local model'),
    el('p','Prepare a packet of at most 512 KiB. OWG makes one request to the configured port on the OWG server’s IPv4 loopback, not necessarily the browser machine. A loopback service may proxy or retain data; verify the service, egress and permissions first. No remote fallback.'),
    field('Local model ID',localModel),field('Local model port',localPort),field('Maximum output tokens',localTokens),
    field('Model evidence representation',contextMode),field('Request JSON-object mode',localJsonMode),field('I authorize sending the selected evidence to this configured loopback model service',localConfirm),
    generate,localStatus,field('Generated proposal (unverified interpretation)',generatedText),useGenerated,saveProposal,saveReceipt);
  refreshLocal();
  const details=el('details');details.id='owgAiOpportunityHandoff';
  details.append(el('summary','Add an external AI proposal to the implementation portfolio'),
    el('p','Preparation and review stay on the local OWG server. The optional local model request sends selected evidence only when explicitly confirmed. Only your entered idea and structural lineage are retained by the pipeline. Nothing is approved or executed.'),
    field('Original workflow evidence JSON files (ordered as in the portable packet)',sources),order,
    el('p','Prepare a packet using the same ordered evidence below, or use an existing exact packet. The byte budget is not a token count. A too-small budget refuses rather than drops events. Download and inspect the files, then share only through an authorized local or cloud AI environment.'),
    field('Context byte budget',budget),prepare,packetStatus,downloadPacket,downloadGuide,
    el('p','Optional compact external handoff: download both matching files, inspect locally and share only with an authorized AI. Compact format is reversible, not redaction.'),downloadCompact,downloadCompactGuide,localSection,
    field('External AI proposal JSON',proposal),field('Human-reviewed opportunity title',title),
    field('Human-reviewed opportunity details',description),preview,status,summary,
    ...(typeof onStage==='function'?[el('p','Starting a new shadow skill draft replaces any unfinished skill draft. The skill still requires its own human review and confirmation.'),stage]:[]),
    field('I reviewed the original proposal and want to create an unassessed idea only',confirmation),commit);
  host.append(details);
}
