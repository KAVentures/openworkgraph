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
 const submitUrl=url=>url.pathname.endsWith('/analysis-jobs')&&url.searchParams.get('source_bound')==='true';
 const resultUrl=url=>/\/analysis-jobs\/[^/]+\/result$/.test(url.pathname);
 const waitForHeld=async(promise,label)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Timed out waiting for '+label)),15000);})]);}finally{clearTimeout(timer);}};
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
  await page.route(submitUrl,async route=>{const response=await route.fetch();seenSubmit();await new Promise(r=>releaseSubmit=r);await route.fulfill({response});},{times:1});
  await run();await waitForHeld(heldSubmit,'source-bound submission');await page.locator('#owgAnalysisCancel').click();releaseSubmit();
  await status.filter({hasText:'no result was displayed'}).waitFor();assert.equal(await out.locator('table').count(),0);
  await waitUntil(async()=>(await(await fetch(base+'/test-analysis/state')).json()).jobs===0);
  // Cancel while a successful result is held: a late complete response cannot restore export/save.
  let releaseResult,seenResult;const heldResult=new Promise(r=>seenResult=r);
  await page.route(resultUrl,async route=>{const response=await route.fetch();seenResult();await new Promise(r=>releaseResult=r);await route.fulfill({response});},{times:1});
  await run();await waitForHeld(heldResult,'retained result');await page.locator('#owgAnalysisCancel').click();releaseResult();
  await status.filter({hasText:'no result was displayed'}).waitFor();assert.equal(await out.locator('table').count(),0);
  assert.ok(await page.locator('#owgAnalysisJson').isDisabled());
  // A log/view refresh also suppresses a held completed result.
  let releaseStale,seenStale;const heldStale=new Promise(r=>seenStale=r);
  await page.route(resultUrl,async route=>{const response=await route.fetch();seenStale();await new Promise(r=>releaseStale=r);await route.fulfill({response});},{times:1});
  await run();await waitForHeld(heldStale,'stale retained result');await page.locator('#owgEventLogAnalyze').click();
  const staleResponse=page.waitForResponse(r=>resultUrl(new URL(r.url())));
  releaseStale();await staleResponse;
  assert.equal(await page.locator('#owgAnalysisResults table').count(),0);
  await page.locator('#owgAnalysisRun').click();await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis complete.'}).waitFor();
  // Same result can still be explicitly saved and re-run via the normal builder.
  await page.locator('#owgAnalysisName').fill('Two cases');await page.locator('#owgAnalysisSave').click();
  await page.getByRole('button',{name:'Run saved analysis Two cases',exact:true}).click();await complete();
  await page.locator('#owgCaseFilterContainsActivity').fill('B');
  await page.locator('#owgCaseFilterApply').click();
  const scope=page.getByLabel('Cases to analyse',{exact:true});
  await scope.waitFor();
  assert.equal(await scope.inputValue(),'all');
  await run();await complete();assert.match(await out.textContent(),/2 matching cases/);
  await scope.selectOption('active');assert.ok(await page.locator('#owgAnalysisSave').isDisabled());
  await run();await complete();assert.match(await out.textContent(),/1 matching cases/);
  await page.locator('#owgAnalysisName').fill('Only B');
  await page.locator('#owgAnalysisSave').click();
  const load=page.getByRole('button',{name:'Load saved analysis Only B into builder',exact:true});
  await load.waitFor();
  await page.locator('#owgCaseFilterClear').click();
  await scope.waitFor({state:'hidden'});
  await load.click();
  await status.getByText('Loaded Only B into the builder.',{exact:false}).waitFor();
  assert.equal(await scope.inputValue(),'saved');
  await run();await complete();assert.match(await out.textContent(),/1 matching cases/);
  await scope.selectOption('all');
  await run();await complete();assert.match(await out.textContent(),/2 matching cases/);

  const ocel={objectTypes:[],eventTypes:[],
    objects:[{id:'order-1',type:'order'},{id:'item-1',type:'item'},{id:'item-2',type:'item'}],
    events:[{id:'e1',type:'Pack',time:'2024-02-01T08:00:00Z',
      relationships:['order-1','item-1','item-2'].map(objectId=>({objectId,qualifier:''}))}]};
  await page.locator('#owgEventLogFile').setInputFiles({name:'object-scope.jsonocel',mimeType:'application/json',
    buffer:Buffer.from(JSON.stringify(ocel))});
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').getByText('Imported 1 events.',{exact:false}).waitFor();
  await page.locator('#owgEventLogCaseType').selectOption('order');
  await page.locator('#owgEventLogAnalyze').click();
  await run();await complete();assert.match(await out.textContent(),/1 matching cases.*Case notion: order/);
  await page.locator('#owgAnalysisName').fill('Orders');
  await page.locator('#owgAnalysisSave').click();
  const savedOrder=page.getByRole('button',{name:'Run saved analysis Orders',exact:true});
  await savedOrder.waitFor();
  await page.locator('#owgEventLogCaseType').selectOption('item');
  await page.locator('#owgEventLogAnalyze').click();
  await run();await complete();assert.match(await out.textContent(),/2 matching cases.*Case notion: item/);
  await page.locator('#owgAnalysisBackground').uncheck();
  await savedOrder.click();await complete();assert.match(await out.textContent(),/1 matching cases.*Case notion: order/);
  assert.equal(await page.locator('#owgEventLogCaseType').inputValue(),'item');
  const [scopedExport]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisJson').click()]);
  const exported=JSON.parse(await fs.readFile(await scopedExport.path(),'utf8'));
  assert.equal(exported.case_notion,'order');assert.deepEqual(exported.rows,[[1]]);
  const [definition]=await Promise.all([page.waitForEvent('download'),
    page.getByRole('button',{name:'Download definition for Orders',exact:true}).click()]);
  const body=await fs.readFile(await definition.path());
  const definitionJson=JSON.parse(body.toString());
  assert.deepEqual(Object.keys(definitionJson).sort(),['analysis','format']);
  assert.equal(definitionJson.analysis.spec.case_type,'order');
  const firstLog=await page.locator('#owgEventLogPick').evaluate(select=>
    [...select.options].find(option=>option.textContent.startsWith('jobs ·')).value);
  await page.locator('#owgEventLogPick').selectOption(firstLog);
  await page.locator('#owgEventLogAnalyze').click();
  const templatePanel=page.locator('#owgAnalysisTemplate');
  await templatePanel.getByText('Import an analysis definition',{exact:true}).click();
  const pickTemplate=templatePanel.getByLabel('Analysis definition file',{exact:true});
  await pickTemplate.setInputFiles({name:definition.suggestedFilename(),mimeType:'application/json',buffer:body});
  await templatePanel.getByText('unknown object type for this log',{exact:false}).waitFor();
  assert(await templatePanel.getByRole('button',{name:'Save imported analysis'}).isDisabled());
  const [csvDefinition]=await Promise.all([page.waitForEvent('download'),
    page.getByRole('button',{name:'Download definition for Only B',exact:true}).click()]);
  const filteredBody=await fs.readFile(await csvDefinition.path());
  await page.locator('#owgEventLogFile').setInputFiles({name:'destination.csv',mimeType:'text/csv',
    buffer:Buffer.from('case,activity,time\nx,B,2024-02-02T00:00:00Z\ny,C,2024-02-03T00:00:00Z\n')});
  await page.locator('#owgEventLogStatus').getByText('File mapping ready.',{exact:false}).waitFor();
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').getByText('Imported 2 events.',{exact:false}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  await templatePanel.getByText('Import an analysis definition',{exact:true}).click();
  const templateFile={name:csvDefinition.suggestedFilename(),mimeType:'application/json',buffer:filteredBody};
  await pickTemplate.setInputFiles(templateFile);
  await templatePanel.getByText('Ready to save.',{exact:true}).waitFor();
  await templatePanel.getByText('{"activity":"B"}',{exact:true}).waitFor();
  assert.equal(await page.locator('#owgAnalysisSaved tbody tr').count(),0);
  await templatePanel.getByRole('button',{name:'Save imported analysis'}).click();
  await templatePanel.getByText('Analysis definition saved.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Run saved analysis Only B',exact:true}).click();
  await complete();assert.match(await out.textContent(),/1 matching cases/);
  await pickTemplate.setInputFiles(templateFile);
  await templatePanel.getByText('This analysis is already saved.',{exact:true}).waitFor();
  assert(await templatePanel.getByRole('button',{name:'Save imported analysis'}).isDisabled());


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
