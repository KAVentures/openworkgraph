const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: explain where a slow case's time went compared with the rest of the log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-explain-'));
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
   const context=await browser.newContext({locale:'en-US'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   const at=(d,h)=>new Date(Date.UTC(2024,0,1+d,8)+h*3600000).toISOString().replace('.000Z','Z');
   for(let i=0;i<20;i++)[['Receive',0],['Check',1],['Approve',3],['Pay',4]].forEach(([a,h])=>rows.push(`c${String(i).padStart(2,'0')},${a},${at(i,h)}`));
   [['Receive',0],['Check',1],['Check',2],['Escalate',3],['Approve',33],['Pay',34]].forEach(([a,h])=>rows.push(`c99,${a},${at(30,h)}`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'explain.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 86 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgCaseExplain');await box.waitFor();await box.scrollIntoViewIfNeeded();
   // The slowest case is offered first; one click explains it.
   await page.getByRole('button',{name:'Explain case c99'}).click();
   const out=page.locator('#owgCaseExplainResult');await out.locator('table').waitFor();
   const text=await out.innerText();
   assert.match(text,/Case c99 took 34\.0 h \(log median 4\.0 h/);
   assert.match(text,/Most of the extra time: Escalate → Approve \(30\.0 h, a path few cases take\)/);
   assert.match(text,/Repeated: Check ×2\./);
   assert.match(text,/Uncommon steps \(in under 10% of cases\): Escalate\./);
   // Typing an ordinary case shows nothing above typical.
   await page.locator('#owgCaseExplainId').fill('c05');await page.locator('#owgCaseExplainRun').click();
   await out.filter({hasText:'No step took longer than usual'}).waitFor();
   await page.locator('#owgCaseExplainId').fill('nope');await page.locator('#owgCaseExplainRun').click();
   await out.filter({hasText:'unknown case'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseExplain'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Case explanation browser acceptance passed: slowest case offered, extra time attributed to its steps, repeats and uncommon steps, ordinary and unknown cases, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
