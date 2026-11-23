const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');const zlib=require('node:zlib');
// Real-browser acceptance: gzip-compressed CSV and XES files are read and imported like plain ones.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-gzip-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
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
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   assert.equal(await page.locator('#owgEventLogFile').getAttribute('accept').then(a=>a.endsWith(',.gz')),true);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv.gz',mimeType:'application/gzip',buffer:zlib.gzipSync(Buffer.from(rows.join('\n')))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   assert.deepEqual(await page.locator('#owgEventLogCase option').allTextContents(),['case','activity','time']);
   await page.getByRole('button',{name:'Preview CSV mapping',exact:true}).click();
   await page.locator('#owgEventLogImportPreviewStatus').getByText(
     '12 rows checked. 12 accepted events in 6 cases. 0 rows rejected.',{exact:true}).waitFor();
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   const xes=await page.evaluate(async()=>{const id=(await (await fetch('/v1/event-logs')).json()).logs[0].log_id;return (await fetch('/v1/event-logs/'+id+'/export?format=xes')).text();});
   await page.locator('#owgEventLogName').fill('');
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.xes.gz',mimeType:'application/gzip',buffer:zlib.gzipSync(Buffer.from(xes))});
   assert.equal(await page.locator('#owgEventLogFormat').inputValue(),'xes');
   assert.equal(await page.locator('#owgEventLogPreviewImport').isVisible(),false);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   const logs=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>[l.source_format,l.events]).sort());
   assert.deepEqual(logs,[['csv',12],['xes',12]]);
   const file=page.locator('#owgEventLogFile'),pick=page.locator('#owgEventLogPick');
   const consent=page.locator('#owgEventLogConsent'),saveStatus=page.locator('#owgEventLogSaveStatus');
   const importButton=page.locator('#owgEventLogImport'),appendButton=page.locator('#owgEventLogAppend');
   async function stage(name){
     await file.setInputFiles({name:name+'.csv',mimeType:'text/csv',
       buffer:Buffer.from('case,activity,time\n'+name+',Open,2024-05-01T08:00:00Z\n')});
     await page.locator('#owgEventLogName').fill(name);
     await page.locator('#owgEventLogStatus').getByText('File mapping ready.',{exact:false}).waitFor();
     await consent.check();
   }
   async function holdSave(pattern,button){
     let release;
     const gate=new Promise(resolve=>{release=resolve;});
     await page.route(pattern,async route=>{
       await gate;
       await route.continue();
     },{times:1});
     await Promise.all([page.waitForRequest(request=>pattern.test(request.url())),button.click()]);
     return release;
   }
   const activeLog=await pick.inputValue();
   await stage('first');
   const finishImport=await holdSave(/\/v1\/event-logs\/import\?/,importButton);
   await stage('next');
   assert(await importButton.isDisabled());assert(await appendButton.isDisabled());
   finishImport();
   await saveStatus.getByText('Imported 1 events.',{exact:false}).waitFor();
   assert.equal(await pick.inputValue(),activeLog);
   assert.equal(await file.evaluate(input=>input.files[0].name),'next.csv');
   assert.equal(await page.locator('#owgEventLogName').inputValue(),'next');
   assert(await consent.isChecked());
   await page.waitForFunction(()=>!document.querySelector('#owgEventLogAppend').disabled);
   const csvId=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs
     .find(log=>log.source_format==='csv'&&log.events===12).log_id);
   await pick.selectOption(csvId);
   const finishAppend=await holdSave(/\/append\?/,appendButton);
   await pick.selectOption(activeLog);
   await stage('after');
   assert(await importButton.isDisabled());assert(await appendButton.isDisabled());
   finishAppend();
   await saveStatus.getByText('Added 1 new events',{exact:false}).waitFor();
   assert.equal(await file.evaluate(input=>input.files[0].name),'after.csv');
   assert.equal(await pick.inputValue(),activeLog);
   assert(await consent.isChecked());
   const saved=await page.evaluate(async id=>(await (await fetch('/v1/event-logs')).json()).logs
     .find(log=>log.log_id===id),csvId);
   assert.equal(saved.events,13);
   assert.deepEqual(errors,[]);
   console.log('Gzip import browser acceptance passed: .csv.gz columns read in the browser, .csv.gz and .xes.gz imported.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
