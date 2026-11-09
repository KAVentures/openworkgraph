const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: find batch processing in an event log (R14).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-batch-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time,who'];
   for(let d=0;d<2;d++)for(let i=0;i<5;i++){const day=`2024-03-0${4+d}`;
     rows.push(`d${d}i${i},Receive,${day}T0${8+i}:00:00Z,clerk`,`d${d}i${i},Approve,${day}T17:00:${String(10*i).padStart(2,'0')}Z,Ana`,`d${d}i${i},Post,${day}T18:${String(20*i%60).padStart(2,'0')}:00Z,Ben`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'invoices.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 30 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogBatching').waitFor();
   await page.locator('#owgBatchRun').click();
   const out=page.locator('#owgBatchResults');await out.locator('table').waitFor();
   const text=await out.textContent();
   assert.match(text,/10 of 30 events were in batches \(activity and resource\)/,text);
   assert.match(text,/Approve100\.0% of 102/,text);
   assert.match(text,/size 5, wait up to/,text);
   assert.doesNotMatch(text,/Post\d/,'one-by-one work is not a batch');
   await page.locator('#owgBatchGap').fill('2');await page.locator('#owgBatchRun').click();
   await out.getByText('No batches with these settings.').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogBatching'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Batch detection browser acceptance passed: sequential batches found with a simulation proposal, one-by-one work excluded, tighter gap finds none, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
