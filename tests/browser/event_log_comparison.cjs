const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: compare two saved event logs from the dashboard.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logcompare-'));
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const importLog=async(name,paths)=>{
     const rows=['case,activity,time'];
     paths.forEach((path,i)=>path.forEach((a,k)=>rows.push(`${name}${i},${a},2024-03-${String(1+i%28).padStart(2,'0')}T${String(k*2).padStart(2,'0')}:00:00Z`)));
     await page.locator('#owgEventLogFile').setInputFiles({name:name+'.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
     await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
     await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
     await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
   };
   await importLog('south',Array.from({length:12},(_,i)=>i%3?['Receive','Approve','Pay']:['Receive','Escalate','Approve','Pay']));
   await importLog('north',Array.from({length:12},(_,i)=>i%4?['Receive','Check','Approve','Pay']:['Receive','Check','Check','Approve','Pay']));
   // The most recent import (north) is selected; compare it with south.
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgLogComparison');await section.waitFor();
   await section.scrollIntoViewIfNeeded();
   assert.equal(await page.locator('#owgLogCompareOther option').count(),2);
   await page.locator('#owgLogCompareRun').click();
   const out=page.locator('#owgLogCompareResults');
   await out.locator('table').first().waitFor();
   const text=await out.innerText();
   assert.match(text,/north \(this log\)/i);
   assert.match(text,/Only in north: Check\./);
   assert.match(text,/Only in south: Escalate\./);
   assert.match(text,/Receive → Approve/);
   assert.match(text,/0 variants occur in both logs/);
   assert.match(text,/not evidence of its cause/);
   assert.equal(await out.locator('table').count(),4);
   async function downloadCaseIds(role){
     const [download]=await Promise.all([page.waitForEvent('download'),
       out.getByRole('button',{name:'Download '+role+' events (CSV)',exact:true}).click()]);
     assert.match(download.suggestedFilename(),new RegExp('-'+role+'-events\.csv$'));
     const lines=(await fs.readFile(await download.path(),'utf8')).trim().split(/\r?\n/).slice(1);
     return [...new Set(lines.map(line=>line.split(',')[0]))].sort();
   }
   assert.equal((await downloadCaseIds('baseline')).length,12);
   const evidence=page.locator('#owgLogCompareEvidence');
   await out.getByRole('button',{name:/Review comparison cases for Receive → Approve \(/}).click();
   await evidence.getByText('8 matching cases in south (case).').waitFor();
   await evidence.getByRole('button',{name:'Open case south1',exact:true}).click();
   await evidence.getByText(/Showing 3 of 3 events/).waitFor();
   assert.match(await evidence.locator('table').last().innerText(),/Approve/);
   await out.getByRole('button',{name:/Review baseline cases for Receive → Check → Check → Approve → Pay/}).click();
   await evidence.getByText('3 matching cases in north (case).').waitFor();
   assert.equal(await evidence.getByRole('button',{name:/Open case north/}).count(),3);
   assert.equal(await evidence.getByRole('heading',{name:'Case south1'}).count(),0);
   await page.locator('#owgCaseFilterRework').selectOption('true');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'3 of 12 cases'}).waitFor();
   await section.getByText('Case selection',{exact:true}).click();
   await page.locator('#owgLogCompareUseActiveFilter').check();
   await page.locator('#owgLogCompareCaseType').selectOption('case');
   await page.locator('#owgLogCompareBaselineFrom').fill('2024-03-04');
   await page.locator('#owgLogCompareBaselineBefore').fill('2024-03-10');
   await page.locator('#owgLogCompareOtherFrom').fill('2024-03-03');
   await page.locator('#owgLogCompareOtherBefore').fill('2024-03-08');
   const [response]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/event-logs/comparison'),
     page.locator('#owgLogCompareRun').click()
   ]);
   const scoped=await response.json();
   assert.equal(scoped.kpis.baseline.cases,2);assert.equal(scoped.kpis.comparison.cases,5);
   assert.equal(scoped.baseline.cohort.rework,true);
   assert.equal(scoped.comparison.cohort.started_before,'2024-03-08T00:00:00Z');
   assert.equal(scoped.comparison.case_notion,'case');
   await out.getByRole('button',{name:/Review baseline cases for entire selection/}).click();
   await evidence.getByText('2 matching cases in north (case).').waitFor();
   assert.deepEqual(await evidence.getByRole('button',{name:/Open case /}).allTextContents(),['north4','north8']);
   await out.getByRole('button',{name:/Review comparison cases for Escalate \(/}).click();
   await evidence.getByText('2 matching cases in south (case).').waitFor();
   assert.deepEqual(await evidence.getByRole('button',{name:/Open case /}).allTextContents(),['south3','south6']);
   assert.deepEqual(await downloadCaseIds('baseline'),['north4','north8']);
   assert.deepEqual(await downloadCaseIds('comparison'),['south2','south3','south4','south5','south6']);
   const save=page.locator('#owgLogCompareJson');
   const [download]=await Promise.all([page.waitForEvent('download'),save.click()]);
   assert.deepEqual(JSON.parse(await fs.readFile(await download.path(),'utf8')),scoped);
   await page.locator('#owgLogCompareOtherFrom').fill('2024-03-09');
   assert.equal(await save.isDisabled(),true);
   assert.equal(await evidence.locator('table').count(),0);
   await page.locator('#owgLogCompareRun').click();
   await out.getByText('A start date must be earlier than its end date.').waitFor();
   await page.locator('#owgLogCompareOtherFrom').fill('2024-03-02');
   let entered,release;
   const seen=new Promise(resolve=>{entered=resolve;});
   const hold=new Promise(resolve=>{release=resolve;});
   await page.route('**/v1/event-logs/comparison',async route=>{
     const pending=await route.fetch();entered();
     await hold;await route.fulfill({response:pending});
   },{times:1});
   await page.locator('#owgLogCompareRun').click();await seen;
   await page.locator('#owgLogCompareOtherFrom').fill('2024-03-05');
   const delivered=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/event-logs/comparison');
   release();await delivered;await page.waitForLoadState('networkidle');
   assert.equal(await save.isDisabled(),true);
   assert.equal(await out.locator('table').count(),0);
   const own=page.locator('#owgLogCompareOther option').filter({hasText:'This log (compare two case selections)'});
   await page.locator('#owgLogCompareOther').selectOption(await own.getAttribute('value'));
   await page.locator('#owgLogCompareUseActiveFilter').uncheck();
   await page.locator('#owgLogCompareBaselineFrom').fill('2024-03-01');
   await page.locator('#owgLogCompareBaselineBefore').fill('2024-03-05');
   await page.locator('#owgLogCompareOtherBefore').fill('2024-03-10');
   const [sameResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/event-logs/comparison'),
     page.locator('#owgLogCompareRun').click()
   ]);
   const same=await sameResponse.json();
   assert.equal(same.baseline.log_id,same.comparison.log_id);
   assert.equal(same.kpis.baseline.cases,4);assert.equal(same.kpis.comparison.cases,5);
   await out.locator('table').first().waitFor();
   await out.getByRole('button',{name:/Review comparison cases for entire selection/}).click();
   await evidence.getByText('5 matching cases in north (case).').waitFor();
   assert.deepEqual(await evidence.getByRole('button',{name:/Open case /}).allTextContents(),['north4','north5','north6','north7','north8']);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgLogComparison'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log comparison browser passed: separate and same-log scopes, case notions, date ranges, active filter, case listings and event histories, JSON export, stale-result guard, WCAG.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
