const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: tag cases by filter and by ID, filter analyses by tag, remove a tag.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-casetags-'));
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
   const rows=['case,activity,time'];
   for(let i=0;i<10;i++)(i%3?['Receive','Pay']:['Receive','Escalate','Pay']).forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgCaseTags');await section.waitFor();await section.scrollIntoViewIfNeeded();
   await page.locator('#owgCaseTagList').filter({hasText:'No tags yet.'}).waitFor();
   // Tag every escalated case through the filter.
   await page.locator('#owgCaseTagName').fill('escalated');
   await page.locator('#owgCaseTagCohortActivity').selectOption('Escalate');
   await page.locator('#owgCaseTagAdd').click();
   await page.locator('#owgCaseTagResult').filter({hasText:'Tagged 4 cases “escalated”.'}).waitFor();
   // Tag two cases by ID with a note; one ID is not in the log.
   await page.locator('#owgCaseTagName').fill('checked');await page.locator('#owgCaseTagNote').fill('looked fine');
   await page.locator('#owgCaseTagIds').fill('c1, c2\nzz');
   await page.locator('#owgCaseTagAdd').click();
   await page.locator('#owgCaseTagResult').filter({hasText:'Tagged 2 cases “checked”; 1 IDs are not cases of this log.'}).waitFor();
   const listText=await page.locator('#owgCaseTagList').innerText();
   assert.match(listText,/checked\t2\t2\t/);assert.match(listText,/escalated\t4\t0\t/);
   await page.getByRole('button',{name:'Show cases tagged checked'}).click();
   await page.locator('#owgCaseTagResult').filter({hasText:'looked fine'}).waitFor();
   // Any analysis can use the tag: KPIs for the escalated cases only.
   await page.locator('#owgEventLogKpiCohortTag').fill('escalated');
   await page.locator('#owgEventLogKpiRun').click();
   const kpi=page.locator('#owgEventLogKpiRun').locator('xpath=ancestor::div[h3][1]');
   await kpi.locator('table').first().waitFor();
   assert.equal((await kpi.locator('table tbody tr').first().locator('td').allInnerTexts())[0],'4');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseTags'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   page.once('dialog',dialog=>dialog.accept());
   await page.getByRole('button',{name:'Remove tag escalated'}).click();
   await page.locator('#owgCaseTagResult').filter({hasText:'Removed “escalated” from 4 cases.'}).waitFor();
   assert.doesNotMatch(await page.locator('#owgCaseTagList').innerText(),/escalated/);
   assert.deepEqual(errors,[]);
   console.log('Case tags browser acceptance passed: tag by filter and by ID with note, unknown IDs reported, KPIs filtered by tag, tag removed, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
