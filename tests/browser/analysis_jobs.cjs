const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-analysis-jobs-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','analysis_jobs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',c=>logs=(logs+c).slice(-6000));
 const control=async(action)=>{const r=await fetch(base+'/test-analysis/'+action,{method:'POST'});assert.ok(r.ok,await r.text());};
 const waitUntil=async(predicate)=>{for(let i=0;i<100;i++){if(await predicate())return;await new Promise(r=>setTimeout(r,50));}throw new Error('Fixture wait timed out: '+logs);};
 try{
  await waitUntil(async()=>{try{return (await fetch(base+'/')).ok&&!!(await fs.readFile(path.join(scratch,'session.txt'),'utf8'));}catch{return false;}});
  const token=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+token);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.locator('#owgEventLogFile').setInputFiles({name:'jobs.csv',mimeType:'text/csv',buffer:Buffer.from('case_id,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-02T00:00:00Z\n2,A,2024-01-03T00:00:00Z\n')});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 3 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgAnalysisRun').waitFor();
  assert.ok(await page.locator('#owgAnalysisBackground').isChecked());
  const status=page.locator('#owgAnalysisStatus'),out=page.locator('#owgAnalysisResults');
  const run=()=>page.locator('#owgAnalysisRun').click();
  const complete=()=>status.filter({hasText:'Analysis complete.'}).waitFor();
  await run();await complete();assert.match(await out.textContent(),/2 matching cases/);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisJson').click()]);
  assert.deepEqual(JSON.parse(await fs.readFile(await download.path(),'utf8')).rows,[[2]]);
  await waitUntil(async()=>(await(await fetch(base+'/test-analysis/state')).json()).jobs===0);
  // Pending workspace lock: UI remains responsive and cancellation does not need that lock.
  await control('hold');await run();await status.filter({hasText:'locally'}).waitFor();
  assert.equal(await out.locator('table').count(),0);assert.ok(await page.locator('#owgAnalysisJson').isDisabled());
  assert.ok(await page.locator('#owgAnalysisSave').isDisabled());
  await page.locator('#owgAnalysisCancel').click();await status.filter({hasText:'no result was displayed'}).waitFor();
  assert.ok(await page.locator('#owgAnalysisRun').isEnabled());await control('release');
  // Actual DuckDB work: cancel the owned native query, then run another analysis successfully.
  await control('heavy');await run();
  await waitUntil(async()=>(await(await fetch(base+'/test-analysis/state')).json()).entered);
  await page.locator('#owgAnalysisCancel').click();await status.filter({hasText:'no result was displayed'}).waitFor();
  await run();await complete();assert.match(await out.textContent(),/2 matching cases/);
  // Deadline covers lock waiting and returns no old or partial result.
  await control('short-deadline');await control('hold');await run();
  await status.filter({hasText:'reached its deadline'}).waitFor();assert.equal(await out.locator('table').count(),0);
  assert.ok(await page.locator('#owgAnalysisJson').isDisabled());await control('release');await control('normal-deadline');
  // Cancel while the submission response is held: its eventual job ID must be erased, not rendered.
  let releaseSubmit,seenSubmit;const heldSubmit=new Promise(r=>seenSubmit=r);
  await page.route('**/analysis-jobs',async route=>{const response=await route.fetch();seenSubmit();await new Promise(r=>releaseSubmit=r);await route.fulfill({response});},{times:1});
  await run();await heldSubmit;await page.locator('#owgAnalysisCancel').click();releaseSubmit();
  await status.filter({hasText:'no result was displayed'}).waitFor();assert.equal(await out.locator('table').count(),0);
  await waitUntil(async()=>(await(await fetch(base+'/test-analysis/state')).json()).jobs===0);
  // Cancel while a successful result is held: a late complete response cannot restore export/save.
  let releaseResult,seenResult;const heldResult=new Promise(r=>seenResult=r);
  await page.route('**/analysis-jobs/*/result',async route=>{const response=await route.fetch();seenResult();await new Promise(r=>releaseResult=r);await route.fulfill({response});},{times:1});
  await run();await heldResult;await page.locator('#owgAnalysisCancel').click();releaseResult();
  await status.filter({hasText:'no result was displayed'}).waitFor();assert.equal(await out.locator('table').count(),0);
  assert.ok(await page.locator('#owgAnalysisJson').isDisabled());
  // A log/view refresh also suppresses a held completed result.
  let releaseStale,seenStale;const heldStale=new Promise(r=>seenStale=r);
  await page.route('**/analysis-jobs/*/result',async route=>{const response=await route.fetch();seenStale();await new Promise(r=>releaseStale=r);await route.fulfill({response});},{times:1});
  await run();await heldStale;await page.locator('#owgEventLogAnalyze').click();
  const staleResponse=page.waitForResponse(r=>r.url().includes('/analysis-jobs/')&&r.url().endsWith('/result'));
  releaseStale();await staleResponse;
  assert.equal(await page.locator('#owgAnalysisResults table').count(),0);
  await page.locator('#owgAnalysisRun').click();await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis complete.'}).waitFor();
  // Same result can still be explicitly saved and re-run via the normal builder.
  await page.locator('#owgAnalysisName').fill('Two cases');await page.locator('#owgAnalysisSave').click();
  await page.getByRole('button',{name:'Run saved analysis Two cases',exact:true}).click();await complete();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
  await page.addScriptTag({content:axe});
  const audit=await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgEventLogAnalysis'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.length}));});
  assert.deepEqual(audit,[]);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  page.once('dialog',d=>d.accept());await page.locator('#owgEventLogDelete').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Local analysis jobs browser passed: complete/save/export, workspace-wait and real DuckDB cancellation, deadline, held submission/result, stale view, cleanup, WCAG and mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
