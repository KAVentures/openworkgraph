const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-zendesk-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','zendesk_process_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',error=>{spawnError=error;});
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Owned Zendesk source fixture not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let reads=0,saves=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{const route=new URL(request.url()).pathname;
   if(request.method()==='POST'&&route==='/v1/process-mining/zendesk-selected-cases')reads++;
   if(request.method()==='POST'&&route==='/v1/process-catalog')saves++;
  });
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByText('Read selected Zendesk ticket status audits',{exact:true}).click();
  assert.equal(reads,0);assert.equal(saves,0);
  await page.getByLabel('Selected Zendesk ticket IDs (comma separated)',{exact:true}).fill('17, 19');
  assert(await page.getByRole('button',{name:'Read selected Zendesk audits',exact:true}).isDisabled());
  await page.getByLabel('I authorize reading the selected ticket audits from the configured Zendesk account.',{exact:true}).check();
  assert.equal(reads,0);
  const fetched=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-mining/zendesk-selected-cases');
  await page.getByRole('button',{name:'Read selected Zendesk audits',exact:true}).click();
  const response=await fetched,value=await response.json();
  assert.equal(response.headers()['cache-control'],'no-store');
  assert.equal(value.process.case_count,2);assert.equal(value.process.input_rows,7);
  assert.equal(value.process.source_case_identity_basis,'selected_ticket_id_verified_on_every_audit');
  assert.equal(value.business_outcomes_verified,false);assert(!JSON.stringify(value).includes('PRIVATE_'));
  await page.getByRole('button',{name:'Explore selected Zendesk status history',exact:true}).click();
  await page.locator('#owgProcessResults').getByText('Full supplied source projection',{exact:true}).waitFor();
  assert.equal(await page.locator('#owgLogSave').isDisabled(),true);
  await page.getByRole('button',{name:'Explore all observed transitions',exact:true}).click();
  await page.getByRole('button',{name:'Inspect observed activity Status Solved',exact:true}).click();
  assert.match(await page.locator('#owgObservedActivityDetails').textContent(),/2 recorded events across 2 cases/);
  await page.getByRole('button',{name:'Design improved TO-BE workflow',exact:true}).click();
  const editor=page.locator('#owgCatalogEditor');await editor.getByLabel('Business goal',{exact:true}).fill('Review Zendesk ticket status progression');
  assert.equal(saves,0);
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1.'}).waitFor();
  assert.equal(saves,1);assert.equal(reads,1);
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(scratch,'zendesk-read-receipt.json'),'utf8')),[{method:'GET',ticket_id:17},{method:'GET',ticket_id:19}]);
  const disk=spawnSync(python,['-c',`import json,sqlite3,sys
c=sqlite3.connect(sys.argv[1]);rows=c.execute('SELECT payload FROM workflow_knowledge').fetchall()
assert len(rows)==1 and 'PRIVATE_' not in rows[0][0] and 'owned-example' not in rows[0][0]
model=json.loads(rows[0][0])['process_model']
assert model['baseline_source']=='selected_source_audit' and model['baseline_coverage']=='partial_source_audit'
assert not model['execution_authorized'] and not model['outcomes_verified']
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.getByLabel('Selected Zendesk ticket IDs (comma separated)',{exact:true}).fill('17');
  assert.equal(await page.getByRole('button',{name:'Explore selected Zendesk status history',exact:true}).count(),0);
  assert.equal(reads,1);assert.deepEqual(errors,[]);
  console.log('Zendesk browser acceptance passed: explicit selection/consent, fixed selected GET audit requests, minimized status projection, shared exploration, no source retention, separate reviewed design save, privacy and mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
