const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: create a differentially private process map and download only its shareable part.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-privmap-'));
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // 300 claims: Receive -> Check -> Approve (three in four) or Reject.
   const rows=['case,activity,time,who'];
   for(let i=0;i<300;i++)rows.push(`claim-${i},Receive,2024-03-01T08:00:00Z,ana`,`claim-${i},Check,2024-03-01T09:00:00Z,bob`,`claim-${i},${i%4?'Approve':'Reject'},2024-03-01T10:00:00Z,cy`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'claims.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 900 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgPrivateMap');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgPrivateMapEpsilon').fill('2');await page.locator('#owgPrivateMapK').fill('4');
   await page.locator('#owgPrivateMapRun').click();
   const out=page.locator('#owgPrivateMapResults');
   await out.filter({hasText:'paths and'}).waitFor();
   const text=await out.textContent();
   assert(text.includes('ε = 2:'),text);assert(text.includes('Only you see this: 300 cases'),text);
   const pairs=await out.locator('tbody tr').evaluateAll(rows=>rows.map(r=>[...r.cells].slice(0,2).map(c=>c.textContent).join('>')));
   assert(pairs.includes('Receive>Check'),pairs.join(','));
   const [file]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPrivateMapDownload').click()]);
   const shared=JSON.parse(await fs.readFile(await file.path(),'utf8'));
   assert.equal(shared.format,'openworkgraph.event_log_private_map.v1');assert.equal(shared.privacy.epsilon,2);
   assert(!('owner_only_not_private' in shared));
   const raw=JSON.stringify(shared);for(const secret of ['claim-','ana','bob','2024-03'])assert(!raw.includes(secret),secret);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPrivateMap'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Private process map browser acceptance passed: noisy map shown, owner-only counts kept out of the shareable download, no case, resource or time in it, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
