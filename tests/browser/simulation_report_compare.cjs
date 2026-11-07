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
  await page.setViewportSize({width:390,height:844});assert.ok(await panel.getByRole('button',{name:'Compare reports'}).isVisible());
  assert.deepEqual(errors,[]);console.log('Simulation report comparison passed: local selection, safe rendering, CSV, malformed refusal and stale-file race.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
