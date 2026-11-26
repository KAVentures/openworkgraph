const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: rank automation candidates and send one to the automation pipeline with its evidence.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-autocand-'));
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
   // Twenty invoices: Receive -> Check (six hours later) -> optional Clarify -> Post.
   const rows=['case,activity,time,who'];
   for(let i=0;i<20;i++){const day=String(1+i).padStart(2,'0');
     rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z,mail`,`c${i},Check,2024-03-${day}T14:00:00Z,ana`);
     if(i%2===0)rows.push(`c${i},Clarify,2024-03-${day}T14:30:00Z,bob`);
     rows.push(`c${i},Post,2024-03-${day}T15:00:00Z,bot`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'invoices.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 70 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgAutomation');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgAutomationRun').click();
   const results=page.locator('#owgAutomationResults');
   await results.filter({hasText:'Score = 100'}).waitFor();
   const first=await results.locator('tbody tr').first().locator('td').allTextContents();
   assert.equal(first[0],'Check');assert.equal(first[4],'Mostly fixed');
   assert.equal(await results.locator('tbody tr').count(),4);
   // The ranking downloads with every score component.
   const [rankCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAutomationReportCsv').click()]);
   const rankLines=(await fs.readFile(await rankCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.match(rankLines[0],/^activity,score,volume_component,routine_component,waiting_component,occurrences,cases,per_week,routing_pattern,/);
   assert.equal(rankLines.length,5);assert.match(rankLines[1],/^Check,[\d.]+,[\d.]+,[\d.]+,[\d.]+,20,20,[\d.]+,mostly_fixed,/);
   await results.getByRole('button',{name:'Add Check to the automation pipeline'}).click();
   await results.filter({hasText:'Added to the pipeline as an idea.'}).waitFor();
   const idea=await page.evaluate(async()=>{const r=await (await fetch('/v1/automation-pipeline')).json();const list=r.ideas||r.items||[];return list.map(i=>[i.title,i.source&&i.source.kind,i.description]);});
   assert.equal(idea.length,1);assert.equal(idea[0][0],'Automate Check');assert.equal(idea[0][1],'event_log');
   assert(idea[0][2].includes('From event log “invoices”: 20 occurrences in 20 cases'),idea[0][2]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAutomation'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Automation candidates browser acceptance passed: ranked activities with components, routing pattern and waiting, sent to the automation pipeline with evidence, CSV ranking, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
