/* PDD package: attach and mask step screenshots locally, then download a process document ZIP. Nothing is stored. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.pdd-package-request.v1';
  const DESIGN='openworkgraph.proposed-process-design.v1';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};

  // Pure helpers, exercised by tests.
  function stepsFromDesign(design){
    if(!design||design.format!==DESIGN||!design.to_be||!Array.isArray(design.to_be.proposed_path))throw new Error('Choose a process design exported from the process designer.');
    const path=design.to_be.proposed_path;
    if(!path.length||path.length>40||!path.every(s=>typeof s==='string'&&s.length&&s.length<=64))throw new Error('The design needs 1 to 40 named steps.');
    return path.map((activity,i)=>({id:'step_'+String(i+1).padStart(3,'0'),activity}));
  }
  function aiPrompt(design){
    const steps=stepsFromDesign(design);
    return ['Write a short, plain-language narrative for a Process Definition Document.',
      'Describe only the steps below in order. Do not invent systems, rules, people, approvals or numbers.',
      'Mark anything you are unsure about as a question for the process owner.','',
      'Goal (as declared by the reviewer): '+String(design.business_goal_declared_by_user||'').slice(0,500),'',
      'Steps:',...steps.map(s=>s.id+': '+s.activity)].join('\n');
  }
  function buildRequest(design,shots,narrative,reviewed){
    return {format:FORMAT,design,screenshots:shots.map(s=>({step_id:s.step_id,image_base64:s.image_base64,caption:s.caption||'',masked_regions:s.masked_regions|0})),
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
    card.appendChild(el('h2','Process documentation with screenshots'));
    card.appendChild(el('div','Create a Process Definition Document from a design exported by the process designer, with optional screenshots for each step. Screenshots you add are masked here, re-encoded without metadata, put into the ZIP you download, and not kept by OpenWorkGraph.','muted'));
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

    let design=null;const shots=[];
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
          image.onload=()=>{URL.revokeObjectURL(url);host.replaceChildren(editor(image,shot=>{shots.push({...shot,step_id:step.id});host.replaceChildren();draw();reviewed.checked=false;refresh();},()=>host.replaceChildren()));};
          image.onerror=()=>{URL.revokeObjectURL(url);status.textContent='That file could not be opened as an image.';};
          image.src=url;
        };
        row.append(thumbs,field('Add screenshot',pick),host);steps.appendChild(row);draw();
      }
    };
    file.onchange=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen)return;
      try{design=JSON.parse(await chosen.text());const list=stepsFromDesign(design);shots.length=0;renderSteps(list);extras.hidden=false;status.textContent=list.length+' steps loaded. Screenshots are optional.';}
      catch(error){design=null;steps.replaceChildren();extras.hidden=true;status.textContent=error instanceof SyntaxError?'That file is not valid JSON.':error.message;}
      refresh();
    };
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(aiPrompt(design));status.textContent='Prompt copied. It contains the goal and step names only, no screenshots.';}catch(_){status.textContent='Copying is not available in this browser.';}};
    make.onclick=async()=>{
      make.disabled=true;status.textContent='Building the document…';
      try{
        const blob=await api('/v1/process-design/pdd-package',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(buildRequest(design,shots,{text:narrative.value,ai_drafted:aiFlag.checked},reviewed.checked))});
        const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='openworkgraph-pdd.zip';document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='PDD package downloaded. Nothing was stored by OpenWorkGraph.';
      }catch(error){status.textContent=error.message;}finally{refresh();}
    };
  }

  window.__owgPddModel={stepsFromDesign,aiPrompt,buildRequest};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
