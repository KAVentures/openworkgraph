const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-wait-boundary-browser-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();

  const long='Long <img src=x onerror=alert(1)>',rows=['case_id,activity,time,lifecycle:transition'];
  for(const [activity,start,end] of [[long,0,10],['Short',1,2],['Next',20,21]]){
    rows.push(['overlap',activity,new Date(Date.UTC(2024,0,1,start)).toISOString(),'start'].join(','));
    rows.push(['overlap',activity,new Date(Date.UTC(2024,0,1,end)).toISOString(),'complete'].join(','));
  }
  await page.locator('#owgEventLogFile').setInputFiles({name:'overlap.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.locator('#owgEventLogDelimiter').selectOption(',');
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
  await page.locator('#owgEventLogCase').selectOption('case_id');await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('time');await page.locator('#owgEventLogResource').selectOption('');
  await page.locator('#owgEventLogAttributes').selectOption(['lifecycle:transition']);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 6 events'}).waitFor();await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgWaitingRun').click();await page.locator('#owgWaitingStatus').filter({hasText:'3 of 3 activity instances have a start and an end'}).waitFor();
  const result=page.locator('#owgWaitingResults'),row=result.locator('table').nth(1).getByRole('row').filter({hasText:'Next'});
  const cells=await row.getByRole('cell').allTextContents();assert.deepEqual(cells.slice(0,2),[long,'Next']);assert.match(cells[3],/10\.0 h/);
  assert.match(await result.textContent(),/does not establish a causal handoff/);assert.equal(await result.locator('img').count(),0);

  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgWaiting'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.setViewportSize({width:1280,height:900});
  // A held report for old settings cannot replace the later explicit query.
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/waiting-time?*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#owgWaitingRun').click();await held;
  await page.locator('#owgWaitingLifecycle').fill('missing_lifecycle');assert.equal(await result.textContent(),'');
  assert.equal(await page.locator('#owgWaitingRun').isDisabled(),false);await page.locator('#owgWaitingRun').click();
  await page.locator('#owgWaitingStatus').filter({hasText:'No start or complete values'}).waitFor();
  const old=page.waitForResponse(r=>r.url().includes('/waiting-time?')&&new URL(r.url()).searchParams.get('lifecycle_attribute')==='lifecycle:transition');release();await old;await page.waitForLoadState('networkidle');
  assert.match(await page.locator('#owgWaitingStatus').textContent(),/No start or complete values/);assert.doesNotMatch(await result.textContent(),/10\.0 h/);
  assert.deepEqual(errors,[]);console.log('Waiting boundary browser passed: actual overlapping lifecycle import, longest-prior-end hotspot label and unchanged10h gap, literal unsafe-looking activity, timestamps-only interpretation, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
