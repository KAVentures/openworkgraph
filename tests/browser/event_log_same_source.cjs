const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: importing the same file twice says it was already loaded, and names the earlier log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-samesrc-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
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
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   const load=async name=>{
     await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
     await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
     await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
     await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
     return (await page.locator('#owgEventLogStatus').textContent());
   };
   const first=await load('orders.csv');
   assert.ok(!first.includes('already loaded'),first);
   const second=await load('orders-copy.csv');
   assert.match(second,/This exact file was already loaded as “orders”/);
   assert.equal((await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length)),2);
   assert.deepEqual(errors,[]);
   console.log('Same-source browser acceptance passed: first import silent, re-import names the earlier log, both kept.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
