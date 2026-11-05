const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: SAP source is off by default, saves config without reading, refuses a read without credential.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-sap-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const env={...process.env,OWG_BROWSER_TEST_DIR:scratch};delete env.OWG_SAP_TOKEN;
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env,stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',c=>{logs=(logs+c).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
   if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
   page.on('request',r=>{if(!r.url().startsWith(base))requests.push(r.url());});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const card=page.locator('#owgSapCard');await card.waitFor();
   await page.locator('#owgSapDetails').getByText('Not configured.').waitFor();
   assert(await page.locator('#owgSapRun').isDisabled());
   await page.locator('#owgSapRoot').fill('https://erp.example/sap/opu/odata/sap');await page.locator('#owgSapSave').click();
   await page.locator('#owgSapStatus').filter({hasText:'Nothing has been read yet.'}).waitFor();
   assert(await page.locator('#owgSapRun').isDisabled(),'a read needs explicit confirmation');
   await page.locator('#owgSapConsent').check();await page.locator('#owgSapRun').click();
   await page.locator('#owgSapStatus').filter({hasText:'OWG_SAP_TOKEN'}).waitFor();
   assert.deepEqual(requests,[],'nothing leaves this computer without a credential');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgSapCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   await page.locator('#owgSapDisconnect').click();await page.locator('#owgSapStatus').filter({hasText:'Disconnected.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('SAP source acceptance passed: off by default, config saved without reading, confirmed read refused without credential, no external requests, disconnect, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
