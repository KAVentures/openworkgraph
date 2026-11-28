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
 const server=spawn(python,['-m','uvicorn','folder_feed_failure_pages_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
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
   const exports=path.join(scratch,'exports');await fs.mkdir(exports);
   const old=new Date(Date.now()-3600*1000);
   await fs.writeFile(path.join(exports,'orders-1.csv'),'case,activity,time\nc3,Pay,2024-03-04T09:00:00Z\nc4,Receive,2024-03-05T08:00:00Z\nc4,Pay,2024-03-05T09:00:00Z\n');
   await fs.writeFile(path.join(exports,'readme.txt'),'not an export');
   for(const name of ['orders-1.csv','readme.txt'])await fs.utimes(path.join(exports,name),old,old);
   await page.locator('#owgFolderFeedFolder').fill(exports);
   await page.locator('#owgFolderFeedPattern').fill('orders-*.csv');
   assert.equal(await page.locator('#owgFolderFeedCreate').isDisabled(),true,'reading a folder needs consent');
   await page.locator('#owgFolderFeedConsent').check();await page.locator('#owgFolderFeedCreate').click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'Feed created.'}).waitFor();
   await fs.writeFile(path.join(scratch,'fail-append'),'1');
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports,exact:true}).click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'1 could not be appended'}).waitFor();
   await page.locator('#owgFolderFeedResult').filter({hasText:'synthetic temporary ingestion failure'}).waitFor();
   const retry=page.locator('#owgFolderFeedList').getByRole('button',{name:'Retry failed files for '+exports,exact:true});
   await page.waitForFunction(folder=>[...document.querySelectorAll('#owgFolderFeedList button')].some(b=>b.getAttribute('aria-label')==='Retry failed files for '+folder&&!b.disabled),exports);
   // Hold the list refresh after the second run. Failure review must not open
   // on a stale row that will disappear when the refreshed list is rendered.
   const feedListUrl=/\/event-logs\/[^/]+\/feeds(?:\?.*)?$/;
   let releaseFeedList;const feedListHeld=new Promise(resolve=>{releaseFeedList=resolve;});
   await page.route(feedListUrl,async route=>{if(route.request().method()==='GET')await feedListHeld;await route.continue();});
   try{
     await Promise.all([page.waitForRequest(req=>req.method()==='GET'&&feedListUrl.test(req.url())),
       page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports,exact:true}).click()]);
     assert.equal(await page.locator('#owgFolderFeedList').getByRole('button',{name:'Review failures for '+exports,exact:true}).isDisabled(),true);
     assert.match(await page.locator('#owgFolderFeedResult').textContent(),/Reading the folder/);
   }finally{releaseFeedList();await page.unroute(feedListUrl);}
   await page.locator('#owgFolderFeedResult').filter({hasText:'1 previously failed (use Retry failed files)'}).waitFor();
   await fs.unlink(path.join(scratch,'fail-append'));
   await fs.writeFile(path.join(exports,'orders-2.csv'),'case,activity,time\nc5,Receive,2024-03-06T08:00:00Z\nc5,Pay,2024-03-06T09:00:00Z\n');await fs.utimes(path.join(exports,'orders-2.csv'),old,old);
   await fs.writeFile(path.join(scratch,'seed-failures'),'1');
   const review=page.locator('#owgFolderFeedList').getByRole('button',{name:'Review failures for '+exports,exact:true});
   const [listed]=await Promise.all([page.waitForResponse(r=>r.url().includes('/failures')&&r.status()===200),review.click()]);
   let pageData=await listed.json();assert.equal(pageData.failures.length,50);
   let pages=1;while(!pageData.failures.some(f=>f.name==='orders-1.csv')){
     assert.ok(pageData.next_after);const [nextPage]=await Promise.all([page.waitForResponse(r=>r.url().includes('/failures?after=')&&r.status()===200),page.getByRole('button',{name:'Next failures',exact:true}).click()]);
     pageData=await nextPage.json();pages++;assert.ok(pages<=3);
   }
   assert.equal(pages,3);const failure=pageData.failures.find(f=>f.name==='orders-1.csv');
   const one=page.getByRole('button',{name:'Retry failed file '+failure.name+' ('+failure.sha256.slice(0,12)+')',exact:true});await one.focus();
   await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
   const [retried]=await Promise.all([page.waitForResponse(r=>r.url().includes('/retry-failed/'+failure.sha256)&&r.status()===200),one.press('Enter')]);
   assert.equal((await retried.json()).files_appended,1);
   await page.locator('#owgFolderFeedResult').filter({hasText:'1 file(s) appended with 2 new events'}).waitFor();
   await page.locator('#owgFolderFeedResult').filter({hasText:'Only the selected content was considered'}).waitFor();
   await page.waitForFunction(folder=>[...document.querySelectorAll('#owgFolderFeedList button')].some(b=>b.getAttribute('aria-label')==='Retry failed files for '+folder&&!b.disabled),exports);
   await page.locator('#owgFolderFeedList').getByRole('button',{name:'Run feed for '+exports,exact:true}).click();
   await page.locator('#owgFolderFeedResult').filter({hasText:'1 file(s) appended with 2 new events, 1 already appended.'}).waitFor();
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
   assert.deepEqual((await fs.readdir(exports)).sort(),['orders-1.csv','orders-2.csv','readme.txt'],'files are never removed');
   assert.deepEqual(errors,[]);
   console.log('Folder feed individual retry browser passed: review, keyboard retry, leave new files, normal run, schedule, deletion and WCAG.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
