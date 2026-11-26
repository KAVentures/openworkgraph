const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: which activities happen in the same cases.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-cooccurrence-'));
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
   // 20 cases: 10 Approve (6 of them Ship), 10 Reject (4 of them Escalate); every case Opens and Closes.
   const rows=['case,activity,time,who'];
   for(let i=0;i<20;i++){const steps=['Open',...(i<10?['Approve',...(i<6?['Ship']:[])]:['Reject',...(i<14?['Escalate']:[])]),'Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T${String(8+j).padStart(2,'0')}:00:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 70 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgCooccurrence');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgCooccurrenceSize').selectOption('8');await page.locator('#owgCooccurrenceRun').click();
   await page.locator('#owgCooccurrenceStatus').filter({hasText:'6 activities over 20 cases. 2 pairs never occur in the same case.'}).waitFor();
   await box.getByText(/Showing 6 of 6 activities/).waitFor();
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgCooccurrenceReportJson').click()]);
   const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));
   assert.equal(report.activities_in_scope,6);
   assert.equal(report.qualifying_never_together_pairs,2);
   assert.equal(report.matrix.length,6);
   const header=await page.locator('#owgCooccurrenceMatrix thead th').allTextContents();
   assert.deepEqual(header,['When a case has … it also has','Close','Open','Approve','Reject','Ship','Escalate']);
   const ship=await page.locator('#owgCooccurrenceMatrix tbody tr').nth(4).locator('td').allTextContents();
   assert.deepEqual(ship,['Ship (6)','100%','100%','100%','0%','100%','0%'],ship.join('|'));
   const never=await page.locator('#owgCooccurrenceNever').textContent();
   assert(never.includes('Approve / Reject')&&never.includes('10 / 10'),never);
   const first=page.locator('#owgCooccurrenceFirst'),second=page.locator('#owgCooccurrenceSecond');
   const both=box.getByRole('button',{name:'Inspect cases with both'});
   const firstOnly=box.getByRole('button',{name:'Inspect cases with first only'});
   const secondOnly=box.getByRole('button',{name:'Inspect cases with second only'});
   const cases=page.locator('#owgEventLogCasesStatus');
   await first.selectOption('Approve');await second.selectOption('Ship');
   await box.getByText('6 cases with both, 4 with first only, 0 with second only.').waitFor();
   assert.equal(await secondOnly.isDisabled(),true);
   await both.click();
   await cases.getByText('6 matching cases. Showing 6 on this page.').waitFor();
   await page.locator('#owgEventLogCasesList').getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('cell',{name:'Ship',exact:true}).waitFor();
   await firstOnly.click();
   await cases.getByText('4 matching cases. Showing 4 on this page.').waitFor();
   await second.selectOption('Reject');
   assert.equal(await both.isDisabled(),true);
   await secondOnly.click();
   await cases.getByText('10 matching cases. Showing 10 on this page.').waitFor();
   await page.locator('#owgCaseFilterWithoutActivity').fill('Ship');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'14 of 20 cases'}).waitFor();
   await page.locator('#owgCooccurrenceRun').click();
   await first.selectOption('Approve');await second.selectOption('Reject');
   await firstOnly.click();
   await cases.getByText('4 matching cases (case filter applied). Showing 4 on this page.').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCooccurrence'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Activity co-occurrence browser acceptance passed: matrix, pair and branch cases, filter scope, complete events, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
