const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-jira-rebind-browser-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','jira_credential_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();

  const refs=['owg:r:'+'1'.repeat(32),'owg:r:'+'2'.repeat(32)],out=page.locator('#owgJiraSourceProfiles');
  await page.locator('#owgJiraSourceRefs').fill(refs.join('\n'));
  for(const id of ['Read','Retain','Labels'])await page.locator('#owgJiraSource'+id).check();
  await page.locator('#owgJiraSourceCreate').click();
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).waitFor();
  assert.equal(await out.getByRole('button',{name:'Verify renewed credential',exact:true}).isDisabled(),true);
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();
  await out.getByRole('button',{name:'Open retained log',exact:true}).waitFor();
  const log=await page.locator('#owgEventLogPick').inputValue();
  await out.getByRole('button',{name:'Disable profile',exact:true}).click();
  await out.getByRole('button',{name:'Enable profile',exact:true}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgEventLogResults').getByText('Status: Ready',{exact:true}).first().waitFor();
  // Auxiliary map/quality panels finish asynchronously. Preserve the actual
  // analysis nodes and unsaved input, rather than comparing their loading text.
  await page.locator('#owgAnalysisName').fill('Keep this unsaved analysis');
  await page.evaluate(()=>{window.ownedAnalysisNode=document.querySelector('#owgAnalysisName');});
  const overview=await page.locator('#owgEventLogResults > div').first().textContent();
  const sourceRead=await out.locator('p').first().textContent();
  const before=JSON.parse(await fs.readFile(path.join(scratch,'provider-counts.json'),'utf8'));
  await fs.writeFile(path.join(scratch,'credential-control.json'),JSON.stringify({mode:'renewed'}));
  await page.locator('#owgJiraSourceRefresh').click();
  await out.getByRole('button',{name:'Verify renewed credential',exact:true}).waitFor();
  const requests=[];page.on('request',r=>requests.push(new URL(r.url()).pathname));
  const verified=page.waitForResponse(r=>r.url().endsWith('/credential')&&r.status()===200);
  page.once('dialog',d=>d.accept());await out.getByRole('button',{name:'Verify renewed credential',exact:true}).click();
  const response=await verified;const result=await response.json();assert.equal(result.credential_generation,2);
  await out.getByText(/Credential check: verified/).waitFor();
  assert.equal(await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).isDisabled(),true);
  assert.equal(await out.locator('p').first().textContent(),sourceRead);
  assert.equal(await page.evaluate(()=>document.querySelector('#owgAnalysisName')===window.ownedAnalysisNode),true);
  assert.equal(await page.locator('#owgAnalysisName').inputValue(),'Keep this unsaved analysis');
  assert.equal(await page.locator('#owgEventLogResults > div').first().textContent(),overview);
  assert.equal(await page.locator('#owgEventLogPick').inputValue(),log);
  // Initial analysis panels may still finish their own detail reads. A
  // credential check must not refresh the dataset picker or rebuild analysis.
  assert.equal(requests.includes('/v1/event-logs'),false);
  const after=JSON.parse(await fs.readFile(path.join(scratch,'provider-counts.json'),'utf8'));
  assert.equal(after.history_reads,before.history_reads);assert.equal(after.issue_reads,before.issue_reads);assert.equal(after.account_reads,before.account_reads+2);
  assert.match(await out.textContent(),/Status: disabled/);
  await out.getByRole('button',{name:'Enable profile',exact:true}).click();
  await out.getByRole('button',{name:'Disable profile',exact:true}).waitFor();
  const replay=page.waitForResponse(r=>r.url().endsWith('/run')&&r.status()===200);
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();
  const replayData=await (await replay).json();assert.equal(replayData.log_id,log);assert.equal(replayData.added_events,0);assert.equal(replayData.duplicate_events_skipped,4);
  await page.waitForFunction(()=>{const b=document.querySelector('#owgJiraSourceProfiles button');return b&&!b.disabled;});
  await fs.writeFile(path.join(scratch,'credential-control.json'),JSON.stringify({mode:'other'}));
  await page.locator('#owgJiraSourceRefresh').click();await out.getByRole('button',{name:'Verify renewed credential',exact:true}).waitFor();
  const refused=page.waitForResponse(r=>r.url().endsWith('/credential')&&r.status()===422);
  page.once('dialog',d=>d.accept());await out.getByRole('button',{name:'Verify renewed credential',exact:true}).click();await refused;
  await out.getByText(/Credential check: failed/).waitFor();
  assert.match(await page.locator('#owgJiraSourceStatus').textContent(),/could not be matched/);
  assert.equal(await page.locator('#owgEventLogPick option').count(),1);
  await fs.writeFile(path.join(scratch,'credential-control.json'),JSON.stringify({mode:'renewed'}));
  await page.locator('#owgJiraSourceRefresh').click();await out.getByRole('button',{name:'Verify renewed credential',exact:true}).waitFor();
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/event-log-sources/jira/*/credential',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  page.once('dialog',d=>d.accept());await out.getByRole('button',{name:'Verify renewed credential',exact:true}).click();await held;
  await out.getByRole('button',{name:'Disable profile',exact:true}).click();
  await out.getByRole('button',{name:'Enable profile',exact:true}).waitFor();
  const old=page.waitForResponse(r=>r.url().endsWith('/credential'));release();await (await old).finished();
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.match(await out.textContent(),/Status: disabled/);
  assert.equal(await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).isDisabled(),true);
  const body=await page.locator('body').textContent();
  for(const marker of ['owned-token-secret','owned-renewed-token','owned-other-token','other-owned-account','EMAIL_SECRET'])assert.equal(body.includes(marker),false);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgJiraSource'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Jira credential rebind browser passed: same-account renewal while paused, preserved history freshness/analysis, no issue reads, replay, cross-account refusal, stale response after disable, secret exclusion, WCAG/mobile');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
