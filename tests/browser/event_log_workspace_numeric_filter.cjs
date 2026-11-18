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
   objects.push({id,type:'order',attributes:[{name:'\ufeffcost\\"mixed 雪',time:new Date(t).toISOString(),value:100+i*37},{name:'region',time:new Date(t).toISOString(),value:i%2?'EU':'US'}]});
   for(const [j,activity,when] of [[0,'Create',t],[1,i>=30?'Slow':'Fast',t+(i>=30?10:1)*86400000]])
    events.push({id:`e${i}-${j}`,type:activity,time:new Date(when).toISOString(),attributes:[],relationships:[{objectId:id,qualifier:''}]});
  }
  const doc={objectTypes:[{name:'order',attributes:[]}],eventTypes:['Create','Slow','Fast'].map(name=>({name,attributes:[]})),objects,events};
  await page.locator('#owgEventLogFile').setInputFiles({name:'orders.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  const name='\ufeffcost\\"mixed 雪';
  await page.locator('#owgCaseFilterRangeName').fill(name);await page.locator('#owgCaseFilterRangeMin').fill('1000');await page.locator('#owgCaseFilterRangeMax').fill('1400');
  await page.locator('#owgCaseFilterApply').click();await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 11 of 40 cases'}).waitFor();
  await page.locator('#owgFilterSetName').fill('Range only');await page.locator('#owgFilterSetSave').click();await page.getByRole('button',{name:'Apply saved filter Range only',exact:true}).waitFor();
  await page.locator('#owgCaseFilterAdd').click();await page.locator('#owgCaseFilterFromExact1').fill('Create');await page.locator('#owgCaseFilterToExact1').fill('Slow');
  const [summary,variants,map]=await Promise.all([
    page.waitForResponse(r=>r.url().includes('/summary?')&&new URL(r.url()).searchParams.has('follows')&&r.status()===200),
    page.waitForResponse(r=>r.url().includes('/variants/preview?')&&new URL(r.url()).searchParams.has('follows')&&r.status()===200),
    page.waitForResponse(r=>r.url().includes('/process-map?')&&new URL(r.url()).searchParams.has('follows')&&r.status()===200),
    page.locator('#owgCaseFilterApply').click()]);
  await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 6 of 40 cases'}).waitFor();
  for(const response of [summary,variants,map]){
   const query=new URL(response.url()).searchParams;assert.deepEqual(JSON.parse(query.get('attribute_range')),{name,min:1000,max:1400});assert.equal(JSON.parse(query.get('follows')).length,1);
  }
  assert.equal((await summary.json()).cases,6);assert.equal((await variants.json()).cases,6);assert.ok((await map.json()).activities.every(a=>a.case_count===6));
  await page.locator('#owgFilterSetName').fill('Slow range');await page.locator('#owgFilterSetSave').click();await page.getByRole('button',{name:'Apply saved filter Slow range',exact:true}).waitFor();
  await page.locator('#owgCaseFilterClear').click();await page.getByRole('button',{name:'Apply saved filter Range only',exact:true}).click();
  await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 11 of 40 cases'}).waitFor();
  await page.getByRole('button',{name:'Apply saved filter Slow range',exact:true}).click();await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 6 of 40 cases'}).waitFor();
  const prefix='#owgEventLogCasesFilter';
  await page.locator(prefix+'RangeName').fill(name);await page.locator(prefix+'RangeMin').fill('1200');await page.locator(prefix+'RangeMax').fill('1300');
  const [cases]=await Promise.all([page.waitForResponse(r=>r.url().includes('/cases/query')&&r.request().method()==='POST'),page.locator('#owgEventLogCasesBrowse').click()]);
  assert.deepEqual(cases.request().postDataJSON().cohort.attribute_range,{name,min:1200,max:1300});assert.equal((await cases.json()).selected_cases,3);
  await page.locator('#owgEventLogCasesStatus').filter({hasText:'3 matching cases (case filter applied)'}).waitFor();
  await page.locator('#owgEventLogKpiCohortRangeName').fill(name);await page.locator('#owgEventLogKpiCohortRangeMin').fill('1200');await page.locator('#owgEventLogKpiCohortRangeMax').fill('1300');
  const [kpis]=await Promise.all([page.waitForResponse(r=>r.url().includes('/kpis?')&&r.status()===200),page.locator('#owgEventLogKpiRun').click()]);
  assert.deepEqual(JSON.parse(new URL(kpis.url()).searchParams.get('attribute_range')),{name,min:1200,max:1300});assert.equal((await kpis.json()).cases,3);
  await page.locator(prefix+'RangeName').fill('another field');await page.locator('#owgEventLogCasesBrowse').click();
  await page.locator('#owgEventLogCasesStatus').filter({hasText:'different numeric attribute range'}).waitFor();
  await page.locator(prefix+'RangeName').fill(name);await page.locator(prefix+'RangeMin').fill('1400');await page.locator(prefix+'RangeMax').fill('1500');await page.locator('#owgEventLogCasesBrowse').click();
  await page.locator('#owgEventLogCasesStatus').filter({hasText:'numeric ranges do not overlap'}).waitFor();
  await page.locator('#owgEventLogOutcomeHours').fill('120');
  const [root]=await Promise.all([page.waitForResponse(r=>r.url().includes('/root-cause')&&r.status()===200),page.locator('#owgEventLogRootCauseRun').click()]);
  const result=await root.json();assert.equal(result.cases,40);const hint=result.associations.find(a=>a.cohort?.attribute_range);assert.ok(hint);
  const [evidence]=await Promise.all([page.waitForResponse(r=>r.url().includes('/cases/query')&&r.request().method()==='POST'),page.getByRole('button',{name:'Inspect cases with '+hint.feature.kind+': '+hint.feature.value,exact:true}).click()]);
  assert.deepEqual(evidence.request().postDataJSON().cohort,hint.cohort);assert.equal((await evidence.json()).selected_cases,hint.cases_with_feature);
  await page.locator('#owgEventLogCasesStatus').filter({hasText:hint.cases_with_feature+' matching cases.'}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audits=await page.evaluate(async()=>{const opts={runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']};return [await window.axe.run(document.querySelector('#owgEventLogCases'),opts),await window.axe.run(document.querySelector('#owgCaseFilter'),opts)];});
  for(const audit of audits)assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));assert.deepEqual(errors,[]);
  console.log('Workspace numeric filter browser passed: combined filters, saved sets, evidence, validation, axe/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
