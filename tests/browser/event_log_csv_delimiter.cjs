const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: semicolon and tab CSV files, and Windows-1252 or UTF-16 text, are read without a manual choice.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-delimiter-'));
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
   const context=await browser.newContext({timezoneId:'UTC'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // Semicolon-separated, with a comma and a semicolon inside quoted cells.
   const semicolon=['Case;Activity;Time;Note','c1;Receive;2024-03-01T08:00:00Z;"a, b"','c1;Pay;2024-03-01T09:00:00Z;"x; y"','c2;Receive;2024-03-02T08:00:00Z;'];
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(semicolon.join('\r\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogDelimiter').value===';'&&document.querySelector('#owgEventLogCase').options.length>=4);
   await page.locator('#owgEventLogCase').selectOption('Case');await page.locator('#owgEventLogActivity').selectOption('Activity');
   await page.locator('#owgEventLogTime').selectOption('Time');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 3 events'}).waitFor();
   // A tab-separated export with a .txt name.
   await page.locator('#owgEventLogName').fill('');
   const tabs=['case\tactivity\ttime','t1\tOpen\t2024-03-01T08:00:00Z','t1\tClose\t2024-03-01T10:00:00Z'];
   await page.locator('#owgEventLogFile').setInputFiles({name:'export.txt',mimeType:'text/plain',buffer:Buffer.from(tabs.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogDelimiter').value==='t'&&document.querySelector('#owgEventLogCase').options.length>=3);
   // Windows-1252 (an "ANSI" export from a spreadsheet) and UTF-16 (Excel Unicode text) are detected and decoded.
   const latin=Buffer.from('Fall;Tätigkeit;Zeit\nw1;Prüfung;2024-03-01T08:00:00Z\nw1;Bestätigt;2024-03-01T09:00:00Z\n','latin1');
   await page.locator('#owgEventLogName').fill('ansi');
   await page.locator('#owgEventLogFile').setInputFiles({name:'ansi.csv',mimeType:'text/csv',buffer:latin});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogEncoding').value==='windows-1252'&&[...document.querySelector('#owgEventLogActivity').options].some(o=>o.value==='Tätigkeit'));
   await page.locator('#owgEventLogCase').selectOption('Fall');await page.locator('#owgEventLogActivity').selectOption('Tätigkeit');
   await page.locator('#owgEventLogTime').selectOption('Zeit');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
   const names=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;const id=logs.find(l=>l.name==='ansi').log_id;
     return (await (await fetch('/v1/event-logs/'+id+'/activities')).json()).activities.map(a=>a.activity).sort();});
   assert.deepEqual(names,['Bestätigt','Prüfung']);
   await page.locator('#owgEventLogName').fill('');
   const utf16=Buffer.concat([Buffer.from([0xFF,0xFE]),Buffer.from('case\tactivity\ttime\nu1\tÖffnen\t2024-03-01T08:00:00Z\n','utf16le')]);
   await page.locator('#owgEventLogFile').setInputFiles({name:'unicode.txt',mimeType:'text/plain',buffer:utf16});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogEncoding').value==='utf-16'&&document.querySelector('#owgEventLogDelimiter').value==='t'&&document.querySelector('#owgEventLogCase').options.length>=3);
   // A comma file switches back.
   await page.locator('#owgEventLogFile').setInputFiles({name:'plain.csv',mimeType:'text/csv',buffer:Buffer.from('case,activity,time\nk1,A,2024-03-01T08:00:00Z\n')});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogDelimiter').value===','&&document.querySelector('#owgEventLogEncoding').value==='utf-8'&&document.querySelector('#owgEventLogCase').options.length>=3);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogDelimiter').closest('section')||document.body,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('CSV delimiter browser acceptance passed: semicolon and tab files detected and imported, Windows-1252 and UTF-16 decoded, comma and UTF-8 restored, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
