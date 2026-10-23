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
  const xml=`<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p" isExecutable="true">
   <startEvent id="s" name="Start"/><exclusiveGateway id="PRIVATE_XML_GATEWAY" name="选择"/>
   <userTask id="a" name="Accept"><documentation>PRIVATE_SOURCE_DOC &lt;script&gt;window.importScriptRan=true&lt;/script&gt;</documentation></userTask>
   <task id="b" name="Reject"/><exclusiveGateway id="j" name="Merge"/><endEvent id="e" name="End"/>
   <sequenceFlow id="f1" sourceRef="s" targetRef="PRIVATE_XML_GATEWAY"/>
   <sequenceFlow id="f2" sourceRef="PRIVATE_XML_GATEWAY" targetRef="a"><conditionExpression>Evidence accepted</conditionExpression></sequenceFlow>
   <sequenceFlow id="f3" sourceRef="PRIVATE_XML_GATEWAY" targetRef="b"><conditionExpression>Evidence rejected</conditionExpression></sequenceFlow>
   <sequenceFlow id="f4" sourceRef="a" targetRef="j"/><sequenceFlow id="f5" sourceRef="b" targetRef="j"/>
   <sequenceFlow id="f6" sourceRef="j" targetRef="e"/></process></definitions>`;
  await page.getByLabel('Choose BPMN process design',{exact:true}).setInputFiles({name:'owned.bpmn',mimeType:'application/bpmn+xml',buffer:Buffer.from(xml)});
  assert.equal(imports,0);assert.equal(saves,0);
  await page.getByRole('button',{name:'Preview selected BPMN',exact:true}).click();
  await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).waitFor();
  assert.equal(imports,1);assert.equal(saves,0);
  const preview=page.locator('#owgBPMNImportResult');assert.match(await preview.textContent(),/6 nodes · 6 transitions · 2 unconfirmed rules/);
  await preview.getByRole('button',{name:'Fit graph',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.importScriptRan),undefined);
  await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).click();
  const editor=page.locator('#owgCatalogEditor');
  assert.match(await editor.textContent(),/Imported declared design · no observed baseline/);
  assert.equal(await editor.getByLabel('Kind import_step_5',{exact:true}).inputValue(),'routing');
  assert.equal(await page.locator('#owgCatalogReviewed').isChecked(),false);assert.equal(saves,0);
  await editor.getByLabel('Activity import_step_2',{exact:true}).fill('Choose outcome');
  await editor.getByLabel('Business goal',{exact:true}).fill('Review an accepted or rejected case');
  await page.locator('#owgCatalogReviewed').check();
  await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1.'}).waitFor();assert.equal(saves,1);
  const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export saved BPMN',exact:true}).click();
  const artifact=await downloaded,exportPath=path.join(scratch,'saved.bpmn');await artifact.saveAs(exportPath);
  const savedXML=await fs.readFile(exportPath,'utf8');
  assert(savedXML.includes('exclusiveGateway'));assert(savedXML.includes('isExecutable="false"'));
  assert(!savedXML.includes('PRIVATE_XML_GATEWAY')&&!savedXML.includes('PRIVATE_SOURCE_DOC'));
  // Declare a typed condition, confirm the changed rule, then separately save
  // a new immutable version before sending any authorized case facts.
  const predicate=editor.locator('.owg-rule-predicate').filter({hasText:'Structured conditions · import_rule_2'});
  await predicate.locator('summary').click();
  await predicate.getByLabel('Predicate field import_rule_2',{exact:true}).fill('/approval/status');
  await predicate.getByLabel('Predicate expected value import_rule_2',{exact:true}).fill('"approved"');
  await predicate.getByRole('button',{name:'Add declared condition for import_rule_2',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogReviewed').isChecked(),false);
  await predicate.locator('..').getByLabel('User-confirmed rule',{exact:true}).check();
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 2.'}).waitFor();assert.equal(saves,2);
  const route=['import_step_1','import_step_2','import_step_3','import_step_5','import_step_6'];
  const facts={cases:[{case_id:'case_1',facts:{approval:{status:'approved'},unused:'PRIVATE_RAW_CASE_VALUE'},activity_ids:route},
    {case_id:'case_2',facts:{approval:{status:'rejected'}},activity_ids:route},{case_id:'case_3',facts:{},activity_ids:route}]};
  let factRequests=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/v1/process-catalog/check-rules')factRequests++;});
  await page.getByLabel('Choose authorized case-facts JSON',{exact:true}).setInputFiles({name:'owned-facts.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(facts))});
  assert.equal(factRequests,0);assert(await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).isDisabled());
  await page.getByLabel('I authorize local analysis of the case facts in this selected file.',{exact:true}).check();assert.equal(factRequests,0);
  const checked=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-catalog/check-rules');
  await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).click();const report=await (await checked).json();
  assert.equal(report.process_reference.revision,2);assert.equal(report.rule_result_counts.matched,1);
  assert.equal(report.rule_result_counts.not_matched,1);assert.equal(report.rule_result_counts.unknown,1);
  assert.equal(report.business_policy_compliance_verified,false);assert.equal(report.execution_permissions_granted,false);
  assert.equal(report.cases[0].manual_review_condition_count_on_proposed_route,1);
  assert(!JSON.stringify(report).includes('PRIVATE_')&&!JSON.stringify(report).includes('approved'));
  await page.locator('#owgCatalogRuleCheckResult').getByText('3 supplied case-fact reports',{exact:true}).waitFor();
  const minimized=page.waitForEvent('download');await page.getByRole('button',{name:'Download minimized rule-check report',exact:true}).click();
  const reportDownload=await minimized,reportPath=path.join(scratch,'rule-check.json');await reportDownload.saveAs(reportPath);
  assert(!(await fs.readFile(reportPath,'utf8')).includes('PRIVATE_RAW_CASE_VALUE'));
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.getByLabel('Choose BPMN process design',{exact:true}).setInputFiles({name:'unsupported.bpmn',mimeType:'application/bpmn+xml',buffer:Buffer.from(xml.replace('<exclusiveGateway id="j"','<inclusiveGateway id="j"'))});
  assert.equal(await page.getByRole('button',{name:'Open imported draft in editor',exact:true}).count(),0);
  await page.getByRole('button',{name:'Preview selected BPMN',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'other process semantics cannot be flattened'}).waitFor();assert.equal(saves,2);
  const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);rows=c.execute('SELECT payload FROM workflow_knowledge').fetchall()
assert len(rows)==2
assert all('PRIVATE_XML_GATEWAY' not in row[0] and 'PRIVATE_SOURCE_DOC' not in row[0] and 'PRIVATE_RAW_CASE_VALUE' not in row[0] for row in rows)
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);assert.deepEqual(errors,[]);
  console.log('BPMN import browser acceptance passed: explicit preview, unreviewed branch/merge draft, inert annotations, no source IDs/notes in saved model, reviewed save, nonexecuting export, unsupported-semantics rejection and mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
