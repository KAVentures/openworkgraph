const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: end-time, events-per-case, path-frequency and rework case filters, saved and re-applied.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-morefilters-'));
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
   // Five cases A,B,C ending Jan 1–5; two A,B,B,C (rework) ending Feb 1–2; one A,X ending Mar 1.
   const paths=[...Array(5).fill(['A','B','C']),...Array(2).fill(['A','B','B','C']),['A','X']];
   const ends=['2024-01-01','2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-02-01','2024-02-02','2024-03-01'];
   const rows=['case,activity,time,who,channel'];
   paths.forEach((p,i)=>p.forEach((a,j)=>rows.push(`c${i},${a},${ends[i]}T0${j}:00:00Z,${j===0&&i<2?'Åna':'Team'},${j===1&&(i===1||i===2)?'web':'email'}`)));
   await page.locator('#owgEventLogFile').setInputFiles({name:'more.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogAttributes').selectOption(['channel']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 25 events'}).waitFor();
   const summaryAfter=async action=>{const [response]=await Promise.all([page.waitForResponse(r=>/\/v1\/event-logs\/log_[0-9a-f]+\/summary(\?|$)/.test(r.url())),action()]);return {url:decodeURIComponent(response.url()),body:await response.json()};};
   let s=await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   assert.equal(s.body.cases,8);
   const box=page.locator('#owgCaseFilter');await box.scrollIntoViewIfNeeded();
   // Rework.
   await page.locator('#owgCaseFilterRework').selectOption('true');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,2);assert.match(s.url,/rework=true/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: repeats an activity.'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterRework').inputValue(),'true','kept after re-analysis');
   // Rare paths only: followed by at most 2 cases, with no rework → the A,X case.
   await page.locator('#owgCaseFilterRework').selectOption('false');await page.locator('#owgCaseFilterPathMax').fill('2');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,1);assert.match(s.url,/path_cases_max=2/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: cases on the same path ≤ 2; repeats no activity.'}).waitFor();
   // Events per case and end date.
   await page.locator('#owgCaseFilterRework').selectOption('');await page.locator('#owgCaseFilterPathMax').fill('');
   await page.locator('#owgCaseFilterEventsMin').fill('3');await page.locator('#owgCaseFilterEndedFrom').fill('2024-01-03');await page.locator('#owgCaseFilterEndedBefore').fill('2024-02-02');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,4);assert.match(s.url,/events_min=3/);assert.match(s.url,/ended_from=2024-01-03T00:00:00Z/);
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Active: ended from 2024-01-03; ended before 2024-02-02; events per case ≥ 3.'}).waitFor();
   // Validation happens before anything is sent.
   await page.locator('#owgCaseFilterEventsMax').fill('2');await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'Events per case: the lower bound must not be above the upper bound.'}).waitFor();
   await page.locator('#owgCaseFilterEventsMax').fill('');
   // Save, clear and re-apply.
   await page.locator('#owgFilterSetName').fill('Finished in January');await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "Finished in January".'}).waitFor();
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   assert.equal(s.body.cases,8);assert.doesNotMatch(s.url,/ended_from|events_min/);
   const saved=page.locator('#owgFilterSetList');await saved.filter({hasText:'ended from 2024-01-03; ended before 2024-02-02; events per case ≥ 3'}).waitFor();
   s=await summaryAfter(()=>saved.getByRole('button',{name:'Apply saved filter Finished in January'}).click());
   assert.equal(s.body.cases,4);
   assert.equal(await page.locator('#owgCaseFilterEventsMin').inputValue(),'3','the saved filter is shown in the controls');
   assert.equal(await page.locator('#owgCaseFilterEndedFrom').inputValue(),'2024-01-03');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   assert.equal(s.body.cases,8);
   await page.locator('#owgCaseFilterStartedFrom').fill('2024-02-01');
   await page.locator('#owgCaseFilterStartedBefore').fill('2024-03-01');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,2);
   assert.equal(s.body.cohort.started_from,'2024-02-01T00:00:00Z');
   assert.equal(s.body.cohort.started_before,'2024-03-01T00:00:00Z');
   await page.locator('#owgFilterSetName').fill('February starts');
   await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').getByText('Saved "February starts".').waitFor();
   await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   s=await summaryAfter(()=>page.getByRole('button',{name:'Apply saved filter February starts'}).click());
   assert.equal(s.body.cases,2);
   assert.equal(await page.locator('#owgCaseFilterStartedFrom').inputValue(),'2024-02-01');
   await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   await page.locator('#owgCaseFilterResource').fill('Å');
   await page.locator('#owgCaseFilterResourceFind').click();
   await page.locator('#owgCaseFilterResourceMatches').selectOption('Åna');
   await page.locator('#owgCaseFilterResourceUse').click();
   assert.equal(await page.locator('#owgCaseFilterResource').inputValue(),'Åna');
   await page.locator('#owgCaseFilterEventAttribute').fill('channel=web');
   s=await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   assert.equal(s.body.cases,1);
   assert.equal(s.body.events,3,'the resource and attribute may match different events in the same case');
   assert.equal(s.body.cohort.resource,'Åna');
   assert.equal(s.body.cohort.event_attribute,'channel=web');
   await page.locator('#owgFilterSetName').fill('Web cases touched by Åna');
   await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').getByText('Saved "Web cases touched by Åna".').waitFor();
   await summaryAfter(()=>page.locator('#owgCaseFilterClear').click());
   s=await summaryAfter(()=>page.getByRole('button',{name:'Apply saved filter Web cases touched by Åna'}).click());
   assert.equal(s.body.cases,1);
   assert.equal(await page.locator('#owgCaseFilterResource').inputValue(),'Åna');
   assert.equal(await page.locator('#owgCaseFilterEventAttribute').inputValue(),'channel=web');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseFilter'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   const ocel={objectTypes:[],eventTypes:[],
     objects:[{id:'o1',type:'order'},{id:'i1',type:'item'},{id:'i2',type:'item'}],
     events:[
       {id:'start',type:'Open',time:'2024-01-01T00:00:00Z',relationships:['o1','i1','i2'].map(objectId=>({objectId}))},
       {id:'pick',type:'Pick',time:'2024-01-01T01:00:00Z',relationships:[{objectId:'i1'}]}
     ]};
   await page.locator('#owgEventLogFile').setInputFiles({name:'items.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(ocel))});
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
   const notions=page.locator('#owgEventLogCaseType');
   await notions.selectOption('item');
   await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   await page.locator('#owgCaseFilterContainsActivity').fill('Pick');
   await summaryAfter(()=>page.locator('#owgCaseFilterApply').click());
   await page.locator('#owgFilterSetName').fill('Picked items');
   await page.locator('#owgFilterSetSave').click();
   await page.locator('#owgFilterSetStatus').filter({hasText:'Saved "Picked items".'}).waitFor();
   await notions.selectOption('order');
   await summaryAfter(()=>page.locator('#owgEventLogAnalyze').click());
   s=await summaryAfter(()=>page.getByRole('button',{name:'Apply saved filter Picked items'}).click());
   assert.equal(s.body.cases,1);
   assert.match(s.url,/case_type=item/);
   assert.equal(await notions.inputValue(),'item');
   assert.deepEqual(errors,[]);
   console.log('More case filters browser acceptance passed: rework, path frequency, events per case and end period applied, validated, saved and re-applied, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
