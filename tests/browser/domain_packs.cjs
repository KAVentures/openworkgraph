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
  const titles=await page.locator('#owgDomainPackChoice option').allTextContents();
  const established=['Customer onboarding and activation','Employee offboarding and access revocation',
    'Expense reimbursement','Order to cash','Procure to pay','Sales lead qualification','IT service management'];
  assert.deepEqual(titles.filter(title=>established.includes(title)),established);
  assert.ok(titles.includes('Employee onboarding and access readiness'));
  assert.ok(titles.includes('Returns and refunds'));
  await page.locator('#owgDomainPackChoice').selectOption('employee_onboarding');
  await page.locator('#owgDomainPackRole-onboarding_case').waitFor();
  await page.locator('#owgDomainPackRole-identity_provisioned').waitFor();
  await page.locator('#owgDomainPackRole-work_started').waitFor();
  assert.equal(await page.locator('#owgDomainPackRole-order').count(),0);
  assert.equal(new Set(titles).size,titles.length,'no duplicate domain-pack titles');
  await page.locator('#owgDomainPackChoice').selectOption('returns_refunds');
  await page.locator('#owgDomainPackRole-return_case').waitFor();
  assert.equal(await page.locator('#owgDomainPackRole-order').count(),0);
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
  // Stricter payment terms: reinstall with the new parameter, then the report counts more late payments.
  await page.locator('#owgDomainPackParam-payment_terms_days').fill('20');
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Reinstalled 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await report.getByRole('cell',{name:'every VF01 Billing is followed by Clear Payment within 1728000 s',exact:true}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDomainPacks'),{
   runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'no horizontal page scroll at 390 px');
  await page.locator('#owgDomainPackUninstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Uninstalled 1.0.0; 4 pack case metrics were removed.'}).waitFor();
  assert.equal(await page.locator('#owgDomainPackRun').isDisabled(),true);
  // The sales pack is usable through the same mapped CSV workflow.
  const salesCsv='lead,activity,time\nL1,Lead created,2026-01-01T00:00:00Z\nL1,Reviewed,2026-01-01T00:30:00Z\nL1,Qualified,2026-01-01T01:00:00Z\nL1,Opportunity created,2026-01-01T03:00:00Z\nL2,Lead created,2026-01-01T00:00:00Z\nL2,Qualified,2026-01-04T00:00:00Z\nL2,Opportunity created,2026-01-06T00:00:00Z\n';
  const beforeSales=await page.locator('#owgEventLogPick').inputValue();
  await page.locator('#owgEventLogFile').setInputFiles({name:'sales.csv',mimeType:'text/csv',buffer:Buffer.from(salesCsv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('lead');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  await page.waitForFunction(previous=>{
    const pick=document.querySelector('#owgEventLogPick'),type=document.querySelector('#owgEventLogCaseType');
    return pick&&pick.value&&pick.value!==previous&&type&&type.options.length>0&&type.value;
  },beforeSales);
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackChoice').selectOption('sales_lead_management');
  await page.locator('#owgDomainPackRole-lead').selectOption('case');
  for(const [role,name] of Object.entries({create_lead:'Lead created',qualify_lead:'Qualified',create_opportunity:'Opportunity created',review_lead:'Reviewed'}))
    await page.locator('#owgDomainPackRole-'+role).selectOption(name);
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await page.locator('#owgDomainPackReport').getByText('sales_lead_time_to_qualify',{exact:true}).waitFor();
  assert.match(await page.locator('#owgDomainPacks').textContent(),/Open, rejected, disqualified/);
  assert.match(await page.locator('#owgDomainPackReport').textContent(),/not validated against any particular source system/);
  // Customer onboarding is a real selectable pack in the existing local browser journey,
  // not just a backend catalog constant. Import its cases and map required/optional roles.
  const onboardingFixture=JSON.parse(execFileSync(python,['-c','import json,sys;sys.path.insert(0,"tests");from test_event_log_domain_pack_customer_onboarding import CSV,MAPPING;print(json.dumps([CSV,MAPPING]))'],{encoding:'utf8'}));
  const [onboardingCsv,onboardingMapping]=onboardingFixture;
  const beforeOnboarding=await page.locator('#owgEventLogPick').inputValue();
  await page.locator('#owgEventLogFile').setInputFiles({name:'onboarding.csv',mimeType:'text/csv',buffer:Buffer.from(onboardingCsv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();
  await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  await page.waitForFunction(previous=>{
    const pick=document.querySelector('#owgEventLogPick'),type=document.querySelector('#owgEventLogCaseType');
    return pick&&pick.value&&pick.value!==previous&&type&&type.options.length>0&&type.value;
  },beforeOnboarding);
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackChoice').selectOption('customer_onboarding');
  await page.locator('#owgDomainPackRole-onboarding_case').selectOption('case');
  for(const [role,name] of Object.entries(onboardingMapping.activities))
    await page.locator('#owgDomainPackRole-'+role).selectOption(name);
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await page.locator('#owgDomainPackReport').getByText('onboarding_open_to_provision',{exact:true}).waitFor();
  assert.match(await page.locator('#owgDomainPackReport').textContent(),/every Access provisioned is followed by Activated within 1209600 s/);
  assert.match(await page.locator('#owgDomainPackReport').textContent(),/not validated against any particular source system/);
  await page.locator('#owgDomainPackParam-provision_hours').fill('96');
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Reinstalled 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await page.locator('#owgDomainPackReport').getByText('every Opened is followed by Access provisioned within 345600 s',{exact:true}).waitFor();
  // Employee offboarding is a distinct installable process pack with explicit
  // uncertainty: a revocation event never proves an identity system was changed.
  const offboardingFixture=JSON.parse(execFileSync(python,['-c',
    'import json,sys;sys.path.insert(0,"tests");from test_event_log_domain_pack_employee_offboarding import CSV,MAPPING;print(json.dumps([CSV,MAPPING]))'],
    {encoding:'utf8'}));
  const [offboardingCsv,offboardingMapping]=offboardingFixture;
  await page.locator('#owgEventLogFile').setInputFiles({
    name:'offboarding.csv',mimeType:'text/csv',buffer:Buffer.from(offboardingCsv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();
  const priorLogId=await page.locator('#owgEventLogPick').inputValue();
  await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  // The status appears before refreshLogs selects the newly imported log.
  // Only analyse once selectLog has finished and populated the case notion.
  await page.waitForFunction(previous => {
    const pick=document.querySelector('#owgEventLogPick');
    const type=document.querySelector('#owgEventLogCaseType');
    return pick && pick.value && pick.value!==previous &&
      type && type.options.length>0 && type.value;
  },priorLogId);
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackChoice').selectOption('employee_offboarding');
  await page.locator('#owgDomainPackRole-offboarding_case').selectOption('case');
  for(const [role,activity] of Object.entries(offboardingMapping.activities))
    await page.locator('#owgDomainPackRole-'+role).selectOption(activity);
  assert.equal(await page.locator('#owgDomainPackParam-revocation_hours').inputValue(),'24');
  assert.equal(await page.locator('#owgDomainPackParam-closure_days').inputValue(),'7');
  const [offInstalled]=await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/domain-packs/employee_offboarding')&&r.request().method()==='POST'),
    page.locator('#owgDomainPackInstall').click()
  ]);
  assert.equal(offInstalled.status(),200,await offInstalled.text());
  assert.deepEqual(JSON.parse(offInstalled.request().postData()).mapping,offboardingMapping);
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0. 5 case metrics were added'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  const offReport=page.locator('#owgDomainPackReport');
  await offReport.getByText('offboarding_start_to_revocation',{exact:true}).waitFor();
  const offText=await offReport.textContent();
  assert.match(offText,/every Offboarding opened is followed by Access revocation logged within 86400 s/);
  assert.match(await page.locator('#owgDomainPackForm').textContent(),
    /does NOT prove that every identity provider/);
  assert.match(offText,/not validated against any particular source system/);
  assert.match(offText,/offboarding_revocation_events/);
  await page.locator('#owgDomainPackParam-revocation_hours').fill('72');
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Reinstalled 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await offReport.getByText('every Offboarding opened is followed by Access revocation logged within 259200 s',{exact:true}).waitFor();
  await page.locator('#owgDomainPackUninstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Uninstalled 1.0.0; 5 pack case metrics were removed.'}).waitFor();
  const auditOff=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDomainPacks'),{
    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(auditOff.violations.map(v=>v.id),[]);
  // Expense reimbursement reuses the actual owner-session import/mapping UI.
  const [expenseCsv,expenseMapping]=JSON.parse(execFileSync(python,['-c',
    'import json,sys;sys.path.insert(0,"tests");from test_event_log_domain_pack_expenses import CSV,MAPPING;print(json.dumps([CSV,MAPPING]))'],{encoding:'utf8'}));
  const beforeExpense=await page.locator('#owgEventLogPick').inputValue();
  await page.locator('#owgEventLogFile').setInputFiles({name:'expenses.csv',mimeType:'text/csv',buffer:Buffer.from(expenseCsv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');
  await page.locator('#owgEventLogActivity').selectOption('activity');
  await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();
  await page.locator('#owgEventLogImport').click();
  await page.waitForFunction(previous=>{
    const pick=document.querySelector('#owgEventLogPick'),type=document.querySelector('#owgEventLogCaseType');
    return pick&&pick.value&&pick.value!==previous&&type&&type.options.length>0&&type.value;
  },beforeExpense);
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackChoice').selectOption('expense_reimbursement');
  await page.locator('#owgDomainPackRole-claim').selectOption('case');
  for(const [role,activity] of Object.entries(expenseMapping.activities))
    await page.locator('#owgDomainPackRole-'+role).selectOption(activity);
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0. 5 case metrics were added'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  const expenseReport=page.locator('#owgDomainPackReport');
  await expenseReport.getByText('expense_approval_to_payment',{exact:true}).waitFor();
  await expenseReport.getByText('every Approved is followed by Paid within 1209600 s',{exact:true}).waitFor();
  assert.match(await page.locator('#owgDomainPackForm').textContent(),/does NOT prove bank settlement/);
  assert.match(await expenseReport.textContent(),/not validated against any particular source system/);
  await page.locator('#owgDomainPackParam-payment_days').fill('30');
  await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Reinstalled 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackRun').click();
  await expenseReport.getByText('every Approved is followed by Paid within 2592000 s',{exact:true}).waitFor();
  const expenseAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDomainPacks'),{
    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(expenseAudit.violations.map(v=>v.id),[]);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),
    'expense report has no page overflow at 390px');
  await page.locator('#owgDomainPackUninstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'Uninstalled 1.0.0; 5 pack case metrics were removed.'}).waitFor();
  assert.equal(await page.locator('#owgDomainPackRun').isDisabled(),true);
  assert.deepEqual(errors,[]);
  console.log('Domain pack browser passed: O2C, P2P, sales, customer onboarding, employee offboarding and expense reimbursement (version-pinned metrics, logged access revocation checks, parameter reconfiguration and uninstall), WCAG/mobile.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
