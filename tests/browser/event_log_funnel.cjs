const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: a milestone funnel over chosen steps.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-footprints-'));
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
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // 40 leads: all get a Call; 30 get an Offer; 20 are Won. 10 of the others are Lost after the Call.
   const rows=['case,activity,time,who'];
   for(let i=0;i<40;i++){const steps=['Lead','Call',...(i<30?['Offer']:['Lost']),...(i<20?['Won']:[])];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-01T${String(j*2).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'leads.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 140 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgFunnel');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgFunnelStep1').selectOption('');await page.locator('#owgFunnelStep2').selectOption('');await page.locator('#owgFunnelStep3').selectOption('');
   await page.locator('#owgFunnelRun').click();
   await page.locator('#owgFunnelStatus').filter({hasText:'Choose at least two steps.'}).waitFor();
   await page.locator('#owgFunnelStep1').selectOption('Lead');await page.locator('#owgFunnelStep2').selectOption('Call');await page.locator('#owgFunnelStep3').selectOption('Offer');
   await page.locator('#owgFunnelAdd').click();await page.locator('#owgFunnelStep4').selectOption('Won');
   await page.locator('#owgFunnelRun').click();
   await page.locator('#owgFunnelStatus').filter({hasText:'20 of 40 cases that reached Lead went on to reach Won (50%), in a median 6 h.'}).waitFor();
   assert((await page.locator('#owgFunnelStatus').textContent()).includes('The largest drop is before step 3 (10 cases).'));
   const funnelRows=page.locator('#owgFunnelResults tbody tr');
   assert.equal(await funnelRows.count(),4);
   const third=await funnelRows.nth(2).locator('td').allTextContents();
   assert.deepEqual(third.slice(0,5),['3. Offer','30','75%','75%','10'],third.join('|'));
   assert.equal(third[6],'Lost (10)');
   // The funnel downloads as one CSV row per step; changing a step disables the stale download.
   const [funnelCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgFunnelReportCsv').click()]);
   const funnelLines=(await fs.readFile(await funnelCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.equal(funnelLines[0],'step,activity,cases,share_of_first_step,conversion_from_previous,dropped_after_previous,stopped_cases_possibly_open,median_hours_from_previous,stopped_cases_last_activities');
   assert.equal(funnelLines.length,5);assert.match(funnelLines[3],/^3,Offer,30,0\.75,0\.75,10,\d+,[\d.]+,Lost \(10\)$/);
   const [funnelJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgFunnelReportJson').click()]);
   assert.equal(JSON.parse(await fs.readFile(await funnelJson.path(),'utf8')).steps.length,4);
   await page.locator('#owgFunnelStep4').selectOption('Lost');
   assert.equal(await page.locator('#owgFunnelReportCsv').isDisabled(),true);
   await page.locator('#owgFunnelStep4').selectOption('Won');
   await page.locator('#owgFunnelWindow').selectOption('24');await page.locator('#owgFunnelRun').click();
   await page.locator('#owgFunnelStatus').filter({hasText:'20 of 40 cases'}).waitFor();
   await page.locator('#owgFunnelEvidenceStep').selectOption('3');
   await page.locator('#owgFunnelEvidenceStatus').selectOption('dropped');
   await page.locator('#owgFunnelRun').click();
   await page.locator('#owgFunnelResults').getByText('Showing 10 of 10 cases stopping before step 3.').waitFor();
   const evidenceRows=page.locator('#owgFunnelResults table').nth(1).locator('tbody tr');
   assert.equal(await evidenceRows.count(),10);
   assert.deepEqual((await evidenceRows.first().locator('td').allTextContents()).slice(0,3),['c30','Lost','2']);
   assert.equal(await evidenceRows.first().getByRole('button',{name:'Open case c30'}).count(),1);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgFunnel'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Milestone funnel browser acceptance passed: step validation, four-step conversion with drop and last activities, conversion window, CSV and JSON downloads, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
