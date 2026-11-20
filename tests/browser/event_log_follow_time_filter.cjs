const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: follower rules with a time limit between the two steps, saved and re-applied.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-followtime-'));
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
   // Submit then Approve after 2, 10, 30 and 60 hours (two cases each); one case never approves.
   const rows=['case,activity,time'];
   [2,2,10,10,30,30,60,60].forEach((h,i)=>{const d=new Date(Date.UTC(2024,2,1+i*4,0));rows.push(`c${i},Submit,${d.toISOString().replace('.000Z','Z')}`);
     rows.push(`c${i},Approve,${new Date(d.getTime()+h*3600000).toISOString().replace('.000Z','Z')}`);});
   rows.push('n0,Submit,2024-04-20T00:00:00Z','n0,Check,2024-04-20T05:00:00Z');
   await page.locator('#owgEventLogFile').setInputFiles({name:'approvals.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 18 events'}).waitFor();
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   let s=await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   assert.equal(s.body.cases,9);
   const box=page.locator('#owgCaseFilter');await box.scrollIntoViewIfNeeded();
   await page.locator('#owgCaseFilterAdd').click();
   await page.locator('#owgCaseFilterFrom1').selectOption('Submit');await page.locator('#owgCaseFilterTo1').selectOption('Approve');
   // Approved more than a day after submission.
   await page.locator('#owgCaseFilterMinHours1').fill('24');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,4);assert.match(s.url,/"min_hours":24/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Submit is eventually followed by Approve after at least 24 h.'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterMinHours1').inputValue(),'24','kept after re-analysis');
   // Between 5 and 40 hours.
   await page.locator('#owgCaseFilterMinHours1').fill('5');await page.locator('#owgCaseFilterMaxHours1').fill('40');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,4);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'after 5 h to 40 h'}).waitFor();
   // Validation before anything is sent.
   await page.locator('#owgCaseFilterMaxHours1').fill('3');await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'The shortest time must be below the longest.'}).waitFor();
   await page.locator('#owgCaseFilterMaxHours1').fill('12');await page.locator('#owgCaseFilterMinHours1').fill('');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,4);
   // Saved with the filter and re-applied.
   await page.locator('#owgFilterSetName').fill('Fast approvals');await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "Fast approvals".'}).waitFor();
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());assert.equal(s.body.cases,9);
   const saved=page.locator('#owgFilterSetList');await saved.filter({hasText:'Submit is eventually followed by Approve within 12 h'}).waitFor();
   s=await summaryAfter(()=>saved.getByRole('button',{name:'Apply saved filter Fast approvals'}).click());
   assert.equal(s.body.cases,4);
   assert.equal(await page.locator('#owgCaseFilterMaxHours1').inputValue(),'12');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Follow time filter browser acceptance passed: at least, between and within limits applied, validated, saved and re-applied, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
