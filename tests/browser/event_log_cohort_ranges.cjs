const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: numeric case-attribute ranges in the per-section case pickers (KPIs, cohort benchmark).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-crf-'));
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
   // Twelve cases with an amount joined from a case table; the four largest take a day.
   const rows=['case,activity,time'];
   for(let i=0;i<12;i++){const day=String(1+i).padStart(2,'0');rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z`,`c${i},Pay,2024-03-${i>=8?String(2+i).padStart(2,'0'):day}T09:00:00Z`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   const table='id,amount\n'+Array.from({length:12},(_,i)=>`c${i},${100*(i+1)}`).join('\n')+'\n';
   await page.evaluate(async table=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='orders').log_id;
     await fetch('/v1/event-logs/'+id+'/case-attributes?id_column=id&column=amount',{method:'POST',body:table,headers:{'Content-Type':'text/csv','X-OWG-Local-Storage-Confirmed':'true'}});},table);
   await page.locator('#owgEventLogAnalyze').click();
   // KPIs for a range: the request carries the range as JSON and counts 4 cases.
   await page.locator('#owgEventLogKpiRun').scrollIntoViewIfNeeded();
   await page.locator('#owgEventLogKpiCohortRangeName').fill('amount');await page.locator('#owgEventLogKpiCohortRangeMin').fill('900');
   const [kpi]=await Promise.all([page.waitForResponse(r=>/\/kpis(\?|$)/.test(new URL(r.url()).pathname+new URL(r.url()).search.slice(0,1))),page.locator('#owgEventLogKpiRun').click()]);
   assert.match(decodeURIComponent(kpi.url()),/attribute_range=\{"name":"amount","min":900\}/);
   assert.equal((await kpi.json()).cases,4);
   // Invalid input is refused in the section, before any request.
   await page.locator('#owgEventLogKpiCohortRangeName').fill('');
   await page.locator('#owgEventLogKpiRun').click();
   await page.getByText('Name the numeric case attribute.').first().waitFor();
   // Benchmark two ranges: below 900 against 900 and above.
   await page.locator('#owgEventLogBenchRun').scrollIntoViewIfNeeded();
   await page.locator('#owgEventLogCohortALabel').fill('smaller');await page.locator('#owgEventLogCohortARangeName').fill('amount');await page.locator('#owgEventLogCohortARangeMax').fill('900');
   await page.locator('#owgEventLogCohortBLabel').fill('larger');await page.locator('#owgEventLogCohortBRangeName').fill('amount');await page.locator('#owgEventLogCohortBRangeMin').fill('900');
   const [bench]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/benchmark')),page.locator('#owgEventLogBenchRun').click()]);
   const b=await bench.json();
   assert.deepEqual(b.cohorts.map(c=>[c.definition.label,c.cases]),[['smaller',8],['larger',4]]);
   assert.deepEqual(b.cohorts.map(c=>c.definition.attribute_range),[{name:'amount',max:900},{name:'amount',min:900}]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogKpiCohort'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Cohort range fields browser acceptance passed: KPIs and benchmark cohorts by numeric case-attribute range, validated, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
