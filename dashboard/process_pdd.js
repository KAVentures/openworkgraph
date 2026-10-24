/* PDD package: attach and mask step screenshots locally, then download a process document ZIP. Nothing is stored. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.pdd-package-request.v1';
  const DESIGN='openworkgraph.proposed-process-design.v1';
  const SAVED='openworkgraph.versioned-process-export.v1';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};

  // Pure helpers, exercised by tests.
  function stepsFromDesign(design){
    if(design?.format===SAVED){
      const nodes=design.process_model?.activities;
      if(!Array.isArray(nodes)||!nodes.length||nodes.length>40||nodes.some(n=>!n||typeof n.label!=='string'||!n.label.length||n.label.length>64||typeof n.id!=='string'||!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(n.id)))throw new Error('Choose a bounded saved process revision.');
      return nodes.map(n=>({id:n.id,activity:n.label}));
    }
    if(!design||design.format!==DESIGN||!design.to_be||!Array.isArray(design.to_be.proposed_path))throw new Error('Choose a process design exported from the process designer.');
    const path=design.to_be.proposed_path;
    if(!path.length||path.length>40||!path.every(s=>typeof s==='string'&&s.length&&s.length<=64))throw new Error('The design needs 1 to 40 named steps.');
    return path.map((activity,i)=>({id:'step_'+String(i+1).padStart(3,'0'),activity}));
  }
  function aiPrompt(design){
    const steps=stepsFromDesign(design);
    if(design.format===SAVED){
      const model=design.process_model;
      return ['Draft a plain-language description of this saved process design. It is not approved policy or verified observed behavior.',
        'Node order is an index, not an execution sequence. Preserve decisions, loops, every parallel branch and join. Do not invent rules, people, tools, numbers or outcomes. Ask the process owner about missing facts.',
        'Declared goal: '+model.goal,'Nodes:',...model.activities.map(n=>n.id+': '+n.label+' ('+n.kind+')'),
        'Transitions:',...model.transitions.map(e=>e.source+' → '+e.target+(e.rule_id?' (declared rule '+e.rule_id+')':'')),
        'Declared rules (not verified policy):',...(model.business_rules||[]).map(r=>r.id+': '+r.text)].join('\n');
    }
    return ['Write a short, plain-language narrative for a Process Definition Document.',
      'Describe only the steps below in order. Do not invent systems, rules, people, approvals or numbers.',
      'Mark anything you are unsure about as a question for the process owner.','',
      'Goal (as declared by the reviewer): '+String(design.business_goal_declared_by_user||'').slice(0,500),'',
      'Steps:',...steps.map(s=>s.id+': '+s.activity)].join('\n');
  }
  function buildRequest(design,shots,narrative,reviewed){
    const source=design.format===SAVED?{process_reference:{workflow_id:design.workflow_id,revision:design.revision,content_sha256:design.content_sha256}}:{design};
    return {format:FORMAT,...source,screenshots:shots.map(s=>({step_id:s.step_id,image_base64:s.image_base64,caption:s.caption||'',masked_regions:s.masked_regions|0})),
      narrative:{text:String(narrative.text||''),ai_drafted:narrative.ai_drafted===true},screenshots_reviewed_and_redacted_by_user:reviewed===true};
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Could not build the document ('+response.status+').');}
    return response.blob();
  }

  // Masking editor: drag rectangles over sensitive areas; they are drawn into the pixels.
  function editor(image,onDone,onCancel){
    const box=el('div','','owg-pdd-editor');box.style.cssText='border:1px solid var(--line,#ccc);border-radius:6px;padding:8px;margin:8px 0';
    const scale=Math.min(1,900/image.naturalWidth);
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    canvas.style.cssText='max-width:100%;cursor:crosshair;touch-action:none;width:'+Math.round(image.naturalWidth*scale)+'px';
    canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Screenshot being masked. Drag to cover sensitive areas.');
    const ctx=canvas.getContext('2d');const rects=[];
    const draw=()=>{ctx.drawImage(image,0,0);ctx.fillStyle='#000';rects.forEach(r=>ctx.fillRect(r.x,r.y,r.w,r.h));};
    draw();
    let start=null;
    const point=e=>{const b=canvas.getBoundingClientRect();return {x:(e.clientX-b.left)*canvas.width/b.width,y:(e.clientY-b.top)*canvas.height/b.height};};
    canvas.onpointerdown=e=>{start=point(e);canvas.setPointerCapture(e.pointerId);};
    canvas.onpointermove=e=>{if(!start)return;const p=point(e);draw();ctx.fillStyle='rgba(0,0,0,.6)';ctx.fillRect(Math.min(start.x,p.x),Math.min(start.y,p.y),Math.abs(p.x-start.x),Math.abs(p.y-start.y));};
    canvas.onpointerup=e=>{if(!start)return;const p=point(e);const r={x:Math.min(start.x,p.x),y:Math.min(start.y,p.y),w:Math.abs(p.x-start.x),h:Math.abs(p.y-start.y)};start=null;if(r.w>3&&r.h>3)rects.push(r);draw();count.textContent=rects.length+' masked area'+(rects.length===1?'':'s');};
    const count=el('span','0 masked areas','muted');count.setAttribute('aria-live','polite');
    const caption=input('owgPddCaption','text','What this screen shows');caption.maxLength=300;
    const undo=button('Undo last mask','secondary');undo.onclick=()=>{rects.pop();draw();count.textContent=rects.length+' masked areas';};
    const all=button('Mask entire screenshot','secondary');all.onclick=()=>{rects.length=0;rects.push({x:0,y:0,w:canvas.width,h:canvas.height});draw();count.textContent='Entire screenshot masked';};
    const done=button('Use this screenshot');done.id='owgPddUseShot';
    const cancel=button('Cancel','secondary');
    done.onclick=()=>{draw();const data=canvas.toDataURL('image/png').split(',')[1];onDone({image_base64:data,caption:caption.value.trim(),masked_regions:rects.length,preview:canvas.toDataURL('image/png')});};
    cancel.onclick=onCancel;
    const row=el('div',null,'row');row.style.alignItems='center';row.append(undo,all,count);
    const actions=el('div',null,'row');actions.append(done,cancel);
    box.append(el('div','Drag over names, email addresses, numbers or anything else that should not appear. Masks are drawn into the image before it leaves this page.','muted'),canvas,row,field('Caption (optional)',caption),actions);
    box._controls={canvas,rects,draw};
    return box;
  }

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgPddCard'))return;
    const card=el('div',null,'card');card.id='owgPddCard';
    card.style.overflowWrap='anywhere';
    card.appendChild(el('h2','Process documentation with screenshots'));
    card.appendChild(el('div','Use a saved process revision from the catalog or an exported proposed design, with optional screenshots for its nodes. Saved graphs keep their decisions, branches and joins. Screenshots are masked here, re-encoded without metadata, included in your download, and not kept by OpenWorkGraph.','muted'));
    const file=input('owgPddDesign','file');file.accept='.json,application/json';
    const status=el('div',null,'muted');status.id='owgPddStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const steps=el('div');steps.id='owgPddSteps';
    const narrative=document.createElement('textarea');narrative.id='owgPddNarrative';narrative.rows=4;narrative.style.width='100%';narrative.maxLength=20000;
    const aiFlag=input('owgPddAiDrafted','checkbox');const aiLabel=el('label');aiLabel.style.cssText='display:flex;gap:6px;align-items:center';aiLabel.append(aiFlag,document.createTextNode(' This narrative was drafted with an AI tool (it is marked as an unreviewed draft)'));
    const copy=button('Copy a prompt for your AI','secondary');copy.id='owgPddCopyPrompt';
    const reviewed=input('owgPddReviewed','checkbox');const reviewedLabel=el('label');reviewedLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    reviewedLabel.append(reviewed,document.createTextNode(' I reviewed every screenshot and masked anything sensitive'));
    const make=button('Download PDD package');make.id='owgPddBuild';make.disabled=true;
    const extras=el('div');extras.id='owgPddExtras';extras.hidden=true;
    extras.append(el('h3','Narrative (optional)'),field('Narrative',narrative),aiLabel,copy,reviewedLabel,make);
    card.append(field('Exported process design (JSON)',file),status,steps,extras);
    const anchor=document.getElementById('owgCommsCard')||document.getElementById('owgEventLogCard');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    let design=null,sourceTicket=0;const shots=[];
    const refresh=()=>{make.disabled=!design||(shots.length>0&&!reviewed.checked);};
    reviewed.onchange=refresh;
    const renderSteps=list=>{
      steps.replaceChildren();
      for(const step of list){
        const row=el('div');row.style.cssText='border-top:1px solid var(--line,#eee);padding:6px 0';
        row.appendChild(el('strong',step.id+': '+step.activity));
        const thumbs=el('div',null,'row');
        const pick=input('owgPddShot_'+step.id,'file');pick.accept='image/png,image/jpeg';pick.setAttribute('aria-label','Add a screenshot for '+step.activity);
        const host=el('div');
        const draw=()=>{thumbs.replaceChildren(...shots.filter(s=>s.step_id===step.id).map(s=>{
          const fig=el('figure');fig.style.cssText='margin:4px;max-width:180px';const img=document.createElement('img');img.src=s.preview;img.alt=s.caption||('Screenshot for '+step.activity);img.style.maxWidth='100%';
          const remove=button('Remove','secondary');remove.onclick=()=>{shots.splice(shots.indexOf(s),1);draw();refresh();};
          fig.append(img,el('figcaption',(s.caption||'No caption')+' · '+s.masked_regions+' masked'),remove);return fig;}));};
        pick.onchange=()=>{
          const chosen=pick.files&&pick.files[0];if(!chosen)return;pick.value='';
          if(shots.filter(s=>s.step_id===step.id).length>=3){status.textContent='At most 3 screenshots per step.';return;}
          const url=URL.createObjectURL(chosen);const image=new Image();
          image.onload=()=>{URL.revokeObjectURL(url);if(!host.isConnected)return;host.replaceChildren(editor(image,shot=>{shots.push({...shot,step_id:step.id});host.replaceChildren();draw();reviewed.checked=false;refresh();},()=>host.replaceChildren()));};
          image.onerror=()=>{URL.revokeObjectURL(url);status.textContent='That file could not be opened as an image.';};
          image.src=url;
        };
        row.append(thumbs,field('Add screenshot',pick),host);steps.appendChild(row);draw();
      }
    };
    const resetSource=()=>{sourceTicket++;design=null;shots.length=0;reviewed.checked=false;narrative.value='';aiFlag.checked=false;steps.replaceChildren();extras.hidden=true;refresh();return sourceTicket;};
    const install=(value,ticket)=>{
      if(ticket!==sourceTicket)return;
      const list=stepsFromDesign(value);design=value;renderSteps(list);extras.hidden=false;
      status.textContent=value.format===SAVED?'Saved revision '+value.revision+' loaded ('+list.length+' nodes). Node order is an attachment index, not an execution sequence.':list.length+' steps loaded. Screenshots are optional.';
      refresh();
    };
    const loadSaved=async(ref,ticket)=>{
      const query=new URLSearchParams({workflow_id:ref.workflow_id,revision:String(ref.revision)});
      const record=JSON.parse(await (await api('/v1/process-catalog/revision?'+query)).text());
      if(record.workflow_id!==ref.workflow_id||record.revision!==ref.revision||record.content_sha256!==ref.content_sha256)throw new Error('Saved revision no longer matches this reference.');
      install({...record,format:SAVED},ticket);
    };
    window.addEventListener('owg-process-pdd-source',async event=>{
      const ticket=resetSource();status.textContent='Loading saved revision…';card.scrollIntoView({block:'start'});
      try{await loadSaved(event.detail,ticket);}catch(error){if(ticket===sourceTicket)status.textContent=error.message;}
    });
    file.onchange=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen)return;
      const ticket=resetSource();
      try{const value=JSON.parse(await chosen.text());if(ticket!==sourceTicket)return;if(value.format===SAVED)await loadSaved(value,ticket);else install(value,ticket);}
      catch(error){if(ticket===sourceTicket){design=null;steps.replaceChildren();extras.hidden=true;status.textContent=error instanceof SyntaxError?'That file is not valid JSON.':error.message;}}
      refresh();
    };
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(aiPrompt(design));status.textContent='Prompt copied. It contains declared process text only, no screenshots.';}catch(_){status.textContent='Copying is not available in this browser.';}};
    make.onclick=async()=>{
      const ticket=sourceTicket;
      make.disabled=true;status.textContent='Building the document…';
      try{
        const blob=await api('/v1/process-design/pdd-package',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(buildRequest(design,shots,{text:narrative.value,ai_drafted:aiFlag.checked},reviewed.checked))});
        if(ticket!==sourceTicket)return;
        const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='openworkgraph-pdd.zip';document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='PDD package downloaded. Nothing was stored by this export.';
      }catch(error){if(ticket===sourceTicket)status.textContent=error.message;}finally{refresh();}
    };
  }

  window.__owgPddModel={stepsFromDesign,aiPrompt,buildRequest};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
