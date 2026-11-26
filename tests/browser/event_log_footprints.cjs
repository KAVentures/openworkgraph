const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: compare log and model footprints, with the full matrix.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-footprints-'));
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
   // 40 claims: Register, then Check and Pay in either order, possibly Check repeated, then Approve or Reject, then Close.
   const rows=['case,activity,time,who'];
   for(let i=0;i<40;i++){const steps=['Register',...(i%2?['Check','Pay']:['Pay','Check']),...(i%5===0?['Check']:[]),i%3?'Approve':'Reject','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-01T${String(j).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'claims.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 208 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgFootprints');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgFootprintsRun').click();
   const out=page.locator('#owgFootprintsResults');
   await page.locator('#owgFootprintsStatus').filter({hasText:'cells agree across 6 activities in 40 cases'}).waitFor();
   await out.locator('details summary').click();
   const matrix=page.locator('#owgFootprintsMatrix');
   assert.equal(await matrix.locator('tbody tr').count(),6);
   const header=(await matrix.locator('thead th').allTextContents()).slice(1);
   const cell=async(a,b)=>(await matrix.locator('tbody tr').nth(header.indexOf(a)).locator('td').nth(header.indexOf(b)+1).textContent());
   assert.equal(await cell('Check','Pay'),'||');
   assert.equal(await cell('Register','Approve'),'#');
   // The matrix downloads as one row per cell, matching the table.
   const [footCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgFootprintsReportCsv').click()]);
   const footLines=(await fs.readFile(await footCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.equal(footLines[0],'from,to,log_relation,model_relation,agrees');
   assert.equal(footLines.length,1+36);
   assert(footLines.includes('Check,Pay,||,||,true'),footLines.join('|'));
   await page.locator('#owgFootprintsNoise').selectOption('0');
   assert.equal(await page.locator('#owgFootprintsReportCsv').isDisabled(),true);
   await page.locator('#owgFootprintsNoise').selectOption('0.2');
   await page.locator('#owgFootprintsRun').click();
   await page.locator('#owgFootprintsReportCsv:not([disabled])').waitFor();
   await out.locator('details summary').click();
   const review=page.locator('#owgFootprintsCaseReview');
   await out.getByRole('button',{name:'Inspect cases for Check → Check',exact:true}).click();
   await review.getByRole('status').filter({hasText:'4 occurrences in 4 cases.'}).waitFor();
   assert.deepEqual(await review.locator('tbody td:first-child').allTextContents(),['c0','c10','c20','c30']);
   await review.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 6 of 6 events'}).waitFor();
   await page.locator('#owgCaseFilterContainsActivity').fill('Reject');await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 14 of 40 cases'}).waitFor();
   await page.locator('#owgFootprintsNoise').selectOption('0.4');
   await page.locator('#owgFootprintsRun').click();
   await out.getByRole('button',{name:'Inspect cases for Check → Check',exact:true}).click();
   await review.getByRole('status').filter({hasText:'2 occurrences in 2 cases.'}).waitFor();
   assert.deepEqual(await review.locator('tbody td:first-child').allTextContents(),['c0','c30']);
   await page.locator('#owgFootprintsNoise').selectOption('0');await page.locator('#owgFootprintsRun').click();
   await page.locator('#owgFootprintsStatus').filter({hasText:'cells agree'}).waitFor();
   await page.locator('#owgFootprintsActivityShare').selectOption('0.75');
   const [projected]=await Promise.all([page.waitForResponse(response=>{
      const url=new URL(response.url());
      return url.pathname.endsWith('/footprints')&&url.searchParams.get('activities')==='0.75';
   }),page.locator('#owgFootprintsRun').click()]);
   assert.ok((await projected.json()).activities.length<5);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgFootprints'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Footprints browser acceptance passed: conformance matrix, exact case evidence and history, cohort and activity projection, matrix CSV, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
