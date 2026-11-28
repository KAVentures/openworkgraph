const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-simulation-compare-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>spawnError=e);
 for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  await page.locator('#owgEventLogFile').setInputFiles({name:'case.csv',mimeType:'text/csv',buffer:Buffer.from('case_id,activity,time\nC1,Receive,2024-01-01T00:00:00Z\nC1,Close,2024-01-01T01:00:00Z\n')});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  const panel=page.locator('#owgSimulationReportCompare');await panel.waitFor();
  const run=n=>({cycle_time_seconds:{p50:n,p90:n+2,mean:n+1},completed_per_day:2,unstable:false});
  const packet=(name,n)=>({format:'openworkgraph.simulation-report.v1',generated_at:'2026-11-06T00:00:00Z',analysis:{simulated_not_observed:true,execution_authorized:false,log:{name:'Tickets',log_id:'log_1'},case_notion:'case',scenario:{label:name,cases:100,distribution:'fixed',routing:'paths',activity_time_basis:'gap',activities:{A:{minutes:2}}},calibration:{observed_cases:10,calibrated_cases:8},baseline:run(10),result:run(n),assumptions:['Synthetic assumption']}});
  const file=(name,doc)=>({name,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
  const input=page.locator('#owgSimulationCompareFiles'),compare=panel.getByRole('button',{name:'Compare reports'}),download=panel.getByRole('button',{name:'Download comparison CSV'});
  await input.setInputFiles([file('=first.json',packet('<img src=x onerror=alert(1)>',8)),file('second.json',packet('Variant B',5))]);
  await compare.click();await panel.getByText('Showing 2 user-selected reports.',{exact:false}).waitFor();
  assert.equal(await panel.locator('tbody tr').count(),4);assert.equal(await panel.locator('img').count(),0);
  assert.match(await panel.textContent(),/8.*5/s);assert.match(await panel.textContent(),/Scope and dataset equality are not verified/);
  const [saved]=await Promise.all([page.waitForEvent('download'),download.click()]);const csv=await fs.readFile(await saved.path(),'utf8');
  assert.match(csv,/'=first\.json/);assert.match(csv,/scenario,p50,8,seconds/);
  await input.setInputFiles([file('bad.json',{}),file('second.json',packet('B',5))]);
  assert.equal(await download.isDisabled(),true);await compare.click();await panel.getByText('Choose OWG simulation reports',{exact:false}).waitFor();
  assert.equal(await panel.locator('tbody tr').count(),0);
  await page.evaluate(()=>{const original=File.prototype.text;window.__releaseReportReads=[];File.prototype.text=function(){return this.name.startsWith('slow')?new Promise(resolve=>window.__releaseReportReads.push(()=>original.call(this).then(resolve))):original.call(this);};});
  await input.setInputFiles([file('slow1.json',packet('OLD SECRET',4)),file('slow2.json',packet('OLD SECRET',4))]);await compare.click();
  await page.waitForFunction(()=>window.__releaseReportReads.length===2);
  await input.setInputFiles([file('new1.json',packet('New A',7)),file('new2.json',packet('New B',6))]);
  await page.evaluate(()=>window.__releaseReportReads.forEach(release=>release()));
  await compare.click();await panel.getByText('Showing 2 user-selected reports.',{exact:false}).waitFor();
  assert.doesNotMatch(await panel.textContent(),/OLD SECRET/);assert.match(await panel.textContent(),/New A/);

  const replay=page.locator('#owgEventLogSimReplay');
  await replay.locator('summary').click();
  const source=replay.locator('#owgEventLogSimReplayFile'),draft=replay.locator('#owgEventLogSimReplayInputs');
  const rerun=replay.getByRole('button',{name:'Run reviewed scenario'});
  await source.setInputFiles(file('old-report.json',packet('Old report',10)));
  await replay.getByText('This report has no reusable submitted scenario.',{exact:true}).waitFor();
  assert.equal(await rerun.isDisabled(),true);
  const scenario={format:'openworkgraph.simulation-scenario.v1',label:'Reviewed staffing',arrivals_per_hour:2,
    activities:{Close:{minutes:15,resources:2}},distribution:'fixed',cases:100,seed:17};
  let simulations=0;
  page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/simulate'))simulations++;});
  await source.setInputFiles(file('scenario.json',scenario));
  await replay.getByText('Scenario loaded. Review its inputs before running.',{exact:true}).waitFor();
  assert.deepEqual(JSON.parse(await draft.inputValue()),scenario);
  assert.equal(simulations,0,'selecting a file only previews its settings');
  const simulationResult=page.locator('#owgEventLogSimulationResults');
  async function simulate(button){
   const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/simulate')),button.click()]);
   assert.equal(response.status(),200,await response.text());
   await simulationResult.getByText('Calibrated against',{exact:false}).waitFor();
   return response.json();
  }
  const first=await simulate(rerun);
  assert.equal(first.scenario.seed,17);
  assert.equal(first.scenario.activities.Close.resources,2);
  async function downloadJson(selector){
   const [artifact]=await Promise.all([page.waitForEvent('download'),page.locator(selector).click()]);
   return JSON.parse(await fs.readFile(await artifact.path(),'utf8'));
  }
  assert.deepEqual(await downloadJson('#owgEventLogSimDownloadInputs'),scenario);
  const report=await downloadJson('#owgEventLogSimulationJson');
  assert.deepEqual(report.submitted_scenario,scenario);
  await source.setInputFiles(file('report.json',report));
  await replay.getByText('Saved report ·',{exact:false}).waitFor();
  const second=await simulate(rerun);
  assert.deepEqual(second.result,first.result);
  await draft.fill(JSON.stringify({...scenario,seed:18,label:'User edited input'}));
  assert.equal((await simulate(rerun)).scenario.seed,18);
  await page.locator('#owgEventLogSimLoad').click();
  await page.locator('#owgEventLogSimArrivals').fill('2');
  await page.locator('#owgEventLogSimCases').fill('100');
  assert.equal((await simulate(page.locator('#owgEventLogSimRun'))).scenario.cases,100);

  await page.locator('#owgEventLogFile').setInputFiles({name:'updated.csv',mimeType:'text/csv',buffer:Buffer.from(
    'case_id,activity,time\nC1,Receive,2024-01-01T00:00:00Z\nC1,Close,2024-01-01T01:00:00Z\nC2,Receive,2024-01-01T02:00:00Z\nC2,Close,2024-01-01T03:00:00Z\n')});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 4 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  await replay.locator('summary').click();await source.setInputFiles(file('prior-run.json',report));
  await replay.getByText('Saved report ·',{exact:false}).waitFor();
  const moved=await simulate(rerun);
  assert.notEqual(moved.log.log_id,first.log.log_id);
  assert.equal(moved.calibration.calibrated_cases,2);
  assert.equal(moved.scenario.activities.Close.resources,2);
  // Use an actual capacity-grid response, then run a browser-only finite review.
  const sourceLog=moved.log.log_id;
  const gridInput={format:'openworkgraph.simulation-capacity-grid.v1',baseline:{cases:50,seed:7,distribution:'fixed'},
    designs:[{label:'Alternative',activities:{Close:{resources:2}}}],arrival_rates_per_hour:[0.5,1],
    replications:2,target:{cycle_time_minutes:10000,minimum_completion_share:.01}};
  const grid=await page.evaluate(async({sourceLog,gridInput})=>{
    const response=await fetch('/v1/event-logs/'+encodeURIComponent(sourceLog)+'/simulation/capacity-grid',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(gridInput)});
    if(!response.ok)throw Error(await response.text());return response.json();
  },{sourceLog,gridInput});
  const [planner]=await Promise.all([page.context().waitForEvent('page'),panel.getByRole('link',{name:'Plan staffing changes across tested demand periods'}).click()]);
  const plannerErrors=[];planner.on('pageerror',e=>plannerErrors.push(e.message));await planner.waitForLoadState('load');
  const network=[];planner.on('request',req=>network.push(req.url()));
  await planner.locator('#source').setInputFiles(file('capacity.json',grid));
  await planner.locator('#status').filter({hasText:'Source grid loaded'}).waitFor();
  await planner.locator('#currency').fill('SEK');await planner.locator('#target').fill('1');
  await planner.getByRole('spinbutton',{name:'Baseline hourly minor-unit cost'}).fill('100');
  await planner.getByRole('spinbutton',{name:'Alternative hourly minor-unit cost'}).fill('200');
  await planner.locator('#cap').fill('1');await planner.locator('#fee').fill('0');
  await planner.getByRole('combobox',{name:'Period 1 demand rate'}).selectOption('0.5');
  await planner.getByRole('spinbutton',{name:'Period 1 duration minutes'}).fill('60');
  assert.equal(await planner.locator('#run').isDisabled(),true);
  await planner.locator('#confirm').check();await planner.locator('#run').click();
  await planner.locator('#status').filter({hasText:'Finite planning review complete'}).waitFor();
  const [planFile]=await Promise.all([planner.waitForEvent('download'),planner.locator('#download').click()]);
  const plan=JSON.parse(await fs.readFile(await planFile.path(),'utf8'));
  assert.equal(plan.format,'openworkgraph.capacity-transition-plan.v1');
  assert.equal(plan.queue_carryover_simulated,false);assert.equal(plan.execution_authorized,false);
  assert.equal(plan.period_eligibility[0].designs.length,2);
  assert.equal(network.length,0);
  await planner.setViewportSize({width:390,height:844});
  assert.equal(await planner.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await planner.locator('#fee').fill('1');assert.equal(await planner.locator('#download').isDisabled(),true);
  assert.equal(await planner.locator('#results').textContent(),'');
  await planner.locator('#clear').click();assert.equal(await planner.locator('#source').inputValue(),'');
  assert.deepEqual(plannerErrors,[]);await planner.close();
  await page.setViewportSize({width:390,height:844});assert.ok(await panel.getByRole('button',{name:'Compare reports'}).isVisible());
  assert.deepEqual(errors,[]);console.log('Simulation report comparison and replay passed, including input review, exact download, report rerun, edited inputs and the original form.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
