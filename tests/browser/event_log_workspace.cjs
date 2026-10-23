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
 // 60 cases: every third is slow and has rework (Fix), every tenth skips Approve.
 const rows=['case_id;step;time;who'];
 for(let c=0;c<60;c++){
   const slow=c%3===0,steps=['Receive','Check',...(slow?['Fix','Check']:[]),'Approve','Close'].filter(s=>!(c%10===0&&s==='Approve'));
   let t=Date.UTC(2024,0,1+c%20,8);
   for(const s of steps){t+=(slow?3:0.25)*3600e3;rows.push(['C'+c,s,new Date(t).toISOString(),s==='Check'?'bot':(slow?'ann':'bob')].join(';'));}
 }
 rows.push('C99;Receive;2024-01-05 09:00:00;bob');
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
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);

   await page.locator('#owgEventLogFile').setInputFiles({name:'claims.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogActivity').selectOption('step');
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'case_id');
   assert.equal(await page.locator('#owgEventLogTime').inputValue(),'time');
   assert.equal(await page.locator('#owgEventLogResource').inputValue(),'who');
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true,'import needs explicit local-storage consent');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'1 rejected'}).waitFor().catch(async error=>{throw new Error('import status: '+await page.locator('#owgEventLogStatus').textContent()+' '+error.message);});
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/Imported 274 events/);

   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogResults h3',{hasText:'Most common variants'}).waitFor();
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Receive → Check → Approve → Close/);

   await page.locator('#owgEventLogSla').fill('4');await page.locator('#owgEventLogBots').fill('bot');
   await page.locator('#owgEventLogKpiRun').click();
   await page.getByText('Rework rate').waitFor();
   const kpi=await page.locator('#owgEventLogKpiRun').locator('xpath=../..').textContent();
   assert.match(kpi,/33\.3%/,'rework and SLA breach both cover the 20 slow cases');
   await page.locator('#owgEventLogKpiCohortResource').fill('ann');
   await page.locator('#owgEventLogKpiRun').click();
   await page.waitForFunction(()=>/\b20\b/.test(document.querySelector('#owgEventLogKpiRun').parentElement.parentElement.querySelector('table tbody tr td').textContent));
   const [kpiCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogKpiCsv').click()]);
   const kpiText=await fs.readFile(await kpiCsv.path(),'utf8');
   assert.match(kpiText,/^metric,value\r\ncases,20\r\n/);assert.match(kpiText,/rework_rate,1\r\n/);

   await page.locator('#owgEventLogCohortALabel').fill('Bob');await page.locator('#owgEventLogCohortAResource').fill('bob');
   await page.locator('#owgEventLogCohortBLabel').fill('Ann');await page.locator('#owgEventLogCohortBResource').fill('ann');
   await page.locator('#owgEventLogBenchRun').click();
   await page.getByText('Difference (second − first)').waitFor();
   const bench=await page.locator('#owgEventLogBenchRun').locator('xpath=../..').textContent();
   assert.match(bench,/Bob40/);assert.match(bench,/Ann20/);assert.match(bench,/Rework rate\+100\.0 pts/);assert.match(bench,/do not say why/);
   const [benchJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogBenchmarkJson').click()]);
   assert.equal(JSON.parse(await fs.readFile(await benchJson.path(),'utf8')).cohorts[1].cases,20);

   await page.locator('#owgEventLogSimLoad').click();
   await page.locator('#owgEventLogSimRun').waitFor();
   assert.match(await page.locator('#owgEventLogSimPaths').inputValue(),/^0\.600: Receive > Check > Approve > Close/);
   await page.locator('#owgEventLogSimNewName').fill('Validate');await page.locator('#owgEventLogSimAdd').click();
   const validate=page.getByLabel('Minutes for Validate');await validate.fill('5');
   await page.getByLabel('Resources for Check').fill('3');
   await page.locator('#owgEventLogSimPaths').fill('0.8: Receive > Validate > Check > Approve > Close\n0.2: Receive > Check > Fix > Check > Approve > Close');
   await page.locator('#owgEventLogSimLabel').fill('Validate first');await page.locator('#owgEventLogSimCases').fill('500');
   await page.locator('#owgEventLogSimRun').click();
   await page.getByText('Measured baseline').waitFor();
   const simulated=await page.locator('#owgEventLogSimRun').locator('xpath=../..').textContent();
   assert.match(simulated,/Validate first/);assert.match(simulated,/Median cycle time change/);assert.match(simulated,/not forecasts/);assert.match(simulated,/Poisson/);

   await page.locator('#owgEventLogModel').fill('Receive > Check > Approve > Close');
   await page.locator('#owgEventLogConformanceRun').click();
   await page.getByText('Skipped reference step').waitFor();
   const conformance=await page.locator('#owgEventLogConformanceRun').locator('xpath=..').textContent();
   assert.match(conformance,/Approve/);assert.match(conformance,/Fix/);

   await page.locator('#owgEventLogOutcomeHours').fill('5');
   await page.locator('#owgEventLogRootCauseRun').click();
   await page.getByText('Holds after correction').waitFor();
   const cause=await page.locator('#owgEventLogRootCauseRun').locator('xpath=../..').textContent();
   assert.match(cause,/activity: Fix/);assert.match(cause,/not causes/);

   await page.evaluate(()=>{window.scrollTo(0,0);});
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   const found=audit.violations.map(v=>`${v.id}: ${v.nodes[0].target.join(' ')}`);
   assert.deepEqual(found,[],'event-log panel WCAG violations:\n'+found.join('\n'));

   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   await page.setViewportSize({width:1280,height:900});
   await page.locator('#owgEventLogExportFormat').selectOption('ocel');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
   const exported=JSON.parse(await fs.readFile(await download.path(),'utf8'));
   assert.equal(exported.events.length,274);assert.equal(exported.objects.length,60);

   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);
   const listed=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length);
   assert.equal(listed,0);
   const unauthenticated=await fetch(base+'/v1/event-logs');assert.equal(unauthenticated.status,401);
   assert.deepEqual(errors,[]);
   console.log('Event-log workspace acceptance passed: consented CSV import with rejected naive time, variants, filtered KPIs with CSV export, cohort benchmark, what-if simulation with an added activity, conformance, root-cause associations, WCAG audit, mobile width, OCEL export, verified deletion.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
