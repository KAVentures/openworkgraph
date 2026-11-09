const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the local focus and switching report.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-focus-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8796';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','focus_report_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8796'],{
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
   const context=await browser.newContext({acceptDownloads:true,timezoneId:'UTC'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=export]').click();
   const card=page.locator('#owgFocusReport');await card.waitFor();
   await page.locator('#owgFocusShow').click();
   await page.locator('#owgFocusTable').waitFor();
   const text=await card.textContent();
   assert.match(text,/3 focus blocks, 2 switches/,text);
   assert.match(text,/Editor ⇄ Mail: 2 switches/,text);
   assert.match(text,/Editor · .* · 40 min \(1 short detours\)/,text);
   assert.match(text,/not a measure of attention/);
   await page.locator('#owgFocusZone').fill('No/Zone');await page.locator('#owgFocusShow').click();
   await page.locator('#owgFocusStatus').filter({hasText:'unknown time zone'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgFocusReport'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Focus report browser acceptance passed: blocks with folded detours, switch pairs, longest blocks, refused zone, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
