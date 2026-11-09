const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: profile the event attributes kept from a CSV import.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-attrprofile-'));
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
   const rows=['case,activity,time,region,amount'];
   for(let i=0;i<10;i++){const south=i>=6;['Receive','Pay'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k*(south?5:1)}:00:00Z,${south?'south':'north'},${100+10*i+(south?5*k:0)}`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'regions.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   await page.locator('#owgEventLogAttributes').selectOption(['region','amount']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 20 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgAttributeProfile');await section.waitFor();await section.scrollIntoViewIfNeeded();
   await page.locator('#owgAttributeProfileRun').click();
   const out=page.locator('#owgAttributeProfileResults');
   await out.locator('table').waitFor();
   const text=await out.innerText();
   assert.match(text,/region\t100\.0% of cases\ttext\tfixed per case\t2\tnorth \(60\.0%, 60 min\); south \(40\.0%, 5\.0 h\)/);
   assert.match(text,/amount\t100\.0% of cases\tnumber\tchanges within 4 cases/);
   assert.match(text,/100 – 195, median/);
   assert.match(text,/not evidence that the value causes/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAttributeProfile'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Attribute profile browser acceptance passed: case- and event-level attributes, frequent values with case time, numeric range, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
