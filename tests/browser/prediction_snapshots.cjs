const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: save a prediction snapshot, append newer events, score it (R19).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-psnap-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const day=n=>new Date(Date.UTC(2024,0,1+n)).toISOString();
   const hour=(iso,h)=>new Date(new Date(iso).getTime()+h*3600e3).toISOString();
   const rows=['case,activity,time'];
   for(let i=0;i<20;i++)rows.push(`c${i},Receive,${day(i)}`,`c${i},Check,${hour(day(i),1)}`,`c${i},Close,${hour(day(i),11)}`);
   for(let i=0;i<6;i++)rows.push(`o${i},Receive,${day(30)}`,`o${i},Check,${hour(day(30),1)}`);
   const upload=async(name,text,action)=>{
     await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'text/csv',buffer:Buffer.from(text)});
     await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
     await page.locator('#owgEventLogConsent').check();await page.locator(action).click();
   };
   await upload('orders.csv',rows.join('\n'),'#owgEventLogImport');
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 72 events'}).waitFor({timeout:8000}).catch(async e=>{throw new Error('status: '+await page.locator('#owgEventLogStatus').textContent()+' activity='+await page.locator('#owgEventLogActivity').inputValue());});
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogPredictions').waitFor();
   await page.locator('#owgPredictSnapshots').getByText('No snapshots yet.').waitFor();
   await page.locator('#owgPredictEnds').fill('Close');
   await page.locator('#owgPredictSnapshot').click();
   await page.locator('#owgPredictSnapshotStatus').filter({hasText:'Saved a snapshot of 6 open cases.'}).waitFor();
   await page.getByRole('button',{name:/^Score snapshot from 2024-01-31T01:00/}).click();
   await page.locator('.owgSnapshotScore').getByText('No frozen case has finished yet').waitFor();
   const newer=['case,activity,time',...[0,1,2,3].map(i=>`o${i},Close,${hour(day(30),11)}`),`o4,Close,${hour(day(30),41)}`,`x1,Receive,${hour(day(30),51)}`];
   await upload('newer.csv',newer.join('\n'),'#owgEventLogAppend');
   await page.locator('#owgEventLogStatus').filter({hasText:'Added 6 new events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgPredictSnapshots li').first().waitFor();
   await page.getByRole('button',{name:/^Score snapshot from 2024-01-31T01:00/}).click();
   const score=page.locator('.owgSnapshotScore');
   await score.getByText(/5 finished, 1 still open/).waitFor();
   const text=await score.textContent();
   assert.match(text,/Range covered 80\.0%/,text);
   assert.match(text,/1 still-open cases are already past their range/,text);
   assert.match(text,/lean towards short ones/,text);
   await page.getByRole('button',{name:/^Delete snapshot from 2024-01-31T01:00/}).click();
   await page.locator('#owgPredictSnapshots').getByText('No snapshots yet.').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogPredictions'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Prediction snapshot browser acceptance passed: frozen open cases, nothing finished yet, newer events appended, scored with error, range coverage and known open misses, deletion, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
