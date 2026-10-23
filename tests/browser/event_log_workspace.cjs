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
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
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

   await page.locator('#owgEventLogCasesBrowse').click();
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'60 matching cases. Showing 30'}).waitFor();
   await page.getByRole('button',{name:'Next case page',exact:true}).click();
   await page.getByRole('button',{name:'First case page',exact:true}).click();
   await page.getByRole('button',{name:'Open case C0',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 5 of 5'}).waitFor();
   assert.match(await page.locator('#owgEventLogCaseEvents').textContent(),/Fix/);
   const variantSection=page.locator('#owgEventLogResults h3',{hasText:'Most common variants'}).locator('..');
   await variantSection.getByRole('button',{name:'Inspect cases',exact:true}).first().click();
   await page.locator('#owgEventLogCasesPath').filter({hasText:'Exact path:'}).waitFor();
   await page.getByRole('button',{name:'Inspect cases containing Fix',exact:true}).click();
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'20 matching cases.'}).waitFor();
   // Save a reviewed graph through the actual catalog and pin a historical revision.
   const saved=await page.evaluate(async()=>{
     const labels=['Receive','Check','Approve','Close'];
     const process_model={goal:'Complete the declared claim review',baseline_source:'user_declared',baseline_path:labels,
       activities:labels.map(label=>({id:label.toLowerCase(),label})),
       transitions:labels.slice(1).map((label,i)=>({source:labels[i].toLowerCase(),target:label.toLowerCase()})),
       entry_activity_id:'receive',terminal_activity_ids:['close']};
     const body={workflow_id:'claims_review',title:'Reviewed claims',user_confirmed:true,expected_revision:0,process_model};
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(!response.ok)throw new Error(await response.text());return {record:await response.json(),body};
   });
   await page.locator('#owgEventLogGraphLoad').click();
   await page.locator('#owgEventLogGraphPick').selectOption('claims_review');
   await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogSavedGraphMap svg').waitFor();
   await page.locator('#owgEventLogSavedGraphMap').getByRole('button',{name:'Select activity check',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'60 matching cases'}).waitFor();
   const resultCell=()=>page.locator('#owgEventLogGraphResults table').first().locator('tbody tr td');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='60');
   assert.equal(await resultCell().nth(1).textContent(),'36');assert.equal(await resultCell().nth(3).textContent(),'0');
   const [graphJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogGraphJson').click()]);
   const comparison=JSON.parse(await fs.readFile(await graphJson.path(),'utf8'));
   assert.equal(comparison.process_reference.content_sha256,saved.record.content_sha256);
   assert.equal(comparison.policy_compliance_verified,false);
   await page.evaluate(async body=>{
     body.expected_revision=1;body.process_model.activities[2].label='Authorize';
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(!response.ok)throw new Error(await response.text());
   },saved.body);
   let graphRelease,graphSeen;
   const graphGate=new Promise(resolve=>graphRelease=resolve),graphFetched=new Promise(resolve=>graphSeen=resolve);
   await page.route('**/process-conformance*',async route=>{const response=await route.fetch();graphSeen();await graphGate;await route.fulfill({response});});
   await page.locator('#owgEventLogGraphRun').click();await graphFetched;
   await page.locator('#owgEventLogGraphRevision').fill('2');
   assert.equal(await page.locator('#owgEventLogGraphRun').isDisabled(),true);
   await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'revision 2.'}).waitFor();
   const oldGraph=page.waitForResponse(response=>response.url().includes('/process-conformance'));
   graphRelease();await (await oldGraph).finished();await page.unroute('**/process-conformance*');
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.equal(await page.locator('#owgEventLogGraphResults').textContent(),'','old revision results cannot repaint a newly loaded graph');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='60');
   assert.equal(await resultCell().nth(1).textContent(),'0');assert.equal(await resultCell().nth(2).textContent(),'60');
   await page.locator('#owgEventLogSavedGraphMap').getByRole('button',{name:'Select activity approve',exact:true}).click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'No displayed log activity has the exact label'}).waitFor();
   await page.locator('#owgEventLogGraphRevision').fill('1');await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'revision 1.'}).waitFor();
   await page.locator('#owgEventLogGraphCohortResource').fill('ann');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='20');
   assert.equal(await resultCell().nth(2).textContent(),'20');
   await page.locator('#owgEventLogGraphCohortResource').fill('absent');await page.locator('#owgEventLogGraphRun').click();
   await page.locator('#owgEventLogGraphResults').getByText('No cases match this selection; no verdict was established.').waitFor();
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
   const measuredPaths=await page.evaluate(()=>window.__owgEventLogModel.parseWeightedPaths(document.querySelector('#owgEventLogSimPaths').value));
   assert.deepEqual(measuredPaths[0],{activities:['Receive','Check','Approve','Close'],weight:0.6});
   await page.locator('#owgEventLogSimNewName').fill('Validate');await page.locator('#owgEventLogSimAdd').click();
   const validate=page.getByLabel('Minutes for Validate');await validate.fill('5');
   await page.locator('#owgEventLogSimNewName').fill('__proto__');await page.locator('#owgEventLogSimAdd').click();
   await page.getByLabel('Minutes for __proto__',{exact:true}).fill('7');
   await page.getByLabel('Resources for __proto__',{exact:true}).fill('2');
   await page.getByLabel('Resources for Check').fill('3');
   await page.locator('#owgEventLogSimPaths').fill(JSON.stringify([{activities:['Receive','Validate','__proto__','Check','Approve','Close'],weight:0.8},{activities:['Receive','Check','Fix','Check','Approve','Close'],weight:0.2}]));
   await page.locator('#owgEventLogSimLabel').fill('Validate first');await page.locator('#owgEventLogSimCases').fill('500');
   const [simulationResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const special=(await simulationResponse.json()).result.activities.find(a=>a.activity==='__proto__');
   assert.equal(special.minutes,7);assert.equal(special.resources,2);
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
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);
   const listed=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length);
   assert.equal(listed,0);
   // Literal headers, case IDs, labels and resources survive the complete UI/API path.
   const literalCSV=' case , activity , time , who \n Case, Review > ,2024-01-01T00:00:00Z," bot,system "\nCase ,Review→ ,2024-01-01T00:00:00Z,bot \n';
   await page.locator('#owgEventLogFile').setInputFiles({name:'literal.csv',mimeType:'text/csv',buffer:Buffer.from(literalCSV)});
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').value===' case ');
   await page.locator('#owgEventLogActivity').selectOption(' activity ');
   await page.locator('#owgEventLogTime').selectOption(' time ');
   await page.locator('#owgEventLogResource').selectOption(' who ');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogModel').waitFor();
   const suggested=JSON.parse(await page.locator('#owgEventLogModel').inputValue());
   assert.ok([' Review > ','Review→ '].includes(suggested.paths[0][0]));
   await page.locator('#owgEventLogModel').fill(JSON.stringify({format:'openworkgraph.reference-model.v1',paths:[[' Review > '],['Review→ ']]}));
   await page.locator('#owgEventLogConformanceRun').click();
   await page.getByText('Fitting cases',{exact:true}).waitFor();
   assert.match(await page.locator('#owgEventLogConformanceRun').locator('xpath=../..').textContent(),/100\.0%/);
   await page.locator('#owgEventLogBots').fill(JSON.stringify([' bot,system ']));
   await page.locator('#owgEventLogKpiRun').click();
   await page.getByText('Automation rate',{exact:true}).waitFor();
   assert.match(await page.locator('#owgEventLogKpiRun').locator('xpath=../..').textContent(),/50\.0%/);
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length),0);
   const longRows=['case,activity,time,who',...Array.from({length:205},(_,i)=>'Long,Step '+i+',2024-01-01T00:00:00Z,bot')];
   await page.locator('#owgEventLogFile').setInputFiles({name:'long.csv',mimeType:'text/csv',buffer:Buffer.from(longRows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').value==='case');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 205 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogCasesBrowse').waitFor();
   let release,seen;
   const gate=new Promise(resolve=>release=resolve),fetched=new Promise(resolve=>seen=resolve);
   await page.route('**/cases/query*',async route=>{const response=await route.fetch();seen();await gate;await route.fulfill({response});});
   await page.locator('#owgEventLogCasesBrowse').click();await fetched;
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogCasesBrowse').waitFor();
   const stale=page.waitForResponse(response=>response.url().includes('/cases/query'));
   release();await (await stale).finished();await page.unroute('**/cases/query*');
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.equal(await page.locator('#owgEventLogCasesStatus').textContent(),'','stale case response cannot repaint a new analysis');
   await page.locator('#owgEventLogCasesBrowse').click();
   await page.getByRole('button',{name:'Open case Long',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 100 of 205'}).waitFor();
   await page.locator('#owgEventLogCaseEventsMore').click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 200 of 205'}).waitFor();
   await page.locator('#owgEventLogCaseEventsMore').click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 205 of 205'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCaseEvents tbody tr').count(),205);
   assert.match(await page.locator('#owgEventLogCaseEvents').textContent(),/204 equal-timestamp events/);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   // Object-centric log: separate maps per object type, linked by shared events.
   const ocel={objectTypes:[],eventTypes:[],objects:[{id:'o1',type:'order'},{id:'i1',type:'item'},{id:'i2',type:'item'}],events:[
     {id:'p',type:'Place order',time:'2024-01-01T08:00:00Z',relationships:[{objectId:'o1',qualifier:''},{objectId:'i1',qualifier:''},{objectId:'i2',qualifier:''}]},
     {id:'k1',type:'Pick item',time:'2024-01-01T09:00:00Z',relationships:[{objectId:'i1',qualifier:''}]},
     {id:'k2',type:'Pick item',time:'2024-01-01T10:00:00Z',relationships:[{objectId:'i2',qualifier:''}]},
     {id:'s',type:'Ship',time:'2024-01-01T12:00:00Z',relationships:[{objectId:'o1',qualifier:''},{objectId:'i1',qualifier:''},{objectId:'i2',qualifier:''}]}]};
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(ocel))});
   assert.equal(await page.locator('#owgEventLogFormat').inputValue(),'ocel');
   assert.equal(await page.locator('#owgEventLogMapping').isHidden(),true);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 4 events'}).waitFor();
   await page.locator('#owgEventLogCaseType').selectOption('item');
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogObjectGraph').waitFor();
   assert.equal(await page.locator('#owgEventLogObjectType').inputValue(),'item');
   const itemMap=await page.locator('#owgEventLogObjectMap').textContent();
   assert.match(itemMap,/Pick item/);assert.match(itemMap,/Linked to/);assert.match(itemMap,/order/);
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Activities shared by several object types/);
   await page.locator('#owgEventLogObjectType').selectOption('order');
   await page.waitForFunction(()=>!/Pick item/.test(document.querySelector('#owgEventLogObjectMap').textContent));
   assert.match(await page.locator('#owgEventLogObjectMap').textContent(),/Place order.*Ship/s);
   await page.addScriptTag({content:axeSource});
   const mapAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(mapAudit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'object map WCAG violations');
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   const unauthenticated=await fetch(base+'/v1/event-logs');assert.equal(unauthenticated.status,401);
   assert.deepEqual(errors,[]);
   console.log('Event-log workspace acceptance passed: consented CSV import with rejected naive time, variants, filtered KPIs with CSV export, cohort benchmark, what-if simulation with an added activity, conformance, root-cause associations, WCAG audit, mobile width, OCEL export, verified deletion, OCEL import with per-object-type maps.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
