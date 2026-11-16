const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: an insight finding or a root-cause row becomes the global case filter in one click.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-ffind-'));
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
   // Fifteen cases; the five in APAC take twelve hours, the rest one hour. Region comes from a case table.
   const rows=['case,activity,time'];
   for(let i=0;i<15;i++){const day=String(1+i).padStart(2,'0');rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z`,`c${i},Pay,2024-03-${day}T${i%3===0?'20':'09'}:00:00Z`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'payments.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 30 events'}).waitFor();
   const table='id,Region\n'+Array.from({length:15},(_,i)=>`c${i},${i%3===0?'APAC':'EU'}`).join('\n')+'\n';
   await page.evaluate(async table=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='payments').log_id;
     await fetch('/v1/event-logs/'+id+'/case-attributes?id_column=id&column=Region',{method:'POST',body:table,headers:{'Content-Type':'text/csv','X-OWG-Local-Storage-Confirmed':'true'}});},table);
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   // From an automatic insight.
   const item=page.locator('#owgInsightResults li').filter({hasText:'Cases with Region=APAC (33%) take 12.0× as long'});
   await item.waitFor();
   let s=await summaryAfter(()=>item.getByRole('button',{name:/^Filter to these cases: /}).click());
   assert.match(s.url,/attribute=Region=APAC/);assert.equal(s.body.cases,5);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=APAC.'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterAttribute').inputValue(),'Region=APAC');
   // With a filter active, findings do not offer to replace it.
   assert.equal(await page.locator('#owgInsightResults').getByRole('button',{name:/^Filter to these cases: /}).count(),0);
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   assert.equal(s.body.cases,15);
   // From a root-cause row.
   await page.locator('#owgEventLogRootCauseRun').scrollIntoViewIfNeeded();
   await page.locator('#owgEventLogOutcome').selectOption('duration_over');await page.locator('#owgEventLogOutcomeHours').fill('6');
   await page.locator('#owgEventLogRootCauseRun').click();
   const rcFilter=page.locator('#owgEventLogRootCauseResults').getByRole('button',{name:'Filter every view to the cases with attribute: Region=APAC'});
   await rcFilter.waitFor();
   assert.equal(await page.locator('#owgEventLogRootCauseResults').getByRole('button',{name:/^Filter every view to the cases with (activity|resource):/}).count(),0,'only attribute rows can become the global filter');
   s=await summaryAfter(()=>rcFilter.click());
   assert.match(s.url,/attribute=Region=APAC/);assert.equal(s.body.cases,5);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: Region=APAC.'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Filter-from-findings browser acceptance passed: insight and root-cause attribute groups applied as the global case filter, not offered over an active filter, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
