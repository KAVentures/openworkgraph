const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the local time-allocation report and its CSV.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-time-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8795';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','time_allocation_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8795'],{
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
   const context=await browser.newContext({acceptDownloads:true,timezoneId:'UTC'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=export]').click();
   const card=page.locator('#owgTimeAllocation');await card.waitFor();
   assert.equal(await page.locator('#owgTimeZone').inputValue(),'UTC');
   await page.locator('#owgTimeShow').click();
   await page.locator('#owgTimeTable').waitFor();
   const text=await card.textContent();
   assert.match(text,/3 h 30 min recorded/,text);
   assert.match(text,/Spreadsheet: 2 h 0 min \(57\.1%\), 1 h 36 min engaged/,text);
   assert.match(text,/Mail: 1 h 30 min/,text);
   assert.match(text,/not effort, productivity or billable time/);
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgTimeCsv').click()]);
   const csvText=await fs.readFile(await download.path(),'utf8');
   assert.match(csvText,/^date,app,recorded_hours/);
   assert.match(csvText,/,Spreadsheet,2\.0/);
   await page.locator('#owgTimeZone').fill('Mars/Base');await page.locator('#owgTimeShow').click();
   await page.locator('#owgTimeStatus').filter({hasText:'unknown time zone'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgTimeAllocation'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Time allocation browser acceptance passed: browser time zone default, daily table, app shares with engaged time, CSV download, unknown zone refused, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
