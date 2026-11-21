const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: root cause for the cases carrying a tag.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-rctag-'));
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
   // 40 cases; the 12 tagged "complaint" all go through Escalate, which only 3 other cases do.
   const rows=['case,activity,time,who'];
   for(let i=0;i<40;i++){const steps=['Open',...((i<12||i%10===0)?['Escalate']:[]),'Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i%28).padStart(2,'0')}T${String(8+j).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'complaints.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 94 events'}).waitFor();
   const tagged=await page.evaluate(async()=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='complaints').log_id;
     return (await fetch('/v1/event-logs/'+id+'/case-tags',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({tag:'complaint',case_ids:Array.from({length:12},(_,i)=>'c'+i)})})).json();});
   assert.equal(tagged.tagged,12);
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogRootCauseRun').waitFor();await page.locator('#owgEventLogRootCauseRun').scrollIntoViewIfNeeded();
   await page.locator('#owgEventLogOutcome').selectOption('tagged');
   assert.equal(await page.locator('#owgEventLogOutcomeTag').isVisible(),true);
   await page.locator('#owgEventLogRootCauseRun').click();
   await page.locator('#owgEventLogRootCauseResults').filter({hasText:'Enter the tag of the cases to explain.'}).waitFor();
   await page.locator('#owgEventLogOutcomeTag').fill('complaint');await page.locator('#owgEventLogRootCauseRun').click();
   const results=page.locator('#owgEventLogRootCauseResults');
   await results.filter({hasText:'Outcome in 30.0% of 40 cases'}).waitFor();
   assert((await results.locator('tbody tr').first().textContent()).includes('activity: Escalate'));
   assert.deepEqual(errors,[]);
   console.log('Root cause with a tagged outcome browser acceptance passed: tag field shown, the tagged cases explained by the step they share.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
