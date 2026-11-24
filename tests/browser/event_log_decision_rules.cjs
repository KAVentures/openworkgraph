const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: find a decision point and learn the rule behind it from data recorded before the decision.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-decisions-'));
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
   // 80 invoices: after Check, amounts of 5000 or more go to Manager approval, the rest to Auto approve.
   const rows=['case,activity,time,who,amount'];
   for(let i=0;i<80;i++){const amount=(i*137)%10000;
     rows.push(`c${i},Receive,2024-03-01T08:00:00Z,mail,${amount}`,`c${i},Check,2024-03-01T09:00:00Z,ana,`,
       `c${i},${amount>=5000?'Manager approval':'Auto approve'},2024-03-01T10:00:00Z,bob,`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'invoices.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogAttributes').selectOption(['amount']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 240 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgDecisions');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgDecisionFind').click();
   const points=page.locator('#owgDecisionPoints');
   await points.locator('tbody tr').first().waitFor();
   assert.equal(await points.locator('tbody tr').count(),1);
   assert.equal((await points.locator('tbody tr td').first().textContent()),'Check');
   await points.getByRole('button',{name:'Learn rules after Check'}).click();
   const results=page.locator('#owgDecisionResults');
   await results.filter({hasText:'Held-out accuracy'}).waitFor();
   const text=await results.textContent();
   assert(/Held-out accuracy (9\d|100)%/.test(text)&&text.includes("for always predicting"),text);
   assert(text.includes('Most informative data: event.amount 100%'),text);
   const conditions=await results.locator('tbody tr td:first-child').allTextContents();
   assert(conditions.some(text=>/^event\.amount ≥ /.test(text)),conditions.join(' | '));
   assert(conditions.some(text=>/^\(event\.amount < .* or missing or nonnumeric\)$/.test(text)),conditions.join(' | '));
   const holdout=page.locator('#owgDecisionHoldout');
   await holdout.getByRole('heading',{name:'Held-out results by branch'}).waitFor();
   await holdout.getByRole('columnheader',{name:'Precision',exact:true}).waitFor();
   assert.equal(await holdout.locator('table').first().locator('tbody tr').count(),2);
   await holdout.getByText('Held-out branch mix-ups',{exact:true}).click();
   await page.locator('#owgDecisionAttributeScope').selectOption('events');
   assert.equal(await holdout.locator('table').count(),0);
   const [timedResponse]=await Promise.all([
     page.waitForResponse(response=>{
       const url=new URL(response.url());
       return url.pathname.endsWith('/decision-rules')&&url.searchParams.get('include_case_attributes')==='false';
     }),
     points.getByRole('button',{name:'Learn rules after Check'}).click()
   ]);
   const timed=await timedResponse.json();
   assert.equal(timedResponse.status(),200,JSON.stringify(timed));
   assert.equal(timed.includes_case_attributes,false);
   assert.ok(timed.features.every(feature=>feature.name.startsWith('event.')));
   await results.getByText('Inputs use only event attributes recorded at or before the decision.').waitFor();
   await holdout.getByRole('heading',{name:'Held-out results by branch'}).waitFor();
   const observed=page.locator('#owgDecisionObservedBranches'),cases=page.locator('#owgDecisionBranchCases');
   await points.getByRole('button',{name:'Review branches after Check'}).click();
   await observed.getByRole('button',{name:'Inspect cases continuing to Auto approve'}).click();
   await cases.getByRole('status').filter({hasText:'44 recorded occurrences in 44 cases'}).waitFor();
   await cases.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 3 of 3 events'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgDecisions'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   const completionRows=['case,activity,time,who,amount'];
   for(let i=0;i<80;i++){
     completionRows.push(`c${i},Check,2024-03-01T08:00:00Z,ana,${i%2}`);
     if(i%2)completionRows.push(`c${i},(end),2024-03-01T09:00:00Z,bob,`);
   }
   await page.locator('#owgEventLogFile').setInputFiles({name:'completion.csv',mimeType:'text/csv',buffer:Buffer.from(completionRows.join('\n'))});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
   await page.locator('#owgEventLogAttributes').selectOption(['amount']);
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 120 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgDecisionFind').click();
   await points.filter({hasText:'(case ended) · case completion 50%'}).waitFor();
   assert.match(await points.textContent(),/\(end\) 50%/);
   await points.getByRole('button',{name:'Learn rules after Check'}).click();
   await results.getByRole('cell',{name:'(case ended) · case completion',exact:true}).waitFor();
   await results.getByRole('cell',{name:'(end)',exact:true}).waitFor();
   await points.getByRole('button',{name:'Review branches after Check'}).click();
   await observed.getByRole('button',{name:'Inspect cases ending here'}).click();
   await cases.getByRole('button',{name:'Open case c0',exact:true}).waitFor();
   assert.equal(await cases.getByRole('button',{name:'Open case c1',exact:true}).count(),0);
   await observed.getByRole('button',{name:'Inspect cases continuing to (end)'}).click();
   await cases.getByRole('button',{name:'Open case c1',exact:true}).waitFor();
   assert.equal(await cases.getByRole('button',{name:'Open case c0',exact:true}).count(),0);
   assert.deepEqual(errors,[]);
   console.log('Decision rules browser acceptance passed: learned rules and held-out accuracy, observed branches and case history, terminal and literal activities, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
