const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a JSON Lines file is converted to CSV, mapped and imported.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-jsonl-'));
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
   const lines=[];
   for(let i=0;i<4;i++)for(const [j,a] of ['Create','Ship'].entries())lines.push(JSON.stringify({order:{id:'o'+i},event:a,at:`2024-03-0${1+i}T0${8+j}:00:00Z`,tags:['web']}));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.jsonl',mimeType:'application/x-ndjson',buffer:Buffer.from(lines.join('\n')+'\n')});
   await page.locator('#owgEventLogStatus').filter({hasText:'Read 8 JSON lines with 4 columns from orders.jsonl'}).waitFor();
   assert.equal(await page.locator('#owgEventLogSheet').isVisible(),false);
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogCase').selectOption('order.id');await page.locator('#owgEventLogActivity').selectOption('event');
   await page.locator('#owgEventLogTime').selectOption('at');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 8 events'}).waitFor();
   const logs=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>[l.name,l.events]));
   assert.deepEqual(logs,[['orders',8]]);
   assert.deepEqual(errors,[]);
   console.log('JSON Lines browser acceptance passed: converted with dotted nested columns, mapped and imported.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
