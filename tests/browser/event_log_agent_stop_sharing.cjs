const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: one action stops sharing every log with AI agents.
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
   const box=page.locator('#owgAgentShare');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Not shared: AI agents cannot read this log.'}).waitFor();
   const piLogs=()=>page.evaluate(async()=>(await (await fetch('/v1/process-intelligence/logs')).json()).logs.map(l=>[l.name,l.level]));
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareLevel').selectOption('cases');await page.locator('#owgAgentShareSave').click();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Shared with AI agents since'}).waitFor();
   assert.deepEqual(await piLogs(),[['orders','cases']]);
   await page.locator('#owgAgentShareEvery').filter({hasText:'Shared with AI agents now: orders (also individual cases).'}).waitFor();
   // Cancelling the confirmation changes nothing.
   page.once('dialog',d=>d.dismiss());await page.locator('#owgAgentShareStopAll').click();
   assert.deepEqual(await piLogs(),[['orders','cases']]);
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareStopAll').click();
   await page.locator('#owgAgentShareStopAllStatus').filter({hasText:'Stopped sharing 1 log: orders.'}).waitFor();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Not shared: AI agents cannot read this log.'}).waitFor();
   assert.equal(await page.locator('#owgAgentShareLevel').inputValue(),'');
   assert.deepEqual(await piLogs(),[]);
   await page.locator('#owgAgentShareEvery').filter({hasText:'No log is shared with AI agents.'}).waitFor();
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareStopAll').click();
   await page.locator('#owgAgentShareStopAllStatus').filter({hasText:'No log was shared with AI agents.'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAgentShare'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Stop-sharing browser acceptance passed: confirmation, every share withdrawn, status refreshed, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
