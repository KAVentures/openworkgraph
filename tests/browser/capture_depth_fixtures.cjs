const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
// Real-browser fixtures for high-fidelity capture (#634) on harder pages: a single-page app that re-renders
// the control under a resting pointer, a same-origin iframe, and rapid passes over many controls.
(async()=>{
 const root=path.resolve(__dirname,'..','..','browser_extension');
 const content=await fs.readFile(path.join(root,'content.js'),'utf8');
 const depth=await fs.readFile(path.join(root,'capture_depth.js'),'utf8');
 const settings={signals:{hover_dwell:true,drag_drop:true,pointer_trajectory:true},
   parameters:{hover_dwell_ms:600,trajectory_hz:10,max_events_per_minute:600},profile:'custom',
   expires_at:new Date(Date.now()+3600e3).toISOString()};
 // The extension DOM API exposes open and closed shadow roots; without it high-detail capture fails closed.
 const stub=`window.chrome={dom:{openOrClosedShadowRoot(el){return el.shadowRoot;}},runtime:{sendMessage(m){(window.top.__all||(window.top.__all=[])).push(m);return Promise.resolve();}},
   storage:{local:{get:async()=>({openworkgraph_capture_depth_v1:${JSON.stringify(settings)}}),set:async()=>{}},onChanged:{addListener(){}}}};`;
 const html=`<!doctype html><html><body style="margin:0">
  <div id="app" style="position:absolute;left:20px;top:20px"><button id="spa" style="width:120px;height:40px">Send</button></div>
  <div id="swap" style="position:absolute;left:20px;top:100px"><button id="old" style="width:120px;height:40px">A</button></div>
  <div id="row" style="position:absolute;left:20px;top:180px">${Array.from({length:12},(_,i)=>`<button style="width:50px;height:30px">${i}</button>`).join('')}</div>
  <iframe id="frame" style="position:absolute;left:500px;top:100px;width:400px;height:300px;border:0"
    srcdoc="<body style='margin:0'><button id=inner style='width:120px;height:40px'>In</button></body>"></iframe></body></html>`;
 const browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
 try{
   const page=await browser.newPage({viewport:{width:1000,height:700}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.setContent(html);await page.waitForTimeout(200);
   for(const frame of page.frames()){await frame.evaluate(stub);await frame.addScriptTag({content});await frame.addScriptTag({content:depth});}
   await page.waitForTimeout(100);
   const all=async(action)=>(await page.evaluate(()=>window.__all||[])).filter(m=>!action||m.action===action);
   const settle=async()=>{await page.mouse.move(990,690);await page.waitForTimeout(250);};
   await settle();

   // 1. A re-render that replaces the hovered button with an equivalent one keeps one continuous hover.
   await page.hover('#spa');await page.waitForTimeout(300);
   await page.evaluate(()=>{const n=document.createElement('button');n.id='spa';n.style.cssText='width:120px;height:40px';n.textContent='Send';
     document.getElementById('app').replaceChildren(n);});
   await page.waitForTimeout(700);await settle();
   let hovers=await all('control_hover_dwell');
   assert.equal(hovers.length,1,JSON.stringify(hovers));
   assert(hovers[0].metadata.duration_ms>=950,'the dwell spans the re-render: '+hovers[0].metadata.duration_ms);
   assert.equal(hovers[0].metadata.control_rerendered,true);

   // 2. A re-render into a different kind of control ends the first hover and starts a new one.
   await page.hover('#old');await page.waitForTimeout(700);
   await page.evaluate(()=>{const n=document.createElement('div');n.setAttribute('role','tab');n.style.cssText='width:120px;height:40px';
     document.getElementById('swap').replaceChildren(n);});
   await page.mouse.move(60,121);await page.waitForTimeout(700);await settle();
   hovers=(await all('control_hover_dwell')).slice(1);
   assert.deepEqual(hovers.map(h=>h.target.role||h.target.tag),['button','tab'],JSON.stringify(hovers));
   assert(hovers.every(h=>!h.metadata.control_rerendered));

   // 3. Rapid passes across many controls are not dwells.
   const before=(await all('control_hover_dwell')).length;
   for(let i=0;i<12;i++){await page.mouse.move(45+i*50,195);await page.waitForTimeout(40);}
   await settle();
   assert.equal((await all('control_hover_dwell')).length,before,'quick passes record nothing');

   // 4. A same-origin iframe: hover and pointer samples come from the frame, marked as a subframe,
   //    with points normalised to the frame's own viewport.
   const box=await page.locator('#frame').boundingBox();
   await page.mouse.move(box.x+60,box.y+20);await page.waitForTimeout(800);await settle();
   const inner=(await all('control_hover_dwell')).at(-1);
   assert.equal(inner.metadata.frame_kind,'subframe');assert.equal(inner.metadata.top_frame,false);assert.equal(inner.target.tag,'button');
   for(let i=0;i<10;i++){await page.mouse.move(box.x+200+i*10,box.y+150);await page.waitForTimeout(120);}
   await settle();
   for(const frame of page.frames())await frame.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   const framed=(await all('pointer_trajectory')).filter(m=>m.metadata.frame_kind==='subframe');
   assert(framed.length>=1,'the frame sent its own trajectory');
   const pts=framed.flatMap(m=>m.metadata.points);
   // The sweep ran across the frame's middle (y = 150 of 300 px, x = 200..290 of 400 px).
   const sweep=pts.filter(([t,x,y])=>Math.abs(y-0.5)<0.02);
   assert(sweep.length>=5&&sweep.every(([t,x])=>x>=0.45&&x<=0.8),'points are fractions of the frame viewport: '+JSON.stringify(pts));
   assert(pts.every(([t,x,y])=>x>=0&&x<=1&&y>=0&&y<=1));
   assert.deepEqual(errors,[]);
   console.log('Capture-depth fixture acceptance passed: re-rendered controls keep one hover, a different control starts a new one, quick passes over many controls record nothing, iframe hover and frame-normalised pointer samples.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
