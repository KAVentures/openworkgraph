const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: from a root-cause association, list its slowest cases, explain one, tag them and save them as a log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-rccases-'));
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
   // Twelve cases: the six that Bob approves take three days, the rest take an hour.
   const rows=['case,activity,time,who'];
   for(let i=0;i<12;i++){
     const day=String(1+i).padStart(2,'0'),slow=i%2===0,who=slow?'bob':'ana';
     rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z,clerk`);
     rows.push(`c${i},Approve,2024-03-${day}T08:30:00Z,${who}`);
     rows.push(`c${i},Pay,${slow?`2024-03-${String(4+i).padStart(2,'0')}T${String(8+i).padStart(2,'0')}:00:00Z`:`2024-03-${day}T09:00:00Z`},bot`);
   }
   await page.locator('#owgEventLogFile').setInputFiles({name:'approvals.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 36 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogRootCauseRun').waitFor();await page.locator('#owgEventLogRootCauseRun').scrollIntoViewIfNeeded();
   await page.locator('#owgEventLogOutcome').selectOption('duration_over');
   await page.locator('#owgEventLogOutcomeHours').fill('24');
   const results=page.locator('#owgEventLogRootCauseResults');
   let release,startedResolve;const held=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{startedResolve=resolve;});
   await page.route('**/root-cause?*',async route=>{const response=await route.fetch();startedResolve();await held;await route.fulfill({response});},{times:1});
   await page.locator('#owgEventLogRootCauseRun').click();await started;
   await page.locator('#owgEventLogOutcomeHours').fill('25');
   const completed=page.waitForResponse(r=>r.url().includes('/root-cause')&&r.status()===200);
   release();await completed;
   assert.equal(await results.getByRole('button',{name:/Tag the cases/}).count(),0);
   await page.locator('#owgEventLogOutcomeHours').fill('24');
   await page.locator('#owgEventLogRootCauseRun').click();
   await results.getByRole('button',{name:'Slowest cases with resource: bob'}).waitFor();
   await page.locator('#owgEventLogOutcomeHours').fill('25');
   assert.equal(await results.getByRole('button',{name:/Tag the cases|Save the cases/}).count(),0);
   await page.locator('#owgEventLogOutcomeHours').fill('24');
   await page.locator('#owgEventLogRootCauseRun').click();
   await results.getByRole('button',{name:'Slowest cases with resource: bob'}).waitFor();
   let releaseCases,startedCases;const holdCases=new Promise(resolve=>{releaseCases=resolve;}),caseRequest=new Promise(resolve=>{startedCases=resolve;});
   await page.route('**/cases/slowest',async route=>{const response=await route.fetch();startedCases();await holdCases;await route.fulfill({response});},{times:1});
   await results.getByRole('button',{name:'Slowest cases with resource: bob'}).click();await caseRequest;
   await page.locator('#owgEventLogOutcomeHours').fill('25');
   const casesDone=page.waitForResponse(r=>r.url().endsWith('/cases/slowest'));releaseCases();await casesDone;
   assert.equal(await results.getByText(/Slowest of the 6 cases/).count(),0);
   await page.locator('#owgEventLogOutcomeHours').fill('24');await page.locator('#owgEventLogRootCauseRun').click();
   await results.getByRole('button',{name:'Slowest cases with resource: bob'}).click();
   const panel=page.locator('#owgEventLogRootCauseCases');
   await panel.filter({hasText:'Slowest of the 6 cases with resource: bob'}).waitFor();
   const caseButtons=panel.getByRole('button',{name:/^Explain case c\d+$/});
   assert.equal(await caseButtons.count(),6);
   const names=await caseButtons.evaluateAll(bs=>bs.map(b=>b.getAttribute('aria-label').replace('Explain case ','')));
   assert(names.every(n=>Number(n.slice(1))%2===0),'only Bob’s cases: '+names);
   assert.equal(names[0],'c10','the slowest first');
   // Choosing a case explains it in the Explain a case section.
   await caseButtons.first().click();
   await page.locator('#owgCaseExplainResult').filter({hasText:'Case c10 took'}).waitFor();
   assert.equal(await page.locator('#owgCaseExplainId').inputValue(),'c10');
   // Tag the six cases.
   page.once('dialog',d=>d.accept('slow approvals'));
   await results.getByRole('button',{name:'Tag the cases with resource: bob'}).click();
   await panel.filter({hasText:'Tagged 6 cases “slow approvals”.'}).waitFor();
   const tags=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='approvals').log_id;return (await fetch('/v1/event-logs/'+id+'/case-tags')).json();});
   assert.deepEqual(tags.tags.map(t=>[t.tag,t.cases,t.cases_with_note]),[['slow approvals',6,6]]);
   // Save them as a new log; the source is unchanged.
   page.once('dialog',d=>d.accept('Bob approvals'));
   await results.getByRole('button',{name:'Save the cases with resource: bob as a new log'}).click();
   await panel.filter({hasText:'Saved 18 events of 6 cases as “Bob approvals”. This log is unchanged.'}).waitFor();
   const all=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>[l.name,l.events]));
   assert.deepEqual(all.sort(),[['Bob approvals',18],['approvals',36]]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogRootCauseResults'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Root-cause case actions browser acceptance passed: slowest cases of an association, explain, tag and save as a log, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
