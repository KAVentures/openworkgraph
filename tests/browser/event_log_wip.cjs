const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: work in progress over time with a Little's law check.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-wip-'));
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
   const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let d=0;d<10;d++)for(let k=0;k<2;k++){const t=new Date(Date.UTC(2024,2,4+d,1+k));rows.push(`c${d}_${k},Open,${t.toISOString()}`);
     if(!(d===9&&k===1))rows.push(`c${d}_${k},Close,${new Date(t.getTime()+2*86400e3).toISOString()}`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'wip.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 39 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogWip').waitFor();
   await page.locator('#owgWipEnds').fill('Close');await page.locator('#owgWipRun').click();
   const out=page.locator('#owgWipResults');await out.locator('table').waitFor();
   const text=await out.textContent();
   assert.match(text,/20 cases; 1 never reached an end activity/,text);
   assert.match(text,/ratio (0\.9|1\.0|1\.1)\d*/,text);
   assert.equal(await out.locator('tbody tr').count(),12);
   assert.match(await out.locator('svg').getAttribute('aria-label'),/Open cases per day: from 2 to 1, peak 4/);
   const review=page.locator('#owgWipCaseReview');
   await out.getByRole('button',{name:'Inspect 2 arrivals for 2024-03-04',exact:true}).click();
   await review.getByRole('status').filter({hasText:'2 cases. Showing 2 on this page.'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c0_0','c0_1']);
   await out.getByRole('button',{name:'Inspect 2 completions for 2024-03-06',exact:true}).click();
   await review.getByRole('status').filter({hasText:'2 cases. Showing 2 on this page.'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c0_0','c0_1']);
   await out.getByRole('button',{name:'Inspect 1 case open at end for 2024-03-15',exact:true}).click();
   await review.getByRole('button',{name:'Open case c9_1',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 1 of 1 events'}).waitFor();
   await page.locator('#owgWipBucket').selectOption('week');await page.locator('#owgWipRun').click();
   await out.locator('svg[aria-label^="Open cases per week"]').waitFor();
   assert.equal(await review.getByRole('status').count(),0);
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgWipReportJson').click()]);
   const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));
   assert.equal(report.format,'openworkgraph.work-in-progress-report.v1');
   assert.equal(report.settings.bucket,'week');
   assert.deepEqual(report.settings.end_activities,['Close']);
   assert.equal(report.report.buckets.length,3);
   await page.locator('#owgWipEnds').fill('["Close, disputed"]');
   assert.equal(await page.locator('#owgWipReportJson').isDisabled(),true);
   const [response]=await Promise.all([
     page.waitForResponse(r=>r.url().endsWith('/work-in-progress')&&r.request().method()==='POST'),
     page.locator('#owgWipRun').click()
   ]);
   assert.deepEqual(response.request().postDataJSON().end_activities,['Close, disputed']);
   assert.equal(response.status(),200);
   await out.getByText(/20 cases; 20 never reached an end activity/).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogWip'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgCaseFilterContainsActivity').fill('Close');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 19 of 20 cases'}).waitFor();
   await page.locator('#owgWipEnds').fill('Close');await page.locator('#owgWipRun').click();
   await out.getByText(/19 cases; 0 never reached an end activity/).waitFor();
   assert.equal(await out.getByRole('button',{name:/Inspect .* open at end for 2024-03-15/}).count(),0);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Work-in-progress browser acceptance passed: daily and weekly history, exact bucket cases, recorded evidence, active case filter, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
