const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: filter cases by an event attribute kept from a CSV import.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-evattr-'));
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
   const context=await browser.newContext({});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time,region'];
   for(let i=0;i<10;i++)['Receive','Pay'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k}:00:00Z,${i>=6?'south':'north'}`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'regions.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogAttributes').selectOption(['region']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 20 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const filter=page.locator('#owgEventLogKpiCohortEventAttribute');await filter.waitFor();await filter.scrollIntoViewIfNeeded();
   await filter.fill('region=south');
   await page.locator('#owgEventLogKpiRun').click();
   const kpi=page.locator('#owgEventLogKpiRun').locator('xpath=ancestor::div[h3][1]');
   await kpi.locator('table').first().waitFor();
   const cells=await kpi.locator('table tbody tr').first().locator('td').allInnerTexts();
   assert.equal(cells[0],'4','four south cases');
   await filter.fill('region');await page.locator('#owgEventLogKpiRun').click();
   await kpi.getByText('Event attribute must look like name=value.').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogKpiCohort'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-attribute case filter browser acceptance passed: CSV column filters KPIs, invalid input is explained, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
