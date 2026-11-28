const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: case figures by one or two dimensions in a breakdown table.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-breakdown-'));
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
   // 24 cases: team red or blue in turn; red cases take 2 h, blue 6 h; every third case ends with Reject instead of Done.
   const rows=['case,activity,time,team'];
   for(let i=0;i<24;i++){const red=i%2===0,end=i%3===0?'Reject':'Done',day=String(1+i).padStart(2,'0');
     rows.push(`c${i},Open,2024-0${1+(i%2)}-${day}T08:00:00Z,${red?'red':'blue'}`);
     rows.push(`c${i},${end},2024-0${1+(i%2)}-${day}T${red?'10':'14'}:00:00Z,`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'teams.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
   await page.locator('#owgEventLogAttributes').selectOption(['team']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 48 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgBreakdown');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>[...document.querySelector('#owgBreakdownRows').options].some(o=>o.value==='event_attribute:team'));
   await page.locator('#owgBreakdownRows').selectOption('event_attribute:team');
   await page.locator('#owgBreakdownColumns').selectOption('end_activity');
   await page.locator('#owgBreakdownMeasure').selectOption('median_hours');
   await page.locator('#owgBreakdownRun').click();
   await page.locator('#owgBreakdownStatus').filter({hasText:'Median duration by event attribute team and last activity, over 24 cases. Overall: 4 h.'}).waitFor();
   const header=await page.locator('#owgBreakdownResults thead th').allTextContents();
   assert.deepEqual(header,['Event attribute team','Done','Reject','All']);
   const body=await page.locator('#owgBreakdownResults tbody tr').evaluateAll(trs=>trs.map(tr=>[...tr.cells].map(td=>td.textContent)));
   assert.deepEqual(body,[['blue','6 h','6 h *','6 h'],['red','2 h','2 h *','2 h'],['All','4 h','4 h','4 h']],JSON.stringify(body));
   const out=page.locator('#owgBreakdownResults'),review=page.locator('#owgBreakdownCaseReview');
   await out.getByRole('button',{name:'Inspect cases for red · Reject (2 h *)',exact:true}).click();
   await review.getByRole('status').filter({hasText:'4 cases. Showing 4 on this page.'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c0','c12','c18','c6']);
   await review.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 2 of 2 events'}).waitFor();
   await out.getByRole('button',{name:'Inspect cases for Reject (4 h)',exact:true}).click();
   await review.getByRole('status').filter({hasText:'8 cases. Showing 8 on this page.'}).waitFor();
   // The same table as CSV: one line per cell, then the totals.
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgBreakdownCsv').click()]);
   const csvLines=(await fs.readFile(await download.path(),'utf8')).trim().split('\n');
   assert.equal(csvLines[0],'event attribute team,last activity,cases,share,median_hours,p90_hours,events_per_case,rework_rate,fewer_than_5_cases');
   assert.equal(csvLines.length,1+4+2+2+1,csvLines.join('|'));
   assert.ok(csvLines.includes('All,All,24,1.0,4.0,6.0,2.0,0.0,false'),csvLines.join('|'));
   // Shares, one dimension, and the share of cases doing an activity.
   await page.locator('#owgBreakdownRows').selectOption('start_month');await page.locator('#owgBreakdownColumns').selectOption('');
   await page.locator('#owgBreakdownMeasure').selectOption('with_activity_share');
   assert.equal(await page.locator('#owgBreakdownActivity').isVisible(),true);
   await page.locator('#owgBreakdownActivity').selectOption('Done');
   await page.locator('#owgBreakdownActivity option[value=Reject]').evaluate(option=>option.remove());
   await page.locator('#owgBreakdownActivityExact').fill('Rej');
   await page.locator('#owgBreakdownActivityFind').click();
   await page.locator('#owgBreakdownActivityMatches').selectOption('Reject');
   await page.locator('#owgBreakdownActivityUse').click();
   assert.equal(await page.locator('#owgBreakdownActivityExact').inputValue(),'Reject');
   await page.locator('#owgBreakdownRun').click();
   await page.locator('#owgBreakdownStatus').filter({hasText:'Share doing an activity by month the case started, over 24 cases. Overall: 33.3%.'}).waitFor();
   const months=await page.locator('#owgBreakdownResults tbody tr').evaluateAll(trs=>trs.map(tr=>[...tr.cells].map(td=>td.textContent)));
   assert.deepEqual(months,[['2024-01','33.3%'],['2024-02','33.3%'],['All','33.3%']],JSON.stringify(months));
   await out.getByRole('button',{name:'Inspect cases for 2024-01 (33.3%)',exact:true}).click();
   await review.getByRole('columnheader',{name:'Did Reject',exact:true}).waitFor();
   assert.equal(await review.locator('tbody tr').count(),12);
   const [csvResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/breakdown.csv')),
     page.locator('#owgBreakdownCsv').click()]);
   assert.equal(new URL(csvResponse.url()).searchParams.get('with_activity'),'Reject');
   await page.locator('#owgBreakdownActivityExact').fill('Done');
   assert.equal(await out.textContent(),'');assert.equal(await review.textContent(),'');
   // Duration bands: red cases take 2 h and blue 6 h, both in the 1 to 24 hour band.
   await page.locator('#owgBreakdownRows').selectOption('duration_band');await page.locator('#owgBreakdownMeasure').selectOption('cases');
   await page.locator('#owgBreakdownRun').click();
   await page.locator('#owgBreakdownStatus').filter({hasText:'Cases by case duration, over 24 cases.'}).waitFor();
   assert.deepEqual(await page.locator('#owgBreakdownResults tbody tr').evaluateAll(trs=>trs.map(tr=>tr.cells[0].textContent)),['1 to 24 hours','All']);
   await page.locator('#owgBreakdownRows').selectOption('start_month');
   await page.locator('#owgBreakdownColumns').selectOption('start_month');await page.locator('#owgBreakdownRun').click();
   await page.locator('#owgBreakdownStatus').filter({hasText:'Choose two different dimensions.'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgBreakdown'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Breakdown table browser acceptance passed: cells and totals, exact case evidence, event history, activity share, CSV, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
