const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-shared-cases-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>spawnError=e);
 for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Owned server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }assert(ready,'Owned server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let previews=0,saves=0;
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{
   const url=new URL(r.url());if(url.pathname==='/v1/process-mining/preview')previews++;
   if(url.pathname==='/v1/process-mining/datasets'&&r.method()==='POST')saves++;
  });
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByText('Combine authorized exports using a shared case key',{exact:true}).click();
  const a='Order;Operation;When;Private;Account\nJOIN_NATIVE_ORDER;Open;2026-10-20T00:00:00Z;JOIN_EXTRA_SECRET;JOIN_SYSTEM_ACCOUNT\nJOIN_NATIVE_UNMATCHED_A;Open;2026-10-20T00:03:00Z;JOIN_EXTRA_SECRET;JOIN_SYSTEM_ACCOUNT\n';
  const b='Reference,Stage,At,Other\nJOIN_NATIVE_ORDER,Review,2026-10-20T00:01:00Z,JOIN_EXTRA_SECRET\nJOIN_NATIVE_ORDER,Close,2026-10-20T00:02:00Z,JOIN_EXTRA_SECRET\nJOIN_NATIVE_UNMATCHED_B,Waiting,2026-10-20T00:00:30Z,JOIN_EXTRA_SECRET\n';
  await page.getByLabel('Choose two to four event-log exports',{exact:true}).setInputFiles([
   {name:'JOIN_SOURCE_A.csv',mimeType:'text/csv',buffer:Buffer.from(a)},
   {name:'JOIN_SOURCE_B.csv',mimeType:'text/csv',buffer:Buffer.from(b)}]);
  await page.getByLabel('Delimiter · Source 1',{exact:true}).selectOption(';');
  await page.getByRole('button',{name:'Inspect selected export columns',exact:true}).click();
  await page.locator('#owgSharedCaseStatus').filter({hasText:'Choose each export’s authoritative shared case key'}).waitFor();
  for(const n of [1,2])for(const [label,index] of [['Shared authoritative case key',0],['Activity column',1],['Timestamp column',2]])
   await page.getByLabel(label+' · Source '+n,{exact:true}).selectOption(String(index));
  await page.getByRole('button',{name:'Prepare shared-key import for review',exact:true}).click();
  await page.locator('#owgSharedCaseStatus').filter({hasText:'5 events across 3 cases from 2 exports; 1 case shares keys'}).waitFor();
  assert.equal(previews,0);assert.equal(saves,0);
  assert(await page.getByRole('button',{name:'Use reviewed shared-key import for next analysis',exact:true}).isDisabled());
  await page.getByRole('button',{name:'Show local shared-case lookup',exact:true}).click();
  assert((await page.locator('#owgSharedCaseLookup').textContent()).includes('case_key_1 · "JOIN_NATIVE_ORDER" · Source 1, Source 2'));
  await page.getByRole('button',{name:'Hide local shared-case lookup',exact:true}).click();assert.equal(await page.locator('#owgSharedCaseLookup').textContent(),'');
  await page.locator('#owgSharedCaseReview').check();
  await page.getByRole('button',{name:'Use reviewed shared-key import for next analysis',exact:true}).click();
  assert.equal(previews,0);assert.equal(saves,0);
  const preview=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-mining/preview');
  await page.getByRole('button',{name:'Analyze reviewed combined log',exact:true}).click();
  const response=await preview,report=await response.json();assert.equal(response.status(),200);
  assert.equal(report.case_count,3);assert.equal(report.case_correlation.multi_source_case_count,1);
  assert.equal(report.cases[0].case_key_alias,'case_key_1');assert.deepEqual(report.cases[0].events.map(e=>e.origin),['source_1','source_2','source_2']);
  await page.getByRole('heading',{name:'Reviewed shared case keys',exact:true}).waitFor();
  assert(!(await page.locator('#owgProcessResults').textContent()).includes('JOIN_NATIVE_'));
  const receipt=JSON.parse(await fs.readFile(path.join(scratch,'last-preview-shape.json'),'utf8'));
  assert.equal(receipt.header,'case_id,activity,timestamp,origin');assert.equal(receipt.unselected_test_markers_seen,false);
  assert.equal(saves,0);
  await page.getByRole('button',{name:'Explore all observed transitions',exact:true}).click();
  await page.getByRole('button',{name:'Inspect observed activity Open',exact:true}).click();
  const globalSource=page.locator('#owgObservedActivityDetails tbody tr').filter({hasText:'Source 1'});
  assert.deepEqual(await globalSource.locator('td').allTextContents(),['Source 1','2','2']);
  assert(!(await page.locator('#owgObservedActivityDetails').textContent()).includes('Participant labels'));
  await page.getByRole('button',{name:'Open · 1 events',exact:true}).click();
  const variantSource=page.locator('#owgProcessActivityDetails tbody tr').filter({hasText:'Source 1'});
  assert.deepEqual(await variantSource.locator('td').allTextContents(),['Source 1','1','1']);
  assert.deepEqual(await page.locator('#owgObservedActivityDetails tbody tr').filter({hasText:'Source 1'}).locator('td').allTextContents(),['Source 1','2','2']);
  assert.equal(previews,1);assert.equal(saves,0);
  // Withdrawing a retention grant cancels a still-unsent request even if review is checked again.
  await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
  const retention=page.getByLabel('I authorize retaining this selected event log locally.',{exact:true});
  await retention.check();await page.evaluate(async()=>{const ready=await window.__owgAuthReady;window.__owgAuthReady=new Promise(resolve=>window.releaseSharedCaseAuth=()=>resolve(ready));});
  await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).click();
  await retention.uncheck();await retention.check();await page.evaluate(()=>window.releaseSharedCaseAuth());
  await page.waitForTimeout(100);assert.equal(saves,0);
  await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).click();
  try{await page.locator('#owgProcessStatus').filter({hasText:'Saved a minimized local snapshot.'}).waitFor();}catch(error){throw new Error('Save flow: '+JSON.stringify({status:await page.locator('#owgProcessStatus').textContent(),saves,errors}));}
  assert.equal(saves,1);
  const retainedReceipt=JSON.parse(await fs.readFile(path.join(scratch,'last-retention-shape.json'),'utf8'));
  assert.equal(retainedReceipt.header,'case_id,activity,timestamp,origin');assert.equal(retainedReceipt.unselected_test_markers_seen,false);
  const open=page.getByRole('button',{name:/^Open log_/});await open.waitFor();const id=(await open.textContent()).slice(5);
  await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
  await page.getByRole('button',{name:'List saved local logs',exact:true}).click();await page.getByRole('button',{name:'Open '+id,exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Opened retained local snapshot.'}).waitFor();
  assert.equal(await page.locator('#owgSharedCaseLookup').textContent(),'');
  const first=page.locator('#owgLogCases details').filter({hasText:'Case 1 · 3 events'});await first.locator('summary').click();
  await first.locator('li').filter({hasText:'Source 2'}).first().waitFor();assert.equal(await first.locator('li').count(),3);
  await page.getByLabel('Case contains exact activity',{exact:true}).fill('Waiting');
  await page.getByRole('button',{name:'Apply case filters',exact:true}).click();
  await page.locator('#owgCohortStatus').filter({hasText:'Selected 1 of 3 retained cases, including all 1 events'}).waitFor();
  await page.locator('#owgLogCases summary').filter({hasText:'Case 2 · 1 events'}).waitFor();
  assert((await page.locator('#owgLogCases').textContent()).includes('case_key_3'));
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);body='\\n'.join(c.iterdump())
assert 'JOIN_NATIVE_' not in body and 'JOIN_EXTRA_SECRET' not in body and 'JOIN_SOURCE_' not in body
assert c.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==5
assert c.execute("SELECT COUNT(*) FROM process_log_events WHERE origin='source_2'").fetchone()[0]==3
assert c.execute("SELECT COUNT(*) FROM process_log_cases WHERE case_key_alias IS NOT NULL").fetchone()[0]==3
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});assert.equal(disk.status,0,disk.stderr);
  await page.setViewportSize({width:1280,height:900});page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Delete '+id,exact:true}).click();await page.getByText('No retained local logs.',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Shared-case browser acceptance passed: two reviewed exports, browser-minimized keys and unused fields, no automatic upload/retention, exact shared case/source history and full-versus-variant activity support, pending retention withdrawal, saved alias stability, whole-case filtering, deletion, mobile and no canonical evidence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
