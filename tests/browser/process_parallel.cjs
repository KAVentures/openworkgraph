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
  assert(await page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).isEnabled());
  const direct=await fetch(base+'/v1/process-catalog/agent-project?'+new URLSearchParams({workflow_id:saved.workflow_id,revision:1}),{headers:{Authorization:'OWG-Session '+session}});
  assert.equal(direct.status,200);
  const [sdkDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).click()]);
  const sdkPath=path.join(scratch,'parallel-sdk.zip');await sdkDownload.saveAs(sdkPath);
  const sdkCheck=spawnSync(python,['-c',`import json,sys,zipfile
z=zipfile.ZipFile(sys.argv[1]);s=json.loads(z.read('workflow.spec.json'))
assert s['control_flow_contract']=='openworkgraph.parallel-node-firing-plan.v1'
assert s['execution_schedule']=='serial_token_valid_node_order' and not s['execution_authorization']
assert len([n for n in s['process_model']['activities'] if n['kind'].startswith('parallel_')])==2
assert json.loads(z.read('tool-bindings.example.json'))['enabled'] is False
`,sdkPath],{encoding:'utf8'});assert.equal(sdkCheck.status,0,sdkCheck.stdout+sdkCheck.stderr);
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
  const nestedXml=xml.replace('<task id="a2" name="A2"/>',
    '<parallelGateway id="innerFork" name="Inner checks" gatewayDirection="Diverging"/>'+
    '<task id="c1" name="C1"/><task id="d1" name="D1"/>'+
    '<parallelGateway id="innerJoin" name="Inner complete" gatewayDirection="Converging"/><task id="a2" name="A2"/>')
    .replace('<sequenceFlow id="f3" sourceRef="a1" targetRef="a2"/>',
      [['a1','innerFork'],['innerFork','c1'],['innerFork','d1'],['c1','innerJoin'],['d1','innerJoin'],['innerJoin','a2']]
        .map(([source,target],i)=>'<sequenceFlow id="nested'+i+'" sourceRef="'+source+'" targetRef="'+target+'"/>').join(''));
  await page.getByLabel('Choose BPMN process design',{exact:true}).setInputFiles({name:'nested.bpmn',mimeType:'application/bpmn+xml',buffer:Buffer.from(nestedXml)});
  const nestedPreview=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-catalog/import-bpmn');
  await page.getByRole('button',{name:'Preview selected BPMN',exact:true}).click();
  const nestedDraft=await(await nestedPreview).json();
  assert.equal(nestedDraft.process_model.activities.filter(n=>n.kind.startsWith('parallel_')).length,4);
  await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).click();
  await editor.getByLabel('Business goal',{exact:true}).fill('Complete all inner and outer checks');
  await page.locator('#owgCatalogReviewed').check();
  const nestedSaving=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/v1/process-catalog');
  await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  const nestedSaved=await(await nestedSaving).json();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1.'}).waitFor();
  const innerIndex=nestedDraft.process_model.activities.findIndex(n=>n.kind==='parallel_split'&&n.label==='Inner checks');
  assert(innerIndex>=0);
  await editor.getByRole('button',{name:'Select activity '+nestedDraft.process_model.activities[innerIndex].id,exact:true}).focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'owgCatalogActivity'+innerIndex);
  const [nestedBpmn]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Export saved BPMN',exact:true}).click()]);
  const nestedBpmnPath=path.join(scratch,'nested.bpmn');await nestedBpmn.saveAs(nestedBpmnPath);
  assert.equal(((await fs.readFile(nestedBpmnPath,'utf8')).match(/<bpmn:parallelGateway/g)||[]).length,4);
  const [nestedDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).click()]);
  const nestedZip=path.join(scratch,'nested-sdk.zip');await nestedDownload.saveAs(nestedZip);
  const nestedCheck=spawnSync(python,['-c',`import json,sys,zipfile
z=zipfile.ZipFile(sys.argv[1]);s=json.loads(z.read('workflow.spec.json'))
assert len([n for n in s['process_model']['activities'] if n['kind'].startswith('parallel_')])==4
assert not s['execution_authorization'] and not json.loads(z.read('tool-bindings.example.json'))['enabled']
z.extractall(sys.argv[2]);sys.path.insert(0,sys.argv[2])
from runtime import check_plan
for c in json.loads(z.read('synthetic-cases.json'))['cases']:
 assert check_plan(dict(revision=s['revision'],content_sha256=s['content_sha256'],activity_ids=c['activity_ids']),s)['all_produced_branch_tokens_consumed']
`,nestedZip,path.join(scratch,'nested-sdk')],{encoding:'utf8'});
  assert.equal(nestedCheck.status,0,nestedCheck.stderr);
  await page.getByRole('button',{name:'Compare next CSV with this saved revision',exact:true}).click();
  const nestedRows=['case_id,activity,timestamp'];
  for(const [id,activities] of [['PRIVATE_NESTED',['B1','A1','D1','B2','C1','A2','Close']],['PRIVATE_INNER_MISSING',['A1','C1','A2','B1','B2','Close']]])
    activities.forEach((activity,i)=>nestedRows.push(id+','+activity+',2026-10-20T00:0'+i+':00Z'));
  await page.getByLabel('Choose business event log CSV',{exact:true}).setInputFiles({name:'nested.csv',mimeType:'text/csv',buffer:Buffer.from(nestedRows.join('\n'))});
  const nestedAnalysis=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-mining/preview');
  await page.getByRole('button',{name:'Analyze selected CSV',exact:true}).click();
  const nestedCompared=await(await nestedAnalysis).json();
  assert.equal(nestedCompared.conformance.exact_structural_match_cases,1);
  assert.equal(nestedCompared.conformance.structurally_deviating_cases,1);
  assert.equal(nestedCompared.conformance.reference.content_sha256,nestedSaved.content_sha256);
  assert(!JSON.stringify(nestedCompared).includes('PRIVATE_'));
  await page.getByText('1 exact path matches · 1 differing cases',{exact:false}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.setViewportSize({width:1280,height:900});page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Delete all revisions',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Process revisions deleted locally.'}).waitFor();
  const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
` ,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);assert.deepEqual(errors,[]);
  console.log('Parallel browser acceptance passed: reviewed flat and nested paired gateways, keyboard/mobile, BPMN and SDK token-schedule archives with disabled bindings, accepted interleavings versus missing inner/outer branches, exact revisions and no canonical capture.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
