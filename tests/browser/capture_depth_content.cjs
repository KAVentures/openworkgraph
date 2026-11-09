const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
// Real-browser acceptance of the extension's opt-in high-fidelity signals (#634) with stubbed extension APIs.
(async()=>{
 const root=path.resolve(__dirname,'..','..','browser_extension');
 const content=await fs.readFile(path.join(root,'content.js'),'utf8');
 const depth=await fs.readFile(path.join(root,'capture_depth.js'),'utf8');
 const html=`<!doctype html><html><body style="margin:0">
  <nav role="menubar"><div role="menuitem" id="menu" style="display:inline-block;width:120px;height:40px">Export</div></nav>
  <button id="save" title="Save changes" style="width:120px;height:40px">Save</button>
  <button id="row" style="height:40px;white-space:nowrap">private message row</button>
  <button id="off" disabled style="width:120px;height:40px">Disabled</button>
  <input id="pw" type="password" style="width:120px;height:40px">
  <div id="far" style="position:relative;width:300px;height:200px">empty area
    <div style="position:fixed;left:760px;top:300px;width:120px;height:40px"><input id="pw2" type="password" style="width:120px;height:40px;box-sizing:border-box"><div id="cover" style="position:absolute;inset:0"></div></div>
    <div id="sw" aria-label="password" style="position:fixed;left:760px;top:420px"><button id="reveal" title="Show">Reveal</button></div></div>
  <div id="closed-shadow" style="position:fixed;left:620px;top:450px;width:110px;height:45px"></div>
  <div id="card" draggable="true" role="button" aria-label="Invoice card" style="width:100px;height:40px">Invoice</div>
  <div id="draft" draggable="true" contenteditable="true" role="button" style="width:100px;height:40px">private draft words</div>
  <div id="plain" draggable="true" role="button" style="width:100px;height:40px">Quarterly salary review</div>
  <div id="ref" draggable="true" role="button" aria-labelledby="note" style="width:100px;height:40px">Drag</div>
  <div id="note" contenteditable="true">private note words</div>
  <label for="field">Notes <span contenteditable="true">private label words</span></label><div id="field" draggable="true" role="button" style="width:100px;height:40px">F</div>
  <div id="bin" role="region" aria-label="Archive bin" style="width:200px;height:80px">Archive</div>
  <input id="typed" value="private typed words" style="position:absolute;left:400px;top:600px;width:100px">
  <div id="mirror" draggable="true" role="button" style="position:absolute;left:520px;top:600px;width:60px;height:30px">M</div>
  <script>for(const a of ['aria-label','title','placeholder'])document.getElementById('mirror').setAttribute(a,document.getElementById('typed').value);
          document.getElementById('bin').addEventListener('dragover',e=>e.preventDefault());
          document.getElementById('bin').addEventListener('drop',e=>e.preventDefault());</script></body></html>`;
 const settings=(signals)=>({signals,parameters:{hover_dwell_ms:600,trajectory_hz:10,max_events_per_minute:600},profile:'custom',expires_at:new Date(Date.now()+3600e3).toISOString()});
 const browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
 try{
   const page=await browser.newPage({viewport:{width:1000,height:700}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const load=async(signals)=>{
     await page.goto('about:blank');await page.setContent(html);
     await page.evaluate(([s])=>{window.__msgs=[];
       const roots=new WeakMap();const host=document.querySelector('#closed-shadow');const shadow=host.attachShadow({mode:'closed'});
       shadow.innerHTML='<input type="password" style="width:100px;height:40px">';roots.set(host,shadow);
       window.__owgShadowRoots=roots;window.__owgInspectCount=0;
       window.chrome={dom:{openOrClosedShadowRoot(el){window.__owgInspectCount++;return roots.get(el)||el.shadowRoot;}},runtime:{sendMessage(m){window.__msgs.push(m);return Promise.resolve();}},
       storage:{local:{get:async()=>({openworkgraph_capture_depth_v1:s}),set:async()=>{}},onChanged:{addListener(fn){window.__changed=fn;}}}};},[settings(signals)]);
     await page.addScriptTag({content});await page.addScriptTag({content:depth});
     await page.waitForTimeout(50);
   };
   const msgs=async(action)=>(await page.evaluate(()=>window.__msgs)).filter(m=>!action||m.action===action);
   // Enhanced: hover dwell and drag/drop on; trajectories off.
   await load({hover_dwell:true,drag_drop:true,pointer_trajectory:false});
   await page.mouse.move(900,650);
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);
   await page.hover('#save');await page.waitForTimeout(100);await page.mouse.move(900,650); // a quick pass is not a dwell
   await page.hover('#off');await page.waitForTimeout(800);await page.mouse.move(900,650);  // disabled control
   await page.hover('#pw');await page.waitForTimeout(800);await page.mouse.move(900,650);   // secure field
   await page.hover('#save');await page.waitForTimeout(800);await page.mouse.down();await page.mouse.up();
   let hovers=await msgs('control_hover_dwell');
   assert.equal(hovers.length,2,JSON.stringify(hovers));
   assert.equal(hovers[0].target.role,'menuitem');assert.equal(hovers[0].metadata.ended_by,'pointer_left');
   assert(hovers[0].metadata.duration_ms>=600&&hovers[0].metadata.duration_ms%50===0);
   assert.equal(hovers[1].target.label,undefined,'no label is ever recorded, not even an author title');assert.equal(hovers[1].metadata.ended_by,'activated');
   assert(!JSON.stringify(hovers).includes('password'),'secure fields are never hover targets');
   // Wait for the next hover message rather than reading straight away; report what arrived if none does.
   const nextHover=async(selector)=>{const before=(await msgs('control_hover_dwell')).length;
     await page.hover(selector);await page.waitForTimeout(800);await page.mouse.move(900,650);
     try{await page.waitForFunction(n=>window.__msgs.filter(m=>m.action==='control_hover_dwell').length>n,before,{timeout:5000});}
     catch(_){throw new Error('no hover recorded for '+selector+': '+JSON.stringify(await msgs('control_hover_dwell')));}
     return (await msgs('control_hover_dwell')).at(-1);};
   const contentHover=await nextHover('#plain');
   assert.equal(contentHover.target.label,undefined,'a role=button container never contributes its text on hover');
   assert.equal((await nextHover('#row')).target.label,undefined,'native button text can be user content, so it is not a label');
   // A control inside a secure widget is never a hover target.
   const beforeSecure=(await msgs('control_hover_dwell')).length;
   await page.hover('#reveal');await page.waitForTimeout(800);await page.mouse.move(900,650);await page.waitForTimeout(300);
   assert.equal((await msgs('control_hover_dwell')).length,beforeSecure,'no hover inside a secure widget');
   // A completed drag records source and destination controls and payload kinds, never data.
   await page.dragAndDrop('#card','#bin');
   const drops=await msgs('drag_drop');assert.equal(drops.length,1);
   assert.equal(drops[0].target.label,undefined);assert.equal(drops[0].metadata.source_target.label,undefined);assert.equal(drops[0].metadata.source_target.role,'button');
   assert.equal(drops[0].metadata.completion,'complete');
   const ends=await msgs('drag_end');assert.equal(ends.at(-1).metadata.completion,'complete');
   // Even structural attributes can mirror a person's typed text.
   await page.evaluate(()=>{const el=document.querySelector('#mirror');el.setAttribute('role','SYNTHETIC_DRAFT_742');
     el.setAttribute('type','SYNTHETIC_DRAFT_742');el.dispatchEvent(new DragEvent('dragstart',{bubbles:true}));});
   const hostile=(await msgs('drag_start')).at(-1);
   assert.equal(hostile.target.tag,'div');assert.equal(hostile.target.role,'');assert.equal(hostile.target.type,'');
   assert(!JSON.stringify(hostile).includes('SYNTHETIC_DRAFT_742'),'page-controlled structural fields do not carry draft text');
   await page.evaluate(()=>{const el=document.querySelector('#mirror');el.setAttribute('role','button');el.removeAttribute('type');});
   // An abandoned drag is marked incomplete.
   await page.evaluate(()=>{const dt=new DataTransfer();dt.setData('text/plain','SECRET PAYLOAD');
     const card=document.getElementById('card');card.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));
     card.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));});
   const abandoned=(await msgs('drag_end')).at(-1);assert.equal(abandoned.metadata.completion,'incomplete');
   assert.deepEqual((await msgs('drag_start')).at(-1).metadata.payload_kinds,['text']);
   // A drop from outside the page (e.g. a file from the desktop) is attributed as external, with kinds only.
   await page.evaluate(()=>{const dt=new DataTransfer();dt.items.add(new File(['contents'],'salary.xlsx'));
     document.getElementById('bin').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));});
   const external=(await msgs('drag_drop')).at(-1);assert.equal(external.metadata.drag_id,'external');assert.deepEqual(external.metadata.payload_kinds,['files']);
   // Editable or text-labelled draggables never contribute their text as a label (structural labels only).
   await page.evaluate(()=>{for(const id of ['draft','plain','ref','field','mirror']){const dt=new DataTransfer();dt.setData('text/plain','x');
     const el=document.getElementById(id);el.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));
     el.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));}});
   const starts=(await msgs('drag_start')).slice(-5);
   assert.equal(starts[0].target.editable,true);
   assert(starts.every(s=>s.target.label===undefined),'no drag target carries a label');
   assert.deepEqual(starts[4].target,{tag:'div',role:'button',type:''},'a label copied from typed text is not recorded');
   const all=JSON.stringify(await msgs());
   for(const leaked of ['SECRET PAYLOAD','salary.xlsx','contents','private draft words','Quarterly salary review','private note words','private label words','private message row','private typed words'])assert(!all.includes(leaked),leaked+' must not be recorded');
   const highFidelity=JSON.stringify((await msgs()).filter(m=>['control_hover_dwell','drag_start','drag_drop','drag_end'].includes(m.action)));
   for(const label of ['Save changes','Invoice card','Archive bin'])assert(!highFidelity.includes(label),'no label of any kind in hover or drag events: '+label);
   assert.equal((await msgs('pointer_trajectory')).length,0,'trajectories are off in Enhanced');
   // Full fidelity: sampled, viewport-normalised pointer movement.
   await load({hover_dwell:false,drag_drop:false,pointer_trajectory:true});
   for(let i=0;i<30;i++){await page.mouse.move(100+i*20,100+i*10);await page.waitForTimeout(40);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const traj=await msgs('pointer_trajectory');assert.equal(traj.length,1);
   const points=traj[0].metadata.points;
   assert(points.length>=5&&points.length<=30,'sampled at the configured rate, not every event: '+points.length);
   assert(points.every(([t,x,y])=>t>=0&&x>=0&&x<=1&&y>=0&&y<=1));
   assert.equal(traj[0].metadata.sample_hz,10);
   assert.equal((await msgs('control_hover_dwell')).length,0);
   // No samples are taken over a secure field; the batch before it is ended there.
   const box=await page.locator('#pw').boundingBox();
   const pwFraction=[ (box.x+box.width/2)/1000, (box.y+box.height/2)/700 ];
   await page.mouse.move(900,650);
   for(let i=0;i<6;i++){await page.mouse.move(800-i*10,600);await page.waitForTimeout(120);}
   for(let i=0;i<6;i++){await page.mouse.move(box.x+5+i*15,box.y+box.height/2);await page.waitForTimeout(120);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const secureBatches=(await msgs('pointer_trajectory')).slice(1);
   const near=([t,x,y])=>Math.abs(y-pwFraction[1])<0.02&&x>=box.x/1000-0.005&&x<=(box.x+box.width)/1000+0.005;
   assert(secureBatches.length>=1,'the batch before the secure field was flushed');
   assert(!secureBatches.flatMap(b=>b.metadata.points).some(near),'no pointer samples over the password field');
   // A password field under a transparent overlay is still secure: nothing is sampled over it.
   const covered=await page.locator('#pw2').boundingBox();
   await page.mouse.move(900,650);
   for(let i=0;i<6;i++){await page.mouse.move(covered.x+5+i*15,covered.y+covered.height/2);await page.waitForTimeout(120);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const overCover=([t,x,y])=>x>=covered.x/1000-0.005&&x<=(covered.x+covered.width)/1000+0.005&&y>=covered.y/700-0.005&&y<=(covered.y+covered.height)/700+0.005;
   assert(!(await msgs('pointer_trajectory')).flatMap(b=>b.metadata.points).some(overCover),'no samples over a covered password field');
   // Closed shadow roots retarget events to the host. The extension API must
   // inspect the actual root, and absent that API must fail closed.
   const closed=await page.locator('#closed-shadow').boundingBox();
   await page.mouse.move(900,650);
   for(let i=0;i<4;i++){await page.mouse.move(closed.x+10+i*15,closed.y+20);await page.waitForTimeout(120);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const overClosed=([t,x,y])=>x>=closed.x/1000&&x<=(closed.x+closed.width)/1000&&y>=closed.y/700&&y<=(closed.y+closed.height)/700;
   assert(!(await msgs('pointer_trajectory')).flatMap(b=>b.metadata.points).some(overClosed),'closed-shadow password is not sampled');
   // Browser hit testing omits pointer-events:none elements. Protect both a
   // normal password and a closed-shadow password hidden behind overlays.
   await page.evaluate(()=>{
     const wrap=(id,left)=>{const host=document.createElement('div');host.id=id;
       host.style.cssText=`position:fixed;left:${left}px;top:500px;width:100px;height:40px;pointer-events:none`;
       document.body.append(host);const overlay=document.createElement('div');overlay.style.cssText=`position:fixed;left:${left}px;top:500px;width:100px;height:40px`;
       document.body.append(overlay);return host;};
     const plain=wrap('no-pointer-password',210);plain.innerHTML='<input type="password" style="width:100px;height:40px;pointer-events:none">';
     const host=wrap('no-pointer-shadow',400);const shadow=host.attachShadow({mode:'closed'});
     shadow.innerHTML='<input type="password" style="width:100px;height:40px;pointer-events:none">';window.__owgShadowRoots.set(host,shadow);
     const frame=document.createElement('iframe');frame.id='opaque-frame';frame.style.cssText='position:fixed;left:560px;top:500px;width:100px;height:40px;pointer-events:none';
     document.body.append(frame);
   });
   const startGeometry=(await msgs('pointer_trajectory')).length;
   for(const left of [210,400,560])for(let i=0;i<4;i++){
     await page.mouse.move(left+10+i*15,520);await page.waitForTimeout(120);
   }
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const geometry=(await msgs('pointer_trajectory')).slice(startGeometry).flatMap(b=>b.metadata.points);
   for(const left of [210,400,560])assert(!geometry.some(([,x,y])=>x>=left/1000&&x<=(left+100)/1000&&y>=500/700&&y<=540/700),
     'no samples over pointer-events:none secure fields, closed roots or embedded frames');
   // Suppressing a secure point must retain the sample throttle. A burst must
   // not cause one full geometry scan per pointer event.
   await page.waitForTimeout(150);
   const inspection=await page.evaluate(()=>{
     window.__owgInspectCount=0;
     for(let i=0;i<30;i++)window.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'mouse',clientX:230,clientY:520}));
     return {calls:window.__owgInspectCount,elements:document.querySelectorAll('*').length};
   });
   assert(inspection.calls>0&&inspection.calls<=inspection.elements+5,'suppressed pointer bursts remain rate-limited');
   // If the bounded inspection cannot cover the document, fail closed rather
   // than silently treating an unseen password as safe.
   await page.evaluate(()=>{const extra=document.createElement('div');extra.id='too-many-elements';
     const fragment=document.createDocumentFragment();for(let i=0;i<10001;i++)fragment.append(document.createElement('span'));
     extra.append(fragment);document.body.append(extra);});
   await page.waitForTimeout(150);
   const beforeLimit=(await msgs('pointer_trajectory')).length;
   for(let i=0;i<4;i++){await page.mouse.move(700+i*15,620);await page.waitForTimeout(120);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   assert.equal((await msgs('pointer_trajectory')).length,beforeLimit,'complex-page scan limits suppress details');
   await page.evaluate(()=>document.querySelector('#too-many-elements').remove());
   const beforeUnavailable=(await msgs('pointer_trajectory')).length;
   await page.evaluate(()=>{delete window.chrome.dom;});
   for(let i=0;i<4;i++){await page.mouse.move(400+i*15,320);await page.waitForTimeout(120);}
   await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   assert.equal((await msgs('pointer_trajectory')).length,beforeUnavailable,'no detail when shadow inspection is unavailable');
   // Standard: nothing new is emitted at all, and a settings change takes effect live.
   await load({hover_dwell:false,drag_drop:false,pointer_trajectory:false});
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);await page.dragAndDrop('#card','#bin');
   for(let i=0;i<10;i++)await page.mouse.move(50+i*30,50);
   assert.deepEqual((await msgs()).map(m=>m.action).filter(a=>!['page_view','click','focus_control'].includes(a)),[]);
   await page.evaluate(()=>window.__changed({openworkgraph_capture_depth_v1:{newValue:{signals:{hover_dwell:true},parameters:{hover_dwell_ms:600},expires_at:new Date(Date.now()+3600e3).toISOString()}}},'local'));
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);
   assert.equal((await msgs('control_hover_dwell')).length,1,'a live settings change turns the signal on');
   // Expired detail emits nothing, even if the stored settings still say it is on.
   await page.evaluate(()=>window.__changed({openworkgraph_capture_depth_v1:{newValue:{signals:{hover_dwell:true},parameters:{hover_dwell_ms:600},expires_at:new Date(Date.now()-1000).toISOString()}}},'local'));
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);await page.waitForTimeout(200);
   assert.equal((await msgs('control_hover_dwell')).length,1,'expired detail is off');
   // A hostile page cannot force one whole-DOM security scan per synthetic event.
   await load({hover_dwell:true,drag_drop:true,pointer_trajectory:false});
   const flood=await page.evaluate(()=>{
     for(let i=0;i<100;i++)document.body.appendChild(document.createElement('div'));
     window.__owgInspectCount=0;
     const el=document.querySelector('#card');
     for(let i=0;i<200;i++)el.dispatchEvent(new DragEvent('dragstart',{bubbles:true,clientX:10,clientY:10}));
     return window.__owgInspectCount;
   });
   assert(flood<6000,'synthetic drag flood must not trigger a scan per event: '+flood);
   // Refill the probe budget before testing the independent depth guard.
   await page.waitForTimeout(900);
   // Secure display:contents around a shadow slot has no bounding box; its
   // light-DOM input is still part of the visual secure widget under overlays.
   const slotted=await page.evaluate(()=>{
     const host=document.createElement('div');host.style='position:fixed;left:100px;top:100px;width:200px;height:50px';
     const input=document.createElement('input');input.style='width:180px;height:40px';host.appendChild(input);
     const shadow=host.attachShadow({mode:'closed'});shadow.innerHTML='<div aria-label="password" style="display:contents"><slot></slot></div>';
     window.__owgShadowRoots.set(host,shadow);document.body.appendChild(host);
     const overlay=document.createElement('div');overlay.style='position:fixed;left:100px;top:100px;width:200px;height:50px;z-index:100';document.body.appendChild(overlay);
     const probe=()=>owgSecureAt({clientX:120,clientY:120,composedPath:()=>[overlay,document.body,document.documentElement,document,window]});
     const secure=probe();shadow.querySelector('div').setAttribute('aria-label','ordinary');const ordinary=probe();
     host.remove();overlay.remove();return {secure,ordinary};
   });
   assert.deepEqual(slotted,{secure:true,ordinary:false},'secure shadow-slot widget is suppressed; ordinary slot remains usable');
   // The scanner must not materialize a full DOM list, and its node limit
   // includes non-elements (a text-only flood cannot bypass the limit).
   const boundedWalk=await page.evaluate(()=>{
     const container=document.createElement('div');
     for(let i=0;i<10010;i++)container.appendChild(document.createTextNode('x'));
     document.body.appendChild(container);
     const original=document.querySelectorAll;let materialized=false;
     document.querySelectorAll=()=>{materialized=true;throw new Error('whole-DOM materialization');};
     try{return {suppressed:owgSecureAt({clientX:10,clientY:10,composedPath:()=>[]}),materialized};}
     finally{document.querySelectorAll=original;container.remove();}
   });
   assert.deepEqual(boundedWalk,{suppressed:true,materialized:false},'too many DOM nodes fail closed without materializing a list');
   // Overlong composed paths must not be interpreted as verified-safe.
   const deep=await page.evaluate(()=>{
     let root=document.body;
     for(let i=0;i<150;i++){const el=document.createElement('div');root.appendChild(el);root=el;}
     root.setAttribute('role','button');root.setAttribute('draggable','true');
     const before=window.__msgs.length;
     root.dispatchEvent(new DragEvent('dragstart',{bubbles:true,clientX:10,clientY:10}));
     return window.__msgs.slice(before).filter(m=>m.action==='drag_start').length;
   });
   assert.equal(deep,0,'overlong event paths fail closed');
   assert.deepEqual(errors,[]);
   console.log('Capture-depth content acceptance passed: semantic hover dwell (menus, activation, quick passes, disabled and secure controls), complete/incomplete/external drag without payloads, sampled normalised trajectories, Standard emits nothing new, live settings changes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
