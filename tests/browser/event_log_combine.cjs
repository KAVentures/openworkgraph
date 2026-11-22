const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: two saved logs are combined into a new log with shared case IDs, and the new log opens.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-combine-'));
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
   const load=async(name,rows)=>{
     await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'text/csv',buffer:Buffer.from(['case,activity,time',...rows].join('\n'))});
     await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
     await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
     await page.locator('#owgEventLogStatus').filter({hasText:'Imported '+rows.length+' events'}).waitFor();
   };
   await load('invoices.csv',[0,1,2,3].map(i=>`c${i+2},Invoice,2024-03-0${1+i}T12:00:00Z`));
   await load('orders.csv',[0,1,2,3].map(i=>`c${i},Order,2024-03-0${1+i}T08:00:00Z`));
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgCombine');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgCombineRun').click();
   await page.locator('#owgCombineResult').filter({hasText:'Tick at least one log'}).waitFor();
   await box.getByLabel(/invoices/).check();
   assert.equal(await page.locator('#owgCombineName').inputValue(),'orders (combined)');
   await page.locator('#owgCombineRun').click();
   await page.locator('#owgCombineResult').filter({hasText:'Saved orders (combined): 8 events from 2 logs. 2 IDs appear in more than one log and were joined.'}).waitFor();
   const names=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>l.name).sort());
   assert.deepEqual(names,['invoices','orders','orders (combined)']);
   await page.locator('#owgCombineOpen').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Opened orders (combined)'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgCombine').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCombine'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Combine browser acceptance passed: tick required, combined log saved with joined IDs, opened, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
