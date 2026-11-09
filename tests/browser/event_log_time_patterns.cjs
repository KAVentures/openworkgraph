const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: when work happens, by local weekday and hour.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pattern-'));
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
   const context=await browser.newContext({timezoneId:'Europe/Stockholm'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time','a,Receive,2024-03-04T08:00:00Z','a,Approve,2024-03-04T16:30:00Z','b,Receive,2024-04-01T08:00:00Z','b,Approve,2024-04-06T21:00:00Z','c,Receive,2024-03-10T23:30:00Z'];
   await page.locator('#owgEventLogFile').setInputFiles({name:'times.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 5 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogTimePatterns').waitFor();
   assert.equal(await page.locator('#owgPatternZone').inputValue(),'Europe/Stockholm');
   await page.locator('#owgPatternRun').click();
   const out=page.locator('#owgPatternResults');await out.locator('#owgPatternGrid').waitFor();
   const text=await out.textContent();
   assert.match(text,/5 events and 3 new cases in Europe\/Stockholm\. 40\.0% of events fell outside 08:00–18:00/,text);
   const monday=await page.locator('#owgPatternGrid tbody tr').first().locator('td').allTextContents();
   assert.equal(monday[0],'1');assert.equal(monday[9],'1');assert.equal(monday[10],'1');assert.equal(monday[17],'1');
   await page.locator('#owgPatternZone').fill('Mars/Base');await page.locator('#owgPatternRun').click();
   await out.getByText('unknown time zone').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogTimePatterns'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Time patterns browser acceptance passed: browser time zone default, daylight-saving-aware weekday x hour grid, outside-hours share, refused zone, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
