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
  <button id="save" style="width:120px;height:40px">Save</button>
  <button id="off" disabled style="width:120px;height:40px">Disabled</button>
  <input id="pw" type="password" style="width:120px;height:40px">
  <div id="far" style="width:300px;height:200px">empty area</div>
  <div id="card" draggable="true" role="button" aria-label="Invoice card" style="width:100px;height:40px">Invoice</div>
  <div id="bin" role="region" aria-label="Archive bin" style="width:200px;height:80px">Archive</div>
  <script>document.getElementById('bin').addEventListener('dragover',e=>e.preventDefault());
          document.getElementById('bin').addEventListener('drop',e=>e.preventDefault());</script></body></html>`;
 const settings=(signals)=>({signals,parameters:{hover_dwell_ms:600,trajectory_hz:10,max_events_per_minute:600},profile:'custom'});
 const browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
 try{
   const page=await browser.newPage({viewport:{width:1000,height:700}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const load=async(signals)=>{
     await page.goto('about:blank');await page.setContent(html);
     await page.evaluate(([s])=>{window.__msgs=[];window.chrome={runtime:{sendMessage(m){window.__msgs.push(m);return Promise.resolve();}},
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
   assert.equal(hovers[1].target.label,'Save');assert.equal(hovers[1].metadata.ended_by,'activated');
   assert(!JSON.stringify(hovers).includes('password'),'secure fields are never hover targets');
   // A completed drag records source and destination controls and payload kinds, never data.
   await page.dragAndDrop('#card','#bin');
   const drops=await msgs('drag_drop');assert.equal(drops.length,1);
   assert.equal(drops[0].target.label,'Archive bin');assert.equal(drops[0].metadata.source_target.label,'Invoice card');
   assert.equal(drops[0].metadata.completion,'complete');
   const ends=await msgs('drag_end');assert.equal(ends.at(-1).metadata.completion,'complete');
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
   const all=JSON.stringify(await msgs());
   for(const leaked of ['SECRET PAYLOAD','salary.xlsx','contents'])assert(!all.includes(leaked),leaked+' must not be recorded');
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
   // Standard: nothing new is emitted at all, and a settings change takes effect live.
   await load({hover_dwell:false,drag_drop:false,pointer_trajectory:false});
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);await page.dragAndDrop('#card','#bin');
   for(let i=0;i<10;i++)await page.mouse.move(50+i*30,50);
   assert.deepEqual((await msgs()).map(m=>m.action).filter(a=>!['page_view','click','focus_control'].includes(a)),[]);
   await page.evaluate(()=>window.__changed({openworkgraph_capture_depth_v1:{newValue:{signals:{hover_dwell:true},parameters:{hover_dwell_ms:600}}}},'local'));
   await page.hover('#menu');await page.waitForTimeout(800);await page.mouse.move(900,650);
   assert.equal((await msgs('control_hover_dwell')).length,1,'a live settings change turns the signal on');
   assert.deepEqual(errors,[]);
   console.log('Capture-depth content acceptance passed: semantic hover dwell (menus, activation, quick passes, disabled and secure controls), complete/incomplete/external drag without payloads, sampled normalised trajectories, Standard emits nothing new, live settings changes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
