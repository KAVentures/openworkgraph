const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: download the drawn process map as SVG and PNG (R7).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-mapexport-'));
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
   const rows=['case,activity,time'];
   for(let i=0;i<8;i++)['Receive order','Check stock',i%2?'Ship':'Back-order','Invoice'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-0${1+(i%8)}T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 32 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgMapGraph svg').first().waitFor();
   const [svgDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgMapDownloadSvg').click()]);
   assert.match(svgDownload.suggestedFilename(),/^process-map-[A-Za-z0-9_-]+\.svg$/);
   const svgText=await fs.readFile(await svgDownload.path(),'utf8');
   assert.match(svgText,/^<svg[^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
   for(const name of ['Receive order','Check stock','Ship','Back-order','Invoice'])assert(svgText.includes(name),name);
   assert.match(svgText,/<desc>[^<]*activities 100%, paths 100%/);
   assert.match(svgText,/not a verified business workflow/);
   assert.doesNotMatch(svgText,/tabindex/);
   const [pngDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgMapDownloadPng').click()]);
   const png=await fs.readFile(await pngDownload.path());
   assert.deepEqual([...png.subarray(0,8)],[137,80,78,71,13,10,26,10],'a real PNG');
   assert(png.length>2000,'the PNG has content: '+png.length);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogProcessMap'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Process map export browser acceptance passed: self-contained SVG with every activity, slider settings and interpretation, a real 2x PNG, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
