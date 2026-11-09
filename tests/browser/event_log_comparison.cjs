const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: compare two saved event logs from the dashboard.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logcompare-'));
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
   const context=await browser.newContext({});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const importLog=async(name,paths)=>{
     const rows=['case,activity,time'];
     paths.forEach((path,i)=>path.forEach((a,k)=>rows.push(`${name}${i},${a},2024-03-${String(1+i%28).padStart(2,'0')}T${String(k*2).padStart(2,'0')}:00:00Z`)));
     await page.locator('#owgEventLogFile').setInputFiles({name:name+'.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
     await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
     await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
     await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
   };
   await importLog('south',Array.from({length:12},(_,i)=>i%3?['Receive','Approve','Pay']:['Receive','Escalate','Approve','Pay']));
   await importLog('north',Array.from({length:12},(_,i)=>i%4?['Receive','Check','Approve','Pay']:['Receive','Check','Check','Approve','Pay']));
   // The most recent import (north) is selected; compare it with south.
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgLogComparison');await section.waitFor();
   await section.scrollIntoViewIfNeeded();
   assert.equal(await page.locator('#owgLogCompareOther option').count(),1);
   await page.locator('#owgLogCompareRun').click();
   const out=page.locator('#owgLogCompareResults');
   await out.locator('table').first().waitFor();
   const text=await out.innerText();
   assert.match(text,/north \(this log\)/i);
   assert.match(text,/Only in north: Check\./);
   assert.match(text,/Only in south: Escalate\./);
   assert.match(text,/Receive → Approve/);
   assert.match(text,/0 variants occur in both logs/);
   assert.match(text,/not evidence of its cause/);
   assert.equal(await out.locator('table').count(),3);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgLogComparison'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log comparison browser acceptance passed: KPIs, activities only in one log, path differences, variant overlap and caveat, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
