const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the owner schedules a log for automatic deletion and clears it.
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
      const box=page.locator('#owgExpiry');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgExpiryStatus').filter({hasText:'This log is kept until you delete it.'}).waitFor();
   assert.equal(await page.locator('#owgExpiryClear').isDisabled(),true);
   const day=new Date(Date.now()+20*86400000).toISOString().slice(0,10);
   await page.locator('#owgExpiryDate').fill(day);
   page.once('dialog',d=>d.dismiss());await page.locator('#owgExpirySave').click();
   await page.locator('#owgExpiryStatus').filter({hasText:'This log is kept until you delete it.'}).waitFor();
   page.once('dialog',d=>d.accept());await page.locator('#owgExpirySave').click();
   await page.locator('#owgExpiryStatus').filter({hasText:'This log will be deleted after '+day+' 23:59 UTC.'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgExpiryStatus').filter({hasText:'This log will be deleted after '+day}).waitFor();
   assert.equal(await page.locator('#owgExpiryDate').inputValue(),day);
   await page.locator('#owgExpiryClear').click();
   await page.locator('#owgExpiryStatus').filter({hasText:'This log is kept until you delete it.'}).waitFor();
   assert.equal(await page.locator('#owgExpiryDate').inputValue(),'');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgExpiry'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Automatic deletion browser acceptance passed: confirmation, scheduled date kept after re-analysis, cleared, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
