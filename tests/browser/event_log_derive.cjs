const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: save the cases matching a filter as a new event log and open it.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-derive-'));
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
   const context=await browser.newContext({timezoneId:'UTC'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<10;i++)(i%3?['Receive','Pay']:['Receive','Escalate','Pay']).forEach((a,k)=>rows.push(`c${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const section=page.locator('#owgEventLogDerive');await section.waitFor();await section.scrollIntoViewIfNeeded();
   // Without a filter nothing is copied.
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Choose which cases to copy.'}).waitFor();
   const savePanel=page.locator('#owgEventLogDerive');
   await page.locator('#owgDeriveEventsFrom').fill('2024-01-01T00:00');
   await page.locator('#owgDeriveCohortAttribute').fill('missing-equals');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').getByText('Attribute must look like name=value.').waitFor();
   assert.equal(await page.locator('#owgEventLogPick option').count(),1);
   await page.locator('#owgDeriveCohortAttribute').fill('');
   await page.locator('#owgDeriveEventsFrom').fill('');
   await page.locator('#owgDeriveCohortActivity').selectOption('Escalate');
   const endpoint=await page.evaluate(()=>'/v1/event-logs/'+document.querySelector('#owgEventLogPick').value+'/export/matching-cases');
   assert.ok([401,403].includes((await context.request.post(base+endpoint,{data:{cohort:{activity:'Escalate'}}})).status()));
   const [file]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   const csv=(await fs.readFile(await file.path(),'utf8')).trim().split(/\r?\n/);
   assert.equal(csv[0],'case_id,activity,timestamp,resource');
   assert.equal(csv.length,13);
   assert.deepEqual([...new Set(csv.slice(1).map(line=>line.split(',')[0]))],['c0','c3','c6','c9']);
   assert.equal(await page.locator('#owgEventLogPick option').count(),1);

   await page.locator('#owgDeriveEventsFrom').fill('2024-03-04T00:30');
   await page.locator('#owgDeriveEventsBefore').fill('2024-03-05T00:00');
   await savePanel.getByRole('button',{name:'Preview copy',exact:true}).click();
   await page.locator('#owgDerivePreview').getByText('2 events and 1 linked objects across 1 matching cases with events in the window.',{exact:false}).waitFor();
   const [windowFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   const windowRows=(await fs.readFile(await windowFile.path(),'utf8')).trim().split(/\r?\n/).slice(1);
   assert.deepEqual(windowRows.map(row=>row.split(',').slice(0,2)),[['c3','Escalate'],['c3','Pay']]);
   await page.locator('#owgDeriveEventsFrom').fill('');
   await page.locator('#owgDeriveEventsBefore').fill('');

   await savePanel.getByRole('button',{name:'Preview copy',exact:true}).click();
   await page.locator('#owgDerivePreview').getByText('4 of 10 cases match. The copy would retain 12 events and 4 linked objects.').waitFor();
   assert.equal(await page.locator('#owgEventLogPick option').count(),1);
   await page.locator('#owgDeriveName').fill('Escalated orders');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').filter({hasText:'Saved Escalated orders: 4 of 10 cases, 12 events.'}).waitFor();
   // The current analysis stays open; the picker now lists both logs.
   assert.equal(await page.locator('#owgEventLogPick option').count(),2);
   assert.equal(await page.locator('#owgEventLogDerive').count(),1);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogDerive'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgDeriveOpen').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Opened Escalated orders'}).waitFor();
   assert.match(await page.locator('#owgEventLogPick option:checked').innerText(),/^Escalated orders · 12 events$/);
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogResults table').first().waitFor();
   assert.match(await page.locator('#owgEventLogResults table').first().innerText(),/\n4\t12\t3\t/);
   const objects=[{id:'order-1',type:'order'},{id:'item-1',type:'item'},{id:'item-2',type:'item'}];
   objects[0].relationships=[{objectId:'item-1',qualifier:'contains'}];
   const ocel={objectTypes:[],eventTypes:[],objects,events:[{
     id:'event-1',type:'Open',time:'2024-04-01T00:00:00Z',
     attributes:[{name:'lifecycle:transition',value:'complete'}],
     relationships:objects.map(object=>({objectId:object.id,qualifier:''}))
   }]};
   await page.locator('#owgEventLogFile').setInputFiles({name:'linked.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(ocel))});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 1 events'}).waitFor();
   await page.locator('#owgEventLogCaseType').selectOption('order');
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgDeriveCohortActivity').selectOption('Open');
   await page.locator('#owgDeriveExportFormat').selectOption('xes-attributes');
   const [xesFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   assert.match(xesFile.suggestedFilename(),/matching-cases\.xes$/);
   const xml=await fs.readFile(await xesFile.path(),'utf8');
   assert.match(xml,/key="concept:name" value="order-1"/);
   assert.match(xml,/key="lifecycle:transition" value="complete"/);
   assert.doesNotMatch(xml,/item-1|item-2/);
   await page.locator('#owgDeriveExportFormat').selectOption('csv-review');
   const [reviewFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   assert.match(reviewFile.suggestedFilename(),/matching-cases\.review\.csv$/);
   assert.match(await fs.readFile(await reviewFile.path(),'utf8'),/^case_id_review,activity_review,/);
   await page.locator('#owgDeriveExportFormat').selectOption('ocel');
   const [ocelFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDeriveExport').click()]);
   assert.match(ocelFile.suggestedFilename(),/matching-cases\.jsonocel$/);
   const objectExport=JSON.parse(await fs.readFile(await ocelFile.path(),'utf8'));
   assert.deepEqual(objectExport.objects.map(object=>object.id),['item-1','item-2','order-1']);
   assert.deepEqual(objectExport.events.map(event=>event.id),['event-1']);

   await page.getByRole('button',{name:'Preview copy',exact:true}).click();
   await page.locator('#owgDerivePreview').getByText('The copy would also retain 1 supplied object relationship.',{exact:false}).waitFor();
   await page.locator('#owgDeriveName').fill('Order subset');
   await page.locator('#owgDeriveRun').click();
   await page.locator('#owgDeriveResult').getByText('Retained 1 supplied object relationship.',{exact:false}).waitFor();
   await page.locator('#owgDeriveOpen').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Opened Order subset'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCaseType option').first().getAttribute('value'),'item');
   assert.equal(await page.locator('#owgEventLogCaseType').inputValue(),'order');
   assert.deepEqual(errors,[]);
   console.log('Derived log browser acceptance passed: filter required, matching cases saved as a new log, current analysis kept, new log opens and analyses, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
