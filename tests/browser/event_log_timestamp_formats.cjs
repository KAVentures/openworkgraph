const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: non-ISO CSV timestamps get a suggested format (or an ambiguity warning) and import with an explicit offset.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logtsfmt-'));
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
   const summaryOf=name=>page.evaluate(async name=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const log=logs.find(l=>l.name===name);return {...(await (await fetch('/v1/event-logs/'+log.log_id+'/summary')).json()),events:log.events};},name);
   // Day-first times: the format is found from the first rows and the browser's offset is filled in.
   const dayFirst=['case,activity,time'];
   for(let i=0;i<5;i++)['Receive','Approve','Pay'].forEach((a,k)=>dayFirst.push(`c${i},${a},${String(13+i).padStart(2,'0')}/03/2024 ${String(9+k).padStart(2,'0')}:05`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'day first.csv',mimeType:'text/csv',buffer:Buffer.from(dayFirst.join('\n'))});
   await page.locator('#owgEventLogStatus').filter({hasText:'Times look like 13/03/2024 09:05, read as 2024-03-13 09:05:00 with format %d/%m/%Y %H:%M at offset +05:30.'}).waitFor();
   assert.equal(await page.locator('#owgEventLogTimeFormat').inputValue(),'%d/%m/%Y %H:%M');
   assert.equal(await page.locator('#owgEventLogNaiveOffset').inputValue(),'+05:30');
   await page.locator('#owgEventLogNaiveOffset').fill('+01:00');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 15 events'}).waitFor();
   let summary=await summaryOf('day first');
   assert.equal(summary.cases,5);
   assert.equal(summary.first_event_at,'2024-03-13T08:05:00Z');
   // Every day is 12 or less: both orders fit, nothing is chosen for the user.
   const ambiguous=['case,activity,time'];
   for(let i=0;i<4;i++)['Receive','Pay'].forEach((a,k)=>ambiguous.push(`a${i},${a},0${1+i}/0${2+k}/2024 10:00`));
   await page.locator('#owgEventLogName').fill('');
   await page.locator('#owgEventLogNaiveOffset').fill('');
   await page.locator('#owgEventLogFile').setInputFiles({name:'ambiguous.csv',mimeType:'text/csv',buffer:Buffer.from(ambiguous.join('\n'))});
   await page.locator('#owgEventLogStatus').filter({hasText:'fit both day-first and month-first formats'}).waitFor();
   assert.equal(await page.locator('#owgEventLogTimeFormat').inputValue(),'');
   const labels=await page.locator('#owgEventLogTimeFormat option').allInnerTexts();
   assert(labels.some(l=>l.startsWith('%m/%d/%Y %H:%M — month-first: 01/02/2024 10:00 reads as 2024-01-02 10:00:00')),labels.join('|'));
   assert(labels.some(l=>l.startsWith('%d/%m/%Y %H:%M — day-first: 01/02/2024 10:00 reads as 2024-02-01 10:00:00')),labels.join('|'));
   await page.locator('#owgEventLogTimeFormat').selectOption('%m/%d/%Y %H:%M');
   assert.equal(await page.locator('#owgEventLogNaiveOffset').inputValue(),'+05:30','choosing a format without an offset fills in the browser offset');
   await page.locator('#owgEventLogNaiveOffset').fill('Z');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 8 events'}).waitFor();
   summary=await summaryOf('ambiguous');
   assert.equal(summary.first_event_at,'2024-01-02T10:00:00Z');
   assert.equal(summary.last_event_at,'2024-04-03T10:00:00Z');
   // ISO files keep the default and get no format message.
   await page.locator('#owgEventLogName').fill('');
   await page.locator('#owgEventLogFile').setInputFiles({name:'iso.csv',mimeType:'text/csv',buffer:Buffer.from('case,activity,time\nx,A,2024-03-04T08:00:00Z\n')});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready'}).waitFor();
   await page.waitForTimeout(300);
   assert.equal(await page.locator('#owgEventLogTimeFormat').inputValue(),'');
   assert.match(await page.locator('#owgEventLogStatus').innerText(),/File mapping ready/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run({include:[['#owgEventLogTimeFormat']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log timestamp format browser acceptance passed: day-first format suggested with an offset, ambiguous dates left to the user, ISO files unchanged.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
