const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: import a log, analyse, KPIs, conformance, root cause, export and delete.
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

   const rows=['case,activity,time,who'];
   for(let i=0;i<26;i++){
     const activities=i<25?['Receive','Check','Alternate '+i,'Close']:['Receive','Check','Approve','Close'];
     activities.forEach((activity,index)=>rows.push(['case'+i,activity,'2024-02-01T00:00:0'+index+'Z',i<25?'inside':'outside'].join(',')));
   }
   await page.locator('#owgEventLogFile').setInputFiles({name:'variants.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
   await page.locator('#owgEventLogActivity').selectOption('activity');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 104 events'}).waitFor({timeout:10000}).catch(async error=>{throw Error((await page.locator('#owgEventLogStatus').textContent())+' '+error.message);});
   await page.locator('#owgEventLogAnalyze').click();
   await page.evaluate(async()=>{
     const labels=['Receive','Check','Approve','Close'];
     const process_model={goal:'Complete the declared claim review',baseline_source:'user_declared',baseline_path:labels,
       activities:labels.map(label=>({id:label.toLowerCase(),label})),
       transitions:labels.slice(1).map((label,i)=>({source:labels[i].toLowerCase(),target:label.toLowerCase()})),
       entry_activity_id:'receive',terminal_activity_ids:['close']};
     const body={workflow_id:'claims_review',title:'Reviewed claims',user_confirmed:true,expected_revision:0,process_model};
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(!response.ok)throw new Error(await response.text());return response.json();
   });
   await page.locator('#owgEventLogGraphLoad').click();
   await page.locator('#owgEventLogGraphPick').selectOption('claims_review');
   await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogSavedGraphMap svg').waitFor();

   const result=page.locator('#owgEventLogGraphResults');
   await page.locator('#owgEventLogGraphCohortResource').fill('inside');
   await page.locator('#owgEventLogGraphRun').click();
   await result.getByText(/Showing 1–20 of 25/).waitFor();
   await result.getByRole('button',{name:'Next variants'}).click();
   await result.getByText(/Showing 21–25 of 25/).waitFor();
   await result.getByText('Show alignment',{exact:true}).first().click();
   await result.getByText('Expected by model',{exact:true}).first().waitFor();
   const [request]=await Promise.all([
     page.waitForRequest(req=>req.url().includes('/cases/query')&&req.method()==='POST'),
     result.getByRole('button',{name:'Inspect cases',exact:true}).first().click()
   ]);
   assert.equal(request.postDataJSON().cohort.resource,'inside');
   assert.ok(request.postDataJSON().path.length);
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'1 matching cases'}).waitFor();
   await result.getByRole('button',{name:'Previous variants'}).click();
   await result.getByText(/Showing 1–20 of 25/).waitFor();
   await page.locator('#owgEventLogGraphVariantStatus').selectOption('match');
   await result.getByText(/of 0 selected variant results/).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogSavedGraph'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   assert.deepEqual(errors,[]);
   console.log('Browser passed variant paging, alignments, scoped evidence, result filters and axe.');
 }finally{
   if(browser)await browser.close();
   server.kill();
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null&&server.signalCode===null)server.kill('SIGKILL');
   await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
