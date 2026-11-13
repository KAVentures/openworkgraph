const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a newer export with the same columns is offered the mapping recorded with the earlier import.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logreuse-'));
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
   const context=await browser.newContext({timezoneId:'Asia/Kolkata'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // Columns in an order the automatic guess gets wrong, a semicolon delimiter and day-first times.
   const header='Region;Logged;Stage;Ref;Clerk';
   const january=[header];for(let i=0;i<4;i++)['Open','Close'].forEach((a,k)=>january.push(`EU;${10+i}/01/2024 0${8+k}:00;${a};R${i};ana`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'january.csv',mimeType:'text/csv',buffer:Buffer.from(january.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   assert.equal(await page.locator('#owgEventLogReuse').isVisible(),false,'nothing to reuse yet');
   await page.locator('#owgEventLogCase').selectOption('Ref');await page.locator('#owgEventLogActivity').selectOption('Stage');
   await page.locator('#owgEventLogResource').selectOption('Clerk');await page.locator('#owgEventLogAttributes').selectOption(['Region']);
   await page.locator('#owgEventLogTime').selectOption('Logged');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogTimeFormat').value==='%d/%m/%Y %H:%M');
   await page.locator('#owgEventLogNaiveOffset').fill('+01:00');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 8 events'}).waitFor();
   // February's export has the same columns: the January mapping is offered, not applied.
   const february=[header];for(let i=0;i<3;i++)['Open','Close'].forEach((a,k)=>february.push(`EU;0${1+i}/02/2024 0${8+k}:00;${a};F${i};bo`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'february.csv',mimeType:'text/csv',buffer:Buffer.from(february.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.locator('#owgEventLogReuseMapping').waitFor();
   assert.equal(await page.locator('#owgEventLogReuseMapping').innerText(),'Use the mapping from “january”');
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'Region','not applied until chosen');
   await page.locator('#owgEventLogReuseMapping').click();
   await page.locator('#owgEventLogReuse').filter({hasText:'Mapping from “january” applied.'}).waitFor();
   for(const [id,value] of [['Case','Ref'],['Activity','Stage'],['Time','Logged'],['Resource','Clerk'],['TimeFormat','%d/%m/%Y %H:%M'],['NaiveOffset','+01:00']])
     assert.equal(await page.locator('#owgEventLog'+id).inputValue(),value,id);
   assert.deepEqual(await page.locator('#owgEventLogAttributes').evaluate(s=>[...s.selectedOptions].map(o=>o.value)),['Region']);
   assert.equal(await page.locator('#owgEventLogConsent').isChecked(),false,'consent is asked again');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogAppend').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Added 6 new events'}).waitFor();
   const summary=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;const log=logs.find(l=>l.name==='january');
     return (await fetch('/v1/event-logs/'+log.log_id+'/summary')).json();});
   assert.equal(summary.cases,7);
   assert.equal(summary.last_event_at,'2024-02-03T08:00:00Z');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run({include:[['#owgEventLogReuse']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log mapping reuse browser acceptance passed: the earlier mapping (columns, attributes, format, offset) offered for a matching export, applied on request and appended.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
