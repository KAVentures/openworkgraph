const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: process cost from a cost model entered in the dashboard.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logcosts-'));
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
   const rows=['case,activity,time,who'];
   for(let i=0;i<10;i++){const path=i<6?['Receive','Check','Pay']:['Receive','Check','Check','Pay'];path.forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k}:00:00Z,${a==='Check'?'Ana':'clerk'}`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'invoices.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 34 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgEventLogCosts');await section.waitFor();await section.scrollIntoViewIfNeeded();
   await page.locator('#owgCostCurrency').fill('USD');
   await page.getByLabel('Cost of one Receive').fill('2');
   await page.getByLabel('Cost of one Check').fill('10.5');
   await page.locator('#owgCostHourly').fill('4');
   await page.locator('#owgCostRun').click();
   const out=page.locator('#owgCostResults');
   await out.locator('table').first().waitFor();
   const text=await out.innerText();
   // Pay has no price: 10*2 + 14*10.5 = 167; holding (6*2 h + 4*3 h) * 4 = 96.
   assert.match(text,/\$263\.00/);
   assert.match(text,/\$167\.00/);
   assert.match(text,/\$96\.00/);
   assert.match(text,/\$42\.00/);
   assert.match(text,/No price for Pay: 10 executions are left out of the totals\./);
   assert.match(text,/Receive → Check → Check → Pay/);
   assert.match(text,/not verified rework/);
   // A default price fills the blanks.
   await page.locator('#owgCostDefault').fill('1');await page.locator('#owgCostRun').click();
   await out.filter({hasText:'$273.00'}).waitFor();
   assert.doesNotMatch(await out.innerText(),/No price for/);
   // Save the prices with the log; analysing again fills them in, and they can be forgotten.
   await page.locator('#owgCostSaveModel').click();
   await page.locator('#owgCostModelStatus').filter({hasText:'Prices saved.'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgCostModelStatus').filter({hasText:/Prices saved .* are filled in/}).waitFor();
   assert.equal(await page.getByLabel('Cost of one Check').inputValue(),'10.5');
   assert.equal(await page.locator('#owgCostCurrency').inputValue(),'USD');
   assert.equal(await page.locator('#owgCostDefault').inputValue(),'1');
   await page.locator('#owgCostForgetModel').click();
   await page.locator('#owgCostModelStatus').filter({hasText:'Saved prices forgotten.'}).waitFor();
   assert.equal(await page.locator('#owgCostForgetModel').isDisabled(),true);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCosts'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Process cost browser acceptance passed: activity prices, holding cost, unpriced activities, default price, costliest variants, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
