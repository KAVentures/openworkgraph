const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: an OCEL 2.0 SQLite database is recognised and imported with its objects.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-ocelsqlite-in-'));
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
   const dbPath=path.join(scratch,'orders.sqlite');
   execFileSync(python,['-c',`import sqlite3,sys
sys.path.insert(0,'tests')
from test_event_log_ocel_sqlite_import import build
c=sqlite3.connect(sys.argv[1]);build(c);c.commit()`,dbPath]);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.sqlite',mimeType:'application/octet-stream',buffer:await fs.readFile(dbPath)});
   await page.locator('#owgEventLogStatus').filter({hasText:'Read an OCEL 2.0 database: 6 events and 6 objects. 9 times without an offset are read as UTC.'}).waitFor();
   assert.equal(await page.locator('#owgEventLogFormat').inputValue(),'ocel');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 6 events'}).waitFor();
   const types=await page.evaluate(async()=>{const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;
     return (await (await fetch('/v1/event-logs/'+id+'/object-types')).json()).object_types.map(t=>t.object_type).sort();});
   assert.deepEqual(types,['item','order']);
   assert.deepEqual(errors,[]);
   console.log('OCEL SQLite import browser acceptance passed: recognised, converted to OCEL and imported with both object types.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
