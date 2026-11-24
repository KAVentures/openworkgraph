const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: check timings against the temporal profile and find a case invoiced far too late.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-temporal-'));
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
   // 20 orders: shipped hours after the order, invoiced two days later -- except c0, invoiced weeks later.
   const rows=['case,activity,time,who'];
   for(let i=0;i<20;i++){const day=String(1+i%5).padStart(2,'0'),inv=String(1+i%5+2).padStart(2,'0');
     rows.push(`c${i},Order,2024-01-${day}T09:00:00Z,ana`,`c${i},Ship,2024-01-${day}T1${i%3}:00:00Z,bot`,
       i===0?'c0,Invoice,2024-02-20T09:00:00Z,bob':`c${i},Invoice,2024-01-${inv}T09:${String(i).padStart(2,'0')}:00Z,bob`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 60 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgTemporal');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgTemporalRun').click();
   const out=page.locator('#owgTemporalResults');
   await page.locator('#owgTemporalStatus').filter({hasText:'1 of 20 cases (5%) have at least one unusual timing'}).waitFor();
   const cases=out.locator('table').last();
   assert.equal(await cases.locator('tbody tr').count(),1);
   const row=await cases.locator('tbody tr').first().textContent();
   assert(row.startsWith('c0')&&row.includes('→ Invoice'),row);
   assert((await out.locator('table').first().textContent()).includes('Order'));
   await out.getByRole('button',{name:'Browse all deviating cases',exact:true}).click();
   const review=page.locator('#owgTemporalCases');
   await review.getByText('1 matching case with 2 deviations. Showing 1.').waitFor();
   await out.getByRole('button',{name:'Inspect timings from Order to Invoice',exact:true}).click();
   await review.getByText('1 matching case with 1 deviation. Showing 1.').waitFor();
   await page.locator('#owgTemporalDirection').selectOption('faster');
   await review.getByText('No cases match these timings.').waitFor();
   await page.locator('#owgTemporalDirection').selectOption('slower');
   await review.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('cell',{name:'Invoice',exact:true}).waitFor();
   await page.locator('#owgTemporalZeta').selectOption('6');await page.locator('#owgTemporalRun').click();
   assert.equal(await review.textContent(),'');
   await page.locator('#owgTemporalStatus').filter({hasText:'0 of 20 cases'}).waitFor();
   const heldOut=['case,activity,time,who'];
   for(let i=0;i<6;i++)heldOut.push(
     `baseline${i},Order,2024-01-01T09:00:00Z,ana`,
     `baseline${i},Invoice,2024-01-01T09:${String(8+i).padStart(2,'0')}:00Z,bob`);
   heldOut.push(
     'cross,Order,2024-01-01T23:59:00Z,ana','cross,Invoice,2024-01-02T00:00:00Z,bob',
     'held-out-late,Order,2024-01-02T00:00:00Z,ana','held-out-late,Invoice,2024-01-02T01:00:00Z,bob',
     'unknown,Order,2024-01-02T09:00:00Z,ana','unknown,Escalate,2024-01-02T09:40:00Z,bob'
   );
   await page.locator('#owgEventLogFile').setInputFiles({
     name:'cutoff.csv',mimeType:'text/csv',buffer:Buffer.from(heldOut.join('\n'))
   });
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 18 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const cutoff=page.locator('#owgTemporalBaselineCutoff');
   await cutoff.fill('2024-01-02T00:00');
   await page.locator('#owgTemporalRun').click();
   await page.locator('#owgTemporalStatus').getByText('1 of 1 later cases with checked pairs have unusual timings.').waitFor();
   const coverage=await out.locator('table').first().locator('tbody td').allTextContents();
   assert.deepEqual(coverage,['2024-01-02T00:00:00Z','6','2','1','1','1']);
   await out.getByRole('button',{name:'Inspect timings from Order to Invoice',exact:true}).click();
   await review.getByText('1 matching case with 1 deviation. Showing 1.').waitFor();
   await review.getByRole('button',{name:'Open case held-out-late',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('cell',{name:'Invoice',exact:true}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgTemporal'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Temporal profile browser acceptance passed: the late invoice is found though each step looks normal, a wider limit clears it, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
