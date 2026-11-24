const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: due-date adherence from an attribute and from days after start.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-footprints-'));
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
   // 20 invoices due 10 days after receipt: 12 paid on time, 6 paid 2.38 days late, 2 never paid (overdue).
   const rows=['case,activity,time,who,due'];
   for(let i=0;i<20;i++){const d=String(1+i).padStart(2,'0'),due=`2024-01-${String(11+i).padStart(2,'0')}`;
     rows.push(`inv${i},Receive invoice,2024-01-${d}T09:00:00Z,ana,${due}`);
     if(i<12)rows.push(`inv${i},Pay,2024-01-${String(5+i).padStart(2,'0')}T09:00:00Z,ana,`);
     else if(i<18)rows.push(`inv${i},Pay,2024-01-${String(14+i).padStart(2,'0')}T09:00:00Z,ana,`);
     else rows.push(`inv${i},Remind,2024-01-31T09:00:00Z,ana,`);}
   rows.push('late-marker,Receive invoice,2024-02-27T09:00:00Z,ana,2024-02-28','late-marker,Remind,2024-02-28T09:00:00Z,ana,');
   await page.locator('#owgEventLogFile').setInputFiles({name:'invoices.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   await page.locator('#owgEventLogResource').selectOption('who');
   if(await page.locator('#owgEventLogAttributes').count())await page.locator('#owgEventLogAttributes').selectOption(['due']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 42 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgDue');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgDueActivity').selectOption('Remind');
   await page.locator('#owgDueActivity option[value=Pay]').evaluate(option=>option.remove());
   await page.locator('#owgDueActivityExact').fill('Pa');
   await page.locator('#owgDueActivityFind').click();
   await page.locator('#owgDueActivityMatches').selectOption('Pay');
   await page.locator('#owgDueActivityUse').click();
   assert.equal(await page.locator('#owgDueActivityExact').inputValue(),'Pay');
   await page.locator('#owgDueRun').click();
   await page.locator('#owgDueStatus').filter({hasText:'Enter the name of the due-date attribute.'}).waitFor();
   await page.locator('#owgDueAttribute').fill('due');await page.locator('#owgDueRun').click();
   await page.locator('#owgDueStatus').filter({hasText:'Pay happened on time in 66.7% of the cases where it happened (12 on time, 6 late).'}).waitFor();
   const status=await page.locator('#owgDueStatus').textContent();
   assert(status.includes('2 open cases are overdue.')&&status.includes('a median 2.38 days late'),status);
   await page.locator('#owgDueResults details summary').click();
   assert.match(await page.locator('#owgDueResults details').textContent(),/inv1[2-7]2\.38/);
   await box.getByRole('button',{name:'Inspect Open, overdue cases'}).click();
   const review=page.locator('#owgDueCaseReview');
   await review.getByText('2 cases in this group. Showing 2 on this page.').waitFor();
   await review.getByRole('button',{name:'Open case inv18'}).click();
   const events=page.locator('#owgEventLogCaseEvents');
   await events.getByRole('heading',{name:'Case inv18'}).waitFor();
   await events.getByRole('cell',{name:'Remind',exact:true}).waitFor();
   await box.getByRole('button',{name:'Inspect Late cases',exact:true}).click();
   await review.getByText('6 cases in this group. Showing 6 on this page.').waitFor();
   const cutoff=page.locator('#owgDueAsOf');await cutoff.fill('2024-01-25T12:00');
   assert.equal(await review.textContent(),'');
   await page.locator('#owgDueRun').click();
   await box.getByText(/Status as of 2024-01-25 12:00:00 UTC/).waitFor();
   const [overdue]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/due-dates/cases')),
     box.getByRole('button',{name:'Inspect Open, overdue cases',exact:true}).click()]);
   assert.equal(overdue.status(),200,await overdue.text());
   const historical=await overdue.json();
   assert.equal(historical.as_of,'2024-01-25T12:00:00Z');
   assert.deepEqual(historical.cases.map(c=>[c.case_id,c.done_at]),[['inv12',null],['inv13',null]]);
   const [exported]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDueExportJson').click()]);
   const report=JSON.parse(await fs.readFile(await exported.path(),'utf8'));
   assert.equal(report.as_of,historical.as_of);assert.equal(report.groups.open_overdue,2);
   assert.equal(report.most_late_cases,undefined);
   await cutoff.fill('');
   assert.equal(await page.locator('#owgDueExportJson').count(),0);
   await page.locator('#owgDueMode').selectOption('days');await page.locator('#owgDueDays').fill('3');await page.locator('#owgDueRun').click();
   await page.locator('#owgDueStatus').filter({hasText:'(0 on time, 18 late)'}).waitFor();
   // Keep it as a signal: 2 open invoices are overdue, more than 1.
   await page.locator('#owgDueMode').selectOption('attribute');
   await page.locator('#owgDueAlertCount').fill('1');
   const [savedSignal]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/signals')&&r.request().method()==='POST'),page.locator('#owgDueAlertSave').click()]);
   assert.equal(savedSignal.request().postDataJSON().when.activity,'Pay');
   await page.locator('#owgDueAlertMessage').filter({hasText:'Saved as a signal.'}).waitFor();
   await page.locator('#owgSignalEvaluate').scrollIntoViewIfNeeded();await page.locator('#owgSignalEvaluate').click();
   const dueSignal=page.locator('tr',{hasText:'Overdue Pay'}).filter({hasText:'2 overdue'});
   await dueSignal.first().waitFor();assert.match(await dueSignal.first().textContent(),/yes/);
   await page.locator('#owgSignalScorecardRun').click();
   await page.locator('#owgSignalScorecard tbody').filter({hasText:'Overdue Pay'}).waitFor();
   assert.match(await page.locator('#owgSignalScorecard').textContent(),/2024-01.*Overdue Pay.*\(holds\)/s);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDue'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Due dates browser acceptance passed: attribute due dates with on-time, late and overdue cases, most late cases, days-after-start mode, overdue signal, monthly scorecard, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
