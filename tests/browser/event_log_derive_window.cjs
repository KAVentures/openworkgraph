const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: saving a log trimmed to a time window keeps only the events inside it.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-trim-'));
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
   const context=await browser.newContext({timezoneId:'UTC'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // 6 cases on 1..6 March: Receive 08:00, Pay 09:00.
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgEventLogDerive');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Choose which cases to copy. Or set a time window or an activity trim.'}).waitFor();
   await page.locator('#owgDeriveEventsFrom').fill('2024-03-02T08:30');
   await page.locator('#owgDeriveEventsBefore').fill('2024-03-04T00:00');
   await page.locator('#owgDeriveName').fill('Early March');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Saved Early March: 6 of 6 cases, 3 events. Only events in the time window were kept: 2 cases have events in it; 9 events outside it were left out.'}).waitFor();
   const paths=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;const id=logs.find(l=>l.name==='Early March').log_id;
     return (await (await fetch('/v1/event-logs/'+id+'/variants')).json()).variants.map(v=>[v.path.join('>'),v.cases]);});
   assert.deepEqual(paths.sort(),[['Pay',1],['Receive>Pay',1]]);
   // Activity trim: keep each case from its first Pay to the case end.
   await page.locator('#owgDeriveEventsFrom').fill('');await page.locator('#owgDeriveEventsBefore').fill('');
   await page.locator('#owgDeriveTrimFrom').selectOption('Pay');
   await page.getByRole('button',{name:'Preview copy',exact:true}).click();
   await page.locator('#owgDerivePreview').filter({hasText:'6 of 6 cases match. The copy would retain 6 events and 6 linked objects across 6 matching cases with the trim activities.'}).waitFor();
   await page.locator('#owgDeriveName').fill('Payments only');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Saved Payments only: 6 of 6 cases, 6 events. Only events between the trim activities were kept: 6 cases have them; 6 events outside were left out.'}).waitFor();
   await page.locator('#owgDeriveExportFormat').selectOption('csv');
   const [trimmedFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   assert.deepEqual((await fs.readFile(await trimmedFile.path(),'utf8')).trim().split(/\r?\n/).slice(1).map(line=>line.split(',')[1]),Array(6).fill('Pay'));
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogDerive'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Derived time window browser acceptance passed: filter, window or trim required, events outside the window left out, activity trim saved and exported, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
