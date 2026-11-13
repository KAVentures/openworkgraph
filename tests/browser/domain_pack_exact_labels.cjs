const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pack-labels-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  const unicodeRare='🔥'.repeat(150);
  let csv='case,activity,time\nc1,Rare submitted,2026-01-01T00:00:00Z\nc1,Rare approved,2026-01-02T00:00:00Z\nc1,'+unicodeRare+',2026-01-03T00:00:00Z\n';
  for(let i=0;i<1005;i++)csv+='c'+(i+2)+',Noise'+String(i).padStart(4,'0')+',2026-01-01T00:00:00Z\n';
  await page.locator('#owgEventLogFile').setInputFiles({name:'many.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  try{await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();}catch(e){throw Error('Import status: '+await page.locator('#owgEventLogStatus').textContent());}
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCaseType')?.value);
  await page.locator('#owgEventLogAnalyze').click();await page.locator('#owgDomainPackSetup').click();
  await page.locator('#owgDomainPackChoice').selectOption('expense_reimbursement');
  await page.locator('#owgDomainPackRole-claim').selectOption('case');
  for(const [role,value] of Object.entries({submitted:'Rare submitted',approved:'Rare approved',paid:unicodeRare})){
   const select=page.locator('#owgDomainPackRole-'+role);assert.equal(await select.locator('option').filter({hasText:value}).count(),0);
   await select.selectOption({label:'Enter exact label…'});await page.locator('#owgDomainPackExact-'+role).fill(value);
  }
  await page.locator('#owgDomainPackInstall').click();await page.locator('#owgDomainPackStatus').filter({hasText:'Installed 1.0.0.'}).waitFor();
  await page.locator('#owgDomainPackSetup').click();await page.waitForFunction(()=>!document.querySelector('#owgDomainPackSetup').disabled);
  await page.locator('#owgDomainPackChoice').selectOption('expense_reimbursement');
  for(const [role,value] of Object.entries({submitted:'Rare submitted',approved:'Rare approved',paid:unicodeRare})){
   const exact=page.locator('#owgDomainPackExact-'+role);assert.equal(await exact.isVisible(),true);assert.equal(await exact.inputValue(),value);
  }
  await page.locator('#owgDomainPackExact-paid').fill('Missing paid');await page.locator('#owgDomainPackInstall').click();
  await page.locator('#owgDomainPackStatus').filter({hasText:'not an activity of this log'}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDomainPacks'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
  assert.deepEqual(errors,[]);console.log('Rare exact domain-pack labels: mapped, retained after reload, invalid label refused, axe/mobile passed.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
