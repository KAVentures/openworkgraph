const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-prediction-monitoring-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 // Open → Work → Close every 4 hours; from case 800 each step takes three times as long.
 let seed=7;const rand=()=>(seed=(seed*16807)%2147483647)/2147483647,exp=mean=>-mean*Math.log(1-rand());
 const rows=['case_id,activity,time'],start=Date.parse('2024-01-01T00:00:00Z');
 for(let i=0;i<1200;i++){
  const t=start+i*4*3600e3,mean=i>=800?30:10,a=exp(mean),b=exp(mean);
  rows.push(`c${i},Open,${new Date(t).toISOString()}`,`c${i},Work,${new Date(t+a*3600e3).toISOString()}`,`c${i},Close,${new Date(t+(a+b)*3600e3).toISOString()}`);
 }
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  await page.locator('#owgEventLogFile').setInputFiles({name:'slowing.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length>=3);
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 3,600 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgEventLogPredictions').waitFor();
  await page.locator('#owgPredictEnds').fill('Close');
  // The live estimator setting does not change what monitoring scores.
  await page.locator('#owgPredictEstimator').selectOption('survival');
  const [watched]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/predictions/monitoring')),page.locator('#owgPredictMonitor').click()]);
  assert.equal(watched.status(),200,await watched.text());
  const sent=JSON.parse(watched.request().postData());
  assert.deepEqual(sent.end_activities,['Close']);assert.equal(sent.windows,6);assert.equal('estimator' in sent,false);
  const out=page.locator('#owgPredictMonitoring');
  await out.getByText(/^In the latest window ranges have become too narrow and typical error has risen\./).waitFor();
  assert.equal(await out.locator('tbody tr').count(),6);
  assert.match(await out.textContent(),/Proposal, not applied: scale each range around its median by \d\.\d\d×/);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogPredictions'),{
   runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'no horizontal page scroll at 390 px');
  assert.deepEqual(errors,[]);
  console.log('Prediction monitoring browser passed: rolling windows on a slowing process, narrow-range and rising-error flags, a recalibration proposal that is not applied, WCAG and 390 px width.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
