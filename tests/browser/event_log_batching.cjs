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
   // The per-activity summary downloads as CSV, including activities without batches.
   const [batchCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgBatchReportCsv').click()]);
   const batchLines=(await fs.readFile(await batchCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.match(batchLines[0],/^activity,events,batched_share,batches,simultaneous_batches,median_batch_size,/);
   assert(batchLines.some(l=>/^Approve,10,1,2,0,5,5,5,/.test(l)),batchLines.join('|'));
   assert(batchLines.some(l=>/^Post,\d+,0,0,/.test(l)),batchLines.join('|'));
   await page.locator('#owgBatchGap').fill('61');
   assert.equal(await page.locator('#owgBatchReportCsv').isDisabled(),true);
   await page.locator('#owgBatchGap').fill('60');await page.locator('#owgBatchRun').click();
   await page.locator('#owgBatchReportCsv:not([disabled])').waitFor();
   const batches=page.locator('#owgBatchList'),cases=page.locator('#owgBatchCases');
   await out.getByRole('button',{name:'View batches for Approve',exact:true}).click();
   await batches.getByRole('status').filter({hasText:'2 batches. Showing 2 on this page.'}).waitFor();
   await batches.getByRole('button',{name:'Inspect cases for batch 1 on this page, starting 2024-03-05 17:00:00 UTC',exact:true}).click();
   await cases.getByRole('status').filter({hasText:'5 matching cases across 5 batch events.'}).waitFor();
   assert.deepEqual(await cases.locator('tbody tr td:first-child').allTextContents(),['d1i0','d1i1','d1i2','d1i3','d1i4']);
   await cases.getByRole('button',{name:'Open case d1i0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 3 of 3 events'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogBatching'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgCaseFilterEndedBefore').fill('2024-03-05');await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 5 of 10 cases'}).waitFor();
   await page.locator('#owgBatchRun').click();
   await out.getByRole('button',{name:'View batches for Approve',exact:true}).click();
   await batches.getByRole('status').filter({hasText:'1 batch. Showing 1 on this page.'}).waitFor();
   await page.locator('#owgBatchGap').fill('2');await page.locator('#owgBatchRun').click();
   await out.getByText('No batches with these settings.').waitFor();
   assert.equal(await batches.getByRole('status').count(),0);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Batch detection browser acceptance passed: sequential batches, case evidence and event history, active cohort, summary CSV, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
