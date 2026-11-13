const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: stuck open cases.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-stuck-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];const at=(d,h)=>new Date(Date.UTC(2024,2,1+d,h)).toISOString();
   for(let i=0;i<30;i++)rows.push(`c${i},Receive,${at(i,0)}`,`c${i},Check,${at(i,1+i%3)}`,`c${i},Close,${at(i,3+i%3)}`);
   rows.push(`late,Receive,${at(39,-6)}`,`fresh,Receive,${at(40,-1)}`,`last,Receive,${at(40,0)}`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'stuck.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 93 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgStuckEnds').fill('Close');await page.locator('#owgStuckRun').click();
   const out=page.locator('#owgStuckResults');await out.locator('table').first().waitFor();
   const text=await out.textContent();
   assert.match(text,/1 of 3 judged open cases look stuck/,text);
   assert.match(text,/lateReceive/,text);
   assert.match(text,/not a finding that anyone did anything wrong/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogStuck'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Stuck cases browser acceptance passed: one stuck case against the usual wait, where it waits, caveat, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
