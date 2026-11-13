const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: one click creates and analyses the synthetic order-to-cash sample log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-sample-'));
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
   // A first-time user with no export of their own can try everything on synthetic data.
   await page.locator('#owgEventLogSample').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Created the sample log'}).waitFor({timeout:60000});
   assert.match(await page.locator('#owgEventLogPick option:checked').innerText(),/^Sample: order to cash \(synthetic\)/);
   assert.equal(await page.locator('#owgEventLogCaseType').inputValue(),'order');
   const overview=await page.locator('#owgEventLogResults table').first().innerText();
   assert.match(overview,/\n400\t/,'400 orders in the overview');
   assert.match(await page.locator('#owgEventLogResults').innerText(),/Put on credit hold/);
   // Asking again opens the same log instead of adding a copy.
   await page.locator('#owgEventLogSample').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Opened the sample log you already have'}).waitFor({timeout:60000});
   assert.equal(await page.locator('#owgEventLogPick option').count(),1);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogSample').parentElement,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Sample log browser acceptance passed: one-click synthetic order-to-cash log analysed by order, no duplicate on repeat, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
