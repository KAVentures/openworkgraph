const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the owner hides a noise activity from every case analysis and shows it again.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-hide-'));
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
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Sync,2024-03-0${1+i}T08:30:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 18 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgHiddenActivities');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'No activities are hidden.'}).waitFor();
   assert.equal(await page.locator('#owgHiddenActivitiesClear').isDisabled(),true);
   await page.locator('#owgHiddenActivitiesSave').click();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'Choose at least one activity'}).waitFor();
   await page.locator('#owgHiddenActivitiesPick').selectOption(['Sync']);
   await page.locator('#owgHiddenActivitiesSave').click();
   await page.locator('#owgHiddenActivitiesNotice').filter({hasText:'Hidden activities left out: Sync (6 events)'}).waitFor();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'Hidden: Sync · 6 of 18 events left out.'}).waitFor();
   const summary=await page.evaluate(async()=>{const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;return (await fetch('/v1/event-logs/'+id+'/summary')).json();});
   assert.equal(summary.events,12);assert.deepEqual(summary.hidden_activities.activities,['Sync']);
   assert.deepEqual(await page.locator('#owgHiddenActivitiesPick option:checked').allTextContents(),['Sync (6 events)']);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgHiddenActivities'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   // A second view changes the hidden set; the stale draft must not overwrite it.
   await page.evaluate(async()=>{
     const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;
     const response=await fetch('/v1/event-logs/'+id+'/hidden-activities',{
       method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({activities:['Receive']})});
     if(!response.ok)throw Error('Could not change hidden activities');
   });
   await page.locator('#owgHiddenActivitiesClear').click();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'Hidden activities changed since this view loaded.'}).waitFor();
   assert.deepEqual(await page.locator('#owgHiddenActivitiesPick option:checked').allTextContents(),['Sync (6 events)']);
   await page.locator('#owgHiddenActivitiesReload').click();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'Hidden: Receive'}).waitFor();
   assert.deepEqual(await page.locator('#owgHiddenActivitiesPick option:checked').allTextContents(),['Receive (6 events)']);
   await page.locator('#owgHiddenActivitiesClear').click();
   await page.locator('#owgHiddenActivitiesStatus').filter({hasText:'No activities are hidden.'}).waitFor();
   assert.equal(await page.locator('#owgHiddenActivitiesNotice').count(),0);
   assert.deepEqual(errors,[]);
   console.log('Hidden activities browser acceptance passed: choice required, hidden from analyses with a notice, shown again, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
