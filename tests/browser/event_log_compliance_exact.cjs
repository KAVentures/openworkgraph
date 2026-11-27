const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-compliance-exact-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error('Server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw Error('Server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  const rows=['case,activity,time'];
  for(let i=0;i<8;i++){rows.push(`c${i},Receive,2024-01-0${i+1}T08:00:00Z`);if(i<4)rows.push(`c${i},Approve,2024-01-0${i+1}T09:00:00Z`);}
  await page.locator('#owgEventLogFile').setInputFiles({name:'rules.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  const box=page.locator('#owgEventLogCompliance');await box.waitFor();
  // Remove choices to simulate activities outside the initial 500 suggestions.
  await page.locator('#owgComplianceA option[value=Receive]').evaluate(o=>o.remove());
  await page.locator('#owgComplianceB option[value=Approve]').evaluate(o=>o.remove());
  await page.locator('#owgComplianceAExact').fill('Rece');await page.locator('#owgComplianceAFind').click();
  await page.locator('#owgComplianceAMatches').selectOption('Receive');await page.locator('#owgComplianceAUse').click();
  await page.locator('#owgComplianceBExact').fill('Appr');await page.locator('#owgComplianceBFind').click();
  await page.locator('#owgComplianceBMatches').selectOption('Approve');await page.locator('#owgComplianceBUse').click();
  await page.locator('#owgComplianceTemplate').selectOption('response');await page.locator('#owgComplianceAdd').click();
  const [checked]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/compliance')&&r.request().method()==='POST'),page.locator('#owgComplianceRun').click()]);
  assert.deepEqual(checked.request().postDataJSON().rules,[{template:'response',a:'Receive',b:'Approve'}]);
  const out=page.locator('#owgComplianceResults');await out.getByRole('button',{name:/Save as signal/}).waitFor();
  const [signal]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/signals')&&r.request().method()==='POST'),out.getByRole('button',{name:/Save as signal/}).click()]);
  assert.deepEqual(signal.request().postDataJSON().when.rule,{template:'response',a:'Receive',b:'Approve'});
  await page.locator('#owgComplianceTemplate').selectOption('existence');
  assert.equal(await page.locator('#owgComplianceBExact').isVisible(),false);
  await page.locator('#owgComplianceMin').fill('2');await page.locator('#owgComplianceAdd').click();
  assert.equal(await out.getByRole('button',{name:/Save as signal/}).count(),0);
  let release,resolveStarted;const held=new Promise(r=>{release=r;}),started=new Promise(r=>{resolveStarted=r;});
  await page.route('**/compliance',async route=>{const response=await route.fetch();resolveStarted();await held;await route.fulfill({response});},{times:1});
  await page.locator('#owgComplianceRun').click();await started;
  await page.locator('#owgComplianceRules').getByRole('button').last().click();
  const completed=page.waitForResponse(r=>r.url().endsWith('/compliance'));release();await completed;
  assert.equal(await out.getByRole('button',{name:/Save as signal/}).count(),0);
  await page.locator('#owgComplianceRun').click();await out.getByRole('button',{name:/Save as signal/}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogCompliance'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Compliance exact labels and stale-result browser acceptance passed.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
