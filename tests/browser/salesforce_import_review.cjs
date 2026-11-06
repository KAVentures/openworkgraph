const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Synthetic browser acceptance: offline projection, explicit review, staging, and separate storage consent.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-event-logs-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Event-log server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Event-log server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage({acceptDownloads:true}),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const card=page.locator('#owgEventLogCard');await card.waitFor();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);

   // The latest header read controls readiness when file and delimiter change quickly.
   await page.locator('#owgEventLogFile').setInputFiles({name:'first.csv',mimeType:'text/csv',buffer:Buffer.from('old_case,old_activity,old_time\na,b,c\n')});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready'}).waitFor();
   await page.evaluate(()=>{window.__originalText=Blob.prototype.text;window.__readQueue=[];Blob.prototype.text=function(){const blob=this;return new Promise(resolve=>window.__readQueue.push(()=>window.__originalText.call(blob).then(resolve)));};});
   await page.locator('#owgEventLogFile').setInputFiles({name:'second.csv',mimeType:'text/csv',buffer:Buffer.from('new_case;new_activity;new_time\na;b;c\n')});
   await page.waitForFunction(()=>window.__readQueue.length===1);
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.waitForFunction(()=>window.__readQueue.length===2);
   await page.locator('#owgEventLogConsent').check();
   await page.evaluate(()=>window.__readQueue[0]());
   await page.waitForTimeout(100);
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/Reading file columns/);
   await page.evaluate(()=>{Blob.prototype.text=window.__originalText;window.__readQueue[1]();});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'new_case');
   assert.equal(await page.locator('#owgEventLogActivity').inputValue(),'new_activity');
   assert.equal(await page.locator('#owgEventLogTime').inputValue(),'new_time');
   assert.equal(await page.locator('#owgEventLogImport').isEnabled(),true);
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false);
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
   await page.locator('#owgEventLogFile').setInputFiles([]);

   const csv='Id,CaseId,CreatedDate,Field,NewValue,OldValue\n017000000000001,500000000000001,2026-01-01T00:00:00Z,created,,SECRET_OLD\n017000000000002,500000000000001,2026-01-01T01:00:00Z,Status,<img src=x onerror=window.__attack=1>,SECRET_OLD\n017000000000003,500000000000001,invalid,Description,SECRET_DESCRIPTION,\n';
   await page.locator('#owgSalesforceFile').setInputFiles({name:'bad.csv',mimeType:'text/csv',buffer:Buffer.from([255])});
   await page.locator('#owgSalesforceAccount').fill('synthetic-sandbox');
   await page.locator('#owgSalesforceCases').fill('500000000000001');
   await page.locator('#owgSalesforceConsent').check();
   await page.locator('#owgSalesforcePrepare').click();
   await page.locator('#owgSalesforceReview').getByText('The export must be valid UTF-8.').waitFor();
   await page.locator('#owgSalesforceFile').setInputFiles({name:'history.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
   await page.locator('#owgSalesforceAccount').fill('synthetic-sandbox');
   await page.locator('#owgSalesforceCases').fill('500000000000001');
   assert.equal(await page.locator('#owgSalesforcePrepare').isDisabled(),true);
   await page.locator('#owgSalesforceConsent').check();
   const prepared=page.waitForResponse(r=>r.url().endsWith('/prepare-salesforce'));
   await page.locator('#owgSalesforcePrepare').click();
   const response=await prepared; const packet=await response.json();assert.equal(response.status(),200,JSON.stringify(packet));
   assert.equal(packet.persisted,false);assert.equal(packet.report.events,2);
   assert.equal(packet.preview.length,2);assert.equal(packet.preview_omitted,0);
   assert.equal(await page.locator('#owgSalesforceReview img').count(),0);
   assert.equal(await page.evaluate(()=>window.__attack),undefined);
   assert.equal(packet.namespace.length,38);
   assert.doesNotMatch(packet.csv,/SECRET|500000000000001/);
   await page.locator('#owgSalesforceStage').waitFor();
   assert.equal(await page.locator('#owgSalesforceStage').isDisabled(),true);
   await page.addScriptTag({content:axeSource});
   const accessibility=await page.evaluate(()=>window.axe.run(document.querySelector('#owgSalesforceSource'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(accessibility.violations.map(v=>v.id),[]);
   // Withdrawing local-processing confirmation invalidates the review.
   await page.locator('#owgSalesforceConsent').uncheck();
   assert.equal(await page.locator('#owgSalesforceStage').count(),0);
   await page.locator('#owgSalesforceConsent').check();
   await page.locator('#owgSalesforcePrepare').click();
   await page.locator('#owgSalesforceStage').waitFor();
   const stored=await page.evaluate(async()=>await(await fetch('/v1/event-logs')).json());
   assert.equal(stored.logs.length,0);
   // An earlier CSV column read must not overwrite a later reviewed Salesforce mapping.
   await page.evaluate(()=>{
     window.__originalBlobText=Blob.prototype.text;
     Blob.prototype.text=function(){const blob=this;return new Promise(resolve=>{window.__releaseOldColumns=()=>window.__originalBlobText.call(blob).then(resolve);});};
   });
   await page.locator('#owgEventLogFile').setInputFiles({name:'earlier.csv',mimeType:'text/csv',buffer:Buffer.from('other_case,other_activity,other_time\na,b,c\n')});
   await page.waitForFunction(()=>typeof window.__releaseOldColumns==='function');
   await page.locator('#owgSalesforceReviewed').check();
   await page.locator('#owgSalesforceStage').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Salesforce events staged'}).waitFor();
   await page.evaluate(()=>{Blob.prototype.text=window.__originalBlobText;window.__releaseOldColumns();});
   await page.waitForTimeout(100);
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'case_id');
   assert.equal(await page.locator('#owgEventLogActivity').inputValue(),'activity');
   assert.equal(await page.locator('#owgEventLogTime').inputValue(),'timestamp');
   assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false);
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
   assert.equal(await page.locator('#owgEventLogUseIdentity').isChecked(),true);
   assert.equal(await page.locator('#owgEventLogEventId').inputValue(),'source_event_id');
   assert.equal(await page.locator('#owgEventLogSourceNamespace').inputValue(),packet.namespace);
   // Changing the selection invalidates the prepared file and prior retention consent.
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgSalesforceAccount').fill('another-sandbox');
   assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false);
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
   assert.equal(await page.locator('#owgEventLogFile').evaluate(el=>el.files.length),0);
   await page.locator('#owgSalesforceAccount').fill('synthetic-sandbox');
   await page.locator('#owgSalesforceConsent').check();
   await page.locator('#owgSalesforcePrepare').click();
   await page.locator('#owgSalesforceReviewed').check();
   await page.locator('#owgSalesforceStage').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Salesforce events staged'}).waitFor();
   assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false);
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
   await page.locator('#owgSalesforceAccount').fill('another-sandbox');
   assert.equal(await page.locator('#owgSalesforceStage').count(),0);
   assert.equal(await page.locator('#owgSalesforceConsent').isChecked(),false);
   const denied=await page.evaluate(async()=>{const r=await fetch('/v1/event-logs/prepare-salesforce',{method:'POST',headers:{Authorization:'Bearer fake','Content-Type':'application/json'},body:'{}'});return r.status;});
   assert.ok([401,403].includes(denied));
   assert.deepEqual(errors,[]);
   console.log('Salesforce preparation, review, consented supplied-ID import and invalidation passed');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
