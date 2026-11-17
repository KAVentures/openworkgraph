const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: case-attribute values and numeric names suggested in every case-attribute input.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-casug-'));
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
   await page.locator('#owgEventLogAnalyze').click();
   await page.waitForFunction(()=>document.querySelectorAll('#owgCaseAttributeValueSuggestions option').length>0);
   const values=await page.locator('#owgCaseAttributeValueSuggestions option').evaluateAll(os=>os.map(o=>[o.value,o.label]));
   assert.deepEqual(values,[['Region=US','8 cases'],['Region=EU','4 cases']]);
   const numeric=await page.locator('#owgCaseAttributeNumericSuggestions option').evaluateAll(os=>os.map(o=>[o.value,o.label]));
   assert.deepEqual(numeric,[['amount','100 to 1200']]);
   // The global filter and the section pickers point at the suggestions; event-attribute inputs do not.
   assert.equal(await page.locator('#owgCaseFilterAttribute').getAttribute('list'),'owgCaseAttributeValueSuggestions');
   assert.equal(await page.locator('#owgCaseFilterRangeName').getAttribute('list'),'owgCaseAttributeNumericSuggestions');
   assert.equal(await page.locator('#owgEventLogKpiCohortAttribute').getAttribute('list'),'owgCaseAttributeValueSuggestions');
   assert.equal(await page.locator('#owgEventLogCohortAAttribute').getAttribute('list'),'owgCaseAttributeValueSuggestions');
   assert.equal(await page.locator('#owgEventLogKpiCohortEventAttribute').getAttribute('list'),null);
   assert.equal(await page.locator('#owgEventLogKpiCohortRangeName').getAttribute('list'),'owgCaseAttributeNumericSuggestions','section range inputs get the numeric names');
   // A suggested value works as typed.
   await page.locator('#owgCaseFilterAttribute').fill('Region=EU');
   const [summary]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),page.locator('#owgCaseFilterApply').click()]);
   assert.equal((await summary.json()).cases,4);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=EU.'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Case attribute suggestions browser acceptance passed: common values and numeric names offered in the global filter and section pickers, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
