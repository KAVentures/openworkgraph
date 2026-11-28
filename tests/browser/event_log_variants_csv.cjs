const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: every variant downloaded as CSV.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-variantscsv-'));
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
   // 12 cases on three paths: 6 Open>Approve>Close, 4 Open>Reject>Close, 2 Open>Close.
   const rows=['case,activity,time,who'];
   for(let i=0;i<12;i++){const steps=i<6?['Open','Approve','Close']:i<10?['Open','Reject','Close']:['Open','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T${String(8+j).padStart(2,'0')}:00:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'paths.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 34 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgVariantsCsv').waitFor();await page.locator('#owgVariantsCsv').scrollIntoViewIfNeeded();
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgVariantsCsv').click()]);
   assert.match(download.suggestedFilename(),/-variants\.csv$/);
   const lines=(await fs.readFile(await download.path(),'utf8')).trim().split('\n');
   assert.equal(lines[0],'rank,path,steps,cases,share,cumulative_share,median_seconds,p90_seconds');
   assert.deepEqual(lines.slice(1).map(l=>l.split(',').slice(0,6).join(',')),
     ['1,Open > Approve > Close,3,6,0.5,0.5','2,Open > Reject > Close,3,4,0.333333,0.833333','3,Open > Close,2,2,0.166667,1.0']);
   await page.locator('#owgVariantsCsvStatus').filter({hasText:'Downloaded 3 variants.'}).waitFor();
   const [exact]=await Promise.all([page.waitForEvent('download'),page.locator('#owgVariantsExactCsv').click()]);
   assert.match(exact.suggestedFilename(),/-variants-exact\.csv$/);
   const exactText=await fs.readFile(await exact.path(),'utf8');
   assert.match(exactText.split('\n')[0],/p90_seconds,path_json/);
   assert.match(exactText,/"\[""Open"",""Approve"",""Close""\]"/);
   await page.locator('#owgVariantsCsvStatus').filter({hasText:'Exact paths are in the path_json column.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('Variant CSV browser acceptance passed: every variant with steps, cases, share and running share.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
