const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-bpmn-import-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',error=>{spawnError=error;});
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Owned BPMN fixture not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let imports=0,saves=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{const route=new URL(request.url()).pathname;
   if(request.method()==='POST'&&route==='/v1/process-catalog/import-bpmn')imports++;
   if(request.method()==='POST'&&route==='/v1/process-catalog')saves++;
  });
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();

  await page.getByText('Import a BPMN design as an unreviewed draft',{exact:true}).click();
  const xml=`<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p" isExecutable="false">
   <parallelGateway id="fork" name="Run checks" gatewayDirection="Diverging"/>
   <task id="a1" name="A1"/><task id="a2" name="A2"/><task id="b1" name="B1"/><task id="b2" name="B2"/>
   <parallelGateway id="join" name="All checks" gatewayDirection="Converging"/><task id="close" name="Close"/>
   <sequenceFlow id="f1" sourceRef="fork" targetRef="a1"/><sequenceFlow id="f2" sourceRef="fork" targetRef="b1"/>
   <sequenceFlow id="f3" sourceRef="a1" targetRef="a2"/><sequenceFlow id="f4" sourceRef="b1" targetRef="b2"/>
   <sequenceFlow id="f5" sourceRef="a2" targetRef="join"/><sequenceFlow id="f6" sourceRef="b2" targetRef="join"/>
   <sequenceFlow id="f7" sourceRef="join" targetRef="close"/></process></definitions>`;
  await page.getByLabel('Choose BPMN process design',{exact:true}).setInputFiles({name:'parallel.bpmn',mimeType:'application/bpmn+xml',buffer:Buffer.from(xml)});
  assert.equal(imports,0);assert.equal(saves,0);
  await page.getByRole('button',{name:'Preview selected BPMN',exact:true}).click();
  await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).waitFor();
  assert.equal(imports,1);assert.equal(saves,0);
  await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).click();
  const editor=page.locator('#owgCatalogEditor');
  assert.equal(await editor.getByLabel('Kind import_step_1',{exact:true}).inputValue(),'parallel_split');
  assert.equal(await editor.getByLabel('Kind import_step_6',{exact:true}).inputValue(),'parallel_join');
  assert.equal(await page.locator('#owgCatalogReviewed').isChecked(),false);
  assert((await editor.textContent()).includes('joins require every branch'));
  await editor.getByRole('button',{name:'Select activity import_step_1',exact:true}).focus();
  await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.id),'owgCatalogActivity0');
  await editor.getByLabel('Business goal',{exact:true}).fill('Complete both independent case checks');
  await page.locator('#owgCatalogReviewed').check();
  const saving=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/v1/process-catalog');
  await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  const saved=await(await saving).json();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1.'}).waitFor();assert.equal(saves,1);
  assert(await page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).isDisabled());
  const direct=await fetch(base+'/v1/process-catalog/agent-project?'+new URLSearchParams({workflow_id:saved.workflow_id,revision:1}),{headers:{Authorization:'OWG-Session '+session}});
  assert.equal(direct.status,422);assert((await direct.text()).includes('fork/join-capable'));
  const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'Export saved BPMN',exact:true}).click();
  const download=await downloading,artifact=path.join(scratch,'parallel.bpmn');await download.saveAs(artifact);
  const exported=await fs.readFile(artifact,'utf8');assert.equal((exported.match(/<bpmn:parallelGateway/g)||[]).length,2);
  assert(exported.includes('gatewayDirection="Converging"')&&exported.includes('isExecutable="false"'));
  await page.getByRole('button',{name:'Compare next CSV with this saved revision',exact:true}).click();
  const rows=['case_id,activity,timestamp'];
  for(const [number,sequence] of [['PRIVATE_PARALLEL_CASE',['B1','A1','B2','A2','Close']],['PRIVATE_MISSING_BRANCH',['A1','A2','Close']]])
   sequence.forEach((activity,index)=>rows.push(number+','+activity+',2026-10-20T00:0'+index+':00Z'));
  await page.getByLabel('Choose business event log CSV',{exact:true}).setInputFiles({name:'checks.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  const analyzing=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-mining/preview');
  await page.getByRole('button',{name:'Analyze selected CSV',exact:true}).click();
  const comparison=await(await analyzing).json();assert.equal(comparison.conformance.exact_structural_match_cases,1);
  assert.equal(comparison.conformance.structurally_deviating_cases,1);assert.equal(comparison.conformance.structural_comparison_unknown_cases,0);
  assert(!JSON.stringify(comparison).includes('PRIVATE_'));
  await page.getByText('1 exact path matches · 1 differing cases',{exact:false}).waitFor();
  assert.equal(comparison.conformance.reference.content_sha256,saved.content_sha256);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.setViewportSize({width:1280,height:900});page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Delete all revisions',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Process revisions deleted locally.'}).waitFor();
  const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
` ,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);assert.deepEqual(errors,[]);
  console.log('Parallel browser acceptance passed: explicit BPMN preview and paired gateways, reviewed save, keyboard/mobile, nonexecuting export, linear-runtime refusal, accepted interleaving versus missing branch, exact revision and no canonical capture.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
