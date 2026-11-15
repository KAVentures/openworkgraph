const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-root-evidence-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  const objects=[],events=[],start=Date.UTC(2024,0,1);
  for(let i=0;i<40;i++){
   const id='o'+i,t=start+i*86400000;
   objects.push({id,type:'order',attributes:[{name:'cost\\"mixed 雪',time:new Date(t).toISOString(),value:100+i*37},{name:'region',time:new Date(t).toISOString(),value:i%2?'EU':'US'}]});
   for(const [j,activity,when] of [[0,'Create',t],[1,i>=30?'Slow':'Fast',t+(i>=30?10:1)*86400000]])
    events.push({id:`e${i}-${j}`,type:activity,time:new Date(when).toISOString(),attributes:[],relationships:[{objectId:id,qualifier:''}]});
  }
  const doc={objectTypes:[{name:'order',attributes:[]}],eventTypes:['Create','Slow','Fast'].map(name=>({name,attributes:[]})),objects,events};
  await page.locator('#owgEventLogFile').setInputFiles({name:'orders.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgCaseFilterAdd').click();await page.locator('#owgCaseFilterFromExact1').fill('Create');await page.locator('#owgCaseFilterToExact1').fill('Slow');await page.locator('#owgCaseFilterApply').click();
  await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 10 of 40 cases'}).waitFor();
  const prefix='#owgEventLogCasesFilter';
  await page.locator(prefix+'RangeName').fill('cost\\"mixed 雪');
  await page.locator(prefix+'RangeMin').fill('1000');await page.locator(prefix+'RangeMax').fill('1400');
  const [cases]=await Promise.all([page.waitForResponse(r=>r.url().includes('/cases/query')&&r.request().method()==='POST'),page.locator('#owgEventLogCasesBrowse').click()]);
  assert.deepEqual(cases.request().postDataJSON().cohort.attribute_range,{name:'cost\\"mixed 雪',min:1000,max:1400});
  assert.equal((await cases.json()).selected_cases,6); // intersects the active follower filter
  await page.locator('#owgEventLogCasesStatus').filter({hasText:'6 matching cases'}).waitFor();
  await page.locator(prefix+'RangeMax').fill('900');await page.locator('#owgEventLogCasesBrowse').click();
  await page.locator('#owgEventLogCasesStatus').filter({hasText:'minimum must be below'}).waitFor();
  const first='#owgEventLogCohortA',second='#owgEventLogCohortB';
  for(const selector of [first,second])await page.locator(selector+'RangeName').fill('cost\\"mixed 雪');
  await page.locator(first+'RangeMax').fill('1000');await page.locator(second+'RangeMin').fill('1000');
  const [comparison]=await Promise.all([page.waitForResponse(r=>r.url().includes('/benchmark')&&r.request().method()==='POST'),page.locator('#owgEventLogBenchRun').click()]);
  const sent=comparison.request().postDataJSON();assert.deepEqual(sent.cohorts.map(c=>c.attribute_range),[{name:'cost\\"mixed 雪',max:1000},{name:'cost\\"mixed 雪',min:1000}]);
  assert.deepEqual((await comparison.json()).cohorts.map(c=>c.cases),[25,15]);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audits=await page.evaluate(async()=>{const opts={runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']};return [await window.axe.run(document.querySelector('#owgEventLogCases'),opts),await window.axe.run(document.querySelector('#owgEventLogCohortA'),opts)];});
  for(const audit of audits)assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));assert.deepEqual(errors,[]);
  console.log('Numeric cohort browser passed: case evidence and comparisons, validation, axe/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
