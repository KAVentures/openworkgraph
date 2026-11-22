const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: every activity and every resource of the selection downloads as CSV.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-statscsv-'));
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
   const rows=['case,activity,time,who'];
   for(let i=0;i<12;i++){const steps=i<6?['Open','Approve','Close']:i<10?['Open','Reject','Close']:['Open','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T${String(8+j).padStart(2,'0')}:00:00Z,${a==='Approve'?'bo':'ana'}`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'paths.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 34 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgStatisticsExport');await box.waitFor();await box.scrollIntoViewIfNeeded();
   const fetchCsv=async id=>{const [d]=await Promise.all([page.waitForEvent('download'),page.locator(id).click()]);
     return {name:d.suggestedFilename(),lines:(await fs.readFile(await d.path(),'utf8')).trim().split('\n')};};
   const acts=await fetchCsv('#owgStatisticsExportActivities');
   assert.match(acts.name,/-activities\.csv$/);
   assert.equal(acts.lines[0].split(',').slice(0,4).join(','),'activity,events,cases,case_share');
   assert.deepEqual(acts.lines.slice(1).map(l=>l.split(',').slice(0,3).join(',')),['Close,12,12','Open,12,12','Approve,6,6','Reject,4,4']);
   await page.locator('#owgStatisticsExportStatus').filter({hasText:'Downloaded 4 activities.'}).waitFor();
   const res=await fetchCsv('#owgStatisticsExportResources');
   assert.deepEqual(res.lines.slice(1).map(l=>l.split(',').slice(0,2).join(',')),['ana,28','bo,6']);
   await page.locator('#owgStatisticsExportStatus').filter({hasText:'Downloaded 2 resources.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('Statistics CSV browser acceptance passed: every activity and resource with frequency and cases.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
