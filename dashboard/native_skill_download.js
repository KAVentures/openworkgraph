/* Review and download native AI shadow skills; no installation or invocation. */
export function mountNativeSkillDownload(host,api){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const field=(label,node)=>{const n=el('label',label+' ');n.style.display='block';n.style.margin='0.5rem 0';n.append(node);return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const file=()=>{const n=el('input');n.type='file';n.accept='.json,application/json';return n;};
  const sources=file();sources.multiple=true;
  const proposal=file(),draft=el('textarea');draft.rows=18;draft.style.width='100%';
  const emptyDraft=()=>JSON.stringify({name:'',description:'',option_index:0,instructions:'',tool_mappings:{},
    source_verification:'',test_plan:'',escalation:'',rollback:'',review_note:''},null,2);
  draft.value=emptyDraft();
  const draftFile=file(),loadDraft=button('Load editable draft JSON');
  const preview=button('Preview reviewed shadow skill'),download=button('Download exact shadow skill ZIP');
  const confirmation=el('input');confirmation.type='checkbox';
  const status=el('p','Select the original evidence and external AI proposal. Complete the human-reviewed draft.');
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const selection=el('p');selection.setAttribute('role','status');
  const order=el('ol'),rendered=el('div'),controls=[sources,proposal,draft,draftFile,loadDraft];
  let files=[],basis=null,busy=false,transferredProposal=null;
  function refresh(){
    controls.forEach(n=>n.disabled=busy);preview.disabled=busy;
    confirmation.disabled=busy||!basis;download.disabled=busy||!basis||!confirmation.checked;
    selection.textContent=transferredProposal?'Using the transferred proposal and selected evidence. Choose a proposal file to replace it.':'Choose an original proposal file.';
    order.replaceChildren();
    files.forEach((f,i)=>{
      const row=el('li',f.name+' '),up=button('Move earlier'),down=button('Move later');
      up.disabled=busy||i===0;down.disabled=busy||i===files.length-1;
      up.setAttribute('aria-label','Move skill evidence file '+(i+1)+' earlier');
      down.setAttribute('aria-label','Move skill evidence file '+(i+1)+' later');
      const move=delta=>{if(busy)return;[files[i],files[i+delta]]=[files[i+delta],files[i]];invalidate();refresh();};
      up.onclick=()=>move(-1);down.onclick=()=>move(1);row.append(up,down);order.append(row);
    });
  }
  function invalidate(){basis=null;confirmation.checked=false;rendered.replaceChildren();status.textContent='Inputs changed. Preview the exact package again.';refresh();}
  sources.onchange=()=>{files=Array.from(sources.files||[]);if(transferredProposal){transferredProposal=null;proposal.value='';}invalidate();};
  proposal.onchange=()=>{transferredProposal=null;invalidate();};draft.oninput=invalidate;draftFile.onchange=invalidate;confirmation.onchange=refresh;
  async function read(f){return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());}
  loadDraft.onclick=async()=>{
    if(busy)return;const f=draftFile.files?.[0];
    if(!f||!f.size||f.size>256*1024){status.textContent='Choose a reviewed draft JSON file of at most 256 KiB.';return;}
    busy=true;refresh();
    try{draft.value=await read(f);invalidate();}catch(e){status.textContent=e.message;}finally{busy=false;refresh();}
  };
  preview.onclick=async()=>{
    if(busy)return;
    invalidate();busy=true;refresh();
    try{
      const p=transferredProposal||proposal.files?.[0];
      if(files.length<1||files.length>8||!p||files.some(f=>!f.size)||!p.size)throw Error('Choose one to eight original evidence JSON files and one proposal.');
      if(files.reduce((n,f)=>n+f.size,p.size)>8*1024*1024||new TextEncoder().encode(draft.value).length>256*1024)
        throw Error('Selected evidence/proposal exceeds 8 MiB or draft exceeds 256 KiB.');
      const request={sources:await Promise.all(files.map(read)),proposal:await read(p),draft:draft.value};
      const response=await api('/v1/native-skill-download/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
      const value=await response.json(),m=value.manifest;
      if(value.installation_performed!==false||value.execution_authorized!==false||
        m?.format!=='openworkgraph.ai-shadow-skill-package.v1'||m.mode!=='shadow_only'||m.execution_authorized!==false||
        m.deployment_verified!==false||m.client_permissions_verified!==false||!/^[a-f0-9]{64}$/.test(m.package_sha256)||
        !value.rendered_files||Object.keys(value.rendered_files).length!==2)throw Error('Unexpected package preview.');
      for(const [path,text] of Object.entries(value.rendered_files)){
        const pre=el('pre',text);pre.style.whiteSpace='pre-wrap';pre.style.overflowWrap='anywhere';
        rendered.append(el('h4',path),pre);
      }
      const pin=el('p','Package SHA-256: '+m.package_sha256);pin.style.overflowWrap='anywhere';
      rendered.append(pin,el('p','Selected proposed environment: '+m.selected_environment+' · No installation or permissions verified.'));
      basis={request,pin:m.package_sha256};
      status.textContent='Inspect both complete rendered files. Confirm only for isolated shadow use.';
    }catch(e){status.textContent=e.message;}finally{busy=false;refresh();}
  };
  download.onclick=async()=>{
    if(busy||!basis||!confirmation.checked)return;
    const selected=basis;busy=true;refresh();confirmation.checked=false;
    try{
      const response=await api('/v1/native-skill-download/archive',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({draft_request:selected.request,expected_package_sha256:selected.pin,confirm_shadow_download:true})});
      if(response.headers.get('x-owg-package-sha256')!==selected.pin)throw Error('Returned package does not match preview.');
      const bytes=await response.arrayBuffer();
      const pin=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
      if(pin!==response.headers.get('x-owg-archive-sha256'))throw Error('Archive byte digest mismatch.');
      const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'})),a=el('a');
      a.href=url;a.download='owg-shadow-skill.zip';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      status.textContent='Exact shadow archive downloaded. Archive SHA-256: '+pin+'. No installation or invocation performed.';
    }catch(e){status.textContent=e.message;}finally{busy=false;refresh();}
  };
  const details=el('details');details.id='owgNativeSkillDownload';
  details.append(el('summary','Prepare reviewed native AI shadow skills'),
    el('p','Use the same original evidence in the same order as the portable packet. Supply a human-reviewed draft with the exact option index and all required tool mappings. Blank fields must be completed; no permissions are granted.'),
    field('Original skill evidence JSON files',sources),order,field('Original external AI skill proposal JSON',proposal),selection,
    field('Optional reviewed draft JSON file',draftFile),loadDraft,field('Editable human-reviewed skill draft JSON',draft),
    preview,status,rendered,field('I inspected both rendered files and confirm download for isolated shadow testing only',confirmation),download,
    el('p','Extract and verify the archive using its retained package digest before manual setup in a separately restricted test client. Removing a staged skill does not undo external side effects.'));
  host.append(details);refresh();
  function acceptReviewedProposal(data){
    if(busy)throw Error('Skill preparation is busy; finish that operation first.');
    if(!data||Object.keys(data).sort().join(',')!=='proposal,sources'||!Array.isArray(data.sources)||
      data.sources.length<1||data.sources.length>8||!data.sources.every(s=>typeof s==='string'&&s.length>0)||
      typeof data.proposal!=='string'||!data.proposal.length)throw Error('Invalid proposal transfer.');
    // Do not parse/re-serialize JSON: preserve the strings used by server review,
    // including the original source order and numeric representations.
    const encoder=new TextEncoder();
    const size=data.sources.reduce((n,s)=>n+encoder.encode(s).length,encoder.encode(data.proposal).length);
    if(size>8*1024*1024)throw Error('Transferred evidence/proposal exceeds 8 MiB.');
    const nextFiles=data.sources.map((s,i)=>new File([s],'reviewed-evidence-'+(i+1)+'.json',{type:'application/json'}));
    const nextProposal=new File([data.proposal],'reviewed-proposal.json',{type:'application/json'});
    sources.value='';proposal.value='';draftFile.value='';
    files=nextFiles;transferredProposal=nextProposal;draft.value=emptyDraft();invalidate();
    details.open=true;details.querySelector('summary').focus();
    status.textContent='Evidence and proposal transferred. Complete a new human-reviewed draft; no approval was transferred.';
  }
  return {acceptReviewedProposal};
}
