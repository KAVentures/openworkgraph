const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: draw the performance spectrum and read batching and overtaking for each segment.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-spectrum-'));
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
   // 30 orders over three days: picked hourly, shipped together at 18:00, invoiced next day in reverse order.
   const rows=['case,activity,time,who'];
   for(let i=0;i<30;i++){const day=1+Math.floor(i/10),hour=8+i%10;const d=String(day).padStart(2,'0'),n=String(day+1).padStart(2,'0');
     rows.push(`o${i},Pick,2024-03-${d}T${String(hour).padStart(2,'0')}:00:00Z,ana`,`o${i},Ship,2024-03-${d}T18:00:00Z,bot`,
       `o${i},Invoice,2024-03-${n}T${String(20-i%10).padStart(2,'0')}:${String(i%10).padStart(2,'0')}:00Z,bob`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 90 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgSpectrum');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgSpectrumRun').click();
   const out=page.locator('#owgSpectrumResults');
   await page.locator('#owgSpectrumStatus').filter({hasText:'2 of 2 segments'}).waitFor();
   assert.deepEqual(await out.locator('h4').allTextContents(),['Pick → Ship','Ship → Invoice']);
   assert.equal(await out.locator('canvas[role=img]').count(),2);
   const text=await out.textContent();
   assert(text.includes('30 occurrences')&&text.includes('100% released together'),text);
   const label=await out.locator('canvas').first().getAttribute('aria-label');
   assert(label.startsWith('Performance spectrum of Pick to Ship: 30 occurrences'),label);
   await out.locator('details summary').first().click();
   assert((await out.locator('details').first().locator('tbody tr').count())>=3);
   // Segment figures and busy periods download as CSV; period counts add up to the segment.
   const [spectrumCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgSpectrumReportCsv').click()]);
   const spectrumLines=(await fs.readFile(await spectrumCsv.path(),'utf8')).trim().split(/\r?\n/).map(l=>l.split(','));
   assert.equal(spectrumLines[0].join(','),'kind,from,to,period_start_utc,occurrences,fast,normal,slow,very_slow,median_seconds,p25_seconds,p75_seconds,p95_seconds,fifo_share,batched_share');
   const pickShip=spectrumLines.filter(l=>l[1]==='Pick'&&l[2]==='Ship');
   assert.equal(pickShip[0][0],'segment');assert.equal(pickShip[0][4],'30');assert.equal(pickShip[0][14],'1');
   assert.equal(pickShip.slice(1).reduce((n,l)=>n+Number(l[4]),0),30);
   assert(pickShip.slice(1).every(l=>l[0]==='period'&&/^\d{4}-\d\d-\d\dT/.test(l[3])));
   const review=page.locator('#owgSpectrumCaseReview');
   await out.getByRole('button',{name:'Inspect cases from Pick to Ship',exact:true}).click();
   await review.getByRole('status').filter({hasText:'30 occurrences in 30 cases.'}).waitFor();
   await review.getByRole('button',{name:'Open case o0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 3 of 3 events'}).waitFor();
   const firstCell=out.locator('details').first().locator('tbody button').first();
   const count=Number(await firstCell.textContent());
   await firstCell.click();
   await review.getByRole('status').filter({hasText:`${count} ${count===1?'occurrence in 1 case':'occurrences in '+count+' cases'}.`}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgSpectrum'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Performance spectrum browser acceptance passed: both segments, per-period and segment case evidence, event history, CSV download, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
