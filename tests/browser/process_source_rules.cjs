const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-source-rules-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_source_rules_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',error=>{spawnError=error;});
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Owned source-rule fixture not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  const calls=async()=>JSON.parse(await fs.readFile(path.join(scratch,'source-calls.json'),'utf8'));
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Owned current-source rules · r1',exact:true}).click();
  await page.getByRole('button',{name:'Load observed source record choices',exact:true}).click();
  const choices=page.getByLabel('Observed source record choices',{exact:true});await choices.locator('option[value="0"]').waitFor({state:'attached'});
  await choices.selectOption('0');assert.deepEqual(await calls(),[]);
  const ref=page.getByLabel('Source rule observed record reference',{exact:true});assert.equal(await ref.inputValue(),'owg:r:'+'a'.repeat(32));
  const run=page.getByRole('button',{name:'Refresh selected record and check rules',exact:true});
  const consent=page.getByLabel('I authorize refreshing this selected source record to check saved rules.',{exact:true});
  assert(await run.isDisabled());await consent.check();assert.deepEqual(await calls(),[]);
  const checked=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-catalog/check-current-source-rules');
  await run.click();const report=await (await checked).json();
  assert.equal(report.rule_result_counts.matched,1);assert.equal(report.facts_source_authoritativeness_verified,true);
  assert.equal(report.case_association_verified,false);assert.equal(report.historical_case_facts_verified,false);
  assert.equal(report.generated_agent_source_fact_guard_resolved,false);assert.equal(report.execution_permissions_granted,false);
  assert.equal(report.declared_manual_review_condition_count,1);assert.equal(report.cases[0].manual_review_conditions_evaluated,false);
  for(const value of ['PRIVATE_','in_1234567890ABCDEF','acct_1234567890ABCDEF','99999999','paid'])assert(!JSON.stringify(report).includes(value));
  assert.deepEqual(await calls(),['account','invoice']);
  await page.locator('#owgCatalogSourceRuleResult').getByRole('heading',{name:'Current-source conditions · saved revision 1',exact:true}).waitFor();
  const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'Download minimized current-source rule report',exact:true}).click();
  const download=await downloading,file=path.join(scratch,'minimized-current-source.json');await download.saveAs(file);
  assert.equal(JSON.parse(await fs.readFile(file,'utf8')).selected_record_reference,'owg:r:'+'a'.repeat(32));
  await choices.selectOption('1');assert.equal(await consent.isChecked(),false);assert(await run.isDisabled());
  assert.equal(await page.locator('#owgCatalogSourceRuleResult').textContent(),'');assert.deepEqual(await calls(),['account','invoice']);
  await consent.check();const checkedVoid=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-catalog/check-current-source-rules');
  await run.click();assert.equal((await (await checkedVoid).json()).rule_result_counts.not_matched,1);
  await page.locator('#owgCatalogStatus').filter({hasText:'Current-source rule check complete.'}).waitFor();
  const type=page.getByLabel('Source rule record type',{exact:true});await type.selectOption('stripe_refund');
  assert.equal(await consent.isChecked(),false);await consent.check();
  const wrongType=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-catalog/check-current-source-rules');
  await run.click();assert.equal((await (await wrongType).json()).rule_result_counts.unknown,1);
  assert.deepEqual(await calls(),['account','invoice','account','invoice']);
  await type.selectOption('stripe_invoice');await consent.check();
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/v1/process-catalog/check-current-source-rules?*',async route=>{await gate;await route.continue();});
  const pending=page.waitForRequest(request=>new URL(request.url()).pathname==='/v1/process-catalog/check-current-source-rules');
  await run.click();await pending;await page.getByRole('button',{name:'Clear current-source rule check',exact:true}).click();
  const late=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/process-catalog/check-current-source-rules');
  release();await late;await page.unroute('**/v1/process-catalog/check-current-source-rules?*');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#owgCatalogSourceRuleResult').textContent(),'');assert.equal(await consent.isChecked(),false);assert(await run.isDisabled());
  const disk=spawnSync(python,['-c',`import sqlite3,sys,json
c=sqlite3.connect(sys.argv[1]);original=json.load(open(sys.argv[2]))
assert [list(row) for row in c.execute('SELECT * FROM events ORDER BY id')]==original
rows=c.execute('SELECT payload FROM workflow_knowledge').fetchall();assert len(rows)==1
assert all('PRIVATE_' not in row[0] and 'in_1234567890ABCDEF' not in row[0] and '99999999' not in row[0] for row in rows)
`,path.join(scratch,'data','workflow_observer.db'),path.join(scratch,'original-events.json')],{encoding:'utf8'});
  assert.equal(disk.status,0,disk.stderr);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('Current-source rule browser acceptance passed: explicit consent, canonical/account proof, fresh matched and unmatched states, wrong-type no-read, private-value-free export, late-result invalidation, unchanged evidence/model and mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
