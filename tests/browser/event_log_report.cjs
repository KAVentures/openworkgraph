const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: download a self-contained HTML report for a saved event log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logreport-'));
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<8;i++)['Receive order','Check stock',i%2?'Ship':'Back-order','Invoice'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-0${1+(i%8)}T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 32 events'}).waitFor();
   // The report is available as soon as a log is selected; analysis is not required.
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogReport').click()]);
   assert.match(download.suggestedFilename(),/^log_[0-9a-f]{16}-report\.html$/);
   const html=await fs.readFile(await download.path(),'utf8');
   assert.match(html,/^<!doctype html>/);
   assert(html.includes('<h1>orders</h1>'),'the log name heads the report');
   assert(html.includes('Receive order → Check stock → Ship → Invoice'),'the variant path');
   for(const name of ['Back-order','Invoice'])assert(html.includes(name),name);
   assert.doesNotMatch(html,/<script/i);
   assert.match(html,/not verified rework/);
   await page.locator('#owgEventLogStatus').filter({hasText:'Report ready'}).waitFor();
   // Saved copies: none yet, save one now, then download it.
   await page.locator('#owgEventLogSavedReports').click();
   await page.locator('#owgEventLogSavedList').filter({hasText:'No saved copies yet.'}).waitFor();
   assert.equal(await page.locator('#owgEventLogSavedReports').getAttribute('aria-expanded'),'true');
   await page.locator('#owgEventLogSaveCopy').click();
   await page.locator('#owgEventLogSavedList').filter({hasText:/Saved report-\d{8}T\d{6}Z(?:-\d+)?\.html\./}).waitFor();
   assert.equal(await page.locator('#owgEventLogSavedList tbody tr').count(),1);
   await page.locator('#owgEventLogSavedList').filter({hasText:'Save two copies to see what changed between them.'}).waitFor();
   await page.locator('#owgEventLogSaveCopy').click();
   await page.locator('#owgEventLogSavedChanges').waitFor();
   const changesText=await page.locator('#owgEventLogSavedChanges').innerText();
   assert.match(changesText,/Changes since the previous copy/);
   assert.match(changesText,/Cases\t8\t8\tno change/);
   assert.equal(await page.locator('#owgEventLogSavedList tbody tr').count()>=2,true);
   const [copy]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogSavedList').getByRole('button',{name:/^Download saved report report-/}).first().click()]);
   assert.match(copy.suggestedFilename(),/^log_[0-9a-f]{16}-report-\d{8}T\d{6}Z(?:-\d+)?\.html$/);
   const copyHtml=await fs.readFile(await copy.path(),'utf8');
   assert(copyHtml.startsWith('<!doctype html>')&&copyHtml.includes('<h1>orders</h1>'));
   await page.addScriptTag({content:axeSource});
   const savedAudit=await page.evaluate(async()=>window.axe.run({include:[['#owgEventLogManage'],['#owgEventLogSavedList']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(savedAudit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgEventLogSavedReports').click();
   assert.equal(await page.locator('#owgEventLogSavedList').isVisible(),false);
   // Opened on its own, the saved report renders with no network and no errors.
   const offline=await context.newPage(),offlineErrors=[],requests=[];
   offline.on('pageerror',e=>offlineErrors.push(e.message));offline.on('request',r=>requests.push(r.url()));
   await offline.setContent(html);
   assert.equal(await offline.locator('.tile').count()>=8,true);
   assert.equal(await offline.locator('table').count(),4);
   assert(html.includes('<h2>Slowest transitions</h2>'),'the report lists the slowest transitions');
   assert.deepEqual(requests.filter(u=>!u.startsWith('about:')&&!u.startsWith('data:')),[]);
   assert.deepEqual(offlineErrors,[]);
   await offline.addScriptTag({content:axeSource});
   const audit=await offline.evaluate(async()=>window.axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log report browser acceptance passed: script-free HTML download with overview, variants and activities that renders offline and passes a WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
