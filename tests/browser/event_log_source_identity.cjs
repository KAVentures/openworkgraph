const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-source-ids-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 const csv=ids=>'event,case,activity,time,who\n'+ids.map(id=>id+',c1,Open,2026-01-01T00:00:00Z,ann').join('\n')+'\n';
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  const load=async(text,name='source.csv')=>{
   await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'text/csv',buffer:Buffer.from(text)});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  };
  const status=text=>page.locator('#owgEventLogStatus').filter({hasText:text}).waitFor();
  await load(csv(['e1','e2']));await page.locator('#owgEventLogUseIdentity').check();
  await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogEventId').selectOption('event');await page.locator('#owgEventLogSourceNamespace').fill('crm');
  await page.locator('#owgEventLogConsent').check();
  await page.locator('#owgEventLogSourceNamespace').fill('sales');
  assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false,'identity changes require renewed storage consent');
  await page.locator('#owgEventLogSourceNamespace').fill('crm');await page.locator('#owgEventLogKeepRejected').check();
  await page.locator('#owgEventLogConsent').check();assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
  await page.locator('#owgEventLogKeepRejected').uncheck();await page.locator('#owgEventLogImport').click();await status('Imported 2 events');
  assert.match(await page.locator('#owgEventLogStatus').textContent(),/Supplied source-ID matching/);
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCaseType').value==='source_case');
  async function append(text,expected){
   await load(text);await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogEventId').selectOption('event');await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogAppend').click();await status(expected);
  }
  await append(csv(['e3']),'Added 1 new events');
  await append(csv(['e1','e2','e3']),'skipped 3 already in the log');
  await append(csv(['e1','e4']).replace('e1,c1,Open','e1,c1,Changed'),'conflicts with the saved event payload');
  assert.match(await page.locator('#owgEventLogPick').textContent(),/3 events/);
  await page.locator('#owgEventLogAnalyze').click();await page.locator('#owgQuality summary').click();
  await page.locator('#owgQualityResults').filter({hasText:'distinct source IDs with identical payloads remain separate events'}).waitFor();
  await page.locator('#owgEventLogExportFormat').selectOption('sourcecsv');
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
  const downloaded=path.join(scratch,'roundtrip.csv');await download.saveAs(downloaded);
  const text=await fs.readFile(downloaded,'utf8');assert.match(text,/source_event_id/);assert.match(text,/crm,e3,crm,c1/);assert.doesNotMatch(text,/e4/);
  await page.locator('#owgEventLogSourceNamespace').fill('');await load(text,'roundtrip.csv');
  await page.waitForFunction(()=>document.querySelector('#owgEventLogEventId').value==='source_event_id');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();await status('Imported 3 events');
  await page.waitForFunction(()=>document.querySelector('#owgEventLogPick').options.length===2);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const axe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(axe.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[]);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile width');
  for(let i=0;i<2;i++){
   page.once('dialog',d=>d.accept());await page.locator('#owgEventLogDelete').click();await status('Event log deleted.');
  }
  await page.locator('#owgEventLogManage').waitFor({state:'hidden'});assert.deepEqual(errors,[]);
  console.log('Source-ID CSV browser acceptance passed: explicit mapping and consent, identical payload/distinct IDs, replay, atomic conflict, diagnostics, key-preserving export/reimport, WCAG, mobile and deletion.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
