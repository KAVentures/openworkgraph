const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the case filter keeps cases with or without a case tag, saved and re-applied.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-filtertag-'));
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
   // Ten cases; three are tagged "off-model" through the case-tag API.
   const rows=['case,activity,time'];
   for(let i=0;i<10;i++)['A','B'].forEach((a,j)=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T0${j}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'tagged.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 20 events'}).waitFor();
   const tagged=await page.evaluate(async()=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='tagged').log_id;
     return (await fetch('/v1/event-logs/'+id+'/case-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tag:'off-model',case_ids:['c0','c1','c2']})})).json();});
   assert.equal(tagged.tagged,3);
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   let s=await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   assert.equal(s.body.cases,10);
   const box=page.locator('#owgCaseFilter');await box.scrollIntoViewIfNeeded();
   // Only the tagged cases.
   await page.locator('#owgCaseFilterTag').fill('off-model');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,3);assert.match(s.url,/[?&]tag=off-model/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: tagged "off-model".'}).waitFor();
   // Everything except them.
   await page.locator('#owgCaseFilterTagMode').selectOption('without_tag');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,7);assert.match(s.url,/without_tag=off-model/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: not tagged "off-model".'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterTagMode').inputValue(),'without_tag','kept after re-analysis');
   // Saved and re-applied.
   await page.locator('#owgFilterSetName').fill('Fitting cases');await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "Fitting cases".'}).waitFor();
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());assert.equal(s.body.cases,10);
   const saved=page.locator('#owgFilterSetList');await saved.filter({hasText:'not tagged "off-model"'}).waitFor();
   s=await summaryAfter(()=>saved.getByRole('button',{name:'Apply saved filter Fitting cases'}).click());
   assert.equal(s.body.cases,7);
   // The summary arrives before the sections are rebuilt: wait for the re-rendered filter before auditing it.
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: not tagged "off-model".'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Case tag filter browser acceptance passed: tagged and untagged cases, kept after re-analysis, saved and re-applied, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
