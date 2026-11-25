const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the owner's activity glossary is edited, kept and downloaded.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-glossary-'));
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
   // 6 cases Open > Approve > Close: 18 events, 3 activities.
   const rows=['case,activity,time,who'];
   for(let i=0;i<6;i++)['Open','Approve','Close'].forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T${String(8+j).padStart(2,'0')}:00:00Z,ana`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 18 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgGlossary');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgGlossaryLoad').click();
   await page.locator('#owgGlossaryStatus').filter({hasText:'0 of 3 activities described, covering 0% of events.'}).waitFor();
   await page.getByLabel('Description for Approve').fill('Manager approves the order');
   await page.getByLabel('Team for Approve').fill('Finance');
   await page.getByLabel('System for Approve').fill('=SAP');
   await page.getByLabel('Automation for Approve').selectOption('candidate');
   await page.getByRole('button',{name:'Save notes for Approve'}).click();
   await page.locator('#owgGlossaryStatus').filter({hasText:'Saved notes for Approve. 1 of 3 activities described, covering 33% of events. 1 marked as automation candidates.'}).waitFor();
   await page.locator('#owgGlossaryLoad').click();
   await page.locator('#owgGlossaryStatus').filter({hasText:'1 of 3 activities described'}).waitFor();
   assert.equal(await page.getByLabel('Description for Approve').inputValue(),'Manager approves the order');
   assert.equal(await page.getByLabel('Automation for Approve').inputValue(),'candidate');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgGlossaryCsv').click()]);
   assert.equal(download.suggestedFilename().endsWith('-activity-glossary.csv'),true,download.suggestedFilename());
   const text=await fs.readFile(await download.path(),'utf8');
   assert(text.startsWith('activity,in_log,events,description,owner_team,system,automation\n'),text);
   assert(text.includes("Approve,yes,6,Manager approves the order,Finance,'=SAP,candidate"),text);
   await page.locator('#owgGlossaryStatus').filter({hasText:'Glossary downloaded.'}).waitFor();
   await page.locator('#owgGlossaryImport').setInputFiles({name:'glossary.csv',mimeType:'text/csv',buffer:Buffer.from(text+'Close,yes,6,Order is closed,Sales,,automated\nGhost,no,0,x,,,\n')});
   await page.locator('#owgGlossaryStatus').filter({hasText:'Review the proposed changes before applying.'}).waitFor();
   await page.locator('#owgGlossaryImportReview').getByText(/1 entries would change/).waitFor();
   assert.equal(await page.getByLabel('Description for Close').inputValue(),'');
   await page.locator('#owgGlossaryApplyImport').click();
   await page.locator('#owgGlossaryStatus').filter({hasText:'Imported 3 entries. Skipped 1 activities not in this log. 2 of 3 activities described, covering 67% of events.'}).waitFor();
   assert.equal(await page.getByLabel('Description for Close').inputValue(),'Order is closed');
   assert.equal(await page.getByLabel('System for Approve').inputValue(),'=SAP');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgGlossary'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Activity glossary browser acceptance passed: notes saved and reloaded, coverage, spreadsheet-safe CSV that imports back, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
