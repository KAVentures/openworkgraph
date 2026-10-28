const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-local-decisions-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const model={goal:'Review a purchase',baseline_source:'user_declared',baseline_path:['Review','Accept'],activities:[{id:'choose',label:'Review',kind:'decision'},{id:'accept',label:'Accept'},{id:'escalate',label:'Escalate'}],transitions:[{source:'choose',target:'accept',rule_id:'small'},{source:'choose',target:'escalate',rule_id:'large'}],entry_activity_id:'choose',terminal_activity_ids:['accept','escalate'],business_rules:[{id:'small',text:'Small purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'lt',expected:100}]}},{id:'large',text:'Large purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'gte',expected:100}]}}]};

  // Deliberately overlapping rules on independently supplied paths.
  model.business_rules[0].predicate.conditions=[{path:'/first',operator:'eq',expected:true}];
  model.business_rules[1].predicate.conditions=[{path:'/second',operator:'eq',expected:true}];
  const saved=await page.evaluate(async model=>{await window.__owgAuthReady;const r=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Local purchase',process_model:model,user_confirmed:true})});if(!r.ok)throw new Error(await r.text());return r.json();},model);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();await page.getByRole('button',{name:'Local purchase · r1',exact:true}).click();
  await page.getByRole('button',{name:'Add decision table',exact:true}).click();
  const policy=page.getByLabel('Hit policy decision_table_1',{exact:true});assert.equal(await policy.inputValue(),'UNIQUE');
  await page.getByLabel('Effective start decision_table_1',{exact:true}).fill('2026-01-01');
  await page.getByLabel('Source document label decision_table_1',{exact:true}).fill('Ordered policy <script>literal</script>');
  await page.getByLabel('Source document SHA256 decision_table_1',{exact:true}).fill('a'.repeat(64));
  const oldConfirm=page.getByLabel('I confirm this exact table, referenced rules, source basis and effective dates decision_table_1',{exact:true});await oldConfirm.check();
  const save=async revision=>{await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision '+revision+'.'}).waitFor();};
  await save(2);
  const cases={as_of_date:'2026-10-28',cases:[{case_id:'case_1',facts:{first:true,second:true,PRIVATE:'PRIVATE_FACT'}},{case_id:'case_2',facts:{second:true}},{case_id:'case_3',facts:{first:true}},{case_id:'case_4',facts:{first:false,second:false}}]};
  const run=async()=>{await page.getByLabel('Choose authorized case-facts JSON',{exact:true}).setInputFiles({name:'cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(cases))});await page.getByLabel('I authorize local analysis of the case facts in this selected file.',{exact:true}).check();await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).click();await page.locator('#owgCatalogRuleCheckResult').getByText('4 supplied case-fact reports',{exact:true}).waitFor();};
  const artifact=async()=>{const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download minimized rule-check report',exact:true}).click()]);return JSON.parse(await fs.readFile(await download.path(),'utf8'));};
  await run();assert.equal((await artifact()).cases[0].decision_tables[0].status,'overlap');
  await oldConfirm.check();await page.locator('#owgCatalogReviewed').check();await policy.selectOption('FIRST');
  const confirm=page.getByLabel('I confirm this exact table, referenced rules, source basis and effective dates, and declared row precedence decision_table_1',{exact:true});
  assert.equal(await confirm.isChecked(),false);assert.equal(await page.locator('#owgCatalogReviewed').isChecked(),false);assert.equal(await page.locator('#owgCatalogRuleCheckResult').textContent(),'');
  await confirm.check();await save(3);await run();
  let report=await artifact(),checks=report.cases.map(c=>c.decision_tables[0]);
  assert.deepEqual(checks.map(c=>c.status),['first_match','unknown','first_match','gap']);assert.equal(checks[0].selected_row_id,'row_1');assert.equal(checks[1].blocking_row_id,'row_1');
  assert.equal(report.facts_retained,false);assert.ok(!JSON.stringify(report).includes('PRIVATE'));
  await confirm.check();await page.locator('#owgCatalogReviewed').check();
  const down=page.getByRole('button',{name:'Move row row_1 down in decision_table_1',exact:true});await down.focus();await page.keyboard.press('Enter');
  assert.equal(await confirm.isChecked(),false);assert.equal(await page.locator('#owgCatalogReviewed').isChecked(),false);
  assert.equal(await page.locator('[data-order-row]').first().getAttribute('data-order-row'),'row_2');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.orderRow),'row_1');
  assert.equal(await page.getByRole('button',{name:'Move row row_2 up in decision_table_1',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'Synchronize rows decision_table_1',exact:true}).click();assert.equal(await page.locator('[data-order-row]').first().getAttribute('data-order-row'),'row_2');
  await confirm.check();await save(4);await run();report=await artifact();checks=report.cases.map(c=>c.decision_tables[0]);
  assert.deepEqual(checks.map(c=>c.status),['first_match','first_match','unknown','gap']);assert.equal(checks[0].selected_row_id,'row_2');assert.equal(checks[2].blocking_row_id,'row_2');
  // A held old revision cannot restore a report after a precedence edit.
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/process-catalog/check-rules*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.getByLabel('Choose authorized case-facts JSON',{exact:true}).setInputFiles({name:'cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(cases))});await page.getByLabel('I authorize local analysis of the case facts in this selected file.',{exact:true}).check();await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).click();await held;
  await policy.selectOption('UNIQUE');const response=page.waitForResponse(r=>r.url().includes('/process-catalog/check-rules'));release();await response;assert.equal(await page.locator('#owgCatalogRuleCheckResult').textContent(),'');
  await policy.selectOption('FIRST');
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgCatalogEditor'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('FIRST local browser passed: UNIQUE overlap default, explicit precedence review, keyboard reorder, stable IDs and resync, earlier unknowns, exact saved revisions, minimized downloads, held-result invalidation, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
