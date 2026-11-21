const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a folder feed appends a dropped export once, can run daily, and is deleted with its schedule.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-feed-'));
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
   for(let i=0;i<4;i++)['Receive','Pay'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-0${1+i}T0${8+k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 8 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgFolderFeed');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgFolderFeedList').filter({hasText:'No folder feed yet.'}).waitFor();
   // A folder where a nightly job drops exports: one overlapping file, one unrelated file.
   const exports=path.join(scratch,'exports '),wrong=path.join(scratch,'exports');await fs.mkdir(exports);await fs.mkdir(wrong);
   const old=new Date(Date.now()-3600*1000);
   await fs.writeFile(path.join(exports,' 订单-1.csv'),'case,activity,time\nc3,Pay,2024-03-04T09:00:00Z\nc4,Receive,2024-03-05T08:00:00Z\nc4,Pay,2024-03-05T09:00:00Z\n');
   await fs.writeFile(path.join(exports,'readme.txt'),'not an export');
   await fs.writeFile(path.join(exports,'订单-1.csv'),'case,activity,time\nwrong,A,2024-03-05T08:00:00Z\n');
   await fs.writeFile(path.join(wrong,' 订单-1.csv'),'case,activity,time\nwrong,B,2024-03-05T08:00:00Z\n');
   for(const name of [' 订单-1.csv','订单-1.csv','readme.txt'])await fs.utimes(path.join(exports,name),old,old);
   await fs.utimes(path.join(wrong,' 订单-1.csv'),old,old);
   await page.locator('#owgFolderFeedFolder').fill(exports);
   await page.locator('#owgFolderFeedPattern').fill(' 订单-*.csv');
   assert.equal(await page.locator('#owgFolderFeedCreate').isDisabled(),true,'reading a folder needs consent');
   await page.locator('#owgFolderFeedConsent').check();
   const [creation]=await Promise.all([page.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/feeds')),page.locator('#owgFolderFeedCreate').click()]);
   assert.equal(creation.postDataJSON().folder,exports);assert.equal(creation.postDataJSON().pattern,' 订单-*.csv');
   await page.locator('#owgFolderFeedResult').filter({hasText:'Feed created.'}).waitFor();
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports,exact:true}).click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'1 file(s) appended with 2 new events. Analyse again to include them.'}).waitFor();
   await page.locator('#owgFolderFeedList').filter({hasText:' 订单-1.csv — 2 events added'}).waitFor();
   // Running again finds nothing new.
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports,exact:true}).click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'0 file(s) appended with 0 new events, 1 already appended.'}).waitFor();
   page.once('dialog',d=>d.accept());
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports+' every day'}).click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'Scheduled daily.'}).waitFor();
   const schedules=await page.evaluate(async()=>(await (await fetch('/v1/schedules')).json()).schedules);
   assert.deepEqual(schedules.map(s=>[s.kind,s.interval_minutes]),[['event_log_folder_feed',1440]]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgFolderFeed'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   page.once('dialog',d=>d.accept());
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Delete feed for '+exports}).click();
   await page.locator('#owgFolderFeedList').filter({hasText:'No folder feed yet.'}).waitFor();
   assert.deepEqual(await page.evaluate(async()=>(await (await fetch('/v1/schedules')).json()).schedules),[],'deleting the feed removes its schedule');
   assert.deepEqual((await fs.readdir(exports)).sort(),[' 订单-1.csv','readme.txt','订单-1.csv'],'files are never removed');
   assert.deepEqual(errors,[]);
   console.log('Literal folder-feed browser passed: exact spaced folder and Unicode pattern, source isolation, schedule, deletion and WCAG.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
