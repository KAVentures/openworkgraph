const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

// Authenticated Chromium acceptance for persistent numeric cohort expressions.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-cohort-saved-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>spawnError=e);
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;
   if(server.exitCode!==null)throw new Error('Server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }
  if(!ready)throw new Error('Server not ready: '+logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],
   ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const context=await browser.newContext();
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);
  await page.locator('[role=tab][data-tab=evidence]').click();
  await page.locator('#owgEventLogSample').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Created the sample log'}).waitFor({timeout:60000});
  await page.locator('#owgCohortMetricSection').waitFor();
  await page.locator('#owgCohortMetricSaveName').fill('Browser acceptance duration');
  await page.locator('#owgCohortMetricField').selectOption('duration_seconds');
  await page.locator('#owgCohortMetricSave').click();
  await page.waitForFunction(() => { const picker = document.querySelector('#owgCohortMetricSaved'); return picker && picker.selectedOptions.length > 0 && picker.selectedOptions[0].textContent.includes('Browser acceptance duration'); }, { timeout: 30000 });
  const savedId=await page.locator('#owgCohortMetricSaved').inputValue();
  assert.ok(savedId);
  await page.locator('#owgCohortMetricRun').click();
  await page.locator('#owgCohortMetricResult').filter({hasText:'matching cases'}).waitFor({timeout:60000});
  assert.match(await page.locator('#owgCohortMetricResult').innerText(),/No raw cases returned/);
  const [reviewResponse]=await Promise.all([
    page.waitForResponse(response=>{
      const url=new URL(response.url());
      return url.pathname.endsWith('/evaluate')&&url.searchParams.get('include_case_values')==='true';
    }),
    page.locator('#owgCohortMetricReview').click()
  ]);
  assert.equal(reviewResponse.status(),200,await reviewResponse.text());
  const reviewed=await reviewResponse.json();
  assert.equal(reviewed.saved_metric.metric_id,savedId);
  assert.equal(reviewed.case_values.length,reviewed.metric.rows);
  const caseReview=page.locator('#owgCohortMetricCases');
  const caseRows=caseReview.locator('tbody tr');
  await caseRows.first().waitFor();
  assert.equal(await caseRows.first().locator('td').nth(1).textContent(),reviewed.metric.max);
  const order=page.locator('#owgCohortMetricCaseOrder');
  await order.selectOption('lowest');
  assert.equal(await caseRows.first().locator('td').nth(1).textContent(),reviewed.metric.min);
  if(reviewed.metric.non_null>25){
    await caseReview.getByRole('button',{name:'Next case page'}).click();
    await caseReview.getByRole('status').filter({hasText:/Showing 26–/}).waitFor();
    await caseReview.getByRole('button',{name:'Previous case page'}).click();
  }
  await caseRows.first().getByRole('button',{name:/Open metric case/}).click();
  await page.locator('#owgEventLogCaseEvents').getByText(/Showing \d+ of \d+ events/).waitFor();
  await order.selectOption('missing');
  await caseReview.getByText('No cases in this view.').waitFor();
  await page.locator('#owgCohortMetricField').selectOption('event_count');
  assert.equal(await caseReview.locator('table').count(),0);
  assert.equal(await order.count(),0);
  // A second saved log must not inherit the first log's private metric definitions.
  // Switching back must recover the original definition from authenticated storage.
  const picker=page.locator('#owgEventLogPick');
  const logId=await picker.inputValue();
  // The sample endpoint is idempotent: clicking it twice reopens the same log.
  // Import a distinct synthetic CSV through the authenticated dashboard instead.
  await page.locator('#owgEventLogFile').setInputFiles({
    name:'second-synthetic-log.csv',mimeType:'text/csv',
    buffer:Buffer.from('case_id,activity,timestamp\nCASE-2,Created,2026-01-01T10:00:00Z\nCASE-2,Completed,2026-01-01T10:05:00Z\n')
  });
  await page.locator('#owgEventLogCase option[value="case_id"]').waitFor({state:'attached',timeout:30000});
  await page.locator('#owgEventLogCase').selectOption('case_id');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('timestamp');
  await page.locator('#owgEventLogName').fill('Second synthetic browser acceptance log');
  await page.locator('#owgEventLogConsent').check();
  await page.locator('#owgEventLogImport').click();
  await page.waitForFunction(previous => {
    const selected=document.querySelector('#owgEventLogPick');
    return selected && selected.value && selected.value!==previous;
  },logId,{timeout:60000});
  const secondLogId=await picker.inputValue();
  assert.notEqual(secondLogId,logId,'import must create a distinct saved log');
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgCohortMetricSection').waitFor({state:'visible',timeout:60000});
  assert.equal(await page.locator('#owgCohortMetricSaved option').filter({hasText:'Browser acceptance duration'}).count(),0,
    'saved cohort definitions must remain scoped to their own log');
  // Deterministic stale-response regression: hold a real authenticated response for
  // the original log until after the operator has switched back to the second log.
  // A late response must never populate the second log's saved-definition picker.
  let interceptedResolve,releaseResponse;
  const intercepted=new Promise(resolve=>{interceptedResolve=resolve;});
  const release=new Promise(resolve=>{releaseResponse=resolve;});
  let delayed=false;
  const definitionPath='/v1/event-logs/'+encodeURIComponent(logId)+'/cohort-metric-definitions';
  const delayOriginalDefinitions=async route=>{
    if(delayed){await route.continue();return;}
    delayed=true;
    const response=await route.fetch();
    interceptedResolve();
    await release;
    await route.fulfill({response});
  };
  await page.route('**'+definitionPath,delayOriginalDefinitions);
  await picker.selectOption(logId);
  await picker.dispatchEvent('change');
  await page.locator('#owgEventLogAnalyze').click();
  await Promise.race([intercepted,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Expected original-log definitions request')),30000))]);
  await picker.selectOption(secondLogId);
  await picker.dispatchEvent('change');
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgCohortMetricSection').waitFor({state:'visible',timeout:60000});
  releaseResponse();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#owgCohortMetricSaved option').filter({hasText:'Browser acceptance duration'}).count(),0,
    'late original-log response must not leak a saved definition into the second log');
  await page.unroute('**'+definitionPath,delayOriginalDefinitions);
  // Re-open the original log and confirm that the definition survived the dashboard re-render.
  // Re-select the existing log through the real picker; an empty option is not guaranteed.
  await picker.selectOption(logId);
  await picker.dispatchEvent('change');
  // Selecting a log clears its analysis panel; explicitly reopen Analyse before checking saved definitions.
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgCohortMetricSection').waitFor({state:'visible',timeout:60000});
  await page.waitForFunction(() => [...document.querySelectorAll('#owgCohortMetricSaved option')].some(option => option.textContent.includes('Browser acceptance duration')), null, {timeout:60000});
  await page.locator('#owgCohortMetricSaved').selectOption(savedId);
  assert.equal(await page.locator('#owgCohortMetricField').inputValue(),'duration_seconds');
  // Ordinary analysis downloads must not release a cached result if the
  // authenticated saved-log recomputation changes after operator review.
  await page.locator('#owgAnalysisBackground').uncheck();
  await page.locator('#owgAnalysisRun').click();
  await page.locator('#owgAnalysisJson').waitFor({state:'visible',timeout:60000});
  await page.waitForFunction(()=>!document.querySelector('#owgAnalysisJson').disabled,null,{timeout:60000});
  const analysisPath='/v1/event-logs/'+encodeURIComponent(logId)+'/analysis-source-bound';
  let revalidationSeen=false;
  const changedAnalysis=async route=>{
    revalidationSeen=true;
    const original=await route.fetch();
    const body=await original.json();
    assert.ok(body.source_content_sha256,'reviewed source must have a complete lineage digest');
    // Simulate a changed saved-log source that yields exactly the same aggregates.
    // A result-only comparison would incorrectly permit this download.
    body.source_content_sha256='0'.repeat(64)===body.source_content_sha256?'1'.repeat(64):'0'.repeat(64);
    await route.fulfill({response:original,contentType:'application/json',body:JSON.stringify(body)});
  };
  await page.route('**'+analysisPath,changedAnalysis);
  const downloads=[];
  page.on('download',download=>downloads.push(download.suggestedFilename()));
  await page.locator('#owgAnalysisJson').click();
  await page.locator('#owgAnalysisStatus').filter({hasText:'Saved-log data or analysis results changed'}).waitFor({timeout:30000});
  assert.equal(revalidationSeen,true,'download must revalidate through authenticated source-bound saved-log API');
  assert.deepEqual(downloads,[],'changed recomputation must not trigger JSON download');
  assert.equal(await page.locator('#owgAnalysisCsv').isDisabled(),true,'stale CSV must also be disabled');
  await page.unroute('**'+analysisPath,changedAnalysis);
  // After invalidation the operator must explicitly run and review the current
  // source again. A valid, unchanged source must still permit both exports.
  await page.locator('#owgAnalysisRun').click();
  await page.waitForFunction(()=>!document.querySelector('#owgAnalysisJson').disabled,null,{timeout:60000});
  const jsonDownloadPromise=page.waitForEvent('download',{timeout:30000});
  await page.locator('#owgAnalysisJson').click();
  const jsonDownload=await jsonDownloadPromise;
  assert.match(jsonDownload.suggestedFilename(),/\.json$/i);
  const csvDownloadPromise=page.waitForEvent('download',{timeout:30000});
  await page.locator('#owgAnalysisCsv').click();
  const csvDownload=await csvDownloadPromise;
  assert.match(csvDownload.suggestedFilename(),/\.csv$/i);
  await page.locator('#owgCohortMetricDelete').click();
  await page.locator('#owgCohortMetricSaved option').filter({hasText:'Browser acceptance duration'}).waitFor({state:'detached',timeout:60000});
  assert.deepEqual(errors,[]);
  console.log('Saved cohort metric Chromium acceptance passed: authenticated save, calculate, reopen, select, delete.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
