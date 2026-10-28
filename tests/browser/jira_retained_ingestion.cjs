const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-jira-retained-browser-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','jira_retained_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();

  const refs=['owg:r:'+'1'.repeat(32),'owg:r:'+'2'.repeat(32)];
  const box=page.locator('#owgJiraSource'),out=page.locator('#owgJiraSourceProfiles');
  await page.locator('#owgJiraSourceRefs').fill(refs.join('\n'));
  assert.equal(await page.locator('#owgJiraSourceCreate').isDisabled(),true);
  for(const id of ['Read','Retain','Labels'])await page.locator('#owgJiraSource'+id).check();
  await page.locator('#owgJiraSourceRefs').fill(refs[0]);
  assert.equal(await page.locator('#owgJiraSourceRetain').isChecked(),false);
  await page.locator('#owgJiraSourceRefs').fill(refs.join('\n'));
  for(const id of ['Read','Retain','Labels'])await page.locator('#owgJiraSource'+id).check();
  await page.locator('#owgJiraSourceCreate').click();
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).waitFor();
  assert.match(await out.textContent(),/Status: ready/);
  assert.equal(await page.locator('#owgJiraSourceRetain').isChecked(),false);
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();
  await out.getByRole('button',{name:'Open retained log',exact:true}).waitFor();
  assert.match(await out.textContent(),/4 retained projected events/);
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgEventLogResults').getByText('Status: <img src=x onerror=alert(1)>',{exact:true}).first().waitFor();
  assert.equal(await page.locator('#owgEventLogResults img').count(),0);
  const body=await page.locator('body').textContent();
  for(const marker of ['ACTOR_SECRET','EMAIL_SECRET','SUMMARY_SECRET','ASSIGNEE_SECRET','owned-token-secret'])assert.equal(body.includes(marker),false);
  const replayResponse=page.waitForResponse(r=>r.url().endsWith('/run')&&r.status()===200);
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();
  await replayResponse;await page.waitForLoadState('networkidle');
  assert.match(await page.locator('#owgEventLogPick').inputValue(),/^log_/);
  assert.equal(await page.locator('#owgEventLogPick option').count(),1);
  // Hold a successful old response after commit, then disable. It cannot
  // restore enabled/fresh UI state when delivered later.
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/event-log-sources/jira/*/run',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();await held;
  await out.getByRole('button',{name:'Disable profile',exact:true}).click();
  await out.getByRole('button',{name:'Enable profile',exact:true}).waitFor();
  assert.match(await out.textContent(),/Status: disabled/);
  const old=page.waitForResponse(r=>r.url().endsWith('/run'));release();await old;await page.waitForLoadState('networkidle');
  assert.match(await out.textContent(),/Status: disabled/);
  assert.equal(await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).isDisabled(),true);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgJiraSource'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  const disconnected=page.waitForResponse(r=>r.request().method()==='DELETE'&&r.url().includes('/event-log-sources/jira/'));
  page.once('dialog',d=>d.accept());await out.getByRole('button',{name:'Disconnect profile',exact:true}).click();
  const deleted=await disconnected;assert.equal(deleted.status(),200,await deleted.text());await page.waitForLoadState('networkidle');
  await page.waitForFunction(()=>document.querySelector('#owgJiraSourceProfiles').children.length===0);
  assert.equal(await out.locator('[data-profile-id]').count(),0);
  assert.equal(await page.locator('#owgEventLogPick option').count(),1);
  // Create another owned profile and erase its dataset through the existing
  // workspace delete control: connector checkpoint/manifest are erased too.
  for(const id of ['Read','Retain','Labels'])await page.locator('#owgJiraSource'+id).check();
  await page.locator('#owgJiraSourceCreate').click();
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).waitFor();
  await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).click();
  await out.getByRole('button',{name:'Open retained log',exact:true}).waitFor();
  assert.equal(await page.locator('#owgEventLogPick option').count(),2);
  page.once('dialog',d=>d.accept());await page.locator('#owgEventLogDelete').click();
  await out.getByText(/Status: dataset_deleted/).waitFor();
  assert.equal(await out.getByRole('button',{name:'Open retained log',exact:true}).count(),0);
  assert.equal(await out.getByRole('button',{name:'Read and retain selected histories',exact:true}).isDisabled(),true);
  assert.equal(await page.locator('#owgEventLogPick option').count(),1);
  assert.deepEqual(errors,[]);console.log('Retained Jira browser passed: consent invalidation, actual selected-case ingestion/replay and workspace analysis, literal labels, excluded sensitive fields, held old response after disable, disconnect retains log, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
