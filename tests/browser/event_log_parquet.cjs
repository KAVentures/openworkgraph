const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a Parquet file is converted to CSV, mapped and imported.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-parquet-'));
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
   const file=path.join(scratch,'orders.parquet');
   execFileSync(python,['-c',`import duckdb,sys
duckdb.connect().execute("COPY (SELECT 'o' || (i // 2) AS case_id, CASE WHEN i % 2 = 0 THEN 'Create' ELSE 'Ship' END AS activity, TIMESTAMPTZ '2024-03-01 08:00:00+00' + INTERVAL (i) HOUR AS at FROM range(10) t(i)) TO '"+sys.argv[1]+"' (FORMAT parquet)")`,file]);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.parquet',mimeType:'application/octet-stream',buffer:await fs.readFile(file)});
   await page.locator('#owgEventLogStatus').filter({hasText:'Read 10 rows with 3 columns from orders.parquet.'}).waitFor();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogCase').selectOption('case_id');await page.locator('#owgEventLogActivity').selectOption('activity');
   await page.locator('#owgEventLogTime').selectOption('at');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 10 events'}).waitFor();
   const logs=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>[l.name,l.events]));
   assert.deepEqual(logs,[['orders',10]]);
   assert.deepEqual(errors,[]);
   console.log('Parquet browser acceptance passed: converted with UTC times, mapped and imported.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
