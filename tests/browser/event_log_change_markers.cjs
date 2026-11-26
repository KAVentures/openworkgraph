const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the owner saves a change date once and picks it again for impact.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pishare-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
      const box=page.locator('#owgImpact');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>{const s=document.querySelector('#owgImpactMarker');return !!s&&s.options[0].textContent==='No saved changes yet';});
   await page.locator('#owgImpactMarkerSave').click();
   await page.locator('#owgImpactMarkerStatus').filter({hasText:'Choose the date the change went live first.'}).waitFor();
   await page.locator('#owgImpactDate').fill('2024-03-04');
   await page.locator('#owgImpactMarkerSave').click();
   await page.locator('#owgImpactMarkerStatus').filter({hasText:'Give the change a label.'}).waitFor();
   await page.locator('#owgImpactMarkerLabel').fill('Automation went live');
   await page.locator('#owgImpactMarkerSave').click();
   await page.locator('#owgImpactMarkerStatus').filter({hasText:'Saved the change on 2024-03-04.'}).waitFor();
   const options=await page.locator('#owgImpactMarker option').allTextContents();
   assert.deepEqual(options,['Pick a saved change','2024-03-04 \u2014 Automation went live'],options.join('|'));
   // The drift section lists the recorded change against its window.
   await page.locator('#owgConceptDriftWindow').selectOption('week');
   await page.locator('#owgConceptDriftRun').click();
   await page.locator('#owgConceptDriftMarkers').getByText('Automation went live').waitFor();
   assert.match(await page.locator('#owgConceptDriftMarkers').textContent(),/2024-03-04\s*2024-03-04\s*No/);
   await page.addScriptTag({content:axeSource});
   const driftAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgConceptDrift'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(driftAudit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   // After re-analysis the saved change is still there and fills the date.
   await page.locator('#owgEventLogAnalyze').click();
   await page.waitForFunction(()=>{const s=document.querySelector('#owgImpactMarker');return !!s&&s.options.length===2;});
   assert.equal(await page.locator('#owgImpactDate').inputValue(),'');
   const value=await page.locator('#owgImpactMarker option').nth(1).getAttribute('value');
   await page.locator('#owgImpactMarker').selectOption(value);
   assert.equal(await page.locator('#owgImpactDate').inputValue(),'2024-03-04');
   await page.locator('#owgImpactMarkerStatus').filter({hasText:'Using the change "Automation went live" on 2024-03-04.'}).waitFor();
   await page.locator('#owgImpactMarkerRemove').click();
   await page.locator('#owgImpactMarkerStatus').filter({hasText:'Removed the saved change "Automation went live".'}).waitFor();
   assert.equal(await page.locator('#owgImpactMarker option').count(),1);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgImpact'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Change markers browser acceptance passed: input checks, saved change kept after re-analysis, fills the impact date, listed against its drift window, removed, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
