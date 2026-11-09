const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-domain-packs-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 // The same deterministic order-to-cash log and mapping the pytest suite checks by hand.
 const fixture=JSON.parse(execFileSync(python,['-c','import json,sys;sys.path.insert(0,"tests");from order_to_cash_fixture import order_to_cash,MAPPING;print(json.dumps([order_to_cash(),MAPPING]))'],{encoding:'utf8'}));
 const [doc,mapping]=fixture;
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  await page.locator('#owgEventLogFile').setInputFiles({name:'o2c.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  await page.locator('#owgEventLogCaseType').selectOption(mapping.object_types.order);await page.locator('#owgEventLogAnalyze').click();
  const packs=page.locator('#owgDomainPacks');await packs.waitFor();
  await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Not installed on this log.'}).waitFor();
  // The catalog offers the packs; switching shows their roles and restores this one.
  assert.ok((await page.locator('#owgDomainPackChoice option').allTextContents()).includes('Order to cash'));
  await page.locator('#owgDomainPackChoice').selectOption('procure_to_pay');
  await page.locator('#owgDomainPackRole-purchase_order').waitFor();
  assert.equal(await page.locator('#owgDomainPackRole-order').count(),0);
  await page.locator('#owgDomainPackChoice').selectOption('order_to_cash');
  await page.locator('#owgDomainPackRole-order').waitFor();
  // No log name equals a role label here, so nothing is preselected; map every role explicitly.
  assert.equal(await page.locator('#owgDomainPackRole-order').inputValue(),'');
  for(const kind of ['object_types','activities'])for(const [role,name] of Object.entries(mapping[kind]))
   await page.locator('#owgDomainPackRole-'+role).selectOption(name);
  // Activities of every object type are offered, not only the selected case notion's.
  assert.ok((await page.locator('#owgDomainPackRole-receive_payment option').allTextContents()).includes(mapping.activities.receive_payment));
  const [installed]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/domain-packs/order_to_cash')&&r.request().method()==='POST'),page.locator('#owgDomainPackInstall').click()]);
  assert.equal(installed.status(),200,await installed.text());
  const sent=JSON.parse(installed.request().postData());
  assert.deepEqual(sent.mapping,mapping);assert.deepEqual(sent.parameters,{invoice_within_days:14,payment_terms_days:30});
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0. 4 case metrics were added'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  const report=page.locator('#owgDomainPackReport');
  await report.getByText('115 of 118 objects fit the reference lifecycles; 127 of 127 events match its arcs.',{exact:true}).waitFor();
  const text=await report.textContent();
  assert.match(text,/o2c_order_to_invoice/);assert.match(text,/every Post Goods Issue is preceded by Pick/);
  assert.match(text,/not validated against any particular source system/);
  assert.equal(sent.expected_installation,null);
  const ownerHeaders={Authorization:'OWG-Session '+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim()};
  const logId=await page.locator('#owgEventLogPick').inputValue();
  const endpoint=base+'/v1/event-logs/'+encodeURIComponent(logId)+'/domain-packs/order_to_cash';
  const listing=await page.request.get(endpoint.slice(0,endpoint.lastIndexOf('/')),{headers:ownerHeaders});
  const original=(await listing.json()).installs.find(p=>p.pack_id==='order_to_cash');
  // A second authenticated editor saves while the first dashboard retains its old basis.
  const external=await page.request.post(endpoint,{headers:ownerHeaders,data:{
    version:'1.0.0',mapping,parameters:{payment_terms_days:21},expected_installation:original.installation_token}});
  assert.equal(external.status(),200,await external.text());
  const externalRecord=await external.json();
  await page.locator('#owgDomainPackParam-payment_terms_days').fill('45');
  const [conflict]=await Promise.all([
    page.waitForResponse(r=>r.url()===endpoint&&r.request().method()==='POST'),
    page.locator('#owgDomainPackInstall').click()]);
  assert.equal(conflict.status(),409,await conflict.text());
  await page.locator('#owgDomainPackStatus').filter({hasText:'reload setup'}).waitFor();
  const afterConflict=await page.request.get(endpoint.slice(0,endpoint.lastIndexOf('/')),{headers:ownerHeaders});
  const retained=(await afterConflict.json()).installs.find(p=>p.pack_id==='order_to_cash');
  assert.equal(retained.installation_token,externalRecord.installation_token);
  assert.equal(retained.parameters.payment_terms_days,21);
  // Reload adopts the actual saved basis; another deliberate edit can then save.
  await page.locator('#owgDomainPackSetup').click();
  await page.waitForFunction(()=>document.querySelector('#owgDomainPackParam-payment_terms_days')?.value==='21');
  await page.locator('#owgDomainPackParam-payment_terms_days').fill('45');
  const [saved]=await Promise.all([
    page.waitForResponse(r=>r.url()===endpoint&&r.request().method()==='POST'),
    page.locator('#owgDomainPackInstall').click()]);
  assert.equal(saved.status(),200,await saved.text());
  assert.equal(JSON.parse(saved.request().postData()).expected_installation,externalRecord.installation_token);
  await page.locator('#owgDomainPackRun').click();
  await report.getByText('every VF01 Billing is followed by Clear Payment within 3888000 s',{exact:true}).waitFor();
  // An incomplete server projection must not downgrade an installed save to unconditional.
  await page.route('**/domain-packs',async route=>{
    const upstream=await route.fetch(),body=await upstream.json();
    for(const entry of body.installs)delete entry.installation_token;
    await route.fulfill({response:upstream,json:body});
  },{times:1});
  const incompleteSetup=page.waitForResponse(r=>r.url()===endpoint.slice(0,endpoint.lastIndexOf('/'))&&r.request().method()==='GET');
  await page.locator('#owgDomainPackSetup').click();
  await (await incompleteSetup).finished();
  await page.waitForFunction(()=>!document.querySelector('#owgDomainPackSetup').disabled);
  let unexpectedPosts=0;
  const countPost=request=>{if(request.url()===endpoint&&request.method()==='POST')unexpectedPosts++;};
  page.on('request',countPost);
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installation review is unavailable'}).waitFor();
  page.off('request',countPost);assert.equal(unexpectedPosts,0);
  await page.locator('#owgDomainPackSetup').click();
  await page.waitForFunction(()=>!document.querySelector('#owgDomainPackSetup').disabled);
  const beforeRemove=await page.request.get(endpoint.slice(0,endpoint.lastIndexOf('/')),{headers:ownerHeaders});
  const removeBasis=(await beforeRemove.json()).installs.find(p=>p.pack_id==='order_to_cash');
  const changedAgain=await page.request.post(endpoint,{headers:ownerHeaders,data:{
    version:'1.0.0',mapping,parameters:{payment_terms_days:22},expected_installation:removeBasis.installation_token}});
  assert.equal(changedAgain.status(),200,await changedAgain.text());
  const changedRecord=await changedAgain.json();
  const [staleDelete]=await Promise.all([
    page.waitForResponse(r=>r.url().startsWith(endpoint+'?')&&r.request().method()==='DELETE'),
    page.locator('#owgDomainPackUninstall').click()]);
  assert.equal(staleDelete.status(),409,await staleDelete.text());
  await page.locator('#owgDomainPackStatus').filter({hasText:'reload setup before removing'}).waitFor();
  await page.locator('#owgDomainPackSetup').click();
  await page.waitForFunction(()=>document.querySelector('#owgDomainPackParam-payment_terms_days')?.value==='22');
  const [removed]=await Promise.all([
    page.waitForResponse(r=>r.url().startsWith(endpoint+'?')&&r.request().method()==='DELETE'),
    page.locator('#owgDomainPackUninstall').click()]);
  assert.equal(removed.status(),200,await removed.text());
  assert.equal(new URL(removed.url()).searchParams.get('expected_installation'),changedRecord.installation_token);
  await page.locator('#owgDomainPackStatus').filter({hasText:'Uninstalled 1.0.0;'}).waitFor();
  assert.equal(await page.locator('#owgDomainPackRun').isDisabled(),true);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDomainPacks'),{
    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
  assert.deepEqual(errors,[]);
  console.log('Conditional domain-pack save browser passed: stale saves/deletes refused, exact state preserved, reload/save/remove, missing metadata, axe/mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
