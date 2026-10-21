const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-shadow-review-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_shadow_review_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>spawnError=error);for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic shadow-review server exited: '+logs);
   try{await fs.access(path.join(scratch,'shadow-report.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Synthetic shadow-review server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let reportRequests=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(new URL(request.url()).pathname==='/v1/process-catalog/shadow-review')reportRequests++;});
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Support shadow review · r1',exact:true}).click();
  const report=JSON.parse(await fs.readFile(path.join(scratch,'shadow-report.json'),'utf8'));
  await page.getByLabel('Choose shadow evaluation report',{exact:true}).setInputFiles({name:'shadow.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(report))});
  assert.equal(reportRequests,0);
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogShadowResult h4').filter({hasText:'0 of 1 cases match customer expectations'}).waitFor();
  assert.equal(reportRequests,1);
  assert.match(await page.locator('#owgCatalogShadowResult').textContent(),/Unexpected clarification/);
  assert(!(await page.locator('#owgCatalogShadowResult').textContent()).includes('PRIVATE_'));
  await page.getByLabel('Business goal',{exact:true}).fill('Revised draft only');
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogShadowResult h4').waitFor();
  await page.getByLabel('Choose shadow evaluation report',{exact:true}).setInputFiles({name:'wrong-spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...report,spec_sha256:'c'.repeat(64)}))});
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'report does not match this exact saved process specification'}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Support shadow review · r1',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  const checked=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
assert c.execute('SELECT COUNT(*) FROM workflow_knowledge').fetchone()[0]==1
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
assert 'PRIVATE_' not in '\\n'.join(c.iterdump())
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(checked.status,0,checked.stderr);assert.deepEqual(errors,[]);
  console.log('Shadow review browser acceptance passed: actual generated report, explicit inspection, exact saved revision, mismatch review, edit/file/reload clearing, mobile and no persistence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
