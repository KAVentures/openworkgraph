const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a saved log downloads as an OCEL 2.0 SQLite database.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-ocelsqlite-'));
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
   for(let i=0;i<4;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 8 events'}).waitFor();
   await page.locator('#owgEventLogExportFormat').selectOption('ocelsqlite');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
   assert.match(download.suggestedFilename(),/\.ocel\.sqlite$/);
   const file=await download.path();
   const counts=execFileSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
print(c.execute('SELECT count(*) FROM event').fetchone()[0],c.execute('SELECT count(*) FROM event_object').fetchone()[0],sorted(t for (t,) in c.execute("SELECT ocel_type_map FROM event_map_type")))`,file]).toString().trim();
   assert.equal(counts,"8 8 ['Pay', 'Receive']");
   await page.locator('#owgEventLogStatus').filter({hasText:'Export ready.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('OCEL SQLite export browser acceptance passed: standard tables with every event and link.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
