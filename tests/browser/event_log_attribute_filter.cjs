const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: case-attribute values and numeric ranges as part of the global case filter, saved and re-applied.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-gattr-'));
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
   // Twelve cases with Region and amount joined from a separate case table.
   const rows=['case,activity,time'];
   for(let i=0;i<12;i++){const day=String(1+i).padStart(2,'0');rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z`,`c${i},Pay,2024-03-${day}T09:00:00Z`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   const table='id,Region,amount\n'+Array.from({length:12},(_,i)=>`c${i},${i<4?'EU':'US'},${100*(i+1)}`).join('\n')+'\n';
   const added=await page.evaluate(async table=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='orders').log_id;
     return (await fetch('/v1/event-logs/'+id+'/case-attributes?id_column=id&column=Region&column=amount',{method:'POST',body:table,headers:{'Content-Type':'text/csv','X-OWG-Local-Storage-Confirmed':'true'}})).json();},table);
   assert.equal(added.cases_updated,12);
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   let s=await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   assert.equal(s.body.cases,12);
   const box=page.locator('#owgCaseFilter');await box.scrollIntoViewIfNeeded();
   // A value filter applies to every filtered view.
   await page.locator('#owgCaseFilterAttribute').fill('Region=EU');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.match(s.url,/attribute=Region=EU/);assert.equal(s.body.cases,4);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=EU.'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterAttribute').inputValue(),'Region=EU','kept after re-analysis');
   // Combined with a numeric range: EU cases from 200 (inclusive) below 400.
   await page.locator('#owgCaseFilterRangeName').fill('amount');await page.locator('#owgCaseFilterRangeMin').fill('200');await page.locator('#owgCaseFilterRangeMax').fill('400');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.match(s.url,/attribute_range=\{"name":"amount","min":200,"max":400\}/);assert.equal(s.body.cases,2);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=EU; 200 ≤ amount < 400.'}).waitFor();
   // Invalid input is refused before any request.
   await page.locator('#owgCaseFilterRangeMax').fill('100');await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'The lower bound must be below the upper bound.'}).waitFor();
   await page.locator('#owgCaseFilterRangeMax').fill('400');
   // Save the whole filter, clear it, and apply the saved one.
   await page.locator('#owgFilterSetName').fill('EU mid amounts');await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "EU mid amounts".'}).waitFor();
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   assert.equal(s.body.cases,12);assert.doesNotMatch(s.url,/attribute/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'No case filter'}).waitFor();
   const saved=page.locator('#owgFilterSetList');await saved.filter({hasText:'Region=EU; 200 ≤ amount < 400'}).waitFor();
   s=await summaryAfter(()=>saved.getByRole('button',{name:'Apply saved filter EU mid amounts'}).click());
   assert.equal(s.body.cases,2);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=EU; 200 ≤ amount < 400.'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterRangeName').inputValue(),'amount','the saved range is shown in the controls');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Global attribute filter browser acceptance passed: value and range filters applied to every filtered view, validated, saved and re-applied, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
