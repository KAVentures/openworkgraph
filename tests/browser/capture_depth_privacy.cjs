const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the Privacy card sets capture depth without touching sharing settings (#634).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-capture-depth-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','privacy_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',c=>{logs=(logs+c).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
   if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('#tab-privacy').click();
   const card=page.locator('#pvCaptureDepth');await card.waitFor();
   await page.locator('#pvDepthStatus').filter({hasText:'Current: Standard.'}).waitFor();
   const before=await page.evaluate(async()=>(await (await fetch('/v1/work-text/policy')).json()));
   await card.getByLabel('Enhanced').check();
   assert(await page.locator('#pvDepthHover').isChecked()&&!(await page.locator('#pvDepthTrajectory').isChecked()));
   await page.locator('#pvDepthSave').click();
   await page.locator('#pvDepthStatus').filter({hasText:/Current: Enhanced\. Returns to Standard on .*Saved; sharing settings are unchanged\./}).waitFor();
   const enhanced=await page.evaluate(async()=>(await (await fetch('/v1/capture-depth')).json()));
   const days=(Date.parse(enhanced.expires_at)-Date.now())/864e5;assert(days>6.9&&days<=7,'Enhanced lasts the chosen 7 days: '+days);
   await card.getByLabel('Full fidelity').check();await page.locator('#pvDepthSave').click();
   await page.locator('#pvDepthStatus').filter({hasText:'Tick the acknowledgement'}).waitFor();
   await page.locator('#pvDepthAck').check();await page.locator('#pvDepthHz').fill('8');await page.locator('#pvDepthRetention').fill('14');await page.locator('#pvDepthDuration').selectOption('1');await page.locator('#pvDepthSave').click();
   await page.locator('#pvDepthStatus').filter({hasText:'Current: Full fidelity.'}).waitFor();
   const saved=await page.evaluate(async()=>(await (await fetch('/v1/capture-depth')).json()));
   assert.equal(saved.parameters.trajectory_hz,8);assert.equal(saved.parameters.retention_days,14);assert(Date.parse(saved.expires_at)-Date.now()<=864e5,'Full lasts the chosen 1 day');assert.equal(saved.signals.pointer_trajectory,true);
   // /v1/browser-context is only for the paired extension; tests/test_capture_depth.py checks its capture_depth.
   assert.equal((await page.evaluate(async()=>(await fetch('/v1/browser-context')).status)) >= 400,true);
   const after=await page.evaluate(async()=>(await (await fetch('/v1/work-text/policy')).json()));
   assert.deepEqual(after,before,'work-text sharing is unchanged by capture depth');
   await page.locator('#pvDepthCoverage').click();
   await page.locator('#pvDepthResult').getByText('desktop hover dwell').waitFor();
   const coverage=await page.locator('#pvDepthResult').textContent();
   assert.match(coverage,/desktop pointer trajectory\s*on but desktop collector not seen/,'desktop modalities follow the chosen depth');
   assert.doesNotMatch(coverage,/unsupported/);
   await page.locator('#pvDepthFrictionShow').click();
   await page.locator('#pvDepthResult').getByText(/candidates in the last 24 hours/).waitFor();
   await page.locator('#pvDepthHeatmap').click();
   await page.locator('#pvDepthResult').getByText(/No sampled pointer movement in the last 7 days/).waitFor();
   page.once('dialog',d=>d.accept());
   await page.locator('#pvDepthDelete').click();
   await page.locator('#pvDepthStatus').filter({hasText:/Deleted 0 interaction detail events\. Other activity is kept\./}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#pvCaptureDepth'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Capture-depth Privacy acceptance passed: Standard by default, Enhanced, Full fidelity only after acknowledgement, sharing unchanged, coverage manifest, friction candidates, retention, pointer heatmap, delete now, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
