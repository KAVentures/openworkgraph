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
  // Re-open the log and confirm that the definition survived the dashboard re-render.
  const picker=page.locator('#owgEventLogPick');
  const logId=await picker.inputValue();
  // Re-select the existing log through the real picker; an empty option is not guaranteed.
  await picker.selectOption(logId);
  await picker.dispatchEvent('change');
  await page.waitForFunction(() => [...document.querySelectorAll('#owgCohortMetricSaved option')].some(option => option.textContent.includes('Browser acceptance duration')), null, {timeout:60000});
  await page.locator('#owgCohortMetricSaved').selectOption(savedId);
  assert.equal(await page.locator('#owgCohortMetricField').inputValue(),'duration_seconds');
  await page.locator('#owgCohortMetricDelete').click();
  await page.locator('#owgCohortMetricSaved option').filter({hasText:'Browser acceptance duration'}).waitFor({state:'detached',timeout:60000});
  assert.deepEqual(errors,[]);
  console.log('Saved cohort metric Chromium acceptance passed: authenticated save, calculate, reopen, select, delete.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
