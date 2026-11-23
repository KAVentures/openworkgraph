const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: an anonymized export that leaves out rare paths and identifiers.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-anonymized-'));
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
   // 30 cases A,B,C by ana; 2 cases on a rare path A,Secret step,C by a named person.
   const rows=['case,activity,time,who'];
   for(let i=0;i<32;i++){const steps=i<30?['A','B','C']:['A','Secret step','C'];
     steps.forEach((a,j)=>rows.push(`patient-${i},${a},2024-01-${String(1+(i%28)).padStart(2,'0')}T${String(9+j*2).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,${i<30?'ana':'Dr Who'}`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'patients.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 96 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgAnonymized');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgAnonymizedCheck').click();
   await page.locator('#owgAnonymizedStatus').filter({hasText:'30 of 32 cases would be exported (93.8%); 2 cases on 1 rare paths are left out.'}).waitFor();
   const kept=await page.locator('#owgAnonymizedResults tbody tr').allTextContents();
   assert.ok(kept.some(t=>t.includes('Secret step')),kept.join('|'));
   assert.ok(kept.some(t=>t.includes('case_id, step, activity, timestamp')),kept.join('|'));
   await page.locator('#owgAnonymizedResources').selectOption('pseudonymize');await page.locator('#owgAnonymizedTime').selectOption('day');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnonymizedDownload').click()]);
   assert.equal(download.suggestedFilename().endsWith('-anonymized.csv'),true);
   const text=await fs.readFile(await download.path(),'utf8');const lines=text.trim().split('\n');
   assert.equal(lines[0],'case_id,step,activity,timestamp,resource');assert.equal(lines.length,91);
   for(const secret of ['patient-','ana','Dr Who','Secret step','T09:'])assert.ok(!text.includes(secret),secret);
   assert.ok(lines.slice(1).every(l=>/^C\d{6},[123],[ABC],2024-01-\d\d,Resource 01$/.test(l)),lines.slice(1,4).join('|'));
   await page.locator('#owgAnonymizedStatus').filter({hasText:'Downloaded cases=30, suppressed=2, min_cases=5.'}).waitFor();
   const downloadNames=[];
   const oldDownload=event=>downloadNames.push(event.suggestedFilename());
   page.on('download',oldDownload);
   const csvRoute=/\/anonymized-export\.csv\?/;
   let releaseDownload;
   const delayed=new Promise(resolve=>{releaseDownload=resolve;});
   await page.route(csvRoute,async route=>{
     await delayed;
     await route.continue();
   },{times:1});
   const discardedResponse=page.waitForResponse(r=>csvRoute.test(r.url()));
   await Promise.all([page.waitForRequest(r=>csvRoute.test(r.url())),page.locator('#owgAnonymizedDownload').click()]);
   await page.locator('#owgAnonymizedActivities').selectOption('pseudonymize');
   releaseDownload();
   await discardedResponse;
   await page.locator('#owgAnonymizedTime').selectOption('elapsed');
   await page.locator('#owgAnonymizedCheck').click();
   await page.locator('#owgAnonymizedResults').getByText('2000-01-01T00:00:00Z',{exact:true}).waitFor();
   await page.locator('#owgAnonymizedResults').getByText('Replaced with export-local codes',{exact:true}).waitFor();
   const [codedDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnonymizedDownload').click()]);
   page.off('download',oldDownload);
   assert.deepEqual(downloadNames,[codedDownload.suggestedFilename()]);
   const codedRows=(await fs.readFile(await codedDownload.path(),'utf8')).trim().split('\n').slice(1);
   assert.equal(codedRows.length,90);
   assert.ok(codedRows.every(row=>/^C\d{6},[123],A00000[123],2000-01-01T0[024]:00:00Z,Resource 01$/.test(row)));
   await page.locator('#owgAnonymizedTime').selectOption('sequence');
   const [sequenceDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnonymizedDownload').click()]);
   const sequenceRows=(await fs.readFile(await sequenceDownload.path(),'utf8')).trim().split('\n').slice(1);
   assert.equal(sequenceRows.length,90);
   assert.ok(sequenceRows.every(row=>{
     const [,step,,timestamp]=row.split(',');
     return timestamp==='2000-01-01T00:00:0'+(Number(step)-1)+'Z';
   }));
   await page.locator('#owgAnonymizedMin').selectOption('50');await page.locator('#owgAnonymizedCheck').click();
   await page.locator('#owgAnonymizedStatus').filter({hasText:'no path is followed by at least 50 cases'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAnonymized'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Anonymized export browser acceptance passed: kept/left-out summary, CSV download without identifiers, day time and pseudonymized resources, error for too-large groups, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
