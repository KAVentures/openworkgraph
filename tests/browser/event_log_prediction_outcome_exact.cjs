const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-predict-exact-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let browser,logs='';for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-10000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  const day=n=>Date.UTC(2024,0,1+n),stamp=(d,secs)=>new Date(day(d)+secs*1000).toISOString();
  const rare='  rare 雪😀  ',rows=['case,activity,time'];
  for(let i=0;i<20;i++)rows.push(`c${i},Receive,${stamp(i,0)}`,`c${i},Check,${stamp(i,3600)}`,`c${i},Close,${stamp(i,39600)}`);
  for(let i=0;i<500;i++)for(let j=0;j<2;j++)rows.push(`c${j},Action${String(i).padStart(3,'0')},${stamp(j,3601+i)}`);
  rows.push(`c0,${rare},${stamp(0,7200)}`);
  for(let i=0;i<6;i++)rows.push(`o${i},Receive,${stamp(30,0)}`,`o${i},Check,${stamp(30,3600)}`);
  await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgPredictEnds').fill('Close');
  if(await page.locator('#owgPredictAddEnd').count())await page.locator('#owgPredictAddEnd').click();
  await page.locator('#owgPredictOutcome').selectOption('contains_activity');
  assert.equal(await page.locator('#owgPredictActivity option').filter({hasText:rare}).count(),0);
  await page.locator('#owgPredictOutcomeExact').fill('rare');await page.locator('#owgPredictOutcomeActivityFind').click();
  await page.locator('#owgPredictOutcomeActivityMatches').selectOption(rare);await page.locator('#owgPredictOutcomeActivityUse').click();
  assert.equal(await page.locator('#owgPredictOutcomeExact').inputValue(),rare);
  async function request(button,suffix){
    const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith(suffix)&&r.request().method()==='POST'),page.locator(button).click()]);
    assert.equal(response.status(),200,await response.text());assert.deepEqual(response.request().postDataJSON().outcome,{kind:'contains_activity',activity:rare});
  }
  await request('#owgPredictRun','/predictions');
  await request('#owgPredictMonitor','/predictions/monitoring');
  await request('#owgPredictSnapshot','/prediction-snapshots');
  await page.locator('#owgPredictSnapshotStatus').filter({hasText:'Saved a snapshot'}).waitFor();
  await page.locator('#owgPredictActivity').selectOption('Check');
  assert.equal(await page.locator('#owgPredictOutcomeExact').inputValue(),'');
  assert.equal(await page.locator('#owgPredictResults').textContent(),'');
  assert.equal(await page.locator('#owgPredictMonitoring').textContent(),'');
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogPredictions'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert.deepEqual(errors,[]);
  console.log('Prediction outcome browser passed: rare literal label in estimates, monitoring, snapshots, reset and accessibility.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
