const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-participants-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>spawnError=e);for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Owned log server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }assert(ready,'Owned log server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let previews=0,saves=0;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{const url=new URL(r.url());if(url.pathname==='/v1/process-mining/preview')previews++;
   if(url.pathname==='/v1/process-mining/datasets'&&r.method()==='POST'){saves++;}});
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const source='Case;Step;Time;Person;Group;Notes\nPRIVATE_ONE;Open;2026-10-20T00:00:00Z;ALICE_SECRET;SALES_SECRET;UNSELECTED_SECRET\nPRIVATE_ONE;Review;2026-10-20T00:01:00Z;BOB_SECRET;OPS_SECRET;UNSELECTED_SECRET\nPRIVATE_ONE;Close;2026-10-20T00:02:00Z;ALICE_SECRET;SALES_SECRET;UNSELECTED_SECRET\nPRIVATE_TWO;Open;2026-10-20T00:03:00Z;BOB_SECRET;SALES_SECRET;UNSELECTED_SECRET\nPRIVATE_TWO;Close;2026-10-20T00:04:00Z;BOB_SECRET;SALES_SECRET;UNSELECTED_SECRET\n';
  await page.getByLabel('Choose business event log CSV').setInputFiles({name:'owned.csv',mimeType:'text/csv',buffer:Buffer.from(source)});
  await page.getByText('Map columns from another event-log export',{exact:true}).click();
  await page.getByLabel('Event-log delimiter',{exact:true}).selectOption(';');
  await page.getByRole('button',{name:'Inspect columns on this device',exact:true}).click();
  await page.locator('#owgCSVMappingStatus').filter({hasText:'Inspected 5 event rows and 6 columns'}).waitFor();
  for(const [label,index] of [['Case identifier column','0'],['Activity label column','1'],['Event timestamp column','2'],
   ['Participant identifier column (optional)','3'],['Team identifier column (optional)','4']])await page.getByLabel(label,{exact:true}).selectOption(index);
  assert.equal(previews,0);assert.equal(saves,0);
  assert(await page.getByRole('button',{name:'Use mapped columns for next analysis',exact:true}).isDisabled());
  await page.locator('#owgCSVMapConsent').check();await page.getByRole('button',{name:'Use mapped columns for next analysis',exact:true}).click();
  assert.equal(previews,0);
  assert(!(await page.locator('body').textContent()).includes('ALICE_SECRET'));
  await page.getByRole('button',{name:'Show local participant/team lookup',exact:true}).click();
  assert((await page.locator('#owgCSVLocalLookup').textContent()).includes('Participant 1 · ALICE_SECRET'));
  await page.getByRole('button',{name:'Hide local participant/team lookup',exact:true}).click();
  assert(!(await page.locator('body').textContent()).includes('ALICE_SECRET'));
  assert.equal(previews,0);
  await page.getByRole('button',{name:'Analyze mapped log',exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Local analysis complete.'}).waitFor();
  const receipt=JSON.parse(await fs.readFile(path.join(scratch,'last-preview-shape.json'),'utf8'));
  assert.equal(receipt.header,'case_id,activity,timestamp,participant,team');assert.equal(receipt.unselected_test_markers_seen,false);
  assert.equal(previews,1);assert.equal(saves,0);
  await page.getByRole('heading',{name:'Participant and team labels',exact:true}).waitFor();
  await page.locator('#owgProcessResults details summary').filter({hasText:'Participant labels · 2 distinct · 2 adjacent changes'}).waitFor();
  assert(!(await page.locator('#owgProcessResults').textContent()).includes('ALICE_SECRET'));
  // Revoking future metadata transfer clears the preview while keeping the operator's draft.
  await page.getByRole('button',{name:'Design improved TO-BE workflow',exact:true}).click();
  await page.locator('#owgDesignerGoal').fill('Owned structural draft');
  await page.locator('#owgCSVMapConsent').uncheck();
  assert.equal(await page.locator('#owgProcessResults').textContent(),'');
  assert.equal(await page.locator('#owgCSVLocalLookup').count(),0);
  assert.equal(await page.locator('#owgDesignerGoal').inputValue(),'Owned structural draft');
  assert(await page.getByRole('button',{name:'Analyze mapped log',exact:true}).isDisabled());
  await page.locator('#owgCSVMapConsent').check();page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Use mapped columns for next analysis',exact:true}).click();
  await page.getByRole('button',{name:'Analyze mapped log',exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Local analysis complete.'}).waitFor();
  await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
  await page.getByLabel('I authorize retaining this selected event log locally.').check();
  await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Saved a minimized local snapshot.'}).waitFor();
  assert.equal(saves,1);
  const retainedReceipt=JSON.parse(await fs.readFile(path.join(scratch,'last-retention-shape.json'),'utf8'));
  assert.equal(retainedReceipt.header,'case_id,activity,timestamp,participant,team');assert.equal(retainedReceipt.unselected_test_markers_seen,false);
  const open=page.getByRole('button',{name:/^Open log_/});await open.waitFor();const id=(await open.textContent()).slice(5);
  await page.getByLabel('Cases with participant label',{exact:true}).selectOption('participant_2');
  await page.getByLabel('Cases with team label',{exact:true}).selectOption('team_2');
  await page.getByRole('button',{name:'Apply case filters',exact:true}).click();
  await page.locator('#owgCohortStatus').filter({hasText:'Selected 1 of 2 retained cases, including all 3 events'}).waitFor();
  const selected=page.locator('#owgLogCases details').filter({hasText:'Case 1 · 3 events'});await selected.locator('summary').click();
  await selected.locator('li').filter({hasText:'Participant 1'}).first().waitFor();
  assert.equal(await selected.locator('li').count(),3);assert((await selected.textContent()).includes('Participant 2'));
  await page.getByLabel('Cases with participant label',{exact:true}).selectOption('participant_1');
  await page.getByRole('button',{name:'Apply case filters',exact:true}).click();
  await page.locator('#owgCohortStatus').filter({hasText:'No retained cases match these filters'}).waitFor();
  await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
  await page.getByRole('button',{name:'List saved local logs',exact:true}).click();
  await page.getByRole('button',{name:'Open '+id,exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Opened retained local snapshot.'}).waitFor();
  assert.equal(await page.getByLabel('Cases with participant label',{exact:true}).locator('option').count(),3);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);body='\\n'.join(c.iterdump())
assert 'PRIVATE_' not in body and '_SECRET' not in body
assert c.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==5
assert c.execute("SELECT COUNT(*) FROM process_log_events WHERE participant = 'participant_2'").fetchone()[0]==3
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);
  await page.setViewportSize({width:1280,height:900});page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Delete '+id,exact:true}).click();
  await page.locator('#owgProcessStatus').filter({hasText:'Deleted local event-log snapshot.'}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Participant/team browser acceptance passed: explicit mapping and identity minimization before upload, consent clearing with draft preservation, no automatic retention, full-case label intersection, stable reload, mobile, deletion and unchanged canonical evidence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
