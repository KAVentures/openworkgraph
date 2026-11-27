const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-cluster-export-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','cluster_export_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',error=>{spawnError=error;});
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error('Server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw Error('Server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  const rows=['case,activity,time'];
  for(let i=0;i<8;i++){const steps=i%2?['Open','Check','Close']:['Open','Reject','Archive'];
   steps.forEach((activity,k)=>rows.push(`c${i},${activity},2024-01-0${i+1}T0${k}:00:00Z`));}
  await page.locator('#owgEventLogFile').setInputFiles({name:'cluster.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  const box=page.locator('#owgEventLogClusters');await box.waitFor();
  await page.locator('#owgClusterK').selectOption('2');await page.locator('#owgClusterRun').click();
  const out=page.locator('#owgClusterResults'),review=page.locator('#owgClusterCaseReview');
  await out.getByRole('button',{name:'Inspect cases in cluster 1'}).waitFor();
  const [jsonFile]=await Promise.all([page.waitForEvent('download'),box.getByRole('button',{name:'Download cluster report JSON'}).click()]);
  const report=JSON.parse(await fs.readFile(await jsonFile.path(),'utf8'));
  assert.equal(report.format,'openworkgraph.trace-cluster-report.v1');assert.equal(report.request.k,2);
  assert.equal(report.report.cases,8);assert.equal(report.report.clusters.length,2);
  assert.equal(report.report.variants_clustered,2);assert.equal(report.report.variants_assigned_to_nearest,0);
  const [csvFile]=await Promise.all([page.waitForEvent('download'),box.getByRole('button',{name:'Download cluster report CSV'}).click()]);
  const csvText=await fs.readFile(await csvFile.path(),'utf8');
  assert.match(csvText,/cluster_key,cluster_cases,cluster_share/);assert.equal(csvText.trim().split('\n').length,3);
  const [pageResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/trace-clusters/cases')),
    out.getByRole('button',{name:'Inspect cases in cluster 1'}).click()]);
  const selected=await pageResponse.json();assert.equal(selected.total,4);
  const [download]=await Promise.all([page.waitForEvent('download'),review.getByRole('button',{name:'Download cases in cluster 1 as CSV'}).click()]);
  const lines=(await fs.readFile(await download.path(),'utf8')).trim().split('\n');
  assert.equal(lines[0],'case_id,duration_seconds,events,first_activity,last_activity');
  await review.getByRole('status').filter({hasText:'Downloaded 2 of 4 cases. Download the next file for more.'}).waitFor();
  const [nextFile]=await Promise.all([page.waitForEvent('download'),review.getByRole('button',{name:'Download next cases in cluster 1 as CSV'}).click()]);
  const nextLines=(await fs.readFile(await nextFile.path(),'utf8')).trim().split('\n');
  assert.equal(nextLines[0],lines[0]);
  assert.deepEqual([...lines.slice(1),...nextLines.slice(1)].map(line=>line.split(',')[0]),selected.cases.map(item=>item.case_id));
  await review.getByRole('status').filter({hasText:'Downloaded 4 of 4 cases.'}).waitFor();
  assert(await review.getByRole('button',{name:'Download next cases in cluster 1 as CSV'}).isDisabled());
  // Restrict the source scope, then preview and apply the matching paths as a tag.
  await page.locator('#owgCaseFilterContainsActivity').fill('Open');
  await page.locator('#owgCaseFilterStartedFrom').fill('2024-01-05');
  await page.locator('#owgCaseFilterApply').click();
  await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 4 of 8 cases'}).waitFor();
  await page.locator('#owgClusterK').selectOption('2');await page.locator('#owgClusterRun').click();
  const [scopedCases]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/trace-clusters/cases')),
    out.getByRole('button',{name:'Inspect cases in cluster 1'}).click()]);
  const scoped=await scopedCases.json();assert.equal(scoped.total,2);
  assert(scoped.cases.every(item=>Number(item.case_id.slice(1))>=4));
  const tags=review.locator('#owgClusterTagging');await tags.locator('summary').click();
  await tags.locator('#owgClusterTagName').fill('cluster review');
  await tags.locator('#owgClusterTagNote').fill('reviewed path');
  const [preview]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/trace-clusters/tag/preview')),
    tags.getByRole('button',{name:'Preview cluster tag'}).click()]);
  const previewRequest=preview.request().postDataJSON();
  assert.equal(previewRequest.cohort.activity,'Open');
  assert.equal(previewRequest.cohort.started_from,'2024-01-05T00:00:00Z');
  assert.equal(previewRequest.cluster_key,scoped.cluster_key);
  await tags.locator('#owgClusterTagStatus').filter({hasText:'2 matching cases. 0 already have this tag.'}).waitFor();
  // A concurrent tag change invalidates the reviewed selection instead of overwriting its note.
  await page.evaluate(async caseId=>{
    const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;
    const response=await fetch('/v1/event-logs/'+id+'/case-tags',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tag:'cluster review',note:'other review',case_ids:[caseId]})});
    if(!response.ok)throw Error('Could not create concurrent tag');
  },scoped.cases[0].case_id);
  await tags.getByRole('button',{name:'Apply cluster tag'}).click();
  await tags.locator('#owgClusterTagStatus').filter({hasText:'changed; preview again'}).waitFor();
  await tags.getByRole('button',{name:'Preview cluster tag'}).click();
  await tags.locator('#owgClusterTagStatus').filter({hasText:'1 already have this tag.'}).waitFor();
  await tags.getByText('other review',{exact:true}).waitFor();
  let releaseTag,tagStarted;const heldTag=new Promise(resolve=>{releaseTag=resolve;}),startedTag=new Promise(resolve=>{tagStarted=resolve;});
  await page.route('**/trace-clusters/tag',async route=>{const response=await route.fetch();tagStarted();await heldTag;await route.fulfill({response});},{times:1});
  await tags.getByRole('button',{name:'Apply cluster tag'}).click();await startedTag;
  assert(await tags.locator('#owgClusterTagName').isDisabled());assert(await tags.locator('#owgClusterTagNote').isDisabled());
  const savedTag=page.waitForResponse(r=>r.url().endsWith('/trace-clusters/tag')&&r.request().method()==='POST');releaseTag();await savedTag;
  await page.locator('#owgClusterStatus').filter({hasText:'Tagged 2 cases “cluster review”.'}).waitFor();
  assert.equal(await out.textContent(),'');assert.equal(await review.textContent(),'');
  const tagged=await page.evaluate(async()=>{
    const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;
    return (await (await fetch('/v1/event-logs/'+id+'/case-tags?tag=cluster%20review')).json()).cases;
  });
  assert.deepEqual(tagged.map(item=>item.case_id).sort(),scoped.cases.map(item=>item.case_id).sort());
  assert(tagged.every(item=>item.note==='reviewed path'));
  // Clusters that cannot be represented as a variant cohort remain fully taggable.
  await page.route('**/trace-clusters',async route=>{const response=await route.fetch(),body=await response.json();
    body.clusters[0].cohort=null;await route.fulfill({response,json:body});},{times:1});
  await page.locator('#owgClusterRun').click();await out.getByRole('button',{name:'Inspect cases in cluster 1'}).click();
  await review.locator('#owgClusterTagging summary').click();
  await review.locator('#owgClusterTagName').fill('large path set');
  await review.getByRole('button',{name:'Preview cluster tag'}).click();
  await review.locator('#owgClusterTagStatus').filter({hasText:'2 matching cases.'}).waitFor();
  assert(await review.getByRole('button',{name:'Apply cluster tag'}).isEnabled());
  // The case-count safety limit refuses tagging instead of applying a partial cluster.
  await page.route('**/trace-clusters',async route=>{const response=await route.fetch(),body=await response.json();
    body.clusters[0].cases=100001;await route.fulfill({response,json:body});},{times:1});
  await page.locator('#owgClusterRun').click();await out.getByRole('button',{name:'Inspect cases in cluster 1'}).click();
  await review.locator('#owgClusterTagging summary').click();
  await review.getByText(/limited to 100,000 cases/).waitFor();
  assert.equal(await review.getByRole('button',{name:'Apply cluster tag'}).count(),0);
  await page.locator('#owgClusterK').selectOption('3');assert.equal(await out.textContent(),'');assert.equal(await review.textContent(),'');
  assert(await box.getByRole('button',{name:'Download cluster report JSON'}).isDisabled());
  assert(await box.getByRole('button',{name:'Download cluster report CSV'}).isDisabled());
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogClusters'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Trace cluster case export browser acceptance passed.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
