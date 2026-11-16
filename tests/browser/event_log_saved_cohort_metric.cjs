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
  // A second saved log must not inherit the first log's private metric definitions.
  // Switching back must recover the original definition from authenticated storage.
  const picker=page.locator('#owgEventLogPick');
  const logId=await picker.inputValue();
  // The sample endpoint is idempotent: clicking it twice reopens the same log.
  // Import a distinct synthetic CSV through the authenticated dashboard instead.
  await page.locator('#owgEventLogFile').setInputFiles({
    name:'second-synthetic-log.csv',mimeType:'text/csv',
    buffer:Buffer.from('case_id,activity,timestamp\\nCASE-2,Created,2026-01-01T10:00:00Z\\nCASE-2,Completed,2026-01-01T10:05:00Z\\n')
  });
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
  await page.locator('#owgCohortMetricDelete').click();
  await page.locator('#owgCohortMetricSaved option').filter({hasText:'Browser acceptance duration'}).waitFor({state:'detached',timeout:60000});
  assert.deepEqual(errors,[]);
  console.log('Saved cohort metric Chromium acceptance passed: authenticated save, calculate, reopen, select, delete.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
