const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: save the cases matching a filter as a new event log and open it.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-derive-'));
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
   const context=await browser.newContext({});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<10;i++)(i%3?['Receive','Pay']:['Receive','Escalate','Pay']).forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgEventLogDerive');await section.waitFor();await section.scrollIntoViewIfNeeded();
   // Without a filter nothing is copied.
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Choose which cases to copy.'}).waitFor();
   await page.locator('#owgDeriveCohortActivity').selectOption('Escalate');
   await page.locator('#owgDeriveName').fill('Escalated orders');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Saved Escalated orders: 4 of 10 cases, 12 events.'}).waitFor();
   // The current analysis stays open; the picker now lists both logs.
   assert.equal(await page.locator('#owgEventLogPick option').count(),2);
   assert.equal(await page.locator('#owgEventLogDerive').count(),1);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogDerive'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgDeriveOpen').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Opened Escalated orders'}).waitFor();
   assert.match(await page.locator('#owgEventLogPick option:checked').innerText(),/^Escalated orders · 12 events$/);
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogResults table').first().waitFor();
   assert.match(await page.locator('#owgEventLogResults table').first().innerText(),/\n4\t12\t3\t/);
   assert.deepEqual(errors,[]);
   console.log('Derived log browser acceptance passed: filter required, matching cases saved as a new log, current analysis kept, new log opens and analyses, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
