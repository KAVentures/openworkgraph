const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-github-source-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','github_process_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>spawnError=error);for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic GitHub source server exited: '+logs);
   try{await fs.access(path.join(scratch,'source-calls.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Synthetic GitHub source server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=overview]').click();
  await page.evaluate(()=>window.refreshWorkflowEvidenceDrafting());
  const lane=page.locator('[data-record-lane]').filter({hasText:'github'}).first();
  await lane.locator('summary').click();
  assert.equal(JSON.parse(await fs.readFile(path.join(scratch,'source-calls.json'),'utf8')),0);
  page.once('dialog',dialog=>dialog.accept());
  await lane.getByRole('button',{name:'Read opted-in GitHub review history',exact:true}).click();
  await lane.getByRole('button',{name:'Explore GitHub source process',exact:true}).waitFor();
  assert.equal(JSON.parse(await fs.readFile(path.join(scratch,'source-calls.json'),'utf8')),1);
  await lane.getByRole('button',{name:'Explore GitHub source process',exact:true}).click();
  await page.locator('#owgProcessResults h3').filter({hasText:'Full supplied source projection'}).waitFor();
  await page.getByRole('button',{name:'Explore all observed transitions',exact:true}).click();
  assert.equal(await page.locator('#owgObservedTransitionMap g[role=button]').count(),3);
  await page.getByRole('button',{name:'Inspect observed activity Review approved',exact:true}).press('Enter');
  assert.match(await page.locator('#owgObservedActivityDetails').textContent(),/1 recorded events across 1 cases/);
  await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
  await page.getByLabel('I authorize retaining this selected event log locally.').check();
  assert(await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).isDisabled());
  await page.getByRole('button',{name:'Design improved TO-BE workflow',exact:true}).click();
  await page.getByLabel('Business goal',{exact:true}).fill('Review a selected pull request');
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1.'}).waitFor();
  assert.equal(JSON.parse(await fs.readFile(path.join(scratch,'source-calls.json'),'utf8')),1);
  assert(!(await page.locator('#owgProcessResults').textContent()).includes('PRIVATE_'));
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  const checked=spawnSync(python,['-c',`import json,sqlite3,sys
c=sqlite3.connect(sys.argv[1]);original=json.load(open(sys.argv[2]))
assert [list(row) for row in c.execute('SELECT * FROM events ORDER BY id')]==original
rows=c.execute('SELECT payload FROM workflow_knowledge').fetchall();assert len(rows)==1
model=json.loads(rows[0][0])['process_model']
assert model['baseline_source']=='selected_source_audit' and model['baseline_coverage']=='partial_source_audit'
assert 'PRIVATE_' not in '\\n'.join(c.iterdump())
`,path.join(scratch,'data','workflow_observer.db'),path.join(scratch,'original-events.json')],{encoding:'utf8'});
  assert.equal(checked.status,0,checked.stderr);assert.deepEqual(errors,[]);
  console.log('GitHub process browser acceptance passed: canonical proof, separate source opt-in, explicit read, existing CLI projection, shared map, no retention, partial-source design, mobile and unchanged evidence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
