const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: next-activity prediction for open cases with its backtest.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-next-'));
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
   const paths=[];for(let i=0;i<40;i++)paths.push(i%2?['Import','Check','Reject','Close']:['Receive','Check','Approve','Pay']);
   paths.push(['Receive','Check'],['Import','Check']);
   const at=(i,k)=>new Date(Date.UTC(2024,0,1+i,8+k)).toISOString().replace('.000Z','Z');
   paths.forEach((path,i)=>path.forEach((a,k)=>rows.push(`c${String(i).padStart(3,'0')},${a},${at(i,k)}`)));
   await page.locator('#owgEventLogFile').setInputFiles({name:'routes.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 164 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgNextActivity');await section.waitFor();await section.scrollIntoViewIfNeeded();
   await page.locator('#owgNextRun').click();
   await page.locator('#owgNextResults').filter({hasText:'List the activities that end a case.'}).waitFor();
   await page.locator('#owgNextEnds').fill('Pay, Close');
   await page.locator('#owgNextRun').click();
   const out=page.locator('#owgNextResults');await out.locator('table').first().waitFor();
   const text=await out.innerText();
   assert.match(text,/2 open and 40 completed cases\./);
   assert.match(text,/the first guess was right 100\.0% of the time/);
   assert.match(text,/c041\tCheck\tReject 100\.0%\tlast 2 activities\t20/);
   assert.match(text,/c040\tCheck\tApprove 100\.0%\tlast 2 activities\t20/);
   assert.match(text,/not instructions/);
   // With one activity of history the two routes cannot be told apart.
   await page.locator('#owgNextHistory').selectOption('1');await page.locator('#owgNextRun').click();
   await out.filter({hasText:'Approve 50.0%, Reject 50.0%'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgNextActivity'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Next-activity browser acceptance passed: history-specific predictions, backtest against baselines, history setting, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
