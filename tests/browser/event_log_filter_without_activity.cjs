const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: keep only cases that never do an activity.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-without-activity-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // Ten cases A > B; four of them (c0-c3) also Escalate.
   const rows=['case,activity,time'];
   for(let i=0;i<10;i++)['A',...(i<4?['Escalate']:[]),'B'].forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T0${j}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'escalations.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   let s=await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   assert.equal(s.body.cases,10);
   const box=page.locator('#owgCaseFilter');await box.scrollIntoViewIfNeeded();
   await page.locator('#owgCaseFilterWithoutActivity').fill('Escalate');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,6);assert.match(s.url,/[?&]without_activity=Escalate/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: never does "Escalate".'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterWithoutActivity').inputValue(),'Escalate','kept after re-analysis');
   await page.locator('#owgFilterSetName').fill('No escalation');await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "No escalation".'}).waitFor();
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());assert.equal(s.body.cases,10);
   const saved=page.locator('#owgFilterSetList');await saved.filter({hasText:'never does "Escalate"'}).waitFor();
   s=await summaryAfter(()=>saved.getByRole('button',{name:'Apply saved filter No escalation'}).click());
   assert.equal(s.body.cases,6);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: never does "Escalate".'}).waitFor();
   await page.locator('#owgCaseFilterWithoutActivity').fill(' ');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Enter an activity label of at most 200 characters for "never does".'}).waitFor();
   await page.locator('#owgCaseFilterWithoutActivity').fill('Escalate');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Without-activity filter browser acceptance passed: cases that never do an activity, kept, saved and re-applied, input check, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
